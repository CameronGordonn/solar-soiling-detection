#!/usr/bin/env bash
# Unattended finisher. Waits for the fits to drain, then merges, summarises,
# preserves and documents the label set. Does NOT run the regional-holdout gate:
# that needs a training matrix (weather aggregates + static features per row) which
# does not exist for PVDAQ labels and is itself a quota-bound build. See §4 of
# docs/PVDAQ_LANE_HANDOFF_20260831.md.
set -u
cd /home/cameron/repos/solar-soiling-ml
LOG=outputs/soiling/fleet/_finish.log
echo "=== finisher started $(date) ===" >> $LOG

running() { ps -C python -o args= 2>/dev/null | grep -c "pvdaq_daily_srr_probe"; }

# Drain: idle means no workers AND no new results for two consecutive checks.
# Polls rather than assuming a duration, so a suspended laptop just makes it wait.
prev=-1; idle=0
for i in $(seq 1 480); do
  n=$(ls outputs/soiling/fleet/shard*.json 2>/dev/null | xargs -r grep -ho '"system_id"' 2>/dev/null | wc -l)
  if [ "$(running)" -eq 0 ] && [ "$n" -eq "$prev" ]; then idle=$((idle+1)); else idle=0; fi
  prev=$n
  if [ "$idle" -ge 3 ]; then echo "drained after $i checks ($n records)" >> $LOG; break; fi
  sleep 120
done

# Stop the loops so nothing rewrites the outputs mid-merge.
for p in outputs/soiling/fleet/supervisor.pid; do
  [ -f "$p" ] && kill "$(cat $p)" 2>/dev/null && echo "stopped $(cat $p)" >> $LOG
done
pkill -x sleep 2>/dev/null
sleep 5

PYTHONPATH=. conda run -n solar-soiling python \
  scripts/analyze/pvdaq_fleet_extract.py --merge >> $LOG 2>&1

# A FILE, not a heredoc. `conda run ... python - <<PY` writes nothing and reports
# success (trap 1) -- which is exactly how the first unattended run merged and
# uploaded correctly while silently producing no summary at all.
PYTHONPATH=. conda run -n solar-soiling python \
  scripts/analyze/pvdaq_fleet_summary.py >> $LOG 2>&1

# Preserve: the irradiance cache is days of rate-limited quota, so the asset is
# rebuilt and re-uploaded rather than left on this laptop.
mkdir -p /tmp/handoff-build
PYTHONPATH=. conda run -n solar-soiling python scripts/data/build_handoff_bundle.py \
  --groups pvdaq --tar /tmp/handoff-build >> $LOG 2>&1
gh release upload handoff-v1 /tmp/handoff-build/handoff-pvdaq.tar --clobber \
  --repo Better-Behavior-Foundation/solar-soiling-ml >> $LOG 2>&1 \
  && echo "asset uploaded $(date)" >> $LOG || echo "UPLOAD FAILED" >> $LOG

echo "=== finisher done $(date) ===" >> $LOG
