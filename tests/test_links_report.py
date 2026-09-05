"""Tests for the links report renderer (``jlab.links.report``) — t8.

Covers t8's acceptance criteria:

1. Hostile URLs (``javascript:``, ``data:``, a URL carrying embedded HTML)
   render as inert escaped text with no executable ``href``; only
   ``http``/``https`` may become a clickable anchor.
2. The page is self-contained: no ``<script>``, no external resource fetch,
   and ``pyproject.toml`` still reads ``dependencies = []``.
3. The flat CSV is one row per share with every cell a scalar, and the
   deduped per-URL summary CSV is *derived from that flat table in code*.
4. Coverage travels with the CSV: a reader holding only a CSV can tell the
   window it covers and whether any channel was partial or failed.
5. No verdict language anywhere in the rendered HTML.
6. ``from_attachment`` records are visibly marked as expiring, with their
   jump link beside them, in both the HTML and the CSVs.
7. HTML + both CSVs land as one atomic artifact set.
"""

from __future__ import annotations

import csv
import shutil
import subprocess
import threading
import tomllib
from pathlib import Path

import pytest

from jlab import atomic_writeset
from jlab.cli._errors import CliError
from jlab.links import paths as links_paths
from jlab.links.report import (
    FLAT_CSV_HEADER,
    SUMMARY_CSV_HEADER,
    flat_rows,
    render_report,
    summary_rows,
    write_report,
)

HOSTILE_NAME = '<script>alert(1)</script>"><img src=x onerror=alert(2)>'

JS_URL = "javascript:alert(1)"
DATA_URL = "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg=="
HTML_URL = 'https://example.com/a"><script>alert(3)</script>'


def _record(
    url: str = "https://example.com/a",
    *,
    channel_id: str = "c1",
    channel_name: str = "general",
    timestamp: str | None = "2026-09-01T10:00:00+00:00",
    thread: dict | None = None,
    author_id: str = "111",
    jump_url: str | None = "https://discord.com/channels/1/2/3",
    from_attachment: bool = False,
) -> dict:
    return {
        "url": url,
        "channel": {"id": channel_id, "name": channel_name},
        "timestamp": timestamp,
        "thread": thread if thread is not None else {},
        "author_id": author_id,
        "jump_url": jump_url,
        "from_attachment": from_attachment,
    }


def _records() -> list[dict]:
    return [
        _record("https://example.com/a", timestamp="2026-09-01T10:00:00+00:00"),
        _record(
            "https://example.com/a",
            channel_id="c2",
            channel_name="hardware",
            timestamp="2026-09-02T11:00:00+00:00",
            author_id="222",
        ),
        _record(
            "https://example.com/b",
            timestamp="2026-09-03T12:00:00+00:00",
            thread={"id": "t1", "name": "a thread"},
            author_id="222",
        ),
    ]


def _scan(**overrides) -> dict:
    base = {
        "guild_id": "1326246312072581160",
        "since_days": 30,
        "cutoff": "2026-08-05T00:00:00+00:00",
        "exclude_bots": True,
        "scanned_text_channels": 104,
        "channels_ok": 101,
        "channels_partial": 2,
        "channels_failed": 1,
        "message_count": 1234,
        "complete": False,
    }
    base.update(overrides)
    return base


def _resolved() -> dict:
    return {"111": {"display_name": "Ada"}, "222": {"display_name": "Bo"}}


def _read_csv(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.reader(fh))


# --- 1. hostile URLs are inert -------------------------------------------


def test_javascript_url_never_becomes_an_href():
    html = render_report(_scan(), [_record(JS_URL)])
    lowered = html.lower()
    assert 'href="javascript:' not in lowered
    assert "href='javascript:" not in lowered
    # It is still shown, as inert escaped text.
    assert "javascript:alert(1)" in html


def test_data_url_never_becomes_an_href():
    html = render_report(_scan(), [_record(DATA_URL)])
    assert 'href="data:' not in html.lower()
    assert DATA_URL in html


