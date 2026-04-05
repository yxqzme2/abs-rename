"""
app/api/routes/match.py
------------------------
Routes for viewing and updating match results:
  GET  /api/results/{batch_run_id}           — fetch all results for a run
  POST /api/results/{batch_run_id}/approve   — approve/reject a plan
  POST /api/results/{batch_run_id}/search    — re-search for one audiobook
  POST /api/results/{batch_run_id}/select    — user selects a specific candidate
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.connection import get_db
from app.db.queries.results import (
    get_results_for_batch,
    update_plan_approval,
)
from app.providers.audnexus import AudNexusProvider
from app.services.matcher import match_audiobook
from app.services.preview_planner import build_rename_plan
from app.models.local_audiobook import LocalAudiobook, LocalMetadata, ScanStatus
from app.models.candidate import AudibleCandidate

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/results/{batch_run_id}")
async def get_results(batch_run_id: int):
    """Return all scan results for a batch run."""
    results = await get_results_for_batch(batch_run_id)
    if not results:
        raise HTTPException(status_code=404, detail="Batch run not found or has no results.")
    return {"batch_run_id": batch_run_id, "items": results}


class ApprovalRequest(BaseModel):
    plan_id:      int
    approved:     bool
    custom_path:  str | None = None  # user may edit the destination path inline


@router.post("/results/{batch_run_id}/approve")
async def set_approval(batch_run_id: int, request: ApprovalRequest):
    """Approve or reject a single rename plan."""
    await update_plan_approval(
        plan_id=request.plan_id,
        approved=request.approved,
        custom_path=request.custom_path,
    )
    return {"ok": True, "plan_id": request.plan_id, "approved": request.approved}


class SearchAgainRequest(BaseModel):
    audiobook_id: int
    query:        str        # user-supplied custom search string
    author:       str | None = None


@router.post("/results/{batch_run_id}/search")
async def search_again(batch_run_id: int, request: SearchAgainRequest):
    """
    Re-run a metadata search for one audiobook with a custom query.
    Returns updated candidates and the new best match + rename plan.
    """
    # Load the audiobook and its metadata from DB
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM local_audiobooks WHERE id = ? AND batch_run_id = ?",
            (request.audiobook_id, batch_run_id),
        )
        ab_row = await cursor.fetchone()

        cursor = await db.execute(
            "SELECT * FROM local_metadata WHERE local_audiobook_id = ?",
            (request.audiobook_id,),
        )
        meta_row = await cursor.fetchone()

        cursor = await db.execute(
            "SELECT output_folder, template_used, is_dry_run FROM batch_runs WHERE id = ?",
            (batch_run_id,),
        )
        run_row = await cursor.fetchone()

    if not ab_row or not run_row:
        raise HTTPException(status_code=404, detail="Audiobook or batch run not found.")

    audiobook = LocalAudiobook(
        id=ab_row["id"],
        batch_run_id=batch_run_id,
        source_path=ab_row["source_path"],
        filename=ab_row["filename"],
        folder_path=ab_row["folder_path"],
        file_size=ab_row["file_size"],
        scan_status=ScanStatus(ab_row["scan_status"]),
    )

    metadata: LocalMetadata | None = None
    if meta_row:
        metadata = LocalMetadata(
            id=meta_row["id"],
            local_audiobook_id=request.audiobook_id,
            duration_seconds=meta_row["duration_seconds"],
            title_from_tags=meta_row["title_from_tags"],
            author_from_tags=meta_row["author_from_tags"],
            album_from_tags=meta_row["album_from_tags"],
            narrator_from_tags=meta_row["narrator_from_tags"],
            series_from_tags=meta_row["series_from_tags"],
            series_index_from_tags=meta_row["series_index_from_tags"],
        )

    provider = AudNexusProvider()
    try:
        candidates = await provider.search_books(request.query, request.author)
    finally:
        await provider.close()

    # Replace candidates for this audiobook — delete old ones first so we
    # don't accumulate duplicates across multiple searches.
    async with get_db() as db:
        await db.execute(
            "DELETE FROM audible_candidates WHERE local_audiobook_id = ? AND batch_run_id = ?",
            (request.audiobook_id, batch_run_id),
        )
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
                    batch_run_id,
                    request.audiobook_id,
                    cand.provider_id, cand.asin, cand.title, cand.subtitle,
                    json.dumps(cand.authors), json.dumps(cand.narrators),
                    cand.series_name, cand.series_position, cand.runtime_seconds,
                    cand.image_url, cand.language, cand.release_date,
                    cand.raw_payload_json,
                ),
            )

    # Score new candidates
    match_result, best = await match_audiobook(
        audiobook, metadata, candidates, batch_run_id
    )

    # Return new candidates list so UI can display them
    cand_list = [
        {
            "asin":            c.asin,
            "title":           c.title,
            "authors":         c.authors,
            "narrators":       c.narrators,
            "series_name":     c.series_name,
            "series_position": c.series_position,
            "runtime_seconds": c.runtime_seconds,
            "image_url":       c.image_url,
        }
        for c in candidates
    ]

    return {
        "audiobook_id":  request.audiobook_id,
        "candidates":    cand_list,
        "best_asin":     best.asin if best else None,
        "confidence":    match_result.confidence_score,
        "match_status":  match_result.match_status.value,
    }


class SelectCandidateRequest(BaseModel):
    audiobook_id: int
    asin:         str   # user chose this specific candidate


@router.post("/results/{batch_run_id}/select")
async def select_candidate(batch_run_id: int, request: SelectCandidateRequest):
    """
    User manually selects a specific candidate ASIN for one audiobook.
    Fetches full details, re-scores, updates match result and rename plan.
    """
    # Load context from DB
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM local_audiobooks WHERE id = ? AND batch_run_id = ?",
            (request.audiobook_id, batch_run_id),
        )
        ab_row = await cursor.fetchone()

        cursor = await db.execute(
            "SELECT * FROM local_metadata WHERE local_audiobook_id = ?",
            (request.audiobook_id,),
        )
        meta_row = await cursor.fetchone()

        cursor = await db.execute(
            "SELECT output_folder, template_used, is_dry_run FROM batch_runs WHERE id = ?",
            (batch_run_id,),
        )
        run_row = await cursor.fetchone()

        # Check if this candidate is already in the DB
        cursor = await db.execute(
            "SELECT * FROM audible_candidates WHERE asin = ? AND batch_run_id = ?",
            (request.asin, batch_run_id),
        )
        existing_cand = await cursor.fetchone()

    if not ab_row or not run_row:
        raise HTTPException(status_code=404, detail="Not found.")

    # Fetch the candidate details (from DB if available, else from provider)
    candidate: AudibleCandidate | None = None
    if existing_cand:
        authors   = json.loads(existing_cand["authors"]   or "[]")
        narrators = json.loads(existing_cand["narrators"] or "[]")
        candidate = AudibleCandidate(
            asin=existing_cand["asin"],
            title=existing_cand["title"] or "",
            subtitle=existing_cand["subtitle"],
            authors=authors,
            narrators=narrators,
            series_name=existing_cand["series_name"],
            series_position=existing_cand["series_position"],
            runtime_seconds=existing_cand["runtime_seconds"],
            image_url=existing_cand["image_url"],
            language=existing_cand["language"],
            release_date=existing_cand["release_date"],
        )
    else:
        provider = AudNexusProvider()
        try:
            candidate = await provider.get_book_by_asin(request.asin)
        finally:
            await provider.close()

    if not candidate:
        raise HTTPException(status_code=404, detail=f"Candidate ASIN {request.asin} not found.")

    audiobook = LocalAudiobook(
        id=ab_row["id"], batch_run_id=batch_run_id,
        source_path=ab_row["source_path"], filename=ab_row["filename"],
        folder_path=ab_row["folder_path"], file_size=ab_row["file_size"],
        scan_status=ScanStatus(ab_row["scan_status"]),
    )
    metadata = LocalMetadata(
        local_audiobook_id=request.audiobook_id,
        duration_seconds=meta_row["duration_seconds"] if meta_row else None,
        title_from_tags=meta_row["title_from_tags"] if meta_row else None,
        author_from_tags=meta_row["author_from_tags"] if meta_row else None,
        series_from_tags=meta_row["series_from_tags"] if meta_row else None,
        series_index_from_tags=meta_row["series_index_from_tags"] if meta_row else None,
    ) if meta_row else None

    # User explicitly chose this — confidence is 100 by definition.
    # Still score the dimensions for informational purposes but override final.
    from app.services.matcher import score_candidate
    from app.models.match_result import MatchResult, MatchStatus
    breakdown = score_candidate(candidate, metadata, audiobook)

    async with get_db() as db:
        await db.execute(
            """
            UPDATE match_results SET
                selected_candidate_asin = ?,
                confidence_score        = 100.0,
                match_status            = ?,
                title_score             = ?,
                author_score            = ?,
                narrator_score          = ?,
                series_score            = ?,
                runtime_score           = ?
            WHERE local_audiobook_id = ? AND batch_run_id = ?
            """,
            (
                candidate.asin,
                MatchStatus.USER_SELECTED.value,
                breakdown.title_score,
                breakdown.author_score,
                breakdown.narrator_score,
                breakdown.series_score,
                breakdown.runtime_score,
                request.audiobook_id,
                batch_run_id,
            ),
        )

    # Rebuild rename plan
    plan = await build_rename_plan(
        audiobook=audiobook,
        metadata=metadata,
        candidate=candidate,
        match_result=MatchResult(
            local_audiobook_id=request.audiobook_id,
            batch_run_id=batch_run_id,
            selected_candidate_asin=candidate.asin,
            confidence_score=breakdown.confidence,
            match_status=MatchStatus.USER_SELECTED,
        ),
        output_folder=run_row["output_folder"],
        template=run_row["template_used"] or "{author}/{title}",
        batch_run_id=batch_run_id,
        is_dry_run=bool(run_row["is_dry_run"]),
    )

    # Auto-approve since user explicitly selected this
    if plan.id:
        async with get_db() as db:
            await db.execute(
                "UPDATE rename_plans SET user_approved = 1 WHERE id = ?",
                (plan.id,),
            )

    return {
        "audiobook_id":          request.audiobook_id,
        "selected_asin":         candidate.asin,
        "confidence":            100.0,
        "full_destination_path": plan.full_destination_path,
        "plan_id":               plan.id,
    }
