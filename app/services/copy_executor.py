"""
app/services/copy_executor.py
------------------------------
Executes approved copy/move operations and streams per-file progress events
via an async generator (consumed by the SSE route).

Key behaviours:
- Only copies/moves files where RenamePlan.user_approved is True
- M4B/M4A: copies files to output folder
- MP3: moves files to mp3_handoff_folder (folder + file renamed)
- Skips conflicts (destination already exists) — logs them
- Dry-run mode: performs all validation but skips actual filesystem changes
- Uses shutil.copy2 (copies, preserves metadata) or shutil.move (MP3 hand-off)
- Optional deletion after successful copy/move
- Streams a progress event dict after each file is processed
- All operations logged to copy_operations DB table
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

from app.config import MP3_HANDOFF_FOLDER
from app.db.connection import get_db
from app.models.copy_operation import CopyOperation, CopyStatus, OperationType
from app.models.rename_plan import RenamePlan
from app.utils.file_utils import ensure_dir

logger = logging.getLogger(__name__)


async def execute_copies(
    plans: list[RenamePlan],
    batch_run_id: int,
    is_dry_run: bool = True,
    overwrite: bool = False,
    delete_after: bool = False,
    mp3_handoff_folder: str | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Async generator that processes each approved RenamePlan, performs the
    copy/move (or simulates it in dry-run mode), persists the result to DB, and
    yields a progress event dict after each item.

    Args:
        plans: List of RenamePlan objects to execute
        batch_run_id: FK to active BatchRun
        is_dry_run: If True, validate but don't write to filesystem
        overwrite: If True, overwrite existing destination files
        delete_after: If True, delete source file after successful copy/move
        mp3_handoff_folder: Destination folder for MP3 hand-off (if None, disabled)

    Usage (in a FastAPI SSE route):
        async for event in execute_copies(plans, batch_run_id, is_dry_run, ...):
            yield f"data: {json.dumps(event)}\n\n"

    Progress event dict structure:
        {
            "index":      int,      # 1-based current item number
            "total":      int,      # total items being processed
            "source":     str,      # source file path
            "destination": str,     # destination file path
            "operation":  str,      # "copy" (M4B) or "move" (MP3)
            "status":     str,      # "success" | "skipped" | "error" | "dry_run"
            "error":      str|None, # error message if status == "error"
            "done":       bool,     # True only on the final event
            "summary":    dict|None # populated on the final event only
        }
    """
    approved = [p for p in plans if p.user_approved and p.full_destination_path]
    total = len(approved)

    summary = {
        "total":             total,
        "m4b_copied":        0,
        "mp3_moved":         0,
        "deleted":           0,
        "skipped_conflicts": 0,
        "errors":            0,
        "dry_run":           0,
    }

    if total == 0:
        yield {
            "index": 0, "total": 0,
            "source": "", "destination": "",
            "status": "done", "error": None,
            "done": True, "summary": summary,
        }
        return

    for index, plan in enumerate(approved, start=1):
        dest_path   = Path(plan.full_destination_path)

        # Retrieve the original source file path and audio format
        audiobook_info = await _get_audiobook_info(plan.local_audiobook_id)
        if not audiobook_info:
            event: dict = {
                "index": index, "total": total,
                "source": "", "destination": str(dest_path),
                "operation": "unknown", "status": "error",
                "error": "Audiobook info not found in database",
                "done": index == total, "summary": None,
            }
            summary["errors"] += 1
            yield event
            continue

        actual_source = audiobook_info["source_path"]
        audio_format = audiobook_info["audio_format"]

        # Determine operation type and actual destination
        if audio_format == "mp3" and mp3_handoff_folder:
            operation_type = OperationType.COPY
            # For MP3: copy to hand-off folder with simple name (series_index)
            actual_dest = _calculate_mp3_destination(
                mp3_handoff_folder,
                audiobook_info.get("series_from_tags"),
                audiobook_info.get("series_index_from_tags"),
            )
        else:
            operation_type = OperationType.COPY
            actual_dest = dest_path

        op = CopyOperation(
            batch_run_id=batch_run_id,
            source_path=actual_source,
            destination_path=str(actual_dest),
            operation_type=operation_type,
            status=CopyStatus.PENDING,
            timestamp=datetime.utcnow().isoformat(),
        )

        event: dict = {
            "index":       index,
            "total":       total,
            "source":      actual_source,
            "destination": str(actual_dest),
            "operation":   "copy" if operation_type == OperationType.COPY else "move",
            "status":      "",
            "error":       None,
            "done":        index == total,
            "summary":     None,
        }

        # --- Validation ---
        if not Path(actual_source).exists():
            op.status = CopyStatus.ERROR
            op.error_message = f"Source file does not exist: {actual_source}"
            event["status"] = "error"
            event["error"]  = op.error_message
            summary["errors"] += 1
            await _persist_op(op)
            if index == total:
                event["summary"] = summary
            yield event
            continue

        # --- Conflict check (re-check at execution time) ---
        if actual_dest.exists() and not overwrite:
            op.status = CopyStatus.SKIPPED_CONFLICT
            event["status"] = "skipped_conflict"
            summary["skipped_conflicts"] += 1
            logger.info("Skipped conflict: %s", actual_dest)
            await _persist_op(op)
            if index == total:
                event["summary"] = summary
            yield event
            continue

        # --- Dry-run ---
        if is_dry_run:
            op.status = CopyStatus.DRY_RUN
            event["status"] = "dry_run"
            summary["dry_run"] += 1
            op_verb = "move" if operation_type == OperationType.MOVE else "copy"
            logger.info("[DRY RUN] Would %s: %s -> %s", op_verb, actual_source, actual_dest)
            await _persist_op(op)
            if index == total:
                event["summary"] = summary
            yield event
            continue

        # --- Actual copy ---
        try:
            if audio_format == "mp3":
                # MP3: copy source folder's contents to hand-off location
                await _copy_mp3_to_handoff(actual_source, actual_dest)
                op.status = CopyStatus.SUCCESS
                event["status"] = "success"
                summary["mp3_moved"] += 1
                logger.info("Copied MP3 folder: %s -> %s", actual_source, actual_dest)
            else:
                # M4B/M4A: copy file to destination
                ensure_dir(actual_dest.parent)
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    shutil.copy2,
                    actual_source,
                    str(actual_dest),
                )
                op.status = CopyStatus.SUCCESS
                event["status"] = "success"
                summary["m4b_copied"] += 1
                logger.info("Copied: %s -> %s", actual_source, actual_dest)

            # --- Optional: delete source after successful copy/move ---
            if delete_after:
                await _delete_source(actual_source, audio_format)
                op.status = CopyStatus.DELETED
                summary["deleted"] += 1
                logger.info("Deleted source: %s", actual_source)

        except OSError as exc:
            op.status = CopyStatus.ERROR
            op.error_message = str(exc)
            event["status"] = "error"
            event["error"]  = str(exc)
            summary["errors"] += 1
            logger.error("Operation failed: %s -> %s: %s", actual_source, actual_dest, exc)

        await _persist_op(op)
        if index == total:
            event["summary"] = summary
        yield event


