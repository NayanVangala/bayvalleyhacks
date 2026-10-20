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


class SessionController:
    """Owns one session: its state, pre-roll, open buffer, and segments."""

    def __init__(self, sample_rate: int = 16000, preroll_s: int = 10) -> None:
        self.sample_rate = sample_rate
        self.preroll_s = preroll_s
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

    # ------------------------------------------------------------------
    # State access
    # ------------------------------------------------------------------

    @property
    def state(self) -> SessionState:
        return self._state

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require(self, action: str, *allowed: SessionState) -> None:
        """Guard a transition. Raise InvalidTransition outside allowed."""
        if self._state not in allowed:
            raise InvalidTransition(
                "cannot %s from state %s" % (action, self._state.name)
            )

    def _seal_current(self) -> SealedSegment:
        """Seal the open buffer into a SealedSegment and start fresh.

        Segment 0, and only segment 0, gets the stashed pre-roll snapshot
        prepended. The preroll flag records whether that actually added
        any bytes, so downstream code can tell real pre-roll apart from
        a segment that simply happens to be first.
        """
        is_first = len(self.segments) == 0
        prefix = self._pending_preroll if is_first else b""
        pcm = prefix + bytes(self._current)
        segment = SealedSegment(
            seq=len(self.segments),
            pcm=pcm,
            duration_s=len(pcm) / BYTES_PER_SAMPLE / self.sample_rate,
            preroll=bool(prefix),
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
            # the user thinks does not exist. Count it so it is visible.
            self.dropped_bytes += len(pcm)
        else:  # ENDED
            raise InvalidTransition(
                "cannot push_audio from state %s" % self._state.name
            )

    def new_segment(self) -> SealedSegment:
        """RECORDING only. Seal the open segment and open the next one.

        This is the push-to-talk boundary: the human decided the previous
        speaker turn or topic ended here. Returns the sealed segment.
        """
        self._require("new_segment", SessionState.RECORDING)
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
        return sealed

    def resume(self) -> None:
        """PAUSED -> RECORDING. Open a fresh segment buffer."""
        self._require("resume", SessionState.PAUSED)
        self._state = SessionState.RECORDING

    def end(self) -> list[SealedSegment]:
        """ARMED, RECORDING, or PAUSED -> ENDED. Finish the session.

        Seals the open segment if it is non-empty and has not already
        been sealed by pause(). Returns only the segments sealed by
        this call, so callers know exactly what is new.
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
        # In PAUSED the open buffer is empty by construction (pause
        # sealed it, and push_audio drops while paused), and in ARMED
        # recording never started, so there is nothing else to seal.
        # Any unspent pre-roll stash dies with the session.
        self._current = bytearray()
        self._pending_preroll = b""
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
