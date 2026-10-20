"""Configuration for the OnRecord server.

Settings come from environment variables only, with local-first defaults
that match .env.example at the repo root. This module uses only the
standard library, so it imports cleanly in tests and offline smoke runs.
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_TRUTHY = frozenset({"1", "true", "yes"})


def _env_str(name: str) -> Optional[str]:
    """Return the env var value, or None if it is unset or blank."""
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _env_float(name: str, default: float) -> float:
    value = _env_str(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        raise ValueError(
            f"environment variable {name} must be a number, got {value!r}"
        ) from None


def _env_int(name: str, default: int) -> int:
    value = _env_str(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(
            f"environment variable {name} must be an integer, got {value!r}"
        ) from None


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env_str(name)
    if value is None:
        return default
    return value.lower() in _TRUTHY


@dataclass(frozen=True)
class Settings:
    """Immutable server settings, built from the environment.

    Defaults mirror .env.example: SQLite under data_dir when
    database_url is None, and Chroma storage under data_dir / "chroma"
    when chroma_dir is not set explicitly.
    """

    data_dir: Path = Path("./data")
    database_url: Optional[str] = None
    chroma_dir: Path = Path("./data/chroma")
    host: str = "127.0.0.1"
    port: int = 8000
    sample_rate: int = 16000
    face_threshold: float = 0.45
    voice_threshold: float = 0.75
    face_only_threshold: float = 0.60
    voice_only_threshold: float = 0.85
    stt_model: str = "small.en"
    slack_webhook_url: Optional[str] = None
    rehearsal: bool = False

    def __post_init__(self) -> None:
        # Thresholds are cosine similarities. A value outside [0, 1]
        # is always a configuration mistake, and a silent one: above 1
        # no speaker can ever match (everyone stays Unknown), below 0
        # unrelated speakers start matching. Fail loudly at startup
        # instead of failing the demo quietly.
        for field_name in (
            "face_threshold",
            "voice_threshold",
            "face_only_threshold",
            "voice_only_threshold",
        ):
            value = getattr(self, field_name)
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(
                    f"{field_name} must be between 0 and 1, got {value!r}"
                )
        if int(self.sample_rate) <= 0:
            raise ValueError(
                f"sample_rate must be positive, got {self.sample_rate!r}"
            )
        if not 0 <= int(self.port) <= 65535:
            raise ValueError(
                f"port must be between 0 and 65535, got {self.port!r}"
            )

    @property
    def db_path(self) -> Path:
        """Path of the local SQLite database file."""
        return self.data_dir / "onrecord.db"


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached settings, creating storage dirs on first call.

    Reads DATABASE_URL, DATA_DIR, CHROMA_DIR, HOST, PORT, SAMPLE_RATE,
    FACE_THRESHOLD, VOICE_THRESHOLD, FACE_ONLY_THRESHOLD,
    VOICE_ONLY_THRESHOLD, STT_MODEL, SLACK_WEBHOOK_URL, and
    ONRECORD_REHEARSAL from the environment. Blank values count as
    unset.
    """
    data_dir = Path(_env_str("DATA_DIR") or "./data")

    chroma_value = _env_str("CHROMA_DIR")
    chroma_dir = Path(chroma_value) if chroma_value is not None else data_dir / "chroma"

    settings = Settings(
        data_dir=data_dir,
        database_url=_env_str("DATABASE_URL"),
        chroma_dir=chroma_dir,
        host=_env_str("HOST") or "127.0.0.1",
        port=_env_int("PORT", 8000),
        sample_rate=_env_int("SAMPLE_RATE", 16000),
        face_threshold=_env_float("FACE_THRESHOLD", 0.45),
        voice_threshold=_env_float("VOICE_THRESHOLD", 0.75),
        face_only_threshold=_env_float("FACE_ONLY_THRESHOLD", 0.60),
        voice_only_threshold=_env_float("VOICE_ONLY_THRESHOLD", 0.85),
        stt_model=_env_str("STT_MODEL") or "small.en",
        slack_webhook_url=_env_str("SLACK_WEBHOOK_URL"),
        rehearsal=_env_bool("ONRECORD_REHEARSAL", False),
    )

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.chroma_dir.mkdir(parents=True, exist_ok=True)

    return settings


def reset_settings_cache() -> None:
    """Clear the cached settings so tests can re-read the environment."""
    get_settings.cache_clear()
