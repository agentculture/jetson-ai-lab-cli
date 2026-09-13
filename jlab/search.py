"""``jlab discord search`` — regex search over the cached corpus, execution-bounded.

This is the read-only query layer on top of :mod:`jlab.cache` and
:mod:`jlab.coverage`: it never contacts Discord (see the module docstring
note below) and answers only from what :func:`jlab.fetch.fetch_channel` has
already written. A suspect hit found here is meant to be followed by a
narrower ``discord read`` or a deeper ``discord fetch``, not by this module
reaching for the network itself.

**Cache-served only (o7-equivalent for search).** Nothing in this module
imports :mod:`jlab.cli._discord` or opens a Discord session. It reads
:func:`jlab.cache.iter_messages` (decrypted, oldest-first) and
:func:`jlab.coverage.describe` (gap arithmetic) and nothing else — so a test
that swaps the Discord seam for one that raises on any call still gets a
correct answer from this module.

**Compile-time validation (o1-equivalent).** :func:`compile_pattern` is
called first, before any Mongo access, so a malformed ``--grep`` fails with a
code-1 :class:`~jlab.cli._errors.CliError` before the cache is ever opened.

**Execution bound (o13).** Python's ``re`` cannot be interrupted once a match
is running — a catastrophic-backtracking pattern (``(a+)+$`` against a long
run of ``a``s) can hang the process indefinitely with no way to reclaim it in
the same interpreter. So matching does not happen in this process at all: it
runs in a forked child (:func:`_bounded_search`), streaming each match back
over a :class:`multiprocessing.Queue` as it is found, under an overall
wall-clock deadline (``--timeout``, declared, with a sane default,
:data:`DEFAULT_TIMEOUT_SECONDS`). If the deadline fires before the child
reports done, the child is killed and the result carries ``bounded: True`` —
**never** an empty result that reads as "no matches": whatever matched before
the cutoff is still returned, and the diagnostic/JSON field says explicitly
that the bound, not an exhausted corpus, is why the scan stopped.

**Gap-reporting (o18).** A window is resolved from ``--since``/``--until``
(both or neither — a lone bound is a code-1 user error) via
:func:`resolve_window`; with neither given, the window defaults to the whole
history :mod:`jlab.fetch` would drain (Discord's epoch through now), so
"search everything" still names a concrete window rather than an implicit
"whatever happens to be cached". :func:`jlab.coverage.describe` then reports
which parts of that window are actually covered — a channel with **no**
coverage record at all comes back ``complete: False`` with the whole window
listed under ``uncovered``, never as a silent zero-match answer.
"""

from __future__ import annotations

import datetime as dt
import multiprocessing as mp
import queue as _queue_mod
import re
import time
from typing import Any, Sequence

from jlab import cache as _cache
from jlab import coverage as _coverage
from jlab.cli._errors import EXIT_USER_ERROR, CliError
from jlab.fetch import DISCORD_EPOCH

UTC = dt.timezone.utc

#: Wall-clock budget, in seconds, for one search's regex matching (o13). A
#: pathological pattern is killed at this bound rather than allowed to hang;
#: tests pass a small override so the suite stays fast.
DEFAULT_TIMEOUT_SECONDS = 5.0

#: Reasons the matching worker can report finishing (as opposed to being
#: killed at the wall-clock bound, which never reaches ``_worker``'s return).
REASON_EXHAUSTED = "exhausted"
REASON_MAX_MATCHES = "max-matches"


def compile_pattern(pattern: str) -> "re.Pattern[str]":
    """Compile *pattern* up front; a malformed pattern is a code-1 user error.

    Called before any Mongo or coverage access — the first thing
    :func:`search_channel` does — so a bad ``--grep`` never reaches the cache.
    """
    try:
        return re.compile(pattern)
    except re.error as err:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"--grep is not a valid regular expression: {err}",
            remediation="fix the pattern (Python `re` syntax) and try again",
        ) from err


def resolve_window(
    since: dt.datetime | None,
    until: dt.datetime | None,
    *,
    now: dt.datetime | None = None,
) -> _coverage.Interval:
    """Resolve ``--since``/``--until`` into a concrete window (o18).

    Both or neither — a lone bound cannot express a window and is refused
    (code 1). With neither given, the window is the channel's whole possible
    history (Discord's epoch through *now*), the same default
    :func:`jlab.fetch.fetch_channel` drains to, so "search everything" is
    still a nameable, gap-reportable window rather than "whatever is cached".
    """
    if (since is None) != (until is None):
        raise CliError(
            code=EXIT_USER_ERROR,
            message="--since and --until must be given together",
            remediation=(
                "pass both bounds to search a window, or neither to search "
                "the channel's whole history"
            ),
        )
    if since is None:
        return _coverage.Interval(DISCORD_EPOCH, now or dt.datetime.now(UTC))
    return _coverage.Interval(since, until)


