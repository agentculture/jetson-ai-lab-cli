#!/usr/bin/env bash
set -euo pipefail
# jetson-discord-scan — thin shim. The real implementation now lives in the
# jlab CLI: `jlab discord channels|read|active|doctor`. Read-only, public-only.
exec uv run jlab discord "$@"
