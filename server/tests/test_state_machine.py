"""Tests for SessionController and RingBuffer (state_machine.py).

No VAD is involved: segments exist only because a control was pressed.
Durations are checked against PCM16 byte counts: at sample_rate samples
per second and 2 bytes per sample, one second is sample_rate * 2 bytes.
"""

import pytest

from onrecord.state_machine import (
    InvalidTransition,
    RingBuffer,
    SessionController,
    SessionState,
)

SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2


def pcm(seconds, fill=1, sample_rate=SAMPLE_RATE):
    """Return PCM16 bytes for the given duration, filled with one byte."""
    n = int(seconds * sample_rate * 2)
    return bytes([fill]) * n


def make_controller(preroll_s=10, sample_rate=SAMPLE_RATE):
    return SessionController(sample_rate=sample_rate, preroll_s=preroll_s)


# ----------------------------------------------------------------------
# Happy path
# ----------------------------------------------------------------------


def test_happy_path_first_segment_has_preroll_second_does_not():
    c = make_controller()
    assert c.state == SessionState.IDLE

    c.arm()
    assert c.state == SessionState.ARMED

    c.push_audio(pcm(1.0, fill=1))
    c.start()
    assert c.state == SessionState.RECORDING

    c.push_audio(pcm(2.0, fill=2))
    seg0 = c.new_segment()
    assert seg0.seq == 0
    assert seg0.preroll is True
    assert seg0.duration_s == pytest.approx(3.0)
    assert len(seg0.pcm) == int(3.0 * BYTES_PER_SECOND)
    assert seg0.pcm[:BYTES_PER_SECOND] == pcm(1.0, fill=1)
    assert seg0.pcm[BYTES_PER_SECOND:] == pcm(2.0, fill=2)

    c.push_audio(pcm(1.5, fill=3))
    seg1 = c.new_segment()
    assert seg1.seq == 1
    assert seg1.preroll is False
    assert seg1.duration_s == pytest.approx(1.5)
    assert seg1.pcm == pcm(1.5, fill=3)

    assert c.segments == [seg0, seg1]


def test_preroll_pushed_while_idle_is_also_captured():
    c = make_controller()
    c.push_audio(pcm(0.5, fill=1))
    c.arm()
    c.start()
    c.push_audio(pcm(0.5, fill=2))
    seg = c.new_segment()
    assert seg.seq == 0
    assert seg.preroll is True
    assert seg.duration_s == pytest.approx(1.0)


def test_no_preroll_audio_means_first_segment_flag_is_false():
    c = make_controller()
    c.arm()
    c.start()
    c.push_audio(pcm(1.0))
    seg = c.new_segment()
    assert seg.seq == 0
    assert seg.preroll is False
    assert seg.duration_s == pytest.approx(1.0)


# ----------------------------------------------------------------------
# Invalid transitions
# ----------------------------------------------------------------------


def test_start_from_idle_raises():
    c = make_controller()
    with pytest.raises(InvalidTransition):
        c.start()
    assert c.state == SessionState.IDLE


def test_new_segment_from_armed_raises():
    c = make_controller()
    c.arm()
    with pytest.raises(InvalidTransition):
        c.new_segment()
    assert c.state == SessionState.ARMED


def test_new_segment_from_idle_raises():
    c = make_controller()
    with pytest.raises(InvalidTransition):
        c.new_segment()


def test_resume_from_recording_raises():
    c = make_controller()
    c.arm()
    c.start()
    with pytest.raises(InvalidTransition):
        c.resume()
    assert c.state == SessionState.RECORDING


def test_resume_from_idle_raises():
    c = make_controller()
    with pytest.raises(InvalidTransition):
        c.resume()


def test_push_audio_after_end_raises():
    c = make_controller()
    c.arm()
    c.start()
    c.push_audio(pcm(0.5))
    c.end()
    assert c.state == SessionState.ENDED
    with pytest.raises(InvalidTransition):
        c.push_audio(pcm(0.5))


def test_arm_from_armed_raises():
    c = make_controller()
    c.arm()
    with pytest.raises(InvalidTransition):
        c.arm()


def test_pause_from_idle_raises():
    c = make_controller()
    with pytest.raises(InvalidTransition):
        c.pause()


def test_end_from_idle_raises():
    c = make_controller()
    with pytest.raises(InvalidTransition):
        c.end()


# ----------------------------------------------------------------------
# Pause, dropped audio, resume, end
# ----------------------------------------------------------------------


def test_pause_seals_open_segment_and_dropped_audio_is_counted():
    c = make_controller()
    c.arm()
    c.start()
    c.push_audio(pcm(1.0, fill=1))

    sealed = c.pause()
    assert c.state == SessionState.PAUSED
    assert sealed is not None
    assert sealed.seq == 0
    assert sealed.duration_s == pytest.approx(1.0)
    assert sealed.pcm == pcm(1.0, fill=1)

    dropped = pcm(0.5, fill=2)
    c.push_audio(dropped)
    c.push_audio(dropped)
    assert c.dropped_bytes == len(dropped) * 2

    c.resume()
    assert c.state == SessionState.RECORDING
    c.push_audio(pcm(1.0, fill=3))

    final = c.end()
    assert c.state == SessionState.ENDED
    assert len(final) == 1
    assert final[0].seq == 1
    assert final[0].duration_s == pytest.approx(1.0)
    assert final[0].pcm == pcm(1.0, fill=3)

    assert len(c.segments) == 2
    for seg in c.segments:
        assert bytes([2]) * 10 not in seg.pcm or seg.pcm == pcm(1.0, fill=1)
        assert dropped not in seg.pcm


