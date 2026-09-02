#!/usr/bin/env bash
# Relaunch fit shards whenever they finish, picking up newly-warmed cells.
# Lives in outputs/ rather than the scratchpad, because the scratchpad is cleared
# between sessions and took the previous copy of this script with it (trap 4).
cd /home/cameron/repos/solar-soiling-ml
LOG=outputs/soiling/fleet/_supervisor.log
echo $$ > outputs/soiling/fleet/supervisor.pid
# Count real python processes only. A pgrep -f on the script name matches any SHELL
# whose command line contains the pattern, including unrelated diagnostics, and a
# pkill -f on it killed the caller's own shell.
running() { ps -C python -o args= 2>/dev/null | grep -c "pvdaq_daily_srr_probe"; }
echo "SUPERVISOR START $(date)" >> $LOG
for round in $(seq 1 200); do
  if [ "$(running)" -gt 0 ]; then sleep 240; continue; fi
  echo "=== round $round launching $(date +%H:%M:%S) ===" >> $LOG
  PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/pvdaq_fleet_extract.py \
    --no-warm --launch --workers 3 >> $LOG 2>&1 || echo "LAUNCH FAILED" >> $LOG
  bash outputs/soiling/fleet/run_shards.sh >> $LOG 2>&1
  echo "--- round $round handed off at $(date +%H:%M:%S) ---" >> $LOG
  sleep 240
done
echo "SUPERVISOR FINISHED $(date)" >> $LOG
rm -f outputs/soiling/fleet/supervisor.pid
