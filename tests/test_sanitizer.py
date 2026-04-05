"""
tests/test_sanitizer.py
------------------------
Tests for filename/path sanitization.
Covers invalid characters, Windows reserved names, trailing dots/spaces,
and path segment joining.
"""

import pytest
from app.path_engine.sanitizer import sanitize_segment, sanitize_path


class TestSanitizeSegment:
    def test_clean_string_unchanged(self):
        assert sanitize_segment("Patrick Rothfuss") == "Patrick Rothfuss"

    def test_invalid_chars_replaced_with_hyphen(self):
        # Windows-invalid characters: \ / : * ? " < > |
        result = sanitize_segment('Title: "The Best" Book?')
        assert ":" not in result
        assert '"' not in result
        assert "?" not in result

    def test_trailing_dots_stripped(self):
        # Windows disallows trailing dots on folder/file names
        assert sanitize_segment("My Folder.") == "My Folder"
        assert sanitize_segment("Name...") == "Name"

    def test_trailing_spaces_stripped(self):
        assert sanitize_segment("Name   ") == "Name"

    def test_leading_dots_stripped(self):
        assert sanitize_segment(".hidden") == "hidden"

    def test_windows_reserved_con(self):
        result = sanitize_segment("CON")
        assert result == "_CON"

    def test_windows_reserved_nul(self):
        assert sanitize_segment("NUL") == "_NUL"

    def test_windows_reserved_com1(self):
        assert sanitize_segment("COM1") == "_COM1"

    def test_windows_reserved_lpt9(self):
        assert sanitize_segment("LPT9") == "_LPT9"

    def test_windows_reserved_case_insensitive(self):
        assert sanitize_segment("con") == "_con"
        assert sanitize_segment("Nul") == "_Nul"

    def test_reserved_name_in_longer_string_not_prefixed(self):
        # "CONSOLE" is not a reserved name — only exact matches
        result = sanitize_segment("CONSOLE")
        assert result == "CONSOLE"

    def test_empty_string_returns_unknown(self):
        assert sanitize_segment("") == "Unknown"

    def test_whitespace_only_returns_unknown(self):
        assert sanitize_segment("   ") == "Unknown"

    def test_slash_in_segment_replaced(self):
        result = sanitize_segment("Author/Name")
        assert "/" not in result

    def test_backslash_replaced(self):
        result = sanitize_segment("Author\\Name")
        assert "\\" not in result

    def test_consecutive_hyphens_collapsed(self):
        result = sanitize_segment("A--B---C")
        assert "---" not in result

    def test_colon_in_series_name(self):
        # Common pattern: "Series: Book 1"
        result = sanitize_segment("Mistborn: The Final Empire")
        assert ":" not in result
        assert len(result) > 0


class TestSanitizePath:
    def test_basic_path(self):
        result = sanitize_path("Patrick Rothfuss/The Kingkiller Chronicle/01 - The Name of the Wind")
        assert result == "Patrick Rothfuss/The Kingkiller Chronicle/01 - The Name of the Wind"

    def test_invalid_chars_in_multiple_segments(self):
        result = sanitize_path("Author: Name/Title? Book")
        assert ":" not in result
        assert "?" not in result

    def test_backslash_separator_normalized(self):
        result = sanitize_path("Author\\Title")
        # Should be split and rejoined with /
        assert "\\" not in result
        assert "/" in result

    def test_reserved_name_in_path(self):
        result = sanitize_path("Author/CON/Title")
        assert "/CON/" not in result
        assert "_CON" in result
