"""
app/api/routes/copy.py
-----------------------
POST /api/copy/{batch_run_id}  — execute approved copy operations
GET  /api/copy/{batch_run_id}/stream — SSE stream of copy progress
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.config import MP3_HANDOFF_FOLDER
from app.db.queries.results import get_approved_plans
from app.db.queries.batch_runs import get_batch_run
from app.models.rename_plan import RenamePlan
from app.services.copy_executor import execute_copies

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/copy/{batch_run_id}/stream")
async def stream_copy(
    batch_run_id: int,
    overwrite: bool = False,
    delete_after: bool = False,
):
    """
    SSE endpoint that streams copy progress for a batch run.

    Query params:
        overwrite      — if true, existing destination files are overwritten;
                         if false (default), conflicts are skipped and logged.
        delete_after   — if true, delete source files after successful copy/move
    """
    run = await get_batch_run(batch_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Batch run not found.")

    plan_rows = await get_approved_plans(batch_run_id)

    # Convert DB rows to RenamePlan objects
    plans: list[RenamePlan] = []
    for row in plan_rows:
        plans.append(RenamePlan(
            id=row["id"],
            local_audiobook_id=row["local_audiobook_id"],
            batch_run_id=batch_run_id,
            template_used=row["template_used"],
            destination_dir=row["destination_dir"],
            destination_filename=row["destination_filename"],
            full_destination_path=row["full_destination_path"],
            is_conflict=bool(row["is_conflict"]),
            is_dry_run=bool(row["is_dry_run"]),
            user_approved=bool(row["user_approved"]),
        ))

    is_dry_run = bool(run["is_dry_run"])

    async def event_generator():
        async for event in execute_copies(
            plans,
            batch_run_id,
            is_dry_run,
            overwrite=overwrite,
            delete_after=delete_after,
            mp3_handoff_folder=MP3_HANDOFF_FOLDER if MP3_HANDOFF_FOLDER else None,
        ):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering if proxied
        },
    )


@router.get("/copy/{batch_run_id}/summary")
async def get_copy_summary(batch_run_id: int):
    """Return the approved plan summary before execution (pre-flight view)."""
    run = await get_batch_run(batch_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Batch run not found.")

    plan_rows = await get_approved_plans(batch_run_id)

    return {
        "batch_run_id": batch_run_id,
        "is_dry_run":   bool(run["is_dry_run"]),
        "total_approved": len(plan_rows),
        "plans": [
            {
                "plan_id":              row["id"],
                "source_path":          row.get("source_path", ""),
                "full_destination_path": row["full_destination_path"],
                "is_conflict":          bool(row["is_conflict"]),
            }
            for row in plan_rows
        ],
    }
