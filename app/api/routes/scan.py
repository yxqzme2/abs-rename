"""
app/api/routes/scan.py
-----------------------
POST /api/scan          — create a BatchRun and return its ID immediately.
GET  /api/scan/{id}/stream — SSE stream that runs the full scan pipeline
                             and yields progress events to the UI.

Workflow:
  POST /api/scan
    1. Resolve naming template
    2. Create BatchRun row (stores all params)
    3. Return {batch_run_id} immediately

  GET /api/scan/{id}/stream
    1. Load BatchRun params from DB
    2. Scan folders for .m4b files
    3. For each file: yield "scanning" event → match → yield "matched"/"unmatched"
    4. Yield final "done" event with summary counts
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.db.connection import get_db
from app.db.queries.batch_runs import (
    create_batch_run,
    get_batch_run,
    update_batch_run_counts,
)
from app.db.queries.templates import get_default_template
from app.models.batch_run import BatchRun
from app.providers.audnexus import AudNexusProvider
from app.services.matcher import match_audiobook
from app.services.preview_planner import build_rename_plan
from app.services.scanner import scan_folders, derive_search_query

logger = logging.getLogger(__name__)
router = APIRouter()


class ScanRequest(BaseModel):
    source_folders: list[str]
    output_folder:  str
    template_id:    int | None = None
    is_dry_run:     bool = True


# ---------------------------------------------------------------------------
# Step 1 — create the BatchRun and return its ID immediately
# ---------------------------------------------------------------------------

@router.post("/scan")
async def start_scan(request: ScanRequest):
    """
    Create a BatchRun row with the scan parameters and return the ID.
    The actual scanning runs via GET /api/scan/{id}/stream.
    """
    if not request.source_folders:
        raise HTTPException(status_code=400, detail="No source folders provided.")
    if not request.output_folder:
        raise HTTPException(status_code=400, detail="No output folder provided.")

    # Resolve template
    template_row = await get_default_template()
    template_string = (template_row["template_string"]
                       if template_row else "{author}/{title}")

    if request.template_id:
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT template_string FROM user_template_preferences WHERE id = ?",
                (request.template_id,),
            )
            row = await cursor.fetchone()
            if row:
                template_string = row["template_string"]

    run = BatchRun(
        source_folders=request.source_folders,
        output_folder=request.output_folder,
        template_used=template_string,
        is_dry_run=request.is_dry_run,
        started_at=datetime.utcnow().isoformat(),
    )
    run = await create_batch_run(run)
    logger.info("BatchRun %d created. dry_run=%s", run.id, run.is_dry_run)

    return {"batch_run_id": run.id}


# ---------------------------------------------------------------------------
# Step 2 — SSE stream: run the scan pipeline and emit progress events
# ---------------------------------------------------------------------------

def _sse(data: dict) -> str:
    """Format a dict as an SSE message."""
    return f"data: {json.dumps(data)}\n\n"


@router.get("/scan/{batch_run_id}/stream")
async def stream_scan(batch_run_id: int):
    """
    SSE endpoint. Runs the full scan → match → plan pipeline and streams
    one event per file plus a final 'done' event.
    """
    run_row = await get_batch_run(batch_run_id)
    if not run_row:
        raise HTTPException(status_code=404, detail="Batch run not found.")

    async def event_generator():
        run = BatchRun(
            id=run_row["id"],
            source_folders=json.loads(run_row["source_folders"]),
            output_folder=run_row["output_folder"],
            template_used=run_row["template_used"] or "{author}/{title}",
            is_dry_run=bool(run_row["is_dry_run"]),
            started_at=run_row["started_at"],
        )
        template_string = run.template_used

        # --- Scan phase: find all .m4b files ---
        scanned_pairs = await scan_folders(run.id, run.source_folders)
        run.total_scanned = len(scanned_pairs)
        total = run.total_scanned

        yield _sse({"type": "total", "total": total})

        provider = AudNexusProvider()
        try:
            for index, (audiobook, metadata) in enumerate(scanned_pairs, start=1):
                filename = audiobook.filename

                # Notify UI we're starting this file
                yield _sse({
                    "type": "scanning",
                    "index": index,
                    "total": total,
                    "filename": filename,
                })

                # --- Search ---
                title_q, author_q = derive_search_query(
                    metadata,
                    audiobook.filename,
                    audiobook.folder_path.split("/")[-1] or
                    audiobook.folder_path.split("\\")[-1],
                )
                candidates = await provider.search_books(title_q, author_q)

                # Persist candidates
                async with get_db() as db:
                    for cand in candidates:
                        await db.execute(
                            """
                            INSERT INTO audible_candidates
                                (batch_run_id, local_audiobook_id, provider_id, asin,
                                 title, subtitle, authors, narrators, series_name,
                                 series_position, runtime_seconds, image_url, language,
                                 release_date, raw_payload_json)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                run.id, audiobook.id,
                                cand.provider_id, cand.asin,
                                cand.title, cand.subtitle,
                                json.dumps(cand.authors),
                                json.dumps(cand.narrators),
                                cand.series_name, cand.series_position,
                                cand.runtime_seconds, cand.image_url,
                                cand.language, cand.release_date,
                                cand.raw_payload_json,
                            ),
                        )

                # --- Score ---
                match_result, best_candidate = await match_audiobook(
                    audiobook, metadata, candidates, run.id
                )

                # Persist match result
                async with get_db() as db:
                    cursor = await db.execute(
                        """
                        INSERT INTO match_results
                            (local_audiobook_id, batch_run_id,
                             selected_candidate_asin, confidence_score, match_status,
                             title_score, author_score, narrator_score,
                             series_score, runtime_score)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            match_result.local_audiobook_id,
                            match_result.batch_run_id,
                            match_result.selected_candidate_asin,
                            match_result.confidence_score,
                            match_result.match_status.value,
                            match_result.title_score,
                            match_result.author_score,
                            match_result.narrator_score,
                            match_result.series_score,
                            match_result.runtime_score,
                        ),
                    )
                    match_result.id = cursor.lastrowid

                # Update audiobook scan_status
                new_status = match_result.match_status.value
                if new_status == "auto":
                    new_status = "matched"
                elif new_status != "review_required":
                    new_status = "unmatched"

                async with get_db() as db:
                    await db.execute(
                        "UPDATE local_audiobooks SET scan_status = ? WHERE id = ?",
                        (new_status, audiobook.id),
                    )

                # Tally counts
                from app.models.match_result import MatchStatus
                if match_result.match_status == MatchStatus.AUTO:
                    run.total_matched += 1
                    status_label = "matched"
                elif match_result.match_status == MatchStatus.REVIEW_REQUIRED:
                    run.total_review_required += 1
                    status_label = "review"
                else:
                    run.total_unmatched += 1
                    status_label = "unmatched"

                # Build rename plan and auto-approve if matched
                auto_approve = best_candidate is not None
                plan = await build_rename_plan(
                    audiobook=audiobook,
                    metadata=metadata,
                    candidate=best_candidate,
                    match_result=match_result,
                    output_folder=run.output_folder,
                    template=template_string,
                    batch_run_id=run.id,
                    is_dry_run=run.is_dry_run,
                )

                if auto_approve and plan.full_destination_path:
                    async with get_db() as db:
                        await db.execute(
                            "UPDATE rename_plans SET user_approved = 1 WHERE id = ?",
                            (plan.id,),
                        )
                    run.total_planned += 1

                # Emit result for this file
                matched_title = best_candidate.title if best_candidate else None
                confidence = round(match_result.confidence_score, 1)
                yield _sse({
                    "type": "result",
                    "index": index,
                    "total": total,
                    "filename": filename,
                    "status": status_label,
                    "matched_title": matched_title,
                    "confidence": confidence,
                })

        finally:
            await provider.close()

        # Finalize BatchRun
        run.completed_at = datetime.utcnow().isoformat()
        await update_batch_run_counts(run)

        logger.info(
            "BatchRun %d complete. scanned=%d matched=%d review=%d unmatched=%d",
            run.id, run.total_scanned, run.total_matched,
            run.total_review_required, run.total_unmatched,
        )

        yield _sse({
            "type": "done",
            "batch_run_id":          run.id,
            "total_scanned":         run.total_scanned,
            "total_matched":         run.total_matched,
            "total_review_required": run.total_review_required,
            "total_unmatched":       run.total_unmatched,
        })

    return StreamingResponse(event_generator(), media_type="text/event-stream")
