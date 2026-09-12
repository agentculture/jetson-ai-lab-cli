# Build Plan — jlab discord paged read + regex search

slug: `jlab-discord-paged-read-regex-search` · status: `exported` · from frame: `jlab-discord-paged-read-regex-search`

> jlab discord reads a channel's full history past the 100-message cap and filters it with a regex

## Tasks

### t1 — Approved-dependency allowlist: add pymongo, rewrite CLAUDE.md's dependency convention as an approved list, and enforce it with a test

- covers: c24, h22
- acceptance:
  - pyproject.toml lists pymongo; CLAUDE.md's Conventions bullet reads as an approved-dependencies list naming discord-bot-cli and pymongo, keeping the 'deliberate decision to discuss' rule
  - a test reads project.dependencies and optional-dependencies and FAILS when any distribution is absent from the allowlist (obligation o1) — verified by a test that adds a fake dep and asserts the failure
  - changes reviewed by: pi --provider nemotron --model associate — the review prompt must include this task's obligations verbatim as the review instructions (o1)

### t2 — jlab-mongodb deployment: dedicated instance, URI from config, doctor reachability and instance-identity check

- covers: c25, h20, c29, h21, c34, h33
- acceptance:
  - connection URI is read from an env var with no baked-in host/port default; no code path, default, fallback or test fixture resolves to 27017 or 27018, asserted by a test (o4)
  - an absent or unreachable cache exits code 2 with an actionable hint, never a traceback and never a silent empty result (o10)
  - the documented setup records the bind address and the internal-network assumption it rests on (o14)
  - changes reviewed by: pi --provider nemotron --model associate — the review prompt must include this task's obligations verbatim as the review instructions (o4, o10, o14)

### t3 — Backward cursor: thread before= through `_history`/`_drain`/`_collect_history` alongside after=

- covers: c3, h2, c10, h4
- acceptance:
  - a fake channel deeper than one 100-message page pages backwards to completion, proving the loop advances rather than re-fetching the same page
  - the overshoot-and-drop cap and the rate-limit resume behaviour are preserved unchanged for the existing after= direction
  - a full drain completes inside one action coroutine per invocation
  - changes reviewed by: pi --provider nemotron --model associate — the review prompt must include this task's obligations verbatim as the review instructions

### t4 — Lift the 100-message bound in `read_messages` and extend rate-limit handling to the backward drain

- depends on: t3
- covers: c2, h38, c15, h7
- acceptance:
  - the 1..100 validation at `_discord.py`:422 and the single unwindowed channel.history call at :431 are replaced by a call into the paging primitive
  - a simulated 429 mid-drain is retried with clamped backoff and resumed from its cursor; the result reports whether the window was fully read rather than silently truncating (o5)
  - changes reviewed by: pi --provider nemotron --model associate — the review prompt must include this task's obligations verbatim as the review instructions (o5)

### t5 — Mongo cache layer: message documents with created/updated/stored timestamps, application-level encrypt-on-store and decrypt-on-fetch

- depends on: t2
- covers: c41, h35, c11, h5
- acceptance:
  - message content is encrypted on store and decrypted on fetch at the application layer, so encryption does not depend on the MongoDB edition's storage engine; doctor reports it as measured, not assumed (o16)
  - each cached message carries created, updated and stored timestamps
  - full message bodies are retained by decision and CLAUDE.md states this beside the members no-content rule and the links URL-only rule (o8)
  - changes reviewed by: pi --provider nemotron --model associate — the review prompt must include this task's obligations verbatim as the review instructions (o8, o16)

### t6 — Interval-based coverage: merged intervals per channel, gap arithmetic, and write-before-widen ordering

- depends on: t5
- covers: c22, h9, h14, c30, h41, c36, h30
- acceptance:
  - coverage is a merged list of intervals per channel, not a single oldest/newest pair; a test fetches two disjoint spans and asserts the gap between them is named, not swallowed (o18)
  - a repeat fetch over a covered window issues strictly fewer requests than the first run, asserted by request count (o6)
  - coverage widens only after the messages in that span are durably written, and concurrent fetches against one channel are serialised via an advisory lock held outside the guarded directory (o12)
  - changes reviewed by: pi --provider nemotron --model associate — the review prompt must include this task's obligations verbatim as the review instructions (o6, o12, o18)

### t7 — discord fetch verb: backward paged fetch of a public channel into the cache, public check re-applied per caller-supplied id

- depends on: t4, t6
- covers: c21, h8, c8, h16, c45, h39
- acceptance:
  - a non-public channel id is refused with a code-1 error before any history request is issued; its name and contents never reach output or cache (o3)
  - `_channel_public` is the single source of the public test, applied at fetch time and on every sweep, so the definition cannot drift between paths
  - a second query against an already-fetched window returns results with the Discord seam monkeypatched to raise on any call (o7)
  - changes reviewed by: pi --provider nemotron --model associate — the review prompt must include this task's obligations verbatim as the review instructions (o3, o7)

### t8 — discord search verb: regex over the cached corpus with a declared execution bound

- depends on: t7
- covers: c5, h3, c37, h31
- acceptance:
  - a malformed --grep pattern is compiled up front and exits 1 with error:/hint: before any Discord request; no traceback
  - a pathological backtracking pattern terminates within a declared bound and reports being bounded, never hanging and never returning an empty result that reads as no matches (o13)
  - a search over a partially covered window reports the uncovered spans rather than answering as if complete (o18)
  - changes reviewed by: pi --provider nemotron --model associate — the review prompt must include this task's obligations verbatim as the review instructions (o13, o18)

### t9 — discord read served from cache with --refresh as the only live path

