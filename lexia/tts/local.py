"""Local TTS engine backed by the espeak CLI or pyttsx3."""

from __future__ import annotations

import hashlib
import pathlib
import shutil
import subprocess
import wave

from .base import SpeakResult, TTSUnavailable

WORDS_PER_SECOND = 2.6


def _wav_duration(path: pathlib.Path, text: str) -> float:
    """Return duration from the WAV header, falling back to a word estimate."""
    try:
        with wave.open(str(path), "rb") as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
            if rate:
                return frames / float(rate)
    except Exception:
        pass
    word_count = len(text.split()) if text else 0
    return word_count / WORDS_PER_SECOND


class LocalTTSEngine:
    name = "local"

    def synthesize(self, text: str, out_dir: pathlib.Path) -> SpeakResult:
        if not text or not text.strip():
            raise TTSUnavailable("LocalTTSEngine requires non-empty text")

        out_dir = pathlib.Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        # One file per text, named by content hash like FakeTTSEngine.
        # A single fixed filename made every speak overwrite the last
        # one, so an earlier session's /audio/ URL served the wrong text.
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        out_path = out_dir / f"local_{digest}.wav"

        espeak = shutil.which("espeak")
        if espeak:
            try:
                result = subprocess.run(
                    [espeak, "-w", str(out_path), text],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode != 0:
                    stderr = (result.stderr or "").strip()
                    raise TTSUnavailable(
                        f"espeak failed with exit code {result.returncode}: {stderr}"
                    )
                if not out_path.exists():
                    raise TTSUnavailable("espeak ran but produced no output file")
            except TTSUnavailable:
                raise
            except Exception as exc:
                raise TTSUnavailable(f"espeak synthesis failed: {exc}") from exc

            duration = _wav_duration(out_path, text)
            return SpeakResult(
                audio_path=str(out_path),
                duration_seconds=duration,
                engine=self.name,
            )

        try:
            import pyttsx3
        except Exception as exc:
            raise TTSUnavailable(
                "No local TTS available: espeak binary not found and "
                f"pyttsx3 could not be imported: {exc}"
            ) from exc

        try:
            engine = pyttsx3.init()
            engine.save_to_file(text, str(out_path))
            engine.runAndWait()
        except Exception as exc:
            raise TTSUnavailable(
                "No local TTS available: espeak binary not found and "
                f"pyttsx3 synthesis failed: {exc}"
            ) from exc

        if not out_path.exists():
            raise TTSUnavailable(
                "No local TTS available: espeak binary not found and "
                "pyttsx3 ran but produced no output file"
            )

        duration = _wav_duration(out_path, text)
        return SpeakResult(
            audio_path=str(out_path),
            duration_seconds=duration,
            engine=self.name,
        )
