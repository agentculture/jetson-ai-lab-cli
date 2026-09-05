# jlab discord links report

> jlab discord links extracts every URL shared in the Jetson AI Lab Discord's public channels over the last 90 days and renders a local, gitignored HTML report of what the community is sharing
> instruction: Ship it as one verb: uv run jlab discord links. Verify by running it against the real guild once, end to end, and opening both the HTML and a CSV.

## Audience

- Jetson AI Lab maintainers and the mesh agents working alongside them — the same readers the members report serves. They want to see what the community is actually reading and sharing, without joining 100 channels or scrolling 90 days of history by hand.
  - instruction: Write the README section for a maintainer who has never run the verb, not for someone who read this spec.

## Before → After

- Before: There is no way to see what has been shared. 'jlab discord read' prints one channel's recent messages and 'jlab discord active' ranks channels by traffic, but neither extracts URLs, and nothing spans the full 90-day window across all public channels. Finding a link someone posted last month means remembering which channel and scrolling.
  - instruction: Do not remove or reshape 'discord read' or 'discord active'; links is additive.
- After: One invocation of 'jlab discord links' sweeps 90 days of public text channels and writes a local HTML report plus CSV tables of every URL shared — each with its channel, timestamp, thread reference and the member who posted it — so a maintainer can see the community's reading list in one page and slice it in a spreadsheet.
  - instruction: Verify by running the verb once end to end and opening both artifacts, per h1.

## Why it matters

- The links a community shares are the clearest available signal of what it is actually working on and struggling with. For an agent whose stated purpose is fetching and indexing Jetson AI Lab knowledge, the URLs members already vetted for each other are the highest-value seed corpus available, and today they are unreachable.
  - instruction: State this motivation in the README so the report's purpose is legible without the spec.

## Requirements

- The verb reuses the existing scan stage unchanged: `_discord`.`scan_window` (jlab/cli/`_discord.py`:524-592) already pages the full 90-day window (`DEFAULT_WINDOW_DAYS`=90) over public text channels with --since/--concurrency, so links needs no new fetch machinery.
  - instruction: Build `cmd_discord_links` on `scan_window`; do not add a second windowed pager.
  - honesty: The links verb adds no second windowed pager: a grep of the diff shows history() paging logic still exists in exactly one place, jlab/cli/`_discord.py`.
- A new jlab/links/ package mirrors jlab/members/ shape: extract.py (URL extraction), report.py (HTML), paths.py (contained output dir). jlab/members/resolve.py is reusable UNCHANGED because it consumes only the KEYS of its stats mapping (resolve.py:204-206).
  - instruction: Import `resolve_authors` as-is; do not fork it.
  - honesty: jlab/members/resolve.py is byte-identical before and after the ship, and the links package imports it rather than copying it.
- The explain catalog needs a ('discord','links') entry plus an updated `_DISCORD` group verb list (jlab/explain/catalog.py:255-261 and 132-137), or tests/`test_cli.py`::`test_every_catalog_path_resolves` and 'teken cli doctor . --strict' both fail.
  - instruction: Add the catalog entry in the same commit as the verb.
  - honesty: 'uv run jlab explain discord links' resolves, and both tests/`test_cli.py`::`test_every_catalog_path_resolves` and 'teken cli doctor . --strict' pass.
- .gitignore needs its own links entry: line 237 is the path-anchored '/data/reports/members/', which does NOT cover a sibling links report directory.
  - instruction: Land the ignore rule in the same commit as the verb; assert with git check-ignore, as tests/`test_members_paths.py`:60 does.
  - honesty: 'git status' after a real links run shows no untracked report files, and git check-ignore succeeds on the written paths.
- URLs are extracted from three sources: message.content by regex, attachments\[\].url, and embeds\[\].url. Only the first is available today — `_serialize_message` (jlab/cli/`_discord.py`:202-211) reads none of the other two — so the extractor depends on the seam extension in c18.
  - instruction: Decide the embed/attachment question before writing the extractor — it changes whether the seam is touched.
  - honesty: A message whose only URL is an attachment, and a message whose only URL is in an embed, each contribute exactly one link to the report — proving all three extraction sources are live, not just the regex.
