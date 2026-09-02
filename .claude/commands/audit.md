---
description: Audit the YOLO dataset labels for polygon integrity and outliers.
---

Run the dataset audit to validate label quality before training.

```bash
python scripts/data/audit_dataset.py --rules configs/yolo/dataset_audit.yaml $ARGUMENTS
```

After the audit:
1. Summarize any flagged issues (degenerate polygons, outlier sizes, missing labels)
2. Report counts: total images, total labeled objects, flagged files
3. If issues found, suggest remediation (re-label in Roboflow, delete bad tiles, etc.)
4. If clean, confirm dataset is ready for training