- depends on: t7
- covers: c33, h44
- acceptance:
  - read on a covered window returns the same messages as today; on an uncovered window it reports the gap and points at --refresh rather than returning empty (o21)
  - --refresh is the only path by which read contacts Discord, so a caller can tell from the command alone whether the network was touched (o22)
  - changes reviewed by: pi --provider nemotron --model associate — the review prompt must include this task's obligations verbatim as the review instructions (o21, o22; note o11 is superseded by o21 and is not dischargeable as written)

### t10 — Coverage inspection: make what the cache holds answerable from the CLI

- depends on: t6
- covers: c38, h32
- acceptance:
  - an operator can ask what windows the cache holds for a channel without opening Mongo by hand, in text and --json
  - changes reviewed by: pi --provider nemotron --model associate — the review prompt must include this task's obligations verbatim as the review instructions

### t11 — Daily reconciliation sweep: apply edits, remove deletions, purge channels that became private

- depends on: t7
- covers: c46, h40, c31, h42, c48, h37
- acceptance:
  - a full cycle is demonstrable: a message edited on Discord is updated in the cache, a deleted one removed, and a channel made private has its content purged — each asserted by a test
  - the cache converges to Discord's current state using only the sweep, with no gateway connection anywhere in the test (o19)
  - a rate-limit encounter delays the sweep rather than narrowing what it reconciles; an incomplete sweep reports itself as incomplete (o20)
  - the sweep is a single idempotent pass driven by cron or a systemd timer; no scheduler is built into jlab
  - changes reviewed by: pi --provider nemotron --model associate — the review prompt must include this task's obligations verbatim as the review instructions (o19, o20)

### t12 — Per-user deletion: purge one author's content from cache and derived reports

- depends on: t5
- covers: c32, h43, c40, h34
- acceptance:
  - a purge by author id removes that author's messages from the cache AND from derived reports, verified by fetch, purge, then search finding nothing for them (o15)
  - deletion is a real runnable CLI verb taking an author id or channel id, not a documented manual Mongo query
  - retention is explicit: content is bounded by what the stated functionality needs rather than kept indefinitely
  - changes reviewed by: pi --provider nemotron --model associate — the review prompt must include this task's obligations verbatim as the review instructions (o15)

### t13 — Privacy policy alignment: make the published commitments match what the code can actually do

- depends on: t12
- covers: c42, h36, c43, h45
- acceptance:
  - every deletion, retention and encryption commitment the published policy makes is one the shipped code can perform; anything it cannot is corrected in the policy rather than left as an unbacked promise (o17)
  - the policy describes collection, storage, retention, sharing and deletion for THIS cache specifically, and the Developer Portal link resolves to it
  - changes reviewed by: pi --provider nemotron --model associate — the review prompt must include this task's obligations verbatim as the review instructions (o17)

### t14 — CLI contract: explain catalog entries and rubric compliance for the new verbs

- depends on: t9, t11, t12
- covers: c13, h6, c12, h18
- acceptance:
  - every new verb path resolves through explain, and the parser-walking test at tests/`test_cli.py`:127-164 passes without being amended to excuse it
  - results on stdout, diagnostics on stderr, --json everywhere, errors as error:/hint: or {code,message,remediation} with no traceback; teken cli doctor . --strict passes unchanged in CI (o9)
  - changes reviewed by: pi --provider nemotron --model associate — the review prompt must include this task's obligations verbatim as the review instructions (o9)

### t15 — Invariant guards: read-only surface and no edits to the sibling repo

- depends on: t7
- covers: c9, h17, c7, h46
- acceptance:
  - a test asserts the adapter surface contains no send/post/react/edit/delete/`create_thread` call, so read-only stays literally true as the seam grows (o2)
  - the change set touches no file in the sibling discord-bot-cli repository; anything missing upstream is worked around locally and marked, as the existing WORKAROUND(discord-bot-cli#14) serializer is
  - changes reviewed by: pi --provider nemotron --model associate — the review prompt must include this task's obligations verbatim as the review instructions (o2)

### t16 — Documentation: README, CLAUDE.md agent description and the end-to-end motivating case

- depends on: t14
- covers: c1, h12, c16, h23, c17, h24, c18, h25, c19, h26, c20, h27
- acceptance:
  - the motivating case runs end to end and is recorded: a channel whose recent 100 messages stop short of September returns September matches after a fetch
  - CLAUDE.md's description of the agent is updated so the repo stops describing a read capability it has outgrown, without claiming the indexing and answering it still does not do
  - output is usable by both audiences without a second tool: a maintainer reads the report, the agent consumes the same run via --json
  - changes reviewed by: pi --provider nemotron --model associate — the review prompt must include this task's obligations verbatim as the review instructions

## Risks

- [unknown_nonblocking] file-disjointness violation in the verb layer: t7, t8, t9, t10, t11 and t12 each register a verb in jlab/cli/`_commands`/discord.py, so waves 4 and 5 are formally parallel but serialised at merge — the dependency graph sequences content, not file writes, and the CLI does not check this. Either give the verb tasks explicit deps to serialise them, or have the merging agent land them one at a time in wave order
- [follow_up] t3 and t4 both rewrite jlab/cli/`_discord.py`'s paging internals; t4 now depends on t3 to force sequential merge rather than leaving them same-wave (task t4)
- [unknown_nonblocking] the associate-model review criterion on every task assumes pi --provider nemotron --model associate stays reachable at its configured endpoint; the endpoint answered 401 (reachable, authenticating) at plan time but is a local network dependency outside this repo's control
- [unknown_nonblocking] c35 (forums and threads excluded from the corpus) remains an unconfirmed assumption on the frame and is therefore not a coverage target — no task covers it, so the coverage-reporting mitigation h29 is carried by t8's gap-reporting criterion rather than by a dedicated task
