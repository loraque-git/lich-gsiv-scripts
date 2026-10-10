#!/usr/bin/env bash
# Watch live Lich scripts/tools and debounce into sync-from-live.sh.
set -euo pipefail

LIVE_SCRIPTS="${LIVE_SCRIPTS:-/home/loraque/Lich5/scripts}"
LIVE_TOOLS="${LIVE_TOOLS:-/home/loraque/Lich5/tools/saga-log-summary}"
REPO="${REPO:-/home/loraque/Projects/lich-gsiv-scripts}"
SYNC="${SYNC:-$REPO/tools/sync-from-live.sh}"
DEBOUNCE_SEC="${DEBOUNCE_SEC:-8}"
PENDING="/tmp/lich-gsiv-sync.pending"

rm -f "$PENDING"

watch_loop() {
  inotifywait -m -q \
    -e close_write,create,delete,move \
    --format '%w%f' \
    "$LIVE_SCRIPTS" \
    "$LIVE_TOOLS" \
    | while IFS= read -r path; do
      case "$path" in
        *__pycache__*|*.pyc|*Copy*|*'(1)'*|*DESKTOP*|*HAL_5000*) continue ;;
      esac
      date -Is >"$PENDING"
    done
}

debounce_loop() {
  local stamp
  while true; do
    sleep "$DEBOUNCE_SEC"
    [[ -f "$PENDING" ]] || continue
    stamp=$(cat "$PENDING" 2>/dev/null || true)
    rm -f "$PENDING"
    # If more events arrived during sleep, PENDING will be recreated; next loop catches them.
    echo "lich-gsiv-sync-watch: sync (event at ${stamp:-unknown})"
    "$SYNC" || echo "lich-gsiv-sync-watch: sync exited $?" >&2
  done
}

echo "lich-gsiv-sync-watch: watching $LIVE_SCRIPTS and $LIVE_TOOLS (debounce ${DEBOUNCE_SEC}s)"
watch_loop &
WATCH_PID=$!
trap 'kill "$WATCH_PID" 2>/dev/null || true' EXIT
debounce_loop
