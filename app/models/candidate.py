"""
app/models/candidate.py
-----------------------
AudibleCandidate — a single result from the metadata provider (AudNexus).

The field names here are provider-agnostic. The AudNexus provider maps its
response fields into this model before returning them to the rest of the app.
"""

from __future__ import annotations

from pydantic import BaseModel
import re


class AudibleCandidate(BaseModel):
    """
    Normalized book metadata from any provider.
    All fields except asin and title are optional.
    """
    id:               int | None = None
    batch_run_id:     int | None = None
    local_audiobook_id: int | None = None

    provider_id:      str = "audnexus"
    asin:             str

    title:            str
    subtitle:         str | None = None
    authors:          list[str] = []
    narrators:        list[str] = []

    series_name:      str | None = None
    series_position:  str | None = None   # raw e.g. "2", "2.5", "Book 3"

    runtime_seconds:  float | None = None
    image_url:        str | None = None
    language:         str | None = None
    release_date:     str | None = None   # ISO date string YYYY-MM-DD or year

    raw_payload_json: str | None = None   # original provider response

    # --- Convenience helpers ---

    @property
    def first_author(self) -> str:
        return self.authors[0] if self.authors else ""

    @property
    def first_narrator(self) -> str:
        return self.narrators[0] if self.narrators else ""

    @property
    def release_year(self) -> str | None:
        """Extract 4-digit year from release_date if available."""
        if not self.release_date:
            return None
        match = re.search(r"(\d{4})", self.release_date)
        return match.group(1) if match else None

    def series_position_as_float(self) -> float | None:
        """
        Convert series_position string to float for scoring comparisons.
        e.g. "2" -> 2.0, "2.5" -> 2.5, "Book 3" -> 3.0
        """
        if not self.series_position:
            return None
        raw = self.series_position.strip()
        try:
            return float(raw)
        except ValueError:
            pass
        match = re.search(r"(\d+(?:\.\d+)?)", raw)
        if match:
            return float(match.group(1))
        return None
