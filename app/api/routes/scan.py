"""
app/api/routes/scan.py
-----------------------
POST /api/scan  — start a new scan+match batch run.

Workflow:
  1. Create a BatchRun record
  2. Scan all source folders for .m4b files
  3. For each file: search AudNexus, score candidates, build rename plan
  4. Persist everything
  5. Return the batch_run_id so the UI can load the results page
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db.connection import get_db
from app.db.queries.batch_runs import create_batch_run, update_batch_run_counts
from app.db.queries.templates import get_default_template
from app.models.batch_run import BatchRun
from app.models.local_audiobook import ScanStatus
from app.providers.audnexus import AudNexusProvider
from app.services.matcher import match_audiobook
from app.services.preview_planner import build_rename_plan
from app.services.scanner import scan_folders, derive_search_query

logger = logging.getLogger(__name__)
router = APIRouter()


class ScanRequest(BaseModel):
    source_folders: list[str]
    output_folder:  str
    template_id:    int | None = None   # None = use default
    is_dry_run:     bool = True


@router.post("/scan")
async def start_scan(request: ScanRequest):
    """
    Kick off a full scan → match → plan pipeline.
    Returns the batch_run_id and summary counts.
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

    # Create BatchRun
    run = BatchRun(
        source_folders=request.source_folders,
        output_folder=request.output_folder,
        template_used=template_string,
        is_dry_run=request.is_dry_run,
        started_at=datetime.utcnow().isoformat(),
    )
    run = await create_batch_run(run)
    logger.info("BatchRun %d started. dry_run=%s", run.id, run.is_dry_run)

    # --- Scan ---
    scanned_pairs = await scan_folders(run.id, request.source_folders)
    run.total_scanned = len(scanned_pairs)

    provider = AudNexusProvider()
    try:
        # --- Match each file ---
        for audiobook, metadata in scanned_pairs:
            title_q, author_q = derive_search_query(
                metadata,
                audiobook.filename,
                audiobook.folder_path.split("/")[-1] or audiobook.folder_path.split("\\")[-1],
            )

            candidates = await provider.search_books(title_q, author_q)

            # Persist candidates
            async with get_db() as db:
                for cand in candidates:
                    authors_json   = json.dumps(cand.authors)
                    narrators_json = json.dumps(cand.narrators)
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
                            run.id,
                            audiobook.id,
                            cand.provider_id,
                            cand.asin,
                            cand.title,
                            cand.subtitle,
                            authors_json,
                            narrators_json,
                            cand.series_name,
                            cand.series_position,
                            cand.runtime_seconds,
                            cand.image_url,
                            cand.language,
                            cand.release_date,
                            cand.raw_payload_json,
                        ),
                    )

            # Score
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
            elif new_status == "review_required":
                new_status = "review_required"
            else:
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
            elif match_result.match_status == MatchStatus.REVIEW_REQUIRED:
                run.total_review_required += 1
            else:
                run.total_unmatched += 1

            # Auto-approve any item that has a candidate match.
            # The user will review and can deselect before executing.
            # Unmatched items (no candidate) are never auto-approved.
            auto_approve = best_candidate is not None

            plan = await build_rename_plan(
                audiobook=audiobook,
                metadata=metadata,
                candidate=best_candidate,
                match_result=match_result,
                output_folder=request.output_folder,
                template=template_string,
                batch_run_id=run.id,
                is_dry_run=request.is_dry_run,
            )

            if auto_approve and plan.full_destination_path:
                async with get_db() as db:
                    await db.execute(
                        "UPDATE rename_plans SET user_approved = 1 WHERE id = ?",
                        (plan.id,),
                    )
                run.total_planned += 1

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

    return {
        "batch_run_id":       run.id,
        "total_scanned":      run.total_scanned,
        "total_matched":      run.total_matched,
        "total_review_required": run.total_review_required,
        "total_unmatched":    run.total_unmatched,
        "total_planned":      run.total_planned,
        "is_dry_run":         run.is_dry_run,
    }
