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
