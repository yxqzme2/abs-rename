"""
app/models/local_audiobook.py
------------------------------
Pydantic models for a discovered .m4b file and its embedded tag data.

LocalAudiobook  — file-level record (path, size, scan status)
LocalMetadata   — tag values extracted from inside the file (1:1)
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ScanStatus(str, Enum):
    PENDING          = "pending"
    SCANNED          = "scanned"
    MATCHED          = "matched"
    UNMATCHED        = "unmatched"
    REVIEW_REQUIRED  = "review_required"
    ERROR            = "error"


class LocalAudiobook(BaseModel):
    """File-level record created by the scanner for each .m4b found."""
    id:           int | None = None
    batch_run_id: int
    source_path:  str           # absolute path to the file
    filename:     str           # just the filename with extension
    folder_path:  str           # parent directory
    extension:    str = ".m4b"
    file_size:    int = 0       # bytes
    scan_status:  ScanStatus = ScanStatus.PENDING


class LocalMetadata(BaseModel):
    """
    Tag data read from the .m4b file by the metadata reader.
    All fields are optional — tags may be absent or unreadable.
    """
    id:                     int | None = None
    local_audiobook_id:     int

    duration_seconds:       float | None = None

    title_from_tags:        str | None = None
    author_from_tags:       str | None = None
    album_from_tags:        str | None = None
    narrator_from_tags:     str | None = None
    series_from_tags:       str | None = None
    series_index_from_tags: str | None = None   # raw string e.g. "2", "2.5"

    has_embedded_cover:     bool = False
    raw_tags_json:          str | None = None   # full dump for debugging

    def series_index_as_float(self) -> float | None:
        """
        Convert the raw series index string to a float for scoring.
        Returns None if not present or not parseable.
        Examples: "2" -> 2.0, "Book 2.5" -> 2.5, "Part 1" -> 1.0
        """
        if not self.series_index_from_tags:
            return None
        raw = self.series_index_from_tags.strip()
        # Try direct conversion first
        try:
            return float(raw)
        except ValueError:
            pass
        # Extract first number from strings like "Book 2.5", "Part 1"
        import re
        match = re.search(r"(\d+(?:\.\d+)?)", raw)
        if match:
            return float(match.group(1))
        return None


class LocalAudiobookWithMeta(BaseModel):
    """Combined view used throughout the app after scanning is complete."""
    audiobook: LocalAudiobook
    metadata:  LocalMetadata | None = None
