"""Tests for ``jlab discord members`` — the CLI wiring of the members pipeline.

No network calls: ``_discord.scan_window`` and ``resolve.resolve_authors``
are monkeypatched at the source, exactly like ``tests/test_discord.py``
monkeypatches ``active_scan``. This file is deliberately separate from
``tests/test_discord.py`` (which pins sibling tasks' guards) per the
operator's instruction.

The load-bearing property under test (c24/h33): ``--json`` must emit the
id-only aggregate and must NEVER call ``resolve_authors`` — display names
must not be reachable via stdout redirection.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jlab.cli import _discord, main
from jlab.cli._errors import CliError
from jlab.members.resolve import ResolvedAuthor, ResolveResult

_GUILD_ID = 1326246312072581160


def _canned_scan(*, guild_id=_GUILD_ID, since_days=90, author_ids=("111", "222")) -> dict:
    channels = [
        {
            "id": "ch1",
            "name": "general",
            "status": "ok",
            "reason": None,
            "messages": [
                {
                    "id": "m1",
                    "author": {"id": author_ids[0], "bot": False},
                    "content": "hello there?",
                    "created_at": "2026-06-01T00:00:00+00:00",
                },
            ],
        },
    ]
    if len(author_ids) > 1:
        channels.append(
            {
                "id": "ch2",
                "name": "help",
                "status": "ok",
                "reason": None,
                "messages": [
                    {
                        "id": "m2",
                        "author": {"id": author_ids[1], "bot": False},
                        "content": "hi",
                        "created_at": "2026-06-02T00:00:00+00:00",
                    },
                ],
            }
        )
    return {
        "guild_id": str(guild_id),
        "since_days": since_days,
        "cutoff": "2026-03-03T00:00:00+00:00",
        "scanned_text_channels": len(channels),
        "channels_ok": len(channels),
        "channels_partial": 0,
        "channels_failed": 0,
        "complete": True,
        "message_count": sum(len(c["messages"]) for c in channels),
        "channels": channels,
    }


def _canned_resolve(author_ids, *, include_departed=False, departed=frozenset()) -> ResolveResult:
    resolved = {}
    for aid in author_ids:
        if aid in departed:
            resolved[aid] = ResolvedAuthor(id=aid, status="departed", member=False)
        else:
            resolved[aid] = ResolvedAuthor(
                id=aid,
                status="ok",
                member=True,
                display_name=f"Person {aid}",
                username=f"user{aid}",
            )
    return ResolveResult(
        guild_id=str(_GUILD_ID),
        total_authors=len(author_ids),
        include_departed=include_departed,
        resolved=resolved,
    )


# ---------------------------------------------------------------------------
# --json is id-only: the c24/h33 containment guard.
# ---------------------------------------------------------------------------


def test_discord_members_json_is_id_only(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)
    monkeypatch.setattr(_discord, "scan_window", lambda *a, **kw: _canned_scan())

    def _boom(*_a, **_kw):
        raise AssertionError("resolve_authors must never be called in --json mode")

    monkeypatch.setattr("jlab.cli._commands.discord._resolve_mod.resolve_authors", _boom)
    monkeypatch.setattr(
        "jlab.cli._commands.discord._report_mod.write_report",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("write_report must never be called in --json mode")
        ),
    )

    rc = main(["discord", "members", "--json"])
    assert rc == 0

    raw_out = capsys.readouterr().out
    payload = json.loads(raw_out)

    # id-only aggregate shape.
    assert payload["guild_id"] == str(_GUILD_ID)
    assert {m["author_id"] for m in payload["members"]} == {"111", "222"}
    for member in payload["members"]:
        assert set(member.keys()) == {
            "author_id",
            "message_count",
            "distinct_channels",
            "question_starts",
            "substance",
        }

    # No name ever appears anywhere in the raw JSON text.
    assert "display_name" not in raw_out
    assert "username" not in raw_out
    assert "Person 111" not in raw_out
    assert "Person 222" not in raw_out


# ---------------------------------------------------------------------------
# default (render) path — one invocation, prints the report path.
# ---------------------------------------------------------------------------


def test_discord_members_default_writes_report_and_prints_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)
    monkeypatch.setattr(_discord, "scan_window", lambda *a, **kw: _canned_scan())
    monkeypatch.setattr(
        "jlab.cli._commands.discord._resolve_mod.resolve_authors",
        lambda guild_id, stats, **kw: _canned_resolve(list(stats.keys())),
    )

    written_path = tmp_path / "members-report.html"
    calls: list[dict] = []

    def _fake_write_report(aggregate, **kwargs):
        calls.append({"aggregate": aggregate, **kwargs})
        written_path.write_text("<html></html>", encoding="utf-8")
        return written_path

    monkeypatch.setattr(
        "jlab.cli._commands.discord._report_mod.write_report",
        _fake_write_report,
    )

    rc = main(["discord", "members"])
    assert rc == 0

    out = capsys.readouterr().out.strip()
    assert out == str(written_path)
    assert len(calls) == 1
    # names are present on the render path.
    resolved = calls[0]["resolved"]
    assert resolved["111"]["display_name"] == "Person 111"


def test_discord_members_since_defaults_to_90_days(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen = {}

    def _fake_scan_window(guild_id, since_days=None, **kw):
        seen["since_days"] = since_days
        return _canned_scan(since_days=since_days)

    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)
    monkeypatch.setattr(_discord, "scan_window", _fake_scan_window)

    rc = main(["discord", "members", "--json"])
    assert rc == 0
    assert seen["since_days"] == 90
    assert _discord.DEFAULT_WINDOW_DAYS == 90


def test_discord_members_since_flag_overrides_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = {}

    def _fake_scan_window(guild_id, since_days=None, **kw):
        seen["since_days"] = since_days
        return _canned_scan(since_days=since_days)

    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)
    monkeypatch.setattr(_discord, "scan_window", _fake_scan_window)

    rc = main(["discord", "members", "--since", "14", "--json"])
    assert rc == 0
    assert seen["since_days"] == 14


# ---------------------------------------------------------------------------
# --include-departed vs the default exclusion.
# ---------------------------------------------------------------------------


def test_discord_members_default_excludes_departed_authors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)
    monkeypatch.setattr(_discord, "scan_window", lambda *a, **kw: _canned_scan())

    def _fake_resolve_authors(guild_id, stats, *, include_departed=False):
        return _canned_resolve(
            list(stats.keys()), include_departed=include_departed, departed={"222"}
        )

    monkeypatch.setattr(
        "jlab.cli._commands.discord._resolve_mod.resolve_authors",
        _fake_resolve_authors,
    )

    captured = {}

    def _fake_write_report(aggregate, **kwargs):
        captured["aggregate"] = aggregate
        captured["kwargs"] = kwargs
        out = tmp_path / "r.html"
        out.write_text("<html></html>", encoding="utf-8")
        return out

    monkeypatch.setattr(
        "jlab.cli._commands.discord._report_mod.write_report",
        _fake_write_report,
    )

    rc = main(["discord", "members"])
    assert rc == 0

    rendered_ids = {m["author_id"] for m in captured["aggregate"]["members"]}
    assert rendered_ids == {"111"}
    assert captured["kwargs"]["excluded_count"] == 1


def test_discord_members_include_departed_flag_keeps_everyone(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)
    monkeypatch.setattr(_discord, "scan_window", lambda *a, **kw: _canned_scan())

    def _fake_resolve_authors(guild_id, stats, *, include_departed=False):
        return _canned_resolve(
            list(stats.keys()), include_departed=include_departed, departed={"222"}
        )

    monkeypatch.setattr(
        "jlab.cli._commands.discord._resolve_mod.resolve_authors",
        _fake_resolve_authors,
    )

    captured = {}

    def _fake_write_report(aggregate, **kwargs):
        captured["aggregate"] = aggregate
        captured["kwargs"] = kwargs
        out = tmp_path / "r.html"
        out.write_text("<html></html>", encoding="utf-8")
        return out

    monkeypatch.setattr(
        "jlab.cli._commands.discord._report_mod.write_report",
        _fake_write_report,
    )

    rc = main(["discord", "members", "--include-departed"])
    assert rc == 0

    rendered_ids = {m["author_id"] for m in captured["aggregate"]["members"]}
    assert rendered_ids == {"111", "222"}
    assert captured["kwargs"]["excluded_count"] is None


# ---------------------------------------------------------------------------
# stdout/stderr split + diagnostics.
# ---------------------------------------------------------------------------


def test_discord_members_progress_goes_to_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)
    monkeypatch.setattr(_discord, "scan_window", lambda *a, **kw: _canned_scan())

    rc = main(["discord", "members", "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "scanning" in captured.err
    # the JSON result landed on stdout only.
    assert json.loads(captured.out)


def test_discord_members_resolving_diagnostic_on_render_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)
    monkeypatch.setattr(_discord, "scan_window", lambda *a, **kw: _canned_scan())
    monkeypatch.setattr(
        "jlab.cli._commands.discord._resolve_mod.resolve_authors",
        lambda guild_id, stats, **kw: _canned_resolve(list(stats.keys())),
    )
    out_path = tmp_path / "r.html"

    def _fake_write_report(aggregate, **kwargs):
        out_path.write_text("<html></html>", encoding="utf-8")
        return out_path

    monkeypatch.setattr(
        "jlab.cli._commands.discord._report_mod.write_report",
        _fake_write_report,
    )

    rc = main(["discord", "members"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "resolving" in captured.err
    assert captured.out.strip() == str(out_path)


# ---------------------------------------------------------------------------
# failures raise CliError (never a traceback).
# ---------------------------------------------------------------------------


def test_discord_members_scan_failure_becomes_clierror(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)

    def _boom(*_a, **_kw):
        raise CliError(code=2, message="discord unreachable", remediation="retry later")

    monkeypatch.setattr(_discord, "scan_window", _boom)

    rc = main(["discord", "members"])
    assert rc == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error: discord unreachable" in captured.err
    assert "hint: retry later" in captured.err


def test_discord_members_rejects_bad_since_via_scan_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # scan_window itself validates since_days >= 1 (CliError code 1); the
    # members verb must not swallow or re-wrap that into a traceback.
    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)

    rc = main(["discord", "members", "--since", "0"])
    assert rc == 1
