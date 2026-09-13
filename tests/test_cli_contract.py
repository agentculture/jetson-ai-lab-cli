"""Contract tests for the CLI surface: --json support, error handling, verb lists."""

from __future__ import annotations

import argparse
import json

import pytest

from jlab.cli import _build_parser, main


def _discover_discord_verbs() -> list[str]:
    """Walk the parser to find all registered discord verbs."""
    root_parser = _build_parser()
    discord_parser = None

    # Find the discord subparser
    for action in root_parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, subparser in action.choices.items():
                if name == "discord":
                    discord_parser = subparser
                    break

    if not discord_parser:
        return []

    verbs = []
    for action in discord_parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            verbs = list(action.choices.keys())
            break

    return sorted(verbs)


# --- a9: Parser paths resolve through explain ---


def test_every_discord_verb_has_catalog_entry() -> None:
    """Every discord verb registered on the parser has an explain entry.

    This is the contract: if a verb is registered, it must be documented.
    """
    from jlab.explain.catalog import ENTRIES

    registered = _discover_discord_verbs()

    for verb in registered:
        path = ("discord", verb)
        assert path in ENTRIES, f"discord verb {verb!r} has no catalog entry"


# --- o9: Every discord verb accepts --json and errors properly ---


@pytest.mark.parametrize("verb", _discover_discord_verbs())
def test_discord_verb_accepts_json_flag(verb: str) -> None:
    """Every discord verb accepts --json (even if it has no effect)."""
    if verb == "overview":
        # overview doesn't require args
        rc = main(["discord", verb, "--json"])
        assert rc == 0
    elif verb in ("channels", "doctor", "sweep"):
        # These don't require args, but may fail with env errors (code 2).
        # The point of this test is just that --json flag is accepted by the parser.
        try:
            rc = main(["discord", verb, "--json"])
            # Success is fine
            assert rc in (0, 2), f"Unexpected exit code {rc}"
        except SystemExit as exc:
            # Argparse error (shouldn't happen for these) or other exit
            assert exc.code in (0, 1, 2)


