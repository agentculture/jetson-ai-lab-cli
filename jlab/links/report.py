"""HTML + CSV report renderer for the links pipeline (t8).

Turns :func:`jlab.links.extract.extract_links`' flat share records — plus
the ``scan_window()`` result they came from, which is where the coverage
figures live — into three artifacts written as **one** atomic set:

* ``links-report.html`` — a self-contained page a maintainer opens off the
  local filesystem;
* ``links-report.csv`` — the **flat** table, one row per share;
* ``links-report-summary.csv`` — the **deduped per-address** table,
  derived in code from the flat table (never from a second pass over the
  extraction).

All three go through :func:`jlab.atomic_writeset.write_artifact_set` into
``data/reports/links/<run-id>/``, so a killed run leaves either the whole
set or nothing at all.

This module deliberately **mirrors** ``jlab/members/report.py`` — the
``_esc``/``_STYLE``/``_bar_chart`` patterns are copied, not imported. The
two report packages stay independent; a change to the members page must
never silently restyle the links page or vice versa. CSV writing, by
contrast, is genuinely shared: this module owns no CSV code of its own and
calls :func:`jlab.csv_export.write_csv` for both tables.

Five properties are load-bearing, and ``tests/test_links_report.py``
covers each one.

**Hostile URLs are inert.** The report's whole input is attacker-supplied
text: anyone in a public Discord channel can post any string, and this
page turns those strings into a document. Two separate defences apply to
every one of them. First, everything is escaped with
:func:`html.escape` (``quote=True``) in element *and* attribute context,
so a URL carrying ``"><script>`` renders as literal text. Second — and
this is the part escaping alone does *not* buy — a URL only becomes a
clickable ``href`` when :func:`_safe_href` confirms its scheme is
``http`` or ``https``. A ``javascript:``/``data:``/``vbscript:``/``file:``
URL, or a scheme-relative ``//host/path``, is still *shown* (dropping it
would hide a real share) but is rendered as inert text with no ``href``
at all. The jump link gets the same treatment even though the CLI
constructs it, rather than being trusted because of where it came from.

**Self-contained.** Nothing here imports outside the standard library:
the one diagram is hand-emitted inline SVG, there is no charting or
templating library, no ``<script>``, no ``<link>``, no ``<img>``, and no
CSS ``url(...)``. The page renders identically with the network
disabled. The only external references on it are the shared addresses
themselves, as anchors — and an anchor fetches nothing until a reader
clicks it.

**Attachment addresses are marked expiring.** A live probe measured that
Discord's attachment CDN URLs stop resolving roughly 14-22 hours after
they are fetched, independent of how old the message is. Presenting one
as a stable link would be a lie with a delayed fuse: it works while the
report is being written and is dead by the time anyone reads it. So every
record with ``from_attachment: True`` renders with a visible *expiring*
badge and is **never** made clickable — the clickable route back is its
jump link, which sits in the same row — and both CSVs carry the same fact
in a ``url_expires`` column.

**Coverage travels with the CSV.** The HTML has a metadata block, but a
CSV is the artifact most likely to be mailed or imported on its own, and
a partial sweep would otherwise present as a complete dataset. Rather
than a preamble block (which breaks naive parsers and does not survive
filtering, sorting, or splitting the file), the coverage figures are
**columns on every row**: window start/end, channels attempted / ok /
partial / failed, and whether coverage was complete. Any single row of
either CSV, read in isolation, states the window it covers and whether
anything was missed. The figures are read from the ``scan_window()``
result verbatim — never recomputed here.

**No verdict.** The page organises shares; it does not rank, judge, or
recommend anything. Nothing is scored or combined into a single number,
and the row order (alphabetical) is described on the page as a
presentation choice.

Name resolution is a later stage's job. This module accepts an OPTIONAL
``resolved`` mapping of ``author_id -> {"display_name": str, ...}``; with
none supplied it renders author ids alone. An author absent from the
mapping — someone who has since left the guild — keeps their row, their
link and their bare id. There is no ``unknown`` placeholder for a person:
dropping or blanking the row would delete a real share because of
something that happened to its poster afterwards.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jlab.atomic_writeset import write_artifact_set
from jlab.cli._errors import EXIT_USER_ERROR, CliError
from jlab.csv_export import write_csv
from jlab.links.paths import links_report_path, links_run_dir, new_run_id

__all__ = [
    "COVERAGE_COLUMNS",
    "FLAT_CSV_HEADER",
    "SUMMARY_CSV_HEADER",
    "flat_rows",
    "render_report",
    "summary_rows",
    "write_report",
]

# Schemes allowed to become a clickable href. Everything else renders as
# inert text -- see the module docstring.
_SAFE_SCHEMES = frozenset({"http", "https"})

_EXPIRING = "expiring"
_STABLE = "stable"

_UNKNOWN = "unknown"

# Coverage columns appended to BOTH CSV tables, so either one read alone
# states the window it covers and whether anything was missed.
COVERAGE_COLUMNS: tuple[str, ...] = (
    "window_start",
    "window_end",
    "channels_attempted",
    "channels_ok",
    "channels_partial",
    "channels_failed",
    "coverage_complete",
)

# The flat table: one row per share, every cell a scalar.
FLAT_CSV_HEADER: tuple[str, ...] = (
    "url",
    "url_expires",
    "shared_at",
    "channel_id",
    "channel_name",
    "thread_id",
    "thread_name",
    "author",
    "author_id",
    "jump_url",
) + COVERAGE_COLUMNS

# The deduped table, derived from the flat table by :func:`summary_rows`.
# ``channels_touched`` is a COUNT, not a joined list: a CSV cell is a
# scalar (see jlab/csv_export.py), never a delimiter-packed collection.
SUMMARY_CSV_HEADER: tuple[str, ...] = (
    "url",
    "url_expires",
    "shares",
    "first_shared_at",
    "last_shared_at",
    "channels_touched",
    "people_who_shared_it",
    "example_jump_url",
) + COVERAGE_COLUMNS

# Bar-chart geometry (SVG user units) -- copied from jlab/members/report.py.
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


def _safe_href(url: Any) -> str | None:
    """Return *url* when it may safely become an ``href``, else ``None``.

    Only ``http`` and ``https`` qualify. Anything else -- ``javascript:``,
    ``data:``, ``vbscript:``, ``file:``, a scheme-relative ``//host/path``,
    or a string carrying control characters that could smuggle a scheme
    past a naive check -- is refused, and the caller renders the address as
    inert text instead. Refusing is deliberate: escaping makes a hostile
    URL *display* safely, but only scheme filtering stops a click from
    executing it.
    """
    if not isinstance(url, str):
        return None
    candidate = url.strip()
    if not candidate:
        return None
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in candidate):
        return None
    try:
        scheme = urlsplit(candidate).scheme.lower()
    except ValueError:
        return None
    if scheme not in _SAFE_SCHEMES:
        return None
    return candidate


def _author_label(author_id: str, resolved: Mapping[str, Any] | None) -> str:
    """Display name for *author_id* when one was resolved, else the bare id.

    Never a placeholder: an author who has left the guild is absent from
    *resolved*, and their row keeps their id rather than reading
    ``unknown``.
    """
    if resolved:
        entry = resolved.get(author_id)
        if isinstance(entry, Mapping):
            name = entry.get("display_name")
        else:
            name = entry
        if name:
            return str(name)
    return author_id


def flat_rows(
    records: Iterable[Mapping[str, Any]],
    *,
    resolved: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Flatten extraction *records* into one all-scalar row per share.

    This is the canonical table: the HTML's first table, the flat CSV, and
    (via :func:`summary_rows`) the deduped CSV are all built from it. Row
    order is alphabetical by channel, then thread, then time, then address
    -- a presentation choice that puts shares from the same thread next to
    each other, carrying no ranking implication.
    """
    rows: list[dict[str, Any]] = []
    for record in records or []:
        channel = record.get("channel") or {}
        thread = record.get("thread") or {}
        author_id = str(record.get("author_id") or "")
        rows.append(
            {
                "url": str(record.get("url") or ""),
                "url_expires": _EXPIRING if record.get("from_attachment") else _STABLE,
                "shared_at": record.get("timestamp") or "",
                "channel_id": str(channel.get("id") or ""),
                "channel_name": str(channel.get("name") or ""),
                "thread_id": str(thread.get("id") or ""),
                "thread_name": str(thread.get("name") or ""),
                "author_label": _author_label(author_id, resolved),
                "author_id": author_id,
                "jump_url": record.get("jump_url") or "",
            }
        )
    rows.sort(
        key=lambda row: (
            row["channel_name"].casefold(),
            row["thread_name"].casefold(),
            row["shared_at"],
            row["url"],
        )
    )
    return rows