def test_url_carrying_embedded_html_cannot_break_out():
    html = render_report(_scan(), [_record(HTML_URL)])
    assert "<script>alert(3)</script>" not in html
    assert "&lt;script&gt;alert(3)&lt;/script&gt;" in html
    assert "<script" not in html.lower()
    # The stray double quote is escaped, so no attribute break-out.
    assert '"><script' not in html


def test_only_http_and_https_urls_become_anchors():
    records = [
        _record("https://example.com/ok"),
        _record("http://example.com/ok-too"),
        _record(JS_URL),
        _record(DATA_URL),
        _record("vbscript:msgbox(1)"),
        _record("//example.com/scheme-relative"),
        _record("file:///etc/passwd"),
    ]
    html = render_report(_scan(), records)
    assert 'href="https://example.com/ok"' in html
    assert 'href="http://example.com/ok-too"' in html
    for hostile in ("javascript:", "data:", "vbscript:", "file:"):
        assert f'href="{hostile}' not in html
    assert 'href="//example.com/scheme-relative"' not in html


def test_a_hostile_jump_url_is_not_turned_into_an_href_either():
    html = render_report(_scan(), [_record(jump_url=JS_URL)])
    assert 'href="javascript:' not in html.lower()


def test_hostile_display_name_renders_as_literal_text():
    html = render_report(_scan(), _records(), resolved={"111": {"display_name": HOSTILE_NAME}})
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert '"><img' not in html
    assert "<script" not in html.lower()


def test_hostile_channel_and_thread_names_are_escaped():
    records = [
        _record(channel_name='<b>chan</b>"x', thread={"id": "t", "name": "<i>th</i>"}),
    ]
    html = render_report(_scan(), records)
    assert "<b>chan</b>" not in html
    assert "&lt;b&gt;chan&lt;/b&gt;" in html
    assert "&lt;i&gt;th&lt;/i&gt;" in html


# --- 2. self-contained ---------------------------------------------------


def test_renders_a_standalone_html_document():
    html = render_report(_scan(), _records())
    assert html.startswith("<!doctype html>")
    assert "</html>" in html
    assert "<svg" in html


def test_no_script_tag_and_no_external_resource_fetch():
    html = render_report(_scan(), _records())
    lowered = html.lower()
    assert "<script" not in lowered
    assert "<link" not in lowered
    assert "<iframe" not in lowered
    assert "<img" not in lowered
    assert "src=" not in lowered
    assert "@import" not in lowered
    # Anchors to shared URLs are the only external references, and an
    # anchor fetches nothing until a reader clicks it.
    assert "url(" not in lowered


def test_runtime_dependencies_stay_empty():
    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["dependencies"] == []


# --- 3. flat CSV, derived summary ----------------------------------------


def test_flat_rows_is_one_row_per_share():
    rows = flat_rows(_records())
    assert len(rows) == 3


def test_flat_csv_is_one_row_per_share_with_scalar_cells():
    path = write_report(_scan(), _records(), filename="t8-flat.html")
    try:
        rows = _read_csv(path.with_suffix(".csv"))
        assert rows[0] == list(FLAT_CSV_HEADER)
        assert len(rows) == 1 + 3
        for row in rows[1:]:
            assert len(row) == len(FLAT_CSV_HEADER)
            for cell in row:
                assert isinstance(cell, str)
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def test_summary_is_derived_from_the_flat_table_alone():
    """The summary function's ONLY input is the flat table it deduplicates."""
    flat = flat_rows(_records())
    summary = summary_rows(flat)
    by_url = {row["url"]: row for row in summary}
    assert set(by_url) == {"https://example.com/a", "https://example.com/b"}
    assert by_url["https://example.com/a"]["shares"] == 2
    assert by_url["https://example.com/a"]["first_shared_at"] == "2026-09-01T10:00:00+00:00"
    assert by_url["https://example.com/a"]["last_shared_at"] == "2026-09-02T11:00:00+00:00"
    assert by_url["https://example.com/a"]["channels_touched"] == 2
    assert by_url["https://example.com/b"]["shares"] == 1
    assert by_url["https://example.com/b"]["channels_touched"] == 1


