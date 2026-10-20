"""Contract tests for lexia.tts.

Contracts covered:
* FakeTTSEngine().synthesize(text, tmp_path) writes a .wav file that
  the stdlib wave module can open, with a duration greater than zero.
* get_tts_engine() respects the LEXIA_TTS_ENGINE environment variable.
* Empty text raises ValueError.
"""

import wave
from pathlib import Path

import pytest

from lexia.tts import FakeTTSEngine, get_tts_engine

SAMPLE_TEXT = "hello world this is a test"


def resolve_wav_path(result, tmp_path):
    candidates = []
    if isinstance(result, (str, Path)):
        candidates.append(Path(result))
    if result is not None and not isinstance(result, (str, Path, tuple)):
        for attr in ("path", "audio_path", "file_path", "output_path", "wav_path"):
            value = getattr(result, attr, None)
            if value:
                candidates.append(Path(value))
    if isinstance(result, tuple):
        for item in result:
            if isinstance(item, (str, Path)):
                candidates.append(Path(item))
    if Path(tmp_path).is_file():
        candidates.append(Path(tmp_path))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    found = sorted(Path(tmp_path).rglob("*.wav"))
    assert found, "synthesize did not write any .wav file under %s" % tmp_path
    return found[0]


def result_duration(result, wav_path):
    if result is not None and not isinstance(result, (str, Path)):
        value = getattr(result, "duration_seconds", None)
        if value is not None:
            return value
        if isinstance(result, dict) and "duration_seconds" in result:
            return result["duration_seconds"]
    if isinstance(result, dict) and "duration_seconds" in result:
        return result["duration_seconds"]
    with wave.open(str(wav_path), "rb") as wav_file:
        return wav_file.getnframes() / float(wav_file.getframerate())


def test_fake_engine_writes_openable_wav(tmp_path):
    engine = FakeTTSEngine()
    result = engine.synthesize(SAMPLE_TEXT, tmp_path)

    wav_path = resolve_wav_path(result, tmp_path)
    assert wav_path.suffix == ".wav"

    with wave.open(str(wav_path), "rb") as wav_file:
        assert wav_file.getnframes() > 0
        wave_duration = wav_file.getnframes() / float(wav_file.getframerate())
    assert wave_duration > 0

    assert result_duration(result, wav_path) > 0


def test_fake_engine_rejects_empty_text(tmp_path):
    engine = FakeTTSEngine()
    with pytest.raises(ValueError):
        engine.synthesize("", tmp_path)


def test_get_tts_engine_respects_env(monkeypatch):
    monkeypatch.setenv("LEXIA_TTS_ENGINE", "fake")
    engine = get_tts_engine()
    assert isinstance(engine, FakeTTSEngine) or "fake" in type(
        engine
    ).__name__.lower() or str(
        getattr(engine, "name", getattr(engine, "engine", ""))
    ).lower() == "fake"