def summary_rows(flat: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Derive the deduped per-address table **from the flat table**.

    The only input is :func:`flat_rows`' output -- there is deliberately no
    second pass over the extraction records, so the two tables cannot
    disagree about what was shared. One row per distinct address, carrying
    how many times it was shared, when it was first and last seen, how many
    distinct channels it reached, how many distinct people shared it, and
    one jump link back into the conversation. An address counts as
    ``expiring`` if *any* of its shares was an attachment address.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for row in flat:
        url = row["url"]
        entry = grouped.get(url)
        if entry is None:
            entry = grouped[url] = {
                "url": url,
                "url_expires": _STABLE,
                "shares": 0,
                "first_shared_at": "",
                "last_shared_at": "",
                "_channels": set(),
                "_people": set(),
                "example_jump_url": "",
            }
        entry["shares"] += 1
        if row["url_expires"] == _EXPIRING:
            entry["url_expires"] = _EXPIRING
        stamp = row["shared_at"]
        if stamp:
            if not entry["first_shared_at"] or stamp < entry["first_shared_at"]:
                entry["first_shared_at"] = stamp
            if stamp > entry["last_shared_at"]:
                entry["last_shared_at"] = stamp
        if row["channel_id"] or row["channel_name"]:
            entry["_channels"].add((row["channel_id"], row["channel_name"]))
        if row["author_id"]:
            entry["_people"].add(row["author_id"])
        if not entry["example_jump_url"] and row["jump_url"]:
            entry["example_jump_url"] = row["jump_url"]

    rows: list[dict[str, Any]] = []
    for entry in grouped.values():
        entry["channels_touched"] = len(entry.pop("_channels"))
        entry["people_who_shared_it"] = len(entry.pop("_people"))
        rows.append(entry)
    # Presentation ordering only -- see the visible note the page carries.
    rows.sort(key=lambda row: (row["url"].casefold(), row["url"]))
    return rows


def _coverage_cells(scan_result: Mapping[str, Any], generated_at: str) -> tuple[Any, ...]:
    """The seven coverage cells appended to every CSV row.

    Read straight off the ``scan_window()`` result; nothing is recomputed
    here, so the CSV can never disagree with the scan about what it saw.
    """
    complete = scan_result.get("complete")
    if complete is None:
        complete_cell: str = _UNKNOWN
    else:
        complete_cell = "yes" if complete else "no"
    return (
        scan_result.get("cutoff") or "",
        generated_at,
        scan_result.get("scanned_text_channels"),
        scan_result.get("channels_ok"),
        scan_result.get("channels_partial"),
        scan_result.get("channels_failed"),
        complete_cell,
    )


def _bar_chart(title: str, rows: list[tuple[str, float, str]]) -> str:
    """Hand-emit one inline-SVG horizontal bar chart.

    Copied from ``jlab/members/report.py`` rather than imported: the two
    report packages stay independent. *rows* is ``(label, value,
    formatted_value)``; bars scale against the largest value present, and
    an all-zero chart renders zero-width bars rather than dividing by zero.
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
  margin: 0 auto; max-width: 68rem; padding: 2rem 1.25rem 4rem;
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
td.label { word-break: break-all; }
.addr { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.9em; }
.inert { color: #55554e; }
.badge {
  display: inline-block; font-size: 0.72em; font-weight: 700;
  letter-spacing: 0.04em; text-transform: uppercase;
  padding: 0.05rem 0.35rem; border-radius: 3px;
  background: #fbeee5; color: #8a3d10; border: 1px solid #e0b598;
}
.badge.plain { background: #f1f1ed; color: #55554e; border-color: #d5d5cc; }
.charts { display: grid; gap: 1.5rem; }
figure.chart { margin: 0; }
figcaption { font-weight: 600; margin-bottom: 0.35rem; }
svg.bars { max-width: 100%; height: auto; overflow: visible; }
svg.bars .bar { fill: #6f8fae; }
svg.bars text { font: 12px system-ui, sans-serif; fill: #1c1c1a; }
svg.bars .bar-value { fill: #55554e; }
footer { color: #55554e; font-size: 0.85rem; margin: 2rem 0 0; }
"""


def _address_cell(url: str, *, expiring: bool) -> str:
    """Render one shared address: anchor, or inert text when it must not click.

    Three outcomes, and only the first is clickable:

    * a plain ``http``/``https`` address becomes an anchor;
    * an **attachment** address never becomes an anchor, however valid its
      scheme, because it will have stopped resolving by the time most
      readers open the page -- it gets an *expiring* badge, and the row's
      jump link is the working way back;
    * anything whose scheme is not ``http``/``https`` is shown as inert
      text with a badge saying so, so a reader can see what was posted
      without the page offering to execute it.
    """
    text = _esc(url)
    if expiring:
        return f'<span class="addr">{text}</span> <span class="badge">expiring</span>'
    href = _safe_href(url)
    if href is None:
        return (
            f'<span class="addr inert">{text}</span> '
            f'<span class="badge plain">not a web address</span>'
        )
    return f'<a class="addr" href="{_esc(href)}" rel="noreferrer noopener">{text}</a>'


def _row_address(row: Mapping[str, Any]) -> str:
    """Render one table row's address cell from its ``url_expires`` flag."""
    return _address_cell(row["url"], expiring=row["url_expires"] == _EXPIRING)


def _jump_cell(jump_url: str) -> str:
    """Render the jump link back to the message the address appeared in."""
    if not jump_url:
        return ""
    href = _safe_href(jump_url)
    if href is None:
        return f'<span class="addr inert">{_esc(jump_url)}</span>'
    return f'<a href="{_esc(href)}" rel="noreferrer noopener">open the message</a>'


def _metadata_block(
    scan_result: Mapping[str, Any],
    generated_at: str,
    share_count: int,
    address_count: int,
) -> str:
    exclude_bots = scan_result.get("exclude_bots")
    if exclude_bots is None:
        bots = _UNKNOWN
    else:
        bots = "excluded" if exclude_bots else "included"
    items: list[tuple[str, str]] = [
        ("Scan finished", _text(generated_at)),
        ("Window start (oldest message considered)", _text(scan_result.get("cutoff"))),
        ("Window end (scan cutoff)", _text(generated_at)),
        ("Window length", f"{_num(scan_result.get('since_days'))} days"),
        ("Discord guild id", _text(scan_result.get("guild_id"))),
        ("Public text channels attempted", _num(scan_result.get("scanned_text_channels"))),
        ("Channels read in full", _num(scan_result.get("channels_ok"))),
        ("Channels partially read", _num(scan_result.get("channels_partial"))),
        ("Channels that could not be read", _num(scan_result.get("channels_failed"))),
        ("Messages considered", _num(scan_result.get("message_count"))),
        ("Bot and webhook authors", _esc(bots)),
        ("Shares found in the window", _num(share_count)),
        ("Distinct addresses among them", _num(address_count)),
    ]
    body = "\n".join(f"<dt>{_esc(term)}</dt><dd>{value}</dd>" for term, value in items)
    return f'<dl class="meta">\n{body}\n</dl>'


def _coverage_note(scan_result: Mapping[str, Any]) -> str:
    if scan_result.get("complete") is True:
        return (
            '<p class="note">Coverage of the attempted channels is complete: '
            "every channel attempted was read in full for the whole window.</p>"
        )
    partial = _num(scan_result.get("channels_partial"))
    failed = _num(scan_result.get("channels_failed"))
    return (
        '<p class="note warn"><strong>Coverage is incomplete.</strong> '
        f"{partial} channel(s) were only partially read and {failed} channel(s) "
        "could not be read at all, so the shares below understate what was "
        "posted in those channels. A channel that could not be read is not the "
        "same as a channel where nothing was shared; re-run the scan if you need "
        "the gap closed.</p>"
    )


def _flat_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            "<p>No link rows: no message inside this window carried an address, "
            "or every message that did was filtered out before this stage.</p>"
        )
    head = (
        "<tr>"
        '<th class="label">Shared address</th>'
        '<th class="label">Shared at</th>'
        '<th class="label">Channel</th>'
        '<th class="label">Thread</th>'
        '<th class="label">Shared by</th>'
        '<th class="id">Discord id</th>'
        '<th class="label">Back to the message</th>'
        "</tr>"
    )
    body = "\n".join(
        "<tr>"
        f'<td class="label">{_row_address(row)}</td>'
        f'<td class="label">{_esc(row["shared_at"])}</td>'
        f'<td class="label">{_esc(row["channel_name"])}</td>'
        f'<td class="label">{_esc(row["thread_name"])}</td>'
        f'<td class="label">{_esc(row["author_label"])}</td>'
        f'<td class="id">{_esc(row["author_id"])}</td>'
        f'<td class="label">{_jump_cell(row["jump_url"])}</td>'
        "</tr>"
        for row in rows
    )
    return f"<table>\n<thead>{head}</thead>\n<tbody>\n{body}\n</tbody>\n</table>"


