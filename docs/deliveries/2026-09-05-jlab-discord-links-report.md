# Delivery Summary — jlab discord links report

plan: `jlab-discord-links-report` · run: `complete` · date: `2026-09-05`
baseline: `devague summary skeleton`

## Intent

Ship `jlab discord links`: a read-only, public-only sweep of the Jetson AI Lab
Discord that extracts every URL shared over a 90-day window and writes one
run's HTML report plus a flat CSV and a derived per-URL summary CSV into a
gitignored per-run subdirectory. The plan was seeded from a converged frame
(40 claims, 36 honesty conditions, 21 scope entries), pressure-tested by a
rigorous `/challenge` pass, and fanned out as 14 tasks over 6 dependency
waves. Two tasks were added or replaced mid-run: `t15` superseded `t7` after
an atomicity conflict, and `t16` was added to close deviation `d3`.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — Probe: verify whether Discord attachment CDN URLs are signed and expiring
- `t2` — Extend `_serialize_message` with attachment URLs, embed URLs, jump link and channel identity
- `t3` — Contained, gitignored output paths for links artifacts, and the ignore rules for both verbs' CSVs
- `t4` — Shared CSV writer with formula-injection defence and a flat, atomic-cell schema
- `t5` — All-or-nothing writer for a multi-file artifact set
- `t6` — URL extraction stage: jlab/links/extract.py
- `t7` — Members report emits CSV tables through the shared writer
- `t8` — Links report renderer: HTML plus flat and summary CSVs
- `t9` — Cached extraction so the report regenerates without a second scan
- `t10` — Wire the links verb into the discord noun group
- `t11` — Explain catalog entry for the links verb
- `t12` — Extend the read-only and public-only invariant guards to the links modules
- `t13` — Document the links verb and the content-retention inversion
- `t14` — Version bump and changelog entry
- `t15` — Per-run subdirectory layout for report artifacts, wired through the atomic writer (supersedes t7)
- `t16` — Cache the scan's coverage fields so a cache-rendered report states its real coverage (closes d3)

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | Read-only probe against the live guild: 100 public text channels, 146 messages. Measured attachment URLs signed with `ex`/`is`/`hm`, expiring 14–22h **from fetch** (a 143-day-old message's URL expired in 14.7h). Found a second fact nobody had raised — auto-preview embeds duplicate a URL already in content, rich embeds carry `url=None`. No repo files; result recorded on `c36` and `c41`. |
| `t2` | delivered | `_serialize_message` carries `attachments[].url`, `embeds[].url` + description/field bodies, `jump_url`, channel and thread identity. Keys added only — none renamed or removed. Merged `b676053`. |
| `t3` | delivered | `jlab/links/paths.py` + both `.gitignore` rules (links dir and members CSVs) in one commit. Merged `24871de`. |
| `t4` | delivered | `jlab/csv_export.py` — prefix-escapes `= + - @ TAB CR`, raises `TypeError` on non-scalar cells. Merged `632e5af`. |
| `t5` | delivered | `jlab/atomic_writeset.py` — temp-dir + rename, with the platform caveat documented rather than overclaimed. Merged `42f6118`. |
| `t6` | delivered | `jlab/links/extract.py` — four deduped sources, dedupe scoped within a message so one row per share survives. Merged `9884438`. |
| `t7` | **dropped** | Superseded by `t15`. Its CSV column design was reused; its two-sequential-writes mechanism was rejected — see `d1`. Branch never merged. |
| `t8` | delivered | `jlab/links/report.py` — HTML + flat CSV + derived summary CSV, scheme-filtered hrefs, per-row coverage columns, ephemeral attachment marking. Merged `d92aad4`. |
| `t9` | delivered | `jlab/links/cache.py` — `{scanned_at, records}`, ids only, `attachments_expired()`. Merged `685992a`. Coverage gap closed later by `t16`. |
| `t10` | delivered | `cmd_discord_links` + flags; `--json` hard stop; departed authors keep their links. Merged `b0dd394`. |
| `t11` | delivered | `("discord","links")` catalog entry, `_DISCORD` verb list, **and** the converse test closing issue #15. Merged `b88ec49`. |
| `t12` | delivered | Links modules added to `_GUARDED_FILES`; private-channel leak test asserting the literal private URL is absent. Guard sabotage-verified. Merged `216f9b5`. |
| `t13` | delivered | README + CLAUDE.md sections; the content-retention inversion stated in both; `r8` corrections applied. Merged `e2f53e3`; cache-location error corrected in `e543718`. |
| `t14` | delivered | 0.7.0 + Keep-a-Changelog entry. Merged `d56e49d`. |
| `t15` | delivered | Per-run subdirectory layout for both verbs; members artifacts via `write_artifact_set`; run ids sanitised like filenames. Merged `37f40b8`. |
| `t16` | delivered | Cache carries a trimmed copy of the six coverage fields; old payloads still load. Merged `9b244ba`. |

16 of 16 plan tasks accounted for: 15 delivered, 1 dropped (superseded).

## Mid-work Decisions

- `d1` — Report artifacts moved to one subdirectory **per run**. `t5`'s `write_artifact_set` replaces an entire destination directory, which is correct only if that directory belongs to one run; against a shared flat directory it would delete siblings and race under `pytest -n auto`. `t7` diagnosed this correctly and then fell back to two unprotected sequential writes — the exact hazard `h31` forbids. The per-run layout resolves it **in favour of** the guarantee. Filed as [#14](https://github.com/agentculture/jetson-ai-lab-cli/issues/14).
- `d2` — The extraction cache lives in a **sibling** `<run-id>-cache` directory, following directly from `d1`: one run directory holds exactly one artifact set. Filed as [#16](https://github.com/agentculture/jetson-ai-lab-cli/issues/16).
- `d3` — A cache-rendered report initially carried **no** coverage figures, because `write_cache` stored only `{scanned_at, records}`. `t10` rendered `unknown` rather than inventing numbers. Closed by `t16`.
- **Not covered by any deviation record:** the `t1` probe result reshaped the spec *before* wave 1's remaining tasks fanned out. Extraction widened from three sources to four (`c16`, `c41`), and attachment URLs became ephemeral-marked rather than presented as stable (`c42`). Both were adjudicated as spec amendments, not deviations, because they landed before the affected code was written.
- **Not covered by any deviation record:** `t13`'s documentation described the cache as living in the run directory. It was briefed before `d2` existed and had no way to know. Corrected in `e543718` — a doc task briefed early describes the plan, not the build.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t7` (`d1`) | `write_artifact_set` replaces its entire destination directory, which conflicts with a shared flat report directory; the fallback of two unprotected sequential writes reintroduced exactly the hazard `h31` forbids | needs-follow-up (closed by `t15`) |
| `t10` (`d2`) | The report set and the cache cannot share one run directory; neither task owned both files needed for a tidier fix | needs-follow-up (documented; layout stands) |
| `t9` (`d3`) | The cache payload omitted the scan's coverage fields, so a cached re-render could not state its own coverage | needs-follow-up (closed by `t16`) |
| `t2`, `t6` | Extraction widened from three sources to four after the `t1` probe measured that `embeds[].url` alone adds duplicates while missing rich-embed links | acceptable |
| `t8` | Attachment URLs are never rendered as clickable anchors **at all**, stronger than the specced "marked as expiring" | acceptable |

## Evidence

- tests: full suite `334 passed, 1 skipped` at `08afe53`
- tests: `tests/test_links_cli.py::test_discord_links_json_is_id_only` — pass (5 flag combinations, raising fakes)
- tests: `tests/test_links_cli.py::test_departed_author_keeps_their_link` — pass
- tests: `tests/test_links_report.py::test_only_http_and_https_urls_become_anchors` — pass
- tests: `tests/test_links_report.py::test_a_killed_write_leaves_no_partial_set` — pass
- tests: `tests/test_links_cli.py::test_links_from_cache_render_matches_original_coverage_columns` — pass
- tests: `tests/test_members_invariants.py::test_private_channel_contributes_nothing_to_links_extraction` — pass
- tests: `tests/test_members_invariants.py::test_no_discord_write_calls_anywhere_in_members_code_paths` — pass, **and verified to fail when sabotaged**
- tests: `tests/test_csv_export.py::test_write_csv_round_trips_through_pandas_read_csv` — **skipped, never executed**
- coverage: 95.99% (gate 60)
- lint: `black --check`, `isort --check-only`, `flake8`, `bandit -r jlab` — all clean; bandit 0 medium, 0 high
- lint: `markdownlint-cli2` — 0 errors
- rubric: `teken cli doctor . --strict` — 26 PASS
- commits: `33d444b..08afe53`
- issues: [#14](https://github.com/agentculture/jetson-ai-lab-cli/issues/14), [#15](https://github.com/agentculture/jetson-ai-lab-cli/issues/15) (closed), [#16](https://github.com/agentculture/jetson-ai-lab-cli/issues/16)
- devague: 14 approved evidence records (`e1`–`e14`), 5 behavioral deltas (`b1`–`b5`), 3 approved deviations, 1 approved lapse

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| `jlab discord links` ships and writes HTML + both CSVs in one invocation | high | evidence `e1` · test `tests/test_links_cli.py::test_discord_links_one_invocation_writes_html_and_csvs` · commit `b0dd394` |
| `--json` is id-only under every flag combination, with no opt-in | high | evidence `e2` · test `tests/test_links_cli.py::test_discord_links_json_is_id_only` |
| A departed author's link is kept with its bare id and no name | high | evidence `e3` · test `tests/test_links_cli.py::test_departed_author_keeps_their_link` |
| Hostile URL schemes are refused as hrefs, not merely escaped | high | evidence `e4` · test `tests/test_links_report.py::test_only_http_and_https_urls_become_anchors` |
| A killed write never leaves a mismatched artifact pair | high | evidence `e5` · test `tests/test_links_report.py::test_a_killed_write_leaves_no_partial_set` |
| Attachment URLs are marked expiring and never clickable | high | evidence `e6` · test `tests/test_links_report.py::test_attachment_url_is_never_rendered_as_a_clickable_link` |
| A cached re-render states the same coverage without a Discord scan | high | evidence `e7` · test `tests/test_links_cli.py::test_links_from_cache_render_matches_original_coverage_columns` |
| A private channel is never fetched and leaks nothing | high | evidence `e8` · test `tests/test_members_invariants.py::test_private_channel_contributes_nothing_to_links_extraction` |
| No write path exists in any links or members module | high | evidence `e9` · guard sabotage-verified (a `.send(` was added and the test failed) |
| A CSV read alone states its window and coverage gaps | high | evidence `e10` · test `tests/test_links_report.py::test_a_reader_with_only_a_csv_can_see_the_window_and_the_gaps` |
| The report issues no verdict | high | evidence `e11` · test `tests/test_links_report.py::test_no_verdict_language_anywhere_in_the_rendered_output` |
| URLs come from four deduped sources | high | evidence `e12` · `tests/test_links_extract.py` (41 tests) |
| CSV fields are escaped so they open as inert text, not formulas | **medium** | evidence `e13` at *fidelity* strength — asserted on emitted bytes and via `csv.reader`; `h29` asked for verification by **opening** a generated CSV in a spreadsheet, which was never done |
| The flat CSV loads in pandas and Google Sheets with no preprocessing | **unverified** | evidence `e14` — the pandas test exists but is **skipped** (pandas is not a dev dependency); Google Sheets was never tried |
| A full 90-day run completes within an agreed time budget | **unverified** | lapse `l1` (approved) — the "under 5 minutes" figure was invented, never measured; `c30` now says so in its own text |

## Remaining Work / Follow-up

- **`r9` / `e14` — verify spreadsheet loadability for real.** The stated purpose of these CSVs is Google Drive and Python, and that is the least-verified criterion in the delivery. Next step: add pandas to the dev dependency group so the skipped test runs, and open one generated CSV by hand once. Nothing can automate the Sheets half.
- **`l1` / `c30` — time a real full-window run** and replace the removed performance figure with a measured one. Until then the success signal honestly states no budget is set.
- **`r5` — stale docstring premise.** `jlab/members/aggregate.py` asserts the serialized shape "carries only `{id, author, content, created_at}`", which `t2` made false. The `question_starts` heuristic name still stands; the docstring's reasoning does not.
- **`r6` — the isolation guard is prose-sensitive.** It greps literal substrings across `jlab/`, so a docstring mentioning `author.bot` trips it. The guard's own comment says the honest fix is an explicit exemption, never rewording. Two agents have now paid this toll.
- **`v1` — shared-token rate contention.** `DISCORD_BOT_TOKEN` is one credential shared with `discord-bot-cli` and other mesh agents. `--from-cache` reduces repeat cost but nothing bounds the first sweep or coordinates with other agents.
- **`v2` — link liveness is unexamined.** Nothing checks whether a shared URL still resolves; doing so would turn a read-only scan into an outbound crawler, which is a different tool with its own consent questions.
- **`d2` stands as designed** — the sibling cache directory follows from `d1` and is documented. Revisit only if the artifact-set primitive changes.
- **`t7`'s branch was deleted**, not merged. Its CSV column design survives in `t15`; nothing else is recoverable from it.
