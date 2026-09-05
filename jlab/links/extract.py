"""URL extraction stage: flat, deduped link records from a scan (t6).

Consumes :func:`jlab.cli._discord.scan_window`'s output dicts — the same
shape :mod:`jlab.members.aggregate` consumes — and never touches Discord
itself. ``history()`` paging past the upstream 100-message cap lives in
exactly one place, ``jlab/cli/_discord.py``; this module only reads the
already-fetched, already-serialized message dicts it produces.

Where :mod:`jlab.members.aggregate` deliberately *discards* message
content (see its module docstring), this module does the opposite for
URLs: it is the one place in the pipeline allowed to look inside
``content`` and embed bodies, and it does so only to pull out URLs — no
surrounding text is retained anywhere in the output.

Four sources, deduped per message
----------------------------------

Each message can carry URLs in up to four places:

1. ``message["content"]`` — free text, scanned by regex.
2. ``message["attachments"][*]["url"]`` — Discord CDN links, taken
   verbatim (no regex needed; the field *is* a URL).
3. ``message["embeds"][*]["url"]`` — an embed's own URL, taken verbatim.
4. ``message["embeds"][*]["description"]`` and
   ``message["embeds"][*]["fields"][*]["value"]`` — embed *bodies*,
   scanned by the same regex as content.

A live probe (documented on ``_serialize_embed`` in
``jlab/cli/_discord.py``) found that Discord's auto-generated preview
embeds (``type=link``/``type=article``) carry a ``url`` that merely
duplicates one already present in ``message.content`` — so source (1) and
source (3) collide constantly and must be deduped. By contrast
``type=rich`` embeds (the kind bots like RSS/webhook integrations post)
have ``url is None`` and hide their real links inside the description and
field values — sources (3) and (1)/(2) never see those, only source (4)
does.

Dedup scope is a single message: the four sources are merged into one
set of URLs *per message*, so a URL that appears in both the content and
its auto-preview embed yields exactly one record. Dedup never crosses
messages — a URL shared twenty times across twenty messages is twenty
records (see "Output shape" below).

URL regex — what it handles and what it doesn't
------------------------------------------------

``_URL_RE`` matches ``http(s)://`` followed by any run of non-whitespace,
non-angle-bracket characters, then ``_strip_trailing_punctuation`` trims
off characters that are almost always prose/markdown punctuation rather
than part of the URL:

* **Trailing sentence punctuation** (``. , ! ? ; : ' "``) is stripped
  unconditionally, so ``see https://x.com/a.`` extracts
  ``https://x.com/a`` (the sentence-final period is dropped).
* **Angle-bracket wrapping** (``<https://x.com>``) is handled for free:
  the character class excludes ``<``/``>``, so the match simply stops at
  the closing ``>`` and the opening ``<`` was never part of the match.
* **Markdown links** (``[text](https://x.com/a)``) are handled by
  balance-counting: a trailing ``)``/``]``/``}`` is stripped as long as
  the candidate URL has more closing than opening brackets of that kind,
  so the markdown syntax's closing paren comes off while a URL whose
  *own* path legitimately contains balanced parens (e.g. a Wikipedia
  ``(disambiguation)`` link) is left alone.
* **Code fences / inline code** are *not* special-cased: a URL inside
  \\`\\`\\`fenced\\`\\`\\` text or \\`inline code\\` is still extracted like
  any other content URL. This is a deliberate simplification, not a
  guarantee that fenced code is excluded — documented here so a future
  reader doesn't assume otherwise.
* Query strings and fragments are captured as part of the URL (they are
  non-whitespace and not in the excluded bracket set), so
  ``https://x.com/a?b=1#c`` is not truncated at ``?`` or ``#``.
* What it does *not* handle: bare/schemeless mentions (``x.com/a`` with
  no ``http(s)://``), IDN/punycode edge cases beyond what plain regex
  scanning naturally handles, and URLs that legitimately end in one of
  the stripped punctuation characters (e.g. a path segment ending in a
  literal comma) — these are rare enough in Discord's wild text that
  perfect handling was not attempted.

Output shape — flat, one record per share
------------------------------------------

The canonical output of :func:`extract_links` is a **flat list**, one
record per (message, deduped-url) pair — never a nested per-URL summary.
If the same URL is shared in twenty different messages, that is twenty
records; a deduped, aggregated-by-URL view is a *later* stage's job, not
this one's. Each record is all-scalar:

.. code-block:: python

    {
        "url": str,
        "channel": {"id": str, "name": str} | {},
        "timestamp": str | None,        # message's created_at, ISO 8601
        "thread": {"id": str, "name": str} | {},  # {} when not in a thread
        "author_id": str,
        "jump_url": str | None,
        "from_attachment": bool,        # see "Ephemeral attachment URLs"
    }

No surrounding message text (``content``, embed ``title``/``description``,
field bodies) is retained in any record — only the URL string itself
survives extraction.

Ephemeral attachment URLs
--------------------------

Discord attachment URLs are signed and expire roughly 14-22 hours after
they are fetched (measured). They are not dropped — a link shared as a
file attachment is still a real share worth reporting — but every record
sourced (even partly) from ``attachments[].url`` carries
``from_attachment: True`` so a later renderer can flag it as a link that
will stop resolving well before most report-reading sessions are over.

Bots
----

Messages whose author is flagged as a bot are excluded by default, matching
:func:`jlab.cli._discord.scan_window`'s own ``exclude_bots`` default and
:mod:`jlab.members.aggregate`'s posture. Pass ``include_bots=True`` to
include them. The two runs over the same scan differ *only* by
bot-authored links: no human-authored link record appears, disappears, or
changes shape between the two.

Threads
-------

``message["thread"]`` is passed through verbatim: ``{}`` when the message
was not posted in/from a thread, or ``{"id": ..., "name": ...}`` when it
was. Two records with the same non-empty ``thread`` dict are grouped by
that reference; a record with an empty thread is never given a
placeholder id/name in its place.
"""

