# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is (and isn't yet)

**Intended agent:** `jetson-ai-lab-cli` — a Discord-facing knowledge fetch & index
agent for the Jetson AI Lab community. It is meant to fetch and index Jetson AI
Lab docs/sources and answer members' questions on Discord.

**Current state:** none of that domain functionality exists yet. This repo is the
unmodified **AgentCulture "culture-agent-template" scaffold** (see `git log`:
*"scaffold jetson-ai-lab-cli from culture-agent-template"*). What's actually here
today is a generic, dependency-free **agent-first CLI** plus the mesh-agent
plumbing (identity, skill kit, CI/deploy baseline). Building the Discord/Jetson
functionality means *adding* verbs/nouns to this CLI (or new subsystems) on top
of the scaffold — the patterns below are how you do that.

Be honest about this gap: don't describe the Discord/index features as if they
exist. The README's "What you get" section still describes the template, not a
shipped knowledge agent.

## Running the CLI — the command-name trap

The installed console script is **`jlab`**, not `jetson-ai-lab-cli`:

```bash
uv run jlab whoami          # correct
python3 -m jlab whoami      # also works (jlab/__main__.py)
```

There is **no `jetson-ai-lab-cli` binary**. But `argparse`'s `prog=` is set to
`"jetson-ai-lab-cli"`, so `--help`/usage text, the `learn` output, the `explain`
catalog, and the README all *print* commands as `jetson-ai-lab-cli whoami …`.
Those strings are the display name, not the invocation. (The PyPI distribution
name is yet a third spelling: `jetson-ai-lab`. Python package: `jlab/`.)

This inconsistency is a known scaffold artifact — when you build out the domain,
reconciling the four names (`jlab` script / `jetson-ai-lab` dist / `jlab` package
/ `jetson-ai-lab-cli` prog) is a reasonable cleanup, but don't change it casually:
tests assert `usage: jetson-ai-lab-cli` and `nick: jetson-ai-lab-cli`.

## Commands

Python 3.12+, managed with **uv**. Runtime has **zero third-party dependencies**
(`dependencies = []` in `pyproject.toml`); `teken`, pytest, linters are dev-only.

```bash
uv sync                                          # create .venv, install dev deps
uv run pytest -n auto                            # full suite (xdist parallel)
uv run pytest tests/test_cli.py::test_whoami_text  # a single test
uv run pytest -n auto --cov=jlab --cov-report=term # with coverage (CI gate: fail_under=60)
uv run teken cli doctor . --strict               # the agent-first rubric gate CI runs
```

