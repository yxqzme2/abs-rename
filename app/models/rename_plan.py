"""
app/models/rename_plan.py
-------------------------
RenamePlan — the resolved destination path for one audiobook file,
produced by the path template engine.
"""

from __future__ import annotations

from pydantic import BaseModel


class RenamePlan(BaseModel):
    id:                     int | None = None
    local_audiobook_id:     int
    batch_run_id:           int

    template_used:          str | None = None   # the template string that was rendered
    destination_dir:        str | None = None   # directory portion of destination
    destination_filename:   str | None = None   # filename + extension
    full_destination_path:  str | None = None   # dir + filename combined

    is_conflict:            bool = False  # True if dest file already exists
    is_dry_run:             bool = True
    user_approved:          bool = False  # must be True before copy proceeds