from __future__ import annotations

import re
from typing import Any

# Matches http(s):// followed by any run of non-whitespace, non-angle-bracket
# characters. Angle brackets are excluded from the match itself so
# ``<https://x.com>`` naturally stops at the closing ``>`` without needing a
# separate unwrap step.
_URL_RE = re.compile(r"https?://[^\s<>]+")

# Stripped unconditionally from the end of every match: near-universally
# sentence/prose punctuation, never part of a real URL's final character.
_TRAILING_PUNCTUATION = ".,!?;:'\""

# (open, close) bracket pairs checked for balance so a markdown link's
# closing punctuation comes off while a URL with legitimately balanced
# brackets in its own path (e.g. a Wikipedia "(disambiguation)" link) is
# left alone.
_BRACKET_PAIRS = (("(", ")"), ("[", "]"), ("{", "}"))


def _strip_trailing_punctuation(url: str) -> str:
    """Trim prose/markdown punctuation that regex matching swept in."""
    while url and url[-1] in _TRAILING_PUNCTUATION:
        url = url[:-1]
    changed = True
    while changed:
        changed = False
        for open_c, close_c in _BRACKET_PAIRS:
            while url.endswith(close_c) and url.count(open_c) < url.count(close_c):
                url = url[:-1]
                changed = True
    return url


def _extract_urls(text: str | None) -> list[str]:
    """Return every URL found in *text* by regex, in order of appearance."""
    if not text:
        return []
    found = []
    for match in _URL_RE.finditer(text):
        url = _strip_trailing_punctuation(match.group(0))
        if url:
            found.append(url)
    return found


def _record_url(found: dict[str, bool], url: str | None, *, from_attachment: bool = False) -> None:
    """Merge *url* into *found*, OR-ing in ``from_attachment`` on collision."""
    if not url:
        return
    if from_attachment or url not in found:
        found[url] = found.get(url, False) or from_attachment


def _collect_content_urls(message: dict, found: dict[str, bool]) -> None:
    for url in _extract_urls(message.get("content")):
        _record_url(found, url)


def _collect_attachment_urls(message: dict, found: dict[str, bool]) -> None:
    for attachment in message.get("attachments") or []:
        _record_url(found, attachment.get("url"), from_attachment=True)


def _collect_embed_urls(message: dict, found: dict[str, bool]) -> None:
    for embed in message.get("embeds") or []:
        _record_url(found, embed.get("url"))
        for url in _extract_urls(embed.get("description")):
            _record_url(found, url)
        for field in embed.get("fields") or []:
            for url in _extract_urls(field.get("value")):
                _record_url(found, url)


def _urls_in_message(message: dict) -> dict[str, bool]:
    """Return ``{url: from_attachment}`` for every deduped URL in *message*.

    Merges all four sources (content regex, attachment URLs, embed URLs,
    embed description/field regex) into one url-keyed map. A URL is marked
    ``from_attachment=True`` if *any* of its occurrences came from an
    attachment, even if the same URL also appears elsewhere in the message.
    """
    found: dict[str, bool] = {}
    _collect_content_urls(message, found)
    _collect_attachment_urls(message, found)
    _collect_embed_urls(message, found)
    return found


def _sort_key(record: dict[str, Any]) -> tuple:
    return (
        record.get("timestamp") or "",
        (record.get("channel") or {}).get("id") or "",
        record["url"],
    )


def _eligible_author_id(message: dict, include_bots: bool) -> Any:
    """Return the message's ``author.id``, or ``None`` if it should be skipped.

    Skipped when the author is a bot and ``include_bots`` is false, or when
    there is no resolvable id to attribute a share to at all.
    """
    author = message.get("author") or {}
    if not include_bots and author.get("bot"):
        return None
    return author.get("id")


def _records_for_message(message: dict, author_id: Any) -> list[dict]:
    """Build one output record per deduped URL found in *message*."""
    return [
        {
            "url": url,
            "channel": message.get("channel") or {},
            "timestamp": message.get("created_at"),
            "thread": message.get("thread") or {},
            "author_id": author_id,
            "jump_url": message.get("jump_url"),
            "from_attachment": from_attachment,
        }
        for url, from_attachment in _urls_in_message(message).items()
    ]


def extract_links(scan_result: dict, *, include_bots: bool = False) -> list[dict]:
    """Extract a flat list of link-share records from a ``scan_window()`` result.

    One record per (message, deduped-url) pair — see the module docstring
    for the exact shape, the four sources and how they're deduped, and the
    ``from_attachment`` ephemerality flag. Messages with no resolvable
    ``author.id`` are skipped, matching :func:`jlab.members.aggregate.aggregate`'s
    posture (there is nothing to attribute the share to otherwise).

    The returned list is sorted by ``(timestamp, channel id, url)`` purely
    for deterministic output — this carries no ranking/importance
    implication, matching the rest of this pipeline's stance.
    """
    records: list[dict] = []

    for channel in scan_result.get("channels", []):
        for message in channel.get("messages", []):
            author_id = _eligible_author_id(message, include_bots)
            if author_id is None:
                continue
            records.extend(_records_for_message(message, author_id))

    records.sort(key=_sort_key)
    return records
