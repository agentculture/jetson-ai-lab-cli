# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/). This project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