def test_summary_csv_matches_the_derived_summary_rows():
    path = write_report(_scan(), _records(), filename="t8-summary.html")
    try:
        summary_path = path.parent / "t8-summary-summary.csv"
        rows = _read_csv(summary_path)
        assert rows[0] == list(SUMMARY_CSV_HEADER)
        assert len(rows) == 1 + 2  # two distinct URLs from three shares
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def test_both_tables_appear_in_the_html():
    html = render_report(_scan(), _records())
    assert "Every share" in html
    assert "One row per address" in html


# --- 4. coverage travels with the CSV ------------------------------------


_COVERAGE_COLUMNS = (
    "window_start",
    "window_end",
    "channels_attempted",
    "channels_ok",
    "channels_partial",
    "channels_failed",
    "coverage_complete",
)


def test_both_csv_headers_carry_the_coverage_columns():
    for header in (FLAT_CSV_HEADER, SUMMARY_CSV_HEADER):
        for column in _COVERAGE_COLUMNS:
            assert column in header


def test_a_reader_with_only_a_csv_can_see_the_window_and_the_gaps():
    path = write_report(
        _scan(),
        _records(),
        generated_at="2026-09-05T00:00:00+00:00",
        filename="t8-coverage.html",
    )
    try:
        for csv_path in (path.with_suffix(".csv"), path.parent / "t8-coverage-summary.csv"):
            rows = _read_csv(csv_path)
            header = rows[0]
            assert rows[1:], "expected at least one data row to carry coverage"
            for row in rows[1:]:
                cells = dict(zip(header, row))
                assert cells["window_start"] == "2026-08-05T00:00:00+00:00"
                assert cells["window_end"] == "2026-09-05T00:00:00+00:00"
                assert cells["channels_attempted"] == "104"
                assert cells["channels_ok"] == "101"
                assert cells["channels_partial"] == "2"
                assert cells["channels_failed"] == "1"
                assert cells["coverage_complete"] == "no"
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def test_complete_coverage_reads_as_yes_in_the_csv():
    path = write_report(
        _scan(complete=True, channels_partial=0, channels_failed=0, channels_ok=104),
        _records(),
        filename="t8-complete.html",
    )
    try:
        rows = _read_csv(path.with_suffix(".csv"))
        cells = dict(zip(rows[0], rows[1]))
        assert cells["coverage_complete"] == "yes"
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def test_incomplete_coverage_is_stated_prominently_in_the_html():
    html = render_report(_scan(complete=False), _records())
    assert "Coverage is incomplete" in html


def test_complete_coverage_says_so_in_the_html():
    html = render_report(
        _scan(complete=True, channels_partial=0, channels_failed=0, channels_ok=104),
        _records(),
    )
    assert "Coverage is incomplete" not in html
    assert "every channel attempted was read in full" in html


def test_header_carries_run_metadata():
    html = render_report(_scan(), _records(), generated_at="2026-09-05T00:00:00+00:00")
    assert "2026-09-05T00:00:00+00:00" in html
    assert "2026-08-05T00:00:00+00:00" in html
    for label in ("Window start", "Window end", "attempted", "read in full", "could not be read"):
        assert label in html


def test_missing_metadata_renders_as_unknown_not_none():
    html = render_report(_scan(cutoff=None, scanned_text_channels=None), _records())
    assert ">None<" not in html
    assert "unknown" in html


# --- 5. no verdict language ----------------------------------------------

_VERDICT_WORDS = (
    "top",
    "best",
    "most active",
    "most shared",
    "recommend",
    "leaderboard",
    "winner",
    "rank",
    "score",
    "shortlist",
    "candidate",
    "worst",
    "popular",
    "trending",
)