- Bot and webhook handling is decided explicitly for links rather than inherited: excluded by default, includable behind an opt-in flag (see c21). `scan_window`'s `exclude_bots` (jlab/cli/`_discord.py`:524-592) is the mechanism; the members spec's blanket exclusion (spec:108-109) informed the default but did not dictate it.
  - instruction: Default excludes bots; the opt-in flag is c21's --include-bots. No separate mechanism.
  - honesty: The default run and the opt-in run over the same window differ only by bot-authored links, with no human-authored link appearing or disappearing between them.
- `_serialize_message` (jlab/cli/`_discord.py`:202-211) gains attachments\[\].url and embeds\[\].url. Because it is the single serializer behind channels/read/active/members, every existing discord payload changes shape in the same edit — this is a shared-seam change, not a links-local one.
  - instruction: Re-run the whole tests/`test_discord.py` and members suite after the seam edit; treat any payload-shape assertion break as expected work, not collateral.
  - honesty: After the serializer gains attachments, embeds and `jump_url`, the existing channels/read/active/members suites all still pass. Any assertion that breaks is updated deliberately, and no existing verb's documented output silently changes meaning.
- Each extracted link record carries url, channel, timestamp, and a thread reference where one applies, so links shared in the same thread can be grouped in the report. No surrounding message text is retained.
  - instruction: Carry thread id and name on each link record where the message has one; group by thread in the report and leave the field empty otherwise.
  - honesty: Two links shared in the same thread are visibly grouped in the report, and a link shared outside any thread renders cleanly with an empty thread reference rather than a placeholder or an error.
- Bot and webhook links are excluded by default and included only behind an explicit opt-in flag on the links verb, wired to `scan_window`'s existing `exclude_bots` parameter.
  - instruction: Name the flag explicitly (e.g. --include-bots) and wire it to `scan_window`'s `exclude_bots`; state the active mode in the report metadata block.
  - honesty: Running with no flag yields zero bot-authored links; running with the opt-in yields strictly more links than the default run on the same window, and the report states which mode produced it.
- The report's tables are also written as CSV next to the HTML. The CSVs carry resolved display names, so they are person-level data and must live inside the same repo-anchored, gitignored report directory as the HTML — never a bare-CWD write.
  - instruction: Assert with git check-ignore that the CSV paths are ignored too, not just the .html.
  - honesty: git check-ignore returns success for every CSV path the verb writes, not just the .html — verified by a test that enumerates the actual written filenames rather than asserting a hardcoded directory.
- CSV output is a SHARED capability, not links-only: the members report also emits its tables as CSV alongside its HTML, into the same repo-anchored gitignored directory. Both verbs get the same HTML+CSV pair rather than links growing a second output style the sibling verb lacks.
  - instruction: Give the CSV writer one implementation both report modules call; assert git check-ignore covers the members CSVs too.
  - honesty: The members verb and the links verb produce their CSVs through one shared writer — a single implementation, not two — and the members CSVs land in the members report directory, gitignored, in the same commit that adds them.
- Each link record carries a jump link back to the Discord message the URL appeared in, so a reader can open the original conversation in one click. This is a third field the shared serializer must supply: a grep for `jump_url` across jlab/ returns nothing today, and `_serialize_message` (jlab/cli/`_discord.py`:202-211) carries neither message.`jump_url` nor even a channel id — the message dict has only id/author/content/`created_at`. The jump link is derivable as <https://discord.com/channels/><`guild_id`>/<`channel_id`>/<`message_id`> once channel id is threaded through per c19, so carrying `jump_url` verbatim from discord.py is a convenience, not a necessity.
  - instruction: Render the jump link as the clickable anchor in both the HTML and a plain column in the CSV; it is subject to the same scheme sanitisation as c11 even though it is CLI-constructed rather than user-supplied.
  - honesty: Every link row's jump link, when opened, lands on the message that actually contains that URL — verified against a real message rather than a constructed fixture before shipping.