def _summary_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p>No addresses to summarise.</p>"
    head = (
        "<tr>"
        '<th class="label">Address</th>'
        "<th>Shares</th>"
        '<th class="label">First seen</th>'
        '<th class="label">Last seen</th>'
        "<th>Channels touched</th>"
        "<th>People who shared it</th>"
        '<th class="label">Back to a message</th>'
        "</tr>"
    )
    body = "\n".join(
        "<tr>"
        f'<td class="label">{_row_address(row)}</td>'
        f'<td>{row["shares"]:,}</td>'
        f'<td class="label">{_esc(row["first_shared_at"])}</td>'
        f'<td class="label">{_esc(row["last_shared_at"])}</td>'
        f'<td>{row["channels_touched"]:,}</td>'
        f'<td>{row["people_who_shared_it"]:,}</td>'
        f'<td class="label">{_jump_cell(row["example_jump_url"])}</td>'
        "</tr>"
        for row in rows
    )
    return f"<table>\n<thead>{head}</thead>\n<tbody>\n{body}\n</tbody>\n</table>"


def _chart(rows: list[dict[str, Any]]) -> str:
    """One diagram: how many shares each channel contributed."""
    if not rows:
        return ""
    counts: dict[str, int] = {}
    for row in rows:
        label = row["channel_name"] or row["channel_id"] or ""
        counts[label] = counts.get(label, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: item[0].casefold())
    chart = _bar_chart(
        "Shares per channel",
        [(label, float(count), f"{count:,}") for label, count in ordered],
    )
    return '<div class="charts">\n' + chart + "\n</div>"


