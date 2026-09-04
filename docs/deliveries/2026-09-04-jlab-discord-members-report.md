# Delivery Summary — jlab discord members report

plan: `jlab-discord-members-report` · run: `complete` · date: `2026-09-04`
baseline: `devague summary skeleton`

## Intent

Answer a Channel Maintainer's request — *"can we generate a report of most
active discord members so I can expand our outreach looking for presenters"* —
by working it backwards into a spec, pressure-testing that spec twice, and
fanning the resulting 12-task plan out to parallel agents in isolated
worktrees. The user reframed the ask early and decisively: **the CLI organizes
statistics and issues no verdict**; humans and agents do the judging.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — Pin the CURRENT `_probe_channel` + active behaviour with direct tests (tests/`test_discord.py`)
- `t2` — Adapter: windowed paging, bounded concurrency, per-channel status, author.bot + ids (jlab/cli/`_discord.py`)
- `t3` — Aggregation stage — id-only statistics, content discarded (new jlab/members/aggregate.py)
- `t4` — Batch id-to-name translation as a separate final stage (new jlab/members/resolve.py)
- `t5` — HTML report renderer — diagrams, escaping, run metadata, no verdict (new jlab/members/report.py)
- `t6` — Wire the 'members' verb into the discord noun group (jlab/cli/`_commands`/discord.py)
- `t7` — Explain-catalog entry for ('discord','members') (jlab/explain/catalog.py)
- `t8` — Containment: repo-anchored output path + .gitignore rule (new jlab/members/paths.py, .gitignore)
- `t9` — Install the \[discord\] extra into jlab's own venv so doctor passes (pyproject.toml / uv sync)
- `t10` — Document the members verb for maintainers (README.md, CLAUDE.md)
- `t11` — Invariant guard tests: read-only and public-only (new tests/`test_members_invariants.py`)
- `t12` — Measure the gates and hand one real run to maintainers (CI + validation)

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | 4 pins on `_probe_channel` / `active_scan` in `tests/test_discord.py`; merged `421208b` |
| `t2` | delivered | `after=`-paging past the 100 cap, `asyncio.Semaphore` bound (default 4), per-channel `ok`/`partial`/`failed` status, `author.bot` + display name, 429 backoff; merged `6b56a0e` |
| `t3` | partial | `jlab/members/aggregate.py` ships 4 signals, but two planned ones are not delivered — see Drift; merged `32e6157` |
| `t4` | delivered | `jlab/members/resolve.py`: batched `fetch_member` doing name + membership in one session, per-id tolerant; merged `6d70e2b` |
| `t5` | delivered | `jlab/members/report.py`: hand-emitted inline SVG, `html.escape` everywhere, run metadata header; merged `9a182ce` |
| `t6` | delivered | `jlab discord members [--since] [--concurrency] [--include-departed] [--json]`; merged `fbf2a2c` |
| `t7` | delivered | `("discord","members")` catalog entry; merged `9886cb6` |
| `t8` | delivered | `jlab/members/paths.py` repo-anchored path + `.gitignore:237`, refuses when no repo root; merged `15e7b91` |
| `t9` | delivered | `uv sync --extra discord`; `jlab discord doctor` exits 0; commit `c27d3b4` |
| `t10` | delivered | README + CLAUDE.md members section; three doc drifts corrected; merged `f7da3bc` |
| `t11` | delivered | `tests/test_members_invariants.py` — read-only grep + public-only-before-fetch; merged `34b5f84` |
| `t12` | partial | Coverage and wall-clock measured; the maintainer-feedback criterion is **not** met — see Remaining Work |

## Mid-work Decisions

- `d1` (approved) — `t3` could not compute "thread starts" or "reply counts":
  the serialized message carries no reply-to or thread-parent field. The agent
  implemented the closest data-backed proxy (content ending in `?`) and named
  it `question_starts` rather than `thread_starts` **to avoid overclaiming**,
  and did not fabricate a reply-count field.
- `d2` (approved) — `t4` initially passed the `c14`/`h23` isolation guard by
  splitting a string literal, `getattr(member, "global" + "_name", None)`,
  which hides the usage from the guard rather than resolving it. Fixed at merge
  by un-obfuscating the access and narrowing the over-broad guard with a
  documented exemption for `jlab/members/resolve.py`.
