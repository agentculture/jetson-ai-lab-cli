# Delivery Summary — jlab discord paged read + regex search

plan: `jlab-discord-paged-read-regex-search` · run: `complete` · date: `2026-09-12`
baseline: `devague summary skeleton`

## Intent

> jlab discord reads a channel's full history past the 100-message cap and filters it with a regex

The run executed the confirmed 16-task plan (`docs/plans/2026-09-12-jlab-discord-paged-read-regex-search.md`) through seven dependency waves on branch `plan/discord-paged-read-regex-search`. The goal: fetch a public channel's history backwards past the 100-message ceiling into a dedicated, encrypted jlab-mongodb cache; fetch only what is missing on a repeat run; serve `read` and regex `search` from that cache; and meet Discord's deletion, encryption-at-rest and privacy-policy obligations with per-user and per-channel purge plus a daily reconciliation sweep.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — Approved-dependency allowlist: add pymongo, rewrite CLAUDE.md's dependency convention as an approved list, and enforce it with a test
- `t2` — jlab-mongodb deployment: dedicated instance, URI from config, doctor reachability and instance-identity check
- `t3` — Backward cursor: thread before= through `_history`/`_drain`/`_collect_history` alongside after=
- `t4` — Lift the 100-message bound in `read_messages` and extend rate-limit handling to the backward drain
- `t5` — Mongo cache layer: message documents with created/updated/stored timestamps, application-level encrypt-on-store and decrypt-on-fetch
- `t6` — Interval-based coverage: merged intervals per channel, gap arithmetic, and write-before-widen ordering
- `t7` — discord fetch verb: backward paged fetch of a public channel into the cache, public check re-applied per caller-supplied id
- `t8` — discord search verb: regex over the cached corpus with a declared execution bound
- `t9` — discord read served from cache with --refresh as the only live path
- `t10` — Coverage inspection: make what the cache holds answerable from the CLI
- `t11` — Daily reconciliation sweep: apply edits, remove deletions, purge channels that became private
- `t12` — Per-user deletion: purge one author's content from cache and derived reports
- `t13` — Privacy policy alignment: make the published commitments match what the code can actually do
- `t14` — CLI contract: explain catalog entries and rubric compliance for the new verbs
- `t15` — Invariant guards: read-only surface and no edits to the sibling repo
- `t16` — Documentation: README, CLAUDE.md agent description and the end-to-end motivating case

## Actual Delivery

All 16 tasks are accounted for: 15 delivered on the plan branch; `t13` is partial.

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | Allowlist test `tests/test_dependencies.py`; approved set is discord-bot-cli, pymongo and cryptography (d1); merge `afab3de` |
| `t2` | delivered | `jlab/mongo.py`: `JLAB_MONGO_URI`, guard refusing ports 27017/27018, doctor reachability; README deployment and bind-address posture; merge `70a3d5a`. The `jlab-mongodb` container was created on this host on 2026-09-13 |
| `t3` | delivered | `before=` cursor and direction-correct 429 resume in `jlab/cli/_discord.py`; merge `183d6eb` |
| `t4` | delivered | `read_messages` uncapped, reports `complete`/`reason`; merge `e0259f0`; docstring overclaim fixed in `80cdf8b` |
| `t5` | delivered | `jlab/cache.py` and `jlab/crypto.py`: AES-256-GCM application-layer encryption (d1), three timestamps, measured-encryption probe; merge `ffe7d69` |
| `t6` | delivered | `jlab/coverage.py`: merged intervals, gap arithmetic, write-before-widen, per-channel `flock`; merge `f4263f6`. Later fix `8369887` stores and compares coverage at BSON millisecond precision |
| `t7` | delivered | `jlab/fetch.py` plus `discord fetch`; merge `1e3cbd3`. Gate fix `2404cf4` adds a cross-guild refusal and a key/URI preflight; live fix `c4a4ecb` resolves permissions against the fetched guild |
| `t8` | delivered | `jlab/search.py` plus `discord search`: pattern compiled first, match in a child process under a wall-clock `--timeout`, gaps reported; merge `c9e7f1f` |
| `t9` | delivered | `jlab/read.py`: cache-served `read`; `--refresh` does a live re-read reconciled through `jlab/reconcile.py` (shared with sweep); encrypted author names (d4); merge `508aaf6` |
| `t10` | delivered | `discord coverage`; gate fix `5a84a79` (no-window JSON never claims complete; lone bound rejected); merge `fc7f6ce` |
| `t11` | delivered | `jlab/sweep.py` plus `discord sweep`: live visibility re-check, edits applied, deletions only in completely re-read spans, private/gone channels purged, probe reap; merge `7adb29b` |
| `t12` | delivered | `jlab/purge.py` plus `discord purge`: author/channel/older-than, dry-run by default, derived reports removed, keyed-hash suppression list (d3); merge `f5c1c87`; `cb2196c` withholds the author id from output (risk r15) |
| `t13` | partial | Policy disclosure written and proposed in OriNachum/jetson-bot#2 (fork `agentculture/jetson-bot`, branch `docs/jlab-message-cache-disclosure`). Not merged; the Developer Portal link is not updated or verifiable from here |
| `t14` | delivered | `tests/test_cli_contract.py` derives every verb list from the parser, docstring, `overview --json` and catalog; docstring and catalog verb lists completed; merge `238f665` |
| `t15` | delivered | `tests/test_invariants.py`: read-only surface, no writes into the sibling checkout, and `WORKAROUND` marker format; each detector is proven against bad snippets; merge `90269a1` |
| `t16` | delivered | README and CLAUDE.md describe the new verbs; `docs/motivating-case.md` records the live case; merge `c6baecc`; docs `5ce7e1d` |

