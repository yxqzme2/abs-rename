"""
app/path_engine/sanitizer.py
-----------------------------
Sanitizes strings for use as filesystem path segments.

Handles both Windows and Linux restrictions:
- Invalid characters replaced with a hyphen
- Windows reserved device names prefixed with underscore
- Trailing periods and spaces stripped (Windows restriction)
- Consecutive separators collapsed
"""

from __future__ import annotations

import re

# Characters forbidden in Windows filenames (also covers Linux's '/')
_WIN_INVALID = re.compile(r'[\\/:*?"<>|]')

# One or more slashes/backslashes (catches path traversal attempts)
_PATH_SEP = re.compile(r"[/\\]+")

# Trailing dots or spaces on any path segment (Windows restriction)
_TRAILING_DOT_SPACE = re.compile(r"[\s.]+$")

# Leading dots or spaces
_LEADING_DOT_SPACE = re.compile(r"^[\s.]+")

# Collapse runs of spaces, hyphens, or underscores that result from substitution
_MULTI_SPACE = re.compile(r" {2,}")
_MULTI_HYPHEN = re.compile(r"-{2,}")

# Windows reserved device names (case-insensitive, full segment only)
_WIN_RESERVED = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$",
    re.IGNORECASE,
)


def sanitize_segment(text: str) -> str:
    """
    Sanitize a single path segment (folder name or filename without extension).

    - Replaces invalid characters with a hyphen
    - Strips leading/trailing dots and spaces
    - Prefixes Windows reserved names with underscore
    - Collapses duplicate spaces and hyphens
    - Returns "Unknown" if the result is empty
    """
    if not text:
        return "Unknown"

    # Replace path separators and invalid characters
    text = _PATH_SEP.sub("-", text)
    text = _WIN_INVALID.sub("-", text)

    # Strip leading and trailing problem chars
    text = _LEADING_DOT_SPACE.sub("", text)
    text = _TRAILING_DOT_SPACE.sub("", text)

    # Collapse duplicates
    text = _MULTI_SPACE.sub(" ", text)
    text = _MULTI_HYPHEN.sub("-", text)

    text = text.strip()

    # Prefix Windows reserved device names
    if _WIN_RESERVED.match(text):
        text = f"_{text}"

    return text or "Unknown"


def sanitize_path(path: str) -> str:
    """
    Sanitize a full relative path (e.g., "Author/Series/Title").
    Each segment is sanitized independently, then rejoined with '/'.
    """
    # Split on either separator
    segments = re.split(r"[/\\]", path)
    sanitized = [sanitize_segment(seg) for seg in segments if seg]
    return "/".join(sanitized)
