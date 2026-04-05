"""
app/services/matcher.py
------------------------
Scores AudibleCandidates against a LocalAudiobook + LocalMetadata pair
using a weighted multi-dimensional scoring system.

Scoring dimensions and default weights (configurable in config.py):
  title    45%  — fuzzy string similarity
  author   25%  — token overlap
  narrator 10%  — token overlap (skipped if neither side has narrator data)
  series   15%  — fuzzy string similarity (skipped if neither side has series)
  runtime   5%  — proximity within tolerance bands

Each dimension returns 0–100 before weighting.
Final confidence score is 0–100.
"""

from __future__ import annotations

import logging
import re
from typing import NamedTuple

from rapidfuzz import fuzz

from app.config import (
    CONFIDENCE_AUTO_APPROVE,
    CONFIDENCE_REVIEW_REQUIRED,
    RUNTIME_TOLERANCE_FULL,
    RUNTIME_TOLERANCE_PARTIAL,
    SCORE_WEIGHTS,
)
from app.models.candidate import AudibleCandidate
from app.models.local_audiobook import LocalAudiobook, LocalMetadata
from app.models.match_result import MatchResult, MatchStatus

logger = logging.getLogger(__name__)

# Noise tokens stripped from titles before comparison (comparison only)
_NOISE_RE = re.compile(
    r"\b(unabridged|audiobook|audio\s*book|a\s+novel|the\s+complete"
    r"|book\s+\d+|part\s+\d+|volume\s+\d+|vol\s*\.?\s*\d+)\b",
    re.IGNORECASE,
)
_PUNCT_RE    = re.compile(r"[^\w\s]")
_WHITESPACE  = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, remove noise tokens."""
    text = text.lower()
    text = _NOISE_RE.sub(" ", text)
    text = _PUNCT_RE.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    return text


def _token_overlap(a: list[str], b: list[str]) -> float:
    """
    Score overlap between two name lists (0–100).
    Uses fuzzy matching to handle minor spelling differences.
    """
    if not a or not b:
        return 0.0
    best_total = 0.0
    for name_a in a:
        na = _normalize(name_a)
        for name_b in b:
            nb = _normalize(name_b)
            score = fuzz.token_sort_ratio(na, nb)
            best_total = max(best_total, score)
    return best_total


class ScoreBreakdown(NamedTuple):
    title_score:    float
    author_score:   float
    narrator_score: float
    series_score:   float
    runtime_score:  float
    confidence:     float


def score_candidate(
    candidate: AudibleCandidate,
    metadata: LocalMetadata | None,
    audiobook: LocalAudiobook,
) -> ScoreBreakdown:
    """
    Score a single candidate against the local file's data.

    Returns a ScoreBreakdown with per-dimension scores (0–100) and
    the final weighted confidence score (0–100).
    """
    weights = SCORE_WEIGHTS

    # --- Title score ---
    local_title = ""
    if metadata:
        local_title = (
            metadata.title_from_tags
            or metadata.album_from_tags
            or ""
        )
    if not local_title:
        # Fall back to filename stem
        from pathlib import Path
        local_title = Path(audiobook.filename).stem

    title_score = float(
        fuzz.token_sort_ratio(
            _normalize(local_title),
            _normalize(candidate.title),
        )
    )

    # --- Author score ---
    local_authors: list[str] = []
    if metadata and metadata.author_from_tags:
        local_authors = [metadata.author_from_tags]

    author_score = _token_overlap(local_authors, candidate.authors) if local_authors else 0.0

    # --- Narrator score ---
    # Only score if at least one side has narrator info; otherwise neutral (skip)
    local_narrators: list[str] = []
    if metadata and metadata.narrator_from_tags:
        local_narrators = [metadata.narrator_from_tags]

    if local_narrators and candidate.narrators:
        narrator_score = _token_overlap(local_narrators, candidate.narrators)
    else:
        # No narrator data on one or both sides — treat as neutral (50)
        # so it doesn't heavily penalize items without narrator tags
        narrator_score = 50.0

    # --- Series score ---
    local_series = (metadata.series_from_tags or "") if metadata else ""

    if local_series and candidate.series_name:
        series_score = float(
            fuzz.token_sort_ratio(
                _normalize(local_series),
                _normalize(candidate.series_name),
            )
        )
    elif not local_series and not candidate.series_name:
        # Both have no series — neutral
        series_score = 50.0
    else:
        # One has series, other doesn't — mild penalty
        series_score = 20.0

    # --- Runtime score ---
    local_duration = (metadata.duration_seconds or 0.0) if metadata else 0.0
    candidate_duration = candidate.runtime_seconds or 0.0

    if local_duration > 0 and candidate_duration > 0:
        diff_ratio = abs(local_duration - candidate_duration) / max(local_duration, candidate_duration)
        if diff_ratio <= RUNTIME_TOLERANCE_FULL:
            runtime_score = 100.0
        elif diff_ratio <= RUNTIME_TOLERANCE_PARTIAL:
            # Linear interpolation between full and partial tolerance bands
            t = (diff_ratio - RUNTIME_TOLERANCE_FULL) / (
                RUNTIME_TOLERANCE_PARTIAL - RUNTIME_TOLERANCE_FULL
            )
            runtime_score = 100.0 * (1.0 - t * 0.5)  # 100 -> 50 over the band
        else:
            runtime_score = 0.0
    else:
        # Missing duration on one or both sides — neutral
        runtime_score = 50.0

    # --- Weighted confidence ---
    confidence = (
        title_score    * weights["title"]
        + author_score   * weights["author"]
        + narrator_score * weights["narrator"]
        + series_score   * weights["series"]
        + runtime_score  * weights["runtime"]
    )

    return ScoreBreakdown(
        title_score=round(title_score, 1),
        author_score=round(author_score, 1),
        narrator_score=round(narrator_score, 1),
        series_score=round(series_score, 1),
        runtime_score=round(runtime_score, 1),
        confidence=round(confidence, 1),
    )


def determine_match_status(confidence: float) -> MatchStatus:
    if confidence >= CONFIDENCE_AUTO_APPROVE:
        return MatchStatus.AUTO
    elif confidence >= CONFIDENCE_REVIEW_REQUIRED:
        return MatchStatus.REVIEW_REQUIRED
    else:
        return MatchStatus.UNMATCHED


async def match_audiobook(
    audiobook: LocalAudiobook,
    metadata: LocalMetadata | None,
    candidates: list[AudibleCandidate],
    batch_run_id: int,
) -> tuple[MatchResult, AudibleCandidate | None]:
    """
    Score all candidates for one audiobook and return the best match.

    Returns:
        (MatchResult, best_candidate | None)
        MatchResult has match_status set appropriately.
        best_candidate is None if no candidates were provided.
    """
    if not candidates:
        result = MatchResult(
            local_audiobook_id=audiobook.id,
            batch_run_id=batch_run_id,
            match_status=MatchStatus.UNMATCHED,
            notes="No candidates returned by provider.",
        )
        return result, None

    # Score every candidate
    scored: list[tuple[ScoreBreakdown, AudibleCandidate]] = []
    for candidate in candidates:
        breakdown = score_candidate(candidate, metadata, audiobook)
        scored.append((breakdown, candidate))

    # Sort by confidence descending
    scored.sort(key=lambda x: x[0].confidence, reverse=True)
    best_breakdown, best_candidate = scored[0]

    status = determine_match_status(best_breakdown.confidence)

    result = MatchResult(
        local_audiobook_id=audiobook.id,
        batch_run_id=batch_run_id,
        selected_candidate_asin=best_candidate.asin,
        confidence_score=best_breakdown.confidence,
        match_status=status,
        title_score=best_breakdown.title_score,
        author_score=best_breakdown.author_score,
        narrator_score=best_breakdown.narrator_score,
        series_score=best_breakdown.series_score,
        runtime_score=best_breakdown.runtime_score,
    )

    logger.debug(
        "Match '%s' -> '%s' (%.1f%% confidence, status=%s)",
        audiobook.filename,
        best_candidate.title,
        best_breakdown.confidence,
        status.value,
    )

    return result, best_candidate
