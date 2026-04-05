"""
app/services/preview_planner.py
---------------------------------
Builds RenamePlan records by rendering the path template for each matched
audiobook. Checks for destination conflicts before the copy runs.
Persists plans to the DB.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.db.connection import get_db
from app.models.candidate import AudibleCandidate
from app.models.local_audiobook import LocalAudiobook, LocalMetadata
from app.models.match_result import MatchResult
from app.models.rename_plan import RenamePlan
from app.path_engine.template_engine import render_template

logger = logging.getLogger(__name__)


async def build_rename_plan(
    audiobook: LocalAudiobook,
    metadata: LocalMetadata | None,
    candidate: AudibleCandidate | None,
    match_result: MatchResult,
    output_folder: str,
    template: str,
    batch_run_id: int,
    is_dry_run: bool = True,
) -> RenamePlan:
    """
    Render the naming template for one audiobook and produce a RenamePlan.

    Args:
        audiobook:     the source file
        metadata:      extracted tag data
        candidate:     the matched provider result (may be None for unmatched)
        match_result:  the scoring result
        output_folder: absolute base path for all output files
        template:      the folder naming template string
        batch_run_id:  current batch run FK
        is_dry_run:    if True, flag the plan as dry-run

    Returns:
        RenamePlan — persisted to DB
    """
    plan = RenamePlan(
        local_audiobook_id=audiobook.id,
        batch_run_id=batch_run_id,
        template_used=template,
        is_dry_run=is_dry_run,
        user_approved=False,
    )

    if candidate is None:
        # No match — can't build a plan
        logger.debug("No candidate for '%s', skipping rename plan.", audiobook.filename)
        await _persist_plan(plan)
        return plan

    # Render the template
    dest_dir, dest_filename, rel_path = render_template(
        template, audiobook, metadata, candidate
    )

    # Prepend the output folder to get the absolute destination paths
    abs_dir  = str(Path(output_folder) / dest_dir)
    abs_file = str(Path(output_folder) / rel_path)

    plan.destination_dir       = abs_dir
    plan.destination_filename  = dest_filename
    plan.full_destination_path = abs_file

    # Conflict check — does the destination file already exist?
    plan.is_conflict = Path(abs_file).exists()
    if plan.is_conflict:
        logger.info(
            "Conflict detected: '%s' already exists at '%s'",
            dest_filename, abs_dir,
        )

    await _persist_plan(plan)
    return plan


async def _persist_plan(plan: RenamePlan) -> None:
    """
    Insert or replace the RenamePlan for this audiobook+batch_run.
    Each audiobook can only have one active plan per batch run — if one
    already exists (e.g. after a re-search or manual selection) it is
    replaced in-place rather than accumulating duplicate rows.
    """
    async with get_db() as db:
        # Check for an existing plan for this audiobook in this batch
        cursor = await db.execute(
            "SELECT id FROM rename_plans WHERE local_audiobook_id = ? AND batch_run_id = ?",
            (plan.local_audiobook_id, plan.batch_run_id),
        )
        existing = await cursor.fetchone()

        if existing:
            # Update the existing row
            await db.execute(
                """
                UPDATE rename_plans SET
                    template_used           = ?,
                    destination_dir         = ?,
                    destination_filename    = ?,
                    full_destination_path   = ?,
                    is_conflict             = ?,
                    is_dry_run              = ?,
                    user_approved           = ?
                WHERE id = ?
                """,
                (
                    plan.template_used,
                    plan.destination_dir,
                    plan.destination_filename,
                    plan.full_destination_path,
                    int(plan.is_conflict),
                    int(plan.is_dry_run),
                    int(plan.user_approved),
                    existing["id"],
                ),
            )
            plan.id = existing["id"]
        else:
            cursor = await db.execute(
                """
                INSERT INTO rename_plans
                    (local_audiobook_id, batch_run_id, template_used,
                     destination_dir, destination_filename, full_destination_path,
                     is_conflict, is_dry_run, user_approved)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.local_audiobook_id,
                    plan.batch_run_id,
                    plan.template_used,
                    plan.destination_dir,
                    plan.destination_filename,
                    plan.full_destination_path,
                    int(plan.is_conflict),
                    int(plan.is_dry_run),
                    int(plan.user_approved),
                ),
            )
            plan.id = cursor.lastrowid
