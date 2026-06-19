# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/). This project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
