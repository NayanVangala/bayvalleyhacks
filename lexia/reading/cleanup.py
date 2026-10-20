"""Text cleanup for OCR output.

Pure stdlib. Normalizes whitespace, repairs hyphenated line breaks,
and drops short junk lines produced by OCR.
"""

from __future__ import annotations

import re
import unicodedata

_UNICODE_SPACES = (
    "\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005"
    "\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000\ufeff"
)

_SPACE_TABLE = {ord(ch): " " for ch in _UNICODE_SPACES}
_SPACE_TABLE[ord("\t")] = " "
_SPACE_TABLE[ord("\x0b")] = " "
_SPACE_TABLE[ord("\x0c")] = " "

_HYPHEN_BREAK_RE = re.compile(r"([A-Za-z])-\s*\n\s*([A-Za-z])")
_MANY_NEWLINES_RE = re.compile(r"\n{3,}")
_MANY_SPACES_RE = re.compile(r" {2,}")


def clean_text(raw: str) -> str:
    """Return a cleaned version of raw OCR text."""
    if not raw:
        return ""

    text = unicodedata.normalize("NFKC", raw)

    # Normalize line endings and unicode line separators.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u2028", "\n").replace("\u2029", "\n")

    # Normalize unicode spaces and tabs to a plain space.
    text = text.translate(_SPACE_TABLE)

    # Join hyphenated line breaks: "exam-\nple" becomes "example".
    text = _HYPHEN_BREAK_RE.sub(r"\1\2", text)

    # Collapse 3 or more newlines into a single line break.
    # A single blank line (two newlines) is kept as a paragraph break.
    text = _MANY_NEWLINES_RE.sub("\n", text)

    cleaned_lines: list[str] = []
    for line in text.split("\n"):
        # Collapse runs of spaces and strip trailing spaces per line.
        line = _MANY_SPACES_RE.sub(" ", line).rstrip()
        # Also strip leading spaces for consistency, keeping empties.
        line = line.lstrip() if line.strip() else ""

        # Drop junk lines: lines with no letters at all, such as
        # symbol only OCR noise, and short lines with no letters.
        # Empty lines are kept so paragraph breaks survive.
        stripped = line.strip()
        if stripped and not any(ch.isalpha() for ch in stripped):
            continue
        cleaned_lines.append(line)

    result = "\n".join(cleaned_lines)

    # Re-collapse newlines after junk removal and trim the whole text.
    result = _MANY_NEWLINES_RE.sub("\n", result)
    return result.strip()
