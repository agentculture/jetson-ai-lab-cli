# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/). This project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.0] - 2026-09-05

### Added

- **`jlab discord links` — what the Jetson AI Lab Discord has been sharing.**
  Sweeps public text channels over a window (default 90 days, `--since`) and
  writes one run's HTML report plus a **flat CSV** (one row per share) and a
  derived **per-URL summary CSV** into a gitignored per-run subdirectory,
  printing the HTML path. URLs are extracted from four deduped sources:
  message content, `attachments[].url`, `embeds[].url`, and embed
  description/field bodies — the last because Discord's auto-preview embeds
  merely duplicate a URL already in the text, while rich embeds carry
  `url=None` and hide their links in the body. Bots are excluded by default
  (`--include-bots` opts in). **It issues no verdict:** no "most shared", no
  ranked domains, no recommended reading.
- **The content-retention inversion, stated on purpose.** The members path
  discards message content after measuring its length; a links report cannot
  exist under that rule, because a URL *is* content. What is retained per
  link: url, channel, timestamp, thread reference, author id and a jump link.
  What is never retained: the surrounding message text.
- **`--from-cache <run-id>` re-renders a previous run without a second scan.**
  A 90-day sweep costs a large share of a bot token shared with other mesh
  agents, so each run caches its extraction (id-only, no display names) to a
  sibling `<run-id>-cache` directory. A cached render shows the **scan**
  timestamp, not the render timestamp, and states the same coverage figures as
  the run that wrote it.
- **Attachment URLs are marked as expiring.** Measured against the live guild:
  Discord CDN attachment links are signed at fetch time and expire roughly
  14-22 hours later regardless of message age — a 143-day-old message's
  attachment URL expired in 14.7h. They are never rendered as clickable
  anchors; the jump link beside them is the durable pointer.
- **CSV output for `jlab discord members` too**, through the same shared
  writer — one implementation, not two.

### Changed

- **Report artifacts now land in a per-run subdirectory** (`data/reports/
  <verb>/<run-id>/`) rather than loose files in a shared directory. This makes
  each run's whole artifact set land through a single atomic `os.replace`, so
  a killed run leaves either a complete set or nothing — never a complete CSV
  beside a missing or stale HTML. **This changes the output path of the
  already-shipped `jlab discord members` verb.**
- `_serialize_message` now carries attachment URLs, embed URLs and bodies, a
  jump link, and channel and thread identity. Keys were only added, never
  renamed or removed, so existing `--json` consumers of `channels` / `read` /
  `active` keep working.
- `resolve_authors` fans out under a bounded semaphore instead of a serial
  loop. The serial version measured 286s of a 302s run — 869 authors at
  ~329ms each, 94.8% of wall-clock spent waiting one round trip at a time.

### Fixed

- **A registered verb with no `explain` entry used to ship green.** The
  existing guard iterated the catalog's paths, proving every entry resolves —
  it could never notice a registered verb *missing* an entry. The converse
  test now walks the parser's registered subcommands and asserts each has one.
- CSV fields beginning with `=`, `+`, `-`, `@`, tab or carriage return are
  prefix-escaped, so a hostile display name or URL opens as inert text rather
  than executing as a formula in Excel or Sheets.
- Rendered URLs are scheme-filtered: only `http`/`https` become anchors.
  `javascript:`, `data:` and control-character smuggling render as inert text
  — escaping makes a hostile URL *display* safely, but only scheme filtering
  stops a click from executing it.

## [0.6.0] - 2026-09-04

### Added

- **`jlab discord members` — anonymous participation statistics for the Jetson
  AI Lab Discord.** Scans public text channels over a window (default 90 days,
  `--since`), aggregates by `author.id` only, and writes a gitignored HTML
  report with inline-SVG diagrams, printing its path. **It issues no verdict:**
  no ranking, no score, no presenter shortlist — the four signals (message
  count, distinct channels, question starts, length-based substance) are shown
  side by side for a human or agent to read. Names are resolved from ids in one
  batched final step, so the scan and aggregation stages never hold a username;
  `--json` stays id-only because stdout redirection is uncontainable. Bots are
  excluded via `author.bot`, and authors who have left the guild are excluded
  by default (`--include-departed` overrides, and the omitted count is always
  reported).
- Windowed history paging past Discord's 100-message cap, with an explicit
  per-channel `ok` / `partial` / `failed` status so a failed read is never
  mistaken for an empty channel.
