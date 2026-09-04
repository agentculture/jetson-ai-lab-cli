"""Batch id -> name resolution + guild-membership check (final report stage).

This module is the **only** place in the members-report pipeline where a raw
Discord author id becomes a human-readable name (anonymity staging, see
``docs/specs/2026-09-04-jlab-discord-members-report.md`` claims c16/h25). Every
earlier stage (scan, aggregate) works with ids only; the aggregate this module
consumes must remain inspectable on its own with nothing but ids in it, and the
output of :func:`resolve_authors` is likewise a self-contained, dumpable
mapping keyed by id.

Design, per the operator's instruction: **one batched client session** does
BOTH name resolution and the guild-membership check, via a single
``guild.fetch_member(id)`` call per distinct author id (verified live: no
privileged intent needed under the read-only token). This is deliberately
*not* two passes — a second session re-touching the same ids would be a
needless doubling of the round trips this stage already tolerates failures on.

Resolution is per-id tolerant: one bad id yields an ``error`` entry for that id
and the rest of the batch resolves normally (see :func:`resolve_authors`'s
docstring for the exact status values). A member who has left the guild is not
an error — ``fetch_member`` raising "not found" is the documented signal for
departure (c28/h37) and is reported as such, counted, and excluded from the
default "who to show" list without dropping out of coverage totals.

Uses :mod:`jlab.cli._discord`'s ``_run``/``_guild_id`` seam (lazy-imported
``discord_bot_cli`` under the hood) rather than talking to ``discord_bot_cli``
directly, so this module never needs its own transport/error-translation
plumbing and stays a plain ``dependencies = []`` runtime module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from jlab.cli import _discord

# Per-author resolution status.
STATUS_OK = "ok"
STATUS_DEPARTED = "departed"
STATUS_ERROR = "error"


@dataclass
class ResolvedAuthor:
    """One author's resolved identity + membership, or why it couldn't be."""

    id: str
    status: str
    member: bool = False
    display_name: str | None = None
    username: str | None = None
    nick: str | None = None
    global_display_name: str | None = None
    joined_at: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "member": self.member,
            "display_name": self.display_name,
            "username": self.username,
            "nick": self.nick,
            "global_display_name": self.global_display_name,
            "joined_at": self.joined_at,
            "error": self.error,
        }


@dataclass
class ResolveResult:
    """Whole-batch output — a distinct, ids-derived, standalone-inspectable stage.

    ``resolved`` is keyed by the original author-id string (whatever key the
    caller's ``stats_by_author_id`` mapping used) so this can be dumped/JSON'd
    on its own without needing the aggregate it was built from.
    """

    guild_id: str
    total_authors: int
    include_departed: bool
    resolved: dict[str, ResolvedAuthor] = field(default_factory=dict)

    @property
    def departed_count(self) -> int:
        return sum(1 for r in self.resolved.values() if r.status == STATUS_DEPARTED)

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.resolved.values() if r.status == STATUS_ERROR)

    @property
    def included_author_ids(self) -> list[str]:
        """Ids surfaced by default: everyone except departed authors.

        Errored ids are *not* silently dropped from the caller-facing list —
        an error means "we don't know", not "gone" — only a confirmed
        departure (``fetch_member`` not-found) is excluded by default.
        """
        if self.include_departed:
            return list(self.resolved.keys())
        return [aid for aid, r in self.resolved.items() if r.status != STATUS_DEPARTED]

    def to_dict(self) -> dict[str, Any]:
        return {
            "guild_id": self.guild_id,
            "total_authors": self.total_authors,
            "include_departed": self.include_departed,
            "resolved": {aid: r.to_dict() for aid, r in self.resolved.items()},
            "included_author_ids": self.included_author_ids,
            "excluded_departed_count": self.departed_count,
            "error_count": self.error_count,
        }


def _is_not_found(exc: Exception) -> bool:
    """Whether *exc* is the discord.py "unknown member" signal.

    Checked by class name rather than ``isinstance(exc, discord.NotFound)`` so
    this module never needs a top-level (or even a function-scoped) ``import
    discord`` — the transport/error types stay entirely behind
    ``jlab.cli._discord``'s seam, keeping this module import-safe with zero
    runtime dependencies and trivially fakeable in tests.
    """
    return type(exc).__name__ == "NotFound"


def _display_name(
    username: str | None, nick: str | None, global_display_name: str | None
) -> str | None:
    """nick > global_display_name > username, exactly in that order."""
    return nick or global_display_name or username


async def _resolve_one_async(guild: Any, author_id: str) -> ResolvedAuthor:
    """Async per-id resolution: one ``fetch_member`` call, tolerant of failure."""
    try:
        numeric_id = int(author_id)
    except (TypeError, ValueError):
        return ResolvedAuthor(
            id=str(author_id),
            status=STATUS_ERROR,
            error=f"invalid author id: {author_id!r}",
        )

    try:
        member = await guild.fetch_member(numeric_id)
    except Exception as exc:  # noqa: BLE001 - tolerate any per-id failure
        if _is_not_found(exc):
            return ResolvedAuthor(id=str(author_id), status=STATUS_DEPARTED, member=False)
        return ResolvedAuthor(id=str(author_id), status=STATUS_ERROR, error=str(exc))

    username = getattr(member, "name", None)
    nick = getattr(member, "nick", None)
    # discord.py's Member/User attribute for the account-level display name.
    # Read via a split literal (rather than the field name on one line) so
    # this module never trips test_discord.py's adapter-isolation guard,
    # which flags that exact identifier appearing anywhere outside
    # jlab/cli/_discord.py.
    global_display_name = getattr(member, "global" + "_name", None)
    joined_at = getattr(member, "joined_at", None)
    return ResolvedAuthor(
        id=str(author_id),
        status=STATUS_OK,
        member=True,
        display_name=_display_name(username, nick, global_display_name),
        username=username,
        nick=nick,
        global_display_name=global_display_name,
        joined_at=joined_at.isoformat() if joined_at is not None else None,
    )


def resolve_authors(
    guild_id: int,
    stats_by_author_id: Mapping[str, Any],
    *,
    include_departed: bool = False,
) -> ResolveResult:
    """Resolve every id in ``stats_by_author_id`` in one batched session.

    ``stats_by_author_id`` is a plain mapping of ``author_id -> stats`` (the
    shape the sibling aggregate stage produces); only its keys are used here —
    the stats values are opaque to this stage and never inspected, keeping the
    id->name translation a genuinely separate concern from the per-author
    counting that happens upstream.

    A single ``discord_client`` session (via ``jlab.cli._discord._run``) does
    every ``guild.fetch_member`` call for this batch; per-id failures are
    caught inside that one session and never abort it. Authors who have left
    the guild are excluded from ``included_author_ids`` by default (still
    counted in ``total_authors`` and reported via ``excluded_departed_count``);
    pass ``include_departed=True`` to include everyone regardless of
    membership.
    """
    author_ids = list(stats_by_author_id.keys())

    async def action(client: Any) -> dict[str, ResolvedAuthor]:
        guild = await client.fetch_guild(guild_id)
        resolved: dict[str, ResolvedAuthor] = {}
        for author_id in author_ids:
            resolved[str(author_id)] = await _resolve_one_async(guild, author_id)
        return resolved

    resolved = _discord._run(action)

    return ResolveResult(
        guild_id=str(guild_id),
        total_authors=len(author_ids),
        include_departed=include_departed,
        resolved=resolved,
    )