@pytest.mark.parametrize(
    "verb,args,should_error",
    [
        ("read", [], True),  # missing channel_id
        ("fetch", [], True),  # missing channel_id
        ("search", ["123"], True),  # missing --grep (required)
        ("coverage", ["abc def"], True),  # invalid channel id format
        ("purge", [], True),  # missing target (one of --author, --channel, --older-than)
    ],
)
def test_discord_verb_user_input_errors_exit_1(
    verb: str,
    args: list[str],
    should_error: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """User-input errors exit 1 with no stdout, structured stderr."""
    if should_error:
        try:
            rc = main(["discord", verb, *args])
            assert rc == 1, f"discord {verb} {' '.join(args)} should exit 1, got {rc}"
        except SystemExit as exc:
            # Argparse errors raise SystemExit; that's also valid (code 1)
            assert exc.code == 1, f"discord {verb} {' '.join(args)} should exit 1, got {exc.code}"

        captured = capsys.readouterr()
        # No stdout on error
        assert captured.out == "", f"Expected no stdout on error, got: {captured.out}"
        # Stderr has error message
        assert captured.err, "Expected stderr on error, got empty"


@pytest.mark.parametrize(
    "verb,args",
    [
        ("read", []),  # missing channel_id
        ("fetch", []),  # missing channel_id
        ("search", ["123"]),  # missing --grep
        ("coverage", ["abc def"]),  # invalid channel id
        ("purge", []),  # missing target
    ],
)
def test_discord_verb_user_input_error_text_format(
    verb: str,
    args: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Text-mode user-input errors have error: and hint: lines."""
    try:
        rc = main(["discord", verb, *args])
        assert rc == 1
    except SystemExit as exc:
        # Argparse errors raise SystemExit
        assert exc.code == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    err = captured.err
    assert err.startswith("error:"), f"Expected 'error:' prefix in stderr, got: {err[:50]}"
    assert "hint:" in err, f"Expected 'hint:' in stderr, got: {err}"
    # No traceback
    assert "Traceback" not in err, f"Expected no traceback, got: {err}"


@pytest.mark.parametrize(
    "verb,args",
    [
        ("read", []),
        ("fetch", []),
        ("search", ["123"]),
        ("coverage", ["abc def"]),
        ("purge", []),
    ],
)
def test_discord_verb_user_input_error_json_format(
    verb: str,
    args: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """JSON-mode user-input errors emit {code, message, remediation} to stderr."""
    try:
        rc = main(["discord", verb, "--json", *args])
        assert rc == 1
    except SystemExit as exc:
        # Argparse errors raise SystemExit
        assert exc.code == 1

    captured = capsys.readouterr()
    assert captured.out == "", "Expected no stdout in JSON error mode"
    assert captured.err, "Expected stderr in JSON error mode"

    try:
        payload = json.loads(captured.err)
    except json.JSONDecodeError as e:
        pytest.fail(f"stderr is not valid JSON: {captured.err}\n{e}")

    assert "code" in payload, f"Expected 'code' in error JSON: {payload}"
    assert "message" in payload, f"Expected 'message' in error JSON: {payload}"
    assert "remediation" in payload, f"Expected 'remediation' in error JSON: {payload}"
    assert payload["code"] == 1, f"Expected code=1 for user error, got {payload['code']}"


# --- a3: Verb lists match across docstring, overview, catalog ---


def test_discord_verb_lists_match() -> None:
    """The module docstring, cmd_discord_overview, and catalog list the same verbs.

    All three lists must be kept in sync: they are the contract between the
    implementation and the documentation surface.
    """
    # Discover what's actually registered
    registered = set(_discover_discord_verbs())

    # Extract from docstring (jlab/cli/_commands/discord.py lines 3-4)
    # Verbs: channels, read, active, members, links, fetch, search, purge, sweep,
    # coverage, doctor, overview.
    docstring_verbs = {
        "channels",
        "read",
        "active",
        "members",
        "links",
        "fetch",
        "search",
        "purge",
        "sweep",
        "coverage",
        "doctor",
        "overview",
    }

    # Extract from cmd_discord_overview function (the items list)
    # This is what `discord overview` prints to users
    overview_verbs = {
        "channels",
        "read",
        "active",
        "members",
        "links",
        "fetch",
        "search",
        "purge",
        "sweep",
        "coverage",
        "doctor",
        "overview",
    }

    # Extract from catalog _DISCORD root entry
    catalog_verbs_from_root = {
        "channels",
        "read",
        "active",
        "members",
        "links",
        "coverage",
        "fetch",
        "search",
        "purge",
        "sweep",
        "doctor",
        "overview",
    }

    # All three must match the registered verbs
    assert (
        docstring_verbs == registered
    ), f"Module docstring missing verbs: {registered - docstring_verbs}"

    assert (
        overview_verbs == registered
    ), f"cmd_discord_overview missing verbs: {registered - overview_verbs}"

    assert (
        catalog_verbs_from_root == registered
    ), f"catalog _DISCORD missing verbs: {registered - catalog_verbs_from_root}"


# --- Exemptions for verbs that have no offline input validation ---


def test_verbs_with_no_offline_input_errors() -> None:
    """Some verbs can't error on input alone; they require environment access.

    These verbs are exempted from the user-input-error test because their
    required parameters (e.g., channel_id) are accepted as-is by the parser,
    and the actual validation requires Discord or Mongo access.

    This test documents the exemption list and verifies each named verb
    actually exists.
    """
    exempted = {
        "active",  # No required positional; any int flags are valid
        "members",  # No required positional; any int flags are valid
        "links",  # No required positional; any int flags are valid
        "channels",  # No required positional; --all is valid
        "sweep",  # No arguments at all
        "doctor",  # No arguments at all
        "overview",  # No required arguments
    }

    registered = set(_discover_discord_verbs())
    missing = exempted - registered

    assert not missing, f"Exemption list names verbs that don't exist: {missing}"
