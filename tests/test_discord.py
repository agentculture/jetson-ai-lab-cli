"""Tests for the ``jlab discord`` noun group.

No network calls: all tests monkeypatch the adapter layer so nothing hits
Discord or requires a token.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import types
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
        lambda guild_id, since_days=30, fetch_limit=30, top=0, preview=5, **_kw: canned,
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
        lambda guild_id, since_days=30, fetch_limit=30, top=0, preview=5, **_kw: canned,
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
        lambda guild_id, since_days=30, fetch_limit=30, top=0, preview=5, **_kw: canned,
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
    def __init__(
        self,
        id: str,
        name: str,
        *,
        bot: bool = False,
        global_name: str | None = None,
        nick: str | None = None,
    ) -> None:
        self.id = id
        self.name = name
        self.bot = bot
        self.global_name = global_name
        self.nick = nick


class _FakeAttachment:
    def __init__(
        self,
        id: str,
        filename: str,
        url: str,
        *,
        content_type: str | None = None,
        size: int | None = None,
    ) -> None:
        self.id = id
        self.filename = filename
        self.url = url
        self.content_type = content_type
        self.size = size


class _FakeEmbedField:
    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.value = value


class _FakeEmbed:
    def __init__(
        self,
        *,
        type: str = "rich",
        url: str | None = None,
        title: str | None = None,
        description: str | None = None,
        fields: list | None = None,
    ) -> None:
        self.type = type
        self.url = url
        self.title = title
        self.description = description
        self.fields = fields or []


class _FakeThread:
    def __init__(self, id: str, name: str) -> None:
        self.id = id
        self.name = name


class _FakeMsg:
    def __init__(
        self,
        id: str,
        author: str,
        content: str,
        created_at: datetime,
        *,
        bot: bool = False,
        global_name: str | None = None,
        nick: str | None = None,
        attachments: list | None = None,
        embeds: list | None = None,
        thread: _FakeThread | None = None,
        jump_url: str | None = None,
    ) -> None:
        self.id = id
        self.author = _FakeAuthor(id + "a", author, bot=bot, global_name=global_name, nick=nick)
        self.content = content
        self.created_at = created_at
        self.attachments = attachments or []
        self.embeds = embeds or []
        self.thread = thread
        if jump_url is not None:
            self.jump_url = jump_url


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
    def __init__(self, guild: _FakeGuild, channel: _FakeChannel | None = None) -> None:
        self._guild = guild
        self._channel = channel

    async def fetch_guild(self, _gid: int) -> _FakeGuild:
        return self._guild

    async def fetch_channel(self, _cid: int) -> _FakeChannel:
        return self._channel


class _FakeSeam:
    """Stand-in for discord_bot_cli.discord_client.

    ``run`` executes the action against a fake client; ``parse_id`` mimics the
    upstream id parser (override via *id_parser* to exercise error paths).
    """

    def __init__(
        self,
        guild: _FakeGuild | None = None,
        channel: _FakeChannel | None = None,
        id_parser=None,
    ) -> None:
        self._guild = guild
        self._channel = channel
        self._id_parser = id_parser or (lambda value, label: int(value))

    def run(self, action):
        return asyncio.run(action(_FakeClient(self._guild, self._channel)))

    def parse_id(self, value: str, label: str) -> int:
        return self._id_parser(value, label)


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


# ---------------------------------------------------------------------------
# Adapter functions — exercised against the fake seam (real bodies, no mocks).
# ---------------------------------------------------------------------------


def test_seam_missing_extra_raises_env_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real _seam raises CliError(2) when discord_bot_cli is absent.

    The absence is simulated rather than inherited from the environment: a
    ``None`` entry in ``sys.modules`` makes the import raise ImportError. The
    optional [discord] extra IS installed in this repo's venv (so `jlab discord
    doctor` can reach Discord), and this test must assert the missing-extra
    contract either way.
    """
    monkeypatch.setitem(sys.modules, "discord_bot_cli", None)
    with pytest.raises(CliError) as exc:
        _discord._seam()
    assert exc.value.code == 2
    assert "discord-bot-cli" in exc.value.message
    assert exc.value.remediation


def test_list_channels_public_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    guild = _FakeGuild(
        [
            _FakeChannel("c1", "general", "text", True, []),
            _FakeChannel("c2", "secret", "text", False, []),
        ]
    )
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(guild))

    public = _discord.list_channels(123, public_only=True)
    assert [c["name"] for c in public] == ["general"]
    assert public[0]["public"] is True

    every = _discord.list_channels(123, public_only=False)
    assert {c["name"] for c in every} == {"general", "secret"}


def test_list_channels_unknown_perms_treated_nonpublic(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Boom(_FakeChannel):
        def permissions_for(self, _role: object):
            raise RuntimeError("perms unavailable")

    guild = _FakeGuild([_Boom("c9", "weird", "text", True, [])])
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(guild))
    # Unknown perms => public=None => dropped from the public-only default.
    assert _discord.list_channels(123, public_only=True) == []
    every = _discord.list_channels(123, public_only=False)
    assert every[0]["public"] is None


