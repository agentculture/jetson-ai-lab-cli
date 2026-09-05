# Build Plan — jlab discord links report

slug: `jlab-discord-links-report` · status: `exported` · from frame: `jlab-discord-links-report`

> jlab discord links extracts every URL shared in the Jetson AI Lab Discord's public channels over the last 90 days and renders a local, gitignored HTML report of what the community is sharing

## Tasks

### t1 — Probe: verify whether Discord attachment CDN URLs are signed and expiring

- instruction: Read-only probe, no repo files. Run 'uv run jlab discord read' against a public channel carrying an attachment, inspect the attachment URL's query string for ex/is/hm-style expiry parameters, and record the finding on c36 via devague. Blocks t2 and t6 only in the sense that its answer shapes them — do not start the extractor before it lands.
- acceptance:
  - One real attachment URL is fetched from a live public message and its query string inspected for expiry parameters; the finding is recorded on c36 either way
  - If they expire, the decision (drop attachment URLs, or keep them marked as ephemeral with the jump link as the durable pointer) is recorded before any extractor code is written

### t2 — Extend `_serialize_message` with attachment URLs, embed URLs, jump link and channel identity

- instruction: Files: jlab/cli/`_discord.py`, tests/`test_discord.py`. Extend `_serialize_message` only; do not touch `_public_text_channels` or the paging logic. Add keys, never rename or remove one. Thread the enclosing channel's id and name through `_channel_row` so each message record can name its channel. Expect assertion churn in the members and channels/read/active tests and change each one deliberately.
- covers: c18, h2, c31, h8
- acceptance:
  - A serialized message carries attachments\[\].url, embeds\[\].url, a jump link, and the id and name of its enclosing channel
  - A message with a thread carries a thread reference; one without renders an empty reference, never a placeholder or an error
  - The whole existing tests/`test_discord.py` plus the members suite pass; every assertion that changed was changed deliberately and no existing verb's documented output changed meaning
  - The serializer gains only keys — no key is renamed or removed — so existing --json consumers of channels/read/active keep working
  - The serializer carries embed description and field bodies, not only embeds\[\].url, since rich embeds carry url=None and hide their links in the body (t1 probe finding).

### t3 — Contained, gitignored output paths for links artifacts, and the ignore rules for both verbs' CSVs

- instruction: Files: jlab/links/paths.py (new), .gitignore, tests/`test_links_paths.py` (new). Copy the pattern in jlab/members/paths.py verbatim — `_REPO_MARKER`, walking up from `__file__`, the traversal guard — changing only the subdir tuple. Add BOTH ignore rules in this task: the links directory and the members CSVs, so no later task has to edit .gitignore.
- covers: c5, h22, c9, h26
- acceptance:
  - The links report directory is resolved by walking up from `__file__` to the culture.yaml marker, never from the caller's CWD, and the verb refuses to write when no repo root is found
  - A filename containing path separators or .. cannot escape the report directory
  - git check-ignore succeeds on every path the links verb writes and on the members CSV paths, asserted by enumerating actual written filenames rather than a hardcoded directory

### t4 — Shared CSV writer with formula-injection defence and a flat, atomic-cell schema

- instruction: Files: the new shared CSV module and its tests. Neither jlab/members/report.py nor jlab/links/report.py may contain CSV code — they call this. Prefix-escape any field whose first character is = + - @ tab or CR. Keep it dependency-free: csv from the stdlib, nothing else.
- covers: c33, h29, c38, h33
- acceptance:
  - A field beginning with = + - @ tab or carriage return is prefix-escaped so it opens as inert text, verified by opening a generated CSV in a spreadsheet application, not only by asserting on bytes
  - A display name starting with '=' and a URL starting with '@' are both covered by that test
  - Every emitted cell is a scalar: no delimiter-joined list appears in any cell, asserted programmatically
  - A generated CSV loads with a stock pandas.`read_csv` and a stock Google Sheets import with no preprocessing
  - One implementation serves both the members and links report modules; neither has its own CSV code

### t5 — All-or-nothing writer for a multi-file artifact set

- instruction: Files: the new atomic write-set module and its tests. Write into a temp directory and rename into place once every artifact is complete, or give all files one run id. Test the failure path by injecting an exception between writes and asserting the destination is unchanged.
- covers: c35, h31
- acceptance:
  - Killing the write partway leaves either a complete artifact set or no new artifacts at all — never a complete HTML beside missing or stale CSVs
  - The failure path is tested by injecting an exception between file writes and asserting the destination directory is unchanged

