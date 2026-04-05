"""
tests/test_template_engine.py
------------------------------
Tests for the path template rendering engine.
Covers all predefined templates, fallback cases, and edge cases.
"""

import pytest
from app.models.local_audiobook import LocalAudiobook, LocalMetadata, ScanStatus
from app.models.candidate import AudibleCandidate
from app.path_engine.template_engine import render_template, render_example


def _audiobook(**kwargs) -> LocalAudiobook:
    defaults = dict(
        batch_run_id=1,
        source_path="/src/test.m4b",
        filename="test.m4b",
        folder_path="/src",
        scan_status=ScanStatus.SCANNED,
    )
    defaults.update(kwargs)
    return LocalAudiobook(**defaults)


def _metadata(**kwargs) -> LocalMetadata:
    defaults = dict(local_audiobook_id=1)
    defaults.update(kwargs)
    return LocalMetadata(**defaults)


def _candidate(**kwargs) -> AudibleCandidate:
    defaults = dict(asin="B001TEST01", title="Test Title", authors=["Test Author"])
    defaults.update(kwargs)
    return AudibleCandidate(**defaults)


class TestRenderTemplate:
    def test_abs_series_format(self):
        template = "{author}/{series}/{series_index:02d} - {title}"
        ab   = _audiobook()
        meta = _metadata()
        cand = _candidate(
            title="The Name of the Wind",
            authors=["Patrick Rothfuss"],
            series_name="The Kingkiller Chronicle",
            series_position="1",
        )
        dest_dir, dest_file, full = render_template(template, ab, meta, cand)
        assert "Patrick Rothfuss" in dest_dir
        assert "The Kingkiller Chronicle" in dest_dir
        assert "01" in dest_dir
        assert "The Name of the Wind" in dest_dir
        assert dest_file.endswith(".m4b")
        # Filename should match the last folder segment
        last_folder = dest_dir.split("/")[-1]
        assert dest_file == last_folder + ".m4b"

    def test_standalone_fallback_when_no_series(self):
        template = "{author}/{series}/{series_index:02d} - {title}"
        ab   = _audiobook()
        meta = _metadata()
        cand = _candidate(
            title="Standalone Book",
            authors=["Some Author"],
            series_name=None,
            series_position=None,
        )
        dest_dir, dest_file, full = render_template(template, ab, meta, cand)
        assert "Standalone" in dest_dir
        assert "Some Author" in dest_dir
        assert "Standalone Book" in dest_dir
        # series_index prefix should be absent
        assert "00 -" not in dest_dir
        assert "None" not in dest_dir

    def test_unknown_author_fallback(self):
        template = "{author}/{title}"
        ab   = _audiobook()
        meta = _metadata()
        cand = _candidate(title="Orphan Book", authors=[])
        dest_dir, _, _ = render_template(template, ab, meta, cand)
        assert "Unknown Author" in dest_dir

    def test_year_omitted_when_missing(self):
        template = "{author}/{series}/{series_index:02d} - {title} ({year})"
        ab   = _audiobook()
        meta = _metadata()
        cand = _candidate(
            title="Time Book",
            authors=["Author"],
            series_name="Time Series",
            series_position="1",
            release_date=None,
        )
        dest_dir, _, _ = render_template(template, ab, meta, cand)
        assert "({year})" not in dest_dir
        assert "()" not in dest_dir

    def test_year_included_when_present(self):
        template = "{author}/{title} ({year})"
        ab   = _audiobook()
        meta = _metadata()
        cand = _candidate(title="Dated Book", authors=["Author"], release_date="2007-03-27")
        dest_dir, _, _ = render_template(template, ab, meta, cand)
        assert "2007" in dest_dir

    def test_series_index_zero_padded(self):
        template = "{author}/{series}/{series_index:02d} - {title}"
        ab   = _audiobook()
        meta = _metadata()
        cand = _candidate(
            title="Book Five",
            authors=["Auth"],
            series_name="MySeries",
            series_position="5",
        )
        dest_dir, _, _ = render_template(template, ab, meta, cand)
        assert "05 - Book Five" in dest_dir

    def test_series_index_fractional(self):
        # e.g. "2.5" should not crash zero-padding — falls back to string repr
        template = "{author}/{series}/{series_index:02d} - {title}"
        ab   = _audiobook()
        meta = _metadata()
        cand = _candidate(
            title="Interlude",
            authors=["Auth"],
            series_name="MySeries",
            series_position="2.5",
        )
        # Should not raise
        dest_dir, dest_file, full = render_template(template, ab, meta, cand)
        assert "MySeries" in dest_dir
        assert "Interlude" in dest_dir

    def test_filename_matches_last_folder_segment(self):
        template = "{author}/{title}"
        ab   = _audiobook()
        meta = _metadata()
        cand = _candidate(title="My Book", authors=["My Author"])
        dest_dir, dest_file, full = render_template(template, ab, meta, cand)
        last_seg = dest_dir.split("/")[-1]
        assert dest_file == last_seg + ".m4b"

    def test_full_path_is_dir_plus_filename(self):
        template = "{author}/{title}"
        ab   = _audiobook()
        meta = _metadata()
        cand = _candidate(title="My Book", authors=["My Author"])
        dest_dir, dest_file, full = render_template(template, ab, meta, cand)
        assert full == dest_dir + "/" + dest_file

    def test_candidate_data_takes_precedence_over_tags(self):
        template = "{author}/{title}"
        ab   = _audiobook()
        meta = _metadata(title_from_tags="Wrong Title", author_from_tags="Wrong Author")
        cand = _candidate(title="Correct Title", authors=["Correct Author"])
        dest_dir, _, _ = render_template(template, ab, meta, cand)
        assert "Correct Author" in dest_dir
        assert "Correct Title" in dest_dir
        assert "Wrong" not in dest_dir

    def test_no_candidate_falls_back_to_tags(self):
        template = "{author}/{title}"
        ab   = _audiobook()
        meta = _metadata(title_from_tags="Tag Title", author_from_tags="Tag Author")
        dest_dir, _, _ = render_template(template, ab, meta, candidate=None)
        assert "Tag Author" in dest_dir
        assert "Tag Title" in dest_dir

    def test_render_example_does_not_raise(self):
        # Should work with the default ABS series template
        result = render_example("{author}/{series}/{series_index:02d} - {title}")
        assert "Patrick Rothfuss" in result
        assert "Kingkiller" in result