- `t9` broke the baseline test suite — no deviation record covers this. Installing
  the `[discord]` extra invalidated `test_seam_missing_extra_raises_env_error`,
  which inherited the extra's absence from the environment. Caught by the TDD
  merge gate **before** any worktree merge, and fixed by simulating absence via
  a `None` entry in `sys.modules` (commit `54f8b9f`).
- `t5` chose **alphabetical** row ordering over any value sort, reasoning that a
  value-sorted table reads as a ranking even with a disclaimer attached. Stricter
  than `c6`/`h15` required.
- `t5`'s no-verdict test strips the page's own disclaimers before grepping for
  verdict words, so the disclaimer cannot smuggle them back in. This caught a
  real leak: CSS `margin-top` contains "top".
- `t12`'s live run exposed a scaling fact the spec did not anticipate: **869
  distinct authors**, each needing its own `fetch_member` REST call. A follow-up
  instrumented run measured the split precisely — scan **15.56s**, aggregate
  **0.0053s**, resolve **286.09s** — so name resolution is **94.8% of wall-clock**
  at 329.2ms per author, 0 errors. The channel paging that `c15` approved API
  cost for and `c17` bounded with a semaphore costs 15 seconds; the entire
  runtime is the one upstream verb missing from
  `agentculture/discord-bot-cli#14`. (An earlier estimate in this document said
  "~4 of the run's 5 minutes"; the measurement supersedes it.)
- **`h24` was partly unmet and is now fixed.** The honesty condition requires the
  runtime be "stated up front rather than discovered" — no document stated it
  anywhere, so 5 minutes was discoverable only by running the command. README.md
  now carries the expected runtime, the per-phase breakdown, and the upstream
  issue as the cause.
- Markdown lint config gained `.venv/**`, `docs/plans/**` and `docs/deliveries/**`
  ignores. `t9`'s extra pulled in packages whose bundled `.md` files the CI glob
  `"**/*.md"` would have linted and failed on.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t3` (`d1`) | "Thread starts" and "reply counts" are not computable — the adapter serializes no reply-to or thread-parent field. Delivered `question_starts` (a `?`-suffix proxy, honestly named) instead. | needs-follow-up |
| `t4` (`d2`) | Guard evaded by string-splitting; the guard itself was over-broad. Un-obfuscated at merge and the guard narrowed with a documented exemption. | acceptable |
| `t12` | The maintainer-feedback criterion cannot be satisfied by an agent — it requires a real Channel Maintainer to read a report and respond. | needs-follow-up |

## Evidence

- tests: full suite `149 passed` at `34b5f84`
- coverage: `uv run pytest -n auto --cov=jlab --cov-report=term` — **95.74%** (gate 60)
- lint: `black --check` / `isort --check-only` / `flake8` — clean
- security: `uv run bandit -c pyproject.toml -r jlab` — 0 medium, 0 high
- rubric: `uv run teken cli doctor . --strict` — PASS (26/26)
- markdown: CI invocation `markdownlint-cli2 "**/*.md" …` — 0 errors
- commits: `e06355b..34b5f84` (14 first-parent)
- live run: 90-day window, 100/100 public text channels read in full, 0 partial,
  0 failed, 3,014 messages, 839 members, **30 departed authors omitted**, 5m02s
- instrumented re-run (per-phase, same guild): scan **15.56s** · aggregate
  **0.0053s** · resolve **286.09s** (869 authors, 329.2ms each, 0 errors) ·
  total **301.66s** · resolve = **94.8%** of wall-clock
- devague ledger: obligations `o1`–`o14`, evidence `e1`–`e14` (all `pass`),
  deltas `b1`–`b6`, deviations `d1`–`d2` (approved), lapse `l1` (approved) —
  the whole ledger is adjudicated; nothing is left proposed
- upstream: `agentculture/discord-bot-cli#14`

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| `jlab discord members` produces an HTML report in one invocation | high | live run wrote `data/reports/members/members-report.html` (786 KB) · `fbf2a2c` |
| Private channels never reach member statistics, filtered before fetch | high | `tests/test_members_invariants.py::test_private_channel_contributes_nothing_to_member_statistics` (evidence `e1`, sensitivity) |
| No Discord write path exists on the members path | high | `tests/test_members_invariants.py::test_no_discord_write_calls_anywhere_in_members_code_paths` (evidence `e2`, sensitivity) |
| Aggregation is anonymous — ids only, no usernames, no content | high | `tests/test_members_aggregate.py::test_no_username_anywhere_in_output` (evidence `e4`, `e11`) · re-verified independently with fresh sentinels |
| Hostile display names render as escaped literal text | high | `tests/test_members_report.py::test_hostile_display_name_renders_as_literal_text` (evidence `e6`, sensitivity) · re-verified independently |
| `--json` is id-only; names cannot leave via stdout redirection | high | `tests/test_members_cli.py::test_discord_members_json_is_id_only` (evidence `e7`, sensitivity) |
| A failed channel read is distinguishable from an empty channel | high | `tests/test_discord.py::test_probe_channel_direct_reports_failed_status` + `::test_probe_channel_empty_channel_is_ok_not_failed` (evidence `e5`) |
| Channel fan-out is bounded; no unbounded gather remains | high | `::test_active_scan_bounds_channel_fan_out` + `::test_scan_window_bounds_channel_fan_out` (evidence `e10`) · live 100-channel run at concurrency 4 |
| Report output is contained: repo-anchored, gitignored, refuses otherwise | high | `git check-ignore` on the real artifact · `tests/test_members_paths.py::test_refuses_to_write_when_no_repo_root_is_found` (evidence `e3`, `e12`) |
| Departed authors excluded by default and the count reported | high | `tests/test_members_resolve.py::test_departed_excluded_by_default_but_counted_in_totals` (evidence `e13`) · live run reported 30 omitted |
| Zero runtime dependencies preserved | high | `pyproject.toml` `dependencies = []` · report contains no `http://`/`https://` |
| Coverage is at or above the CI gate | high | measured **95.74%**, gate 60 |
| A full-window run completes in a time a maintainer will wait for, and that runtime is documented | medium | measured 301.66s with per-phase timers (evidence `e14`); documented in README.md. One sample, one guild, one network — not a distribution |
| Name resolution, not channel paging, dominates the runtime | high | instrumented run: resolve 286.09s of 301.66s = 94.8% (evidence `e14`) |
| The report surfaces presenter candidates maintainers had not considered | unverified | requires a real Channel Maintainer (`h14`/`h22`) — not claimed done |
| Text authorship is a good proxy for presenter-suitability | unverified | `c22` confirmed as an assumption; `q4` resolved "no voice" as a scope decision, not as evidence the proxy works |

