# Build Plan — jlab discord members report

slug: `jlab-discord-members-report` · status: `exported` · from frame: `jlab-discord-members-report`

> jetson-ai-lab-cli can now generate a report of the most active members of the Jetson AI Lab Discord, so Channel Maintainers can expand outreach looking for presenters.

## Tasks

### t1 — Pin the CURRENT `_probe_channel` + active behaviour with direct tests (tests/`test_discord.py`)

- instruction: Do NOT change `_probe_channel` here — this task only pins today's behaviour so t2's change is visible. Test `_probe_channel` directly with a fake channel object; do not go through the stubbed `active_scan` path that tests/`test_discord.py`:258,285 uses.
- covers: c25, h34, h12
- acceptance:
  - A test calls `_probe_channel` directly (not via a stubbed `active_scan`) and asserts today's swallow-returns-empty behaviour
  - A test asserts 'discord active' output shape is unchanged, so a later contract change is visible in the diff
  - A test documents that `active_scan` exposes no per-person/author aggregation today

### t2 — Adapter: windowed paging, bounded concurrency, per-channel status, author.bot + ids (jlab/cli/`_discord.py`)

- instruction: The single riskiest file in the ship. Land it AFTER t1's pins exist. Keep every local workaround (author.bot, display name, after-paging) isolated in jlab/cli/`_discord.py` so upstream discord-bot-cli#14 can replace it in one file. Do not widen the bare-except swallow to the members path — replace it with an explicit per-channel status.
- depends on: t1
- covers: c7, c12, c14, c15, c17, c18, h1, h2, h3, h8, h16, h21, h23, h24, h26, h27
- acceptance:
  - history() pages with after=<cutoff> past the 100-message cap; a channel whose window cannot be fully paged is marked incomplete, never silently truncated
  - Channel fan-out is bounded by an asyncio.Semaphore with a conservative default flag; no unbounded gather remains
  - Each channel carries a status (ok/partial/failed + reason); a failed read is distinguishable from an empty channel
  - Author serialization includes bot and a display name; bots are excluded via author.bot, never a name heuristic
  - Public-channel filtering happens BEFORE any message fetch — a test asserts a private channel is never fetched
  - The local workaround is isolated in this one file so upstream discord-bot-cli#14 can replace it in a single-file change

### t3 — Aggregation stage — id-only statistics, content discarded (new jlab/members/aggregate.py)

- instruction: New file; do not edit jlab/cli/`_discord.py` (t2 owns it). Compute message length at read time and discard content immediately — never carry text through. Do NOT reuse `_rank_channel`'s 'preview' field: it holds verbatim content and would leak members' words into the report.
- depends on: t2
- covers: c16, c20, h9, h29
- acceptance:
  - Aggregates by author.id only; a test asserts no username appears anywhere in the aggregate output
  - Substance uses message LENGTH and reply counts computed at read time; a test asserts no message content reaches the aggregate
  - Emits all four signals side by side (count, distinct channels, thread/question starts, substance) with no combined score

### t4 — Batch id-to-name translation as a separate final stage (new jlab/members/resolve.py)

- instruction: New file. One batched session does BOTH id->name resolution and the guild-membership check (guild.`fetch_member` — verified working under the read-only token, returns nick and `joined_at`). Prefer nick > `global_name` > username. Tolerate per-id failure; never fail the whole batch.
- depends on: t2
- covers: h25, c28, h37
- acceptance:
  - Resolution is a distinct stage: the aggregate can be dumped and inspected on its own and contains only ids
  - Resolves ids in batch within one client session, tolerant per id (an unresolvable id yields an error entry, not a failed batch)
  - Membership check runs in the SAME batched session as name resolution (guild.`fetch_member`), not a second pass
  - Display name prefers the per-guild nick, falling back to `global_name` then username
  - Authors who have left the guild are excluded by default; they still count toward coverage totals and the excluded count is reported

### t5 — HTML report renderer — diagrams, escaping, run metadata, no verdict (new jlab/members/report.py)

- instruction: New file, largest code block in the ship — write tests as you go or t12's coverage gate will fail. Stdlib only: html.escape for every user-derived string, hand-emitted SVG or a CDN chart lib in the page. Nothing may read as a verdict: no 'top', 'best', 'most active', or 'recommended' anywhere in the rendered output.
- depends on: t3
- covers: c6, c10, c11, c13, c19, c23, h4, h6, h11, h15, h19, h20, h28, h31
- acceptance:
  - Renders in Chrome from the local filesystem with no build step; pyproject dependencies stay \[\]
  - Every user-derived string is escaped with html.escape; a test with a hostile display name asserts it renders as literal text
  - Header carries run metadata: scan timestamp, window start/end, channels attempted/covered/failed
  - States its own coverage on its face (public text channels only) so it is not mistaken for whole-guild coverage
  - No field, column header, sort order, or diagram title implies a verdict; any default sort is labelled as presentation
  - Readable by a maintainer without reading Python or JSON