def _aware(value: Any) -> dt.datetime | None:
    """Coerce a cached ``created_at`` to a timezone-aware datetime, or None.

    Mirrors ``jlab.coverage._aware``: a real jlab-mongodb round-trip returns
    naive-but-UTC datetimes (BSON carries no zone), so this is not optional.
    """
    if not isinstance(value, dt.datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _worker(
    pattern: str, items: Sequence[tuple[str, str]], max_matches: int | None, q: Any
) -> None:
    """Run in the child process (see the module docstring's o13 section).

    Streams every match back immediately, then a final ``("done", reason)`` —
    the parent only ever sees ``bounded=True`` when this sentinel never
    arrives before the deadline.
    """
    try:
        compiled = re.compile(pattern)
    except re.error:  # pragma: no cover - already validated in the parent
        q.put(("error", "invalid pattern"))
        return
    found = 0
    for message_id, content in items:
        if compiled.search(content or ""):
            q.put(("match", message_id))
            found += 1
            if max_matches is not None and found >= max_matches:
                q.put(("done", REASON_MAX_MATCHES))
                return
    q.put(("done", REASON_EXHAUSTED))


def _bounded_search(
    pattern: str,
    items: Sequence[tuple[str, str]],
    *,
    timeout: float,
    max_matches: int | None,
) -> tuple[list[str], bool, str | None]:
    """Match *pattern* over *items* in a child process, bounded by *timeout*.

    Returns ``(matched_ids, bounded, reason)`` in discovery order (which is
    also ``created_at`` order, since *items* is). ``bounded`` is True exactly
    when the wall-clock deadline fired before the worker finished — whatever
    had already streamed back is still returned (o13: never an empty result
    read as "no matches").
    """
    if not items:
        return [], False, REASON_EXHAUSTED

    ctx = mp.get_context("fork")
    q: Any = ctx.Queue()
    proc = ctx.Process(target=_worker, args=(pattern, list(items), max_matches, q), daemon=True)
    proc.start()
    deadline = time.monotonic() + timeout
    matched: list[str] = []
    bounded = True
    reason: str | None = None
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                kind, payload = q.get(timeout=remaining)
            except _queue_mod.Empty:
                break
            if kind == "match":
                matched.append(payload)
            elif kind == "done":
                reason = payload
                bounded = False
                break
            elif kind == "error":  # pragma: no cover - defence in depth
                reason = payload
                bounded = False
                break
    finally:
        if proc.is_alive():
            proc.terminate()
            proc.join(2)
            q.cancel_join_thread()
        else:
            proc.join(0)
        q.close()
    return matched, bounded, reason


def _render_match(message: dict[str, Any]) -> dict[str, Any]:
    """The output shape: id, created_at, author id + name, jump url, content.

    ``author_name`` is the cache's own decrypted, stored name (deviation d4)
    — never a live Discord lookup, so this cache-served path still never
    contacts Discord. It is ``None`` only for a message cached before d4.
    """
    created = message.get("created_at")
    return {
        "message_id": message.get("message_id"),
        "channel_id": message.get("channel_id"),
        "author_id": message.get("author_id"),
        "author_name": message.get("author_name"),
        "created_at": created.isoformat() if isinstance(created, dt.datetime) else created,
        "jump_url": message.get("jump_url"),
        "content": message.get("content"),
    }


def search_channel(
    channel_id: str,
    pattern: str,
    *,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
    max_matches: int | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    collection: Any = None,
    coverage_collection: Any = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Regex-search *channel_id*'s cached corpus; never touches Discord.

    *pattern* is compiled (and, on failure, raises code-1 :class:`CliError`)
    before anything else runs — no Mongo collection is opened for a malformed
    pattern. *since*/*until* resolve to a window via :func:`resolve_window`
    (o18); *max_matches* stops the scan early ("truncated" in the result,
    distinct from "bounded"); *timeout* is the o13 wall-clock bound.

    Returns a dict with ``matches`` (rendered, oldest-first), ``match_count``,
    ``scanned`` (messages actually checked), ``window``, ``complete``,
    ``uncovered`` (from :func:`jlab.coverage.describe`), ``bounded`` and
    ``truncated``.
    """
    compile_pattern(pattern)  # o1-equivalent: validate before any cache access

    if max_matches is not None and max_matches < 1:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"--max-matches must be >= 1, got {max_matches}",
            remediation="pass a positive integer, or omit it for no limit",
        )
    if timeout is None or timeout <= 0:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"--timeout must be > 0, got {timeout!r}",
            remediation=f"pass a positive number of seconds (default {DEFAULT_TIMEOUT_SECONDS})",
        )

    window = resolve_window(since, until, now=now)
    channel = str(channel_id)
    coverage_info = _coverage.describe(channel, window, collection=coverage_collection)

    messages = list(_cache.iter_messages(channel, collection=collection))
    by_id = {m["message_id"]: m for m in messages}
    in_window = [
        m
        for m in messages
        if (created := _aware(m.get("created_at"))) is not None
        and window.start <= created <= window.end
    ]
    items = [(m["message_id"], m.get("content") or "") for m in in_window]

    matched_ids, bounded, reason = _bounded_search(
        pattern, items, timeout=timeout, max_matches=max_matches
    )
    matches = [by_id[mid] for mid in matched_ids if mid in by_id]

    return {
        "channel_id": channel,
        "pattern": pattern,
        "window": window.to_dict(),
        "matches": [_render_match(m) for m in matches],
        "match_count": len(matches),
        "scanned": len(items),
        "complete": coverage_info["complete"],
        "uncovered": coverage_info["uncovered"],
        "bounded": bounded,
        "truncated": reason == REASON_MAX_MATCHES,
        "timeout": timeout,
    }