def test_pause_with_no_audio_returns_none():
    c = make_controller()
    c.arm()
    c.start()
    assert c.pause() is None
    assert c.state == SessionState.PAUSED
    assert c.segments == []


def test_end_from_armed_with_no_audio_returns_empty():
    c = make_controller()
    c.arm()
    c.push_audio(pcm(1.0))
    result = c.end()
    assert result == []
    assert c.state == SessionState.ENDED
    assert c.segments == []


def test_end_from_recording_seals_final_segment():
    c = make_controller()
    c.arm()
    c.start()
    c.push_audio(pcm(2.0))
    result = c.end()
    assert len(result) == 1
    assert result[0].seq == 0
    assert result[0].duration_s == pytest.approx(2.0)
    assert c.segments == result


def test_end_from_paused_returns_empty_and_keeps_prior_segments():
    c = make_controller()
    c.arm()
    c.start()
    c.push_audio(pcm(1.0))
    sealed = c.pause()
    assert sealed is not None
    result = c.end()
    assert result == []
    assert c.segments == [sealed]
    assert c.state == SessionState.ENDED


# ----------------------------------------------------------------------
# Pre-roll cap
# ----------------------------------------------------------------------


def test_preroll_cap_with_small_preroll():
    c = make_controller(preroll_s=2)
    c.arm()
    c.push_audio(pcm(3.0, fill=1))
    c.start()
    c.push_audio(pcm(1.0, fill=2))
    seg = c.new_segment()
    assert seg.preroll is True
    assert seg.duration_s == pytest.approx(3.0)
    assert len(seg.pcm) == int(3.0 * BYTES_PER_SECOND)
    assert seg.pcm[: int(2.0 * BYTES_PER_SECOND)] == pcm(2.0, fill=1)
    assert seg.pcm[int(2.0 * BYTES_PER_SECOND) :] == pcm(1.0, fill=2)


def test_preroll_cap_default_ten_seconds_with_low_sample_rate():
    low_rate = 1000
    c = make_controller(preroll_s=10, sample_rate=low_rate)
    c.arm()
    c.push_audio(pcm(12.0, fill=1, sample_rate=low_rate))
    c.start()
    c.push_audio(pcm(1.0, fill=2, sample_rate=low_rate))
    seg = c.new_segment()
    assert seg.duration_s == pytest.approx(11.0)
    assert seg.duration_s <= 11.0


# ----------------------------------------------------------------------
# RingBuffer unit tests
# ----------------------------------------------------------------------


def test_ring_buffer_wraparound_keeps_newest_bytes_in_order():
    rb = RingBuffer(8)
    rb.push(b"abcd")
    assert rb.snapshot() == b"abcd"
    rb.push(b"efghij")
    assert rb.snapshot() == b"cdefghij"
    assert len(rb) == 8


def test_ring_buffer_wraparound_across_many_small_pushes():
    rb = RingBuffer(5)
    for ch in b"abcdefgh":
        rb.push(bytes([ch]))
    assert rb.snapshot() == b"defgh"


def test_ring_buffer_snapshot_is_non_destructive():
    rb = RingBuffer(10)
    rb.push(b"hello")
    first = rb.snapshot()
    second = rb.snapshot()
    assert first == b"hello"
    assert second == first
    assert len(rb) == 5
    rb.push(b"!")
    assert rb.snapshot() == b"hello!"


def test_ring_buffer_clear_empties():
    rb = RingBuffer(10)
    rb.push(b"some audio bytes")
    rb.clear()
    assert len(rb) == 0
    assert rb.snapshot() == b""
    rb.push(b"xy")
    assert rb.snapshot() == b"xy"


def test_ring_buffer_oversized_single_chunk_keeps_tail():
    rb = RingBuffer(4)
    rb.push(b"0123456789")
    assert rb.snapshot() == b"6789"
    assert len(rb) == 4


def test_ring_buffer_exact_capacity_chunk():
    rb = RingBuffer(4)
    rb.push(b"ab")
    rb.push(b"cdef")
    assert rb.snapshot() == b"cdef"


def test_ring_buffer_zero_capacity_keeps_nothing():
    rb = RingBuffer(0)
    rb.push(b"abc")
    assert rb.snapshot() == b""
    assert len(rb) == 0


# ----------------------------------------------------------------------
# Reset
# ----------------------------------------------------------------------


def test_reset_returns_to_idle_and_clears_segments():
    c = make_controller()
    c.arm()
    c.push_audio(pcm(0.5, fill=1))
    c.start()
    c.push_audio(pcm(1.0, fill=2))
    seg = c.new_segment()
    assert seg.seq == 0
    c.push_audio(pcm(0.5, fill=3))
    c.pause()
    c.push_audio(pcm(0.25, fill=4))
    assert c.dropped_bytes > 0
    assert len(c.segments) >= 1

    c.reset()
    assert c.state == SessionState.IDLE
    assert c.segments == []
    assert c.dropped_bytes == 0
    assert c.preroll.snapshot() == b""
    assert len(c.preroll) == 0
    assert c.started_monotonic is None

    c.arm()
    c.start()
    c.push_audio(pcm(1.0, fill=5))
    seg_after = c.new_segment()
    assert seg_after.seq == 0
    assert seg_after.pcm == pcm(1.0, fill=5)
    assert seg_after.preroll is False


def test_reset_from_idle_is_allowed():
    c = make_controller()
    c.reset()
    assert c.state == SessionState.IDLE
    assert c.segments == []