def test_read_messages_serializes(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    chan = _FakeChannel(
        "c1",
        "general",
        "text",
        True,
        [_FakeMsg("m0", "ann", "first", now), _FakeMsg("m1", "bob", "second", now)],
    )
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(channel=chan))
    msgs = _discord.read_messages(999, limit=5)
    assert [m["author"]["name"] for m in msgs] == ["ann", "bob"]  # oldest-first
    assert msgs[0]["content"] == "first"
    assert msgs[0]["created_at"] is not None


def test_read_messages_rejects_bad_limit() -> None:
    for bad in (0, 101):
        with pytest.raises(CliError) as exc:
            _discord.read_messages(999, limit=bad)
        assert exc.value.code == 1


def test_parse_id_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam())
    assert _discord.parse_id("42", "channel_id") == 42


def test_parse_id_invalid_translates_to_user_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(value: str, label: str) -> int:
        raise ValueError("not numeric")

    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(id_parser=boom))
    with pytest.raises(CliError) as exc:
        _discord.parse_id("abc", "channel_id")
    assert exc.value.code == 1
    assert "channel_id" in exc.value.message


def test_guild_id_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JLAB_GUILD_ID", "777")
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam())
    assert _discord._guild_id() == 777


def test_doctor_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    guild = _FakeGuild([_FakeChannel("c1", "general", "text", True, [])])
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(guild))
    assert _discord.doctor(123) == {"ok": True, "guild_id": "123"}


# ---------------------------------------------------------------------------
# _run — translate discord-bot-cli errors into the 0/1/2 jlab contract.
# ---------------------------------------------------------------------------


class _RaisingSeam:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def run(self, _action):
        raise self._exc


def _inject_fake_db_clierror(monkeypatch: pytest.MonkeyPatch):
    """Make ``discord_bot_cli.cli._errors.CliError`` importable (it's not installed)."""

    class _DBCliError(Exception):
        def __init__(self, code: int, message: str, remediation: str) -> None:
            super().__init__(message)
            self.code = code
            self.message = message
            self.remediation = remediation

    errors_mod = types.ModuleType("discord_bot_cli.cli._errors")
    errors_mod.CliError = _DBCliError
    monkeypatch.setitem(sys.modules, "discord_bot_cli", types.ModuleType("discord_bot_cli"))
    monkeypatch.setitem(sys.modules, "discord_bot_cli.cli", types.ModuleType("discord_bot_cli.cli"))
    monkeypatch.setitem(sys.modules, "discord_bot_cli.cli._errors", errors_mod)
    return _DBCliError


def test_run_preserves_upstream_clierror(monkeypatch: pytest.MonkeyPatch) -> None:
    """An upstream CliError (e.g. missing token) keeps its code 2 + remediation."""
    db_err = _inject_fake_db_clierror(monkeypatch)
    upstream = db_err(2, "DISCORD_BOT_TOKEN not set", "export DISCORD_BOT_TOKEN=...")
    monkeypatch.setattr(_discord, "_seam", lambda: _RaisingSeam(upstream))
    with pytest.raises(CliError) as exc:
        _discord.list_channels(123)
    assert exc.value.code == 2  # NOT mis-wrapped as exit 1
    assert exc.value.message == "DISCORD_BOT_TOKEN not set"
    assert exc.value.remediation == "export DISCORD_BOT_TOKEN=..."


def test_run_wraps_unknown_exception_as_env_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_discord, "_seam", lambda: _RaisingSeam(RuntimeError("boom")))
    with pytest.raises(CliError) as exc:
        _discord.list_channels(123)
    assert exc.value.code == 2
    assert "Discord request failed" in exc.value.message
    assert exc.value.remediation