_ALLOWED_DISCLAIMERS = (
    "does not rank, judge, or recommend anything",
    "not a recommendation",
)


def test_no_verdict_language_anywhere_in_the_rendered_output():
    html = render_report(_scan(), _records(), resolved=_resolved())
    lowered = html.lower()
    for phrase in _ALLOWED_DISCLAIMERS:
        assert phrase in lowered, f"expected disclaimer {phrase!r} on the page"
        lowered = lowered.replace(phrase, " ")
    for word in _VERDICT_WORDS:
        assert word not in lowered, f"verdict language {word!r} reached the rendered output"


def test_row_order_is_labelled_as_presentation():
    html = render_report(_scan(), _records())
    assert "presentation choice" in html
    assert "not a recommendation" in html


# --- 6. attachment URLs are marked EPHEMERAL -----------------------------


def _attachment_records() -> list[dict]:
    return [
        _record("https://example.com/stable"),
        _record(
            "https://cdn.example.com/attachments/1/2/file.png?ex=abc",
            timestamp="2026-09-02T09:00:00+00:00",
            jump_url="https://discord.com/channels/1/2/9",
            from_attachment=True,
        ),
    ]


def test_attachment_url_is_visibly_marked_as_expiring_in_the_html():
    html = render_report(_scan(), _attachment_records())
    assert "expiring" in html.lower()
    assert "14-22 hours" in html


def test_attachment_url_is_never_rendered_as_a_clickable_link():
    html = render_report(_scan(), _attachment_records())
    assert 'href="https://cdn.example.com/attachments/1/2/file.png?ex=abc"' not in html
    # ...but the address itself is still shown, and its jump link is clickable.
    assert "cdn.example.com/attachments/1/2/file.png" in html
    assert 'href="https://discord.com/channels/1/2/9"' in html


def test_the_jump_link_sits_beside_every_expiring_row():
    html = render_report(_scan(), _attachment_records())
    row_start = html.index("cdn.example.com/attachments")
    row_end = html.index("</tr>", row_start)
    row = html[row_start:row_end]
    assert "https://discord.com/channels/1/2/9" in row


def test_expiry_is_marked_in_both_csvs():
    path = write_report(_scan(), _attachment_records(), filename="t8-attach.html")
    try:
        flat = _read_csv(path.with_suffix(".csv"))
        header = flat[0]
        flags = {
            dict(zip(header, row))["url"]: dict(zip(header, row))["url_expires"] for row in flat[1:]
        }
        assert flags["https://example.com/stable"] == "stable"
        assert flags["https://cdn.example.com/attachments/1/2/file.png?ex=abc"] == "expiring"
        # The jump link travels with it in the flat CSV.
        jumps = {
            dict(zip(header, row))["url"]: dict(zip(header, row))["jump_url"] for row in flat[1:]
        }
        assert jumps["https://cdn.example.com/attachments/1/2/file.png?ex=abc"] == (
            "https://discord.com/channels/1/2/9"
        )

        summary = _read_csv(path.parent / "t8-attach-summary.csv")
        sheader = summary[0]
        sflags = {
            dict(zip(sheader, row))["url"]: dict(zip(sheader, row))["url_expires"]
            for row in summary[1:]
        }
        assert sflags["https://cdn.example.com/attachments/1/2/file.png?ex=abc"] == "expiring"
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def test_a_url_shared_both_as_attachment_and_plainly_is_summarised_as_expiring():
    url = "https://example.com/both"
    flat = flat_rows(
        [
            _record(url),
            _record(url, timestamp="2026-09-04T00:00:00+00:00", from_attachment=True),
        ]
    )
    summary = summary_rows(flat)
    assert summary[0]["url_expires"] == "expiring"


# --- author ids and resolution -------------------------------------------


def test_author_ids_render_when_no_resolution_is_supplied():
    html = render_report(_scan(), _records())
    assert "111" in html
    assert "Ada" not in html


