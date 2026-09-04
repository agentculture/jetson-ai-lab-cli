"""Tests for the HTML report renderer (jlab.members.report).

Covers t5's acceptance criteria:

1. Self-contained, build-step-free HTML (no ``<script>``, no external
   fetch) and ``dependencies == []`` in ``pyproject.toml``.
2. Every user-derived string is HTML-escaped — including a genuinely
   hostile display name, which must render as literal text.
3. Run metadata in the header: scan timestamp, window start/end, channels
   attempted / ok / partial / failed.
4. The page states its own coverage (public **text** channels only).
5. No verdict language anywhere in the rendered output, and the default
   row order is labelled as presentation.
6. Readable without reading Python or JSON (headings + a table + labelled
   diagrams, in prose a maintainer can follow).
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from jlab.members.report import render_report, write_report

HOSTILE_NAME = '<script>alert(1)</script>"><img src=x onerror=alert(2)>'


def _member(
    author_id: str,
    *,
    messages: int = 3,
    channels: int = 2,
    questions: int = 1,
    total_length: int = 60,
) -> dict:
    return {
        "author_id": author_id,
        "message_count": messages,
        "distinct_channels": channels,
        "question_starts": questions,
        "substance": {
            "total_length": total_length,
            "avg_length": (total_length / messages) if messages else 0.0,
        },
    }


def _aggregate(**overrides) -> dict:
    base = {
        "guild_id": "1326246312072581160",
        "since_days": 30,
        "cutoff": "2026-08-05T00:00:00+00:00",
        "scanned_text_channels": 104,
        "channels_ok": 101,
        "channels_partial": 2,
        "channels_failed": 1,
        "complete": False,
        "message_count": 1234,
        "members": [
            _member("111", messages=9, channels=4, questions=3, total_length=450),
            _member("222", messages=2, channels=1, questions=0, total_length=18),
        ],
    }
    base.update(overrides)
    return base


# --- 1. self-contained, no build step, no dependencies -------------------


def test_renders_a_standalone_html_document():
    html = render_report(_aggregate())
    assert html.startswith("<!doctype html>")
    assert "</html>" in html
    assert "<svg" in html  # diagrams are hand-emitted inline SVG


def test_no_script_tags_and_no_external_resources():
    html = render_report(_aggregate())
    lowered = html.lower()
    assert "<script" not in lowered
    assert "http://" not in lowered
    assert "https://" not in lowered
    assert "<link" not in lowered
    assert "<iframe" not in lowered


def test_runtime_dependencies_stay_empty():
    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["dependencies"] == []


# --- 2. escaping / hostile display name ---------------------------------


def test_hostile_display_name_renders_as_literal_text():
    html = render_report(
        _aggregate(),
        resolved={"111": {"display_name": HOSTILE_NAME}},
    )
    # The raw payload never appears; the escaped form does.
    assert "<script>alert(1)</script>" not in html
    assert "<img src=x" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(2)&gt;" in html
    # No unescaped double quote from the name leaks into the document.
    assert '"><img' not in html
    assert "&quot;&gt;&lt;img" in html
    # Still exactly one script-free document.
    assert "<script" not in html.lower()


def test_hostile_ids_and_guild_id_are_escaped():
    agg = _aggregate(
        guild_id='<b>guild</b>"x',
        members=[_member('<i>1</i>&"')],
    )
    html = render_report(agg)
    assert "<b>guild</b>" not in html
    assert "&lt;b&gt;guild&lt;/b&gt;" in html
    assert "<i>1</i>" not in html
    assert "&lt;i&gt;1&lt;/i&gt;" in html


def test_escaping_covers_attribute_context():
    """A hostile name reaching an attribute (SVG bar tooltip) is quote-escaped."""
    html = render_report(
        _aggregate(),
        resolved={"111": {"display_name": 'x" onload="alert(1)'}},
    )
    assert 'onload="alert(1)' not in html
    assert "&quot; onload=&quot;alert(1)" in html


def test_display_names_are_used_when_supplied_and_ids_alone_otherwise():
    plain = render_report(_aggregate())
    assert "111" in plain
    assert "Ada Lovelace" not in plain

    named = render_report(_aggregate(), resolved={"111": {"display_name": "Ada Lovelace"}})
    assert "Ada Lovelace" in named
    # The id stays visible so a maintainer can disambiguate renames.
    assert "111" in named


# --- 3. run metadata -----------------------------------------------------


def test_header_carries_run_metadata():
    html = render_report(_aggregate(), generated_at="2026-09-04T12:00:00+00:00")
    assert "2026-09-04T12:00:00+00:00" in html  # scan timestamp
    assert "2026-08-05T00:00:00+00:00" in html  # window start
    assert "104" in html  # channels attempted
    assert "101" in html  # channels fully read
    for label in (
        "Scan finished",
        "Window start",
        "Window end",
        "attempted",
        "read in full",
        "partially read",
        "could not be read",
    ):
        assert label in html


def test_incomplete_coverage_is_stated_prominently():
    html = render_report(_aggregate(complete=False))
    assert "Coverage is incomplete" in html


def test_complete_coverage_says_so():
    agg = _aggregate(complete=True, channels_partial=0, channels_failed=0, channels_ok=104)
    html = render_report(agg)
    assert "Coverage is incomplete" not in html
    assert "every channel attempted was read in full" in html


def test_missing_metadata_fields_render_as_unknown_not_none():
    agg = _aggregate(cutoff=None, scanned_text_channels=None, complete=None)
    html = render_report(agg)
    assert ">None<" not in html
    assert "unknown" in html


def test_generated_at_defaults_to_now_in_utc():
    html = render_report(_aggregate())
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", html)


# --- 4. self-stated coverage --------------------------------------------


def test_states_public_text_channel_coverage_on_its_face():
    html = render_report(_aggregate())
    assert "public text channels" in html
    assert "voice" in html.lower()
    assert "forum" in html.lower()


def test_excluded_member_count_is_stated():
    html = render_report(_aggregate(), excluded_count=7)
    assert "7" in html
    assert "no longer in the guild" in html


def test_excluded_count_absent_when_not_supplied():
    html = render_report(_aggregate())
    assert "no longer in the guild" not in html


# --- 5. no verdict -------------------------------------------------------

_VERDICT_WORDS = (
    "top",
    "best",
    "most active",
    "recommend",
    "leaderboard",
    "winner",
    "rank",
    "score",
    "shortlist",
    "candidate",
    "worst",
    "influen",
)

# The only places a verdict word may appear are the page's own explicit
# disclaimers, which *deny* a verdict. They are stripped before the grep so
# the disclaimer cannot smuggle verdict language back in.
_ALLOWED_DISCLAIMERS = (
    "does not rank, judge, or recommend anyone",
    "not a recommendation",
)


def test_no_verdict_language_anywhere_in_the_rendered_output():
    html = render_report(
        _aggregate(),
        resolved={"111": {"display_name": "Ada Lovelace"}},
        excluded_count=2,
    )
    lowered = html.lower()
    for phrase in _ALLOWED_DISCLAIMERS:
        assert phrase in lowered, f"expected disclaimer {phrase!r} on the page"
        lowered = lowered.replace(phrase, " ")
    for word in _VERDICT_WORDS:
        assert word not in lowered, f"verdict language {word!r} reached the rendered output"


def test_four_signals_are_shown_side_by_side():
    html = render_report(_aggregate())
    for header in (
        "Messages",
        "Distinct channels",
        "Messages ending in a question",
        "Characters written",
    ):
        assert header in html


def test_default_row_order_is_labelled_as_presentation():
    html = render_report(_aggregate())
    assert "presentation choice" in html
    assert "not a recommendation" in html


def test_rows_are_ordered_alphabetically_by_label():
    html = render_report(
        _aggregate(),
        resolved={"111": {"display_name": "zeta"}, "222": {"display_name": "alpha"}},
    )
    assert html.index(">alpha<") < html.index(">zeta<")


# --- 6. readability ------------------------------------------------------


def test_page_reads_as_prose_and_a_table():
    html = render_report(_aggregate())
    assert "<h1" in html
    assert "<table" in html
    assert "<th" in html
    assert "Jetson AI Lab" in html


def test_diagram_titles_describe_signals_without_judging():
    html = render_report(_aggregate())
    for title in (
        "Messages per member",
        "Distinct channels per member",
        "Messages ending in a question per member",
        "Characters written per member",
    ):
        assert title in html


def test_empty_member_list_renders_a_readable_page():
    html = render_report(_aggregate(members=[], message_count=0))
    assert "No member rows" in html
    assert "</html>" in html


def test_all_zero_signals_do_not_crash_the_bar_chart():
    agg = _aggregate(
        members=[_member("111", messages=0, channels=0, questions=0, total_length=0)],
    )
    html = render_report(agg)
    assert "<svg" in html


# --- write_report --------------------------------------------------------


def test_write_report_writes_into_the_repo_anchored_directory():
    path = write_report(_aggregate(), filename="test-members-report.html")
    try:
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        assert text.startswith("<!doctype html>")
        assert path.parts[-3:] == ("reports", "members", "test-members-report.html")
    finally:
        path.unlink(missing_ok=True)


def test_write_report_returns_the_same_html_it_rendered():
    path = write_report(_aggregate(), filename="test-members-report-2.html")
    try:
        assert path.read_text(encoding="utf-8") == render_report(
            _aggregate(), generated_at=_generated_at_of(path)
        )
    finally:
        path.unlink(missing_ok=True)


def _generated_at_of(path: Path) -> str:
    match = re.search(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00",
        path.read_text(encoding="utf-8"),
    )
    assert match is not None
    return match.group(0)


def test_render_report_rejects_a_non_mapping_aggregate():
    with pytest.raises(Exception):
        render_report(None)  # type: ignore[arg-type]
