"""Tests for ``jlab discord links`` — the CLI wiring of the links pipeline (t10).

No network calls: ``_discord.scan_window`` is monkeypatched at the source
and, where a real ``resolve_authors`` batch is wanted, ``_discord._seam``
is faked exactly like ``tests/test_members_resolve.py`` does.

The load-bearing properties under test:

* ``--json`` emits the id-only extraction payload and returns **before**
  ``resolve_authors``, ``write_cache`` or the report writer is reachable —
  proven with fakes that raise if called, mirroring
  ``tests/test_members_cli.py::test_discord_members_json_is_id_only``;
* one invocation writes the whole artifact set (HTML + both CSVs) and
  prints only the path;
* a departed author keeps their link — the members verb's
  ``included_author_ids`` row filter is deliberately **not** applied here,
  because dropping a row here would delete a real link share.
"""

from __future__ import annotations

import asyncio
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import jlab.members.resolve as _resolve_source
from jlab.cli import _build_parser, _discord, main
from jlab.cli._commands import discord as discord_cmd
from jlab.cli._errors import CliError
from jlab.links import cache as links_cache
from jlab.links import paths as links_paths
from jlab.members.resolve import ResolvedAuthor, ResolveResult

_GUILD_ID = 1326246312072581160


# ---------------------------------------------------------------------------
# canned scan / resolve fixtures
# ---------------------------------------------------------------------------


def _message(
    mid: str,
    author_id: str,
    content: str,
    *,
    bot: bool = False,
    channel: dict | None = None,
    thread: dict | None = None,
    created_at: str = "2026-06-01T00:00:00+00:00",
) -> dict:
    return {
        "id": mid,
        "author": {"id": author_id, "bot": bot},
        "content": content,
        "created_at": created_at,
        "channel": channel or {"id": "ch1", "name": "general"},
        "thread": thread or {},
        "jump_url": f"https://discord.com/channels/{_GUILD_ID}/ch1/{mid}",
        "attachments": [],
        "embeds": [],
    }


def _canned_scan(*, since_days: int = 90, exclude_bots: bool = True) -> dict:
    messages = [
        _message("m1", "111", "look at https://example.com/a"),
        _message(
            "m2",
            "222",
            "and https://example.com/b too",
            channel={"id": "ch2", "name": "help"},
            thread={"id": "t9", "name": "a thread"},
            created_at="2026-06-02T00:00:00+00:00",
        ),
    ]
    if not exclude_bots:
        messages.append(_message("m3", "999", "bot said https://example.com/bot", bot=True))
    channels = [
        {
            "id": "ch1",
            "name": "general",
            "status": "ok",
            "reason": None,
            "messages": messages,
        },
    ]
    return {
        "guild_id": str(_GUILD_ID),
        "since_days": since_days,
        "cutoff": "2026-03-03T00:00:00+00:00",
        "scanned_text_channels": 7,
        "channels_ok": 5,
        "channels_partial": 1,
        "channels_failed": 1,
        "complete": False,
        "exclude_bots": exclude_bots,
        "message_count": len(messages),
        "channels": channels,
    }


def _canned_resolve(author_ids, *, departed=frozenset()) -> ResolveResult:
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
        include_departed=True,
        resolved=resolved,
    )


class _Recorder:
    """Stand-in for ``links.report.write_report`` that records its inputs."""

    def __init__(self, out_path: Path) -> None:
        self.out_path = out_path
        self.calls: list[dict] = []

    def __call__(self, scan_result, records, **kwargs):
        self.calls.append({"scan_result": scan_result, "records": list(records), **kwargs})
        self.out_path.write_text("<html></html>", encoding="utf-8")
        return self.out_path


