"""Markdown catalog for ``jetson-ai-lab-cli explain <path>``.

Each entry is verbatim markdown. Keys are command-path tuples. The empty tuple
and ``("jetson-ai-lab-cli",)`` both resolve to the root entry.

Keep bodies self-contained: an agent reading one entry should get enough
context without chaining reads.
"""

from __future__ import annotations

_ROOT = """\
# jetson-ai-lab-cli

A clonable template for AgentCulture mesh agents. It carries an agent-first CLI
(cited from the teken `python-cli` reference), a mesh identity (`culture.yaml` +
`CLAUDE.md`), the canonical guildmaster skill kit under `.claude/skills/`, and a
buildable/deployable package baseline. Clone it, rename the package, edit
`culture.yaml`, and you have a new agent.

## Verbs

- `jetson-ai-lab-cli whoami` — identity probe from `culture.yaml`.
- `jetson-ai-lab-cli learn` — structured self-teaching prompt.
- `jetson-ai-lab-cli explain <path>` — markdown docs for any noun/verb.
- `jetson-ai-lab-cli overview` — descriptive snapshot of the agent.
- `jetson-ai-lab-cli doctor` — check the agent-identity invariants.
- `jetson-ai-lab-cli cli overview` — describe the CLI surface.

## Exit-code policy

- `0` success
- `1` user-input error
- `2` environment / setup error
- `3+` reserved

## See also

- `jetson-ai-lab-cli explain whoami`
- `jetson-ai-lab-cli explain doctor`
"""

_WHOAMI = """\
# jetson-ai-lab-cli whoami

Reports the agent's identity from `culture.yaml`: nick (`suffix`), backend,
served model, and the package version. Read-only.

## Usage

    jetson-ai-lab-cli whoami
    jetson-ai-lab-cli whoami --json
"""

_LEARN = """\
# jetson-ai-lab-cli learn

Prints a structured self-teaching prompt covering purpose, command map,
exit-code policy, `--json` support, and the `explain` pointer.

## Usage

    jetson-ai-lab-cli learn
    jetson-ai-lab-cli learn --json
"""

_EXPLAIN = """\
# jetson-ai-lab-cli explain <path>

Prints markdown documentation for any noun/verb path. Unlike `--help` (terse,
positional), `explain` is global and addressable by path.

## Usage

    jetson-ai-lab-cli explain jetson-ai-lab-cli
    jetson-ai-lab-cli explain whoami
    jetson-ai-lab-cli explain --json <path>
"""

_OVERVIEW = """\
# jetson-ai-lab-cli overview

Read-only descriptive snapshot of the agent: identity (from `culture.yaml`), the
verb surface, and the sibling-pattern artifacts the template carries. Accepts an
ignored `target` so a stray path never hard-fails.

## Usage

    jetson-ai-lab-cli overview
    jetson-ai-lab-cli overview --json
"""

_DOCTOR = """\
# jetson-ai-lab-cli doctor

Checks the agent-identity invariants `steward doctor` verifies:
prompt-file-present and backend-consistency (`claude` → `CLAUDE.md`), plus a
skills-present check. Exits 1 when unhealthy.

## Usage

    jetson-ai-lab-cli doctor
    jetson-ai-lab-cli doctor --json
"""

_CLI = """\
# jetson-ai-lab-cli cli

Noun group for CLI-surface introspection. `cli overview` describes the CLI
itself (distinct from the global `overview`, which describes the agent).

## Usage

    jetson-ai-lab-cli cli overview
    jetson-ai-lab-cli cli overview --json
"""


ENTRIES: dict[tuple[str, ...], str] = {
    (): _ROOT,
    ("jetson-ai-lab-cli",): _ROOT,
    ("whoami",): _WHOAMI,
    ("learn",): _LEARN,
    ("explain",): _EXPLAIN,
    ("overview",): _OVERVIEW,
    ("doctor",): _DOCTOR,
    ("cli",): _CLI,
    ("cli", "overview"): _CLI,
}
