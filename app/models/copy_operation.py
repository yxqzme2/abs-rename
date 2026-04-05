"""
app/models/copy_operation.py
-----------------------------
CopyOperation — one file copy attempt with status and error info.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class CopyStatus(str, Enum):
    PENDING           = "pending"
    SUCCESS           = "success"
    SKIPPED_CONFLICT  = "skipped_conflict"
    ERROR             = "error"
    DRY_RUN           = "dry_run"


class CopyOperation(BaseModel):
    id:               int | None = None
    batch_run_id:     int
    source_path:      str
    destination_path: str
    status:           CopyStatus = CopyStatus.PENDING
    error_message:    str | None = None
    timestamp:        str = Field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )
