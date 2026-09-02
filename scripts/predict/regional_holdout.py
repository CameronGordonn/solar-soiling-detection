"""Does the ML head survive being pointed at a region it has never seen?

WHY THIS AND NOT SPATIAL CV. `SpatialCVConfig` groups at **50 km** and assigns those
small clusters to folds at random, so a held-out cluster almost always has same-state
neighbours sitting in the training set. That is the right test for "does it generalise
past this station", and the wrong one for "does it generalise past this region". Under
the architecture we actually run -- an ML head that estimates regional climatology,
with physics channels layered on top for per-array detail -- portability to an unseen
region IS the head's job description, so it deserves a test shaped like the job.

Here a region is a k-means cluster on (lat, lon), hundreds of km across. Each region
is held out whole, the model trains on the rest, and every row is scored by a model
that never saw anywhere near it. Predictions are pooled across regions for one honest
AUC per feature set.

THE CONTRAST THIS EXISTS TO RESOLVE. `longitude` is the single highest-gain feature in
the production model (7.9%), and land cover carries 18.4%. Both plausibly work by
memorising *where* rather than learning *why*, which spatial CV at 50 km cannot detect
and which would show up here as a portability penalty. The two middle rows separate
them: same base, one adds location, the other adds land cover.

Bootstrap is over REGIONS, not rows: rows inside a region share weather and a handful
of stations, so resampling rows would report an interval that is far too narrow.

WHICH LABEL SET IT SCORES. `--matrix` and `--feats` default to the NREL training matrix
and the 40-feature production list. **Pass them explicitly when scoring anything else.**
Until 2026-08-31 both were hardcoded module constants, so pointing this script at a new
label set was impossible and an invocation that *looked* like a fleet run silently
re-scored NREL and returned the number already in the artifact. Every result now records
the matrix and feature list it actually read, in the output JSON, so a number can always
be traced back to what produced it.

Note that a fleet label file (e.g. `outputs/soiling/pvdaq_fleet_labels.json`) is NOT a
matrix. It has no engineered weather/AQ/static features, so it cannot be passed here
directly -- it has to be built into a feature matrix first. See
`docs/PVDAQ_LANE_HANDOFF_20260831.md` section 4.

Usage:
    PYTHONPATH=. conda run -n solar-soiling python scripts/predict/regional_holdout.py \
        --regions 8 --seeds 3 --out-json outputs/soiling/regional_holdout.json

    # scoring a different label set
    PYTHONPATH=. conda run -n solar-soiling python scripts/predict/regional_holdout.py \
        --matrix outputs/soiling/fleet_matrix.parquet \
        --feats  runs/soiling/<run>/feature_names.json \
        --regions 8 --seeds 3 --out-json outputs/soiling/regional_holdout_fleet.json
"""

from __future__ import annotations

import argparse
import json
import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

warnings.filterwarnings("ignore")

DEFAULT_MATRIX = Path("outputs/soiling/training_matrix.parquet")
DEFAULT_FEATS = Path("runs/soiling/run_optionb/feature_names.json")
MODEL_CFG = Path("configs/soiling/model.yaml")

REQUIRED_COLS = ("latitude", "longitude", "label", "station_id")


def group_of(name: str) -> str:
    if name.startswith(("somosclean", "kimber")):
        return "physics"
    if re.search(r"(temperature|humidity|wind|precip|rain|dew|cloud|shortwave|pm2_5|pm10|dry_day)", name):
        return "weather"
    if name in ("latitude", "longitude", "elevation_m"):
        return "location"
    if name.startswith("worldcover") or name == "nlcd_class" or "distance_to" in name:
        return "landcover"
    if name in ("tilt_deg", "azimuth_deg", "area_m2", "system_kw"):
        return "geometry"
    if name in ("month_of_year", "year", "age_years"):
        return "time"
    return "other"


def build_sets(feat: list[str]) -> dict[str, list[str]]:
    g = {c: group_of(c) for c in feat}
    def pick(*keep):
        return [c for c in feat if g[c] in keep]
    dead = [c for c in feat if c.startswith(("pm2_5", "pm10"))]
    trim = [c for c in feat if c not in dead + ["tilt_deg"]]
    wp = pick("weather", "physics")
    wpl = pick("weather", "physics", "location")
    wplc = pick("weather", "physics", "landcover")
    # Counts are derived, never hardcoded: a 35-feature list labelled "(40)" is a lie the
    # reader cannot catch, and these names are the keys the output JSON is written under.
    return {
        f"production ({len(feat)})":        feat,
        f"trim: -tilt -deadAQ ({len(trim)})": trim,
        f"weather+physics ({len(wp)})":     wp,
        f"  +location ({len(wpl)})":        wpl,
        f"  +landcover ({len(wplc)})":      wplc,
    }


