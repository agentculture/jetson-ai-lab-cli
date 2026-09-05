# jetson-ai-lab-cli

Discord-facing knowledge fetch & index agent for the Jetson AI Lab community — fetches and indexes Jetson AI Lab docs/sources and answers members' questions on Discord.

> **Status:** the read side is real, the rest is still scaffold. The agent can
> **scan the Jetson AI Lab Discord read-only today** (see below); indexing what it
> reads and answering members' questions are not built yet.

## What you get

- **A read-only Jetson AI Lab Discord scanner** — the `jetson-discord-scan` skill,
  the first slice of the agent's actual job. See
  [Jetson AI Lab Discord](#jetson-ai-lab-discord-read-only).
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
