"""Push-to-talk session state machine (BUILD_PLAN Section 2.4).

There is no VAD anywhere in this module. Audio becomes a segment only
because a human pressed a control: arm, start, new_segment, pause, end.
Silence is never detected, trimmed, or used to decide anything.

State graph:

    IDLE      --arm()-->    ARMED
    ARMED     --start()-->  RECORDING
    RECORDING --pause()-->  PAUSED
    PAUSED    --resume()--> RECORDING
    ARMED     --end()-->    ENDED
    RECORDING --end()-->    ENDED
    PAUSED    --end()-->    ENDED
    any       --reset()-->  IDLE   (test and reuse helper, not a session step)

Every other transition raises InvalidTransition. Callers must not have to
guess whether a stray button press corrupted a session.

Audio routing by state:

    IDLE / ARMED : audio goes into the pre-roll ring buffer only.
    RECORDING    : audio appends to the current (open) segment buffer.
    PAUSED       : audio is dropped and counted in dropped_bytes.
    ENDED        : audio is an error (InvalidTransition).

Pre-roll: while IDLE or ARMED the ring buffer keeps the last preroll_s
seconds of audio. start() snapshots that ring and stashes the snapshot.
The stash is prepended to segment 0 when segment 0 is sealed, and the
sealed segment is flagged preroll=True. Later segments never get it.
The sealed segment also carries preroll_bytes, preroll_s, and
preroll_partial attributes: a snapshot shorter than the full ring is
partial pre-roll, flagged as such, and it is never padded up to the
full preroll_s to pretend otherwise.

Pause gaps: pause() opens an explicit gap record in ``gaps`` and
resume() (or end() from PAUSED) closes it with a measured duration
and the number of audio bytes dropped while paused. A pause is never
a silent hole in the timeline: callers drain closed gap records and
render them between segments. Ingest can add its own closed gap
records (frame gaps, reconnects, source failover) through note_gap().

End guard: end() is the direct, programmatic end. UIs that need the
BUILD_PLAN two-step guard use request_end() followed by
confirm_end(): confirm_end() raises InvalidTransition unless a
request is pending and still inside END_CONFIRM_WINDOW_S. A stray
single tap can arm the request, but it cannot end the session.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Optional

from .interfaces import SealedSegment

# PCM16 mono: every sample is exactly 2 bytes. All duration math and all
# buffer capacities in this module are in bytes, converted with this.
BYTES_PER_SAMPLE = 2


class SessionState(Enum):
    """Lifecycle states for one recording session."""

    IDLE = "IDLE"
    ARMED = "ARMED"
    RECORDING = "RECORDING"
    PAUSED = "PAUSED"
    ENDED = "ENDED"


class InvalidTransition(Exception):
    """Raised when a control is pressed in a state that forbids it."""


class RingBuffer:
    """Fixed capacity byte buffer that overwrites the oldest bytes first.

    Used for pre-roll: we always want the newest capacity_bytes of audio,
    in oldest-to-newest order, and we never want to grow without bound
    while a session sits armed waiting for the human to press start.
    """

    def __init__(self, capacity_bytes: int) -> None:
        if capacity_bytes < 0:
            raise ValueError("capacity_bytes must be >= 0")
        self.capacity_bytes = capacity_bytes
        self._buf = bytearray()

    def push(self, data: bytes) -> None:
        """Append data, discarding the oldest bytes beyond capacity."""
        if self.capacity_bytes == 0:
            # Zero capacity means pre-roll is disabled. Keep nothing.
            return
        if len(data) >= self.capacity_bytes:
            # The incoming chunk alone fills the ring. Keep only its tail.
            self._buf = bytearray(data[-self.capacity_bytes :])
            return
        self._buf.extend(data)
        overflow = len(self._buf) - self.capacity_bytes
        if overflow > 0:
            # FIFO overwrite: drop from the front, keep the newest bytes.
            del self._buf[:overflow]

    def snapshot(self) -> bytes:
        """Return the buffered bytes, oldest first, without clearing."""
        return bytes(self._buf)

    def clear(self) -> None:
        self._buf.clear()

    def __len__(self) -> int:
        return len(self._buf)


# Seconds a request_end() stays confirmable. Matches the spirit of the
# BUILD_PLAN hold-then-confirm guard: long enough for a deliberate
# second step, short enough that a forgotten request cannot be
# confirmed by an unrelated tap much later.
END_CONFIRM_WINDOW_S = 30.0


class SessionController:
    """Owns one session: its state, pre-roll, open buffer, and segments."""

    def __init__(
        self,
        sample_rate: int = 16000,
        preroll_s: int = 10,
        initial_seq: int = 0,
    ) -> None:
        self.sample_rate = sample_rate
        self.preroll_s = preroll_s
        # Seq of the next segment to seal. Starts at 0 for a fresh
        # session. A controller rebuilt after a server restart passes
        # the count of segments already stored for the session, so seq
        # (and therefore the wire seg_idx) survives reconnects and
        # restarts instead of restarting at 0 and colliding.
        self._initial_seq = int(initial_seq or 0)
        # Pre-roll capacity in bytes: samples per second times bytes per
        # sample times the number of pre-roll seconds.
        preroll_capacity = sample_rate * BYTES_PER_SAMPLE * preroll_s
        self.preroll = RingBuffer(preroll_capacity)

        self._state = SessionState.IDLE
        # Every segment sealed so far, in order. seq is the list index.
        self.segments: list[SealedSegment] = []
        # Bytes thrown away because they arrived while PAUSED. Surfaced
        # so the UI can tell the user audio was lost, not silently eaten.
        self.dropped_bytes = 0

        # Audio for the segment currently being recorded (not yet sealed).
        self._current = bytearray()
        # Pre-roll snapshot taken by start(), waiting to be prepended to
        # segment 0. Empty bytes means there is nothing to prepend.
        self._pending_preroll = b""
        # Monotonic timestamp of start(), for session-relative timing.
        # Monotonic, not wall clock, so clock adjustments cannot move it.
        self.started_monotonic: Optional[float] = None

        # Explicit timeline gap records, in the order they opened.
        # A record is a dict: kind, duration_s (None while the gap is
        # still open), dropped_bytes, detail, after_seq (the seq of
        # the segment that will follow the gap), closed (bool).
        self.gaps: list[dict] = []
        # The one gap currently open (a pause), or None. At most one
        # pause gap can be open at a time: pause is only reachable
        # from RECORDING, and resume or end closes it.
        self._open_gap: Optional[dict] = None
        # Monotonic time of a pending request_end(), or None. See the
        # two-step end guard in the module docstring.
        self._end_requested_at: Optional[float] = None

    # ------------------------------------------------------------------
    # State access
    # ------------------------------------------------------------------

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def next_seq(self) -> int:
        """Seq the next sealed segment will carry."""
        return self._initial_seq + len(self.segments)

    @property
    def end_requested(self) -> bool:
        """True while a two-step end request is pending confirmation."""
        return self._end_requested_at is not None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require(self, action: str, *allowed: SessionState) -> None:
        """Guard a transition. Raise InvalidTransition outside allowed."""
        if self._state not in allowed:
            raise InvalidTransition(
                "cannot %s from state %s" % (action, self._state.name)
            )

    def _open_pause_gap(self) -> None:
        """Open the explicit gap record for a pause starting now."""
        gap = {
            "kind": "PAUSE",
            "duration_s": None,
            "dropped_bytes": 0,
            "detail": "session paused",
            "after_seq": self.next_seq,
            "closed": False,
            "_started_monotonic": time.monotonic(),
        }
        self.gaps.append(gap)
        self._open_gap = gap

    def _close_open_gap(self) -> None:
        """Close the open pause gap with its measured duration."""
        gap = self._open_gap
        if gap is None:
            return
        started = gap.pop("_started_monotonic", None)
        if started is not None:
            gap["duration_s"] = max(0.0, time.monotonic() - started)
        gap["closed"] = True
        self._open_gap = None

    def note_gap(
        self,
        kind: str,
        duration_s: Optional[float] = None,
        dropped_bytes: int = 0,
        detail: str = "",
    ) -> dict:
        """Record an already closed timeline gap found by the caller.

        Used by ingest for losses the controller cannot see itself:
        frame sequence gaps inside a segment, reconnect downtime,
        capture-source failover, staged frames evicted on the phone.
        duration_s stays None when the lost duration is unknown (for
        example a reconnect that replay may still backfill); the gap
        is still explicit in the timeline, just unmeasured.
        """
        gap = {
            "kind": str(kind),
            "duration_s": duration_s,
            "dropped_bytes": int(dropped_bytes or 0),
            "detail": str(detail or ""),
            "after_seq": self.next_seq,
            "closed": True,
        }
        self.gaps.append(gap)
        return gap

    def _seal_current(self) -> SealedSegment:
        """Seal the open buffer into a SealedSegment and start fresh.

        Segment 0, and only segment 0, gets the stashed pre-roll snapshot
        prepended. The preroll flag records whether that actually added
        any bytes, so downstream code can tell real pre-roll apart from
        a segment that simply happens to be first.
        """
        is_first = len(self.segments) == 0 and self._initial_seq == 0
        prefix = self._pending_preroll
        pcm = prefix + bytes(self._current)
        segment = SealedSegment(
            seq=self.next_seq,
            pcm=pcm,
            duration_s=len(pcm) / BYTES_PER_SAMPLE / self.sample_rate,
            preroll=bool(prefix) and is_first,
        )
        # Partial pre-roll is flagged, never padded: preroll_s is the
        # actual snapshot length, and preroll_partial says it was
        # shorter than the full ring (the wearer started talking less
        # than preroll_s before pressing start, or the ring had not
        # filled yet). Extra attributes on the dataclass instance;
        # the dataclass itself lives in interfaces.py.
        segment.preroll_bytes = len(prefix)  # type: ignore[attr-defined]
        segment.preroll_s = (  # type: ignore[attr-defined]
            len(prefix) / BYTES_PER_SAMPLE / self.sample_rate if prefix else 0.0
        )
        segment.preroll_partial = bool(prefix) and len(prefix) < (  # type: ignore[attr-defined]
            self.sample_rate * BYTES_PER_SAMPLE * self.preroll_s
        )
        self.segments.append(segment)
        # Fresh open buffer, and the pre-roll stash is spent either way:
        # it must never be prepended twice.
        self._current = bytearray()
        self._pending_preroll = b""
        return segment

    # ------------------------------------------------------------------
    # Controls (the only things that move the state machine)
    # ------------------------------------------------------------------

    def arm(self) -> None:
        """IDLE -> ARMED. The session is ready but not recording yet.

        Audio pushed while ARMED feeds the pre-roll ring only.
        """
        self._require("arm", SessionState.IDLE)
        self._state = SessionState.ARMED

    def start(self) -> None:
        """ARMED -> RECORDING. Open segment 0.

        Snapshots the pre-roll ring now. Audio recorded before this press
        is not lost, and audio from before arming is not included beyond
        the ring capacity.
        """
        self._require("start", SessionState.ARMED)
        self._pending_preroll = self.preroll.snapshot()
        # Drain semantics: the snapshot is taken exactly once. Clearing
        # the ring means no later code path can snapshot the same audio
        # a second time or mistake stale ring bytes for fresh pre-roll.
        self.preroll.clear()
        self.started_monotonic = time.monotonic()
        self._state = SessionState.RECORDING

    def push_audio(self, pcm: bytes) -> None:
        """Route one chunk of PCM16 audio according to the current state.

        IDLE / ARMED -> pre-roll ring. RECORDING -> open segment buffer.
        PAUSED -> dropped and counted. ENDED -> InvalidTransition.
        """
        if self._state in (SessionState.IDLE, SessionState.ARMED):
            self.preroll.push(pcm)
        elif self._state == SessionState.RECORDING:
            self._current.extend(pcm)
        elif self._state == SessionState.PAUSED:
            # Deliberate drop: a paused session must not grow a segment
            # the user thinks does not exist. Count it so it is visible,
            # both in the session total and in the open pause gap, so
            # the timeline can show exactly what the pause cost.
            self.dropped_bytes += len(pcm)
            if self._open_gap is not None:
                self._open_gap["dropped_bytes"] += len(pcm)
        else:  # ENDED
            raise InvalidTransition(
                "cannot push_audio from state %s" % self._state.name
            )

    def new_segment(self) -> SealedSegment:
        """RECORDING only. Seal the open segment and open the next one.

        This is the push-to-talk boundary: the human decided the previous
        speaker turn or topic ended here. Returns the sealed segment.

        Sealing nothing is an error, not an empty segment: if the open
        buffer is empty and no pre-roll snapshot is waiting, there is
        no segment to seal. This is also the debounce for the control:
        a double tap (or a replayed talk_end after a reconnect) lands
        here with an empty buffer and raises InvalidTransition instead
        of minting a zero length segment and advancing seq out of sync
        with the client's seg_idx.
        """
        self._require("new_segment", SessionState.RECORDING)
        if len(self._current) == 0 and not self._pending_preroll:
            raise InvalidTransition(
                "cannot new_segment with no audio in the open segment"
            )
        return self._seal_current()

    def pause(self) -> Optional[SealedSegment]:
        """RECORDING -> PAUSED. Seal the open segment, if it has audio.

        Returns the sealed segment, or None if the open buffer was empty.
        An empty pause does not consume the pre-roll stash: if segment 0
        has not recorded anything yet, its pre-roll is still waiting for
        the first real audio after resume().
        """
        self._require("pause", SessionState.RECORDING)
        sealed = None
        if len(self._current) > 0:
            sealed = self._seal_current()
        self._state = SessionState.PAUSED
        # The pause itself becomes an explicit timeline record. It is
        # closed by resume() or end() with its measured duration and
        # the bytes dropped while paused, so the transcript can render
        # the gap instead of stitching the neighbours together.
        self._open_pause_gap()
        return sealed

    def resume(self) -> None:
        """PAUSED -> RECORDING. Open a fresh segment buffer.

        Closes the pause gap opened by pause(): the segment that
        follows starts after the gap, never spanning it.
        """
        self._require("resume", SessionState.PAUSED)
        self._close_open_gap()
        self._state = SessionState.RECORDING

    def request_end(self) -> None:
        """Step one of the two-step end guard. Does not end anything.

        Legal from ARMED, RECORDING, or PAUSED (the states end() is
        legal from). Records the request time; confirm_end() only
        honours it inside END_CONFIRM_WINDOW_S. A repeated request
        inside the window is a no-op (debounce): it does not extend
        the window, so a stream of stray taps cannot keep a stale
        request alive forever.
        """
        self._require(
            "request_end",
            SessionState.ARMED,
            SessionState.RECORDING,
            SessionState.PAUSED,
        )
        if self._end_requested_at is None:
            self._end_requested_at = time.monotonic()

    def confirm_end(self) -> list[SealedSegment]:
        """Step two of the two-step end guard. Ends the session.

        Raises InvalidTransition when no request_end() is pending or
        the pending request has expired. On success this behaves
        exactly like end().
        """
        if self._end_requested_at is None:
            raise InvalidTransition(
                "cannot confirm_end without a pending request_end"
            )
        if time.monotonic() - self._end_requested_at > END_CONFIRM_WINDOW_S:
            self._end_requested_at = None
            raise InvalidTransition(
                "cannot confirm_end: the end request has expired"
            )
        return self.end()

    def end(self) -> list[SealedSegment]:
        """ARMED, RECORDING, or PAUSED -> ENDED. Finish the session.

        Seals the open segment if it is non-empty and has not already
        been sealed by pause(). Returns only the segments sealed by
        this call, so callers know exactly what is new.

        This is the direct end used by programmatic callers (REST,
        session_end on the wire, where the client's own UI already ran
        its hold and confirm steps). Interactive callers that need the
        guard on this side should use request_end() + confirm_end().
        """
        self._require(
            "end",
            SessionState.ARMED,
            SessionState.RECORDING,
            SessionState.PAUSED,
        )
        sealed_now: list[SealedSegment] = []
        if self._state == SessionState.RECORDING and len(self._current) > 0:
            sealed_now.append(self._seal_current())
        # Ending from PAUSED closes the open pause gap at the end
        # time: the gap is part of the timeline even when the session
        # never resumes.
        self._close_open_gap()
        # In PAUSED the open buffer is empty by construction (pause
        # sealed it, and push_audio drops while paused), and in ARMED
        # recording never started, so there is nothing else to seal.
        # Any unspent pre-roll stash dies with the session.
        self._current = bytearray()
        self._pending_preroll = b""
        self._end_requested_at = None
        self._state = SessionState.ENDED
        return sealed_now

    def reset(self) -> None:
        """Any state -> IDLE. Clear everything so the controller is reusable."""
        self._state = SessionState.IDLE
        self.segments = []
        self.dropped_bytes = 0
        self.preroll.clear()
        self._current = bytearray()
        self._pending_preroll = b""
        self.started_monotonic = None
        self.gaps = []
        self._open_gap = None
        self._end_requested_at = None
        self._initial_seq = 0
