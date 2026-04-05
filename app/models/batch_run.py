"""
app/models/batch_run.py
-----------------------
BatchRun — one complete scan+copy session.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class BatchRun(BaseModel):
    id:           int | None = None

    started_at:   str = Field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )
    completed_at: str | None = None

    # JSON-serialized list of source folder paths
    source_folders: list[str] = []
    output_folder:  str = ""
    template_used:  str | None = None
    is_dry_run:     bool = True

    # Summary counters (updated as the run progresses)
    total_scanned:            int = 0
    total_matched:            int = 0
    total_review_required:    int = 0
    total_unmatched:          int = 0
    total_planned:            int = 0
    total_copied:             int = 0
    total_skipped_conflicts:  int = 0
    total_errors:             int = 0
