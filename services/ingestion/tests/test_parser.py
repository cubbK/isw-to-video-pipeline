"""Tests for the ISW HTML parser.

Uses the real example HTML fixture at examples/isw_report.html.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Resolve paths relative to repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPO_ROOT / "examples" / "isw_report.html"

# Add the service source to sys.path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parser import (  # noqa: E402
    ParsedReport,
    _clean_text,
    _extract_date_from_title,
    parse_report,
)


@pytest.fixture()
def html_fixture() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


@pytest.fixture()
def parsed(html_fixture: str) -> ParsedReport:
    return parse_report(html_fixture)


# ---------------------------------------------------------------------------
# Date extraction
# ---------------------------------------------------------------------------
class TestDateExtraction:
    def test_extracts_date_from_standard_title(self):
        title = "Russian Offensive Campaign Assessment, February 16, 2026"
        assert _extract_date_from_title(title) == "2026-02-16"

    def test_extracts_date_january(self):
        assert _extract_date_from_title("Foo, January 1, 2025") == "2025-01-01"

    def test_extracts_date_december(self):
        assert _extract_date_from_title("Bar, December 31, 2025") == "2025-12-31"

    def test_raises_on_missing_date(self):
        with pytest.raises(ValueError, match="Could not extract date"):
            _extract_date_from_title("No date here")


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------
class TestCleanText:
    def test_collapses_whitespace(self):
        assert _clean_text("  hello   world  ") == "hello world"

    def test_replaces_nbsp(self):
        assert _clean_text("hello\xa0world") == "hello world"


# ---------------------------------------------------------------------------
# Full parse against fixture
# ---------------------------------------------------------------------------
class TestParseReport:
    def test_title(self, parsed: ParsedReport):
        assert "February 16, 2026" in parsed.title
        assert "Russian Offensive Campaign Assessment" in parsed.title

    def test_date(self, parsed: ParsedReport):
        assert parsed.date == "2026-02-16"

    def test_toplines_non_empty(self, parsed: ParsedReport):
        assert len(parsed.toplines) >= 5

    def test_topline_headline_is_bold_lead(self, parsed: ParsedReport):
        first = parsed.toplines[0]
        assert "Russian officials" in first.headline
        assert "Geneva" in first.headline

    def test_topline_body_is_not_empty(self, parsed: ParsedReport):
        for t in parsed.toplines:
            # At least the headline OR body should have content
            assert t.headline or t.body

    def test_key_takeaways_count(self, parsed: ParsedReport):
        assert len(parsed.key_takeaways) == 6

    def test_key_takeaways_content(self, parsed: ParsedReport):
        assert "Russian officials are unlikely" in parsed.key_takeaways[0]
        assert "energy infrastructure" in parsed.key_takeaways[1]

    def test_sections_present(self, parsed: ParsedReport):
        section_ids = [s.id for s in parsed.sections]
        assert "ukr-ops" in section_ids
        assert "russian-ne" in section_ids
        assert "ru-me" in section_ids
        assert "russian-se" in section_ids
        assert "air-missile-drone" in section_ids
        assert "belarus" in section_ids

    def test_section_has_title(self, parsed: ParsedReport):
        for s in parsed.sections:
            assert s.title, f"Section {s.id} has no title"

    def test_section_body_not_empty(self, parsed: ParsedReport):
        # Most sections have body text (Belarus may be minimal)
        non_empty = [s for s in parsed.sections if s.body]
        assert len(non_empty) >= 5

    def test_section_with_map(self, parsed: ParsedReport):
        # russian-ne section should have a map
        ne = next(s for s in parsed.sections if s.id == "russian-ne")
        assert ne.map_url is not None
        assert "Sumy" in ne.map_url or "Sumy" in (ne.map_title or "")

    def test_overview_map_url(self, parsed: ParsedReport):
        assert parsed.overview_map_url is not None
        assert "Russo-Ukrainian-War" in parsed.overview_map_url
        assert parsed.overview_map_url.endswith(".webp")

    def test_source_refs_non_empty(self, parsed: ParsedReport):
        assert len(parsed.source_refs) > 10

    def test_source_refs_deobfuscated(self, parsed: ParsedReport):
        # Make sure ' dot ' is replaced with '.'
        for ref in parsed.source_refs:
            assert " dot " not in ref

    def test_serializable_to_json(self, parsed: ParsedReport):
        d = parsed.to_dict()
        json_str = json.dumps(d, ensure_ascii=False)
        assert json_str
        roundtrip = json.loads(json_str)
        assert roundtrip["date"] == "2026-02-16"
        assert len(roundtrip["key_takeaways"]) == 6

    def test_no_duplicate_sections(self, parsed: ParsedReport):
        ids = [s.id for s in parsed.sections]
        assert len(ids) == len(set(ids))

    def test_maps_in_donetsk_section(self, parsed: ParsedReport):
        # ru-me has multiple maps — we grab the first
        ru_me = next(s for s in parsed.sections if s.id == "ru-me")
        assert ru_me.map_url is not None

    def test_kharkiv_map_in_sections(self, parsed: ParsedReport):
        """Kharkiv map appears inside ru-me section body."""
        ru_me = next(s for s in parsed.sections if s.id == "ru-me")
        assert "Kharkiv" in ru_me.body or "Kharkiv" in (ru_me.map_title or "")
