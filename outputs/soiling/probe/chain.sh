#!/usr/bin/env bash
# Build the PVDAQ training matrix, then run the regional gate. Unattended, and it
# refuses to report a gate result it cannot stand behind.
cd /home/cameron/repos/solar-soiling-ml
LOG=outputs/soiling/_chain.log
MIN_ROWS=500
rows() { PYTHONPATH=. conda run -n solar-soiling python -c "
import pandas as pd
try: print(len(pd.read_parquet('outputs/soiling/pvdaq_training_matrix.parquet')))
except Exception: print(0)" 2>/dev/null | tail -1; }

echo "=== chain (v2) started $(date) ===" >> $LOG
# Many short passes rather than twelve. Open-Meteo's ceilings are hourly AND daily,
# so progress arrives in bursts whenever a window opens; the previous version ran a
# fixed twelve passes and then fired the gate on whatever it had, which at ~25 rows a
# pass would have meant scoring roughly 300 of 821 systems and reporting it as the
# answer. A thin matrix does not fail loudly, it just quietly answers the wrong
# question.
for pass in $(seq 1 200); do
  n=$(rows)
  [ "${n:-0}" -ge 780 ] && { echo "matrix complete: $n rows" >> $LOG; break; }
  echo "--- pass $pass  rows=$n  $(date +%H:%M) ---" >> $LOG
  PYTHONPATH=. conda run -n solar-soiling python scripts/predict/build_pvdaq_matrix.py \
    --checkpoint-every 25 >> $LOG 2>&1
  sleep 300
done

n=$(rows)
if [ "${n:-0}" -lt "$MIN_ROWS" ]; then
  echo "STOPPING: matrix has $n rows, under the $MIN_ROWS floor. Not running the gate." >> $LOG
  echo "A regional holdout on a partial matrix reports the sampling, not the model." >> $LOG
  exit 0
fi

echo "=== gate on $n rows $(date) ===" >> $LOG
for T in loss binary; do
  echo "--- target $T ---" >> $LOG
  PYTHONPATH=. conda run -n solar-soiling python scripts/predict/regional_holdout.py \
    --matrix outputs/soiling/pvdaq_training_matrix.parquet \
    --target $T --regions 8 --seeds 3 \
    --out-json outputs/soiling/regional_holdout_fleet_$T.json >> $LOG 2>&1
done
echo "=== chain done $(date) ===" >> $LOG
