---
name: jetson-discord-scan
type: command
description: Read-only shallow scan of the Jetson AI Lab Discord — list PUBLIC channels, read a channel's recent messages, or find which public channels are active/high-traffic in the last month. Use when the user says "scan the Jetson AI Lab Discord", "what's active on Discord", "which channels are busy", "read #<channel>", or wants to read/collect Jetson AI Lab community discussion. It only reads — it does not index or answer. Private channels are always ignored.
---

# Jetson AI Lab Discord scan

Read-only reconnaissance of the **Jetson AI Lab Research Group** Discord
(guild `1326246312072581160`). This skill is a **thin shim** around the
`jlab` CLI — the real implementation lives in the `jlab discord` subcommands.
It never posts, reacts, or creates threads. This is the **read/fetch** side of
the agent's intended job; the indexing and question-answering parts are **not
built yet** — this skill only reads.

**Public channels only.** The bot token is read-scoped, and this skill filters
to channels the guild's `@everyone` role can view. Private / role-gated channels
are excluded from every default code path, so their names and contents never
leak into a scan result or the committed channel map. (`--all` is the one
opt-in that includes private channels, for an admin verifying the split.)

## Prerequisites

- `DISCORD_BOT_TOKEN` exported in the environment (read-scoped bot token — read
  from the env, never passed as a flag).
- `jlab` CLI installed and on `$PATH`.

Run `doctor` first to confirm the setup:

```bash
jlab discord doctor
# -> {"ok": true, "guild_id": "1326246312072581160", ...}
```

## Usage

```bash
# Which PUBLIC channels were active in the last month, ranked by traffic?
jlab discord active                                  # since=30d, all active channels
jlab discord active --since 7 --top 15 --preview 3   # last week, top 15, 3 msgs each
jlab discord active --since 30 --limit 100 --par 12  # deeper count, more parallelism

# List public channels (each tagged "public": true); --all adds private ones.
jlab discord channels
jlab discord channels --all

# Read one channel's recent messages (1-100, default 20).
jlab discord read 1427964860573945856 --limit 50     # e.g. #nvda-dgx-spark
```

All results are JSON on **stdout**; diagnostics/errors go to **stderr**. Exit
codes follow the agent-first contract: `0` ok, `1` user-input error, `2`
environment/setup error (missing token, absent CLI, unreadable guild).

### `active` output shape

```json
{
  "guild_id": "1326246312072581160",
  "since_days": 30,
  "fetch_limit": 40,
  "probed_text_channels": 97,
  "active_channels": 46,
  "channels": [
    {
      "id": "...", "name": "nvda-dgx-spark",
      "last_post": "2026-06-17T05:41:12+00:00",
      "msgs_in_window": 40,
      "saturated": true,          // >= fetch_limit posts in window — traffic underestimated; raise --limit
      "preview": [{"author": "...", "content": "...", "created_at": "..."}]
    }
  ]
}
```

Channels are ranked by `msgs_in_window` (in-window traffic), then by
`last_post`. `saturated: true` means the channel hit the fetch limit — bump
`--limit` (max 100) for a truer count on the busiest channels.

## How it works

This skill is a thin shim: it delegates to `jlab discord <subcommand>`. The
`jlab` CLI handles all the heavy lifting — talking to the Discord API, filtering
public channels, fanning out parallel reads, and merging results.

- **`jlab discord active`** probes each public text channel for recent messages,
  ranks them by traffic, and returns a ranked list with optional previews.
- **`jlab discord channels`** lists channels, tagging each with a `public` flag
  (a channel is public iff `@everyone` has `view_channel`).
- **`jlab discord read`** fetches recent messages from a single channel.

The `jlab` CLI reuses the `discord-bot-cli` library under the hood for the
Discord transport layer, but agents should interact only through `jlab discord`
subcommands.
