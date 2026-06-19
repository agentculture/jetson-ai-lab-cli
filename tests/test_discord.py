"""Tests for the ``jlab discord`` noun group.

No network calls: all tests monkeypatch the adapter layer so nothing hits
Discord or requires a token.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from jlab.cli import _discord, main
from jlab.cli._errors import CliError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GUILD_ID = 1326246312072581160


def _seam_error() -> None:
    """Raise CliError(code=2) simulating missing discord_bot_cli."""
    raise CliError(
        code=2,
        message="discord-bot-cli (with its [discord] extra) is not installed",
        remediation="install it: uv pip install 'jetson-ai-lab[discord]'",
    )


# ---------------------------------------------------------------------------
# discord_bot_cli absent — CliError(code=2)
# ---------------------------------------------------------------------------


def test_discord_channels_without_extra_exits_2(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When discord_bot_cli is absent, `jlab discord channels` exits 2."""
    monkeypatch.setattr("jlab.cli._discord._seam", _seam_error)
    rc = main(["discord", "channels"])
    assert rc == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err
    assert "hint:" in captured.err


# ---------------------------------------------------------------------------
# overview — no network needed
# ---------------------------------------------------------------------------


def test_discord_overview_text(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["discord", "overview"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "jetson-ai-lab-cli discord" in out


def test_discord_overview_json(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["discord", "overview", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["subject"] == "jetson-ai-lab-cli discord"
    assert isinstance(payload["sections"], list)


# ---------------------------------------------------------------------------
# bare `jlab discord` — prints overview
# ---------------------------------------------------------------------------


def test_discord_bare_prints_overview(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["discord"])
    assert rc == 0
    assert "jetson-ai-lab-cli discord" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# channels — monkeypatched
# ---------------------------------------------------------------------------


def test_discord_channels_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canned = [
        {"id": "111", "name": "general", "type": "text", "public": True},
        {"id": "222", "name": "private-stuff", "type": "text", "public": False},
    ]
    monkeypatch.setattr(
        "jlab.cli._discord._guild_id",
        lambda: _GUILD_ID,
    )
    monkeypatch.setattr(
        "jlab.cli._discord.list_channels",
        lambda guild_id, public_only=True: [
            c for c in canned if not public_only or c["public"] is True
        ],
    )
    rc = main(["discord", "channels", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["guild_id"] == str(_GUILD_ID)
    # Default is public-only, so private-stuff should be absent.
    names = [c["name"] for c in payload["channels"]]
    assert "general" in names
    assert "private-stuff" not in names


def test_discord_channels_all_includes_private(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canned = [
        {"id": "111", "name": "general", "type": "text", "public": True},
        {"id": "222", "name": "private-stuff", "type": "text", "public": False},
    ]
    monkeypatch.setattr(
        "jlab.cli._discord._guild_id",
        lambda: _GUILD_ID,
    )
    monkeypatch.setattr(
        "jlab.cli._discord.list_channels",
        lambda guild_id, public_only=True: [
            c for c in canned if not public_only or c["public"] is True
        ],
    )
    rc = main(["discord", "channels", "--all", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    names = [c["name"] for c in payload["channels"]]
    assert "general" in names
    assert "private-stuff" in names


def test_discord_channels_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canned = [
        {"id": "111", "name": "general", "type": "text", "public": True},
    ]
    monkeypatch.setattr(
        "jlab.cli._discord._guild_id",
        lambda: _GUILD_ID,
    )
    monkeypatch.setattr(
        "jlab.cli._discord.list_channels",
        lambda guild_id, public_only=True: canned,
    )
    rc = main(["discord", "channels"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "111" in out
    assert "general" in out


# ---------------------------------------------------------------------------
# read — monkeypatched
# ---------------------------------------------------------------------------


def test_discord_read_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canned = [
        {
            "id": "msg1",
            "author": {"id": "a1", "name": "alice"},
            "content": "hello",
            "created_at": "2026-01-01T00:00:00+00:00",
        },
    ]
    monkeypatch.setattr(
        "jlab.cli._discord.read_messages",
        lambda channel_id, limit=20: canned,
    )
    monkeypatch.setattr(
        "jlab.cli._discord.parse_id",
        lambda value, label: int(value),
    )
    rc = main(["discord", "read", "123", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["channel_id"] == "123"
    assert len(payload["messages"]) == 1
    assert payload["messages"][0]["author"]["name"] == "alice"


def test_discord_read_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canned = [
        {
            "id": "msg1",
            "author": {"id": "a1", "name": "bob"},
            "content": "world",
            "created_at": "2026-01-01T00:00:00+00:00",
        },
    ]
    monkeypatch.setattr(
        "jlab.cli._discord.read_messages",
        lambda channel_id, limit=20: canned,
    )
    monkeypatch.setattr(
        "jlab.cli._discord.parse_id",
        lambda value, label: int(value),
    )
    rc = main(["discord", "read", "456"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "bob" in out
    assert "world" in out


# ---------------------------------------------------------------------------
# active — monkeypatched
# ---------------------------------------------------------------------------


def test_discord_active_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canned = {
        "guild_id": str(_GUILD_ID),
        "since_days": 30,
        "fetch_limit": 30,
        "probed_text_channels": 5,
        "active_channels": 2,
        "channels": [
            {
                "id": "ch1",
                "name": "general",
                "last_post": "2026-06-01T00:00:00+00:00",
                "msgs_in_window": 10,
                "saturated": False,
                "preview": [],
            },
        ],
    }
    monkeypatch.setattr(
        "jlab.cli._discord._guild_id",
        lambda: _GUILD_ID,
    )
    monkeypatch.setattr(
        "jlab.cli._discord.active_scan",
        lambda guild_id, since_days=30, fetch_limit=30, top=0, preview=5: canned,
    )
    rc = main(["discord", "active", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["guild_id"] == str(_GUILD_ID)
    assert payload["active_channels"] == 2


def test_discord_active_probing_goes_to_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canned = {
        "guild_id": str(_GUILD_ID),
        "since_days": 30,
        "fetch_limit": 30,
        "probed_text_channels": 1,
        "active_channels": 0,
        "channels": [],
    }
    monkeypatch.setattr(
        "jlab.cli._discord._guild_id",
        lambda: _GUILD_ID,
    )
    monkeypatch.setattr(
        "jlab.cli._discord.active_scan",
        lambda guild_id, since_days=30, fetch_limit=30, top=0, preview=5: canned,
    )
    rc = main(["discord", "active", "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "probing" in captured.err
    # JSON result on stdout, not stderr.
    assert json.loads(captured.out)


def test_discord_active_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canned = {
        "guild_id": str(_GUILD_ID),
        "since_days": 7,
        "fetch_limit": 30,
        "probed_text_channels": 3,
        "active_channels": 1,
        "channels": [
            {
                "id": "ch1",
                "name": "general",
                "last_post": "2026-06-01T00:00:00+00:00",
                "msgs_in_window": 5,
                "saturated": False,
                "preview": [],
            },
        ],
    }
    monkeypatch.setattr(
        "jlab.cli._discord._guild_id",
        lambda: _GUILD_ID,
    )
    monkeypatch.setattr(
        "jlab.cli._discord.active_scan",
        lambda guild_id, since_days=30, fetch_limit=30, top=0, preview=5: canned,
    )
    rc = main(["discord", "active", "--since", "7"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "general" in out


# ---------------------------------------------------------------------------
# doctor — monkeypatched
# ---------------------------------------------------------------------------


def test_discord_doctor_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "jlab.cli._discord._guild_id",
        lambda: _GUILD_ID,
    )
    monkeypatch.setattr(
        "jlab.cli._discord.doctor",
        lambda guild_id: {"ok": True, "guild_id": str(guild_id)},
    )
    rc = main(["discord", "doctor", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True


def test_discord_doctor_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "jlab.cli._discord._guild_id",
        lambda: _GUILD_ID,
    )
    monkeypatch.setattr(
        "jlab.cli._discord.doctor",
        lambda guild_id: {"ok": True, "guild_id": str(guild_id)},
    )
    rc = main(["discord", "doctor"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ok" in out


# ---------------------------------------------------------------------------
# explain paths resolve
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ["discord"],
        ["discord", "channels"],
        ["discord", "read"],
        ["discord", "active"],
        ["discord", "doctor"],
        ["discord", "overview"],
    ],
)
def test_explain_discord_paths_resolve(path: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["explain", *path])
    assert rc == 0, f"explain {' '.join(path)} failed"
    out = capsys.readouterr().out
    assert out.strip()


# ---------------------------------------------------------------------------
# active_scan — real ranking logic against fake Discord objects.
#
# The verb-level tests above monkeypatch active_scan entirely, so they never
# exercise its serialize/rank internals. These fakes drive the real function:
# they would fail if Discord objects leaked past the session (the dict-vs-object
# regression) or if ranking/cutoff/saturation were wrong.
# ---------------------------------------------------------------------------


class _FakeType:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakePerms:
    def __init__(self, view: bool) -> None:
        self.view_channel = view


class _FakeAuthor:
    def __init__(self, id: str, name: str) -> None:
        self.id = id
        self.name = name


class _FakeMsg:
    def __init__(self, id: str, author: str, content: str, created_at: datetime) -> None:
        self.id = id
        self.author = _FakeAuthor(id + "a", author)
        self.content = content
        self.created_at = created_at


class _FakeChannel:
    def __init__(self, id: str, name: str, type_name: str, public: bool, messages: list) -> None:
        self.id = id
        self.name = name
        self.type = _FakeType(type_name)
        self._public = public
        self._messages = messages  # oldest-first

    def permissions_for(self, _role: object) -> _FakePerms:
        return _FakePerms(self._public)

    def history(self, limit: int):
        newest_first = list(reversed(self._messages))[:limit]

        async def _gen():
            for m in newest_first:
                yield m

        return _gen()


class _FakeGuild:
    def __init__(self, channels: list) -> None:
        self.default_role = object()
        self._channels = channels

    async def fetch_channels(self) -> list:
        return self._channels


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


def test_active_scan_ranks_and_serializes(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)

    def msgs(author: str, *ages_days: int) -> list:
        # oldest-first
        return [
            _FakeMsg(f"{author}{i}", author, f"hi {i}", now - timedelta(days=d))
            for i, d in enumerate(ages_days)
        ]

    guild = _FakeGuild(
        [
            _FakeChannel("c1", "general", "text", True, msgs("ann", 3, 2, 1)),
            _FakeChannel("c2", "busy", "text", True, msgs("bob", 5, 4, 3, 2, 1)),
            _FakeChannel("c3", "quiet", "text", True, msgs("eve", 60)),  # stale
            _FakeChannel("c4", "secret", "text", False, msgs("x", 1)),  # private
            _FakeChannel("c5", "lounge", "voice", True, msgs("y", 1)),  # not text
        ]
    )
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(guild))

    result = _discord.active_scan(123, since_days=30, fetch_limit=5, top=0, preview=2)

    # Private + voice excluded; the three public text channels are probed.
    assert result["probed_text_channels"] == 3
    # 'quiet' is stale (>30d) so it drops out of the ranked rows.
    names = [c["name"] for c in result["channels"]]
    assert names == ["busy", "general"]  # busy (5 msgs) outranks general (3)
    assert "quiet" not in names
    assert "secret" not in names

    busy = result["channels"][0]
    assert busy["msgs_in_window"] == 5
    assert busy["saturated"] is True  # hit fetch_limit
    general = result["channels"][1]
    assert general["msgs_in_window"] == 3
    assert general["saturated"] is False

    # Preview entries must be plain dicts (the regression: objects leaking out).
    assert len(general["preview"]) == 2
    assert set(general["preview"][0]) == {"author", "content", "created_at"}
    assert general["preview"][-1]["author"] == "ann"


def test_active_scan_empty_when_no_active(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    guild = _FakeGuild(
        [
            _FakeChannel(
                "c1",
                "stale",
                "text",
                True,
                [_FakeMsg("m0", "ann", "old", now - timedelta(days=90))],
            ),
        ]
    )
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(guild))
    result = _discord.active_scan(123, since_days=30)
    assert result["probed_text_channels"] == 1
    assert result["active_channels"] == 0
    assert result["channels"] == []
