#!/usr/bin/env bash
set -euo pipefail

# Restart baobaobot — pidfile mechanism handles stopping the old instance.
exec uv run baobaobot "$@"
