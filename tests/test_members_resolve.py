"""Tests for jlab.members.resolve — batch id->name + membership resolution.

No live Discord calls: everything monkeypatches ``jlab.cli._discord._seam``
with fake guild/member objects, mirroring the pattern in ``test_discord.py``.
"""

from __future__ import annotations

import asyncio

import pytest

from jlab.cli import _discord
from jlab.members import resolve

_GUILD_ID = 1326246312072581160


class NotFound(Exception):
    """Stand-in for discord.NotFound, matched by class name only."""


class _FakeMember:
    def __init__(
        self,
        name: str,
        nick: str | None = None,
        global_name: str | None = None,
        joined_at=None,
    ) -> None:
        self.name = name
        self.nick = nick
        self.global_name = global_name
        self.joined_at = joined_at


class _FakeGuild:
    def __init__(self, members: dict[int, _FakeMember | Exception]) -> None:
        self._members = members

    async def fetch_member(self, member_id: int):
        entry = self._members.get(member_id)
        if isinstance(entry, Exception):
            raise entry
        if entry is None:
            raise NotFound("unknown member")
        return entry


class _FakeClient:
    def __init__(self, guild: _FakeGuild) -> None:
        self._guild = guild

    async def fetch_guild(self, _gid: int) -> _FakeGuild:
        return self._guild


class _FakeSeam:
    def __init__(self, guild: _FakeGuild) -> None:
        self._guild = guild

    def run(self, action):
        return asyncio.run(action(_FakeClient(self._guild)))


def _patch_seam(monkeypatch: pytest.MonkeyPatch, guild: _FakeGuild) -> None:
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(guild))


# ---------------------------------------------------------------------------
# Distinct stage: standalone-inspectable, ids-only-in / ids-keyed-out.
# ---------------------------------------------------------------------------


def test_result_is_standalone_dumpable_and_ids_keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    guild = _FakeGuild({111: _FakeMember("annie")})
    _patch_seam(monkeypatch, guild)

    result = resolve.resolve_authors(_GUILD_ID, {"111": {"message_count": 5}})
    payload = result.to_dict()

    # No stats value leaked into the resolution stage's own output.
    assert "message_count" not in str(payload)
    assert payload["resolved"]["111"]["id"] == "111"
    assert payload["guild_id"] == str(_GUILD_ID)


# ---------------------------------------------------------------------------
# Per-id tolerance — one bad id never fails the batch.
# ---------------------------------------------------------------------------


def test_one_bad_id_does_not_fail_the_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    guild = _FakeGuild(
        {
            111: _FakeMember("annie"),
            222: RuntimeError("boom"),
        }
    )
    _patch_seam(monkeypatch, guild)

    result = resolve.resolve_authors(_GUILD_ID, {"111": {}, "222": {}})

    assert result.resolved["111"].status == resolve.STATUS_OK
    assert result.resolved["222"].status == resolve.STATUS_ERROR
    assert "boom" in result.resolved["222"].error
    assert result.total_authors == 2


def test_invalid_author_id_is_an_error_entry_not_a_batch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild = _FakeGuild({111: _FakeMember("annie")})
    _patch_seam(monkeypatch, guild)

    result = resolve.resolve_authors(_GUILD_ID, {"111": {}, "not-a-number": {}})

    assert result.resolved["111"].status == resolve.STATUS_OK
    assert result.resolved["not-a-number"].status == resolve.STATUS_ERROR


# ---------------------------------------------------------------------------
# Membership check runs in the SAME session (single fetch_member per id).
# ---------------------------------------------------------------------------


def test_membership_and_name_resolved_in_one_call_per_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    guild = _FakeGuild({111: _FakeMember("annie", nick="Ann")})

    async def counting_fetch_member(member_id: int):
        calls.append(member_id)
        return await _FakeGuild.fetch_member(guild, member_id)

    guild.fetch_member = counting_fetch_member  # type: ignore[method-assign]
    _patch_seam(monkeypatch, guild)

    result = resolve.resolve_authors(_GUILD_ID, {"111": {}})

    assert calls == [111]
    entry = result.resolved["111"]
    assert entry.member is True
    assert entry.status == resolve.STATUS_OK


# ---------------------------------------------------------------------------
# Display name precedence: nick > global_name > username.
# ---------------------------------------------------------------------------


def test_display_name_prefers_nick_over_global_name_over_username(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild = _FakeGuild(
        {
            1: _FakeMember("user1", nick="Nicky", global_name="Global One"),
            2: _FakeMember("user2", nick=None, global_name="Global Two"),
            3: _FakeMember("user3", nick=None, global_name=None),
        }
    )
    _patch_seam(monkeypatch, guild)

    result = resolve.resolve_authors(_GUILD_ID, {"1": {}, "2": {}, "3": {}})

    assert result.resolved["1"].display_name == "Nicky"
    assert result.resolved["2"].display_name == "Global Two"
    assert result.resolved["3"].display_name == "user3"


def test_joined_at_is_serialized_iso_string(monkeypatch: pytest.MonkeyPatch) -> None:
    import datetime

    joined = datetime.datetime(2026, 7, 1, 11, 38, 21, tzinfo=datetime.timezone.utc)
    guild = _FakeGuild({111: _FakeMember("annie", joined_at=joined)})
    _patch_seam(monkeypatch, guild)

    result = resolve.resolve_authors(_GUILD_ID, {"111": {}})

    assert result.resolved["111"].joined_at == joined.isoformat()


# ---------------------------------------------------------------------------
# Departed authors: excluded by default, counted in coverage, count reported.
# ---------------------------------------------------------------------------


def test_departed_author_detected_via_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    guild = _FakeGuild({111: _FakeMember("annie"), 222: None})  # 222 -> NotFound
    _patch_seam(monkeypatch, guild)

    result = resolve.resolve_authors(_GUILD_ID, {"111": {}, "222": {}})

    assert result.resolved["222"].status == resolve.STATUS_DEPARTED
    assert result.resolved["222"].member is False


def test_departed_excluded_by_default_but_counted_in_totals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild = _FakeGuild({111: _FakeMember("annie"), 222: None})
    _patch_seam(monkeypatch, guild)

    result = resolve.resolve_authors(_GUILD_ID, {"111": {}, "222": {}})

    assert result.total_authors == 2  # departed still counts toward coverage
    assert result.included_author_ids == ["111"]  # but excluded from the shown list
    assert result.departed_count == 1
    payload = result.to_dict()
    assert payload["excluded_departed_count"] == 1


def test_include_departed_option_includes_everyone(monkeypatch: pytest.MonkeyPatch) -> None:
    guild = _FakeGuild({111: _FakeMember("annie"), 222: None})
    _patch_seam(monkeypatch, guild)

    result = resolve.resolve_authors(_GUILD_ID, {"111": {}, "222": {}}, include_departed=True)

    assert sorted(result.included_author_ids) == ["111", "222"]
    assert result.departed_count == 1  # still reported even when included


def test_errored_ids_are_not_dropped_from_included_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild = _FakeGuild({111: RuntimeError("transient")})
    _patch_seam(monkeypatch, guild)

    result = resolve.resolve_authors(_GUILD_ID, {"111": {}})

    # An error means "unknown", not "gone" — only a confirmed departure is
    # excluded from the default view.
    assert result.included_author_ids == ["111"]
    assert result.error_count == 1


def test_empty_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    guild = _FakeGuild({})
    _patch_seam(monkeypatch, guild)

    result = resolve.resolve_authors(_GUILD_ID, {})

    assert result.total_authors == 0
    assert result.included_author_ids == []
    assert result.to_dict()["resolved"] == {}
