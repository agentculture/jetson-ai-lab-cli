#!/usr/bin/env bash
# jetson-discord-scan — thin shim. The real implementation now lives in the
# jlab CLI: `jlab discord channels|read|active|doctor`. Read-only, public-only.
set -euo pipefail
exec uv run jlab discord "$@"
