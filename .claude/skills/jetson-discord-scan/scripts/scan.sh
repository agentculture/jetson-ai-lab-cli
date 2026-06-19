#!/usr/bin/env bash
set -euo pipefail
#
# jetson-discord-scan — read-only shallow scan of the Jetson AI Lab Discord.
#
# Wraps the sibling `discord-bot-cli` READ verbs only (channel list / messages).
# It NEVER posts, reacts, or creates threads — the bot token is read-scoped, and
# this script deliberately exposes no write path. All results are JSON on stdout;
# diagnostics and errors go to stderr. Exit codes follow the agent-first
# contract: 0 ok, 1 user-input error, 2 environment/setup error.
#
# Subcommands:
#   doctor                      Preflight: token present + guild readable.
#   channels [--all]            List PUBLIC channels as JSON, each with a "public"
#                               flag (--all also includes private/role-gated ones).
#   read <channel_id> [--limit N]  Last N messages of one channel (1-100, default 20).
#   active [flags]              THE shallow scan — probe all PUBLIC text channels
#                               (private channels are ignored), keep those
#                               posted-to within --since days, rank by in-window
#                               traffic, emit a short preview of each.
#
# `active` flags:
#   --since DAYS    Window for "recently active" (default 30 = last month).
#   --limit N       Messages fetched per channel for ranking (1-100, default 30).
#   --top K         Keep only the K most active channels (default: all active).
#   --preview P     Messages echoed per active channel (default 5).
#   --par N         Parallel channel fetches (default 6).
#
# Environment:
#   DISCORD_BOT_TOKEN          required — read-scoped bot token (read from env).
#   JLAB_GUILD_ID              override the guild (default: Jetson AI Lab).
#   DISCORD_BOT_CLI            full command to invoke the CLI (overrides resolution).
#   DISCORD_BOT_CLI_PROJECT    checkout whose .venv has the [discord] extra
#                              (default: ~/git/discord-bot-cli).

GUILD_ID="${JLAB_GUILD_ID:-1326246312072581160}"   # Jetson AI Lab Research Group
DBC_PROJECT="${DISCORD_BOT_CLI_PROJECT:-$HOME/git/discord-bot-cli}"

die() {
  printf 'error: %s\n' "$1" >&2
  [ -n "${2:-}" ] && printf 'hint: %s\n' "$2" >&2
  exit "${3:-1}"
}

# Resolve how to invoke discord-bot-cli once, into an array. The global install
# may lack the `[discord]` extra; the sibling checkout's venv has it.
if [ -n "${DISCORD_BOT_CLI:-}" ]; then
  read -r -a DBC <<<"$DISCORD_BOT_CLI"
elif [ -d "$DBC_PROJECT/.venv" ] && command -v uv >/dev/null 2>&1; then
  DBC=(uv run --project "$DBC_PROJECT" discord-bot-cli)
elif command -v discord-bot-cli >/dev/null 2>&1; then
  DBC=(discord-bot-cli)
else
  die "discord-bot-cli not found on PATH" \
    "install it, or set DISCORD_BOT_CLI / DISCORD_BOT_CLI_PROJECT" 2
fi

# Python in the same environment, for channels.py — it imports discord_bot_cli
# as a library to add the public/private flag the stock `channel list` omits.
if [ -d "$DBC_PROJECT/.venv" ] && command -v uv >/dev/null 2>&1; then
  DBC_PY=(uv run --project "$DBC_PROJECT" python)
else
  DBC_PY=(python3) # assumes discord_bot_cli is importable here
fi
HERE="$(cd "$(dirname "$0")" && pwd)"

require_token() {
  [ -n "${DISCORD_BOT_TOKEN:-}" ] ||
    die "DISCORD_BOT_TOKEN not set" "export DISCORD_BOT_TOKEN=<read-scoped bot token>" 2
}

cmd="${1:-help}"
shift || true

case "$cmd" in
help | -h | --help)
  # Print the leading doc comment (skip shebang + set), stop at first code line.
  awk 'NR<=2 {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' "$0"
  ;;

doctor)
  require_token
  # Capture stderr into a variable (no fixed /tmp file — race/symlink-safe).
  if err="$("${DBC[@]}" channel list "$GUILD_ID" --json 2>&1 >/dev/null)"; then
    printf '{"ok": true, "guild_id": "%s", "invocation": "%s"}\n' "$GUILD_ID" "${DBC[*]}"
  else
    die "cannot read guild $GUILD_ID" "$(printf '%s' "$err" | tr -d '\n')" 2
  fi
  ;;

channels)
  # Public channels only by default (each carries a "public": true flag); pass
  # --all to include private/role-gated channels too.
  require_token
  want_all=0
  for a in "$@"; do [ "$a" = "--all" ] && want_all=1; done
  if [ "$want_all" = 1 ]; then
    "${DBC_PY[@]}" "$HERE/channels.py"
  else
    "${DBC_PY[@]}" "$HERE/channels.py" --public-only
  fi
  ;;

