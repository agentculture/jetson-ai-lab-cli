---
name: jetson-discord-scan
type: command
description: Read-only shallow scan of the Jetson AI Lab Discord — list PUBLIC channels, read a channel's recent messages, or find which public channels are active/high-traffic in the last month. Use when the user says "scan the Jetson AI Lab Discord", "what's active on Discord", "which channels are busy", "read #<channel>", or wants to index Jetson AI Lab community discussion. Private channels are always ignored.
---

# Jetson AI Lab Discord scan

Read-only reconnaissance of the **Jetson AI Lab Research Group** Discord
(guild `1326246312072581160`). It wraps the sibling
[`discord-bot-cli`](https://github.com/agentculture/discord-bot-cli) **read
verbs only** — it never posts, reacts, or creates threads. This is the first
piece of the agent's intended job: fetch and index Jetson AI Lab discussion.

**Public channels only.** The bot token is read-scoped, and this skill filters
to channels the guild's `@everyone` role can view. Private / role-gated channels
are excluded from every default code path, so their names and contents never
leak into a scan result or the committed channel map. (`channels --all` is the
one opt-in that includes private channels, for an admin verifying the split.)

## Prerequisites

- `DISCORD_BOT_TOKEN` exported in the environment (read-scoped bot token — read
  from the env, never passed as a flag).
- `discord-bot-cli` with its `[discord]` extra reachable. By default this skill
  runs it from the sibling checkout's venv (`~/git/discord-bot-cli`); override
  with `DISCORD_BOT_CLI_PROJECT=<path>` or `DISCORD_BOT_CLI="<full command>"`.

Run `doctor` first to confirm both:

```bash
bash .claude/skills/jetson-discord-scan/scripts/scan.sh doctor
# -> {"ok": true, "guild_id": "1326246312072581160", "invocation": "..."}
```

## Usage

```bash
S=.claude/skills/jetson-discord-scan/scripts/scan.sh

# Which PUBLIC channels were active in the last month, ranked by traffic?
bash $S active                                  # since=30d, all active channels
bash $S active --since 7 --top 15 --preview 3   # last week, top 15, 3 msgs each
bash $S active --since 30 --limit 100 --par 12  # deeper count, more parallelism

# List public channels (each tagged "public": true); --all adds private ones.
bash $S channels
bash $S channels --all

# Read one channel's recent messages (1-100, default 20).
bash $S read 1427964860573945856 --limit 50     # e.g. #nvda-dgx-spark
```

All results are JSON on **stdout**; diagnostics/errors go to **stderr**. Exit
codes follow the agent-first contract: `0` ok, `1` user-input error, `2`
environment/setup error (missing token, absent extra, unreadable guild).

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

## How it works (and why a helper script)

- **Content** (`read`, and `active`'s per-channel fetch) uses the stock
  `discord-bot-cli channel messages`.
- **The channel list** uses `scripts/channels.py`, which imports
  `discord_bot_cli.discord_client` and reuses its `run(action)` transport seam to
  add a `public` flag the stock `channel list` doesn't expose (a channel is
  public iff `@everyone` has `view_channel`). This is cite-don't-import: the
  tool is reused as a library, not modified.
- `active` fans the per-channel reads out with `xargs -P`, then merges and ranks
  in one `python3` pass. ~100 channels scan in ~20s at `--par 8`.

## Reference data

`data/channels.json` is a committed snapshot of the **public** channel map
(id → name → type), so an agent can resolve a channel name without a network
call. Refresh it after the server's channels change:

```bash
bash .claude/skills/jetson-discord-scan/scripts/scan.sh channels \
  > .claude/skills/jetson-discord-scan/data/channels.json
```
