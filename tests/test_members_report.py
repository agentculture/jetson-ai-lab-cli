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

import csv
import re
import shutil
import subprocess
import threading
import tomllib
from pathlib import Path

import pytest

from jlab import atomic_writeset
from jlab.cli._errors import CliError
from jlab.members import paths as members_paths
from jlab.members import report as report_module
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


def _resolved() -> dict:
    return {
        "111": {"display_name": "Ada"},
        "222": {"display_name": "Bo"},
    }


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
        # data/reports/members/<run-id>/test-members-report.html
        assert path.name == "test-members-report.html"
        assert path.parent.parent.parts[-2:] == ("reports", "members")
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def test_write_report_returns_the_same_html_it_rendered():
    path = write_report(_aggregate(), filename="test-members-report-2.html")
    try:
        assert path.read_text(encoding="utf-8") == render_report(
            _aggregate(), generated_at=_generated_at_of(path)
        )
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


# --- per-run directory + atomic artifact set (t15) ------------------------


def _read_csv_beside(html_path: Path) -> list[list[str]]:
    csv_path = html_path.with_suffix(".csv")
    with csv_path.open(newline="", encoding="utf-8") as fh:
        return list(csv.reader(fh))


def test_write_report_puts_its_whole_set_in_a_private_run_directory():
    """Criterion 1: one subdirectory per run, holding that run's whole set."""
    path = write_report(_aggregate())
    try:
        run_dir = path.parent
        assert run_dir.parent == members_paths.members_reports_dir()
        names = sorted(p.name for p in run_dir.iterdir())
        assert names == ["members-report.csv", "members-report.html"]
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def test_two_runs_get_separate_directories_and_neither_deletes_the_other():
    """Criterion 1/3: no sibling run's directory is ever deleted."""
    first = write_report(_aggregate())
    try:
        second = write_report(_aggregate())
        try:
            assert first.parent != second.parent
            assert first.is_file()
            assert first.with_suffix(".csv").is_file()
            assert second.is_file()
            assert second.with_suffix(".csv").is_file()
        finally:
            shutil.rmtree(second.parent, ignore_errors=True)
    finally:
        shutil.rmtree(first.parent, ignore_errors=True)


def test_the_run_directory_is_swapped_into_place_by_a_single_os_replace(monkeypatch):
    """Criterion 1: a fresh run directory takes the one-rename atomic branch."""
    calls: list[tuple] = []
    real_replace = atomic_writeset.os.replace

    def _counting_replace(src, dst, *a, **kw):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(atomic_writeset.os, "replace", _counting_replace)

    path = write_report(_aggregate())
    try:
        assert len(calls) == 1, f"expected one atomic rename, got {calls}"
        assert calls[0][1] == str(path.parent)
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def test_a_failed_run_leaves_no_partial_artifacts_and_spares_the_previous_run(
    monkeypatch,
):
    """Criterion 2/3: never a complete CSV beside a missing or stale HTML."""
    previous = write_report(_aggregate(), filename="previous-run.html")
    try:
        previous_names = sorted(p.name for p in previous.parent.iterdir())
        doomed_run_id = members_paths.new_run_id()

        def _boom(tmp_dir, dest_dir):
            raise RuntimeError("killed partway through the write")

        monkeypatch.setattr(atomic_writeset, "_swap_into_place", _boom)

        aggregate = _aggregate()
        with pytest.raises(RuntimeError):
            write_report(aggregate, run_id=doomed_run_id)

        # Nothing of the failed run reached the report directory: neither its
        # own run directory nor a staging directory named after it. (Asserted
        # against this run's own id rather than a directory-wide before/after
        # snapshot, which other workers under `pytest -n auto` would perturb.)
        reports_dir = members_paths.members_reports_dir()
        assert not members_paths.members_run_dir(doomed_run_id).exists()
        leftovers = [p.name for p in reports_dir.iterdir() if doomed_run_id in p.name]
        assert leftovers == [], f"a failed run left artifacts behind: {leftovers}"

        # The previous run survives fully intact.
        assert sorted(p.name for p in previous.parent.iterdir()) == previous_names
        assert previous.read_text(encoding="utf-8").startswith("<!doctype html>")
    finally:
        shutil.rmtree(previous.parent, ignore_errors=True)