read)
  require_token
  [ -n "${1:-}" ] || die "usage: scan.sh read <channel_id> [--limit N]" "" 1
  ch="$1"
  shift
  # Forward any extra flags (e.g. --limit 50) straight to the read verb; the
  # tool defaults to --limit 20 when none is given. This matches the documented
  # `read <channel_id> --limit N` form.
  "${DBC[@]}" channel messages "$ch" "$@" --json
  ;;

# Internal: fetch one channel's messages into <outdir>/<id>.json. Used by the
# parallel `active` pass via xargs. Tolerates per-channel read failures.
_fetch)
  id="$1"
  lim="$2"
  outdir="$3"
  "${DBC[@]}" channel messages "$id" --limit "$lim" --json >"$outdir/$id.json" 2>/dev/null ||
    printf '{"messages": []}' >"$outdir/$id.json"
  ;;

active)
  require_token
  SINCE=30
  LIMIT=30
  TOP=0
  PREVIEW=5
  PAR=6
  while [ $# -gt 0 ]; do
    case "$1" in
    --since | --limit | --top | --preview | --par)
      [ $# -ge 2 ] || die "flag $1 needs a value" "see: scan.sh help" 1
      case "$1" in
      --since) SINCE="$2" ;;
      --limit) LIMIT="$2" ;;
      --top) TOP="$2" ;;
      --preview) PREVIEW="$2" ;;
      --par) PAR="$2" ;;
      esac
      shift 2
      ;;
    *) die "unknown flag: $1" "see: scan.sh help" 1 ;;
    esac
  done

  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  # Public channels only — private/role-gated channels are never probed. The
  # stderr capture stays inside the per-run mktemp dir (no fixed /tmp path).
  "${DBC_PY[@]}" "$HERE/channels.py" --public-only >"$tmp/_channels.json" 2>"$tmp/_err" ||
    die "cannot read guild $GUILD_ID" "$(tr -d '\n' <"$tmp/_err")" 2

  # Public text channels only — categories/voice/forum carry no plain messages.
  python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
for c in d.get("channels", []):
    if c.get("type") == "text":
        print(c["id"])
' "$tmp/_channels.json" >"$tmp/_ids.txt"

  self="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
  printf 'probing %s text channels (limit %s, par %s) ...\n' \
    "$(wc -l <"$tmp/_ids.txt" | tr -d ' ')" "$LIMIT" "$PAR" >&2
  xargs -P "$PAR" -I{} bash "$self" _fetch {} "$LIMIT" "$tmp" <"$tmp/_ids.txt"

  GUILD_ID="$GUILD_ID" SINCE="$SINCE" LIMIT="$LIMIT" TOP="$TOP" PREVIEW="$PREVIEW" \
    python3 - "$tmp" <<'PY'
import json, os, sys
from datetime import datetime, timedelta, timezone

tmp = sys.argv[1]
since = int(os.environ["SINCE"])
limit = int(os.environ["LIMIT"])
top = int(os.environ["TOP"])
preview = int(os.environ["PREVIEW"])
cutoff = datetime.now(timezone.utc) - timedelta(days=since)

names = {c["id"]: c["name"]
         for c in json.load(open(os.path.join(tmp, "_channels.json")))["channels"]}

rows = []
probed = 0
for fn in os.listdir(tmp):
    if not fn.endswith(".json") or fn.startswith("_"):
        continue
    cid = fn[:-5]
    probed += 1
    msgs = json.load(open(os.path.join(tmp, fn))).get("messages", [])
    if not msgs:
        continue
    def ts(m):
        return datetime.fromisoformat(m["created_at"])
    msgs.sort(key=ts)
    newest = ts(msgs[-1])
    if newest < cutoff:
        continue                      # not active in the window
    in_window = [m for m in msgs if ts(m) >= cutoff]
    rows.append({
        "id": cid,
        "name": names.get(cid, "?"),
        "last_post": newest.isoformat(),
        "msgs_in_window": len(in_window),
        "saturated": len(in_window) == limit,   # >= limit posts in window; traffic underestimated
        "preview": [
            {"author": m["author"]["name"], "content": m["content"], "created_at": m["created_at"]}
            for m in msgs[-preview:]
        ],
    })

# Rank: most in-window traffic first, then most-recently posted.
rows.sort(key=lambda r: (r["msgs_in_window"], r["last_post"]), reverse=True)
if top > 0:
    rows = rows[:top]

json.dump({
    "guild_id": os.environ["GUILD_ID"],
    "since_days": since,
    "fetch_limit": limit,
    "probed_text_channels": probed,
    "active_channels": len(rows),
    "channels": rows,
}, sys.stdout, ensure_ascii=False, indent=2)
print()
PY
  ;;

*)
  die "unknown subcommand: $cmd" "see: scan.sh help" 1
  ;;
esac