def _patch_scan(monkeypatch: pytest.MonkeyPatch, scan=None) -> dict:
    seen: dict = {}
    result = scan if scan is not None else _canned_scan()

    def _fake_scan_window(guild_id, since_days=None, **kwargs):
        seen["guild_id"] = guild_id
        seen["since_days"] = since_days
        seen.update(kwargs)
        return result

    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)
    monkeypatch.setattr(_discord, "scan_window", _fake_scan_window)
    return seen


def _patch_resolve(monkeypatch: pytest.MonkeyPatch, *, departed=frozenset()) -> dict:
    seen: dict = {}

    def _fake_resolve_authors(guild_id, stats, **kwargs):
        seen["guild_id"] = guild_id
        seen["author_ids"] = list(stats.keys())
        seen.update(kwargs)
        return _canned_resolve(list(stats.keys()), departed=departed)

    monkeypatch.setattr(
        "jlab.cli._commands.discord._resolve_mod.resolve_authors",
        _fake_resolve_authors,
    )
    return seen


# ---------------------------------------------------------------------------
# --json is id-only under EVERY flag combination (the hard stop).
# ---------------------------------------------------------------------------


def _forbid_downstream(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(what):
        def _raise(*_a, **_kw):
            raise AssertionError(f"{what} must never be reachable from the --json path")

        return _raise

    monkeypatch.setattr(
        "jlab.cli._commands.discord._resolve_mod.resolve_authors",
        _boom("resolve_authors"),
    )
    monkeypatch.setattr(
        "jlab.cli._commands.discord._links_report_mod.write_report",
        _boom("write_report"),
    )
    monkeypatch.setattr(
        "jlab.cli._commands.discord._links_cache_mod.write_cache",
        _boom("write_cache"),
    )


@pytest.mark.parametrize(
    "extra",
    [
        [],
        ["--include-bots"],
        ["--since", "7"],
        ["--concurrency", "2"],
        ["--since", "7", "--concurrency", "2", "--include-bots"],
    ],
)
def test_discord_links_json_is_id_only(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    extra: list[str],
) -> None:
    _patch_scan(monkeypatch, _canned_scan(exclude_bots=False))
    _forbid_downstream(monkeypatch)

    rc = main(["discord", "links", "--json", *extra])
    assert rc == 0

    raw_out = capsys.readouterr().out
    payload = json.loads(raw_out)
    assert payload["guild_id"] == str(_GUILD_ID)
    assert {r["author_id"] for r in payload["records"]} >= {"111", "222"}
    for record in payload["records"]:
        assert set(record.keys()) == {
            "url",
            "channel",
            "timestamp",
            "thread",
            "author_id",
            "jump_url",
            "from_attachment",
        }

    # No name ever appears anywhere in the raw JSON text.
    assert "display_name" not in raw_out
    assert "username" not in raw_out
    assert "Person 111" not in raw_out


def test_discord_links_json_from_cache_is_also_id_only(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = links_paths.new_run_id()
    links_cache.write_cache(
        discord_cmd._cache_run_id(run_id),
        [
            {
                "url": "https://example.com/a",
                "channel": {"id": "ch1", "name": "general"},
                "timestamp": "2026-06-01T00:00:00+00:00",
                "thread": {},
                "author_id": "111",
                "jump_url": "https://discord.com/channels/1/2/3",
                "from_attachment": False,
            }
        ],
    )
    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)
    _forbid_downstream(monkeypatch)

    rc = main(["discord", "links", "--from-cache", run_id, "--json"])
    assert rc == 0
    raw_out = capsys.readouterr().out
    assert "display_name" not in raw_out
    assert json.loads(raw_out)["records"][0]["author_id"] == "111"


# ---------------------------------------------------------------------------
# One invocation, both artifacts; path-only stdout.
# ---------------------------------------------------------------------------


def test_discord_links_one_invocation_writes_html_and_csvs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No flags, no second command, no manual assembly: HTML + both CSVs."""
    _patch_scan(monkeypatch)
    _patch_resolve(monkeypatch)

    rc = main(["discord", "links"])
    assert rc == 0

    captured = capsys.readouterr()
    printed = captured.out.strip()
    assert "\n" not in printed  # ONLY the written path on stdout
    html_path = Path(printed)
    assert html_path.is_file()

    run_dir = html_path.parent
    flat_csv = run_dir / "links-report.csv"
    summary_csv = run_dir / "links-report-summary.csv"
    assert flat_csv.is_file()
    assert summary_csv.is_file()
    assert "example.com/a" in html_path.read_text(encoding="utf-8")
    # progress went to stderr only
    assert "scanning" in captured.err


def test_discord_links_rows_carry_all_six_fields(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_scan(monkeypatch)
    _patch_resolve(monkeypatch)

    assert main(["discord", "links"]) == 0
    html_path = Path(capsys.readouterr().out.strip())
    rows = list(csv.DictReader((html_path.parent / "links-report.csv").open(encoding="utf-8")))

    threaded = [r for r in rows if r["url"] == "https://example.com/b"][0]
    assert threaded["url"] == "https://example.com/b"
    assert threaded["channel_name"] == "help"
    assert threaded["shared_at"] == "2026-06-02T00:00:00+00:00"
    assert threaded["thread_name"] == "a thread"
    assert threaded["author_id"] == "222"
    assert threaded["author"] == "Person 222"
    assert threaded["jump_url"].startswith("https://discord.com/channels/")


def test_discord_links_passes_scan_result_through_for_coverage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Coverage figures are read off ``scan_window``'s own statuses."""
    scan = _canned_scan()
    _patch_scan(monkeypatch, scan)
    _patch_resolve(monkeypatch)
    recorder = _Recorder(tmp_path / "r.html")
    monkeypatch.setattr(
        "jlab.cli._commands.discord._links_report_mod.write_report",
        recorder,
    )

    assert main(["discord", "links"]) == 0
    assert recorder.calls[0]["scan_result"] is scan


def test_discord_links_coverage_figures_are_not_recomputed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_scan(monkeypatch)
    _patch_resolve(monkeypatch)

    assert main(["discord", "links"]) == 0
    html_path = Path(capsys.readouterr().out.strip())
    rows = list(csv.DictReader((html_path.parent / "links-report.csv").open(encoding="utf-8")))
    # 7 attempted / 5 ok / 1 partial / 1 failed come straight off the scan,
    # not from counting the single channel actually carrying messages.
    assert rows[0]["channels_attempted"] == "7"
    assert rows[0]["channels_ok"] == "5"
    assert rows[0]["channels_partial"] == "1"
    assert rows[0]["channels_failed"] == "1"


# ---------------------------------------------------------------------------
# resolve.py is imported, not copied.
# ---------------------------------------------------------------------------


def test_links_uses_the_shared_unchanged_resolve_module() -> None:
    assert discord_cmd._resolve_mod is _resolve_source
    assert discord_cmd._resolve_mod.resolve_authors.__module__ == "jlab.members.resolve"


def test_links_resolves_names_in_one_batch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_scan(monkeypatch)
    calls: list[list[str]] = []

    def _fake_resolve_authors(guild_id, stats, **kwargs):
        calls.append(list(stats.keys()))
        return _canned_resolve(list(stats.keys()))

    monkeypatch.setattr(
        "jlab.cli._commands.discord._resolve_mod.resolve_authors",
        _fake_resolve_authors,
    )
    monkeypatch.setattr(
        "jlab.cli._commands.discord._links_report_mod.write_report",
        _Recorder(tmp_path / "r.html"),
    )

    assert main(["discord", "links"]) == 0
    assert len(calls) == 1  # ONE batch, at render time
    assert sorted(calls[0]) == ["111", "222"]


def test_links_concurrency_flag_reaches_the_resolve_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_scan(monkeypatch)
    seen = _patch_resolve(monkeypatch)
    monkeypatch.setattr(
        "jlab.cli._commands.discord._links_report_mod.write_report",
        _Recorder(tmp_path / "r.html"),
    )

    assert main(["discord", "links", "--concurrency", "9"]) == 0
    assert seen["concurrency"] == 9


# ---------------------------------------------------------------------------
# A departed author keeps their link (NO included_author_ids row filter).
# ---------------------------------------------------------------------------


class _NotFound(Exception):
    """Stand-in for ``discord.NotFound``, matched by class name only."""


class _FakeMember:
    def __init__(self, name: str) -> None:
        self.name = name
        self.nick = None
        self.global_name = None
        self.joined_at = None


class _FakeGuild:
    def __init__(self, members: dict[int, _FakeMember | None]) -> None:
        self._members = members

    async def fetch_member(self, member_id: int):
        entry = self._members.get(member_id)
        if entry is None:
            raise _NotFound("unknown member")
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


def test_departed_author_keeps_their_link(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``fetch_member`` raising NotFound must not delete the link row."""
    _patch_scan(monkeypatch)
    # 222 has left the guild -> real resolve_authors marks them departed.
    guild = _FakeGuild({111: _FakeMember("annie"), 222: None})
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(guild))

    assert main(["discord", "links"]) == 0
    html_path = Path(capsys.readouterr().out.strip())
    html = html_path.read_text(encoding="utf-8")
    rows = list(csv.DictReader((html_path.parent / "links-report.csv").open(encoding="utf-8")))

    by_url = {r["url"]: r for r in rows}
    assert set(by_url) == {"https://example.com/a", "https://example.com/b"}
    departed_row = by_url["https://example.com/b"]
    assert departed_row["author_id"] == "222"
    # id present, NO display name invented for them.
    assert departed_row["author"] == "222"
    assert "https://example.com/b" in html
    # the resolved author still got their name.
    assert by_url["https://example.com/a"]["author"] == "annie"


# ---------------------------------------------------------------------------
# Bots: excluded by default, included behind --include-bots.
# ---------------------------------------------------------------------------


def test_links_excludes_bots_by_default(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen = _patch_scan(monkeypatch, _canned_scan(exclude_bots=False))

    assert main(["discord", "links", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert seen["exclude_bots"] is True
    assert all(r["author_id"] != "999" for r in payload["records"])


def test_links_include_bots_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen = _patch_scan(monkeypatch, _canned_scan(exclude_bots=False))

    assert main(["discord", "links", "--include-bots", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert seen["exclude_bots"] is False
    assert any(r["author_id"] == "999" for r in payload["records"])


# ---------------------------------------------------------------------------
# Window flags.
# ---------------------------------------------------------------------------


def test_links_since_defaults_to_the_shared_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _patch_scan(monkeypatch)
    assert main(["discord", "links", "--json"]) == 0
    assert seen["since_days"] == _discord.DEFAULT_WINDOW_DAYS == 90


def test_links_since_flag_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_scan(monkeypatch)
    assert main(["discord", "links", "--since", "14", "--json"]) == 0
    assert seen["since_days"] == 14


def test_links_concurrency_defaults_to_the_shared_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _patch_scan(monkeypatch)
    assert main(["discord", "links", "--json"]) == 0
    assert seen["concurrency"] == _discord.DEFAULT_CONCURRENCY


# ---------------------------------------------------------------------------
# Cache / regeneration.
# ---------------------------------------------------------------------------


def test_links_real_run_persists_the_extraction_cache(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_scan(monkeypatch)
    _patch_resolve(monkeypatch)

    assert main(["discord", "links"]) == 0
    captured = capsys.readouterr()
    run_id = Path(captured.out.strip()).parent.name

    payload = links_cache.load_cache(discord_cmd._cache_run_id(run_id))
    assert {r["url"] for r in payload["records"]} == {
        "https://example.com/a",
        "https://example.com/b",
    }
    # ids only in the cache, never names.
    assert "display_name" not in json.dumps(payload)
    # the run id is discoverable from stderr so a re-render is possible.
    assert run_id in captured.err


def test_links_from_cache_renders_without_scanning(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = links_paths.new_run_id()
    links_cache.write_cache(
        discord_cmd._cache_run_id(run_id),
        [
            {
                "url": "https://example.com/cached",
                "channel": {"id": "ch1", "name": "general"},
                "timestamp": "2026-06-01T00:00:00+00:00",
                "thread": {},
                "author_id": "111",
                "jump_url": "https://discord.com/channels/1/2/3",
                "from_attachment": False,
            }
        ],
    )

    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)

    def _no_scanning(*_a, **_kw):
        raise AssertionError("--from-cache must not open a Discord scan")

    monkeypatch.setattr(_discord, "scan_window", _no_scanning)
    _patch_resolve(monkeypatch)

    assert main(["discord", "links", "--from-cache", run_id]) == 0
    captured = capsys.readouterr()
    html_path = Path(captured.out.strip())
    assert html_path.is_file()
    assert "example.com/cached" in html_path.read_text(encoding="utf-8")
    assert (html_path.parent / "links-report.csv").is_file()
    assert run_id in captured.err


def test_links_from_cache_warns_when_attachment_urls_expired(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = links_paths.new_run_id()
    stale = datetime.now(timezone.utc) - timedelta(
        hours=links_cache.ATTACHMENT_URL_EXPIRY_HOURS + 6
    )
    links_cache.write_cache(discord_cmd._cache_run_id(run_id), [], scanned_at=stale)

    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)
    _patch_resolve(monkeypatch)

    assert main(["discord", "links", "--from-cache", run_id]) == 0
    err = capsys.readouterr().err
    assert "expir" in err.lower()


def test_links_from_cache_is_quiet_when_fresh(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = links_paths.new_run_id()
    links_cache.write_cache(
        discord_cmd._cache_run_id(run_id), [], scanned_at=datetime.now(timezone.utc)
    )
    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)
    _patch_resolve(monkeypatch)

    assert main(["discord", "links", "--from-cache", run_id]) == 0
    assert "expir" not in capsys.readouterr().err.lower()


def test_links_from_cache_render_matches_original_coverage_columns(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """t16 / d3: a --from-cache render states the SAME coverage as the run
    that wrote the cache -- no 'unknown' cells, no invented numbers.

    Proof: run once for real (capturing the CSV's coverage columns), then
    re-render from the cache with scan_window monkeypatched to explode, and
    assert the coverage columns come out identical.
    """
    _patch_scan(monkeypatch, _canned_scan())
    _patch_resolve(monkeypatch)

    assert main(["discord", "links"]) == 0
    first_out = capsys.readouterr()
    html_path = Path(first_out.out.strip())
    run_id = html_path.parent.name
    original_rows = list(
        csv.DictReader((html_path.parent / "links-report.csv").open(encoding="utf-8"))
    )
    original_coverage = [
        {
            k: r[k]
            for k in (
                "channels_attempted",
                "channels_ok",
                "channels_partial",
                "channels_failed",
                "coverage_complete",
                "window_start",
            )
        }
        for r in original_rows
    ]
    assert original_coverage  # sanity: rows exist
    assert "unknown" not in json.dumps(original_coverage)

    def _no_scanning(*_a, **_kw):
        raise AssertionError("--from-cache must not open a Discord scan")

    monkeypatch.setattr(_discord, "scan_window", _no_scanning)

    assert main(["discord", "links", "--from-cache", run_id]) == 0
    second_out = capsys.readouterr()
    cached_html_path = Path(second_out.out.strip())
    cached_rows = list(
        csv.DictReader((cached_html_path.parent / "links-report.csv").open(encoding="utf-8"))
    )
    cached_coverage = [
        {
            k: r[k]
            for k in (
                "channels_attempted",
                "channels_ok",
                "channels_partial",
                "channels_failed",
                "coverage_complete",
                "window_start",
            )
        }
        for r in cached_rows
    ]

    assert cached_coverage == original_coverage
    assert "unknown" not in json.dumps(cached_coverage)


def test_links_from_cache_missing_run_is_a_clierror(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)

    rc = main(["discord", "links", "--from-cache", "20260101T000000Z-deadbeef"])
    assert rc == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err
    assert "hint:" in captured.err
    assert "Traceback" not in captured.err


# ---------------------------------------------------------------------------
# Failures raise CliError, never a traceback.
# ---------------------------------------------------------------------------


def test_links_scan_failure_becomes_clierror(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)

    def _boom(*_a, **_kw):
        raise CliError(code=2, message="discord unreachable", remediation="retry later")

    monkeypatch.setattr(_discord, "scan_window", _boom)

    rc = main(["discord", "links"])
    assert rc == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error: discord unreachable" in captured.err
    assert "hint: retry later" in captured.err


def test_links_rejects_bad_since_via_scan_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)
    assert main(["discord", "links", "--since", "0"]) == 1


# ---------------------------------------------------------------------------
# Registration + help text.
# ---------------------------------------------------------------------------


def test_links_verb_is_registered_and_listed_in_the_noun_overview(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["discord", "overview", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    items = [item for section in payload["sections"] for item in section["items"]]
    assert any(item.startswith("links ") for item in items)


def test_links_help_mentions_its_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["discord", "links", "--help"])
    out = capsys.readouterr().out
    for flag in ("--since", "--concurrency", "--include-bots", "--from-cache", "--json"):
        assert flag in out


def _noun_verb_help(verb: str) -> str:
    parser = _build_parser()
    discord_parser = parser._subparsers._group_actions[0].choices["discord"]
    noun_sub = discord_parser._subparsers._group_actions[0]
    for action in noun_sub._choices_actions:
        if action.dest == verb:
            return action.help or ""
    raise AssertionError(f"{verb} is not registered under the discord noun")


def test_members_help_text_is_not_stale(capsys: pytest.CaptureFixture[str]) -> None:
    """r7: the stale ``members`` help said only "an id-only members HTML report".

    It writes an HTML report *and* a CSV, into a per-run subdirectory.
    """
    help_text = _noun_verb_help("members").lower()
    assert "csv" in help_text
    assert "html" in help_text


def test_links_verb_help_text_describes_both_artifacts() -> None:
    help_text = _noun_verb_help("links").lower()
    assert "csv" in help_text
    assert "html" in help_text


# ---------------------------------------------------------------------------
# Finding 5: a --from-cache render states the ORIGINAL scan's metadata, never
# the current environment's or the current flags'.
# ---------------------------------------------------------------------------

_OTHER_GUILD_ID = 999888777666555444


def test_from_cache_metadata_comes_from_the_cache_not_current_config(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Re-rendering a cache under a DIFFERENT guild id and a DIFFERENT
    --include-bots setting must still report what the original scan did.

    Before this was fixed, the guild id and the bot-inclusion line were
    rebuilt from the live environment and the current flags, and the window
    length and message count were omitted entirely -- a report asserting
    something it had never measured.
    """
    _patch_scan(monkeypatch, _canned_scan(since_days=90, exclude_bots=True))
    _patch_resolve(monkeypatch)

    assert main(["discord", "links"]) == 0
    run_id = Path(capsys.readouterr().out.strip()).parent.name

    def _no_scanning(*_a, **_kw):
        raise AssertionError("--from-cache must not open a Discord scan")

    monkeypatch.setattr(_discord, "scan_window", _no_scanning)
    monkeypatch.setattr(_discord, "_guild_id", lambda: _OTHER_GUILD_ID)

    # Different guild in the environment AND the opposite bot setting.
    assert main(["discord", "links", "--from-cache", run_id, "--include-bots"]) == 0
    html = Path(capsys.readouterr().out.strip()).read_text(encoding="utf-8")

    assert f"<dt>Discord guild id</dt><dd>{_GUILD_ID}</dd>" in html
    assert str(_OTHER_GUILD_ID) not in html
    assert "<dt>Bot and webhook authors</dt><dd>excluded</dd>" in html
    assert "<dt>Window length</dt><dd>90 days</dd>" in html
    assert "<dt>Messages considered</dt><dd>2</dd>" in html


def test_from_cache_metadata_is_unknown_for_a_pre_metadata_cache(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An old cache lacking the metadata renders ``unknown`` -- never invented."""
    run_id = links_paths.new_run_id()
    links_cache.write_cache(discord_cmd._cache_run_id(run_id), [])

    monkeypatch.setattr(_discord, "_guild_id", lambda: _OTHER_GUILD_ID)
    _patch_resolve(monkeypatch)

    assert main(["discord", "links", "--from-cache", run_id]) == 0
    html = Path(capsys.readouterr().out.strip()).read_text(encoding="utf-8")

    assert "<dt>Discord guild id</dt><dd>unknown</dd>" in html
    assert "<dt>Bot and webhook authors</dt><dd>unknown</dd>" in html
    assert "<dt>Window length</dt><dd>unknown days</dd>" in html
    assert "<dt>Messages considered</dt><dd>unknown</dd>" in html
    assert str(_OTHER_GUILD_ID) not in html


def test_from_cache_json_metadata_comes_from_the_cache(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_scan(monkeypatch, _canned_scan(since_days=90, exclude_bots=True))
    _patch_resolve(monkeypatch)
    assert main(["discord", "links"]) == 0
    run_id = Path(capsys.readouterr().out.strip()).parent.name

    monkeypatch.setattr(_discord, "_guild_id", lambda: _OTHER_GUILD_ID)
    _forbid_downstream(monkeypatch)

    rc = main(["discord", "links", "--from-cache", run_id, "--include-bots", "--json"])
    assert rc == 0
    raw_out = capsys.readouterr().out
    payload = json.loads(raw_out)

    assert payload["guild_id"] == str(_GUILD_ID)
    assert payload["since_days"] == 90
    assert payload["include_bots"] is False
    # still id-only.
    assert "display_name" not in raw_out


# ---------------------------------------------------------------------------
# Finding 6: a malformed cache is the user's environment, not our bug.
# ---------------------------------------------------------------------------


def _write_raw_cache(run_id: str, payload: object) -> None:
    path = links_paths.links_report_path(
        discord_cmd._cache_run_id(run_id), links_cache.CACHE_FILENAME
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"records": []}, id="missing-scanned_at"),
        pytest.param(["not", "an", "object"], id="wrong-top-level-type"),
        pytest.param({"scanned_at": "2026-06-01T00:00:00+00:00"}, id="missing-records"),
        pytest.param(
            {"scanned_at": "2026-06-01T00:00:00+00:00", "records": "nope"},
            id="records-not-a-list",
        ),
        pytest.param(
            {"scanned_at": "2026-06-01T00:00:00+00:00", "records": [1, 2]},
            id="records-not-objects",
        ),
        pytest.param(
            {"scanned_at": "not a timestamp", "records": []},
            id="unparseable-scanned_at",
        ),
        pytest.param({"scanned_at": 12345, "records": []}, id="scanned_at-not-a-string"),
        pytest.param(
            {"scanned_at": "2026-06-01T00:00:00+00:00", "records": [], "coverage": []},
            id="coverage-not-an-object",
        ),
        pytest.param(
            {"scanned_at": "2026-06-01T00:00:00+00:00", "records": [], "scan_meta": 7},
            id="scan_meta-not-an-object",
        ),
    ],
)
def test_malformed_cache_is_a_clean_user_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    payload: object,
) -> None:
    run_id = links_paths.new_run_id()
    _write_raw_cache(run_id, payload)
    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)

    rc = main(["discord", "links", "--from-cache", run_id])

    assert rc == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err
    assert "hint:" in captured.err
    assert "Traceback" not in captured.err
    # NOT the generic dispatcher's "file a bug" fallback.
    assert "unexpected" not in captured.err.lower()
    assert "KeyError" not in captured.err
    assert "TypeError" not in captured.err
    assert "AttributeError" not in captured.err


def test_cache_that_is_not_json_at_all_is_a_user_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = links_paths.new_run_id()
    path = links_paths.links_report_path(
        discord_cmd._cache_run_id(run_id), links_cache.CACHE_FILENAME
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json at all", encoding="utf-8")
    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)

    assert main(["discord", "links", "--from-cache", run_id]) == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "hint:" in err


# ---------------------------------------------------------------------------
# Finding 4: unreadable is an ENVIRONMENT failure (2); bad input stays (1).
# ---------------------------------------------------------------------------


def test_unreadable_cache_file_is_an_environment_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = links_paths.new_run_id()
    links_cache.write_cache(discord_cmd._cache_run_id(run_id), [])
    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)

    def _denied(*_a, **_kw):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(discord_cmd._links_cache_mod, "load_cache", _denied)

    rc = main(["discord", "links", "--from-cache", run_id])
    assert rc == 2
    err = capsys.readouterr().err
    assert "error:" in err
    assert "hint:" in err
    assert "Traceback" not in err


def test_missing_cache_stays_a_user_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)
    assert main(["discord", "links", "--from-cache", "20260101T000000Z-deadbeef"]) == 1


def test_malformed_run_id_is_a_user_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The run id comes from --from-cache: a bad one is bad input (1), not a
    broken environment (2)."""
    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)

    rc = main(["discord", "links", "--from-cache", "../../etc/passwd"])
    assert rc == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err
    assert "hint:" in captured.err


# ---------------------------------------------------------------------------
# Finding 3: artifact writes that fail on disk are sanitized CliErrors.
# ---------------------------------------------------------------------------

_RAW_OS_TEXT = "No space left on device: '/home/somebody/secret/path'"


def test_cache_write_failure_is_a_sanitized_environment_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_scan(monkeypatch)
    _patch_resolve(monkeypatch)

    def _boom(*_a, **_kw):
        raise OSError(28, _RAW_OS_TEXT)

    monkeypatch.setattr(discord_cmd._links_cache_mod, "write_cache", _boom)

    rc = main(["discord", "links"])
    assert rc == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err
    assert "hint:" in captured.err
    assert "Traceback" not in captured.err
    assert "unexpected" not in captured.err.lower()
    assert "secret" not in captured.err


def test_report_write_failure_is_a_sanitized_environment_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_scan(monkeypatch)
    _patch_resolve(monkeypatch)

    def _boom(*_a, **_kw):
        raise OSError(30, _RAW_OS_TEXT)

    monkeypatch.setattr(discord_cmd._links_report_mod, "write_report", _boom)

    rc = main(["discord", "links"])
    assert rc == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err
    assert "hint:" in captured.err
    assert "Traceback" not in captured.err
    assert "unexpected" not in captured.err.lower()
    assert "secret" not in captured.err


# ---------------------------------------------------------------------------
# SonarCloud: no implicitly concatenated string fragments in the cache hint.
# ---------------------------------------------------------------------------


def test_cache_error_hint_has_no_glued_words(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(_discord, "_guild_id", lambda: _GUILD_ID)
    run_id = links_paths.new_run_id()
    path = links_paths.links_report_path(
        discord_cmd._cache_run_id(run_id), links_cache.CACHE_FILENAME
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{", encoding="utf-8")

    assert main(["discord", "links", "--from-cache", run_id]) == 1
    err = capsys.readouterr().err
    assert "without--from-cache" not in err
    assert "without --from-cache" in err
