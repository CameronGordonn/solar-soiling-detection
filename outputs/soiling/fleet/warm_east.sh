#!/usr/bin/env bash
# East-first warm, paced. The eastern cells are the ones that make the regional
# experiment answerable; the western half already duplicates NREL's bias.
cd /home/cameron/repos/solar-soiling-ml
LOG=outputs/soiling/fleet/_warm.log
for pass in $(seq 1 30); do
  PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/pvdaq_fleet_extract.py \
    --warm-only --order east-first --max-cells 12 --sleep 20 >> $LOG 2>&1
  echo "=== east pass $pass done $(date +%H:%M:%S) ===" >> $LOG
  sleep 420
done
echo "EAST WARM FINISHED $(date)" >> $LOG