def test_parallel_writers_do_not_clobber_each_other():
    """Criterion 3: concurrent calls each get their own intact run directory."""
    results: list[Path] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    start = threading.Barrier(8)

    def _run() -> None:
        try:
            start.wait(timeout=30)
            path = write_report(_aggregate())
        except BaseException as exc:  # pragma: no cover - only on failure
            with lock:
                errors.append(exc)
        else:
            with lock:
                results.append(path)

    threads = [threading.Thread(target=_run) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    try:
        assert not errors, errors
        assert len({path.parent for path in results}) == 8
        for path in results:
            assert path.read_text(encoding="utf-8").startswith("<!doctype html>")
            assert path.with_suffix(".csv").is_file()
            assert sorted(p.name for p in path.parent.iterdir()) == [
                "members-report.csv",
                "members-report.html",
            ]
    finally:
        for path in results:
            shutil.rmtree(path.parent, ignore_errors=True)


def test_every_written_filename_is_gitignored():
    """Criterion 4: enumerate the ACTUAL filenames under the new layout."""
    path = write_report(_aggregate())
    try:
        written = sorted(path.parent.iterdir())
        assert written, "the run wrote nothing to enumerate"
        for artifact in written:
            result = subprocess.run(
                ["git", "check-ignore", "-q", str(artifact)],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
            )
            assert result.returncode == 0, f"{artifact} must be gitignored"
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def test_write_report_rejects_a_hostile_run_id():
    """Criterion 5: a run id cannot escape the report directory either."""
    aggregate = _aggregate()
    for hostile in ("../escape", "sub/dir", "/etc", "..", ".", ""):
        with pytest.raises(CliError):
            write_report(aggregate, run_id=hostile)


# --- the CSV sibling ------------------------------------------------------


def test_csv_header_and_rows_match_the_html_table():
    path = write_report(_aggregate(), resolved=_resolved())
    try:
        rows = _read_csv_beside(path)
        assert rows[0] == [
            "member",
            "discord_id",
            "messages",
            "distinct_channels",
            "messages_ending_in_a_question",
            "characters_written",
            "average_characters_per_message",
        ]
        html = path.read_text(encoding="utf-8")
        # Same rows, same order as the HTML table's <td class="id"> cells.
        html_ids = re.findall(r'<td class="id">([^<]*)</td>', html)
        assert [row[1] for row in rows[1:]] == html_ids
        assert len(rows) - 1 == len(html_ids)
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def test_csv_carries_no_message_content_beyond_counts_and_ids():
    """The privacy contract: only what the HTML already shows."""
    path = write_report(_aggregate(), resolved=_resolved())
    try:
        rows = _read_csv_beside(path)
        assert len(rows[0]) == 7
        for row in rows[1:]:
            assert len(row) == 7
            # Every cell after the label is numeric-or-id, never free text.
            for cell in row[1:]:
                float(cell)
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def test_report_module_contains_no_csv_code_of_its_own():
    """All CSV production goes through jlab.csv_export.write_csv."""
    source = Path(report_module.__file__).read_text(encoding="utf-8")
    assert "import csv" not in source
    assert "csv.writer" not in source
    assert "csv.DictWriter" not in source
    assert "escape_csv_field" not in source, "escaping is write_csv's job, not the renderer's"
    assert "from jlab.csv_export import write_csv" in source


def test_csv_escapes_a_formula_injection_display_name():
    """The shared escaper is actually reached (no hand-rolled path)."""
    path = write_report(
        _aggregate(),
        resolved={"111": {"display_name": "=cmd|' /c calc'!A1"}, "222": {"display_name": "b"}},
    )
    try:
        rows = _read_csv_beside(path)
        labels = [row[0] for row in rows[1:]]
        assert "'=cmd|' /c calc'!A1" in labels
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def _generated_at_of(path: Path) -> str:
    match = re.search(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00",
        path.read_text(encoding="utf-8"),
    )
    assert match is not None
    return match.group(0)


def test_render_report_rejects_a_non_mapping_aggregate():
    with pytest.raises(CliError) as exc:
        render_report(None)  # type: ignore[arg-type]
    assert "aggregate mapping" in exc.value.message
