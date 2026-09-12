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
active channels, scans participation statistics, inspects cache coverage, and
verifies connectivity. Public channels only by default (`--all` is the sole
private opt-in).

## Verbs

- `jetson-ai-lab-cli discord channels [--all]` — list guild channels.
- `jetson-ai-lab-cli discord read <channel_id> [--limit N]` — read recent messages.
- `jetson-ai-lab-cli discord active [flags]` — rank active public channels by traffic.
- `jetson-ai-lab-cli discord members [--since DAYS] [--json]` — scan participation statistics.
- `jetson-ai-lab-cli discord links [--since DAYS] [--json]` — scan shared addresses.
- `jetson-ai-lab-cli discord coverage [<channel_id>] [--since TS] [--until TS]` — inspect cache coverage metadata.
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
the guild is readable — plus, for the paged-read cache, that ``pymongo`` is
installed and the jlab-mongodb instance named by ``JLAB_MONGO_URI`` is
reachable and is genuinely jlab's own dedicated instance (never the legacy
qq-mongodb on 27017 or the eidetic-mongo memory store on 27018). Exits 2 on
any environment error, including an absent or unreachable cache — never a
silent empty result.

It also **measures** the cache's application-level content encryption rather
than assuming it: a marked probe document is written through the real store
path, read straight back out of the collection *without* decrypting, and
checked for the marker. A plaintext hit, or an absent ``JLAB_CACHE_KEY``,
exits 2. The reported line describes what was measured, not what was
configured.

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
write an HTML report plus a CSV into a per-run subdirectory — one invocation,
no pipeline to assemble. Organizes statistics by member without ranking or
verdict. The pipeline is anonymous: aggregates activity by author ID and
resolves names only at render time. Bots and members who have left the guild
are excluded by default; public text channels only.

`--json` emits the id-only aggregate (no name resolution, no report files
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

_DISCORD_LINKS = """\
# jetson-ai-lab-cli discord links

Scan public text channels for shared addresses over a time window and write
one run's HTML report plus its flat and per-address CSVs into a per-run
subdirectory — one invocation, no pipeline to assemble. The page organizes
shares; it issues no verdict — nothing is ranked, scored, or labeled.

`--since` defaults to 90 days; `--concurrency` bounds how many channels are
read in parallel (default 4). `--include-bots` includes bot- and
webhook-authored shares (the default excludes them). `--from-cache RUN_ID`
re-renders a previous run's cached extraction without opening a new Discord
scan; pass the run id from that run's report directory.

`--json` emits the id-only extraction only — no name resolution, and no
report or cache files written — and that containment has no opt-in: no flag
combination turns it off.

Discord's attachment-CDN URLs stop resolving roughly 14-22 hours after they
are fetched, so any address pulled from an attachment renders with a visible
*expiring* badge and is never made clickable; the durable way back to the
message is the jump link in the same row, which is always live.

## Usage

    jetson-ai-lab-cli discord links
    jetson-ai-lab-cli discord links --since 30
    jetson-ai-lab-cli discord links --include-bots
    jetson-ai-lab-cli discord links --concurrency 2
    jetson-ai-lab-cli discord links --from-cache 20260905T101112Z-1a2b3c4d
    jetson-ai-lab-cli discord links --json
"""


_DISCORD_COVERAGE = """\
# jetson-ai-lab-cli discord coverage

Inspect what time windows the jlab message cache holds for a channel, without
opening MongoDB or decrypting message content. Coverage is the cache's central
invariant: every incremental-fetch and gap-reporting guarantee depends on it.

Omit the channel id to list all channels with coverage records. Pass a channel
id to show what intervals the cache covers for that channel. With `--since` and
`--until` (ISO-8601 timestamps), show covered and uncovered spans within that
time window and whether the window is fully cached (complete) or has gaps.

Without a time window, the verb shows the recorded intervals and notes that
completeness requires a window. With a window, it shows both covered spans and
the gaps between them.

## Usage

    jetson-ai-lab-cli discord coverage
    jetson-ai-lab-cli discord coverage 123456789012345678
    jetson-ai-lab-cli discord coverage 123456789012345678 \\
      --since 2026-09-01T00:00:00+00:00 --until 2026-09-15T00:00:00+00:00
    jetson-ai-lab-cli discord coverage 123456789012345678 --json
"""


_DISCORD_PURGE = """\
# jetson-ai-lab-cli discord purge

Delete data from jlab's message cache **and** from every derived report that
carries it — the runnable deletion path behind the privacy policy's promise
to honour deletion requests, including derived indexes. Exactly one target is
required:

- `--author ID` — every cached message by that Discord author id, plus every
  members/links report run whose artifacts mention the id;
- `--channel ID` — every cached message from that channel, plus every report
  run whose artifacts carry the channel id (links jump URLs);
- `--older-than DAYS` — the retention bound: cached messages created, and
  report runs written, more than DAYS ago.

Safety: targets must be bare numeric ids (empty, wildcard and pattern targets
exit 1 before anything is touched). **Without `--yes` the verb is a dry run**
that reports what would be removed; with `--yes` it deletes and reports
exactly what was removed. A report run is removed whole, never edited, because
it is one rendered artifact set and is regenerable by re-running its verb.
Re-running a purge is safe (idempotent).

Coverage stays honest: `--channel` also clears that channel's cache coverage,
and `--older-than` trims every channel's coverage to the cutoff, so a purged
window reads back as a gap rather than as complete. Both run under the
channel's coverage lock (one channel at a time), waiting for an in-flight
fetch of that channel. `--author` leaves coverage unchanged and instead
records the author in a suppression list, so no later fetch or sweep caches
their messages again. The list keeps only a **keyed hash** of the author id
(HMAC-SHA256 under a key derived from `JLAB_CACHE_KEY`), never the id itself;
without the key the purge exits 2 before deleting anything. `--json` adds
`coverage` (`channels`, `applied`) and, for `--author`, `suppression`
(`recorded`, `already_present`). A dry run records and changes nothing.

## Usage

    jetson-ai-lab-cli discord purge --author 123456789012345678
    jetson-ai-lab-cli discord purge --author 123456789012345678 --yes
    jetson-ai-lab-cli discord purge --channel 123456789012345678 --yes --json
    jetson-ai-lab-cli discord purge --older-than 365 --yes
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
    ("discord", "links"): _DISCORD_LINKS,
    ("discord", "coverage"): _DISCORD_COVERAGE,
    ("discord", "purge"): _DISCORD_PURGE,
    ("discord", "doctor"): _DISCORD_DOCTOR,
    ("discord", "overview"): _DISCORD_OVERVIEW,
}
