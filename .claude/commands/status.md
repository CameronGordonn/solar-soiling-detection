---
description: Show current project status — recent commits, experiment results, dataset state, and what's next.
---

Give a concise project status report. Run these checks and summarize findings:

```bash
# Recent commits
git log --oneline -8

# Uncommitted changes
git status --short

# Latest experiment results (if any)
cat outputs/eval/experiment_results.csv 2>/dev/null | tail -5

# Training runs available
ls -lt runs/segment/ 2>/dev/null | head -10

# Dataset image counts
echo "Train:"; ls data/yolo/naip/images/train/ 2>/dev/null | wc -l
echo "Val:";   ls data/yolo/naip/images/val/   2>/dev/null | wc -l
echo "Test:";  ls data/yolo/naip/images/test/  2>/dev/null | wc -l

# Duke dataset (Phase 4)
ls data/yolo/duke/ 2>/dev/null && echo "Duke data present" || echo "Duke data not yet downloaded"
```

Summarize:
1. What was done recently (git log)
2. Best model performance found in results
3. Dataset counts (NAIP + Duke if present)
4. What's next based on docs/Q2_PLAN.md (the canonical status doc)
