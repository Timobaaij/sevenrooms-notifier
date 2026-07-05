#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# run-loop.sh — the every-minute watcher loop.
#
# Runs main.py, waits POLL_INTERVAL seconds, repeats — forever. This is what
# gives you a *real* one-minute cadence: unlike GitHub's cron (delayed 15-20
# min), this is an always-on process that never drifts.
#
# Launched by the systemd service (see setup.sh). Environment (secrets,
# POLL_INTERVAL, PYTHON, STATE_PATH, CONFIG_PATH) is supplied by systemd.
# ---------------------------------------------------------------------------
set -uo pipefail

# Work from the repo root regardless of where systemd invokes us.
cd "$(dirname "$0")/.." || exit 1

PYTHON="${PYTHON:-python3}"
INTERVAL="${POLL_INTERVAL:-60}"

echo "maitre-notifier: starting loop — polling every ${INTERVAL}s with ${PYTHON}"

while true; do
    # Pick up any searches you edited in the web UI (which commits config.json
    # to GitHub). Best-effort: ignored if the checkout is offline or unauth'd.
    git pull --ff-only --quiet 2>/dev/null || true

    if ! "$PYTHON" main.py; then
        echo "maitre-notifier: poll failed at $(date -u +%FT%TZ) (will retry)"
    fi

    sleep "$INTERVAL"
done
