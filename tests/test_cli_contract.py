"""CLI contract for every ``discord`` verb (t14, obligation o9).

Every list here is DERIVED — from the real parser, the module docstring, the
``discord overview --json`` payload and the explain catalog — never hard-coded,
so a verb added later without its contract fails a test instead of drifting.
No test calls a verb in a way that could reach Discord or MongoDB: ``--json``
support is read off the parser, and every error case is rejected offline.
"""

from __future__ import annotations

import argparse
import json
import re

import pytest

from jlab.cli import _build_parser, main
from jlab.cli._commands import discord as discord_cmd
from jlab.explain.catalog import ENTRIES


def _discord_subparsers() -> dict[str, argparse.ArgumentParser]:
    for action in _build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction) and "discord" in action.choices:
            noun = action.choices["discord"]
            for sub in noun._actions:
                if isinstance(sub, argparse._SubParsersAction):
                    return dict(sub.choices)
    raise AssertionError("the parser registers no discord noun group")


VERBS = sorted(_discord_subparsers())

#: Input each verb rejects before any network or database access, beyond the
#: unknown-flag case every verb gets. Keys must be registered verbs.
SEMANTIC_ERRORS: dict[str, list[list[str]]] = {
    "read": [[]],  # missing channel id
    "fetch": [[]],  # missing channel id
    "search": [["123"]],  # missing --grep
    "coverage": [["abc def"], ["123", "--since", "2026-09-01T00:00:00+00:00"]],
    "purge": [[], ["--author", "42", "--channel", "7"]],
}

#: Verbs whose argument parsing accepts anything at all, so no offline
#: user-input error exists for them. Must stay empty unless justified here.
NO_OFFLINE_ERROR: dict[str, str] = {}


def _exit_code(argv: list[str]) -> int:
    """``main``'s exit code, whether it returns it or a parse error raises SystemExit."""
    try:
        return int(main(argv) or 0)
    except SystemExit as exc:
        return int(exc.code or 0)


def _error_cases() -> list[tuple[str, list[str]]]:
    cases = [(verb, ["--no-such-flag"]) for verb in VERBS if verb not in NO_OFFLINE_ERROR]
    for verb, arg_sets in SEMANTIC_ERRORS.items():
        cases.extend((verb, args) for args in arg_sets)
    return cases


def test_the_discord_noun_has_verbs() -> None:
    assert {"fetch", "search", "read", "coverage", "sweep", "purge"} <= set(VERBS)


def test_error_case_tables_name_only_registered_verbs() -> None:
    assert set(SEMANTIC_ERRORS) <= set(VERBS)
    assert set(NO_OFFLINE_ERROR) <= set(VERBS)
    assert set(VERBS) - set(NO_OFFLINE_ERROR), "every verb exempted leaves nothing tested"


@pytest.mark.parametrize("verb", VERBS)
def test_every_discord_verb_has_a_catalog_entry(verb: str) -> None:
    assert ("discord", verb) in ENTRIES
    assert main(["explain", "discord", verb]) == 0


@pytest.mark.parametrize("verb", VERBS)
def test_every_discord_verb_accepts_json(verb: str) -> None:
    options = {s for a in _discord_subparsers()[verb]._actions for s in a.option_strings}
    assert "--json" in options


@pytest.mark.parametrize(("verb", "args"), _error_cases())
def test_a_user_input_error_in_text_mode_is_error_and_hint_on_stderr(
    verb: str, args: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert _exit_code(["discord", verb, *args]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error:")
    assert "\nhint:" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(("verb", "args"), _error_cases())
def test_a_user_input_error_in_json_mode_is_one_json_object_on_stderr(
    verb: str, args: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert _exit_code(["discord", verb, *args, "--json"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert set(payload) == {"code", "message", "remediation"}
    assert payload["code"] == 1


def _docstring_verbs() -> set[str]:
    doc = discord_cmd.__doc__ or ""
    match = re.search(r"Verbs:(.*?)\n\n", doc, re.S)
    assert match, "the module docstring has no 'Verbs:' paragraph"
    return {
        v.strip().rstrip(".") for v in match.group(1).replace("\n", " ").split(",") if v.strip()
    }


def _overview_verbs(capsys: pytest.CaptureFixture[str]) -> set[str]:
    assert main(["discord", "overview", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    sections = payload.get("sections", payload) if isinstance(payload, dict) else payload
    items = next(s["items"] for s in sections if s.get("title") == "Verbs")
    return {item.split()[0] for item in items}


def _catalog_verbs() -> set[str]:
    root = ENTRIES[("discord",)]
    section = root.split("## Verbs", 1)[1].split("\n## ", 1)[0]
    return set(re.findall(r"^- `jetson-ai-lab-cli discord (\w+)", section, re.M))


def test_docstring_lists_every_registered_verb() -> None:
    assert _docstring_verbs() == set(VERBS)


def test_overview_lists_every_registered_verb(capsys: pytest.CaptureFixture[str]) -> None:
    assert _overview_verbs(capsys) == set(VERBS)


def test_catalog_root_lists_every_registered_verb() -> None:
    assert _catalog_verbs() == set(VERBS)