async def _get_audiobook_info(local_audiobook_id: int | None) -> dict | None:
    """
    Retrieve audiobook source path, audio format, and metadata (series, index) by ID.
    Returns dict with 'source_path', 'audio_format', 'series_from_tags', 'series_index_from_tags'.
    """
    if local_audiobook_id is None:
        return None
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT
                la.source_path, la.audio_format,
                lm.series_from_tags, lm.series_index_from_tags
            FROM local_audiobooks la
            LEFT JOIN local_metadata lm ON lm.local_audiobook_id = la.id
            WHERE la.id = ?
            """,
            (local_audiobook_id,),
        )
        row = await cursor.fetchone()
    if row:
        return {
            "source_path": row["source_path"],
            "audio_format": row["audio_format"] or "m4b",
            "series_from_tags": row["series_from_tags"],
            "series_index_from_tags": row["series_index_from_tags"],
        }
    return None


def _calculate_mp3_destination(
    mp3_handoff_folder: str,
    series_name: str | None,
    series_index: str | None,
) -> Path:
    """
    Calculate the destination folder for MP3 hand-off using a simple flat structure.
    Creates a folder named {series_name}_{series_index} under mp3_handoff_folder.
    Example: Series: "Universe Series", Index: "08" -> /mp3-convert/Universe Series_08/
    Falls back to "{series_name}" or "Unknown_Book" if index is missing.
    """
    folder_name = "Unknown_Book"

    if series_name and series_index:
        folder_name = f"{series_name}_{series_index}"
    elif series_name:
        folder_name = series_name

    return Path(mp3_handoff_folder) / folder_name


async def _copy_mp3_to_handoff(source_path: str, dest_path: Path) -> None:
    """
    Copy all files from source MP3 folder to the hand-off destination folder.
    source_path is a folder like /downloads/System Clash/
    dest_path is the target folder like /mp3-convert/System Universe Series_08/
    All files from source are copied to dest (not individual file copy).
    """
    source = Path(source_path)
    ensure_dir(dest_path)

    def _copy_folder_contents() -> None:
        if not source.is_dir():
            raise OSError(f"Source is not a directory: {source}")
        for item in source.iterdir():
            if item.is_file():
                dest_file = dest_path / item.name
                shutil.copy2(str(item), str(dest_file))

    await asyncio.get_event_loop().run_in_executor(None, _copy_folder_contents)


async def _delete_source(source_path: str, audio_format: str) -> None:
    """
    Delete the source file/folder after successful copy.
    - For M4B/M4A: deletes the file (and tries to remove empty parent folders)
    - For MP3: deletes the entire source folder and all its contents
    """
    source = Path(source_path)
    if not source.exists():
        logger.debug("Source already deleted: %s", source_path)
        return

    try:
        if audio_format == "mp3":
            # MP3: delete entire folder and contents
            def _delete_folder() -> None:
                import shutil
                shutil.rmtree(str(source), ignore_errors=False)

            await asyncio.get_event_loop().run_in_executor(None, _delete_folder)
            logger.debug("Deleted MP3 folder: %s", source_path)
        else:
            # M4B/M4A: delete single file
            source.unlink()
            logger.debug("Deleted: %s", source_path)

            # Try to remove parent folder if empty
            parent = source.parent
            try:
                parent.rmdir()
                logger.debug("Removed empty folder: %s", parent)
            except OSError:
                # Folder not empty or other error, that's okay
                pass
    except OSError as exc:
        logger.warning("Could not delete source %s: %s", source_path, exc)


async def _persist_op(op: CopyOperation) -> None:
    """Write or update a CopyOperation record in the DB."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO copy_operations
                (batch_run_id, source_path, destination_path,
                 operation_type, status, error_message, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                op.batch_run_id,
                op.source_path,
                op.destination_path,
                op.operation_type.value,
                op.status.value,
                op.error_message,
                op.timestamp,
            ),
        )
        op.id = cursor.lastrowid