def render_report(
    scan_result: Mapping[str, Any],
    records: Iterable[Mapping[str, Any]],
    *,
    resolved: Mapping[str, Any] | None = None,
    generated_at: str | None = None,
) -> str:
    """Render one self-contained HTML page for *records* and return it.

    *scan_result* is the ``scan_window()`` dict the records were extracted
    from; its coverage fields are read verbatim for the metadata block.
    *resolved* is the optional ``author_id -> {"display_name": ...}``
    mapping produced by the separate resolution stage; without it, rows are
    labelled by author id alone, and an author missing from it keeps their
    id rather than gaining a placeholder. *generated_at* defaults to now,
    in UTC.
    """
    if not isinstance(scan_result, Mapping):
        raise CliError(
            EXIT_USER_ERROR,
            "links report input must be a scan_window() result mapping",
            "pass the dict returned by jlab.cli._discord.scan_window()",
        )

    stamp = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    flat = flat_rows(records, resolved=resolved)
    summary = summary_rows(flat)

    title = "Jetson AI Lab Discord — addresses shared in public channels"
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
        '<p class="note">This page organises what was shared so a person can read '
        "it. It does not rank, judge, or recommend anything, and it combines "
        "nothing into a single number: the two tables below are the same shares "
        "seen two ways, one row per share and one row per address.</p>",
        "<h2>What this covers</h2>",
        "<p>These shares come from the guild's <strong>public text channels "
        "only</strong> — channels the guild's <code>@everyone</code> role can view. "
        "Private and role-gated channels contribute nothing. Voice channels are "
        "deliberately out of scope, and forum channels and their threads are not "
        "covered either, so this is not whole-guild coverage.</p>",
        _coverage_note(scan_result),
        '<p class="note warn"><strong>Addresses badged '
        "<em>expiring</em> are file attachments, not stable web addresses.</strong> "
        "Discord signs them, and a measurement found they expire roughly "
        "14-22 hours after they were fetched — independent of how old the message "
        "is. They are shown because the share was real, but they are deliberately "
        "not clickable: use the jump link in the same row to open the message and "
        "get a fresh address.</p>",
        "<h2>Run metadata</h2>",
        _metadata_block(scan_result, stamp, len(flat), len(summary)),
        "<h2>Every share</h2>",
        "<p>One row per share: the same address posted twice appears twice. Rows "
        "are ordered by channel, then thread, then time, so shares from one "
        "conversation sit together. That order is a presentation choice — it is "
        "not a recommendation and says nothing about the rows themselves.</p>",
        _flat_table(flat),
        "<h2>One row per address</h2>",
        "<p>The same shares, deduplicated by address, with how many times each was "
        "shared, when it was first and last seen, how many channels it reached and "
        "how many people posted it. Rows are ordered alphabetically by address.</p>",
        _summary_table(summary),
        "<h2>The same shares as a diagram</h2>",
        "<p>How many shares each channel contributed, with channels in "
        "alphabetical order. Bar lengths are relative to the largest value in the "
        "diagram.</p>",
        _chart(flat),
        "<footer>Generated by jetson-ai-lab-cli from the Jetson AI Lab Discord "
        f"public message history. Scan finished {_esc(stamp)}. No message text is "
        "kept — only the addresses themselves, the ids and the timestamps reach "
        "this page. This file carries person-level data about real community "
        "members: it is gitignored and not for redistribution.</footer>",
        "</body>",
        "</html>",
        "",
    ]
    return "\n".join(parts)