- `--concurrency` on `jlab discord active` and the new members scan; both
  fan-outs are now bounded by an `asyncio.Semaphore` (default 4) with 429
  backoff.
- Vendored the five missing devague legs (`scope`, `challenge`, `deviate`,
  `validate-delivery`, `summarize-delivery`), cited from devague rather than
  guildmaster — guildmaster's copies still document a six-leg flow and lack
  `validate-delivery` entirely. Rationale in `docs/skill-sources.md`.
- `.pr_agent.toml`: devague's ledger-audit rules verbatim plus this repo's
  invariants (zero runtime deps, the agent-first rubric, read-only/public-only
  Discord).
- Spec, plan and delivery summary under `docs/specs/`, `docs/plans/` and
  `docs/deliveries/`.

### Changed

- `jlab discord active` output shape is unchanged, but its fan-out is now
  semaphore-bounded and its `--par 8` documentation — a flag that never
  existed — is replaced with the measured `--concurrency 4` default.
- README documents the expected runtime (~5 minutes for a 90-day run) with a
  per-phase breakdown, rather than leaving it to be discovered by running it.

### Fixed

- `members_report_path()` refused to constrain its `filename` argument, so a
  name containing `..` or a path separator escaped the gitignored report
  directory entirely. Now refused with a structured error.
- The report's "Window end (newest message considered)" showed the scan finish
  time, overstating the range actually covered.
- A channel holding exactly the message cap was reported as `partial` when its
  window had in fact been read in full.
- `test_seam_missing_extra_raises_env_error` inherited the `[discord]` extra's
  absence from the environment; it now simulates absence and holds either way.
- Markdown lint now ignores `.venv/**`, `docs/plans/**` and `docs/deliveries/**`.

## [0.5.0] - 2026-06-23

### Added

