"""
app/services/scanner.py
-----------------------
Scans one or more source folders for .m4b files, creates LocalAudiobook
records, reads metadata from each file, and persists everything to the DB.

Also exposes the search query derivation logic used by the matcher.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from app.db.connection import get_db
from app.models.local_audiobook import LocalAudiobook, LocalMetadata, ScanStatus
from app.services.metadata_reader import read_metadata
from app.utils.file_utils import scan_m4b_files, file_size_bytes, get_audio_format

logger = logging.getLogger(__name__)

# Noise patterns stripped from filenames when deriving a search query
_FILENAME_NOISE = re.compile(
    r"\s*[-_]?\s*(unabridged|audiobook|audio\s*book|mp3|m4b|aac"
    r"|part\s*\d+|disc\s*\d+|cd\s*\d+)\s*",
    re.IGNORECASE,
)


def derive_search_query(
    metadata: LocalMetadata | None,
    filename: str,
    folder_name: str,
) -> tuple[str, str | None]:
    """
    Determine the best title + author strings to use when searching
    the metadata provider.

    Returns:
        (title_query, author_query)  — author_query may be None

    Priority order (from goal.md):
    1. title tag + author tag (both present)
    2. title tag only
    3. parsed filename (strip extension + noise)
    4. parent folder name
    """
    if metadata:
        title  = (metadata.title_from_tags  or "").strip()
        author = (metadata.author_from_tags or "").strip()

        if title and author:
            logger.debug("Query strategy: tags (title+author) — '%s' by '%s'", title, author)
            return title, author

        if title:
            logger.debug("Query strategy: tags (title only) — '%s'", title)
            return title, None

    # Fall back to filename parsing
    stem = Path(filename).stem                   # strip .m4b
    cleaned = _FILENAME_NOISE.sub(" ", stem)     # remove noise tokens
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if cleaned:
        logger.debug("Query strategy: filename — '%s'", cleaned)
        return cleaned, None

    # Last resort: parent folder name
    logger.debug("Query strategy: folder name — '%s'", folder_name)
    return folder_name, None


async def scan_folders(
    batch_run_id: int,
    source_folders: list[str],
) -> list[tuple[LocalAudiobook, LocalMetadata]]:
    """
    Scan all source folders, read metadata, persist to DB, and return
    a list of (LocalAudiobook, LocalMetadata) pairs.

    Args:
        batch_run_id:    FK to the active BatchRun
        source_folders:  list of absolute folder paths to scan

    Returns:
        List of (audiobook, metadata) tuples for all discovered files
    """
    results: list[tuple[LocalAudiobook, LocalMetadata]] = []

    # Collect all audiobook files across all source folders
    all_files: list[Path] = []
    for folder in source_folders:
        all_files.extend(scan_m4b_files(folder))

    logger.info("Total audiobook files found: %d", len(all_files))

    async with get_db() as db:
        for file_path in all_files:
            # --- Create LocalAudiobook record ---
            audiobook = LocalAudiobook(
                batch_run_id=batch_run_id,
                source_path=str(file_path),
                filename=file_path.name,
                folder_path=str(file_path.parent),
                extension=file_path.suffix.lower(),
                file_size=file_size_bytes(file_path),
                audio_format=get_audio_format(file_path),
                scan_status=ScanStatus.PENDING,
            )

            cursor = await db.execute(
                """
                INSERT INTO local_audiobooks
                    (batch_run_id, source_path, filename, folder_path,
                     extension, file_size, audio_format, scan_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audiobook.batch_run_id,
                    audiobook.source_path,
                    audiobook.filename,
                    audiobook.folder_path,
                    audiobook.extension,
                    audiobook.file_size,
                    audiobook.audio_format,
                    audiobook.scan_status.value,
                ),
            )
            audiobook.id = cursor.lastrowid

            # --- Read metadata from tags ---
            metadata = read_metadata(file_path, audiobook.id)

            await db.execute(
                """
                INSERT INTO local_metadata
                    (local_audiobook_id, duration_seconds,
                     title_from_tags, author_from_tags, album_from_tags,
                     narrator_from_tags, series_from_tags,
                     series_index_from_tags, has_embedded_cover, raw_tags_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    metadata.local_audiobook_id,
                    metadata.duration_seconds,
                    metadata.title_from_tags,
                    metadata.author_from_tags,
                    metadata.album_from_tags,
                    metadata.narrator_from_tags,
                    metadata.series_from_tags,
                    metadata.series_index_from_tags,
                    int(metadata.has_embedded_cover),
                    metadata.raw_tags_json,
                ),
            )

            # --- Update scan status to 'scanned' ---
            await db.execute(
                "UPDATE local_audiobooks SET scan_status = ? WHERE id = ?",
                (ScanStatus.SCANNED.value, audiobook.id),
            )
            audiobook.scan_status = ScanStatus.SCANNED

            results.append((audiobook, metadata))
            logger.debug("Scanned: %s", file_path.name)

    logger.info("Scan complete. %d files processed.", len(results))
    return results
