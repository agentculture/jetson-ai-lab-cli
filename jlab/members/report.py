"""HTML report renderer for the members pipeline (t5).

Turns :func:`jlab.members.aggregate.aggregate`'s id-only statistics into a
single self-contained HTML page a Channel Maintainer can open in Chrome
straight off the local filesystem.

Four properties are load-bearing and are covered by
``tests/test_members_report.py``:

* **Zero runtime dependencies.** Nothing here imports anything outside the
  standard library. The diagrams are *hand-emitted inline SVG* — no Python
  charting library, no templating engine, and deliberately no CDN chart
  script either: an inline-SVG page renders identically with the network
  disabled, which is strictly better than the CDN option the spec also
  allowed. The page contains no ``<script>``, no ``<link>``, and no
  external URL at all.
* **Escaping.** Every user-derived string — display names above all, but
  also author ids and the guild id — goes through :func:`html.escape` with
  ``quote=True`` before it reaches the document, in element *and* attribute
  context. A member whose display name is ``<script>alert(1)</script>``
  renders as literal text and executes nothing.
* **Honest coverage.** The header states when the scan finished, the window
  it covers (start and end), and how many public text channels were
  attempted / read in full / partially read / could not be read. When the
  aggregate is not ``complete`` the page says so prominently, so a reader
  months later can tell a quiet month from a broken run without re-running
  anything. It also states on its face that it covers *public text*
  channels only — voice is deliberately out of scope and forums/threads are
  not covered — so it is never mistaken for whole-guild coverage.
* **No verdict.** Nothing on the page ranks, scores, or labels a person.
  The four signals are rendered side by side, never combined into a single
  number, and the row order (alphabetical by the label in the first column)
  is described in visible text as a presentation choice rather than a
  recommendation. Verdict vocabulary is kept out of every heading, column
  header and diagram title; the only place such a word appears is the
  page's own disclaimer denying a verdict.

Name resolution is a *separate* stage (t4, ``jlab/members/resolve.py``).
This module accepts an OPTIONAL ``resolved`` mapping of
``author_id -> {"display_name": str, ...}`` and an optional
``excluded_count``; with neither supplied it renders author ids alone, so
the two stages compose without depending on each other.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from jlab.cli._errors import EXIT_USER_ERROR, CliError
from jlab.members.paths import members_report_path

__all__ = ["render_report", "write_report"]

_UNKNOWN = "unknown"

# Bar-chart geometry (SVG user units).
_LABEL_WIDTH = 210
_BAR_WIDTH = 300
_ROW_HEIGHT = 22
_VALUE_GUTTER = 80
_CHART_WIDTH = _LABEL_WIDTH + _BAR_WIDTH + _VALUE_GUTTER


def _esc(value: Any) -> str:
    """HTML-escape *value* for element **and** attribute context."""
    return escape(str(value), quote=True)


def _num(value: Any) -> str:
    """Render an integer metadata value, or ``unknown`` when absent."""
    if value is None:
        return _UNKNOWN
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    return _esc(value)


def _text(value: Any) -> str:
    """Render a string metadata value, or ``unknown`` when absent."""
    if value is None or value == "":
        return _UNKNOWN
    return _esc(value)


def _member_label(row: Mapping[str, Any], resolved: Mapping[str, Any] | None) -> str:
    """Human-facing label for a member row: display name if one was resolved.

    Falls back to the author id, so a report rendered without the
    resolution stage is still readable.
    """
    author_id = str(row.get("author_id", ""))
    if resolved:
        entry = resolved.get(author_id) or {}
        if isinstance(entry, Mapping):
            name = entry.get("display_name")
        else:  # a bare string mapping is accepted too
            name = entry
        if name:
            return str(name)
    return author_id


def _bar_chart(title: str, rows: list[tuple[str, float, str]]) -> str:
    """Hand-emit one inline-SVG horizontal bar chart.

    *rows* is ``(label, value, formatted_value)``. Bars are scaled against
    the largest value present; a chart whose values are all zero renders
    zero-width bars rather than dividing by zero. Row order is the caller's
    (alphabetical) order, identical across all four charts, so a reader
    compares the same member across signals by looking down the same line.
    """
    if not rows:
        return ""
    height = _ROW_HEIGHT * len(rows) + 12
    largest = max((value for _, value, _ in rows), default=0)
    parts: list[str] = [
        f'<figure class="chart">\n<figcaption>{_esc(title)}</figcaption>',
        f'<svg class="bars" viewBox="0 0 {_CHART_WIDTH} {height}" '
        f'width="{_CHART_WIDTH}" height="{height}" role="img" '
        f'aria-label="{_esc(title)}">',
    ]
    for index, (label, value, formatted) in enumerate(rows):
        baseline = _ROW_HEIGHT * index + 16
        width = 0 if largest <= 0 else round(value / largest * _BAR_WIDTH)
        parts.append(
            f'<g aria-label="{_esc(label)}">'
            f'<text class="bar-label" x="0" y="{baseline}">{_esc(label)}</text>'
            f'<rect class="bar" x="{_LABEL_WIDTH}" y="{baseline - 11}" '
            f'width="{width}" height="14"></rect>'
            f'<text class="bar-value" x="{_LABEL_WIDTH + width + 6}" '
            f'y="{baseline}">{_esc(formatted)}</text>'
            f"</g>"
        )
    parts.append("</svg>\n</figure>")
    return "\n".join(parts)


_STYLE = """
:root { color-scheme: light dark; }
body {
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  margin: 0 auto; max-width: 60rem; padding: 2rem 1.25rem 4rem;
  background: #fbfbfa; color: #1c1c1a;
}
h1 { font-size: 1.55rem; margin-bottom: 0.25rem; }
h2 { font-size: 1.15rem; margin-bottom: 0.5rem; }
p, dl, table, figure { margin-bottom: 1rem; }
.note {
  border-left: 4px solid #7a7a72; background: #f1f1ed;
  padding: 0.75rem 1rem; border-radius: 4px;
}
.warn { border-left-color: #b4531b; background: #fbeee5; }
dl.meta { display: grid; grid-template-columns: max-content 1fr; gap: 0.2rem 1rem; }
dl.meta dt { color: #55554e; }
dl.meta dd { margin: 0; font-variant-numeric: tabular-nums; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { border-bottom: 1px solid #dcdcd4; padding: 0.35rem 0.5rem; text-align: right; }
th { font-weight: 600; vertical-align: bottom; }
th.label, td.label, th.id, td.id { text-align: left; }
td.id { color: #55554e; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.charts { display: grid; gap: 1.5rem; }
figure.chart { margin: 0; }
figcaption { font-weight: 600; margin-bottom: 0.35rem; }
svg.bars { max-width: 100%; height: auto; overflow: visible; }
svg.bars .bar { fill: #6f8fae; }
svg.bars text { font: 12px system-ui, sans-serif; fill: #1c1c1a; }
svg.bars .bar-value { fill: #55554e; }
footer { color: #55554e; font-size: 0.85rem; margin: 2rem 0 0; }
"""


def _metadata_block(
    aggregate: Mapping[str, Any],
    generated_at: str,
    member_rows: int,
    excluded_count: int | None,
) -> str:
    items: list[tuple[str, str]] = [
        ("Scan finished", _text(generated_at)),
        ("Window start (oldest message considered)", _text(aggregate.get("cutoff"))),
        ("Window end (scan cutoff)", _text(generated_at)),
        ("Window length", f"{_num(aggregate.get('since_days'))} days"),
        ("Discord guild id", _text(aggregate.get("guild_id"))),
        (
            "Public text channels attempted",
            _num(aggregate.get("scanned_text_channels")),
        ),
        ("Channels read in full", _num(aggregate.get("channels_ok"))),
        ("Channels partially read", _num(aggregate.get("channels_partial"))),
        ("Channels that could not be read", _num(aggregate.get("channels_failed"))),
        ("Messages counted", _num(aggregate.get("message_count"))),
        ("Members with at least one message in the window", _num(member_rows)),
    ]
    if excluded_count is not None:
        items.append(
            (
                "Authors omitted because they are no longer in the guild",
                _num(excluded_count),
            )
        )
    body = "\n".join(f"<dt>{_esc(term)}</dt><dd>{value}</dd>" for term, value in items)
    return f'<dl class="meta">\n{body}\n</dl>'


def _coverage_note(aggregate: Mapping[str, Any]) -> str:
    if aggregate.get("complete") is True:
        return (
            '<p class="note">Coverage of the attempted channels is complete: '
            "every channel attempted was read in full for the whole window.</p>"
        )
    partial = _num(aggregate.get("channels_partial"))
    failed = _num(aggregate.get("channels_failed"))
    return (
        '<p class="note warn"><strong>Coverage is incomplete.</strong> '
        f"{partial} channel(s) were only partially read and {failed} channel(s) "
        "could not be read at all, so the numbers below understate activity in "
        "those channels. A channel that could not be read is not the same as a "
        "channel with no activity; re-run the scan if you need the gap closed.</p>"
    )


def _table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p>No member rows: no message by a resolvable author fell inside this window.</p>"
    head = (
        "<tr>"
        '<th class="label">Member</th>'
        '<th class="id">Discord id</th>'
        "<th>Messages</th>"
        "<th>Distinct channels</th>"
        "<th>Messages ending in a question</th>"
        "<th>Characters written</th>"
        "<th>Average characters per message</th>"
        "</tr>"
    )
    body = "\n".join(
        "<tr>"
        f'<td class="label">{_esc(row["label"])}</td>'
        f'<td class="id">{_esc(row["author_id"])}</td>'
        f'<td>{row["messages"]:,}</td>'
        f'<td>{row["channels"]:,}</td>'
        f'<td>{row["questions"]:,}</td>'
        f'<td>{row["characters"]:,}</td>'
        f'<td>{row["avg_characters"]:.1f}</td>'
        "</tr>"
        for row in rows
    )
    return f"<table>\n<thead>{head}</thead>\n<tbody>\n{body}\n</tbody>\n</table>"


def _charts(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    charts = [
        _bar_chart(
            "Messages per member",
            [(r["label"], r["messages"], f'{r["messages"]:,}') for r in rows],
        ),
        _bar_chart(
            "Distinct channels per member",
            [(r["label"], r["channels"], f'{r["channels"]:,}') for r in rows],
        ),
        _bar_chart(
            "Messages ending in a question per member",
            [(r["label"], r["questions"], f'{r["questions"]:,}') for r in rows],
        ),
        _bar_chart(
            "Characters written per member",
            [(r["label"], r["characters"], f'{r["characters"]:,}') for r in rows],
        ),
    ]
    return '<div class="charts">\n' + "\n".join(charts) + "\n</div>"


def _rows(
    aggregate: Mapping[str, Any],
    resolved: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for member in aggregate.get("members") or []:
        substance = member.get("substance") or {}
        rows.append(
            {
                "label": _member_label(member, resolved),
                "author_id": str(member.get("author_id", "")),
                "messages": int(member.get("message_count") or 0),
                "channels": int(member.get("distinct_channels") or 0),
                "questions": int(member.get("question_starts") or 0),
                "characters": int(substance.get("total_length") or 0),
                "avg_characters": float(substance.get("avg_length") or 0.0),
            }
        )
    # Presentation ordering only — see the visible note the page carries.
    rows.sort(key=lambda row: (row["label"].casefold(), row["author_id"]))
    return rows


def render_report(
    aggregate: Mapping[str, Any],
    *,
    resolved: Mapping[str, Any] | None = None,
    excluded_count: int | None = None,
    generated_at: str | None = None,
) -> str:
    """Render *aggregate* as one self-contained HTML page and return it.

    ``resolved`` is the optional ``author_id -> {"display_name": ...}``
    mapping produced by the separate resolution stage; without it, member
    rows are labelled by author id alone. ``excluded_count``, when given,
    is stated in the header as the number of authors omitted because they
    are no longer in the guild. ``generated_at`` defaults to now, in UTC.
    """
    if not isinstance(aggregate, Mapping):
        raise CliError(
            EXIT_USER_ERROR,
            "members report input must be an aggregate mapping",
            "pass the dict returned by jlab.members.aggregate.aggregate()",
        )

    stamp = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = _rows(aggregate, resolved)

    title = "Jetson AI Lab Discord — public text channel participation statistics"
    parts = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_esc(title)}</title>",
        f"<style>{_STYLE}</style>",
        "</head>",
        "<body>",
        f"<h1>{_esc(title)}</h1>",
        '<p class="note">This page organises statistics so a person can read them. '
        "It does not rank, judge, or recommend anyone, and it combines nothing into "
        "a single number: the four signals are shown side by side so you can compare "
        "them yourself and decide what matters for what you are doing.</p>",
        "<h2>What this covers</h2>",
        "<p>These statistics come from the guild's <strong>public text channels "
        "only</strong> — channels the guild's <code>@everyone</code> role can view. "
        "Private and role-gated channels contribute nothing. The guild's voice "
        "channels are deliberately out of scope, and forum channels and their "
        "threads are not covered either, so this is not whole-guild coverage: "
        "someone who takes part mainly by voice or in a forum will look quiet here "
        "or not appear at all.</p>",
        _coverage_note(aggregate),
        "<h2>Run metadata</h2>",
        _metadata_block(aggregate, stamp, len(rows), excluded_count),
        "<h2>Per-member signals</h2>",
        "<p>Rows are ordered alphabetically by the label in the first column. That "
        "order is a presentation choice, chosen to make a given member easy to look "
        "up — it is not a recommendation, and it carries no meaning about the "
        "numbers in the row. The columns are independent measurements, not steps "
        "on a scale: messages sent, how many distinct channels those messages were "
        "spread across, how many messages ended in a question mark, and how many "
        "characters were written.</p>",
        _table(rows),
        "<h2>The same signals as diagrams</h2>",
        "<p>Each diagram below shows one signal, with members in the same "
        "alphabetical order as the table, so the same line across the four "
        "diagrams is the same person. Bar lengths are relative to the largest "
        "value within that one diagram, so lengths are not comparable between "
        "diagrams.</p>",
        _charts(rows),
        "<footer>Generated by jetson-ai-lab-cli from the Jetson AI Lab Discord "
        f"public message history. Scan finished {_esc(stamp)}. Message text is "
        "never stored or quoted — only counts, lengths and ids reach this page. "
        "This file contains person-level data about real community members: it is "
        "gitignored and not for redistribution.</footer>",
        "</body>",
        "</html>",
        "",
    ]
    return "\n".join(parts)


def write_report(
    aggregate: Mapping[str, Any],
    *,
    resolved: Mapping[str, Any] | None = None,
    excluded_count: int | None = None,
    generated_at: str | None = None,
    filename: str | None = None,
) -> Path:
    """Render *aggregate* and write it to the repo-anchored report path.

    The path comes from :func:`jlab.members.paths.members_report_path`,
    which refuses to write outside this checkout's gitignored output
    directory. Returns the path written.
    """
    html = render_report(
        aggregate,
        resolved=resolved,
        excluded_count=excluded_count,
        generated_at=generated_at,
    )
    path = members_report_path() if filename is None else members_report_path(filename)
    path.write_text(html, encoding="utf-8")
    return path
