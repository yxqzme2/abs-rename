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
            operation_type = OperationType.MOVE
            # For MP3: move to hand-off folder with renamed parent folder
            actual_dest = _calculate_mp3_destination(dest_path, mp3_handoff_folder)
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

        # --- Actual copy or move ---
        try:
            if operation_type == OperationType.MOVE:
                # MP3: move source file's parent folder to hand-off location with renamed folder
                await _move_mp3_to_handoff(actual_source, actual_dest)
                op.status = CopyStatus.SUCCESS
                event["status"] = "success"
                summary["mp3_moved"] += 1
                logger.info("Moved MP3: %s -> %s", actual_source, actual_dest)
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
    Retrieve audiobook source path and audio format by ID.
    Returns dict with 'source_path' and 'audio_format', or None if not found.
    """
    if local_audiobook_id is None:
        return None
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT source_path, audio_format FROM local_audiobooks WHERE id = ?",
            (local_audiobook_id,),
        )
        row = await cursor.fetchone()
    if row:
        return {
            "source_path": row["source_path"],
            "audio_format": row["audio_format"] or "m4b",
        }
    return None


def _calculate_mp3_destination(dest_path: Path, mp3_handoff_folder: str) -> Path:
    """
    Calculate the destination path for MP3 hand-off.
    Moves the entire folder structure to mp3_handoff_folder.
    Example: /output/Author/Series/01 - Title/01 - Title.mp3
             -> /mp3-handoff/Author/Series/01 - Title/01 - Title.mp3
    """
    # Reconstruct the path relative to root, place under mp3_handoff_folder
    # dest_path is like: /output/Author/Series/01 - Title/01 - Title.mp3
    # Get the relative parts: Author/Series/01 - Title/01 - Title.mp3
    # Put them under mp3_handoff_folder
    parts = dest_path.parts[1:]  # Skip the root drive/volume
    mp3_dest = Path(mp3_handoff_folder) / Path(*parts)
    return mp3_dest


async def _move_mp3_to_handoff(source_path: str, dest_path: Path) -> None:
    """
    Move MP3 file and its parent folder to the hand-off destination.
    The parent folder is renamed according to the destination path structure.
    """
    source = Path(source_path)
    ensure_dir(dest_path.parent)

    # Run the blocking move in a thread pool
    await asyncio.get_event_loop().run_in_executor(
        None,
        shutil.move,
        str(source),
        str(dest_path),
    )


async def _delete_source(source_path: str, audio_format: str) -> None:
    """
    Delete the source file after successful copy/move.
    For MP3 moves, the parent folder may already be moved, so skip it.
    """
    source = Path(source_path)
    if not source.exists():
        logger.debug("Source already deleted or moved: %s", source_path)
        return

    try:
        # Delete the file
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