### t6 — Wire the 'members' verb into the discord noun group (jlab/cli/`_commands`/discord.py)

- instruction: Follows the repo recipe in CLAUDE.md: register(sub) + `cmd_`\* handler, --json flag, `_output` helpers, CliError on failure. --json MUST stay id-only — name resolution happens only on the render path, or c24's containment hole reopens via stdout redirection.
- depends on: t3, t4, t5
- covers: c1, c4, c24, h13, h33
- acceptance:
  - ONE invocation produces the finished HTML and prints its path; no pipeline for the maintainer to assemble
  - --json stays id-only (no name resolution) so person-level names cannot leave via stdout redirection
  - --since flag with a 90-day default; results to stdout, diagnostics to stderr; failures raise CliError
  - A flag includes every author regardless of current guild membership; default excludes those who left

### t7 — Explain-catalog entry for ('discord','members') (jlab/explain/catalog.py)

- instruction: Tiny but CI-gating: without the ('discord','members') catalog entry, `test_every_catalog_path_resolves` and 'teken cli doctor . --strict' both fail. Land it in the same commit as the verb.
- covers: c26, h35
- acceptance:
  - `test_every_catalog_path_resolves` passes with the new path registered
  - 'uv run teken cli doctor . --strict' passes and the discord overview lists the new verb

### t8 — Containment: repo-anchored output path + .gitignore rule (new jlab/members/paths.py, .gitignore)

- instruction: Resolve the output path by walking up from `__file__` to the repo root — copy the pattern jlab/whoami.py uses to locate culture.yaml. Never use CWD. The .gitignore rule must land in the SAME commit, before any report can be generated.
- covers: c9, c21, h5, h18, h30
- acceptance:
  - Output path resolves from the repo root by walking up from `__file__` (as whoami.py locates culture.yaml), never from CWD
  - The ignore rule lands in .gitignore in the SAME commit, before any report can be generated; asserted with git check-ignore
  - Running from an unrelated directory refuses to write rather than writing person-level data outside the ignored path

### t9 — Install the \[discord\] extra into jlab's own venv so doctor passes (pyproject.toml / uv sync)

- instruction: Environment task, no product code. Core install must stay dependencies = \[\]; the \[discord\] extra stays optional and lazy-imported. Success is 'uv run jlab discord doctor' exiting 0 from this repo.
- covers: h10
- acceptance:
  - 'uv run jlab discord doctor' exits 0 from this repo, not the exit 2 it returns today
  - Core install stays deps=\[\]; the extra remains optional and lazy-imported

### t10 — Document the members verb for maintainers (README.md, CLAUDE.md)

- instruction: Docs only. Correct the two known CLAUDE.md drifts while here: data/channels.json does not exist in the repo, and scan.sh is now a 5-line stub pointing at the jlab CLI (not the --par 8 shell implementation described). State plainly that the CLI organizes statistics and issues no verdict.
- covers: c2, c3, c5
- acceptance:
  - README documents what the report shows and states plainly that the CLI organizes statistics and issues no verdict
  - CLAUDE.md's Domain section covers the members path; the stale data/channels.json and scan.sh descriptions are corrected

### t11 — Invariant guard tests: read-only and public-only (new tests/`test_members_invariants.py`)

- instruction: Guard tests only, no product code. These encode the two load-bearing invariants from CLAUDE.md — read-only and public-only — so a future change that breaks either fails CI rather than shipping.
- depends on: t6
- covers: c8, h17
- acceptance:
  - A test greps the members code paths and asserts no send/edit/delete/`add_reaction`/`create_thread` call exists
  - A test asserts a private (non-@everyone-viewable) channel contributes nothing to any member's statistics

### t12 — Measure the gates and hand one real run to maintainers (CI + validation)

- instruction: Final wave, measurement not implementation. Record the ACTUAL coverage number and the ACTUAL wall-clock of a full-window run — do not assume either. Do not set a measurable success target until a maintainer has seen a real report (h22).
- depends on: t5, t6, t7, t8, t9, t10, t11
- covers: c27, h36, h14, h22
- acceptance:
  - 'uv run pytest -n auto --cov=jlab --cov-report=term' reports coverage at or above 60 — the measured number is recorded, not assumed
  - A real full-window run over the guild's public text channels completes; its wall-clock time is measured and recorded
  - One generated report is handed to a Channel Maintainer, and their feedback on whether it surfaced anyone new is recorded before any measurable success target is set
