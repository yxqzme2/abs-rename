"""
tests/test_matcher.py
----------------------
Tests for the scoring/matching engine.
Covers weighted confidence calculation, runtime tolerance bands,
and match status thresholds.
"""

import pytest
from app.models.local_audiobook import LocalAudiobook, LocalMetadata, ScanStatus
from app.models.candidate import AudibleCandidate
from app.services.matcher import score_candidate, determine_match_status, ScoreBreakdown
from app.models.match_result import MatchStatus


def _audiobook() -> LocalAudiobook:
    return LocalAudiobook(
        batch_run_id=1,
        source_path="/src/test.m4b",
        filename="The Name of the Wind.m4b",
        folder_path="/src",
        scan_status=ScanStatus.SCANNED,
    )


def _metadata(**kwargs) -> LocalMetadata:
    defaults = dict(
        local_audiobook_id=1,
        title_from_tags="The Name of the Wind",
        author_from_tags="Patrick Rothfuss",
        narrator_from_tags="Nick Podehl",
        series_from_tags="The Kingkiller Chronicle",
        series_index_from_tags="1",
        duration_seconds=47700.0,
    )
    defaults.update(kwargs)
    return LocalMetadata(**defaults)


def _candidate(**kwargs) -> AudibleCandidate:
    defaults = dict(
        asin="B002V0QUOC",
        title="The Name of the Wind",
        authors=["Patrick Rothfuss"],
        narrators=["Nick Podehl"],
        series_name="The Kingkiller Chronicle",
        series_position="1",
        runtime_seconds=47700.0,
    )
    defaults.update(kwargs)
    return AudibleCandidate(**defaults)


class TestScoreCandidate:
    def test_perfect_match_scores_high(self):
        bd = score_candidate(_candidate(), _metadata(), _audiobook())
        assert bd.confidence >= 90.0

    def test_title_mismatch_lowers_score(self):
        cand = _candidate(title="Completely Different Book")
        bd = score_candidate(cand, _metadata(), _audiobook())
        assert bd.title_score < 50.0
        assert bd.confidence < 90.0

    def test_author_mismatch_lowers_score(self):
        cand = _candidate(authors=["Wrong Author"])
        bd = score_candidate(cand, _metadata(), _audiobook())
        assert bd.author_score < 50.0

    def test_runtime_within_5pct_scores_full(self):
        # Within ±5% → runtime_score should be 100
        cand = _candidate(runtime_seconds=47700.0 * 1.04)  # +4%
        bd = score_candidate(cand, _metadata(), _audiobook())
        assert bd.runtime_score == 100.0

    def test_runtime_within_15pct_scores_partial(self):
        # Between 5% and 15% → partial score (between 50 and 100)
        cand = _candidate(runtime_seconds=47700.0 * 1.10)  # +10%
        bd = score_candidate(cand, _metadata(), _audiobook())
        assert 50.0 <= bd.runtime_score < 100.0

    def test_runtime_beyond_15pct_scores_zero(self):
        cand = _candidate(runtime_seconds=47700.0 * 0.80)  # -20%
        bd = score_candidate(cand, _metadata(), _audiobook())
        assert bd.runtime_score == 0.0

    def test_missing_runtime_scores_neutral(self):
        cand = _candidate(runtime_seconds=None)
        bd = score_candidate(cand, _metadata(duration_seconds=None), _audiobook())
        assert bd.runtime_score == 50.0

    def test_both_no_series_scores_neutral(self):
        cand = _candidate(series_name=None, series_position=None)
        meta = _metadata(series_from_tags=None, series_index_from_tags=None)
        bd = score_candidate(cand, meta, _audiobook())
        assert bd.series_score == 50.0

    def test_one_side_has_series_other_doesnt(self):
        cand = _candidate(series_name="Some Series")
        meta = _metadata(series_from_tags=None)
        bd = score_candidate(cand, meta, _audiobook())
        assert bd.series_score == 20.0

    def test_no_narrator_data_scores_neutral(self):
        cand = _candidate(narrators=[])
        meta = _metadata(narrator_from_tags=None)
        bd = score_candidate(cand, meta, _audiobook())
        assert bd.narrator_score == 50.0

    def test_title_normalized_noise_stripped(self):
        # "Unabridged" in title should not hurt similarity
        cand = _candidate(title="The Name of the Wind: Unabridged")
        bd = score_candidate(cand, _metadata(), _audiobook())
        assert bd.title_score >= 85.0

    def test_score_breakdown_is_named_tuple(self):
        bd = score_candidate(_candidate(), _metadata(), _audiobook())
        assert isinstance(bd, ScoreBreakdown)
        assert hasattr(bd, 'confidence')
        assert hasattr(bd, 'title_score')

    def test_confidence_is_weighted_sum(self):
        """Verify the confidence formula matches the expected weighted sum."""
        from app.config import SCORE_WEIGHTS
        bd = score_candidate(_candidate(), _metadata(), _audiobook())
        expected = (
            bd.title_score    * SCORE_WEIGHTS["title"]
            + bd.author_score   * SCORE_WEIGHTS["author"]
            + bd.narrator_score * SCORE_WEIGHTS["narrator"]
            + bd.series_score   * SCORE_WEIGHTS["series"]
            + bd.runtime_score  * SCORE_WEIGHTS["runtime"]
        )
        assert abs(bd.confidence - round(expected, 1)) < 0.2


class TestDetermineMatchStatus:
    def test_above_90_is_auto(self):
        assert determine_match_status(95.0) == MatchStatus.AUTO

    def test_exactly_90_is_auto(self):
        assert determine_match_status(90.0) == MatchStatus.AUTO

    def test_between_75_and_90_is_review(self):
        assert determine_match_status(80.0) == MatchStatus.REVIEW_REQUIRED

    def test_exactly_75_is_review(self):
        assert determine_match_status(75.0) == MatchStatus.REVIEW_REQUIRED

    def test_below_75_is_unmatched(self):
        assert determine_match_status(60.0) == MatchStatus.UNMATCHED

    def test_zero_is_unmatched(self):
        assert determine_match_status(0.0) == MatchStatus.UNMATCHED
