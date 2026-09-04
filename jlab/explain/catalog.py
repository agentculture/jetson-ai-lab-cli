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

## Discord (read-only)

- `jetson-ai-lab-cli discord channels|read|active|doctor` — read-only,
  public-only scan of the Jetson AI Lab Discord.

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

_DISCORD = """\
# jetson-ai-lab-cli discord

Read-only Discord noun group. Lists public channels, reads messages, ranks
active channels, scans participation statistics, and verifies connectivity.
Public channels only by default (`--all` is the sole private opt-in).

## Verbs

- `jetson-ai-lab-cli discord channels [--all]` — list guild channels.
- `jetson-ai-lab-cli discord read <channel_id> [--limit N]` — read recent messages.
- `jetson-ai-lab-cli discord active [flags]` — rank active public channels by traffic.
- `jetson-ai-lab-cli discord members [--since DAYS] [--json]` — scan participation statistics.
- `jetson-ai-lab-cli discord doctor` — verify token + guild readable.
- `jetson-ai-lab-cli discord overview` — describe this noun group.

## Conventions

- Read-only only (no post/react/thread).
- Public channels only by default (`--all` is the sole private opt-in).
- Every command supports `--json`.
- Results to stdout, diagnostics/errors to stderr.
"""

_DISCORD_CHANNELS = """\
# jetson-ai-lab-cli discord channels

List the guild's channels with a ``public`` flag. By default only public
channels (those the ``@everyone`` role can view) are returned. Pass ``--all``
to include private/role-gated channels too.

## Usage

    jetson-ai-lab-cli discord channels
    jetson-ai-lab-cli discord channels --all
    jetson-ai-lab-cli discord channels --json
"""

_DISCORD_READ = """\
# jetson-ai-lab-cli discord read <channel_id>

Read recent messages from a single channel. *limit* must be 1-100 (default 20).

## Usage

    jetson-ai-lab-cli discord read 1234567890
    jetson-ai-lab-cli discord read 1234567890 --limit 50
    jetson-ai-lab-cli discord read 1234567890 --json
"""

_DISCORD_ACTIVE = """\
# jetson-ai-lab-cli discord active

Rank active public text channels by recent traffic. Probes all public text
channels in a single REST session, then ranks in-process. Private channels are
filtered out before any message is fetched.

Channel reads fan out concurrently but are bounded by a semaphore
(`--concurrency`, default 4) so a ~100-channel guild never puts an unbounded
number of requests in flight. Each channel read carries its own status, so a
failed read is never mistaken for an empty channel.

## Usage

    jetson-ai-lab-cli discord active
    jetson-ai-lab-cli discord active --since 7 --top 10 --preview 3
    jetson-ai-lab-cli discord active --concurrency 2
    jetson-ai-lab-cli discord active --json
"""

_DISCORD_DOCTOR = """\
# jetson-ai-lab-cli discord doctor

Verify the Discord bot token is set, ``discord-bot-cli`` is importable, and
the guild is readable. Exits 2 on environment error.

## Usage

    jetson-ai-lab-cli discord doctor
    jetson-ai-lab-cli discord doctor --json
"""

_DISCORD_OVERVIEW = """\
# jetson-ai-lab-cli discord overview

Describe the ``discord`` noun group: verbs, conventions, and constraints.

## Usage

    jetson-ai-lab-cli discord overview
    jetson-ai-lab-cli discord overview --json
"""

_DISCORD_MEMBERS = """\
# jetson-ai-lab-cli discord members

Scan public text channels for participation statistics over a time window and
write an HTML report — one invocation, no pipeline to assemble. Organizes
statistics by member without ranking or verdict. The pipeline is anonymous:
aggregates activity by author ID and resolves names only at render time.
Bots and members who have left the guild are excluded by default; public
text channels only.

`--json` emits the id-only aggregate (no name resolution, no HTML file
written) so display names can never leave via stdout redirection.
`--include-departed` includes every author regardless of current guild
membership; the default excludes those who have left. `--since` defaults to
90 days; `--concurrency` bounds how many channels are read in parallel.

## Usage

    jetson-ai-lab-cli discord members
    jetson-ai-lab-cli discord members --since 30
    jetson-ai-lab-cli discord members --include-departed
    jetson-ai-lab-cli discord members --concurrency 2
    jetson-ai-lab-cli discord members --json
"""


ENTRIES: dict[tuple[str, ...], str] = {
    (): _ROOT,
    # Console-script name (pyproject [project.scripts]); the rubric derives the
    # tool name from it, so `explain jlab` must resolve.
    ("jlab",): _ROOT,
    ("jetson-ai-lab-cli",): _ROOT,
    ("whoami",): _WHOAMI,
    ("learn",): _LEARN,
    ("explain",): _EXPLAIN,
    ("overview",): _OVERVIEW,
    ("doctor",): _DOCTOR,
    ("cli",): _CLI,
    ("cli", "overview"): _CLI,
    ("discord",): _DISCORD,
    ("discord", "channels"): _DISCORD_CHANNELS,
    ("discord", "read"): _DISCORD_READ,
    ("discord", "active"): _DISCORD_ACTIVE,
    ("discord", "members"): _DISCORD_MEMBERS,
    ("discord", "doctor"): _DISCORD_DOCTOR,
    ("discord", "overview"): _DISCORD_OVERVIEW,
}