Lint (all run in CI's `lint` job; match them locally before a PR):

```bash
uv run black --check jlab tests
uv run isort --check-only jlab tests
uv run flake8 jlab tests
uv run bandit -c pyproject.toml -r jlab
markdownlint-cli2 "**/*.md" "#node_modules" "#.local" "#.claude/skills" "#.teken"
```

Prefer the vendored skills for routine ops: `run-tests` (pytest+coverage),
`version-bump` (required every PR — see below), `cicd` (the PR lane on `agex pr`),
`sonarclaude` (quality-gate queries).

## Architecture

A small, single-package CLI under `jlab/`. The design goal is **agent-first**:
every output is machine-parseable, errors are structured, nothing leaks a
traceback. Read these four contracts before adding code — they're load-bearing.

**Entry & dispatch (`jlab/cli/__init__.py`).** `main(argv)` builds the parser,
parses, and routes to `_dispatch`, which calls the handler and translates *every*
exception into a clean exit. `_CliArgumentParser` overrides argparse's `.error()`
so even parse-time failures (unknown verb, bad flag) go through the structured
error format and exit `1` instead of argparse's default stderr/exit-2. Because
`--json` errors happen before `args.json` exists, `main()` pre-scans raw argv and
sets the class-level `_json_hint`.

**Command modules (`jlab/cli/_commands/`).** One module per verb, each exposing a
`register(sub)` that adds its subparser and a `cmd_*(args) -> int | None` handler.
`_build_parser()` calls each `register()`. Verbs today: `whoami`, `learn`,
`explain`, `overview`, `doctor`, and the `cli` **noun group** (`cli overview`).
The noun group exists only to satisfy the rubric (`overview_cli_noun_exists`).

**Error contract (`jlab/cli/_errors.py`).** All failures raise `CliError(code,
message, remediation)`. `_dispatch` catches it, and wraps any *other* exception
into a `CliError` so no Python traceback ever reaches the user. Exit-code policy
lives here and is the single source of truth: `0` success, `1` user-input error,
`2` environment/setup error, `3+` reserved.

**Output contract (`jlab/cli/_output.py`).** Strict split: **results → stdout,
errors/diagnostics → stderr, never mixed.** `emit_result` / `emit_error` /
`emit_diagnostic`. `--json` routes structured payloads to the same streams.
Text-mode errors render as `error: <msg>` + `hint: <remediation>` (the `hint:`
prefix is required by the rubric).

**Explain catalog (`jlab/explain/`).** `catalog.py` is a dict keyed by
command-path tuples (`("cli","overview")`) → verbatim markdown; `resolve()` looks
up a path or raises `CliError`. A test (`test_every_catalog_path_resolves`)
asserts every registered path has an entry — keep the catalog in sync with the
verbs you add.

**Identity (`culture.yaml` + `whoami.py`).** `culture.yaml` declares the mesh
agent (`suffix: jetson-ai-lab-cli`, `backend: claude`). `whoami`/`doctor` read it
**without a YAML dependency** (hand-parsed `key: value` lines) to keep runtime
deps empty, and locate it by walking up from `__file__` (not the caller's CWD),
so a wheel install with no `culture.yaml` falls back to literal defaults. `doctor`
mirrors `steward doctor`'s invariants: prompt-file-present, backend-consistency
(`claude`→`CLAUDE.md`, `acp`→`AGENTS.md`, `gemini`→`GEMINI.md`), skills-present.

### Adding a verb or noun (the recipe)

1. New module in `jlab/cli/_commands/` with `register(sub)` + a `cmd_*` handler.
   Add a `--json` flag; emit via `_output` helpers; raise `CliError` on failure.
2. Call its `register()` from `_build_parser()` in `jlab/cli/__init__.py`.
3. Add a markdown entry (and any sub-paths) to `jlab/explain/catalog.py::ENTRIES`.
4. If the verb is an action-verb under a *noun*, that noun must also expose
   `overview` (rubric rule). Descriptive verbs (`overview`) must accept and
   ignore a stray target path — never hard-fail on a bad path.
5. Add tests in `tests/`; run `teken cli doctor . --strict` to confirm the
   rubric still passes.

## The agent-first rubric (don't break it)

CI's `lint` job runs `teken cli doctor . --strict` — the seven-bundle rubric. Any
new surface must keep these true, or the build fails:

- every command supports `--json`;
- stdout = results, stderr = diagnostics/errors, never mixed;
- errors emit `error:`/`hint:` (text) or `{code,message,remediation}` (JSON), no
  tracebacks;
- `learn` stays ≥200 chars and mentions purpose, command map, exit codes,
  `--json`, and `explain`;
- every noun with action-verbs exposes `overview`; descriptive verbs don't
  hard-fail on a bad target;
- every `explain` path resolves.

## Conventions

- **Zero runtime dependencies.** Keep `dependencies = []`. If the Discord/index
  work needs libraries, that's a deliberate decision to discuss — don't quietly
  add deps to the runtime package.
- **`from __future__ import annotations`** at the top of every module.
- **Cite-don't-import skills.** `.claude/skills/` is vendored from **guildmaster**
  (with three skills originating in **devague**). Provenance + re-sync procedure:
  `docs/skill-sources.md`. Don't hand-edit script bodies; re-sync from upstream.
  Every `SKILL.md` must carry `type: command` (load-bearing for the
  culture/claude backend's `core.skill_loader`).

## CI & publishing

- **`.github/workflows/tests.yml`** — three jobs: `test` (pytest + coverage,
  optional SonarCloud scan gated on `SONAR_TOKEN`), `lint` (black/isort/flake8/
  bandit/markdownlint + the rubric gate), and `version-check` (PR-only).
- **SonarCloud** gates CI when `SONAR_TOKEN` is set (`sonar.qualitygate.wait=true`
  in `sonar-project.properties`, project key `agentculture_jetson-ai-lab-cli`).
  Token-less repos and fork PRs skip the scan and stay green.
- **`.github/workflows/publish.yml`** — PyPI **Trusted Publishing** (OIDC, no
  tokens). PRs publish a `.devN` build to TestPyPI; pushes to `main` publish to
  PyPI. Triggers only on `pyproject.toml` / `jlab/**` changes.

## Git / PR workflow

Branch → implement → **bump the version** → PR (via the `cicd` skill) → address
review → merge. The AgentCulture rule, enforced by the `version-check` CI job:
**every PR bumps the version in `pyproject.toml`** — even docs/config/CI-only
changes — or merge is blocked (it'd otherwise fail the PyPI publish). Use the
`version-bump` skill (updates `pyproject.toml` + prepends a Keep-a-Changelog
entry to `CHANGELOG.md`). When posting online on the user's behalf, the `cicd`
scripts auto-sign as `- jetson-ai-lab-cli (Claude)`; don't sign manually in PR
bodies they author.