def _flat_csv_cells(rows: list[dict[str, Any]], coverage: tuple[Any, ...]) -> list[tuple[Any, ...]]:
    return [
        (
            row["url"],
            row["url_expires"],
            row["shared_at"],
            row["channel_id"],
            row["channel_name"],
            row["thread_id"],
            row["thread_name"],
            row["author_label"],
            row["author_id"],
            row["jump_url"],
        )
        + coverage
        for row in rows
    ]


def _summary_csv_cells(
    rows: list[dict[str, Any]], coverage: tuple[Any, ...]
) -> list[tuple[Any, ...]]:
    return [
        (
            row["url"],
            row["url_expires"],
            row["shares"],
            row["first_shared_at"],
            row["last_shared_at"],
            row["channels_touched"],
            row["people_who_shared_it"],
            row["example_jump_url"],
        )
        + coverage
        for row in rows
    ]


def _render_csv_text(name: str, header: tuple[str, ...], cells: list[tuple[Any, ...]]) -> str:
    """Produce CSV text **entirely** via :func:`jlab.csv_export.write_csv`.

    This module owns no CSV code of its own: no ``csv`` import, no manual
    joining, no hand-rolled escaping. Formula-injection escaping and
    correct quoting live in exactly one place. ``write_artifact_set`` wants
    file *content* while ``write_csv`` writes to a *path*, so the shared
    writer runs into a throwaway temp file and the bytes it produced are
    read back. The temp file lives outside the report directory and is
    discarded either way, so nothing partial can appear where a reader
    would look for a report.
    """
    with tempfile.TemporaryDirectory(prefix="jlab-links-csv-") as staging:
        path = write_csv(Path(staging) / name, header, cells)
        return path.read_text(encoding="utf-8")


