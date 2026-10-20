"""Shared pytest setup for the Lexia Read Mode test suite.

Runs before any test module imports the lexia package, so it is the
right place to:

* put the repo root on sys.path so ``import lexia.<module>`` works no
  matter where pytest is invoked from,
* force the fake OCR and TTS engines through the environment so the
  suite passes on machines with no tesseract binary and no espeak,
* point LEXIA_DATA_DIR at a throwaway temp directory so sessions and
  generated audio never touch real user data.
"""

import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("LEXIA_OCR_ENGINE", "fake")
os.environ.setdefault("LEXIA_TTS_ENGINE", "fake")
os.environ.setdefault(
    "LEXIA_DATA_DIR",
    tempfile.mkdtemp(prefix="lexia-tests-"),
)
