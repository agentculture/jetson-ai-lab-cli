# jetson-ai-lab-cli

Discord-facing knowledge fetch & index agent for the Jetson AI Lab community — fetches and indexes Jetson AI Lab docs/sources and answers members' questions on Discord.

> **Status:** the read side is real and now includes a paged, cache-backed
> history and search pipeline — the rest is still scaffold. The agent can
> **read, page past Discord's 100-message cap, search, and reconcile the
> Jetson AI Lab Discord read-only today** (see below); indexing what it reads
> into a queryable corpus and answering members' questions are not built yet.

## What you get

- **A read-only Jetson AI Lab Discord scanner, fetch/search pipeline, and
  reports** — the `jetson-discord-scan` skill's original scan, plus
  `fetch`/`search`/`read --refresh`/`sweep`/`purge` (paged history past the
  100-message cap, cache-served regex search, daily reconciliation, and
  retention-bound deletion) and the `members`/`links` reports. See
  [Jetson AI Lab Discord](#jetson-ai-lab-discord-read-only) and
  [Paged history, search, and retention](#paged-history-search-and-retention).
- **An agent-first CLI** cited from [teken](https://github.com/agentculture/teken)
  (`afi-cli`) — the runtime package has no third-party dependencies.
- **A mesh identity** — `culture.yaml` (`suffix` + `backend`) and the matching
  prompt file (`CLAUDE.md` for `backend: claude`).
- **The canonical guildmaster skill kit** (11 skills) under `.claude/skills/`,
  vendored cite-don't-import. See [`docs/skill-sources.md`](docs/skill-sources.md).
- **A build + deploy baseline** — pytest, lint, the agent-first rubric gate, and
  PyPI Trusted Publishing wired into GitHub Actions.

## Quickstart

```bash
uv sync
uv run pytest -n auto                 # run the test suite
uv run jetson-ai-lab-cli whoami  # identity from culture.yaml
uv run jetson-ai-lab-cli learn   # self-teaching prompt (add --json)
uv run teken cli doctor . --strict    # the agent-first rubric gate CI runs
```

## CLI

| Verb | What it does |
|------|--------------|
| `whoami` | Report this agent's nick, version, backend, and model from `culture.yaml`. |
| `learn` | Print a structured self-teaching prompt. |
| `explain <path>` | Markdown docs for any noun/verb path. |
| `overview` | Read-only descriptive snapshot of the agent. |
| `doctor` | Check the agent-identity invariants (prompt-file-present, backend-consistency). |
| `cli overview` | Describe the CLI surface itself. |

Every command supports `--json`. Results go to stdout, errors/diagnostics to
stderr (never mixed). Exit codes: `0` success, `1` user error, `2` environment
error, `3+` reserved.

## Jetson AI Lab Discord (read-only)

The agent's intended job starts at the **Jetson AI Lab Research Group** Discord —
a hands-on community running modern AI on NVIDIA edge hardware. At a glance:

![Concept map of the Jetson AI Lab Research Group: a central hub "run LLMs, VLMs and robotics on NVIDIA edge hardware" surrounded by six branches — hardware platforms (Orin, Thor, DGX Spark), workloads (LLMs, VLMs, VLAs, speech, agents), software and tooling (JetPack 7.2/CUDA SBSA, vLLM/SGLang, jetson-containers, Isaac), what members do (benchmark, share container recipes, troubleshoot, quantize), physical AI and robotics (sim-to-real, robot arms, drones, VLA inference), and community and cadence (monthly meeting, talks, NVIDIA presence, news feeds).](docs/jetson-ai-lab-server.svg)

The [`jetson-discord-scan`](.claude/skills/jetson-discord-scan/SKILL.md) skill
gives it a **read-only** window into that server. It wraps the read verbs of the
sibling [`discord-bot-cli`](https://github.com/agentculture/discord-bot-cli) —
it never posts, reacts, or creates threads — and it scans **public channels
only** (private / role-gated channels are always excluded).

```bash
S=.claude/skills/jetson-discord-scan/scripts/scan.sh

bash $S doctor                              # token present + guild readable?
bash $S active --since 30 --top 15          # public channels active in the last month, by traffic
bash $S channels                            # public channel map (each tagged "public": true)
bash $S read <channel_id> --limit 50        # recent messages of one channel
```

Needs a **read-scoped** bot token in `DISCORD_BOT_TOKEN` and `discord-bot-cli`
with its `[discord]` extra. Results are JSON on stdout; errors/diagnostics on
stderr.

### The jlab-mongodb cache

The paged-read/search path (`jlab discord fetch`/`read --refresh`/`search`/
`coverage`/`sweep`/`purge` — see [Paged history, search, and retention](#paged-history-search-and-retention)
below) caches message history in a **dedicated** MongoDB
instance, `jlab-mongodb`. It is deliberately its own container: this machine
already runs two unrelated `mongod`s — `qq-mongodb` on port 27017 (a legacy
instance) and `eidetic-mongo` on port 27018 (the shared eidetic memory store)
— and jlab must never borrow either. `jlab discord doctor` fails at exit code
2 if the configured instance is absent, unreachable, or turns out to be one
of those two.

```bash
docker run -d --name jlab-mongodb \
  -p 27019:27017 \
  -v jlab-mongodb-data:/data/db \
  mongo:8.0

export JLAB_MONGO_URI="mongodb://127.0.0.1:27019/jlab"
export JLAB_CACHE_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
uv run jlab discord doctor                  # confirms cache reachable + encrypted
```

**Cached message content is encrypted by jlab itself**, before it reaches
MongoDB, and decrypted on the way back out. MongoDB community edition has no
encrypted storage engine, so encryption at rest cannot be delegated to the
database — `jlab/crypto.py` does it at the application layer instead, with a
key read only from `JLAB_CACHE_KEY`. There is **no plaintext fallback**: an
absent, blank or under-32-character key is an exit-code-2 error and nothing is
written. Keep the key with the deployment — losing it makes the cache
unreadable (the fix is to re-fetch, not to recover).

`jlab discord doctor` **measures** this rather than assuming it: it stores a
marked probe through the real write path, reads the raw stored document back
without decrypting, and fails if the marker is found in it. The construction is
AES-256-GCM from the approved `cryptography` dependency, with the content key
derived from `JLAB_CACHE_KEY` via HKDF-SHA256 and a fresh 96-bit nonce per
message. It protects content at rest, not against anyone holding the key, and
there is no key rotation; `jlab/crypto.py`'s docstring states the limits in
full. Message *metadata* (channel id, author id, timestamps, jump URL) is
stored in the clear on purpose so the cache stays queryable; the body **and**,
per deviation d4, the author's name and display name are encrypted — a paged
read or search can show who said something without a live Discord lookup,
with ids and every timestamp still cleartext.

Each cached message carries three timestamps — `created_at` (Discord's),
`updated_at` (Discord's edit timestamp, `null` when unedited) and `stored_at`
(when jlab wrote the copy) — so an edit is detectable and the age of the local
copy is always known. This path **retains full message bodies, by decision**:
unlike `members` (counts only) and `links` (URLs only), a paged read and a
regex search over history cannot be served without the text itself.

Set `JLAB_MONGO_URI` from the environment — the same convention as
`DISCORD_BOT_TOKEN` — and it must never resolve to port 27017 or 27018;
`jlab` refuses to start against either.

**Bind address and network posture, stated as a decision, not an
oversight:** like the two existing containers on this machine,
`jlab-mongodb` binds `0.0.0.0` (published as `27019:27017` above), so it is
reachable from any interface this host has, not just loopback. That is
accepted **only** because this deployment sits on a trusted internal
network. It is not a hardening baseline — no TLS, no auth enforced by this
setup — so moving `jlab-mongodb` onto a less trusted network (a shared VPC, a
box with a public IP, anything outside the current internal network) is a
real change of posture and needs its own review before it happens, not a
silent inheritance of this setup.

### Member participation statistics (`jlab discord members`)

```bash
jlab discord members                  # last 90 days, public text channels
jlab discord members --since 30       # narrower window
jlab discord members --json           # same statistics, id-only, on stdout
```

This scans the same public text channels and writes an HTML report plus a CSV
sibling — message counts, breadth across channels, thread/question starts, and
length-based substance signals per participant — into a fresh, per-run
directory inside this repo, printing the HTML path when it's done. Each run
gets its own subdirectory so a later run can never overwrite or partially
clobber an earlier one.

**Expect it to take about five minutes.** Measured against the Jetson AI Lab
guild (100 public text channels, 90-day window): ~15s to scan and page the
channels, then ~4m45s to resolve author ids to names — one `fetch_member` call
per distinct author, 869 of them, at ~330ms each. Name resolution is ~95% of
the runtime; a batch resolution verb is filed upstream as
[`agentculture/discord-bot-cli#14`](https://github.com/agentculture/discord-bot-cli/issues/14)
and would cut the run to well under a minute. `--since 30` is proportionally
faster because it finds fewer distinct authors. **It issues no verdict:** the CLI does not rank,
score, or label anyone "most active," and it produces no presenter shortlist —
it organizes statistics so a Channel Maintainer (or an agent) can read the
report and make that judgment themselves.

The pipeline is anonymous end to end until the very last step: messages are
aggregated by `author.id` only, message content never survives past the
aggregation stage (only lengths and counts do), and display names are resolved
from ids in one batch, solely to render the report — `--json` output stays
id-only. Bots and webhooks are excluded; members who have left the guild are
excluded by default (a flag can include everyone). Voice channels are
deliberately out of scope — a member who only attends voice sessions won't show
up in this report — and forum channels/threads are a possible follow-up, not
covered yet. The generated report is **gitignored and never committed**; it's a
local artifact you hand to a maintainer, not a checked-in file.

### Shared links (`jlab discord links`)

```bash
jlab discord links                          # last 90 days, public text channels
jlab discord links --since 30               # narrower window
jlab discord links --include-bots           # count bot/webhook-shared links too
jlab discord links --from-cache <run-id>    # re-render a previous run, no re-scan
jlab discord links --json                   # id-only extraction, on stdout
```

**Why this exists:** the links a community shares are the clearest signal of
what it's actually reading and working on. For an agent whose eventual job is
to fetch and index Jetson AI Lab knowledge, the URLs members already vetted
for each other would make a ready-made seed corpus — today they're scattered
across 90 days and 100 channels with no way to see them in one place, and this
verb only surfaces them; it doesn't fetch, index, or query anything itself.

This sweeps the same public text channels as `active` and `members` and writes
one run's whole artifact set into its own gitignored, per-run directory: an
HTML report, a **flat CSV** (one row per share: url, channel, timestamp,
thread reference, author, jump link), and a derived **summary CSV** (one row
per distinct URL: share count, first/last seen, channels touched). A cached
copy of the extraction is written to a sibling `<run-id>-cache` directory —
one run directory holds exactly one atomically-written artifact set, so the
cache cannot share it — and `--from-cache <run-id>` re-renders that run's HTML
and CSVs without opening a new Discord scan.
Bots and webhooks are excluded by default; `--include-bots` opts them in.
**It issues no verdict:** no "most shared" link, no ranked domains, no
recommended reading — it organizes what was shared and leaves the judgment to
whoever reads the report.

**This report deliberately retains content, unlike the members report above.**
The members path's rule is that message content never survives past
aggregation — only counts and lengths do. A links report can't honor that rule
and still exist, because a URL *is* the content: it's the whole point of the
report. So the inversion is a decision, not a bug: what's retained is the
**URL itself, the channel it was shared in, its timestamp, a thread reference
where one applies, the sharer's author id, and a jump link back to the
original message** — and nothing else. The surrounding message text is never
retained, in the cache, the CSVs, or the HTML.

Names follow the same containment as `members`: aggregation and `--json` are
id-only — display names are resolved from ids in one final batch, only when
rendering the HTML and CSVs, and no flag changes that. A member who has since
left the guild still keeps their link in the report; only their id shows, with
no name resolved for it.

One more thing worth knowing before you open a report: Discord's attachment
CDN links are **signed and expire roughly 14 to 22 hours after the scan that
found them** (measured against the live guild) — regardless of how old the
original message is. The report marks every attachment URL as *expiring* for
this reason, right beside its jump link, which is the durable way back to the
original share once the direct link has gone dead. A report rendered from an
old `--from-cache` copy says so rather than presenting stale links as live.

`discord read` and `discord active` are unchanged by any of this — `links` is
a new, additive verb alongside them.

## Paged history, search, and retention

Discord's own API caps a single read at 100 messages. This pipeline — `fetch`,
`read --refresh`, `search`, `coverage`, `sweep`, `purge` — pages a public
channel's full history into the [jlab-mongodb cache](#the-jlab-mongodb-cache)
past that cap, searches it, keeps it honest against live edits/deletions, and
deletes from it on request. Every verb below supports `--json`; the text
examples are for a maintainer reading a terminal, the `--json` ones for an
agent consuming the same run.

### `jlab discord fetch` — page history past the 100-message cap

```bash
jlab discord fetch 1327720920206282864                    # drain to the channel's beginning
jlab discord fetch 1327720920206282864 --until 2026-08-25  # only back to this date
jlab discord fetch 1327720920206282864 --max-messages 500 --json
```

`--until` bounds how far back the drain goes (default: the channel's
beginning); `--max-messages` bounds the total messages fetched in this one
invocation (default: unbounded). Either way, `jlab.coverage` decides what is
*actually* requested — a re-run only pages the gaps still missing, never
re-fetching what a previous run already stored. The `--json` payload reports
`stored`/`suppressed` counts and the spans fetched vs. already covered.

### `jlab discord search` — cache-served regex search

```bash
jlab discord search 1327720920206282864 --grep 'orin nano' \
  --since 2026-08-25T00:00:00+00:00 --until 2026-08-26T00:00:00+00:00
jlab discord search 1327720920206282864 --grep 'orin nano' --json
```

`search` never opens a Discord session — it only ever reads what `fetch`
already cached. If the requested window isn't fully covered it says so
(`uncovered: [...]`, `complete: false`) rather than returning an empty result
that would read as a false "no matches" — run `fetch` to fill the gap before
trusting a negative. `--max-matches` stops early; `--timeout` bounds the
regex match itself in a child process so a pathological pattern is killed
rather than hanging.

### `jlab discord read --refresh` — a live re-read, not just a gap fill

```bash
jlab discord read 1327720920206282864              # cache-served, no network call
jlab discord read 1327720920206282864 --refresh     # live re-read of the recent window
jlab discord read 1327720920206282864 --refresh --limit 100 --json
```

Without `--refresh`, `read` never touches Discord — it serves the most recent
`--limit` messages from the cache and reports `complete`/`uncovered` exactly
like `search` does. `--refresh` live re-reads that same window and reconciles
it (edits and new messages stored, deletions removed, coverage widened) so an
edit or deletion *inside* an already-covered window surfaces too, not just
gaps a plain `fetch` would find.

### `jlab discord coverage` — what the cache actually holds

```bash
jlab discord coverage                                       # every channel with coverage
jlab discord coverage 1327720920206282864                   # that channel's covered intervals
jlab discord coverage 1327720920206282864 \
  --since 2026-08-25T00:00:00+00:00 --until 2026-08-26T00:00:00+00:00 --json
```

With no `--since`/`--until`, this lists recorded intervals only — `complete`
is reported as `null` because completeness is meaningless without a window.
Pass both bounds to see the covered/uncovered spans and whether that specific
window is fully cached.

### `jlab discord sweep` — the daily reconciliation pass

```bash
jlab discord sweep
jlab discord sweep --json
```

One idempotent pass over every channel with a coverage record: re-verifies
each is still public and in the configured guild (purging, by id only, one
that is gone, foreign, or newly private — a channel it can't re-verify due to
an outage is left alone and reported incomplete, never purged on ambiguous
evidence), then re-reads every covered interval so edits are applied and
deletions removed — deletion only inside a span re-read *completely*. Meant
to run daily from cron; jlab builds no scheduler of its own:

```cron
# Reconcile the cache with Discord every day at 03:00.
0 3 * * * cd /path/to/jetson-ai-lab-cli && \
  JLAB_MONGO_URI=... JLAB_CACHE_KEY=... DISCORD_BOT_TOKEN=... \
  uv run --extra discord jlab discord sweep --json >> /var/log/jlab-sweep.log 2>&1
```

### `jlab discord purge` — per-person, per-channel, and retention-bound deletion

```bash
jlab discord purge --channel 1327720920206282864          # dry run: previews only
jlab discord purge --channel 1327720920206282864 --yes    # actually deletes
jlab discord purge --author <author_id> --yes --json
jlab discord purge --older-than 90 --yes                  # retention sweep
```

Exactly one target per call: `--author`, `--channel`, or `--older-than DAYS`.
**Without `--yes` the verb is a dry run** — it reports what would be removed
and deletes nothing. With it, it deletes matching cache content, clears (or
trims) the affected coverage so a purged window reads back as a genuine gap,
and removes every report run (`members`/`links`, including a `links`
`-cache` sibling) that mentions the target — whole-run, never row-edited,
since a run is one internally-consistent artifact set. An author purge never
prints the author id back (`target.value` is withheld) and records only a
keyed hash of it in a suppression list, so a racing `fetch`/`sweep` can never
re-admit that author's messages. Meant to run from cron beside the daily
sweep, backing the published retention policy:

```cron
# Drop cached messages and report runs older than the retention window, daily at 03:15.
15 3 * * * cd /path/to/jetson-ai-lab-cli && \
  JLAB_MONGO_URI=... JLAB_CACHE_KEY=... DISCORD_BOT_TOKEN=... \
  uv run --extra discord jlab discord purge --older-than 90 --yes --json >> /var/log/jlab-purge.log 2>&1
```

A worked example of this whole pipeline run for real against the live guild —
an uncovered search, a fetch, the same search returning matches, and a final
purge — is recorded in [`docs/motivating-case.md`](docs/motivating-case.md).

## Make it your own

1. Rename the package `jlab/` and the `jetson-ai-lab-cli`
   CLI/dist name throughout `pyproject.toml`, the package, `tests/`, and
   `sonar-project.properties`.
2. Edit `culture.yaml` with your `suffix` and `backend`.
3. Rewrite `CLAUDE.md` for your agent and run `/init`.
4. Re-vendor only the skills you need from guildmaster (see
   [`docs/skill-sources.md`](docs/skill-sources.md)).

See [`CLAUDE.md`](CLAUDE.md) for the full conventions (version-bump-every-PR,
the `cicd` PR lane, deploy setup).

## License

MIT — see [`LICENSE`](LICENSE).