## Mid-work Decisions

- `d1` — replace t5's hand-rolled stdlib cipher with AES-256-GCM and add `cryptography` to the approved runtime dependencies — that encryption backs the published encryption-at-rest commitment, so the user chose a reviewed AEAD over zero new dependencies (GitHub #20)
- `d2` — t2, t3, t4 and t5 were merged before their required associate-model review completed, and no wave-level review ran for waves 1–2 — task agents returned while reviews were in flight, and the main agent merged on its own diff reading and TDD gate (GitHub #21)
- `d3` — add a purge suppression list: `purge --author` records an HMAC of the author id keyed with `JLAB_CACHE_KEY`, and fetch plus sweep skip matching authors — without it, the next fetch or sweep would re-cache a deleted author (GitHub #22)
- `d4` — the cache also stores each author's name and display name, encrypted like the body — the spec requires cache-served `read` to match today's output, and resolving names at read time would contact Discord without `--refresh` (GitHub #23)
- `d5` — tasks t6–t15 and waves 3–5 merged with the associate-review criterion unmet or only partly met — reviews that exhausted a 16,000-token thinking budget were skipped under the user's rule, and the qwen worker reviewer was paused at the user's request (GitHub #24)
- `t14` started from `cb2196c` in parallel with `t9` (the plan has t14 depend on t9) — t9 added a flag, not a verb, so t14's parser-walking tests did not need it; t14 was rebased onto t9's merge before its own merge
- `t13`'s PR was opened against `OriNachum/jetson-bot` before the planned move to the jetson-ai-lab organisation — GitHub carries open PRs with a transferred repository; the user directed the fork into agentculture
- The main agent rewrote the task agents' tests in `t14` and `t15` — as delivered, several could not fail (hard-coded verb lists, detectors that never matched)
- The motivating case used a late-August window instead of September — measured live, no public channel's most recent 100 messages stop short of September 1, and the brief allowed moving the date
- Three live-only defects were fixed after the run's first real use (lapses l2–l4): the roleless stub guild (`c4a4ecb`, upstream agentculture/discord-bot-cli#20), naive BSON datetimes (`3ca416d`) and BSON millisecond precision (`8369887`)
- o11 could not be rejected once o21 superseded it, because it was already approved — it is filed as failing evidence (e23) with delta b4, not silently dropped

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|------------------------|-----------------|
| `t5` (`d1`) | t5 avoided a crypto dependency and documented its construction as 'not a standardised, independently reviewed AEAD' with 'no cryptographic review'; that encryption backs the encryption-at-rest commitment in the published privacy policy and Discord's Developer Terms, so the user chose a reviewed AEAD over zero new dependencies | `acceptable` |
| `t4` (`d2`) | task agents returned control while their pi reviews were still in flight, and the main agent merged on its own diff inspection and TDD gate; the criterion 'changes reviewed by pi associate' was satisfied after merge or not at all | `needs-follow-up` |
| `t12` (`d3`) | purge deleted an author's content, but nothing stopped the next fetch or sweep from re-caching it; the user chose a keyed-hash suppression list so no readable id is retained for someone who asked to be deleted | `needs-follow-up` |
| `t9` (`d4`) | cache-served read must match today's output, which shows author names; the cache stored ids only, and resolving names at read time would contact Discord without --refresh (o22) | `needs-follow-up` |
| `t8` (`d5`) | associate reviews that exhausted their thinking budget were skipped under the user's rule, and the worker reviewer was paused; every merge was still gated by diff reading plus tests before and after (applies to t6–t15 and waves 3–5) | `needs-follow-up` |
| `t13` | the policy PR is open but unmerged, and the Developer Portal link cannot be verified from here, so o17 is unmet (evidence e22, fail) | `needs-follow-up` |
| `t9` | the o11 promise of byte-identical output for a caller passing no new flag no longer holds: `--json` gains `complete`/`uncovered`, and names now come from the cache (o21 supersedes it; evidence e23 fail; delta b4) | `acceptable` |
| `t7`, `t9`, `t11` | as merged, fetch, `read --refresh` and sweep failed against real Discord — every public channel read as private, and sweep would have purged everything — while all unit tests passed; fixed in `c4a4ecb` (lapse l2) | `risky` |
| `t5`, `t6` | as merged, cached timestamps broke against real jlab-mongodb (naive datetimes; millisecond truncation made every live fetch report incomplete); fixed in `3ca416d` and `8369887` (lapses l3, l4) | `risky` |
| `t16` | the motivating case ran on a late-August window rather than September, because no channel's live traffic supports a September case as of 2026-09-13; the mechanism demonstrated is unchanged | `acceptable` |
| `t14` | started before its dependency `t9` merged (see Mid-work Decisions); merged after it | `acceptable` |

## Evidence

- tests: full suite at `3567f3c` — 762 passed (`uv run pytest -q -n auto -p no:cacheprovider`)
- tests: per-obligation runs at `5ce7e1d`, filed as devague evidence `e1`–`e18` — all pass (node ids are in `.devague/deliveries/jlab-discord-paged-read-regex-search.json`)
- docs: `e19` o8 CLAUDE.md retention section — pass; `e20` o14 README bind-address paragraph — pass
- live: `e21` o16 — `jlab discord doctor --json` against jlab-mongodb at `cb2196c`, encrypted and measured — pass
- live: `e24` o18, `e25` o7 — `docs/motivating-case.md` steps 2–4 at `c6baecc` — pass
- live: `e26` o22, `e27` o3 — `read --refresh --limit 20` on real public channel `1327720920206282864` at `8369887` reported `complete: true`, then was purged — pass
- failing: `e22` o17 — OriNachum/jetson-bot#2 unmerged; `e23` o11 — superseded by o21
- deltas: `b1`–`b7`
- lint: `black --check`, `isort --check-only`, `flake8`, `bandit -c pyproject.toml -r jlab` — clean; `uv run teken cli doctor . --strict` — 26/26; `markdownlint-cli2` over all Markdown — 0 errors
- commits: `4991d73..3567f3c` (62 commits; task merges listed in Actual Delivery)
- issues and PRs: agentculture/jetson-ai-lab-cli #20–#24 (d1–d5); OriNachum/jetson-bot#2 (t13); agentculture/discord-bot-cli#19 (gateway events), #20 (stub guild)
- all evidence and deltas are `llm`-origin and **proposed**; the user has not yet adjudicated them

## Delivery Claims

Confidence is capped where only fakes back a claim, since lapses l2–l4 showed that fakes did not reproduce the live Discord and MongoDB shapes. l2–l4 are still proposed, so they are cited as context, not as approved evidence. Approved lapse l1 (Message Content Intent was asserted, not measured) is now measured: the live search returned 5 matches over real content.

| Claim | Confidence | Evidence |
|-------|------------|----------|
| `discord fetch` pages a real public channel backward past the 100-message cap into the encrypted cache, fetching only gaps | high | live `docs/motivating-case.md` step 3 (`stored: 21`) · tests `tests/test_coverage.py::test_repeat_fetch_of_a_covered_window_issues_strictly_fewer_requests` · commit `c4a4ecb` |
| a search over a window the cache does not cover reports it uncovered, never as zero matches; after fetch it reports complete with real matches | high | live `docs/motivating-case.md` steps 2 and 4 · test `tests/test_search.py::test_a_partially_covered_window_reports_the_uncovered_span` |
| `read` is served from the cache; `--refresh` is the only path that contacts Discord, and after it a covered window reports `complete: true` | high | live re-check at `8369887` (e26) · test `tests/test_read.py::test_default_read_never_touches_the_seam_even_when_uncovered` |
| a non-public channel, or a channel of another guild, is refused before any history request | medium | tests `tests/test_fetch.py::test_fetch_refuses_a_non_public_channel_before_any_history_call`, `tests/test_fetch.py::test_fetch_refuses_a_channel_from_another_guild_before_any_history_call` — fakes only; the live check exercised a public channel, not a private one |
| a pathological regex is killed at a declared bound and reported as bounded | high | test `tests/test_search.py::test_pathological_pattern_is_bounded_not_hung` |
| cached message bodies and author names are encrypted at rest with AES-256-GCM, and doctor measures it | high | live doctor e21 · test `tests/test_cache.py::test_stored_document_holds_no_plaintext` · file `jlab/crypto.py` |
| `purge --author` removes the author's cache content and derived reports, stops later re-caching, and never prints the id | medium | tests `tests/test_purge.py::test_fetch_then_purge_then_search_finds_nothing_for_that_author`, `tests/test_purge.py::test_fetch_purge_search_then_fetch_again_still_finds_nothing` · commit `cb2196c` — not exercised live |
| the daily sweep applies edits, removes deletions only in completely re-read spans, and purges channels that became private, without purging public ones | medium | tests `tests/test_sweep.py::test_o19_the_cache_converges_to_discord_using_only_the_sweep`, `tests/test_sweep.py::test_o20_an_incomplete_span_reports_itself_and_deletes_nothing` · stub-guild regression in `tests/test_sweep.py` — `discord sweep` has not been run live |
| coverage widens only after writes, and same-channel fetches across processes are serialised | medium | tests `tests/test_coverage.py::test_coverage_widens_only_after_the_messages_are_written`, `tests/test_coverage.py::test_lock_excludes_a_second_holder` — the lock is re-entrant per process, not per thread (risk r16) |
| jlab connects only to its own jlab-mongodb, and an absent or unreachable instance exits 2 | high | live doctor e21 on port 27019 · tests `tests/test_mongo.py::test_mongo_uri_rejects_legacy_ports_explicit`, `tests/test_mongo.py::test_check_cache_unreachable_exits_env_error_with_hint` |
| every discord verb meets the agent-first CLI contract | high | test `tests/test_cli_contract.py` · `teken cli doctor . --strict` 26/26 |
| the read-only surface has no Discord write call, and nothing writes into the sibling checkout | high | test `tests/test_invariants.py` |
| only approved runtime dependencies ship | high | test `tests/test_dependencies.py::test_dependencies_in_approved_allowlist` |
| the published privacy policy describes this cache and the Developer Portal links to it | unverified | OriNachum/jetson-bot#2 is open, not merged; Portal link not checked (e22 fail) — not claimed done |
| `read` output is byte-identical to before for a caller passing no new flag | unverified | superseded by o21 (e23 fail, delta b4) — not claimed |
| every task was reviewed by the associate model | unverified | d2, d5 — most reviews were skipped; not claimed |

## Remaining Work / Follow-up

- `t13` — merge OriNachum/jetson-bot#2 (or the same PR after the repository moves to the jetson-ai-lab organisation), then point the Discord Developer Portal privacy-policy link at it. Owner: user.
- Adjudicate the proposed records: evidence `e1`–`e27`, deltas `b1`–`b7` and lapses `l2`–`l4` (`devague evidence|delta|lapse --confirm/--reject`). Owner: user.
- Independent review of `t8` (search), `t9` (read), `t11` (sweep) and the whole diff, once a reviewer with enough output budget is available (d5, GitHub #24). Owner: user or a later session.
- Run `discord sweep` live once, on a small covered channel, before scheduling it from cron: it is the only purging path not yet exercised against real Discord.
- Upstream: agentculture/discord-bot-cli#20 (stub guild) — remove `WORKAROUND(discord-bot-cli#20)` in `jlab/fetch.py` and `jlab/sweep.py` once it lands; agentculture/discord-bot-cli#19 (gateway events) would lighten the sweep.
- Known limits carried forward:
  - a backward page of 100+ messages sharing one timestamp ends partial (r7/r8)
  - a message truly deleted but sharing its exact timestamp with a surviving one stays cached until a later sweep
  - `read` and search load a channel's whole cached history into memory
  - `channel_lock` is re-entrant per process, not per thread (r16)
  - an incomplete sweep exits 0, so cron alerting must read its output
- Operations: `jlab-mongodb` (mongo:8.0, port 27019, volume `jlab-mongodb-data`) now runs on this host, and its key lives in `~/.config/jlab/cache.env` — back up that key with the deployment. The cache was left empty.
