# The motivating case, run for real

This records one real, live run of the paged-read / regex-search pipeline
against the actual Jetson AI Lab Discord and the actual `jlab-mongodb`
container — not a fixture. It demonstrates: a channel whose cached, recent
messages don't reach back far enough to cover an older window; a search
against that window honestly reporting it as uncovered rather than "no
matches"; a `fetch` that fills the gap; and the same search then returning
real matches with `complete: true`. It ends with a `purge` that leaves
nothing cached.

Only the channel **id** is recorded below, never its name. No message
content, author id, or author name is pasted into this file — only counts,
timestamps, and message ids (which are themselves opaque Discord snowflakes,
not content).

## Why the search/fetch window is in August, not September

The plan's acceptance criterion describes the case as "a channel whose most
recent 100 messages stop short of September" — i.e., a channel busy enough
that its cached top-100 read does *not* reach back far enough to cover an
early-September window, so that window reads as genuinely uncovered before a
`fetch` fills it in.

Measured live against the actual guild on 2026-09-13, **no public text
channel's most recent 100 messages are confined to September**: across the
~15 busiest channels (by `discord active --json`, ranked over 3/14/30-day
windows), the busiest is channel id `1327720920206282864`, whose top-100
`read --refresh` reaches back only to `2026-08-31T20:03:02.347000+00:00` —
89 of its 100 most recent messages post-date 2026-09-01, but the oldest 11
do not. No other channel in the top 15 comes as close; most reach back into
July or earlier. This is a fact about this guild's actual traffic (roughly
7 messages/day sustained in its busiest channel), not a bug: 100 messages
per channel simply outlasts the ~12 days since September began, for every
channel in this guild.

Per the brief's own fallback ("if no channel's last 100 messages stop short
of September 1, move the date forward... and state the date you used"), the
case below uses the **same channel** (id `1327720920206282864`) and demonstrates
the identical mechanism — a search window the initial 100-message cache does
not cover, filled by `fetch`, then re-searched with real matches — using a
window in **late August** instead of September, since that channel's actual
cached-vs-uncovered boundary sits at the August/September line. The
mechanism under test (coverage-gap reporting, then a backward fetch, then a
covered re-search) is exactly the one the plan describes; only the specific
calendar month differs, because no channel's real traffic supports a
September-dated demonstration as of this run.

## Environment

- Guild: `1326246312072581160` (the configured Jetson AI Lab Research Group guild).
- Channel: id `1327720920206282864` (a busy public text channel; name withheld
  per the read-only/public-only doc convention of never printing channel
  names into committed files).
- `jlab discord doctor --json` confirmed, before this run: guild reachable,
  jlab-mongodb reachable, and content encryption measured (store/fetch probe;
  AES-256-GCM; envelope version 2).
- Run with `uv run --extra discord jlab discord <verb> ...`, `DISCORD_BOT_TOKEN`
  already in the environment, `JLAB_MONGO_URI`/`JLAB_CACHE_KEY` loaded from
  `~/.config/jlab/cache.env`. Neither secret was printed, logged, or committed.

## What the first live run found (and fixed first)

Running this case for the first time surfaced two real defects that no unit
test had caught, because every unit test's Discord/Mongo doubles are
in-memory fakes that don't reproduce the exact shape of the real
dependency. Both are fixed on this branch (see `CHANGELOG.md`'s `[0.8.0]`
entry, `Fixed`) before the case below was run:

1. **Every real Discord channel was refused as "not a public channel."**
   `jlab.fetch.validated_channel` (used by `fetch` and `read --refresh`) and
   `jlab.sweep._Sweeper._verify` (used by `sweep`) both fetch a channel via
   `Client.fetch_channel()`. discord-bot-cli's client runs gateway-less
   (`Intents.none()`, REST only), so discord.py's internal guild cache is
   always empty; `fetch_channel()` then attaches a roleless "unavailable"
   stub guild to the channel it returns, whose `default_role` is `None`, so
   permission resolution against it always fails — even for a genuinely
   public channel. This was **measured against the live guild**: every real
   channel tried before the fix was refused. It would also have made `sweep`
   purge every channel's cache the first time it ran, reading every real
   public channel as newly-private. Fixed by resolving the public check
   against the guild `client.fetch_guild()` already returned (fully
   populated, from the real API payload) instead — filed upstream as
   `agentculture/discord-bot-cli#20`. See `jlab/fetch.py` and
   `jlab/sweep.py`'s `WORKAROUND(discord-bot-cli#20)` comments, and the
   regression tests in `tests/test_fetch.py`, `tests/test_read.py`, and
   `tests/test_sweep.py` (the sweep one modelling the critical case: a
   public channel must never be purged over this).
2. **Every cached-timestamp comparison against real Mongo raised
   `TypeError: can't compare offset-naive and offset-aware datetimes`.**
   pymongo decodes BSON datetimes as naive by default, dropping the UTC
   tzinfo every stored `created_at`/`updated_at`/`stored_at` carries going
   in; `jlab.read`/`jlab.coverage` compare those against an aware "now".
   Fixed by passing `tz_aware=True` at `jlab.mongo._collection`'s single
   `MongoClient` construction choke point.