def test_resolved_names_are_used_and_the_id_stays_visible():
    html = render_report(_scan(), _records(), resolved=_resolved())
    assert "Ada" in html
    assert "111" in html


def test_a_departed_author_keeps_the_link_and_shows_the_bare_id():
    """No placeholder: a link from someone who left keeps its id, not 'unknown'."""
    records = [_record(author_id="999")]
    html = render_report(_scan(), records, resolved={"111": {"display_name": "Ada"}})
    assert "999" in html
    assert "https://example.com/a" in html

    rows = flat_rows(records, resolved={"111": {"display_name": "Ada"}})
    assert rows[0]["author_label"] == "999"
    assert rows[0]["author_id"] == "999"


# --- empty input ---------------------------------------------------------


def test_empty_record_list_renders_a_readable_page():
    html = render_report(_scan(), [])
    assert "No link rows" in html
    assert "</html>" in html


def test_empty_record_list_still_writes_a_complete_artifact_set():
    path = write_report(_scan(), [], filename="t8-empty.html")
    try:
        names = sorted(p.name for p in path.parent.iterdir())
        assert names == ["t8-empty-summary.csv", "t8-empty.csv", "t8-empty.html"]
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def test_render_report_rejects_a_non_mapping_scan_result():
    with pytest.raises(CliError):
        render_report(["not", "a", "mapping"], [])


# --- 7. atomic artifact set ----------------------------------------------


def test_write_report_puts_its_whole_set_in_one_run_directory():
    path = write_report(_scan(), _records())
    try:
        run_dir = path.parent
        assert run_dir.parent == links_paths.links_reports_dir()
        assert run_dir.parent.parts[-2:] == ("reports", "links")
        names = sorted(p.name for p in run_dir.iterdir())
        assert names == [
            "links-report-summary.csv",
            "links-report.csv",
            "links-report.html",
        ]
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def test_the_run_directory_is_swapped_into_place_by_a_single_os_replace(monkeypatch):
    calls: list[tuple] = []
    real_replace = atomic_writeset.os.replace

    def _counting_replace(src, dst, *a, **kw):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(atomic_writeset.os, "replace", _counting_replace)

    path = write_report(_scan(), _records())
    try:
        assert len(calls) == 1, f"expected one atomic rename, got {calls}"
        assert calls[0][1] == str(path.parent)
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def test_a_killed_write_leaves_no_partial_set(monkeypatch):
    doomed = links_paths.new_run_id()

    def _boom(tmp_dir, dest_dir):
        raise RuntimeError("killed partway through the write")

    monkeypatch.setattr(atomic_writeset, "_swap_into_place", _boom)

    with pytest.raises(RuntimeError):
        write_report(_scan(), _records(), run_id=doomed)

    reports_dir = links_paths.links_reports_dir()
    assert not links_paths.links_run_dir(doomed).exists()
    leftovers = [p.name for p in reports_dir.iterdir() if doomed in p.name]
    assert leftovers == [], f"a failed run left artifacts behind: {leftovers}"


def test_parallel_writers_do_not_clobber_each_other():
    results: list[Path] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    start = threading.Barrier(6)

    def _run() -> None:
        try:
            start.wait(timeout=30)
            path = write_report(_scan(), _records())
        except BaseException as exc:  # pragma: no cover - only on failure
            with lock:
                errors.append(exc)
        else:
            with lock:
                results.append(path)

    threads = [threading.Thread(target=_run) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    try:
        assert not errors, errors
        assert len({path.parent for path in results}) == 6
        for path in results:
            assert path.read_text(encoding="utf-8").startswith("<!doctype html>")
            assert len(list(path.parent.iterdir())) == 3
    finally:
        for path in results:
            shutil.rmtree(path.parent, ignore_errors=True)


def test_every_written_filename_is_gitignored():
    path = write_report(_scan(), _records())
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
    for hostile in ("../escape", "sub/dir", "/etc", "..", ".", ""):
        with pytest.raises(CliError):
            write_report(_scan(), _records(), run_id=hostile)