def test_active_scan_tolerates_one_failing_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single channel whose history read fails must not abort the whole scan."""
    now = datetime.now(timezone.utc)

    class _BoomChannel(_FakeChannel):
        def history(self, limit: int):
            raise RuntimeError("cannot read history")

    guild = _FakeGuild(
        [
            _FakeChannel(
                "c1",
                "general",
                "text",
                True,
                [_FakeMsg("m0", "ann", "hi", now - timedelta(days=1))],
            ),
            _BoomChannel("c2", "flaky", "text", True, []),
        ]
    )
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(guild))
    result = _discord.active_scan(123, since_days=30, fetch_limit=5)
    assert result["probed_text_channels"] == 2  # both probed
    assert [c["name"] for c in result["channels"]] == ["general"]  # flaky tolerated, not ranked


def test_active_scan_top_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)

    def msgs(author: str, n: int) -> list:
        return [_FakeMsg(f"{author}{i}", author, "hi", now - timedelta(hours=i)) for i in range(n)]

    guild = _FakeGuild(
        [
            _FakeChannel("c1", "a", "text", True, msgs("a", 1)),
            _FakeChannel("c2", "b", "text", True, msgs("b", 3)),
        ]
    )
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(guild))
    result = _discord.active_scan(123, since_days=30, fetch_limit=10, top=1)
    assert [c["name"] for c in result["channels"]] == ["b"]  # only the top-1 most active


# ---------------------------------------------------------------------------
# _probe_channel — per-channel read status (CHANGED from the t1 pins).
#
# These tests drive `_probe_channel` straight, with a fake discord.py-shaped
# channel object, instead of going through `active_scan` (which the tests
# above either monkeypatch entirely — see test_discord_active_json at line
# ~258 and test_discord_active_probing_goes_to_stderr at ~267 — or drive with
# a fake *seam*/guild that never lets a caller see `_probe_channel`'s own
# return value).
#
# t1 pinned the OLD contract on purpose so this change would show up as a diff
# rather than silent drift. Two assertions below were deliberately rewritten:
#
#   * the bare `except Exception: msgs = []` swallow is gone — a failing read
#     is now `status="failed"` with a `reason`, and is distinguishable from an
#     empty channel (`status="ok"`, `message_count=0`);
#   * a serialized message's `author` is now a dict carrying the authoritative
#     `bot` flag and a display name, not a bare username string.
#
# `active_scan`'s own output shape is UNCHANGED — see
# test_discord_active_output_shape_unchanged below, still passing untouched:
# its preview entries still expose `author` as a plain display-name string.
# ---------------------------------------------------------------------------


def test_probe_channel_direct_reports_failed_status() -> None:
    """A channel.history() failure is reported, not swallowed into an empty list.

    This calls `_probe_channel` directly (not via `active_scan`/a stubbed
    seam) so the assertion is about `_probe_channel`'s own contract, not
    about how `active_scan` happens to aggregate its callees.
    """

    class _BoomChannel:
        id = "c-boom"
        name = "flaky"

        def history(self, limit: int):
            raise RuntimeError("cannot read history")

    result = asyncio.run(_discord._probe_channel(_BoomChannel(), fetch_limit=5))

    # The exception still does NOT propagate out of _probe_channel (one bad
    # channel must not abort a whole scan) — but it is no longer invisible.
    assert result["id"] == "c-boom"
    assert result["name"] == "flaky"
    assert result["messages"] == []
    assert result["message_count"] == 0
    assert result["status"] == _discord.STATUS_FAILED
    assert result["complete"] is False
    assert "cannot read history" in result["reason"]


def test_probe_channel_empty_channel_is_ok_not_failed() -> None:
    """An EMPTY channel reads `ok`; only a genuinely failed read reads `failed`."""
    chan = _FakeChannel("c-empty", "quiet", "text", True, [])
    result = asyncio.run(_discord._probe_channel(chan, fetch_limit=5))
    assert result["status"] == _discord.STATUS_OK
    assert result["message_count"] == 0
    assert result["reason"] is None
    assert result["complete"] is True


def test_probe_channel_direct_happy_path_serializes_messages() -> None:
    """Sanity companion to the failure case: a healthy channel serializes fine."""
    now = datetime.now(timezone.utc)
    chan = _FakeChannel(
        "c1",
        "general",
        "text",
        True,
        [_FakeMsg("m0", "ann", "hi", now - timedelta(days=1))],
    )

    result = asyncio.run(_discord._probe_channel(chan, fetch_limit=5))

    assert result["id"] == "c1"
    assert result["name"] == "general"
    # CHANGED from the t1 pin: `author` is a dict, not a bare name string.
    assert [m["author"]["name"] for m in result["messages"]] == ["ann"]
    assert result["messages"][0]["content"] == "hi"
    assert result["status"] == _discord.STATUS_OK


def test_discord_active_output_shape_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the exact shape of `active_scan`'s return value.

    A later task is expected to change `_probe_channel`'s contract (e.g. to
    surface per-channel failures instead of silently returning an empty
    message list). If that change also alters what `active_scan` returns —
    new top-level keys, new per-channel row keys — this test should fail and
    make that visible in the diff, rather than the shape drifting quietly.
    """
    now = datetime.now(timezone.utc)
    guild = _FakeGuild(
        [
            _FakeChannel(
                "c1",
                "general",
                "text",
                True,
                [_FakeMsg("m0", "ann", "hi", now - timedelta(days=1))],
            ),
        ]
    )
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(guild))

    result = _discord.active_scan(123, since_days=30, fetch_limit=5, top=0, preview=2)

    assert set(result) == {
        "guild_id",
        "since_days",
        "fetch_limit",
        "probed_text_channels",
        "active_channels",
        "channels",
    }
    row = result["channels"][0]
    assert set(row) == {
        "id",
        "name",
        "last_post",
        "msgs_in_window",
        "saturated",
        "preview",
    }
    preview_entry = row["preview"][0]
    assert set(preview_entry) == {"author", "content", "created_at"}