def regions(lats, lons, k, seed=0):
    from sklearn.cluster import KMeans
    xy = np.column_stack([lats, np.asarray(lons) * np.cos(np.radians(np.asarray(lats)))])
    return KMeans(n_clusters=k, n_init=10, random_state=seed).fit_predict(xy)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="binary", choices=("binary", "loss"),
                    help="binary: classify at the iwsr_risk_threshold and score AUC "
                         "(the historical gate). loss: predict annual soiling-loss "
                         "PERCENT and score Spearman rank correlation + MAE. Prefer "
                         "'loss'. See the note in this file's docstring on why the "
                         "threshold cannot travel between climates.")
    ap.add_argument("--regions", type=int, default=8)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--out-json", type=Path, default=None)
    ap.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX,
                    help="feature matrix parquet to score (default: the NREL training matrix)")
    ap.add_argument("--feats", type=Path, default=DEFAULT_FEATS,
                    help="JSON list of feature names (default: the 40-feature production list)")
    args = ap.parse_args()

    from sklearn.metrics import roc_auc_score, mean_absolute_error
    from scipy.stats import spearmanr
    import xgboost as xgb

    for pth, what in ((args.feats, "feature list"), (args.matrix, "matrix")):
        if not pth.exists():
            raise SystemExit(f"ERROR: {what} not found: {pth}")

    feat = json.load(open(args.feats))
    try:
        f = pd.read_parquet(args.matrix)
    except Exception as e:
        hint = ""
        if args.matrix.suffix != ".parquet":
            hint = (f"\n{args.matrix.name} is not a .parquet file. If you meant a label file "
                    "such as outputs/soiling/pvdaq_fleet_labels.json, note that labels are not "
                    "a feature matrix -- they carry no engineered weather/AQ/static columns and "
                    "have to be built into one first. See docs/PVDAQ_LANE_HANDOFF_20260831.md "
                    "section 4.")
        raise SystemExit(f"ERROR: could not read matrix {args.matrix}: "
                         f"{type(e).__name__}{hint}")
    cfg = yaml.safe_load(open(MODEL_CFG))
    p0 = cfg.get("xgb_params", cfg.get("model", {}))

    # Fail loudly rather than scoring the wrong thing. A missing feature column used to
    # surface as a KeyError deep in the loop; a missing `label` or `latitude` would not
    # surface at all until the numbers looked odd.
    missing_req = [c for c in REQUIRED_COLS if c not in f.columns]
    if missing_req:
        raise SystemExit(f"ERROR: {args.matrix} is missing required column(s): "
                         f"{', '.join(missing_req)}. This does not look like a feature matrix.")
    missing_feat = [c for c in feat if c not in f.columns]
    if missing_feat:
        raise SystemExit(
            f"ERROR: {len(missing_feat)} of {len(feat)} features in {args.feats} are absent "
            f"from {args.matrix}: {', '.join(missing_feat[:8])}"
            f"{' ...' if len(missing_feat) > 8 else ''}\n"
            "Scoring would silently median-fill them. Build the matrix with these features, "
            "or pass the --feats list that matches this matrix.")

    print(f"matrix {args.matrix}  ({len(f)} rows)")
    print(f"feats  {args.feats}  ({len(feat)} features)")

    X = f[feat].apply(pd.to_numeric, errors="coerce")
    y = f["label"].values.astype(int)
    # Annual soiling loss in points. The matrix stores IWSR, so this inverts it.
    y_loss = (1.0 - pd.to_numeric(f["iwsr"], errors="coerce").values) * 100.0
    reg = regions(f.latitude.values, f.longitude.values, args.regions)
    f = f.assign(_region=reg)

    sizes = pd.Series(reg).value_counts().sort_index()
    print(f"rows {len(f)}   stations {f.station_id.nunique()}   regions {args.regions}")
    print("region sizes: " + ", ".join(f"r{i}={n}" for i, n in sizes.items()))
    if args.target == "loss":
        # A region is scorable if its losses VARY. Classification needed both classes
        # present, which is a far harsher filter in wet climates and threw away the
        # regions this experiment most wants to look at.
        usable = [r for r in sizes.index
                  if np.nanstd(y_loss[reg == r]) > 1e-6 and (reg == r).sum() >= 5]
        print(f"regions with variable loss (scorable): {len(usable)} of {args.regions}\n")
    else:
        usable = [r for r in sizes.index if len(np.unique(y[reg == r])) == 2]
        print(f"regions with both classes present (scorable): {len(usable)} of {args.regions}\n")

    sets = build_sets(feat)
    results, oof = {}, {}
    for name, cols in sets.items():
        pooled = np.full(len(f), np.nan)
        per_region = {}
        for r in usable:
            te = reg == r
            tr = ~te
            preds = np.zeros(te.sum())
            for s in range(args.seeds):
                p = dict(p0); p["random_state"] = 42 + 97 * s
                if args.target == "loss":
                    m = xgb.XGBRegressor(
                        **{k: v for k, v in p.items() if k != "objective"})
                    m.fit(X.loc[tr, cols], y_loss[tr])
                    preds += m.predict(X.loc[te, cols])
                else:
                    m = xgb.XGBClassifier(
                        **{k: v for k, v in p.items() if k != "objective"},
                        eval_metric="logloss")
                    m.fit(X.loc[tr, cols], y[tr])
                    preds += m.predict_proba(X.loc[te, cols])[:, 1]
            preds /= args.seeds
            pooled[te] = preds
            if args.target == "loss":
                # Spearman, not AUC, and this is the point of the mode. Rank
                # correlation asks "does it order these roofs correctly", which stays
                # answerable in a region where every roof is below the classification
                # threshold -- exactly the eastern case, where the base rate is 2.1%
                # and AUC is computed on ~4 positives or refuses outright.
                rho = spearmanr(y_loss[te], preds).correlation
                per_region[int(r)] = float(rho) if np.isfinite(rho) else float("nan")
            else:
                per_region[int(r)] = float(roc_auc_score(y[te], preds))
        ok = np.isfinite(pooled)
        if args.target == "loss":
            auc = float(spearmanr(y_loss[ok], pooled[ok]).correlation)
            mae = float(mean_absolute_error(y_loss[ok], pooled[ok]))
        else:
            auc = float(roc_auc_score(y[ok], pooled[ok]))
            mae = float("nan")
        oof[name] = (pooled, ok)
        results[name] = {"pooled_metric": auc, "mae_pts": mae,
                         "metric": "spearman" if args.target == "loss" else "auc",
                         "n_scored": int(ok.sum()), "n_features": len(cols),
                         "per_region": per_region}
        lab = "Spearman" if args.target == "loss" else "AUC"
        extra = f"  MAE {mae:.2f} pts" if args.target == "loss" else ""
        vals = [v for v in per_region.values() if np.isfinite(v)]
        print(f"{name:28s} n={len(cols):2d}  pooled out-of-region {lab} {auc:.4f}{extra}"
              f"   per-region {min(vals):.3f}-{max(vals):.3f}")

    # Cluster bootstrap over regions, paired against production.
    rng = np.random.default_rng(0)
    base = next(iter(sets))          # the full feature set, whatever its count
    print(f"\npaired vs {base}, bootstrap over regions (n={args.boot}):")
    for name in sets:
        if name == base:
            continue
        d = []
        for _ in range(args.boot):
            pick = rng.choice(usable, size=len(usable), replace=True)
            idx = np.concatenate([np.flatnonzero(reg == r) for r in pick])
            yy = y[idx]
            if len(np.unique(yy)) < 2:
                continue
            if args.target == "loss":
                a = spearmanr(y_loss[idx], oof[name][0][idx]).correlation
                b = spearmanr(y_loss[idx], oof[base][0][idx]).correlation
            else:
                a = roc_auc_score(yy, oof[name][0][idx])
                b = roc_auc_score(yy, oof[base][0][idx])
            if not (np.isfinite(a) and np.isfinite(b)):
                continue
            d.append(a - b)
        d = np.array(d)
        lo, hi = np.percentile(d, [2.5, 97.5])
        results[name]["delta_vs_production"] = float(results[name]["pooled_metric"]
                                                     - results[base]["pooled_metric"])
        results[name]["delta_ci95"] = [float(lo), float(hi)]
        flag = "" if lo <= 0 <= hi else "   <- interval excludes 0"
        print(f"  {name:28s} {d.mean():+.4f}   95% CI [{lo:+.4f}, {hi:+.4f}]{flag}")

    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        json.dump({"matrix": str(args.matrix),
                   "feats": str(args.feats),
                   "n_rows": int(len(f)),
                   "n_stations": int(f.station_id.nunique()),
                   "n_features": len(feat),
                   "n_regions": args.regions, "seeds": args.seeds,
                   "region_sizes": {int(k): int(v) for k, v in sizes.items()},
                   "results": results}, open(args.out_json, "w"), indent=2)
        print(f"\nwrote {args.out_json}")


if __name__ == "__main__":
    main()