### t6 — URL extraction stage: jlab/links/extract.py

- instruction: Files: jlab/links/extract.py (new), tests/`test_links_extract.py` (new). Consumes `scan_window`'s output dicts; never calls Discord itself. Retain the URL and its coordinates only — no surrounding message text. Honour t1's finding about attachment URLs.
- depends on: t2, t1
- covers: c16, h11, c17, h12, c19, h3, c21, h4, c2, h19, c41, h38
- acceptance:
  - URLs are extracted from three sources — message.content by regex, attachments\[\].url and embeds\[\].url — proven by a message whose only URL is an attachment and another whose only URL is in an embed each contributing exactly one link
  - No second windowed pager is introduced: history() paging logic still exists in exactly one place, jlab/cli/`_discord.py`
  - Each record carries url, channel, timestamp, thread reference and author id; no surrounding message text is retained anywhere in the output
  - Bots are excluded by default and included behind the opt-in flag; the two runs over one window differ only by bot-authored links, with no human-authored link appearing or disappearing
  - Two links shared in the same thread are groupable by their thread reference; a link outside any thread has an empty one
  - Extraction covers four deduped sources: content regex, attachments\[\].url, embeds\[\].url, and regex over embed description and field values. A URL appearing both in content and in its auto-preview embed yields exactly one record; a URL existing only inside a rich embed's body is extracted.

### t7 — Members report emits CSV tables through the shared writer

- instruction: Files: jlab/members/report.py, tests/`test_members_report.py`. Add CSV emission by calling t4's shared writer; change nothing about the existing HTML rendering or the members privacy contract.
- depends on: t4, t5
- covers: c25, h7
- acceptance:
  - The members verb writes CSV tables alongside its HTML, into its existing gitignored report directory
  - Members and links produce their CSVs through one shared writer — a single implementation, not two
  - The members CSVs are gitignored, landing in the same commit that adds them

### t8 — Links report renderer: HTML plus flat and summary CSVs

- instruction: Files: jlab/links/report.py (new), tests/`test_links_report.py` (new). Reuse the `_esc`, `_STYLE` and `_bar_chart` patterns from jlab/members/report.py by copying them, not by importing across packages. Sanitise URL schemes before they become hrefs. Derive the summary CSV from the flat table in code.
- depends on: t4, t5, t6
- covers: c11, h9, c34, h30, c22, h5, c10, h27, c42, h37
- acceptance:
  - A message containing a javascript: URL, a data: URL and a URL with embedded HTML renders as inert escaped text with no executable href
  - The report loads with no network fetch and no script tag, and pyproject.toml still reads dependencies = \[\]
  - The flat CSV is one row per share; the deduped per-URL summary CSV is derived from it rather than from a second extraction model
  - A reader given only a CSV, with no access to the HTML, can tell from the CSV alone which window it covers and whether any channel was partial or failed
  - Grepping the rendered HTML finds no verdict language: no top, best, most active, most shared or recommended
  - Attachment URLs render marked as expiring, with the jump link beside every one, in both the HTML and the CSV — never presented as stable links.

### t9 — Cached extraction so the report regenerates without a second scan

- instruction: Files: jlab/links/cache.py (new), tests/`test_links_cache.py` (new). The cache payload is exactly what --json emits — ids, no names. Store the scan timestamp in it and render that, not the render time. The cache lives under t3's contained path.
- depends on: t3, t6
- covers: c40, h36
- acceptance:
  - Rendering from the cache produces the same report and CSVs as the run that wrote it, without opening a Discord session for the scan — proven by making `scan_window` raise and rendering anyway
  - The cache contains author ids and no display names
  - A cache-rendered report states the scan timestamp on its face, not the render timestamp, so a stale cache is visibly stale
  - The cache file is gitignored alongside the HTML and CSVs
  - A report rendered from a cache older than the attachment expiry window says so, rather than presenting cached attachment URLs as live.

### t10 — Wire the links verb into the discord noun group