- **Vendored the `remember` + `recall` memory skills from eidetic-cli**
  (cite-don't-import) — the write/read halves of eidetic's shared
  `~/.eidetic/memory` surface, so this agent (Claude and its colleague backend)
  can persist facts across sessions and recall them later, sharing one store.
  `remember` drives `eidetic remember` (idempotent upsert of one JSON record or
  an NDJSON batch on stdin, dedup by id + content hash); `recall` drives
  `eidetic recall` with four search modes — exact / approximate / keyword /
  hybrid — each hit carrying text, full provenance metadata, a relevance score,
  and a freshness signal. The `.sh` wrappers are byte-verbatim from eidetic-cli
  (their first-party origin); each `SKILL.md` is localized only in the
  illustrative `--scope <nick>` examples (Provenance keeps "First-party to
  eidetic-cli"). Both default to this agent's PRIVATE scope, reading the suffix
  from `culture.yaml`. Runtime dep: the `eidetic` CLI on PATH (else a local
  eidetic-cli checkout with `uv`). Propagated by rollout-cli's `eidetic-memory`
  recipe.

## [0.4.0] - 2026-06-19

### Added

- `jlab discord` noun group — read-only, public-only scan of the Jetson AI Lab Discord (`channels`, `read`, `active`, `doctor`, `overview`), folding the jetson-discord-scan skill into the agent-first CLI.
- Optional `[discord]` extra (`discord-bot-cli`), lazy-imported — core install stays dependency-free.
- `jlab/cli/_discord.py` adapter isolating all discord-bot-cli coupling behind one module.

### Changed

- jetson-discord-scan skill reduced to a thin shim delegating to `jlab discord`; `channels.py` removed (logic moved into the CLI).
- CLAUDE.md notes the AgentCulture-sibling optional-dep exception to the zero-runtime-deps rule.
- markdownlint ignores devague-generated artifacts (docs/specs, docs/reviews, .devague).

### Fixed

- Committed `data/channels.json` snapshot dropped — the tool pulls fresh and never commits Discord data.

## [0.3.0] - 2026-06-19

### Added

- Vendored ask-colleague skill — drive the sibling colleague CLI to hand a scoped repo task to a *different* backend/model for a diverse second opinion or handoff: read-only explore/review (isolated in a throwaway git worktree), write [--apply|--pr], feedback (ROI grade loop), clean (reap crashed colleague/* branches), and the pilot verbs monitor/guide/stop over colleague flight. Cite-don't-import (origin: colleague, re-broadcast via guildmaster).

### Changed

- docs/skill-sources.md now documents ask-colleague provenance (origin colleague, re-broadcast via guildmaster), the cite-don't-import re-sync rule, and the known upstream nits filed at sync time (CLI-contract → colleague; shellcheck → guildmaster) rather than patched locally.

## [0.2.0] - 2026-06-19

### Added

- jetson-discord-scan skill — read-only shallow scan of the Jetson AI Lab Discord (public channels only). Lists public channels, reads a channel's recent messages, and ranks public text channels by last-30-day traffic, wrapping the sibling discord-bot-cli read verbs. scripts/channels.py reuses discord_bot_cli.discord_client.run() as a library to add a public/private flag the stock channel list omits.
- data/channels.json — committed public-only channel-map snapshot of the Jetson AI Lab Discord (118 public channels).
- docs/jetson-ai-lab-server.svg — an at-a-glance concept map of what the Jetson AI Lab community is about (hardware, workloads, tooling, robotics, community), embedded in the README. Drawn from the read-only scan.

### Changed

- CLAUDE.md + README.md now document the read-only, public-only Discord domain capability (the first slice of real domain functionality) and its load-bearing constraints.
- CLAUDE.md: documented the dual-key explain catalog (explain jlab must resolve) and the SonarCloud Automatic-Analysis-must-stay-off CI footgun.

## [0.1.2] - 2026-05-30

### Changed

- Expanded the seed CLAUDE.md placeholder into a full runtime prompt — CLI architecture, the four agent-first contracts (dispatch/error/output/explain), the rubric constraints, build/test/lint commands, conventions, and the CI/publish + version-bump-per-PR workflow.

### Fixed

- `explain jlab` now resolves — added the console-script name (`jlab`) as an explain-catalog self-key so `teken cli doctor . --strict` (the CI rubric gate) passes; it derives the tool name from `[project.scripts]`, not the `jetson-ai-lab-cli` display name.

## [0.1.1] - 2026-05-26

### Changed

- **CI gates on the SonarCloud quality gate**
  ([issue #3](https://github.com/agentculture/jetson-ai-lab-cli/issues/3)) —
  added `sonar.qualitygate.wait=true` to `sonar-project.properties` so a failing
  gate fails the `test` job when `SONAR_TOKEN` is set. Token-less repos and fork
  PRs remain green (the scan step is guarded by `if: env.SONAR_TOKEN != ''`).

## [0.1.0] - 2026-05-26

### Added

- **Onboarded into the AgentCulture mesh** ([issue #1](https://github.com/agentculture/jetson-ai-lab-cli/issues/1)).
- **Agent-first CLI** cited from teken's (`afi-cli`) `python-cli` reference
  (`teken cli cite`) — verbs `whoami`, `learn`, `explain`, `overview`, `doctor`,
  and the `cli` noun group. Runtime is self-contained (`dependencies = []`);
  `teken>=0.8` is a dev dependency only. Passes the seven-bundle agent-first
  rubric (`teken cli doctor . --strict`). `doctor` checks the agent-identity
  invariants (prompt-file-present, backend-consistency, skills-present).
- **Mesh identity**: `culture.yaml` (`suffix: jetson-ai-lab-cli`,
  `backend: claude`) and the matching `CLAUDE.md` prompt file.
- **Canonical guildmaster skill kit** (11 skills) vendored under
  `.claude/skills/` (cite-don't-import): `agent-config`, `assign-to-workforce`,
  `cicd`, `communicate`, `doc-test-alignment`, `pypi-maintainer`, `run-tests`,
  `sonarclaude`, `spec-to-plan`, `think`, `version-bump`. Every `SKILL.md`
  carries `type: command` (load-bearing for the culture/claude backend);
  `cicd` / `communicate` consumer-identifying prose adapted, all script bodies
  verbatim. Provenance in `docs/skill-sources.md`. Three skills (`think`,
  `spec-to-plan`, `assign-to-workforce`) originate in `devague`, re-broadcast
  via guildmaster.
- **Build + deploy baseline**: `pyproject.toml` (hatchling), `tests/` (pytest,
  xdist, coverage), `.github/workflows/{tests,publish}.yml` (CI rubric/lint gate,
  PyPI Trusted Publishing), `.flake8`, `.markdownlint-cli2.yaml`,
  `sonar-project.properties`, and `.claude/skills.local.yaml.example`.

### Changed

### Fixed
