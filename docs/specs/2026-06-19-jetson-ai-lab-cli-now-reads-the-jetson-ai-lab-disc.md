# jetson-ai-lab-cli now reads the Jetson AI Lab Discord directly from the CLI: a 'jlab discord' noun group (channels / read / active) makes the read-only, public-only scan a first-class agent-first command instead of a helper skill script — with no new runtime dependencies.

> jetson-ai-lab-cli now reads the Jetson AI Lab Discord directly from the CLI: a 'jlab discord' noun group (channels / read / active) makes the read-only, public-only scan a first-class agent-first command instead of a helper skill script — with no new runtime dependencies.

## Audience

- The jetson-ai-lab-cli agent itself (its Claude backend invoking jlab verbs) and operators/maintainers running jlab by hand; both currently reach the Discord only through the jetson-discord-scan skill's scan.sh.

## Before → After

- Before: Discord read access lives ONLY in the jetson-discord-scan skill (bash scan.sh + channels.py). It is invokable as a skill but is not part of the jlab CLI surface: no 'jlab' verb reads Discord, it is not in the explain catalog, has no --json/error contract enforced by the rubric, and is not covered by pytest.
- After: The same read-only/public-only capability is reachable as 'jlab discord channels|read|active' (plus 'jlab discord overview'), honoring the agent-first rubric (--json everywhere, error:/hint: contract, explain entries, stdout/stderr split) and covered by tests — while scan.sh either remains as a thin shim delegating to the CLI or is retired.

## Why it matters

- The agent's intended job (read -> index -> answer) should be driven through ONE machine-parseable CLI, not split between a CLI and a side-channel bash skill. Folding read into jlab is the first step that makes the pipeline a real CLI and lets later index/answer verbs build on the same dispatch/error/output contracts.

## Requirements

- jlab discord preserves the skill's operational contract: token via DISCORD_BOT_TOKEN env only (never a flag), guild via JLAB_GUILD_ID (default 1326246312072581160), and exit codes 0 ok / 1 user-input / 2 environment (missing token, absent extra, unreadable guild).
  - honesty: A test asserts no Discord secret or private-channel name is needed in source/tests, env names (DISCORD_BOT_TOKEN/JLAB_GUILD_ID) and exit codes 0/1/2 match the skill exactly.

## Honesty conditions

- After shipping, an agent can do the whole read workflow (list public channels, read one channel, rank active channels) via 'jlab discord ...' alone, without invoking the jetson-discord-scan skill.
- Both the Claude backend and a hand operator can run the verbs with only DISCORD_BOT_TOKEN set and a reachable discord-bot-cli — no extra setup beyond what scan.sh already needs.
- grep of jlab/ for 'discord' today returns nothing — Discord read access genuinely exists only in .claude/skills/.
- 'uv run jlab discord active --json' produces JSON equivalent to scan.sh active (same ranking, same fields: id/name/last_post/msgs_in_window/saturated/preview) and 'jlab discord channels' never includes private channels unless --all is passed.
- The discord module follows the standard register()/cmd_* + _output/_errors recipe, so later index/answer verbs reuse the same dispatch/error/output contracts.
- Code review confirms the discord command module exposes no post/react/thread call and public-only is the default in every path; --all is the only private opt-in.
- Each acceptance check (active --json parity with scan.sh, teken strict pass, pytest coverage of the verbs, no private-channel leak) is concretely runnable as a gate, not aspirational.
- pyproject.toml lists discord-bot-cli under dependencies (or its [discord] extra) and CLAUDE.md's Conventions section is updated to note the AgentCulture-dep exception.

## Success signals

- 'uv run jlab discord active --json' returns the same ranked public-channel JSON scan.sh produces; 'teken cli doctor . --strict' still passes (every new path has --json, explain entry, error:/hint:, stdout/stderr split); pytest covers the new verbs; private channels never leak into default output.

## Scope / boundaries

- Scope is READ only: channels/read/active. No write path (post/react/thread) is ever added — the read-only and public-only-by-default constraints are preserved verbatim. NOT in scope: indexing, question-answering, caching/storage of message content, or any change to discord-bot-cli itself.

## Non-goals

- Not changing the four-names situation (jlab script / jetson-ai-lab dist / jlab package / jetson-ai-lab-cli prog) as part of this work, and not modifying or forking discord-bot-cli.

## Assumptions

- Adding discord-bot-cli as a dependency is acceptable per the user's ruling that AgentCulture-internal deps are fine; CLAUDE.md's 'zero runtime dependencies' line will be updated to record this exception rather than silently violated.

## Decisions

- Dependency (refined per colleague review): discord-bot-cli is an OPTIONAL [discord] extra in pyproject ([project.optional-dependencies] discord = ["discord-bot-cli[discord]"]), NOT a hard dep — so jlab core keeps dependencies = [] literally and whoami/learn/explain/doctor install+import with zero deps. The discord handlers lazy-import discord_bot_cli; an absent extra raises CliError(code=2) with an install hint (uv pip install 'jetson-ai-lab[discord]'), never an ImportError traceback. This makes the announcement 'no new runtime dependencies' honest for the core install.
- All coupling to discord-bot-cli's internal API (discord_client.run / parse_id / require_token, and their CliError type) is isolated in ONE adapter module jlab/cli/_discord.py, which lazy-imports the extra and translates discord_bot_cli CliError -> jlab CliError. The discord command module imports only this adapter, so an upstream API change touches one file.
- active fold preserves+improves parallelism: instead of xargs subprocess-per-channel, a single discord_client.run(action) opens one REST session and asyncio.gather()s the per-public-text-channel history reads, then ranks in-process. Same ~20s scale, fewer logins, no temp files.
- Resolve v2: add 'jlab discord doctor' as its own verb (token present + discord-bot-cli importable + guild readable), mirroring scan.sh doctor; it does NOT fold into the top-level jlab doctor.
- Resolve v1 per user: DROP the committed data/channels.json snapshot and never commit pulled Discord data. The verbs pull fresh live data to understand trends; the tool persists no channel maps, summaries, or history into the repo. Reinforces the existing boundary (no caching/storage of message content).
