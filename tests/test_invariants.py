"""Invariant guards for the read-only Discord surface and sibling-repo isolation.

These tests assert that:
1. No code path under jlab/ that reaches Discord ever calls a Discord write method
2. No code under jlab/ edits the sibling discord-bot-cli repository
3. Every WORKAROUND marker has a valid format (discord-bot-cli#N)
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

# Discord write methods that must never be called on Discord objects.
# Note: Mongo operations like delete_many/delete_one in jlab/cache.py are NOT
# Discord objects and should not trip this test.
DISCORD_WRITE_METHODS = {
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


def _find_discord_touching_modules() -> set[Path]:
    """Discover all modules under jlab/ that reach Discord.

    Scans for uses of ``_discord._run`` or ``discord_client`` to identify
    modules that touch the Discord adapter.
    """
    jlab_path = Path(__file__).parent.parent / "jlab"
    modules = set()

    for py_file in jlab_path.rglob("*.py"):
        content = py_file.read_text()
        # Check for _discord._run or discord_client usage
        if "_discord._run" in content or "discord_client" in content:
            modules.add(py_file)

    return modules


def _find_write_method_calls(tree: ast.AST) -> list[tuple[int, str, str]]:
    """Walk an AST and find all calls to Discord write methods.

    Returns a list of (line_number, method_name, context_snippet).
    A call is flagged if it's an attribute access on any name followed by
    a call with one of the DISCORD_WRITE_METHODS names.
    """
    findings = []

    class CallVisitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            # Check if this is a method call on an object
            if isinstance(node.func, ast.Attribute):
                method_name = node.func.attr
                if method_name in DISCORD_WRITE_METHODS:
                    findings.append((node.lineno, method_name, ast.unparse(node.func)))
            self.generic_visit(node)

    CallVisitor().visit(tree)
    return findings


def test_no_discord_write_methods() -> None:
    """Assert no Discord write methods are called in modules that reach Discord.

    Scans jlab/cli/_discord.py, jlab/fetch.py, jlab/members/resolve.py, and
    any future modules that use _discord._run or discord_client, and fails if
    any contains a call to send, reply, edit, delete, add_reaction, etc.
    """
    modules = _find_discord_touching_modules()

    errors: list[str] = []

    for module_path in sorted(modules):
        try:
            content = module_path.read_text()
            tree = ast.parse(content, filename=str(module_path))
        except SyntaxError as exc:
            # Syntax errors are a real failure, not a test failure
            raise AssertionError(f"Syntax error in {module_path}: {exc}") from exc

        calls = _find_write_method_calls(tree)
        for lineno, method_name, context in calls:
            errors.append(
                f"{module_path}:{lineno} calls {method_name}() on {context} — "
                f"read-only surface breach"
            )

    assert not errors, "Discord write methods found in jlab/ modules:\n" + "\n".join(errors)


def test_no_discord_bot_cli_edits() -> None:
    """Assert nothing under jlab/ writes to the sibling discord-bot-cli repo.

    Scans jlab/ for:
    - File opens/writes to paths under DISCORD_BOT_CLI_PROJECT or ~/git/discord-bot-cli
    - subprocess calls to paths under those directories with write intent

    Also checks that the sibling repo's working tree is clean (no files were
    modified by this test or prior invocation).
    """
    jlab_path = Path(__file__).parent.parent / "jlab"
    home = Path.home()
    discord_cli_paths = [
        home / "git" / "discord-bot-cli",
        "${DISCORD_BOT_CLI_PROJECT}",  # Also check for env var usage
    ]

    errors: list[str] = []

    for py_file in jlab_path.rglob("*.py"):
        try:
            content = py_file.read_text()
            tree = ast.parse(content, filename=str(py_file))
        except SyntaxError as exc:
            raise AssertionError(f"Syntax error in {py_file}: {exc}") from exc

        class WriteVisitor(ast.NodeVisitor):
            def visit_Call(self, node: ast.Call) -> None:
                # Check for open() calls with write mode
                if isinstance(node.func, ast.Name) and node.func.id == "open":
                    # open(path, ...) — check the first argument
                    if node.args:
                        path_arg = ast.unparse(node.args[0])
                        for discord_path in discord_cli_paths:
                            if str(discord_path) in path_arg:
                                # Check if write mode is set
                                has_write_mode = any(
                                    (
                                        isinstance(kw.value, ast.Constant)
                                        and "w" in str(kw.value.value)
                                    )
                                    or (isinstance(arg, ast.Constant) and "w" in str(arg.value))
                                    for kw in node.keywords
                                    for arg in node.args[1:]
                                )
                                if has_write_mode or len(node.args) == 1:
                                    # Default mode is 'r', but explicit write mode is a breach
                                    # If there's a second arg or a mode keyword, check it
                                    if len(node.args) > 1 or any(
                                        kw.arg == "mode" for kw in node.keywords
                                    ):
                                        errors.append(
                                            f"{py_file}:{node.lineno} opens a file in "
                                            f"discord-bot-cli path: {path_arg}"
                                        )

                # Check for subprocess calls to paths under discord-bot-cli
                if isinstance(node.func, ast.Attribute) and node.func.attr in (
                    "run",
                    "call",
                    "check_call",
                    "check_output",
                    "Popen",
                ):
                    if node.args:
                        cmd = ast.unparse(node.args[0])
                        for discord_path in discord_cli_paths:
                            if str(discord_path) in cmd:
                                errors.append(
                                    f"{py_file}:{node.lineno} runs subprocess with "
                                    f"discord-bot-cli path: {cmd}"
                                )

                self.generic_visit(node)

        WriteVisitor().visit(tree)

    assert not errors, "discord-bot-cli edits found in jlab/:\n" + "\n".join(errors)

    # Check that the sibling repo is clean
    discord_bot_cli_path = home / "git" / "discord-bot-cli"
    try:
        result = subprocess.run(
            ["git", "-C", str(discord_bot_cli_path), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            raise AssertionError(f"discord-bot-cli repo is not clean:\n{result.stdout}")
    except subprocess.TimeoutExpired:
        raise AssertionError("git status check on discord-bot-cli timed out")
    except FileNotFoundError:
        raise AssertionError("discord-bot-cli repo not found at ~/git/discord-bot-cli")


def test_workaround_marker_format() -> None:
    """Assert every WORKAROUND marker has valid format: WORKAROUND(discord-bot-cli#N).

    Scans all .py files under jlab/ for WORKAROUND markers and validates that
    each matches the pattern: WORKAROUND(discord-bot-cli#<digits>)
    """
    jlab_path = Path(__file__).parent.parent / "jlab"

    # Pattern: WORKAROUND(discord-bot-cli#<digits>)
    workaround_pattern = re.compile(r"WORKAROUND\(([a-z0-9_-]+#\d+)\)")

    errors: list[str] = []

    for py_file in jlab_path.rglob("*.py"):
        content = py_file.read_text()
        # Find all WORKAROUND markers
        for i, line in enumerate(content.split("\n"), 1):
            markers = workaround_pattern.findall(line)
            for marker in markers:
                # Validate marker format: should be discord-bot-cli#<number>
                if not marker.startswith("discord-bot-cli#"):
                    errors.append(
                        f"{py_file}:{i} has invalid WORKAROUND marker: {marker} "
                        f"(must be discord-bot-cli#<number>)"
                    )

    assert not errors, "Invalid WORKAROUND markers found:\n" + "\n".join(errors)
