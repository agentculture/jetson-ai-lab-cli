"""Tests for the id-only aggregation stage (jlab.members.aggregate).

Covers t3's three acceptance criteria:

1. Aggregates by ``author.id`` only — no username (``name`` /
   ``display_name``) appears anywhere in the aggregate output.
2. Substance uses message length (and the question-start heuristic — see
   ``aggregate.py``'s module docstring for why "reply counts" / genuine
   thread-starts are not derivable from this input) computed at read time;
   no message content reaches the aggregate output.
3. All four signals (message count, distinct channels, question_starts,
   substance) are emitted side by side, with no combined score/rank field
   and no content-carrying ``preview`` field.
"""

from __future__ import annotations

import json

from jlab.members.aggregate import aggregate

_SECRET_USERNAME = "definitely-not-a-username-xyz"
_SECRET_DISPLAY_NAME = "Definitely Not A Display Name"
_SECRET_CONTENT_A = "the launch codes are hidden under the couch cushions"
_SECRET_CONTENT_B = "is this thing on?"


def _message(msg_id: str, author_id: str | None, content: str, created_at: str) -> dict:
    return {
        "id": msg_id,
        "author": {
            "id": author_id,
            "name": _SECRET_USERNAME,
            "display_name": _SECRET_DISPLAY_NAME,
            "bot": False,
        },
        "content": content,
        "created_at": created_at,
    }


def _scan(channels: list[dict], **overrides) -> dict:
    base = {
        "guild_id": "1326246312072581160",
        "since_days": 90,
        "cutoff": "2026-06-06T00:00:00+00:00",
        "concurrency": 4,
        "max_messages_per_channel": 5000,
        "exclude_bots": True,
        "scanned_text_channels": len(channels),
        "channels_ok": len(channels),
        "channels_partial": 0,
        "channels_failed": 0,
        "message_count": sum(c["message_count"] for c in channels),
        "complete": True,
        "channels": channels,
    }
    base.update(overrides)
    return base


def _channel(chan_id: str, name: str, messages: list[dict], **overrides) -> dict:
    base = {
        "id": chan_id,
        "name": name,
        "messages": messages,
        "message_count": len(messages),
        "status": "ok",
        "reason": None,
        "complete": True,
    }
    base.update(overrides)
    return base


def test_no_username_anywhere_in_output() -> None:
    channel = _channel(
        "c1",
        "general",
        [
            _message("m1", "111", _SECRET_CONTENT_A, "2026-07-01T00:00:00+00:00"),
            _message("m2", "111", _SECRET_CONTENT_B, "2026-07-02T00:00:00+00:00"),
        ],
    )
    scan = _scan([channel])

    result = aggregate(scan)

    dumped = json.dumps(result)
    assert _SECRET_USERNAME not in dumped
    assert _SECRET_DISPLAY_NAME not in dumped
    # Sanity: the field names themselves must not appear either.
    assert "display_name" not in dumped
    assert '"name"' not in dumped

    assert result["members"] == [
        {
            "author_id": "111",
            "message_count": 2,
            "distinct_channels": 1,
            "question_starts": 1,
            "substance": {
                "total_length": len(_SECRET_CONTENT_A) + len(_SECRET_CONTENT_B),
                "avg_length": (len(_SECRET_CONTENT_A) + len(_SECRET_CONTENT_B)) / 2,
            },
        }
    ]


def test_no_message_content_in_output() -> None:
    channel = _channel(
        "c1",
        "general",
        [
            _message("m1", "222", _SECRET_CONTENT_A, "2026-07-01T00:00:00+00:00"),
            _message("m2", "222", _SECRET_CONTENT_B, "2026-07-02T00:00:00+00:00"),
        ],
    )
    scan = _scan([channel])

    result = aggregate(scan)

    dumped = json.dumps(result)
    assert _SECRET_CONTENT_A not in dumped
    assert _SECRET_CONTENT_B not in dumped
    assert "content" not in dumped
    # The verbatim-content preview field from _rank_channel must never
    # appear in the aggregate output.
    assert "preview" not in dumped


