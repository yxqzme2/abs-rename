"""
app/services/copy_executor.py
------------------------------
Executes approved copy operations and streams per-file progress events
via an async generator (consumed by the SSE route).

Key behaviours:
- Only copies files where RenamePlan.user_approved is True
- Skips conflicts (destination already exists) — logs them
- Dry-run mode: performs all validation but skips the actual filesystem copy
- Uses shutil.copy2 (preserves file metadata timestamps)
- Streams a progress event dict after each file is processed
- All operations are logged to the copy_operations DB table
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

from app.db.connection import get_db
from app.models.copy_operation import CopyOperation, CopyStatus
from app.models.rename_plan import RenamePlan
from app.utils.file_utils import ensure_dir

logger = logging.getLogger(__name__)


async def execute_copies(
    plans: list[RenamePlan],
    batch_run_id: int,
    is_dry_run: bool = True,
    overwrite: bool = False,
) -> AsyncGenerator[dict, None]:
    """
    Async generator that processes each approved RenamePlan, performs the
    copy (or simulates it in dry-run mode), persists the result to DB, and
    yields a progress event dict after each item.

    Usage (in a FastAPI SSE route):
        async for event in execute_copies(plans, batch_run_id, is_dry_run):
            yield f"data: {json.dumps(event)}\n\n"

    Progress event dict structure:
        {
            "index":      int,      # 1-based current item number
            "total":      int,      # total items being processed
            "source":     str,      # source file path
            "destination": str,     # destination file path
            "status":     str,      # "success" | "skipped_conflict" | "error" | "dry_run"
            "error":      str|None, # error message if status == "error"
            "done":       bool,     # True only on the final event
            "summary":    dict|None # populated on the final event only
        }
    """
    approved = [p for p in plans if p.user_approved and p.full_destination_path]
    total = len(approved)

    summary = {
        "total":             total,
        "copied":            0,
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
        source      = plan.full_destination_path  # will be overridden below
        source_path = Path(plan.full_destination_path).parent  # placeholder
        dest_path   = Path(plan.full_destination_path)

        # The actual source is the original audiobook file, not the plan dest.
        # We need to retrieve it — the plan only stores the destination.
        # The caller should have already set source_path on the plan, but as
        # a safety measure we look it up from the DB.
        actual_source = await _get_source_path(plan.local_audiobook_id)

        op = CopyOperation(
            batch_run_id=batch_run_id,
            source_path=actual_source or "",
            destination_path=str(dest_path),
            status=CopyStatus.PENDING,
            timestamp=datetime.utcnow().isoformat(),
        )

        event: dict = {
            "index":       index,
            "total":       total,
            "source":      actual_source or "",
            "destination": str(dest_path),
            "status":      "",
            "error":       None,
            "done":        index == total,
            "summary":     None,
        }

        # --- Validation ---
        if not actual_source:
            op.status = CopyStatus.ERROR
            op.error_message = "Source path not found in database."
            event["status"] = "error"
            event["error"]  = op.error_message
            summary["errors"] += 1
            await _persist_op(op)
            if index == total:
                event["summary"] = summary
            yield event
            continue

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
        if dest_path.exists() and not overwrite:
            op.status = CopyStatus.SKIPPED_CONFLICT
            event["status"] = "skipped_conflict"
            summary["skipped_conflicts"] += 1
            logger.info("Skipped conflict: %s", dest_path)
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
            logger.info("[DRY RUN] Would copy: %s -> %s", actual_source, dest_path)
            await _persist_op(op)
            if index == total:
                event["summary"] = summary
            yield event
            continue

        # --- Actual copy ---
        try:
            ensure_dir(dest_path.parent)
            # Run the blocking copy in a thread pool to avoid blocking the event loop
            await asyncio.get_event_loop().run_in_executor(
                None,
                shutil.copy2,
                actual_source,
                str(dest_path),
            )
            op.status = CopyStatus.SUCCESS
            event["status"] = "success"
            summary["copied"] += 1
            logger.info("Copied: %s -> %s", actual_source, dest_path)

        except OSError as exc:
            op.status = CopyStatus.ERROR
            op.error_message = str(exc)
            event["status"] = "error"
            event["error"]  = str(exc)
            summary["errors"] += 1
            logger.error("Copy failed: %s -> %s: %s", actual_source, dest_path, exc)

        await _persist_op(op)
        if index == total:
            event["summary"] = summary
        yield event


async def _get_source_path(local_audiobook_id: int | None) -> str | None:
    """Retrieve the original source file path for a LocalAudiobook by ID."""
    if local_audiobook_id is None:
        return None
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT source_path FROM local_audiobooks WHERE id = ?",
            (local_audiobook_id,),
        )
        row = await cursor.fetchone()
    return row["source_path"] if row else None


async def _persist_op(op: CopyOperation) -> None:
    """Write or update a CopyOperation record in the DB."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO copy_operations
                (batch_run_id, source_path, destination_path,
                 status, error_message, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                op.batch_run_id,
                op.source_path,
                op.destination_path,
                op.status.value,
                op.error_message,
                op.timestamp,
            ),
        )
        op.id = cursor.lastrowid
