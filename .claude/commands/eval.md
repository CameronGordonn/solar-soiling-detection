---
description: Run threshold sweep to calibrate conf/iou, then evaluate on test split. Pass --weights <path> to specify weights (defaults to best.pt).
---

Calibrate detection thresholds and evaluate model performance.

Step 1 — Threshold sweep across confidence [0.1-0.9] × IoU [0.3-0.7]:
```bash
python scripts/detect/eval_threshold_sweep.py --weights runs/segment/train/weights/best.pt $ARGUMENTS
```

Step 2 — Full evaluation on held-out test split:
```bash
python scripts/detect/evaluate.py --weights runs/segment/train/weights/best.pt $ARGUMENTS
```

After evaluation:
1. Show best conf/iou combination from the sweep (highest F1)
2. Report mAP50, precision, recall on the test split
3. Compare against baseline: mAP50=0.563, P=0.53, R=0.56
4. Note progress toward 75% mAP50 target
5. Save results summary to `outputs/eval/`
