"""Invariant guards for the read-only Discord surface and sibling-repo isolation (t15).

1. No module under jlab/ that reaches Discord calls a Discord write method (o2).
2. Nothing under jlab/ writes into, or shells out against, the sibling
   discord-bot-cli checkout — importing ``discord_bot_cli`` as a library is fine.
3. Every ``WORKAROUND`` marker names an upstream issue: ``WORKAROUND(discord-bot-cli#N)``.

Each guard is a small detector run over jlab/ AND over deliberately bad snippets,
so a detector that silently stops matching fails here rather than passing vacuously.
Whether the sibling checkout itself is clean is a property of the developer's
machine, not of this code, so it is not asserted.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

JLAB = Path(__file__).resolve().parent.parent / "jlab"

DISCORD_WRITE_METHODS = frozenset(
    {
        "send",
        "reply",
        "edit",
        "delete",
        "add_reaction",
        "remove_reaction",
        "clear_reactions",
        "create_thread",
        "pin",
        "unpin",
        "publish",
        "create_webhook",
        "purge",
    }
)

_SIBLING_MARKERS = ("discord-bot-cli", "DISCORD_BOT_CLI_PROJECT", "DISCORD_BOT_CLI")
_WRITE_CALLS = frozenset(
    {
        "open",
        "write_text",
        "write_bytes",
        "touch",
        "mkdir",
        "unlink",
        "rmdir",
        "rename",
        "replace",
        "rmtree",
        "copy",
        "copyfile",
        "copytree",
        "move",
        "run",
        "call",
        "check_call",
        "check_output",
        "Popen",
        "system",
    }
)
_MARKER = re.compile(r"WORKAROUND\b(\([^)]*\))?")
_VALID_MARKER = re.compile(r"\(discord-bot-cli#\d+\)")


def _sources() -> list[Path]:
    return sorted(JLAB.rglob("*.py"))


def _reaches_discord(source: str) -> bool:
    return "_discord._run" in source or "discord_client" in source or "_run(action" in source


def write_method_calls(source: str) -> list[str]:
    """``name.<write method>(...)`` calls in *source*, as ``line: expr``."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in DISCORD_WRITE_METHODS
        ):
            found.append(f"{node.lineno}: {ast.unparse(node.func)}")
    return found


def sibling_writes(source: str) -> list[str]:
    """Write-capable calls whose source text mentions the sibling checkout."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name not in _WRITE_CALLS:
            continue
        text = ast.unparse(node)
        if any(marker in text for marker in _SIBLING_MARKERS):
            found.append(f"{node.lineno}: {text}")
    return found


def bad_markers(source: str) -> list[str]:
    """``WORKAROUND`` markers that do not name ``discord-bot-cli#<digits>``."""
    found = []
    for lineno, line in enumerate(source.splitlines(), 1):
        for match in _MARKER.finditer(line):
            if not (match.group(1) and _VALID_MARKER.fullmatch(match.group(1))):
                found.append(f"{lineno}: {match.group(0)}")
    return found


# --- the detectors really detect -------------------------------------------


@pytest.mark.parametrize(
    "snippet",
    [
        "async def f(channel):\n    await channel.send('x')\n",
        "async def f(message):\n    await message.add_reaction('x')\n",
        "async def f(channel):\n    await channel.create_thread(name='x')\n",
        "async def f(message):\n    await message.delete()\n",
    ],
)
def test_write_method_detector_flags_a_discord_write(snippet: str) -> None:
    assert write_method_calls(snippet)


@pytest.mark.parametrize(
    "snippet",
    [
        "open(os.environ['DISCORD_BOT_CLI_PROJECT'] + '/x', 'w')\n",
        "Path.home().joinpath('git/discord-bot-cli/x').write_text('y')\n",
        "subprocess.run(['git', '-C', '~/git/discord-bot-cli', 'commit'])\n",
        "shutil.rmtree(Path('~/git/discord-bot-cli'))\n",
    ],
)
def test_sibling_write_detector_flags_a_write(snippet: str) -> None:
    assert sibling_writes(snippet)


def test_sibling_write_detector_allows_importing_the_library() -> None:
    assert not sibling_writes("from discord_bot_cli import discord_client\n")


@pytest.mark.parametrize(
    "snippet",
    [
        "# WORKAROUND(discord-bot-cli) no issue\n",
        "# WORKAROUND(discord-bot-cli#) empty number\n",
        "# WORKAROUND(other-repo#12) wrong repo\n",
        "# WORKAROUND: bare marker\n",
    ],
)
def test_marker_detector_flags_a_malformed_marker(snippet: str) -> None:
    assert bad_markers(snippet)


def test_marker_detector_accepts_a_well_formed_marker() -> None:
    assert not bad_markers("# WORKAROUND(discord-bot-cli#14) — author.bot\n")


# --- jlab/ holds the invariants --------------------------------------------


def test_discord_modules_are_discovered() -> None:
    reaching = {
        p.relative_to(JLAB).as_posix() for p in _sources() if _reaches_discord(p.read_text())
    }
    assert {"cli/_discord.py", "fetch.py"} <= reaching


def test_no_discord_write_methods() -> None:
    """o2: the read-only surface stays literally read-only as the seam grows."""
    errors = [
        f"{path.relative_to(JLAB)}:{hit}"
        for path in _sources()
        if _reaches_discord(path.read_text())
        for hit in write_method_calls(path.read_text())
    ]
    assert not errors, "Discord write calls under jlab/:\n" + "\n".join(errors)


def test_nothing_under_jlab_writes_into_the_sibling_checkout() -> None:
    errors = [
        f"{path.relative_to(JLAB)}:{hit}"
        for path in _sources()
        for hit in sibling_writes(path.read_text())
    ]
    assert not errors, "writes into discord-bot-cli under jlab/:\n" + "\n".join(errors)


def test_workaround_markers_name_an_upstream_issue() -> None:
    errors = [
        f"{path.relative_to(JLAB)}:{hit}"
        for path in _sources()
        for hit in bad_markers(path.read_text())
    ]
    assert not errors, "malformed WORKAROUND markers under jlab/:\n" + "\n".join(errors)