- --json is strictly id-only, exactly as the members verb does it: it emits the extraction stage with author ids, urls, channels, timestamps, thread references and jump links, resolves no display names, writes no files, and has no opt-in flag that changes this. Names are produced by a separate call that fetches them from ids — the unchanged jlab/members/resolve.py batch (guild.`fetch_member` over a second Discord session), invoked only on the render path immediately before the HTML and CSVs are written. --json returns before that call is ever reached.
  - instruction: Copy the members containment guard verbatim: return immediately after emitting the extraction payload in --json mode, before `resolve_authors` is called. Test it with raising fakes, as tests/`test_members_cli.py` does.
  - honesty: A --json run of the links verb never reaches the name-resolution call and never writes a file, under any flag combination — provable the way the members path proves it, by making `resolve_authors` and the report writer raise if reached from the --json path. Display names exist only in the HTML and CSV artifacts, never on stdout.
- CSV output needs its own injection defence, which no existing code provides. jlab/members/report.py's only sanitiser is `_esc`, which is html.escape(quote=True) — HTML context only. A CSV field beginning with =, +, -, @, tab or carriage return is executed as a formula by Excel and Google Sheets on open, and both a shared URL and a resolved display name are attacker-controlled text landing in exactly that position. c22's whole rationale is that a maintainer opens these in a spreadsheet, which is precisely the execution context.
  - instruction: Prefix-escape any field starting with = + - @ TAB or CR (leading apostrophe or equivalent) in the shared CSV writer from c25, and test it with a display name and a URL that each start with one of those characters.
  - honesty: A member whose display name begins with '=' and a shared URL beginning with '@' both open in LibreOffice or Excel as inert text, not as a formula — verified by opening a generated CSV, not only by asserting on the bytes.