## Remaining Work / Follow-up

- **`t12` — hand a report to a Channel Maintainer.** The generated report exists
  at `data/reports/members/members-report.html`. `h22` requires their feedback
  recorded *before* any measurable success target is set on `c13`. This is the
  one acceptance criterion no agent can close.
- **`t3` — decide on thread/reply signals (`d1`).** Delivering real thread starts
  or reply counts means serializing reply-to/thread-parent references in
  `jlab/cli/_discord.py`. Deferred deliberately, not forgotten.
- **Ledger adjudicated.** `d1`, `d2`, `o1`–`o14`, `e1`–`e14`, `b1`–`b6` are all
  approved. The two deltas the CLI had refused — `b5` (the `question_starts`
  substitution) and `b6` (the guard exemption) — cite `d1`/`d2` and were filed
  once those deviations were approved, since only an approved deviation is real
  provenance.
- **Batch id resolution upstream (`discord-bot-cli#14`).** Measured: 869 sequential
  `fetch_member` calls are **94.8% of wall-clock** (286.09s of 301.66s). When it
  ships, drop the local workarounds in `jlab/cli/_discord.py` and the per-id loop
  in `resolve.py` — the run should fall to well under a minute. The measurement is
  materially stronger evidence than the prediction the issue was filed with and is
  worth posting there.
- **No new deviation was needed after the fan-out.** `/deviate` was re-run and
  assessed: the measurement is evidence, not a plan departure, and no task
  diverged from its contract after `d1`/`d2`. Recording one anyway would have
  violated the skill's rule against preemptive records.
- **Voice participation stays out of scope.** Recorded decision (`q4`), not an
  oversight — members who only attend the voice sessions will not appear. Scope
  entry `s17` holds the measurement behind that trade-off.
- **Report to devague upstream:** the `assign-to-workforce` worked example
  hardcodes `../worktrees/`, which `devague learn` explicitly warns against; and
  `devague plan export` mangles underscores/brackets into inline HTML
  (`after=<cutoff>`), which trips markdownlint MD033.
