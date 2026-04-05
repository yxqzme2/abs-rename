"""
app/db/queries/results.py
--------------------------
DB helpers for fetching scan results — the combined view of audiobooks,
metadata, candidates, match results, and rename plans for a batch run.
"""

from __future__ import annotations

import json
from app.db.connection import get_db


async def get_results_for_batch(batch_run_id: int) -> list[dict]:
    """
    Fetch the full result set for a batch run — one dict per audiobook,
    with metadata, best candidate, match result, and rename plan joined in.
    """
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT
                la.id                       AS audiobook_id,
                la.source_path,
                la.filename,
                la.folder_path,
                la.file_size,
                la.scan_status,

                lm.duration_seconds,
                lm.title_from_tags,
                lm.author_from_tags,
                lm.narrator_from_tags,
                lm.series_from_tags,
                lm.series_index_from_tags,

                mr.id                       AS match_result_id,
                mr.selected_candidate_asin,
                mr.confidence_score,
                mr.match_status,
                mr.title_score,
                mr.author_score,
                mr.narrator_score,
                mr.series_score,
                mr.runtime_score,
                mr.notes                    AS match_notes,

                rp.id                       AS plan_id,
                rp.template_used,
                rp.destination_dir,
                rp.destination_filename,
                rp.full_destination_path,
                rp.is_conflict,
                rp.is_dry_run,
                rp.user_approved

            FROM local_audiobooks la
            LEFT JOIN local_metadata   lm ON lm.local_audiobook_id = la.id
            LEFT JOIN match_results    mr ON mr.local_audiobook_id  = la.id
                                         AND mr.batch_run_id        = la.batch_run_id
            LEFT JOIN rename_plans     rp ON rp.local_audiobook_id  = la.id
                                         AND rp.batch_run_id        = la.batch_run_id
            WHERE la.batch_run_id = ?
            ORDER BY la.filename
            """,
            (batch_run_id,),
        )
        rows = await cursor.fetchall()

    results = []
    for row in rows:
        item = dict(row)
        # Fetch all candidates for this audiobook so the UI can offer alternates
        item["candidates"] = await _get_candidates(item["audiobook_id"], batch_run_id, db=None)
        results.append(item)

    return results


async def _get_candidates(
    local_audiobook_id: int,
    batch_run_id: int,
    db=None,  # unused, kept for signature compat
) -> list[dict]:
    async with get_db() as db2:
        cursor = await db2.execute(
            """
            SELECT asin, title, subtitle, authors, narrators,
                   series_name, series_position, runtime_seconds,
                   image_url, language, release_date
            FROM audible_candidates
            WHERE local_audiobook_id = ? AND batch_run_id = ?
            ORDER BY id
            """,
            (local_audiobook_id, batch_run_id),
        )
        rows = await cursor.fetchall()

    candidates = []
    for row in rows:
        c = dict(row)
        # Parse JSON arrays
        for field in ("authors", "narrators"):
            if c.get(field):
                try:
                    c[field] = json.loads(c[field])
                except (json.JSONDecodeError, TypeError):
                    c[field] = [c[field]]
            else:
                c[field] = []
        candidates.append(c)
    return candidates


async def update_plan_approval(
    plan_id: int,
    approved: bool,
    custom_path: str | None = None,
) -> None:
    """Update user_approved and optionally a custom destination path."""
    async with get_db() as db:
        if custom_path:
            await db.execute(
                """
                UPDATE rename_plans
                SET user_approved = ?, full_destination_path = ?,
                    destination_filename = ?
                WHERE id = ?
                """,
                (
                    int(approved),
                    custom_path,
                    custom_path.split("/")[-1] if "/" in custom_path else custom_path,
                    plan_id,
                ),
            )
        else:
            await db.execute(
                "UPDATE rename_plans SET user_approved = ? WHERE id = ?",
                (int(approved), plan_id),
            )


async def get_approved_plans(batch_run_id: int) -> list[dict]:
    """Return all approved, non-conflicting plans ready for copy execution."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT rp.*, la.source_path
            FROM rename_plans rp
            JOIN local_audiobooks la ON la.id = rp.local_audiobook_id
            WHERE rp.batch_run_id = ?
              AND rp.user_approved = 1
            ORDER BY rp.id
            """,
            (batch_run_id,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]
