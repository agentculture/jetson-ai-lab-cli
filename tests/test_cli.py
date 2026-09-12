"""Smoke tests for the jetson-ai-lab-cli CLI entry point and its verbs."""

from __future__ import annotations

import argparse
import json

import pytest

from jlab import __version__
from jlab.cli import _build_parser, main
from jlab.explain import known_paths
from jlab.explain.catalog import ENTRIES


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_args_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main([])
    assert rc == 0
    assert "usage: jetson-ai-lab-cli" in capsys.readouterr().out


def test_unknown_command_errors(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["bogus"])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "hint:" in err


# --- whoami ---------------------------------------------------------------


def test_whoami_text(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["whoami"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "nick: jetson-ai-lab-cli" in out
    assert "backend: claude" in out
    assert "model:" in out


def test_whoami_json(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["whoami", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["nick"] == "jetson-ai-lab-cli"
    assert payload["version"] == __version__
    assert payload["backend"] == "claude"


# --- learn ----------------------------------------------------------------


def test_learn_text(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["learn"])
    assert rc == 0
    out = capsys.readouterr().out
    assert len(out) >= 200
    assert "jetson-ai-lab-cli" in out
    assert "Exit-code policy" in out
    assert "--json" in out
    assert "explain" in out


def test_learn_json(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["learn", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tool"] == "jetson-ai-lab-cli"
    assert payload["version"] == __version__
    assert payload["json_support"] is True


# --- explain --------------------------------------------------------------


def test_explain_root(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["explain"])
    assert rc == 0
    assert "# jetson-ai-lab-cli" in capsys.readouterr().out


def test_explain_self(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["explain", "jetson-ai-lab-cli"])
    assert rc == 0
    assert capsys.readouterr().out.startswith("#")


def test_explain_json(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["explain", "whoami", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["path"] == ["whoami"]
    assert "jetson-ai-lab-cli whoami" in payload["markdown"]


def test_explain_unknown_path_errors(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["explain", "nonexistent"])
    assert rc == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("error:")
    assert "hint:" in captured.err


def test_every_catalog_path_resolves(capsys: pytest.CaptureFixture[str]) -> None:
    for path in known_paths():
        rc = main(["explain", *path])
        assert rc == 0, f"explain {' '.join(path)} failed"
        capsys.readouterr()


def test_explain_discord_links_resolves(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["explain", "discord", "links"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "discord links" in out


def _registered_command_paths(
    parser: argparse.ArgumentParser,
) -> list[tuple[str, ...]]:
    """Recursively collect every command path the parser actually registers.

    Walks every ``argparse._SubParsersAction`` reachable from *parser*,
    recording one path per subparser choice (and recursing into it), so the
    result is every noun/verb path a user could actually invoke — the
    converse of ``known_paths()``, which only enumerates the catalog's own
    keys and therefore proves nothing about paths the catalog is missing.
    """
    paths: list[tuple[str, ...]] = [()]

    def _walk(current: argparse.ArgumentParser, prefix: tuple[str, ...]) -> None:
        for action in current._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            for name, subparser in action.choices.items():
                child_path = prefix + (name,)
                paths.append(child_path)
                _walk(subparser, child_path)

    _walk(parser, ())
    return paths


def test_every_registered_command_has_a_catalog_entry() -> None:
    """The converse of ``test_every_catalog_path_resolves``.

    That test only proves every CATALOG entry resolves; it says nothing
    about a verb that was registered on the parser but never given an
    entry (exactly the ``discord links`` gap this test was added to close).
    This walks the real, live parser and asserts every registered command
    path — including the root — is a key in ``ENTRIES``.
    """
    parser = _build_parser()
    missing = [path for path in _registered_command_paths(parser) if path not in ENTRIES]
    assert not missing, f"registered commands missing an explain entry: {missing}"


# --- discord coverage -------------------------------------------------------


def test_discord_coverage_text_no_window(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Coverage text mode without a window shows covered intervals."""
    import datetime as dt

    from jlab import coverage as _coverage
    from tests.test_coverage import _FakeCollection

    # Set up lock home to avoid real filesystem
    monkeypatch.setenv(_coverage.STATE_HOME_ENV, str(tmp_path))
    _coverage.release_all_locks()

    # Create fake collection and set up coverage records
    col = _FakeCollection()

    def fake_coverage_collection():
        from contextlib import contextmanager

        @contextmanager
        def _context():
            yield col

        return _context()

    monkeypatch.setattr("jlab.mongo.coverage_collection", fake_coverage_collection)

    # Set up coverage using the proper API
    UTC = dt.timezone.utc
    sep1 = dt.datetime(2026, 9, 1, tzinfo=UTC)
    sep5 = dt.datetime(2026, 9, 5, tzinfo=UTC)
    sep10 = dt.datetime(2026, 9, 10, tzinfo=UTC)
    sep15 = dt.datetime(2026, 9, 15, tzinfo=UTC)

    _coverage.widen_coverage("123456789", _coverage.Interval(sep1, sep5), collection=col)
    _coverage.widen_coverage("123456789", _coverage.Interval(sep10, sep15), collection=col)

    rc = main(["discord", "coverage", "123456789"])
    assert rc == 0
    captured = capsys.readouterr()
    out = captured.out
    assert "123456789" in out
    assert "2026-09-01" in out
    assert "2026-09-05" in out
    assert "2026-09-10" in out
    assert "2026-09-15" in out
    # Should not print a complete boolean without a window
    # (note that "completeness" in the explanatory text is fine)
    assert "Status: complete" not in out
    assert "Status: incomplete" not in out


def test_discord_coverage_json_no_window(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Coverage JSON mode without a window."""
    import datetime as dt

    from jlab import coverage as _coverage
    from tests.test_coverage import _FakeCollection

    monkeypatch.setenv(_coverage.STATE_HOME_ENV, str(tmp_path))
    _coverage.release_all_locks()

    col = _FakeCollection()

    def fake_coverage_collection():
        from contextlib import contextmanager

        @contextmanager
        def _context():
            yield col

        return _context()

    monkeypatch.setattr("jlab.mongo.coverage_collection", fake_coverage_collection)

    UTC = dt.timezone.utc
    sep1 = dt.datetime(2026, 9, 1, tzinfo=UTC)
    sep5 = dt.datetime(2026, 9, 5, tzinfo=UTC)
    _coverage.widen_coverage("123456789", _coverage.Interval(sep1, sep5), collection=col)

    rc = main(["discord", "coverage", "123456789", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["channel_id"] == "123456789"
    assert payload["window"] is None
    assert len(payload["covered"]) > 0


def test_discord_coverage_text_with_window(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Coverage text mode with a window shows covered and uncovered."""
    import datetime as dt

    from jlab import coverage as _coverage
    from tests.test_coverage import _FakeCollection

    monkeypatch.setenv(_coverage.STATE_HOME_ENV, str(tmp_path))
    _coverage.release_all_locks()

    col = _FakeCollection()

    def fake_coverage_collection():
        from contextlib import contextmanager

        @contextmanager
        def _context():
            yield col

        return _context()

    monkeypatch.setattr("jlab.mongo.coverage_collection", fake_coverage_collection)

    UTC = dt.timezone.utc
    sep1 = dt.datetime(2026, 9, 1, tzinfo=UTC)
    sep5 = dt.datetime(2026, 9, 5, tzinfo=UTC)
    _coverage.widen_coverage("123456789", _coverage.Interval(sep1, sep5), collection=col)

    rc = main(
        [
            "discord",
            "coverage",
            "123456789",
            "--since",
            "2026-09-01T00:00:00+00:00",
            "--until",
            "2026-09-15T00:00:00+00:00",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    # With a window, should show covered intervals
    assert "2026-09-01" in out
    # Should show both covered and uncovered, and a status
    assert "Status:" in out or "Covered:" in out


def test_discord_coverage_invalid_channel_id_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Coverage errors on invalid channel id with code 1."""
    rc = main(["discord", "coverage", "abc def"])
    assert rc == 1
    captured = capsys.readouterr()
    err = captured.err
    assert err.startswith("error:")
    assert "hint:" in err


def test_discord_coverage_lists_channels(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Coverage with no channel argument lists channels with coverage."""
    import datetime as dt

    from jlab import coverage as _coverage
    from tests.test_coverage import _FakeCollection

    monkeypatch.setenv(_coverage.STATE_HOME_ENV, str(tmp_path))
    _coverage.release_all_locks()

    # Extend FakeCollection with find() method
    col = _FakeCollection()
    col.find = lambda query: col.docs.values()

    def fake_coverage_collection():
        from contextlib import contextmanager

        @contextmanager
        def _context():
            yield col

        return _context()

    monkeypatch.setattr("jlab.mongo.coverage_collection", fake_coverage_collection)

    # Add coverage for two channels
    UTC = dt.timezone.utc
    sep1 = dt.datetime(2026, 9, 1, tzinfo=UTC)
    sep5 = dt.datetime(2026, 9, 5, tzinfo=UTC)
    _coverage.widen_coverage("111111111", _coverage.Interval(sep1, sep5), collection=col)
    _coverage.widen_coverage("222222222", _coverage.Interval(sep1, sep5), collection=col)

    rc = main(["discord", "coverage"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "111111111" in out
    assert "222222222" in out


def test_discord_coverage_never_prints_message_content(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Coverage output never includes message content, only metadata."""
    import datetime as dt

    from jlab import coverage as _coverage
    from tests.test_coverage import _FakeCollection

    monkeypatch.setenv(_coverage.STATE_HOME_ENV, str(tmp_path))
    _coverage.release_all_locks()

    col = _FakeCollection()

    def fake_coverage_collection():
        from contextlib import contextmanager

        @contextmanager
        def _context():
            yield col

        return _context()

    monkeypatch.setattr("jlab.mongo.coverage_collection", fake_coverage_collection)

    UTC = dt.timezone.utc
    sep1 = dt.datetime(2026, 9, 1, tzinfo=UTC)
    sep5 = dt.datetime(2026, 9, 5, tzinfo=UTC)
    _coverage.widen_coverage("123456789", _coverage.Interval(sep1, sep5), collection=col)

    rc = main(["discord", "coverage", "123456789"])
    assert rc == 0
    out = capsys.readouterr().out
    # Coverage output should contain timestamps and channel IDs, not message text
    assert "secret message" not in out.lower()
    assert "sensitive data" not in out.lower()
    # Should contain coverage metadata
    assert "channel" in out.lower()
    assert "coverage" in out.lower() or "2026-09" in out