def test_active_scan_has_no_per_author_aggregation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Document a current gap: active_scan does not aggregate by author.

    Several authors post in the same channel below. `active_scan`'s ranked
    row only carries channel-level counters (`msgs_in_window`, `saturated`)
    and a short raw `preview` — there is no per-person/author breakdown
    (e.g. a message-count-per-author map, a "top poster", or a set of
    distinct authors) anywhere in the result. This test exists to make that
    gap visible; it should be revisited (not just re-asserted) if a future
    task adds author aggregation to `active_scan`.
    """
    now = datetime.now(timezone.utc)
    guild = _FakeGuild(
        [
            _FakeChannel(
                "c1",
                "general",
                "text",
                True,
                [
                    _FakeMsg("m0", "ann", "hi", now - timedelta(hours=3)),
                    _FakeMsg("m1", "bob", "hey", now - timedelta(hours=2)),
                    _FakeMsg("m2", "ann", "again", now - timedelta(hours=1)),
                ],
            ),
        ]
    )
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(guild))

    result = _discord.active_scan(123, since_days=30, fetch_limit=10, top=0, preview=10)

    row = result["channels"][0]
    # No aggregation keys of any kind — only channel-level counters + a raw
    # preview list. The only place an author name shows up at all is inside
    # each preview entry's "author" field (a copy of the raw message), never
    # rolled up into counts or a distinct-author set.
    aggregation_like_keys = {
        "authors",
        "author_counts",
        "unique_authors",
        "top_author",
        "participants",
        "per_author",
    }
    assert aggregation_like_keys.isdisjoint(row)
    assert row["msgs_in_window"] == 3  # channel-level count only, not per-author


# ---------------------------------------------------------------------------
# t2 — windowed paging, bounded concurrency, per-channel status, author.bot.
#
# Fake discord.py-shaped objects only; nothing here touches Discord.
# ---------------------------------------------------------------------------


class _WindowChannel:
    """A channel whose ``history`` honours ``after`` and records its calls.

    ``limit=None`` + ``after=<cutoff>`` is what discord.py paginates for us, so
    this fake yields *every* matching message regardless of the 100-cap — the
    behaviour the real paging relies on.
    """

    def __init__(
        self,
        id: str,
        name: str,
        messages: list,
        *,
        public: bool = True,
        type_name: str = "text",
    ) -> None:
        self.id = id
        self.name = name
        self.type = _FakeType(type_name)
        self._public = public
        self._messages = messages  # oldest-first
        self.history_calls: list[dict] = []

    def permissions_for(self, _role: object) -> _FakePerms:
        return _FakePerms(self._public)

    def history(self, limit=None, after=None):
        self.history_calls.append({"limit": limit, "after": after})
        selected = [m for m in self._messages if after is None or m.created_at > after]
        if limit is not None:
            selected = list(reversed(selected))[:limit]

        async def _gen():
            for m in selected:
                yield m

        return _gen()


def _window_msgs(n: int, *, author: str = "ann", bot: bool = False) -> list:
    now = datetime.now(timezone.utc)
    return [
        _FakeMsg(f"{author}{i}", author, f"m{i}", now - timedelta(minutes=n - i), bot=bot)
        for i in range(n)
    ]


# -- 1. windowed paging past the 100-message cap -----------------------------


def test_probe_channel_pages_window_past_the_100_cap() -> None:
    """`after=<cutoff>` with no limit reads the whole window, not the first 100."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    chan = _WindowChannel("c1", "general", _window_msgs(250))

    result = asyncio.run(_discord._probe_channel(chan, None, after=cutoff))

    assert result["message_count"] == 250  # well past the upstream 100 cap
    assert result["status"] == _discord.STATUS_OK
    assert result["complete"] is True
    # The window is expressed as an `after` cursor with no per-call limit.
    assert chan.history_calls == [{"limit": None, "after": cutoff}]


def test_probe_channel_honours_the_after_cutoff() -> None:
    """Messages older than the cutoff are excluded by the `after` cursor."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)
    chan = _WindowChannel(
        "c1",
        "general",
        [
            _FakeMsg("old", "ann", "old", now - timedelta(days=40)),
            _FakeMsg("new", "ann", "new", now - timedelta(days=1)),
        ],
    )
    result = asyncio.run(_discord._probe_channel(chan, None, after=cutoff))
    assert [m["content"] for m in result["messages"]] == ["new"]


def test_probe_channel_message_cap_marks_partial_not_silent_truncation() -> None:
    """A window that cannot be fully paged is `partial` — never silently cut."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    chan = _WindowChannel("c1", "busy", _window_msgs(500))

    result = asyncio.run(_discord._probe_channel(chan, None, after=cutoff, max_messages=100))

    assert result["message_count"] == 100
    assert result["status"] == _discord.STATUS_PARTIAL
    assert result["complete"] is False
    assert "cap" in result["reason"]


def test_probe_channel_orders_messages_oldest_first() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    chan = _WindowChannel("c1", "general", _window_msgs(5))
    result = asyncio.run(_discord._probe_channel(chan, None, after=cutoff))
    stamps = [m["created_at"] for m in result["messages"]]
    assert stamps == sorted(stamps)


# -- rate limits -------------------------------------------------------------


class _RateLimited(Exception):
    def __init__(self, retry_after: float = 0.25) -> None:
        super().__init__("429 Too Many Requests")
        self.status = 429
        self.retry_after = retry_after


