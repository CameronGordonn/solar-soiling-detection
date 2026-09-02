import json, sys
import numpy as np
from pathlib import Path
ROOT = Path('/home/cameron/repos/solar-soiling-ml')
sys.path.insert(0, str(ROOT))
from scripts.detect.eval_tile_f1 import load_split, per_tile_counts

_, gts = load_split('test', ROOT/'data/interim/scc21_labelset/images', ROOT/'data/yolo/scc21/tile_labels')
w1 = json.loads((ROOT/'outputs/eval/rfdetr_w1_anchor/detections.json').read_text())['test']
w2 = json.loads((ROOT/'outputs/eval/rfdetr_w2/detections.json').read_text())['test']
c1, c2 = per_tile_counts(w1, gts, 0.40), per_tile_counts(w2, gts, 0.50)
keys = sorted(gts)
a1 = np.array([c1[k] for k in keys], float)
a2 = np.array([c2[k] for k in keys], float)

def f1(s):
    tp, fp, fn = s; d = 2*tp + fp + fn
    return 2*tp/d if d > 0 else 0.0

def rec(s):
    tp, fp, fn = s
    return tp/(tp+fn) if tp+fn else 0.0

lines = [f'W1 F1 {f1(a1.sum(0)):.4f}   W2 F1 {f1(a2.sum(0)):.4f}   diff {f1(a2.sum(0))-f1(a1.sum(0)):+.4f}']
rng = np.random.default_rng(42)
idx = rng.integers(0, len(keys), size=(10000, len(keys)))
d = np.array([f1(a2[i].sum(0)) - f1(a1[i].sum(0)) for i in idx])
dr = np.array([rec(a2[i].sum(0)) - rec(a1[i].sum(0)) for i in idx])
lines.append(f'PAIRED 95% CI on F1 diff: [{np.percentile(d,2.5):+.4f}, {np.percentile(d,97.5):+.4f}]  P(W2>W1)={(d>0).mean():.3f}')
lines.append(f'recall diff {rec(a2.sum(0))-rec(a1.sum(0)):+.4f}  CI [{np.percentile(dr,2.5):+.4f}, {np.percentile(dr,97.5):+.4f}]  P>0={(dr>0).mean():.3f}')
(ROOT/'outputs/eval/rfdetr_w2/paired_vs_w1.txt').write_text('\n'.join(lines) + '\n')
print('\n'.join(lines))