- The CSVs must carry their own coverage metadata. h10 binds the coverage figures to the report, but a CSV has no metadata block and is the artifact most likely to be opened, mailed, or imported on its own. A partial sweep (`scan_window`'s `STATUS_PARTIAL`, or the `max_messages_per_channel` cap of 5000 at jlab/cli/`_discord.py`:48) would then present as a complete dataset with nothing on the face of it saying otherwise.
  - instruction: Either emit a coverage header/sidecar row set in each CSV, or encode window and completeness in the CSV filename; do not rely on the reader also having opened the HTML.
  - honesty: A reader given only a CSV, with no access to the HTML, can tell from the CSV alone which window it covers and whether any channel was partial or failed.
- Writing the artifact set must be all-or-nothing. jlab/members/report.py:405 is a bare path.`write_text`(html) — fine when the output is one file, but c22 and c25 turn it into an HTML file plus N CSVs written in sequence. A run killed partway (Ctrl-C, a 429 storm, an OOM) leaves a directory holding some artifacts from this run and possibly stale ones from the last, with no marker distinguishing them, and the timestamps will look plausible.
  - instruction: Write into a temp directory and rename into place once every artifact is complete, or write all files with a single run id in their names so a partial set is self-evident.
  - honesty: Killing the verb midway through writing leaves either a complete artifact set or no new artifacts at all — never a complete HTML beside missing or stale CSVs.
- The links render path must NOT filter rows by `resolve_result`.`included_author_ids` the way jlab/cli/`_commands`/discord.py:172-175 does. A departed poster's link stays in the report carrying its bare author id, with no display name resolved; the author column shows the id. Dropping the row would delete a link because of something that happened to its poster after the share.
  - instruction: Copy `resolve_authors`' call shape from the members path but not its row filter; add a test that a departed author's link still appears, with an id and no name.
  - honesty: A link shared by someone who has since left the guild appears in both the HTML and the CSV, with its author id present and no display name — verified with a fake whose `fetch_member` raises NotFound.
- The canonical CSV is FLAT: one row per share, every cell atomic, no nested lists — so it loads directly into Google Sheets and into pandas without parsing. A second, deduped per-URL summary CSV is derived from that flat table (share count, first and last seen, channels touched) rather than by complicating the extraction model.
  - instruction: Emit the flat share table first and derive the summary from it in code; assert no CSV cell contains a delimiter-joined list.
  - honesty: The flat CSV loads with a stock pandas.`read_csv` and a stock Google Sheets import with no preprocessing, and every cell in it is a scalar.
- The report and CSVs must be regenerable without a second full scan. One 90-day sweep costs a large share of a bot token shared with other mesh agents (v1), so the extraction stage's output is persisted to the contained report directory and the render stage can run from that cache instead of re-scanning. This is the same id-only payload --json emits (c32), so the cache carries author ids, urls, channels, timestamps, thread references and jump links — and no display names, making it a strictly smaller privacy surface than the CSVs sitting beside it. It lives in the same repo-anchored gitignored directory and is covered by the same ignore rule; name resolution still happens only on the render path.
  - instruction: Give the verb a way to render from the cached extraction rather than rescanning (e.g. a --from-cache path or an explicit rescan flag), and assert the cache file is gitignored alongside the HTML and CSVs.
  - honesty: Rendering from the cache produces the same report and CSVs as the run that wrote it, and does so without opening a Discord session for the scan — provable by making `scan_window` raise and rendering anyway. The cache itself contains no display name. A cache-rendered report also states the SCAN timestamp on its face, not the render timestamp, so a stale cache is visibly stale and cannot be read as current.

## Honesty conditions

- A maintainer who runs the verb once, with no flags and no prior setup beyond `DISCORD_BOT_TOKEN`, gets a report they can read and a CSV they can open — no second command, no manual assembly step.
- The write-call regex in tests/`test_members_invariants.py` runs over the new links modules too — provable by adding a deliberate .send( to one of them and watching the test fail.
- A private channel present in the guild is never passed to history() during a links run — provable with a fake whose history() raises, exactly as tests/`test_members_invariants.py`:257 does — and its name and URLs appear nowhere in the output.
- The exported spec, the README and CLAUDE.md each state in plain words that the links report retains URL content and that this deliberately inverts the members path's no-content rule, so a reader meets the inversion as a decision rather than discovering it as a contradiction.
- The verb refuses to write when no culture.yaml repo root is found, and a filename containing path separators or .. cannot escape the report directory.
- pyproject.toml still reads dependencies = \[\] after the ship, and the generated HTML loads and renders with no network access.
- A message containing a javascript: URL, a data: URL, and a URL with embedded HTML renders in the report as inert escaped text with no executable href, and the report loads with no network fetch and no script tag.
- Inspecting the intermediate scan and extraction data shows author ids only; a display name appears nowhere until the final render step, and an aborted run leaves no file containing a name.
- A Jetson AI Lab maintainer who has not read this spec can run the verb from the README alone and understand what the report is showing them without asking.
- One invocation produces both artifacts — HTML and CSV — with no second command and no manual assembly, and every link row carries all six fields (url, channel, timestamp, thread, author, jump link).
- The existing 'discord read' and 'discord active' verbs behave exactly as before after the ship; links is purely additive to the noun group even though it reopens the shared serializer.
- The report's own text says what it is for, so a reader who never sees this frame understands why the links were collected.
- The report's coverage figures are read off `scan_window`'s own per-channel statuses rather than recomputed, so a partial or failed channel cannot be silently counted as complete.
- The performance number in the shipped spec traces to a timed run, not an estimate — and until one exists, the spec says so in the success signal itself rather than carrying a figure that reads as measured.
- An attachment URL written into the report is either still reachable when a reader opens the report, or is visibly marked in the report as an expiring link — never presented as a durable one.

## Success signals

- A single 'jlab discord links' run over the full 90-day window completes within a measured, agreed budget at the default concurrency of 4, and the report states its own coverage: the count of channels ok, partial and failed, so an incomplete sweep is never presented as exhaustive. The budget is NOT YET SET: the 'under 5 minutes' figure previously carried here was invented, never measured, and is recorded as lapse l1 (assumption-for-measurement). The only timing on record is ~13s for 100 channels, which is the SHALLOW active scan and not comparable. Time one real full-window run and set the number from it before treating this signal as a target.
  - instruction: Measure against the members path's recorded baseline (100 channels in ~13s for the shallow active scan; the members full-window sweep is the closer comparison).

## Scope / boundaries

- Read-only stays absolute. The links path adds no post/react/thread/edit call; its new modules must be added to the `_GUARDED_FILES` list in tests/`test_members_invariants.py`:41-48 so the existing write-call regex guard covers them too.
  - instruction: Add jlab/links/\*.py to `_GUARDED_FILES` in the same commit.
- Public-only stays absolute and stays a PRE-fetch filter. `_public_text_channels` (jlab/cli/`_discord.py`:455-467) drops private channels before any history() call, so no private channel's URLs can ever be fetched, let alone rendered.
  - instruction: Mirror tests/`test_members_invariants.py`:257 — a private channel fake whose history() raises if called.
- The links report is a CONTENT-RETENTION report, and this is a deliberate inversion of the members path's rule, not an oversight. docs/specs/2026-09-04-jlab-discord-members-report.md:112 states 'Message CONTENT never leaves the aggregation stage' and jlab/members/aggregate.py:12-15 discards the string after measuring its length. A URL is content; links cannot exist under that rule. The inversion must be stated explicitly in the spec, README and CLAUDE.md rather than silently contradicting the sibling feature.
  - instruction: Write the reconciliation as its own boundary bullet; a reviewer must not have to infer it.
- Output containment carries over verbatim: a fixed, repo-anchored, gitignored path resolved by walking up to the culture.yaml marker (jlab/members/paths.py:31-71), printing only the path on stdout. Only the subdir constant changes.
  - instruction: Reuse the paths.py pattern including the traversal guard (`test_members_paths.py`:94).
- Zero runtime dependencies holds. dependencies = \[\] in pyproject.toml:16; the HTML is hand-emitted with the `_esc`/`_STYLE`/`_bar_chart` helpers already proven in jlab/members/report.py. No templating or charting library.
  - instruction: Check pyproject.toml still reads dependencies = \[\] after the ship; the \[discord\] extra is the only permitted addition.
- Rendered URLs are a NEW attack surface the members report never had, because links are href-shaped. Hostile schemes (javascript:, data:) must be neutralised and every URL HTML-escaped; the report stays self-contained with no script and no external fetch.
  - instruction: Mirror the hostile-input tests at tests/`test_members_report.py`:101-130, extended to javascript: and data: URLs.
- Display names are resolved in one final batch at render time via the unchanged jlab/members/resolve.py, and both the HTML and the CSVs carry them. --json never reaches that call (c32), so the containment posture matches the members path exactly: names exist only inside the gitignored report directory, never on stdout.
  - instruction: Reuse jlab/members/resolve.py unchanged; call it once, after extraction, immediately before render. Decide the --json containment posture explicitly rather than inheriting it by accident.

## Non-goals

- The CLI issues no verdict about links either. No 'most shared', 'top domain', or 'recommended reading' ranking — it organizes what was shared and leaves the judgement to the human or agent reading it, matching the house rule at docs/specs/2026-09-04-jlab-discord-members-report.md:96-97.
  - instruction: Grep the rendered HTML for verdict language before shipping, as the members ship did.
- Phase 1 scans public TEXT channels only; FORUM channels remain a follow-up slice, not promised here, and voice channels cannot carry text links at all. Threads are no longer deferred the way the members spec deferred them (docs/specs/2026-09-04-jlab-discord-members-report.md:106-107): a thread REFERENCE is now carried on each link record per c19, even though thread channels are not themselves scanned as separate sources.

## Assumptions

- No privileged-intent or token work is needed. `discord_bot_cli` builds its client with discord.Intents.none() and reads history over REST, so message.content already flows through `_serialize_message` (jlab/cli/`_discord.py`:202-211) and is printed verbatim today by 'jlab discord read'. Content access is an existing capability, not a new grant.
  - instruction: Confirm by running jlab discord read against a public channel before building.
- The scan's completeness fields are inherited for free. `scan_window` already returns per-channel ok/partial/failed status and a `max_messages_per_channel` cap of 5000 (jlab/cli/`_discord.py`:37-39, :48, :578-592), so the links report can surface an incomplete sweep instead of presenting it as exhaustive.
  - instruction: Render `channels_ok`/partial/failed in the report metadata block, as the members report does.
- Discord attachment URLs are assumed to remain reachable after the report is written. I did NOT verify this against the repo or the API — it is outside what this pass read. Discord has served attachment CDN URLs as signed, expiring links (ex/is/hm query parameters) since 2023; if that holds, the attachment URLs c16 extracts are dead within roughly 24 hours of the scan, and a 90-day report's attachment rows would be near-uniformly broken while looking perfectly valid.
  - instruction: Verify before building the attachment extractor: fetch one attachment URL from a real message and check for signed expiry parameters. If they expire, either drop attachment URLs, or store the jump link as the durable pointer and mark the attachment URL as ephemeral in the report.

## Scope exploration

- `s1` — `jlab/cli/_discord.py (the read-only seam)`: `scan_window` already pages the full 90-day window over pre-filtered public text channels with --since/--concurrency and returns per-channel ok/partial/failed status. `_serialize_message` keeps message.content but drops embeds, attachments AND `jump_url`, and carries no channel id at all — the dict is only id/author/content/`created_at` — so the links path must extend the serializer (c18, c31) and thread chan\[id\]/chan\[name\] through when flattening.
  - seeds: `c2`, `c15`, `c16`
- `s2` — `jlab/cli/_discord.py:455-467 (_public_text_channels)`: private/role-gated channels are dropped BEFORE any history() call, so public-only is a pre-fetch guarantee the links path inherits rather than re-implements.
  - seeds: `c7`
- `s3` — `discord_bot_cli.discord_client (Intents.none, REST-only)`: message content already flows to callers over REST and is printed verbatim by 'jlab discord read' today — no privileged Message Content Intent and no token change is needed for a links verb.
  - seeds: `c14`
- `s4` — `jlab/members/aggregate.py:1-31 and docs/specs/2026-09-04-jlab-discord-members-report.md:112`: the members path's load-bearing rule is that content is read once, reduced to a length, and discarded; a links report is definitionally content-retaining, so this is the one sibling boundary the new verb inverts and must reconcile out loud.
  - seeds: `c8`, `q3` (question, resolved), `q4` (question, resolved)
- `s5` — `jlab/members/resolve.py:193-206`: `resolve_authors` consumes only the KEYS of its stats mapping and never inspects the values, so it is the one members module a links verb can import unchanged — but only if links attributes URLs to people at all.
  - seeds: `c3`, `q4` (question, resolved)
- `s6` — `jlab/members/report.py and jlab/members/paths.py:31-71`: there is no generic renderer or `report_path`(kind, ...) entry point — field names and the reports subdir tuple are members-specific — so links needs sibling modules; the reusable parts are the `_esc`/`_STYLE`/`_bar_chart` helpers and the render->write split.
  - seeds: `c3`, `c9`, `c10`
- `s7` — `.gitignore:237`: the ignore rule is the path-anchored '/data/reports/members/', not a wildcard, so a links report directory is NOT covered and would be committable by default.
  - seeds: `c5`
- `s8` — `jlab/cli/__init__.py:90-93 and jlab/cli/_commands/discord.py:245-368`: the discord noun group is already registered; a new verb touches only discord.py's register() and a `cmd_discord_links` handler — `__init__.py`, `_errors.py` and `_output.py` all stay untouched.
  - seeds: `c2`
- `s9` — `jlab/explain/catalog.py:132-137, 255-261`: every registered path must resolve or both `test_every_catalog_path_resolves` and the strict teken rubric fail, so the ('discord','links') entry plus the group verb list are ship-blocking, not documentation polish.
  - seeds: `c4`
- `s10` — `tests/test_members_invariants.py:41-88 and :257-297`: the read-only guarantee is enforced by a write-call regex over an explicit `_GUARDED_FILES` list, and public-only by a private-channel fake whose history() raises if touched — both extend to links only if the new modules are added to that list.
  - seeds: `c6`
- `s11` — `tests/test_members_report.py:101-130, 219`: the report tests already pin HTML-escaping of hostile display names and absence of verdict language, but nothing there covers href-shaped output — clickable URLs are an attack surface with no existing test precedent.
  - seeds: `c11`, `c12`
- `s12` — `.github/workflows/tests.yml:40-75, 77-131`: the PR gates are black/isort/flake8/bandit, markdownlint, 'teken cli doctor . --strict', coverage `fail_under`=60, and a version-check job that fails any PR not bumping pyproject.toml's version.
  - seeds: `c4`
- `s13` — `.claude/skills/jetson-discord-scan/scripts/scan.sh and SKILL.md`: scan.sh is a pure 'exec uv run jlab discord $@' passthrough and forwards a links verb with zero changes; SKILL.md documents only channels/read/active and never got a members section, so it is already stale rather than newly stale. Note the skill's stated capability is a 'shallow scan', which a 90-day links sweep with CSV output outgrows.
  - seeds: `c13`
- `s14` — `README.md:74-106 and CLAUDE.md:60-86`: the members verb got a parallel prose section in both files stating its boundaries and its no-verdict claim; links needs the same pair, and it is where the content-retention inversion has to be visible to a human reader.
  - seeds: `c8`, `c12`
- `s15` — `challenge pass / adjacent-systems lens: _serialize_message consumers (jlab/cli/_commands/discord.py, tests/test_discord.py)`: Examined: the seam change in c18/c31 adds KEYS to the message dict rather than renaming or removing any, so existing consumers of channels/read/active --json keep working and only gain fields. Clean on this lens; the residual exposure is that no consumer outside this repo is known to the pass, so 'additive is safe' holds for in-repo callers only.
- `s16` — `challenge pass / security lens: private threads inside public channels`: Examined: `_public_text_channels` (jlab/cli/`_discord.py`:455-467) gates on the CHANNEL's @everyone `view_channel` permission, and channel.history() returns the channel's own messages, not messages inside its threads — so a private thread hosted in a public channel contributes no message content. Clean pass. Note this also means c19's thread reference is a pointer to a thread whose CONTENTS were never scanned, which is the safe direction but worth stating in the report so a reader does not assume thread coverage.
- `s17` — `challenge pass / concurrency lens: scan_window + resolve_authors fan-out`: Examined: both stages bound themselves with an asyncio.Semaphore over a single client session (jlab/cli/`_discord.py`:495-500, :556-571; jlab/members/resolve.py:229-241, as amended this session), with 429 retry/backoff in `_collect_history`/`_retry_after` (`_discord.py`:230-318). No shared mutable state is written across tasks; results are gathered in argument order. Clean pass on correctness — the residual concern is budget, not races, and is already parked.
- `s18` — `challenge pass / reversibility lens: report containment vs redistribution`: Examined: jlab/members/paths.py refuses to write without a culture.yaml repo root and anchors on `__file__` rather than CWD, and .gitignore covers the directory — so accidental commit and stray-directory writes are both bounded. NOT examined and not boundable by this pass: what happens to an artifact after a human moves it, which the CSV form makes materially easier than the members HTML did.
- `s19` — `challenge pass / security lens: jlab/members/report.py:_esc (html.escape, quote=True)`: The only sanitiser in the codebase is HTML-context escaping. CSV is a second output context with a different injection vector (leading = + - @ TAB CR executed as a formula on open) and no defence exists for it anywhere in the repo.
  - seeds: `c33`
- `s20` — `challenge pass / failure-mode lens: jlab/members/report.py:405 (path.write_text)`: The single bare `write_text` is safe for one file and becomes a partial-write hazard the moment c22/c25 make the output a multi-file set.
  - seeds: `c35`
- `s21` — `challenge pass / overlooked-actors lens: jlab/cli/_commands/discord.py:172-175 + resolve.py:97-106`: The members pipeline drops departed authors by filtering rows on `included_author_ids`. That is correct where the row IS the person and silently destructive where the row is a link that happens to have a poster; routed to q1 rather than decided here.
  - seeds: `q1` (question, resolved)

## Decisions

- Serializing a thread reference reverses a position the members frame took. That frame recorded, as contested claims c6/c22, that 'the adapter does not serialize reply/thread references; adding them would reopen jlab/cli/`_discord.py` and widen this ship.' The seam is being reopened here anyway for attachments and embeds, so the cost that justified deferring thread refs no longer applies.
  - instruction: Cross-reference the members frame when writing the spec so the reversal reads as a deliberate revisit, not an unnoticed contradiction.
- Ripple into the members path is explicitly ACCEPTED. Reopening the shared seam (c18) changes payload shapes for channels/read/active/members, and the members verb may be changed to match rather than being insulated from the links work. This lifts the constraint the members frame imposed on itself when it deferred thread references as 'widening the ship'.
  - instruction: Where the members path and the links path would otherwise diverge, prefer converging them over adding a compatibility shim.
- Credential-bearing URLs are an ACCEPTED risk, not a blocker. The report and CSVs are a local artifact in a gitignored, repo-anchored directory — this is not a public store, and no publishing surface exists in the design. The build adds no scrubbing, redaction, or secret-detection pass over extracted URLs; the containment that already exists (c9's fixed gitignored path, resolved from `__file__` rather than CWD) is the whole mitigation, and it is judged sufficient for a local artifact.
  - instruction: Do not add a secret-scanner over URLs. If a publishing or upload path is ever proposed for these artifacts, this decision is void and the risk must be re-adjudicated.

## Hard questions

- How much beyond the URL itself is retained — the bare URL only, or also surrounding message text as context? Retaining context makes the report far more useful and makes it a genuine message-content archive; the URL-only answer keeps the inversion of the members rule as narrow as it can be. (resolved: URL plus channel, timestamp, and thread reference where one applies. NO surrounding message text: the content-retention inversion stays as narrow as it can be while still letting links that were shared in the same thread be grouped together.)
- Is each link attributed to the member who shared it? Attribution reintroduces the person-level data the members path fenced behind a --json containment guard (tests/`test_members_cli.py` makes `resolve_authors` and `write_report` raise in --json mode); an unattributed links report needs no name resolution at all and is a strictly smaller privacy surface. (resolved: Attributed, with display names resolved at render time for the HTML. In addition the report's tables are emitted as CSV alongside the HTML, so the data is usable outside the browser.)
- Does 'all links' include URLs that exist only as attachments or embeds? A content-only regex silently misses attachment URLs and embed-only shares, so the report would under-count without saying so. Including them means extending `_serialize_message` (jlab/cli/`_discord.py`:202-211) to carry message.attachments\[\].url and embed urls — reopening the shared seam that the members ship deliberately left closed. (resolved: Include all three: message.content, attachments\[\].url and embeds\[\].url. This deliberately reopens the shared seam `_serialize_message` (jlab/cli/`_discord.py`:202-211); every existing discord payload shape changes with it, so the channels/read/active/members tests must be re-run as part of the ship.)
- Are bot- and webhook-posted links included? The members path excludes them as non-participants; a links report may want them precisely because feed bots share URLs. `scan_window`'s `exclude_bots` flag makes either answer one keyword, but the answer changes what the report means. (resolved: Make it a flag, default OFF. Bots and webhooks are excluded unless explicitly asked for, matching the members path's default; `scan_window`'s `exclude_bots` already provides the mechanism and the flag just exposes it.)

## Open parks

- [unknown_nonblocking] Rate-budget contention: `DISCORD_BOT_TOKEN` is one shared bot credential used by discord-bot-cli and other mesh agents. A 90-day full-window links sweep costs roughly what the members sweep costs, and running both back to back doubles that draw. Carried forward from the members frame, still unaccounted for.
- [unknown_nonblocking] Link liveness is not examined by this pass. Nothing here checks whether a shared URL still resolves, and fetching them would turn a read-only Discord scan into an outbound crawler — a materially different tool with its own consent questions. Recorded as out of reach rather than checked and cleared.

## Resolved vagueness

- [unknown_nonblocking] Shared URLs can themselves be credentials. Signed CDN links, Colab and Drive sharing links, invite links, and API keys pasted in query strings all appear routinely in developer Discords. A links report concentrates 90 days of them into one file, and the CSV form is far more forwardable than the HTML the members frame worried about — it mails, imports, and pastes cleanly. The gitignore boundary (c9) contains the file on disk and does nothing about redistribution. This pass found no mechanism to bound it and is not proposing one; recorded so the risk is visible rather than assumed away. — resolved: Accepted risk — see c39. Local, gitignored artifact; not a public store.
- [unknown_nonblocking] Whether the CSVs should be regenerable without a second full scan. Every run costs a full 90-day sweep against a shared bot token; a maintainer who wants a differently-shaped CSV today must re-scan. No intermediate cache is specced, and adding one would create a second on-disk store of person-level data needing its own containment. Not decidable now — recorded as a shape the build may back into rather than choose. — resolved: Needed, not deferred — see c40. The extraction payload is cached to the contained directory so the render stage can rerun without a second sweep.