class _RateLimitedOnceChannel(_WindowChannel):
    """Raises 429 on the first history call, then succeeds."""

    def history(self, limit=None, after=None):
        self.history_calls.append({"limit": limit, "after": after})
        if len(self.history_calls) == 1:

            async def _boom():
                raise _RateLimited()
                yield  # pragma: no cover

            return _boom()
        return super().history(limit=limit, after=after)


def test_probe_channel_retries_a_rate_limited_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 429 backs off and retries rather than reporting the channel failed."""
    slept: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(_discord, "_sleep", _fake_sleep)
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    chan = _RateLimitedOnceChannel("c1", "general", _window_msgs(3))

    result = asyncio.run(_discord._probe_channel(chan, None, after=cutoff))

    assert slept == [0.25]  # the server's own retry_after, honoured
    assert result["status"] == _discord.STATUS_OK
    assert result["message_count"] == 3


def test_probe_channel_reports_failed_when_rate_limit_never_clears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(_discord, "_sleep", _fake_sleep)

    class _AlwaysLimited(_WindowChannel):
        def history(self, limit=None, after=None):
            self.history_calls.append({"limit": limit, "after": after})

            async def _boom():
                raise _RateLimited()
                yield  # pragma: no cover

            return _boom()

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    chan = _AlwaysLimited("c1", "general", [])
    result = asyncio.run(_discord._probe_channel(chan, None, after=cutoff))
    assert result["status"] == _discord.STATUS_FAILED
    assert "429" in result["reason"]
    assert len(chan.history_calls) == 4  # initial try + 3 retries


def test_retry_after_ignores_non_rate_limit_errors() -> None:
    assert _discord._retry_after(RuntimeError("boom")) is None
    assert _discord._retry_after(_RateLimited(2.0)) == 2.0


# -- 2. bounded concurrency --------------------------------------------------


class _ConcurrencyProbeChannel(_WindowChannel):
    """Tracks how many history reads are in flight simultaneously."""

    def __init__(self, id: str, name: str, tracker: dict) -> None:
        super().__init__(id, name, _window_msgs(2))
        self._tracker = tracker

    def history(self, limit=None, after=None):
        tracker = self._tracker
        messages = list(self._messages)

        async def _gen():
            tracker["live"] += 1
            tracker["peak"] = max(tracker["peak"], tracker["live"])
            try:
                for m in messages:
                    await asyncio.sleep(0)
                    yield m
            finally:
                tracker["live"] -= 1

        return _gen()


def test_active_scan_bounds_channel_fan_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """No unbounded gather: in-flight channel reads never exceed --concurrency."""
    tracker = {"live": 0, "peak": 0}
    guild = _FakeGuild([_ConcurrencyProbeChannel(f"c{i}", f"ch{i}", tracker) for i in range(20)])
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(guild))

    result = _discord.active_scan(123, since_days=30, fetch_limit=5, concurrency=3)

    assert result["probed_text_channels"] == 20
    assert tracker["peak"] <= 3
    assert tracker["peak"] > 1  # genuinely concurrent, just capped


def test_scan_window_bounds_channel_fan_out(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = {"live": 0, "peak": 0}
    guild = _FakeGuild([_ConcurrencyProbeChannel(f"c{i}", f"ch{i}", tracker) for i in range(20)])
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(guild))

    result = _discord.scan_window(123, since_days=30, concurrency=2)

    assert result["concurrency"] == 2
    assert tracker["peak"] <= 2


def test_default_concurrency_is_conservative() -> None:
    assert _discord.DEFAULT_CONCURRENCY == 4


def test_bad_concurrency_is_a_user_error() -> None:
    for bad in (0, -1):
        with pytest.raises(CliError) as exc:
            _discord.active_scan(123, concurrency=bad)
        assert exc.value.code == 1
        with pytest.raises(CliError) as exc:
            _discord.scan_window(123, concurrency=bad)
        assert exc.value.code == 1


def test_scan_window_rejects_a_nonsense_window() -> None:
    with pytest.raises(CliError) as exc:
        _discord.scan_window(123, since_days=0)
    assert exc.value.code == 1


def test_discord_active_accepts_concurrency_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: dict = {}

    def _fake_scan(guild_id, **kwargs):
        seen.update(kwargs)
        return {
            "guild_id": str(guild_id),
            "since_days": 30,
            "fetch_limit": 30,
            "probed_text_channels": 0,
            "active_channels": 0,
            "channels": [],
        }

    monkeypatch.setattr("jlab.cli._discord._guild_id", lambda: _GUILD_ID)
    monkeypatch.setattr("jlab.cli._discord.active_scan", _fake_scan)
    assert main(["discord", "active", "--concurrency", "2", "--json"]) == 0
    assert seen["concurrency"] == 2
    assert json.loads(capsys.readouterr().out)


# -- 3. per-channel status through scan_window -------------------------------


def test_scan_window_distinguishes_failed_from_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed read and an empty channel are never conflated."""

    class _BoomChannel(_WindowChannel):
        def history(self, limit=None, after=None):
            raise RuntimeError("permission denied")

    guild = _FakeGuild(
        [
            _WindowChannel("c1", "general", _window_msgs(3)),
            _WindowChannel("c2", "empty", []),
            _BoomChannel("c3", "flaky", []),
            _WindowChannel("c4", "capped", _window_msgs(9)),
        ]
    )
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(guild))

    result = _discord.scan_window(123, since_days=30, max_messages_per_channel=4)
    by_name = {c["name"]: c for c in result["channels"]}

    assert by_name["general"]["status"] == _discord.STATUS_OK
    assert by_name["general"]["message_count"] == 3

    # Empty: ok, zero messages, no reason.
    assert by_name["empty"]["status"] == _discord.STATUS_OK
    assert by_name["empty"]["message_count"] == 0
    assert by_name["empty"]["reason"] is None

    # Failed: NOT ok, and carries why.
    assert by_name["flaky"]["status"] == _discord.STATUS_FAILED
    assert "permission denied" in by_name["flaky"]["reason"]

    # Partial: read some, but the window is truncated and says so.
    assert by_name["capped"]["status"] == _discord.STATUS_PARTIAL
    assert by_name["capped"]["complete"] is False

    assert result["channels_ok"] == 2
    assert result["channels_partial"] == 1
    assert result["channels_failed"] == 1
    assert result["complete"] is False  # coverage is never overstated


def test_scan_window_complete_when_every_channel_is_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild = _FakeGuild([_WindowChannel("c1", "general", _window_msgs(3))])
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(guild))
    result = _discord.scan_window(123, since_days=30)
    assert result["complete"] is True
    assert result["channels_failed"] == 0
    assert result["message_count"] == 3
    assert result["since_days"] == 30
    assert result["cutoff"]


# -- 4. author.bot + display name --------------------------------------------


def test_author_serialization_carries_bot_and_display_name() -> None:
    author = _FakeAuthor("a1", "annuser", bot=False, global_name="Ann", nick="Ann (JAL)")
    assert _discord._serialize_author(author) == {
        "id": "a1",
        "name": "annuser",
        "display_name": "Ann (JAL)",  # per-guild nick wins
        "bot": False,
    }


def test_author_display_name_falls_back_without_inventing() -> None:
    # global_name when there is no nick.
    assert (
        _discord._serialize_author(_FakeAuthor("a2", "bobuser", global_name="Bob"))["display_name"]
        == "Bob"
    )
    # username when there is neither — never a fabricated label.
    assert _discord._serialize_author(_FakeAuthor("a3", "eve"))["display_name"] == "eve"


def test_author_bot_flag_defaults_false_on_a_bare_user() -> None:
    class _Bare:
        id = 7
        name = "someone"

    assert _discord._serialize_author(_Bare()) == {
        "id": "7",
        "name": "someone",
        "display_name": "someone",
        "bot": False,
    }


def test_scan_window_excludes_bots_via_author_bot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bots are dropped on the authoritative flag, not a name heuristic."""
    now = datetime.now(timezone.utc)
    guild = _FakeGuild(
        [
            _WindowChannel(
                "c1",
                "general",
                [
                    # A HUMAN whose username looks bot-ish: a name heuristic
                    # would wrongly drop them; author.bot keeps them.
                    _FakeMsg("m0", "robotics-bot-fan", "hi", now - timedelta(minutes=3)),
                    _FakeMsg("m1", "ann", "hey", now - timedelta(minutes=2)),
                    # An actual bot, flagged as such.
                    _FakeMsg("m2", "Helper", "beep", now - timedelta(minutes=1), bot=True),
                ],
            )
        ]
    )
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(guild))

    result = _discord.scan_window(123, since_days=30)
    names = [m["author"]["name"] for m in result["channels"][0]["messages"]]
    assert names == ["robotics-bot-fan", "ann"]
    assert result["exclude_bots"] is True

    kept = _discord.scan_window(123, since_days=30, exclude_bots=False)
    assert len(kept["channels"][0]["messages"]) == 3


def test_read_messages_author_carries_bot_and_display_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `read` verb uses the same author serializer (additive keys only)."""
    now = datetime.now(timezone.utc)
    chan = _FakeChannel(
        "c1", "general", "text", True, [_FakeMsg("m0", "ann", "hi", now, global_name="Ann")]
    )
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(channel=chan))
    msgs = _discord.read_messages(999, limit=5)
    assert msgs[0]["author"]["bot"] is False
    assert msgs[0]["author"]["display_name"] == "Ann"
    assert msgs[0]["author"]["name"] == "ann"  # existing key preserved


# -- 5. public filtering happens BEFORE any message fetch --------------------


class _NeverFetchChannel(_WindowChannel):
    """A private channel: calling history() on it is a hard test failure."""

    def history(self, limit=None, after=None):  # pragma: no cover - must not run
        raise AssertionError("private channel history() must never be fetched")


def test_scan_window_never_fetches_a_private_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    guild = _FakeGuild(
        [
            _WindowChannel("c1", "general", _window_msgs(2)),
            _NeverFetchChannel("c2", "staff-only", [], public=False),
            _NeverFetchChannel("c3", "lounge", [], type_name="voice"),
        ]
    )
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(guild))

    result = _discord.scan_window(123, since_days=30)

    assert [c["name"] for c in result["channels"]] == ["general"]
    assert result["scanned_text_channels"] == 1
    # A private channel leaks neither its name nor a failed-row placeholder.
    assert "staff-only" not in json.dumps(result)


def test_active_scan_never_fetches_a_private_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    guild = _FakeGuild(
        [
            _WindowChannel("c1", "general", _window_msgs(2)),
            _NeverFetchChannel("c2", "staff-only", [], public=False),
        ]
    )
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(guild))

    result = _discord.active_scan(123, since_days=30, fetch_limit=5)

    assert result["probed_text_channels"] == 1
    assert "staff-only" not in json.dumps(result)


# -- 6. the workaround stays isolated in one file ----------------------------


def test_upstream_workaround_is_isolated_to_the_adapter() -> None:
    """discord-bot-cli#14's replacement must be a single-file change.

    Nothing outside `jlab/cli/_discord.py` may touch `author.bot`, a display
    name, or `history(after=...)` directly.
    """
    root = pathlib.Path(_discord.__file__).resolve().parents[2]
    adapter = pathlib.Path(_discord.__file__).resolve()
    needles = ("author.bot", "global_name", ".history(")
    # jlab/members/resolve.py is an intentional exemption. It reads
    # ``Member.global_name`` off a ``guild.fetch_member`` result — a DIFFERENT
    # seam from the message-serialization workaround this guard protects.
    # discord-bot-cli#14 replaces the adapter's message fields; it does not
    # replace member lookup, so resolve.py is not part of that single-file swap.
    # Exempting it here is deliberate: without the exemption the only way to
    # pass was to split the string literal, which hides the usage from this
    # guard entirely and makes the code worse.
    exempt = {(root / "jlab" / "members" / "resolve.py").resolve()}
    offenders = []
    for path in (root / "jlab").rglob("*.py"):
        resolved = path.resolve()
        if resolved == adapter or resolved in exempt:
            continue
        text = path.read_text(encoding="utf-8")
        if any(n in text for n in needles):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []


# ---------------------------------------------------------------------------
# 7. link-bearing serialization (t2)
#
# `_serialize_message` gains KEYS ONLY: attachment urls, embed urls AND embed
# bodies, a jump link, the enclosing channel's identity, and a thread
# reference. Nothing existing is renamed or removed, so `--json` consumers of
# channels/read/active keep working.
# ---------------------------------------------------------------------------


_LEGACY_MESSAGE_KEYS = {"id", "author", "content", "created_at"}


def _serialize(msg, channel=None) -> dict:
    return _discord._serialize_message(msg, channel=channel)


def test_serialize_message_keeps_every_legacy_key() -> None:
    """Additive only: the four pre-existing keys survive with their meaning."""
    now = datetime.now(timezone.utc)
    msg = _FakeMsg("m0", "ann", "hi there", now)
    out = _serialize(msg)
    assert _LEGACY_MESSAGE_KEYS <= set(out)
    assert out["id"] == "m0"
    assert out["content"] == "hi there"
    assert out["created_at"] == now.isoformat()
    assert out["author"]["name"] == "ann"


def test_serialize_message_carries_attachment_urls() -> None:
    now = datetime.now(timezone.utc)
    msg = _FakeMsg(
        "m1",
        "ann",
        "see this",
        now,
        attachments=[
            _FakeAttachment(
                "a1", "log.txt", "https://cdn.discordapp.com/x/log.txt", content_type="text/plain"
            )
        ],
    )
    out = _serialize(msg)
    assert [a["url"] for a in out["attachments"]] == ["https://cdn.discordapp.com/x/log.txt"]
    assert out["attachments"][0]["filename"] == "log.txt"
    assert out["attachments"][0]["id"] == "a1"


def test_serialize_message_no_attachments_is_empty_list_not_none() -> None:
    out = _serialize(_FakeMsg("m2", "ann", "plain", datetime.now(timezone.utc)))
    assert out["attachments"] == []
    assert out["embeds"] == []


def test_serialize_message_carries_embed_url() -> None:
    """`type=link`/`type=article` embeds do carry a url (t1 probe)."""
    msg = _FakeMsg(
        "m3",
        "ann",
        "https://example.org/post",
        datetime.now(timezone.utc),
        embeds=[
            _FakeEmbed(type="article", url="https://example.org/post", title="A post"),
        ],
    )
    out = _serialize(msg)
    assert out["embeds"][0]["url"] == "https://example.org/post"
    assert out["embeds"][0]["type"] == "article"


def test_serialize_message_carries_embed_description_and_field_bodies() -> None:
    """Load-bearing (t1 probe): `type=rich` embeds have url=None.

    Their links live in the description / field values, so a serializer that
    only carried `embeds[].url` would silently drop every rich-embed link.
    """
    msg = _FakeMsg(
        "m4",
        "bot-ish",
        "",
        datetime.now(timezone.utc),
        embeds=[
            _FakeEmbed(
                type="rich",
                url=None,
                title="Release",
                description="notes at https://example.org/rich-body",
                fields=[_FakeEmbedField("Docs", "https://example.org/field-body")],
            )
        ],
    )
    embed = _serialize(msg)["embeds"][0]
    assert embed["url"] is None
    assert "https://example.org/rich-body" in embed["description"]
    assert embed["fields"] == [{"name": "Docs", "value": "https://example.org/field-body"}]


def test_serialize_message_thread_reference_present_and_empty() -> None:
    """A thread renders a reference; no thread renders an EMPTY one."""
    now = datetime.now(timezone.utc)
    with_thread = _serialize(
        _FakeMsg("m5", "ann", "q?", now, thread=_FakeThread("t1", "how do I flash?"))
    )
    assert with_thread["thread"] == {"id": "t1", "name": "how do I flash?"}

    without = _serialize(_FakeMsg("m6", "ann", "no thread", now))
    # Empty reference — not a placeholder string, not None-with-a-label, not
    # an error. Falsy so consumers can branch on it directly.
    assert without["thread"] == {}
    assert not without["thread"]


def test_serialize_message_jump_url_prefers_the_objects_own() -> None:
    msg = _FakeMsg(
        "m7",
        "ann",
        "hi",
        datetime.now(timezone.utc),
        jump_url="https://discord.com/channels/1/2/m7",
    )
    assert _serialize(msg)["jump_url"] == "https://discord.com/channels/1/2/m7"


def test_serialize_message_jump_url_is_none_when_unconstructable() -> None:
    """No `jump_url` and no guild context => None, never a fabricated link."""
    assert _serialize(_FakeMsg("m8", "ann", "hi", datetime.now(timezone.utc)))["jump_url"] is None


def test_serialize_message_carries_channel_identity() -> None:
    now = datetime.now(timezone.utc)
    chan = _FakeChannel("c1", "general", "text", True, [])
    out = _serialize(_FakeMsg("m9", "ann", "hi", now), channel=chan)
    assert out["channel"] == {"id": "c1", "name": "general"}


def test_serialize_message_channel_identity_is_empty_without_a_channel() -> None:
    out = _serialize(_FakeMsg("m10", "ann", "hi", datetime.now(timezone.utc)))
    assert out["channel"] == {}


def test_probe_channel_messages_name_their_channel() -> None:
    """`_channel_row`'s channel identity reaches every message it carries."""
    now = datetime.now(timezone.utc)
    chan = _FakeChannel(
        "c42",
        "jetson-help",
        "text",
        True,
        [_FakeMsg("m0", "ann", "hi", now - timedelta(days=1))],
    )
    row = asyncio.run(_discord._probe_channel(chan, fetch_limit=5))
    assert row["id"] == "c42"
    assert row["messages"][0]["channel"] == {"id": "c42", "name": "jetson-help"}
    assert row["messages"][0]["jump_url"] is None


def test_read_messages_carries_links_and_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """The `read` verb goes through the same serializer."""
    now = datetime.now(timezone.utc)
    chan = _FakeChannel(
        "c1",
        "general",
        "text",
        True,
        [
            _FakeMsg(
                "m0",
                "ann",
                "docs",
                now,
                attachments=[_FakeAttachment("a1", "n.png", "https://cdn/n.png")],
                embeds=[_FakeEmbed(type="link", url="https://example.org")],
                jump_url="https://discord.com/channels/1/c1/m0",
            )
        ],
    )
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(channel=chan))
    msg = _discord.read_messages(999, limit=5)[0]
    assert msg["attachments"][0]["url"] == "https://cdn/n.png"
    assert msg["embeds"][0]["url"] == "https://example.org"
    assert msg["jump_url"] == "https://discord.com/channels/1/c1/m0"
    assert msg["channel"] == {"id": "c1", "name": "general"}
    assert msg["content"] == "docs"  # legacy key unchanged


def test_serialize_message_survives_objects_missing_the_new_fields() -> None:
    """A bare object (no attachments/embeds/thread attrs) must not raise."""

    class _Bare:
        id = "b1"
        content = "hi"
        created_at = None
        author = _FakeAuthor("a1", "ann")

    out = _serialize(_Bare())
    assert out["attachments"] == []
    assert out["embeds"] == []
    assert out["thread"] == {}
    assert out["jump_url"] is None


def test_serialize_message_jump_url_built_from_ids_when_absent() -> None:
    """Without `jump_url`, build the canonical link from guild/channel/msg ids."""

    class _GuildRef:
        id = "9"

    chan = _FakeChannel("c1", "general", "text", True, [])
    chan.guild = _GuildRef()
    out = _serialize(_FakeMsg("m11", "ann", "hi", datetime.now(timezone.utc)), channel=chan)
    assert out["jump_url"] == "https://discord.com/channels/9/c1/m11"
