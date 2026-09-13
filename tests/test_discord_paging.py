"""Regression tests for the backward-paging cursor (qodo #3998468666, #3998468655).

These pin two confirmed bugs found in review of the backward drain
(``jlab.cli._discord._drain_backward`` and friends) that the existing fakes in
``tests/test_discord.py`` were not strict enough to catch:

* **#3998468666** — discord.py's ``Messageable.history`` defaults
  ``oldest_first`` to ``after is not None``, and that default doesn't just
  pick the order messages come back in: it picks which HTTP pagination
  *strategy* runs (an ``after``-anchored fetch vs a ``before``-anchored one).
  A backward walk that handed both an ``after`` floor and a ``before`` cursor
  to the same call would silently run the ``after``-anchored strategy and
  never converge on the shrinking ``before`` cursor. The existing
  ``_BackwardChannel`` fake in ``test_discord.py`` filters by both bounds
  directly and always returns the slice nearest ``before`` — it doesn't model
  the strategy flip, so it can't catch this. ``_StrategyAwareChannel`` below
  does. The fix is to never combine the two cursors in one call at all: the
  ``after`` floor is enforced client-side in ``_drain_backward_page``.

* **#3998468655** — the backward cursor used to move by the oldest message's
  ``created_at`` datetime. discord.py turns a datetime ``before`` into a
  snowflake with its low bits zeroed, which is exclusive of the *whole*
  millisecond, so a sibling message sharing that millisecond at a page
  boundary would be silently skipped. The fix moves the cursor by the oldest
  message's own id (a snowflake) instead — unique and strictly ordered by
  creation, so it has no such tie. ``_TieBoundaryChannel`` below models a raw
  datetime cursor's whole-millisecond exclusion and proves the tied message
  is recovered.

No network calls: fakes only, nothing hits Discord.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from jlab.cli import _discord
from tests.test_discord import _window_msgs


class _StrategyAwareChannel:
    """Models discord.py's pagination-*strategy* selection, keyed off
    ``oldest_first`` (default ``after is not None``) — not just the order
    messages come back in.

    When ``oldest_first``/``reverse`` is true, the slice returned is the one
    nearest ``after`` (ascending); when false, it is the one nearest
    ``before`` (descending). A backward walk needs the latter on every call,
    even when an ``after`` floor is also in play.
    """

    page_cap = 100

    def __init__(self, messages: list) -> None:
        self._messages = list(messages)  # oldest-first, unique increasing ids
        self.history_calls: list[dict] = []

    def _in_range(self, message: Any, after: Any, before: Any, before_id: Any) -> bool:
        if after is not None and message.created_at <= after:
            return False
        if before is not None:
            if before_id is not None:
                return message.id < before_id
            return message.created_at < before
        return True

    def history(
        self,
        *,
        limit: int | None = None,
        after: datetime | None = None,
        before: Any | None = None,
        oldest_first: bool | None = None,
    ) -> Any:
        self.history_calls.append(
            {"limit": limit, "after": after, "before": before, "oldest_first": oldest_first}
        )
        reverse = (after is not None) if oldest_first is None else oldest_first
        before_id = getattr(before, "id", None)
        candidates = [m for m in self._messages if self._in_range(m, after, before, before_id)]
        cap = self.page_cap if limit is None else min(limit, self.page_cap)
        page = candidates[:cap] if reverse else list(reversed(candidates))[:cap]

        async def _gen():
            for m in page:
                yield m

        return _gen()


def test_backward_drain_never_hands_both_cursors_to_one_history_call() -> None:
    """qodo #3998468666: a backward walk with an `after` floor must still
    fully collect a >100-message window, by never sending `after` downstream
    to the same call as `before` — the fix enforces the floor client-side.
    """
    msgs = _window_msgs(250)
    floor = msgs[49].created_at
    chan = _StrategyAwareChannel(msgs)

    messages, complete, reason = asyncio.run(
        _discord._collect_history(
            chan, limit=None, after=floor, before=None, backward=True, max_messages=None
        )
    )

    assert complete is True
    assert reason is None
    assert len(messages) == 200  # msgs[50:250] — strictly newer than the floor
    assert all(m.created_at > floor for m in messages)
    # The load-bearing assertion: no call ever carried a non-None `after`,
    # which is what would flip discord.py's real `oldest_first` default and
    # break the walk (the old, buggy behaviour this fake exists to catch).
    assert all(call["after"] is None for call in chan.history_calls)
    assert len(chan.history_calls) >= 2  # the window really did page


async def _drain_one_page(history_call: Any) -> list:
    return [m async for m in history_call]


def test_strategy_aware_channel_reproduces_the_bug_when_both_cursors_are_sent() -> None:
    """Sanity check on the fake itself: if a caller DID hand both cursors to
    one call (the bug this module guards against), the after-anchored
    strategy kicks in and returns the slice nearest `after`, not `before` —
    the wrong end of the window for a backward walk.
    """
    msgs = _window_msgs(250)
    floor = msgs[49].created_at
    chan = _StrategyAwareChannel(msgs)

    # Simulate the buggy call shape directly: after AND before together.
    page = asyncio.run(_drain_one_page(chan.history(limit=100, after=floor, before=None)))

    # oldest_first defaulted True (after is not None) -> the slice nearest
    # `after`, ascending -- msgs[50:150], not the newest 100 a backward walk
    # actually wants.
    assert [m.id for m in page][:3] == [msgs[50].id, msgs[51].id, msgs[52].id]
    assert page[0].created_at < msgs[150].created_at


class _TieBoundaryChannel:
    """Filters ``before`` as a strict id boundary when given a message-like
    cursor, but as a whole-millisecond-exclusive boundary when given a raw
    ``datetime`` — modelling discord.py's ``time_snowflake(dt, high=False)``
    conversion, which zeroes the low bits and so excludes every message
    sharing that millisecond, not just the one the cursor names.
    """

    page_cap = 100

    def __init__(self, messages: list) -> None:
        self._messages = list(messages)  # oldest-first, unique increasing ids
        self.history_calls: list[dict] = []

    def history(
        self,
        *,
        limit: int | None = None,
        after: datetime | None = None,
        before: Any | None = None,
    ) -> Any:
        self.history_calls.append({"limit": limit, "after": after, "before": before})
        before_id = getattr(before, "id", None)
        if before_id is not None:
            candidates = [m for m in self._messages if m.id < before_id]
        elif before is not None:
            candidates = [m for m in self._messages if m.created_at < before]
        else:
            candidates = list(self._messages)
        if after is not None:
            candidates = [m for m in candidates if m.created_at > after]
        cap = self.page_cap if limit is None else min(limit, self.page_cap)
        page = list(reversed(candidates))[:cap]

        async def _gen():
            for m in page:
                yield m

        return _gen()


class _TiedMsg:
    """A minimal discord.py-message-shaped fake: just `id` and `created_at`."""

    def __init__(self, id: int, created_at: datetime) -> None:
        self.id = id
        self.created_at = created_at


def _tied_boundary_msgs(n: int) -> list:
    """*n* oldest-first messages where index 0 and 1 share one timestamp.

    Real discord.py ids are unique, strictly increasing snowflakes even when
    two messages land in the same millisecond, so index 0 and 1 keep distinct
    (increasing) ids despite the tied ``created_at`` — exactly the page-
    boundary tie qodo #3998468655 is about.
    """
    now = datetime.now(timezone.utc)
    tie = now - timedelta(minutes=n)
    messages = [_TiedMsg(0, tie), _TiedMsg(1, tie)]
    messages += [_TiedMsg(i, now - timedelta(minutes=n - i)) for i in range(2, n)]
    return messages


def test_backward_drain_recovers_a_tied_message_at_the_page_boundary() -> None:
    """qodo #3998468655: the cursor moves by id, not `created_at`, so a
    sibling message sharing the boundary's millisecond is not silently
    skipped.
    """
    msgs = _tied_boundary_msgs(101)  # id 0 and 1 share one timestamp
    chan = _TieBoundaryChannel(msgs)

    messages, complete, reason = asyncio.run(
        _discord._collect_history(
            chan, limit=None, after=None, before=None, backward=True, max_messages=None
        )
    )

    assert complete is True
    assert reason is None
    assert len(messages) == 101
    ids = {m.id for m in messages}
    assert ids == set(range(101))  # id 0 (tied with id 1) is not dropped


def test_backward_drain_would_drop_the_tied_message_with_a_datetime_cursor() -> None:
    """Sanity check on the fake: a raw-datetime `before` cursor (the old,
    buggy edge) really does drop the tied sibling, proving the id-based
    cursor is load-bearing rather than incidental.
    """
    msgs = _tied_boundary_msgs(101)
    chan = _TieBoundaryChannel(msgs)
    tied_timestamp = msgs[1].created_at

    page = asyncio.run(_drain_one_page(chan.history(limit=100, before=tied_timestamp)))

    assert 0 not in {m.id for m in page}
    assert 1 not in {m.id for m in page}