- instruction: Files: jlab/cli/`_commands`/discord.py, tests/`test_links_cli.py` (new). The bottleneck task — fifteen coverage targets, one file, nothing else in its wave. Copy `cmd_discord_members`' stage order but NOT its `included_author_ids` row filter. Return immediately after emitting the payload in --json mode, before `resolve_authors` is reachable.
- depends on: t7, t8, t9
- covers: c1, h1, c3, h20, c23, h6, c27, h15, c30, h10, h34, c32, h18, c37, h32
- acceptance:
  - One invocation with no flags and no setup beyond `DISCORD_BOT_TOKEN` produces both artifacts — HTML and CSV — with no second command and no manual assembly
  - Every link row carries all six fields: url, channel, timestamp, thread, author and jump link
  - --json is id-only under every flag combination: `resolve_authors` and the report writer raise if reached from the --json path, and no file is written
  - The render path resolves display names in one batch via an unchanged jlab/members/resolve.py; that file is byte-identical before and after the ship and is imported, not copied
  - A link whose author has left the guild still appears in HTML and CSV with its author id present and no display name — tested with a `fetch_member` that raises NotFound; the members row filter on `included_author_ids` is NOT applied here
  - The report's coverage figures are read off `scan_window`'s own per-channel statuses rather than recomputed
  - The spec's success signal carries a timed number or states that none exists yet; no invented figure ships as if measured

### t11 — Explain catalog entry for the links verb

- instruction: Files: jlab/explain/catalog.py. Add a `_DISCORD_LINKS` constant mirroring `_DISCORD_MEMBERS`, register ('discord','links'), and update the `_DISCORD` group's verb list. Run teken cli doctor . --strict to confirm.
- depends on: t9, t10
- covers: c4, h21
- acceptance:
  - uv run jlab explain discord links resolves
  - The `_DISCORD` group markdown lists the links verb alongside the others
  - Both tests/`test_cli.py`::`test_every_catalog_path_resolves` and uv run teken cli doctor . --strict pass

### t12 — Extend the read-only and public-only invariant guards to the links modules

- instruction: Files: tests/`test_members_invariants.py`. Add every new links module to `_GUARDED_FILES` and add a private-channel test for the links path shaped like `test_private_channel_contributes_nothing_to_member_statistics`.
- depends on: t9, t10
- covers: c6, h23, c7, h24
- acceptance:
  - The write-call regex guard covers every new links module — proven by adding a deliberate .send( to one of them and watching the test fail
  - A private channel present in the guild is never passed to history() during a links run, proven with a fake whose history() raises, and its name and URLs appear nowhere in the output

### t13 — Document the links verb and the content-retention inversion

- instruction: Files: README.md, CLAUDE.md. Mirror the structure of the existing members sections. The content-retention inversion must be stated in prose a maintainer meets as a decision, not discovers as a contradiction. Run markdownlint-cli2 with the repo's globs.
- depends on: t9, t10
- covers: c8, h25, c26, h14, c28, h16, c29, h17
- acceptance:
  - The exported spec, README.md and CLAUDE.md each state in plain words that the links report retains URL content and that this deliberately inverts the members path's no-content rule
  - A Jetson AI Lab maintainer who has not read the spec can run the verb from the README alone and understand what the report shows
  - README states the motivation, so the report's purpose is legible without the spec
  - The existing discord read and discord active verbs are documented as unchanged; links is additive

### t14 — Version bump and changelog entry

- instruction: Files: pyproject.toml, CHANGELOG.md. Use the version-bump skill — it updates both. Minor bump: this adds a verb and a shared capability. Last task before the PR.
- depends on: t10, t11, t12, t13
- acceptance:
  - pyproject.toml's version differs from origin/main so the version-check CI job passes
  - CHANGELOG.md carries a dated Keep-a-Changelog entry describing the links verb and the shared CSV capability

## Risks

- [unknown_nonblocking] Rate-budget contention on the shared `DISCORD_BOT_TOKEN`. A full-window links sweep costs roughly what a members sweep costs, and the token is shared with discord-bot-cli and other mesh agents. t9's cache reduces repeat cost but nothing bounds the first sweep or coordinates with other agents.
- [unknown_nonblocking] t10 is a single-file bottleneck: fifteen coverage targets land in jlab/cli/`_commands`/discord.py. It cannot be split by file and gates t11, t12 and t13, so the graph narrows to one task at that point regardless of how many agents are available. (task t10)
- [unknown_nonblocking] t2 edits the shared seam every other verb reads. Its acceptance criteria say breakage in the channels/read/active/members suites is expected work, which makes 'tests pass' a weaker merge gate for this task than for the others — a reviewer must check that each changed assertion was changed deliberately. (task t2)
- [unknown_nonblocking] Link liveness is unexamined: nothing verifies a shared URL still resolves, and t1 may find attachment URLs expire within a day. If it does, a large fraction of the report's rows are dead on arrival and the feature's value drops materially — but discovering that is t1's job, before the extractor is built. (task t1)
