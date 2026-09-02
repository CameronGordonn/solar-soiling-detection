---
description: Run a YOLOv11 training job. Pass overrides as arguments (e.g. --model yolov11m-seg.pt --epochs 100).
---

Run a training job on the solar panel detection dataset.

Default invocation (no args): single run with YOLOv11s, 50 epochs, small preset.

```bash
python scripts/detect/train.py --model models/yolo11s-seg.pt --epochs 50 $ARGUMENTS
```

If `--config` is passed in $ARGUMENTS, run the experiment matrix instead:
```bash
python scripts/detect/train_experiment_matrix.py $ARGUMENTS
```

After training completes:
1. Report the run directory (`runs/segment/train*/`)
2. Show final mAP50, precision, recall from the run's `results.csv`
3. Note the path to `best.pt`
4. Remind the user to run `/eval` to calibrate thresholds on the new weights
