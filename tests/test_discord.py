"""Tests for the ``jlab discord`` noun group.

No network calls: all tests monkeypatch the adapter layer so nothing hits
Discord or requires a token.
"""

from __future__ import annotations

import json

import pytest

from jlab.cli import main
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