def test_substance_computed_from_length_not_content() -> None:
    channel = _channel(
        "c1",
        "general",
        [
            _message("m1", "333", "short", "2026-07-01T00:00:00+00:00"),
            _message("m2", "333", "a much longer message body here", "2026-07-02T00:00:00+00:00"),
        ],
    )
    scan = _scan([channel])

    result = aggregate(scan)
    member = result["members"][0]

    assert member["substance"]["total_length"] == len("short") + len(
        "a much longer message body here"
    )
    assert member["substance"]["avg_length"] == member["substance"]["total_length"] / 2


def test_four_signals_side_by_side_no_combined_score() -> None:
    channel_a = _channel(
        "c1",
        "general",
        [_message("m1", "444", "hello", "2026-07-01T00:00:00+00:00")],
    )
    channel_b = _channel(
        "c2",
        "off-topic",
        [
            _message("m2", "444", "is this active?", "2026-07-02T00:00:00+00:00"),
            _message("m3", "444", "another one", "2026-07-03T00:00:00+00:00"),
        ],
    )
    scan = _scan([channel_a, channel_b])

    result = aggregate(scan)
    member = result["members"][0]

    assert set(member.keys()) == {
        "author_id",
        "message_count",
        "distinct_channels",
        "question_starts",
        "substance",
    }
    assert member["message_count"] == 3
    assert member["distinct_channels"] == 2
    assert member["question_starts"] == 1
    assert isinstance(member["substance"], dict)

    # No combined score / rank / ordering field anywhere in the output.
    dumped = json.dumps(result)
    for forbidden in ("score", "rank", "ranking"):
        assert forbidden not in dumped


def test_multiple_authors_keep_separate_rows_sorted_by_id() -> None:
    channel = _channel(
        "c1",
        "general",
        [
            _message("m1", "999", "a", "2026-07-01T00:00:00+00:00"),
            _message("m2", "555", "bb", "2026-07-01T00:00:00+00:00"),
            _message("m3", "999", "ccc", "2026-07-02T00:00:00+00:00"),
        ],
    )
    scan = _scan([channel])

    result = aggregate(scan)

    author_ids = [m["author_id"] for m in result["members"]]
    assert author_ids == ["555", "999"]

    row_999 = next(m for m in result["members"] if m["author_id"] == "999")
    assert row_999["message_count"] == 2
    assert row_999["substance"]["total_length"] == len("a") + len("ccc")


def test_messages_without_author_id_are_skipped() -> None:
    channel = _channel(
        "c1",
        "general",
        [
            _message("m1", None, "orphaned", "2026-07-01T00:00:00+00:00"),
            _message("m2", "1", "attributed", "2026-07-01T00:00:00+00:00"),
        ],
    )
    scan = _scan([channel])

    result = aggregate(scan)

    assert [m["author_id"] for m in result["members"]] == ["1"]


def test_carries_through_coverage_and_completeness_fields() -> None:
    channel = _channel(
        "c1",
        "general",
        [_message("m1", "1", "hi", "2026-07-01T00:00:00+00:00")],
        status="partial",
        complete=False,
    )
    scan = _scan(
        [channel],
        channels_ok=0,
        channels_partial=1,
        channels_failed=0,
        complete=False,
    )

    result = aggregate(scan)

    assert result["guild_id"] == scan["guild_id"]
    assert result["since_days"] == scan["since_days"]
    assert result["cutoff"] == scan["cutoff"]
    assert result["scanned_text_channels"] == scan["scanned_text_channels"]
    assert result["channels_ok"] == 0
    assert result["channels_partial"] == 1
    assert result["channels_failed"] == 0
    assert result["complete"] is False
    assert result["message_count"] == scan["message_count"]


def test_empty_scan_produces_no_members() -> None:
    scan = _scan([])

    result = aggregate(scan)

    assert result["members"] == []
    assert result["message_count"] == 0
