"""
app/db/queries/batch_runs.py
-----------------------------
DB helpers for BatchRun records.
"""

from __future__ import annotations

import json
from datetime import datetime

from app.db.connection import get_db
from app.models.batch_run import BatchRun


async def create_batch_run(run: BatchRun) -> BatchRun:
    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO batch_runs
                (started_at, source_folders, output_folder,
                 template_used, is_dry_run)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                run.started_at,
                json.dumps(run.source_folders),
                run.output_folder,
                run.template_used,
                int(run.is_dry_run),
            ),
        )
        run.id = cursor.lastrowid
    return run


async def update_batch_run_counts(run: BatchRun) -> None:
    async with get_db() as db:
        await db.execute(
            """
            UPDATE batch_runs SET
                completed_at            = ?,
                total_scanned           = ?,
                total_matched           = ?,
                total_review_required   = ?,
                total_unmatched         = ?,
                total_planned           = ?,
                total_copied            = ?,
                total_skipped_conflicts = ?,
                total_errors            = ?
            WHERE id = ?
            """,
            (
                run.completed_at or datetime.utcnow().isoformat(),
                run.total_scanned,
                run.total_matched,
                run.total_review_required,
                run.total_unmatched,
                run.total_planned,
                run.total_copied,
                run.total_skipped_conflicts,
                run.total_errors,
                run.id,
            ),
        )


async def list_batch_runs(limit: int = 50) -> list[dict]:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM batch_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_batch_run(run_id: int) -> dict | None:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM batch_runs WHERE id = ?", (run_id,)
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def get_batch_run_detail(run_id: int) -> dict | None:
    """Return batch run with its copy operations."""
    run = await get_batch_run(run_id)
    if not run:
        return None

    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM copy_operations WHERE batch_run_id = ? ORDER BY timestamp",
            (run_id,),
        )
        ops = await cursor.fetchall()

    run["operations"] = [dict(op) for op in ops]
    return run
