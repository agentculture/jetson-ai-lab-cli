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
stderr. A committed public-only channel-map snapshot lives at
[`data/channels.json`](.claude/skills/jetson-discord-scan/data/channels.json).

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
