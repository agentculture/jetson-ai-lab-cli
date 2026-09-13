"""Tests for :mod:`jlab.reconcile` — the shared span-reconciliation logic
used by both ``jlab.sweep`` and ``jlab.read``'s ``--refresh``.

Focus: qodo review comment 3998468657 on ``jlab/reconcile.py`` — deletion of
an absent cached message must be decided by message id, not by whether
*any* live message happens to share its ``created_at``. Two distinct
messages can legitimately share a millisecond-precision timestamp in a busy
channel; a blanket timestamp veto let a genuinely deleted message hide
behind an unrelated survivor forever.
"""

from __future__ import annotations

import base64
import datetime as dt

import pytest

from jlab import cache as _cache
from jlab import coverage as _coverage
from jlab import reconcile as _reconcile
from tests.test_purge import _FakeCollection

UTC = dt.timezone.utc
_TEST_KEY = base64.urlsafe_b64encode(b"r" * 32).decode()


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("JLAB_" + "CACHE_KEY", _TEST_KEY)
    return _TEST_KEY


def _msg(mid: str, when: dt.datetime, content: str = "body") -> dict:
    return {
        "id": mid,
        "content": content,
        "created_at": when,
        "edited_at": None,
        "author": {"id": "u1", "name": "ann", "display_name": "Ann", "bot": False},
        "jump_url": f"https://discord.com/x/{mid}",
    }


def _seed(col: _FakeCollection, channel_id: str, messages: list[dict]) -> None:
    result = _cache.store_messages(channel_id, messages, collection=col)
    assert result["stored"] == len(messages)


def _cached_ids(col: _FakeCollection, channel_id: str, span: _coverage.Interval) -> set[str]:
    return {
        m["message_id"]
        for m in _cache.messages_between(channel_id, span.start, span.end, collection=col)
    }


def test_a_deleted_message_sharing_a_timestamp_with_a_survivor_is_deleted(key: str) -> None:
    """The exact qodo scenario: two DIFFERENT messages share created_at.

    One is genuinely deleted from Discord (absent from the live re-read);
    the other genuinely survives. Deletion must be decided per-id, so the
    deleted one is removed even though a live message shares its timestamp.
    """
    col = _FakeCollection()
    channel_id = "9001"
    base = dt.datetime(2026, 1, 1, tzinfo=UTC)
    same_stamp = base + dt.timedelta(minutes=5)

    survivor = _msg("m-survivor", same_stamp)
    deleted = _msg("m-deleted", same_stamp)
    untouched = _msg("m-other", base + dt.timedelta(minutes=1))
    _seed(col, channel_id, [survivor, deleted, untouched])

    span = _coverage.Interval(start=base, end=base + dt.timedelta(minutes=10))
    live_messages = [survivor, untouched]  # "m-deleted" is gone from Discord

    result = _reconcile.reconcile_span(
        channel_id,
        span,
        live_messages,
        complete=True,
        reason=None,
        collection=col,
    )

    assert result["complete"] is True
    assert result["deleted"] == 1
    remaining = _cached_ids(col, channel_id, span)
    assert "m-deleted" not in remaining
    assert "m-survivor" in remaining
    assert "m-other" in remaining


def test_a_message_at_the_span_edge_is_not_a_deletion_candidate(key: str) -> None:
    """A cached message exactly on span.start/span.end can never have come
    back from this live read (the read's after=/before= cursors are
    exclusive there), so it must never be treated as evidence of deletion —
    even though :func:`jlab.cache.messages_between` already excludes it from
    the comparison, this pins that guarantee at the reconcile_span level.
    """
    col = _FakeCollection()
    channel_id = "9002"
    start = dt.datetime(2026, 1, 1, tzinfo=UTC)
    end = start + dt.timedelta(minutes=10)

    edge_message = _msg("m-edge", start)  # exactly on the exclusive edge
    inside_message = _msg("m-inside", start + dt.timedelta(minutes=5))
    _seed(col, channel_id, [edge_message, inside_message])

    span = _coverage.Interval(start=start, end=end)
    # A live read bounded by (start, end) could never return "m-edge" either.
    live_messages = [inside_message]

    result = _reconcile.reconcile_span(
        channel_id,
        span,
        live_messages,
        complete=True,
        reason=None,
        collection=col,
    )

    # messages_between already excludes the edge message from "cached", so it
    # was never a deletion candidate to begin with.
    assert result["deleted"] == 0


def test_an_unrelated_timestamp_no_longer_blocks_deletion(key: str) -> None:
    """Sanity check the fix's direction: a cached message whose created_at
    matches nothing live at all is still deleted, as before.
    """
    col = _FakeCollection()
    channel_id = "9003"
    base = dt.datetime(2026, 1, 1, tzinfo=UTC)

    deleted = _msg("m-lonely", base + dt.timedelta(minutes=3))
    _seed(col, channel_id, [deleted])

    span = _coverage.Interval(start=base, end=base + dt.timedelta(minutes=10))

    result = _reconcile.reconcile_span(
        channel_id,
        span,
        [],
        complete=True,
        reason=None,
        collection=col,
    )

    assert result["deleted"] == 1
    assert _cached_ids(col, channel_id, span) == set()
