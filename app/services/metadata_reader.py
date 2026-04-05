"""
app/services/metadata_reader.py
--------------------------------
Reads embedded tag data from a .m4b file using mutagen.

Handles M4B/M4A (MP4 tags) and falls back gracefully when tags are
absent or malformed. Never raises — always returns a LocalMetadata
object even if all fields are None.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from mutagen.mp4 import MP4, MP4StreamInfoError
from mutagen import MutagenError

from app.models.local_audiobook import LocalMetadata

logger = logging.getLogger(__name__)

# --- Tag key mappings for MP4/M4B containers ---

# Standard iTunes/M4B tag atoms
_TITLE_KEYS     = ["©nam"]
_AUTHOR_KEYS    = ["©ART", "©wrt", "aART"]
_ALBUM_KEYS     = ["©alb"]
_NARRATOR_KEYS  = ["©nrt", "TXXX:NARRATOR", "narrator"]

# Series info is stored inconsistently across different taggers.
# We check all common locations.
_SERIES_KEYS    = ["©mvn", "TXXX:SERIES", "series", "©grp"]
_SERIES_IDX_KEYS = ["©mvi", "TXXX:SERIES-PART", "series-part", "©mvc"]


def _first_text(tags: dict, keys: list[str]) -> str | None:
    """Return the first non-empty string value found for any of the given keys."""
    for key in keys:
        val = tags.get(key)
        if val:
            # mutagen returns lists for most text tags
            if isinstance(val, list) and val:
                text = str(val[0]).strip()
                if text:
                    return text
            elif isinstance(val, str) and val.strip():
                return val.strip()
    return None


def _get_duration(audio: MP4) -> float | None:
    """Return duration in seconds from the MP4 stream info."""
    try:
        return float(audio.info.length)
    except (AttributeError, TypeError):
        return None


def _has_cover(tags: dict) -> bool:
    """Return True if an embedded cover art atom is present."""
    return "covr" in tags and bool(tags["covr"])


def read_metadata(file_path: str | Path, local_audiobook_id: int) -> LocalMetadata:
    """
    Open a .m4b file and extract all available tag data.

    Never raises — any exception is caught, logged, and an empty
    LocalMetadata is returned so the scanner can continue.

    Args:
        file_path: absolute path to the .m4b file
        local_audiobook_id: FK to link this metadata to its audiobook record

    Returns:
        LocalMetadata with whatever fields could be read
    """
    path = Path(file_path)
    base = LocalMetadata(local_audiobook_id=local_audiobook_id)

    try:
        audio = MP4(str(path))
    except MP4StreamInfoError as exc:
        logger.warning("Not a valid MP4/M4B file: %s — %s", path.name, exc)
        return base
    except MutagenError as exc:
        logger.warning("mutagen error reading %s: %s", path.name, exc)
        return base
    except Exception as exc:
        logger.warning("Unexpected error reading tags from %s: %s", path.name, exc)
        return base

    tags = audio.tags or {}

    # Build a sanitized raw dump for debugging (skip binary cover art)
    raw: dict = {}
    for k, v in tags.items():
        if k == "covr":
            raw[k] = f"[{len(v)} cover image(s)]"
        else:
            try:
                raw[k] = [str(i) for i in v] if isinstance(v, list) else str(v)
            except Exception:
                raw[k] = "<unserializable>"

    # Series index from ©mvi/©mvc may be an integer atom, not a text atom
    series_index: str | None = _first_text(tags, _SERIES_IDX_KEYS)
    if series_index is None:
        # ©mvi and ©mvc are stored as integers in some taggers
        for key in ["©mvi", "trkn"]:
            val = tags.get(key)
            if val and isinstance(val, list) and val[0]:
                try:
                    series_index = str(int(val[0]))
                    break
                except (TypeError, ValueError):
                    pass

    return LocalMetadata(
        local_audiobook_id=local_audiobook_id,
        duration_seconds=_get_duration(audio),
        title_from_tags=_first_text(tags, _TITLE_KEYS),
        author_from_tags=_first_text(tags, _AUTHOR_KEYS),
        album_from_tags=_first_text(tags, _ALBUM_KEYS),
        narrator_from_tags=_first_text(tags, _NARRATOR_KEYS),
        series_from_tags=_first_text(tags, _SERIES_KEYS),
        series_index_from_tags=series_index,
        has_embedded_cover=_has_cover(tags),
        raw_tags_json=json.dumps(raw, ensure_ascii=False),
    )
