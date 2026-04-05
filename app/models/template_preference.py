"""
app/models/template_preference.py
----------------------------------
UserTemplatePreference — a saved naming template.
"""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class UserTemplatePreference(BaseModel):
    id:              int | None = None
    name:            str                    # display name shown in UI
    template_string: str                    # e.g. "{author}/{series}/..."
    is_default:      bool = False
    created_at:      str = Field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )
