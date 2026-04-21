"""
app/utils/file_utils.py
-----------------------
Filesystem helpers used across the application.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def ensure_dir(path: str | Path) -> Path:
    """Create directory (and parents) if it does not exist. Returns a Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def path_is_writable(path: str | Path) -> bool:
    """Return True if the given directory path exists and is writable."""
    p = Path(path)
    return p.is_dir() and os.access(p, os.W_OK)


def path_exists(path: str | Path) -> bool:
    return Path(path).exists()


def file_size_bytes(path: str | Path) -> int:
    """Return file size in bytes, or 0 if the file cannot be stat'd."""
    try:
        return Path(path).stat().st_size
    except OSError as exc:
        logger.warning("Could not stat file %s: %s", path, exc)
        return 0


def scan_m4b_files(folder: str | Path) -> list[Path]:
    """
    Recursively find all supported audiobook files (.m4b, .m4a, .mp3) under the given folder.
    Returns a sorted list of absolute Paths (files only, not directories).
    """
    root = Path(folder)
    if not root.is_dir():
        logger.warning("Scan target is not a directory: %s", folder)
        return []

    found = []
    for ext in ['*.m4b', '*.m4a', '*.mp3']:
        found.extend([f for f in root.rglob(ext) if f.is_file()])

    found = sorted(set(found))  # Remove duplicates and sort
    logger.info("Found %d audiobook file(s) under %s", len(found), folder)
    return found


def get_audio_format(file_path: str | Path) -> str:
    """
    Determine the audio format from file extension.
    Returns: 'm4b', 'm4a', 'mp3', or 'unknown'
    """
    ext = Path(file_path).suffix.lower()
    if ext in {'.m4b', '.m4a', '.mp3'}:
        return ext.lstrip('.')
    return 'unknown'
