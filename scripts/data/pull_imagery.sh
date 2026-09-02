#!/usr/bin/env bash
# Start / resume / check the Santa Cruz County imagery pull.
#
# Built for a laptop that gets closed. Suspend drops every socket, so the sweep will die
# partway through; this makes the recovery a single idempotent command rather than a
# remembered incantation:
#
#   scripts/data/pull_imagery.sh          # start, or resume if it died. Safe to run anytime.
#   scripts/data/pull_imagery.sh status   # how far along, without touching anything
#   scripts/data/pull_imagery.sh stop     # stop the running pull (progress is kept)
#   scripts/data/pull_imagery.sh 0.208    # same, but for the 21cm set
#
# Resume is by on-disk state, not a checkpoint file: a tile counts as done only if it opens
# as a raster at the expected pixel size, so a truncated file from a mid-write suspend is
# refetched rather than trusted. Running this twice concurrently is prevented by a lock.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"

GSD="${2:-${GSD:-0.0635}}"
case "${1:-start}" in
  0.*|.*) GSD="$1"; ACTION="start" ;;
  *)      ACTION="${1:-start}" ;;
esac

TAG="$(python3 -c "import sys; print('%dcm' % round(float(sys.argv[1]) * 100))" "$GSD")"
OUT_DIR="data/interim/scc_2025_${TAG}"
LOG="outputs/fetch_${TAG}.log"
LOCK="outputs/.fetch_${TAG}.lock"
SERVICE="https://sccgis.santacruzcountyca.gov/server/rest/services/Cache/Imagery_2025/MapServer/export"
TOTAL=249

mkdir -p outputs "$OUT_DIR"

count_done() { find "$OUT_DIR" -name "*_${TAG}_3857.tif" 2>/dev/null | wc -l | tr -d ' '; }
# A bare `kill -0 $pid` is not enough: WSL2 is torn down whenever Windows sleeps, and after
# the VM restarts the recorded pid can belong to an unrelated new process -- which would make
# `start` a permanent no-op and silently stall the pull. Verify the pid is actually ours.
is_running() {
  [ -f "$LOCK" ] || return 1
  local pid; pid="$(cat "$LOCK")"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null \
    && tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q fetch_scc_imagery
}

status() {
  local n; n="$(count_done)"
  if is_running; then
    echo "RUNNING (pid $(cat "$LOCK"))  ${n}/${TOTAL} tiles at ${TAG}"
  else
    echo "stopped               ${n}/${TOTAL} tiles at ${TAG}"
  fi
  [ -f "$LOG" ] && echo "--- last 3 log lines ---" && tail -3 "$LOG" || true
  [ "$n" -ge "$TOTAL" ] && echo "COMPLETE." || true
}

case "$ACTION" in
  status) status ;;
  stop)
    if is_running; then kill "$(cat "$LOCK")" && rm -f "$LOCK" && echo "stopped; progress kept"
    else echo "not running"; fi ;;
  start)
    if is_running; then echo "already running (pid $(cat "$LOCK")) -- nothing to do"; status; exit 0; fi
    rm -f "$LOCK"
    if [ "$(count_done)" -ge "$TOTAL" ]; then echo "already complete ($TOTAL/$TOTAL at $TAG)"; exit 0; fi
    echo "starting/resuming pull at ${TAG} ($(count_done)/${TOTAL} already on disk) -> $LOG"
    # setsid detaches from this terminal so closing the shell (or the IDE) does not kill it.
    setsid nohup conda run --no-capture-output -n solar-soiling \
      python scripts/data/fetch_scc_imagery.py \
        --all --native-gsd "$GSD" --service-url "$SERVICE" --out-dir "$OUT_DIR" \
      >> "$LOG" 2>&1 &
    echo $! > "$LOCK"
    sleep 2
    status ;;
  *) echo "usage: $0 [start|status|stop] [gsd_m]" >&2; exit 2 ;;
esac
