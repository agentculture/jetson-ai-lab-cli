"""Aggregation stage: id-only member statistics, content discarded (t3).

Consumes :func:`jlab.cli._discord.scan_window`'s output and produces a
per-``author.id`` statistics table. Two properties are load-bearing and are
covered by ``tests/test_members_aggregate.py``:

* **Anonymity.** Aggregation keys on ``author["id"]`` only. Nothing in this
  module's output carries ``name`` or ``display_name`` — those live only in
  the raw scan input, which this module never re-emits. Resolving an id back
  to a display name is a *separate, later* stage (t4); this stage must not
  leak a shortcut around that boundary.
* **No content.** Each message's ``content`` is read exactly once, at the
  point this module consumes the scan, to compute a length (and a question
  heuristic — see below). The string itself is never stored, returned, or
  logged; only the integers derived from it survive.

Deliberately **not** done here: ``_rank_channel``'s ``preview`` field (verbatim
message excerpts) is never touched, and no combined score/rank is computed.
The four signals — message count, distinct channels, thread/question starts,
substance — are emitted side by side so a human decides what "active" means;
this module does not decide it for them.

Honesty note on "thread/question starts": the serialized message shape
(``jlab.cli._discord._serialize_message``) carries only
``{id, author, content, created_at}`` — no reply-to / thread-parent field.
There is therefore no data-backed way to detect "this message started a
thread" or "this message was a reply". What the data *does* support is a
"content ends with '?'" heuristic, which this module implements and names
honestly as ``question_starts`` rather than pretending it also covers
threads.
"""

from __future__ import annotations

from typing import Any


def _message_length(message: dict) -> int:
    """Return the character length of *message*'s content.

    This is the only touch point where ``content`` is read. The caller must
    not retain a reference to ``message["content"]`` beyond this call.
    """
    content = message.get("content") or ""
    return len(content)


def _is_question(message: dict) -> bool:
    """Heuristic: does *message*'s content look like a question?

    The scan data has no thread-parent or reply-to field, so a genuine
    "started a thread" / "was a reply" signal cannot be computed from it.
    This is the closest data-backed proxy available: content stripped of
    trailing whitespace ends with ``?``. Named ``question_starts`` (not
    ``thread_starts``) in the output to avoid overclaiming what it measures.
    """
    content = message.get("content") or ""
    return content.rstrip().endswith("?")


def _empty_member_row(author_id: str) -> dict[str, Any]:
    return {
        "author_id": author_id,
        "message_count": 0,
        "distinct_channels": 0,
        "question_starts": 0,
        "substance": {"total_length": 0, "avg_length": 0.0},
        "_channel_ids": set(),
    }


def aggregate(scan_result: dict) -> dict:
    """Aggregate a ``scan_window()`` result into id-only member statistics.

    Returns a dict:

    .. code-block:: python

        {
          "guild_id": str, "since_days": int, "cutoff": str,
          "scanned_text_channels": int, "channels_ok": int,
          "channels_partial": int, "channels_failed": int, "complete": bool,
          "message_count": int,
          "members": [
            {
              "author_id": str,
              "message_count": int,
              "distinct_channels": int,
              "question_starts": int,
              "substance": {"total_length": int, "avg_length": float},
            },
            ...
          ],
        }

    ``members`` carries no username/display-name field anywhere, and no
    combined score or rank — the four per-member signals (message count,
    distinct channels, question_starts, substance) are emitted side by side.
    Presentation ordering (if any) is the renderer's concern; this function
    sorts ``members`` by ``author_id`` purely for deterministic output, which
    carries no "most active" implication.

    Messages with no resolvable author id (``author.id`` is ``None``) are
    skipped — there is nothing to key them on without violating the
    id-only contract.
    """
    by_author: dict[str, dict[str, Any]] = {}

    for channel in scan_result.get("channels", []):
        channel_id = channel.get("id")
        for message in channel.get("messages", []):
            author = message.get("author") or {}
            author_id = author.get("id")
            if author_id is None:
                continue

            row = by_author.setdefault(author_id, _empty_member_row(author_id))

            length = _message_length(message)
            question = _is_question(message)
            # `message` (and its `content`) is not touched again after this
            # point — only the derived integers below are kept.

            row["message_count"] += 1
            row["_channel_ids"].add(channel_id)
            if question:
                row["question_starts"] += 1
            row["substance"]["total_length"] += length

    members = []
    for row in by_author.values():
        total_length = row["substance"]["total_length"]
        count = row["message_count"]
        avg_length = (total_length / count) if count else 0.0
        members.append(
            {
                "author_id": row["author_id"],
                "message_count": count,
                "distinct_channels": len(row["_channel_ids"]),
                "question_starts": row["question_starts"],
                "substance": {
                    "total_length": total_length,
                    "avg_length": avg_length,
                },
            }
        )

    members.sort(key=lambda m: m["author_id"])

    return {
        "guild_id": scan_result.get("guild_id"),
        "since_days": scan_result.get("since_days"),
        "cutoff": scan_result.get("cutoff"),
        "scanned_text_channels": scan_result.get("scanned_text_channels"),
        "channels_ok": scan_result.get("channels_ok"),
        "channels_partial": scan_result.get("channels_partial"),
        "channels_failed": scan_result.get("channels_failed"),
        "complete": scan_result.get("complete"),
        "message_count": scan_result.get("message_count"),
        "members": members,
    }