Neither defect was visible in the test suite before this run: every fake
Discord channel's `permissions_for` reads a plain public/private flag rather
than reproducing discord.py's actual guild-resolution behavior, and every
fake Mongo collection is an in-memory Python dict that never round-trips
through BSON encoding at all.

## The case, step by step

### 1. Populate the cache with the channel's most recent 100 messages

```bash
uv run --extra discord jlab discord read 1327720920206282864 --refresh --limit 100 --json
```

Result: 100 messages returned, `complete: false`. At the time this was
taken for a harmless clock race; it was a real defect — jlab-mongodb stores
datetimes to the millisecond, so a coverage end written at a microsecond
"now" read back truncated and left a sub-millisecond "uncovered" tail on
every live `fetch` and `read --refresh`. Fixed afterwards (see *Follow-up
fix* below); re-run at the fix, the same `read --refresh` reports
`complete: true`. Oldest message: `2026-08-31T20:03:02.347000+00:00`. Newest:
`2026-09-12T14:13:47.055000+00:00`.

### 2. Search an uncovered window — reported as uncovered, not "no matches"

```bash
uv run --extra discord jlab discord search 1327720920206282864 \
  --grep "the" \
  --since 2026-08-25T00:00:00+00:00 --until 2026-08-26T00:00:00+00:00 \
  --json
```

The `2026-08-25 .. 2026-08-26` window is entirely before the cache's covered
start (`2026-08-31T20:03:02Z`), so nothing in it is cached yet. Result:

```json
{
  "match_count": 0,
  "scanned": 0,
  "complete": false,
  "uncovered": [{"start": "2026-08-25T00:00:00+00:00", "end": "2026-08-26T00:00:00+00:00"}]
}
```

stderr carried the diagnostic: `search window not fully covered: 1 gap(s);
run 'jetson-ai-lab-cli discord fetch' to fill them before trusting a
negative result`. This is the load-bearing assertion of the whole case: an
empty result here is reported as *unknown* (uncovered), never as a false
negative ("no matches").

### 3. Fetch the missing history

```bash
uv run --extra discord jlab discord fetch 1327720920206282864 --until 2026-08-25 --json
```

Result: `stored: 21`, `suppressed: 0`, two spans fetched
(`2026-08-25T00:00:00+00:00 .. 2026-08-31T20:03:02.347000+00:00` — the gap
the search above needed — plus a sub-millisecond span at the tail — the same
millisecond-precision defect as step 1), one span already covered
(`2026-08-31T20:03:02.347000+00:00 .. <fetch's own "now">`, i.e. everything
step 1 already cached). `complete: false` only because of that same
sub-millisecond tail; the `2026-08-25 .. 2026-08-26` window this case cares
about is now fully covered.

### 4. Re-run the same search — now covered, with real matches

```bash
uv run --extra discord jlab discord search 1327720920206282864 \
  --grep "the" \
  --since 2026-08-25T00:00:00+00:00 --until 2026-08-26T00:00:00+00:00 \
  --json
```

Result:

```json
{
  "match_count": 5,
  "scanned": 16,
  "complete": true,
  "uncovered": []
}
```

Five matches, five distinct message ids, all dated `2026-08-25` (the window
this case searched), `complete: true`. The same window that was reported
uncovered in step 2 is now honestly reported as fully covered, with real
matches from the real channel — the fetch in step 3 is what changed the
answer, not a cache that was silently trusted either way.

### 5. Purge — the demo leaves nothing cached

```bash
uv run --extra discord jlab discord purge --channel 1327720920206282864 --yes --json
```

Result: `cache: {"matched": 121, "deleted": 121}` (the 100 from step 1 plus
the 21 fetched in step 3), `coverage: {"channels": ["1327720920206282864"], "applied": true}`.
A follow-up `jlab discord coverage --json` for this channel id no longer
lists it among covered channels.

**Every other channel touched while searching for a suitable candidate for
this case was purged the same way** (`purge --channel <id> --yes` for each),
so this run's exploration left no cached content behind either — verified
with a final `jlab discord coverage --json`, which returned `{"channels": []}`.

## Follow-up fix: coverage at storage precision

The `complete: false` in steps 1 and 3 was the third live-only defect this
run surfaced (devague lapse l4). `jlab.coverage` now shrinks every stored
span inward to whole milliseconds (start rounded up, end down, so coverage
is never wider than what was fetched) and compares query windows at the
same precision. Re-checked live against the same channel at that commit:
`read --refresh --limit 20` → `complete: true`, no uncovered spans; a
repeat `fetch` → `complete: true`, `uncovered: []`; then
`purge --channel --yes` deleted the 20 cached messages, leaving the cache
empty.

## Summary table

| Step | Command | Key result |
|------|---------|-----------|
| 1 | `read --refresh --limit 100` | 100 msgs cached; oldest `2026-08-31T20:03:02Z` |
| 2 | `search` (before fetch) | `complete: false`; window `2026-08-25..26` reported uncovered |
| 3 | `fetch --until 2026-08-25` | `stored: 21`, `suppressed: 0`; gap filled |
| 4 | `search` (after fetch) | `complete: true`; `match_count: 5`, `scanned: 16` |
| 5 | `purge --channel --yes` | `cache.deleted: 121`; channel no longer in `coverage --json` |