def write_report(
    scan_result: Mapping[str, Any],
    records: Iterable[Mapping[str, Any]],
    *,
    resolved: Mapping[str, Any] | None = None,
    generated_at: str | None = None,
    filename: str | None = None,
    run_id: str | None = None,
) -> Path:
    """Render *records* and write this run's whole artifact set atomically.

    One run writes **three** artifacts into a directory of its own —
    ``data/reports/links/<run-id>/`` — through
    :func:`jlab.atomic_writeset.write_artifact_set`, which stages every
    file in a temporary directory and only then swaps the finished set into
    place. Killing the write partway therefore leaves either the complete
    set or no new artifacts at all, never a CSV beside a missing or stale
    HTML. Because the destination belongs to this run alone, that swap is a
    single ``os.replace`` and no sibling run's directory is ever touched.

    *filename* names the HTML artifact; the flat CSV takes the same stem
    and the summary CSV takes the stem plus ``-summary``. Both it and the
    run id are validated by :func:`jlab.links.paths.links_report_path`,
    which refuses anything that could escape this checkout's gitignored
    output directory.

    Returns the path of the **HTML file inside the run directory**.
    """
    stamp = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    html = render_report(scan_result, records, resolved=resolved, generated_at=stamp)

    flat = flat_rows(records, resolved=resolved)
    summary = summary_rows(flat)
    coverage = _coverage_cells(scan_result, stamp)

    run = new_run_id() if run_id is None else run_id
    html_path = links_report_path(run) if filename is None else links_report_path(run, filename)
    flat_name = html_path.with_suffix(".csv").name
    summary_name = f"{html_path.stem}-summary.csv"

    write_artifact_set(
        links_run_dir(run),
        {
            html_path.name: html,
            flat_name: _render_csv_text(
                flat_name, FLAT_CSV_HEADER, _flat_csv_cells(flat, coverage)
            ),
            summary_name: _render_csv_text(
                summary_name, SUMMARY_CSV_HEADER, _summary_csv_cells(summary, coverage)
            ),
        },
    )
    return html_path
