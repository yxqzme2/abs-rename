"""
app/models/match_result.py
--------------------------
MatchResult — links a LocalAudiobook to the best AudibleCandidate,
storing the weighted confidence score and per-dimension scores.
"""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel


class MatchStatus(str, Enum):
    AUTO            = "auto"           # high confidence, auto-selected
    REVIEW_REQUIRED = "review_required"  # medium confidence, needs review
    UNMATCHED       = "unmatched"      # no usable candidate found
    USER_SELECTED   = "user_selected"  # user manually chose a candidate


class MatchResult(BaseModel):
    id:                     int | None = None
    local_audiobook_id:     int
    batch_run_id:           int

    selected_candidate_asin: str | None = None

    # Overall weighted confidence (0–100)
    confidence_score:       float = 0.0
    match_status:           MatchStatus = MatchStatus.UNMATCHED

    # Per-dimension scores (0–100 each, before weighting)
    title_score:            float = 0.0
    author_score:           float = 0.0
    narrator_score:         float = 0.0
    series_score:           float = 0.0
    runtime_score:          float = 0.0

    notes:                  str | None = None
