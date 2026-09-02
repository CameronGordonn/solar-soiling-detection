#!/usr/bin/env python3
"""Is the AOI's predicted soiling level right? Measure it against nearby PVDAQ roofs.

``outputs/aoi/.../econ_summary.json`` has carried this warning since 2026-08-12:

    LEVEL IS LIKELY BIASED HIGH AND THE CAUSE IS UNKNOWN. The AOI predicts ~5.53%
    annual soiling loss. The two nearest NREL stations read 4.00% (38 km) and
    3.10% (62 km) ... Treat the level as an upper bound until this is understood.

Two candidate explanations were tested in August and both failed (the worldcover
median-fill, and the PM2.5/PM10 median-fill, which moved p50 the *wrong* way). The
warning has stood unresolved since, and it matters more than it used to: the
2026-08-19 tariff join multiplied every published dollar figure by ~2.6, so the same
proportional bias is now worth 2.6x as many dollars on the public site.

WHAT MAKES THIS ANSWERABLE NOW. The PVDAQ extraction gives per-system annual soiling
loss for real monitored residential roofs at known coordinates, and the fleet run
covers 1,629 of them. Santa Cruz is not a gap in that coverage -- there are systems
inside the county. So the question stops being "our model against a station 38 km
away" and becomes "our model against measured roofs down the road".

WHY THE COMPARISON IS LEGITIMATE, since it is comparing two different instruments:

  * The model is trained on NREL's annual panel, i.e. ``(1 - iwsr) * 100``.
  * PVDAQ ``perfect_clean.loss_pct`` is the same quantity fitted per system.
  * ``pvdaq_phase2_vs_nrel.py`` established the two agree where they overlap:
    paired diff +0.16 pts, 95% CI [-0.62, +0.99], Wilcoxon p 0.684, n=24.

So a gap between the model's AOI prediction and nearby PVDAQ measurements is a
statement about the MODEL, not about the instruments disagreeing.

THE CAVEAT THAT TRAVELS WITH IT, and it must: 881 of NREL's 891 rows came from this
same class of method, so Phase 2 is a reproduction check rather than independent
validation. Both numbers here inherit whatever that method gets wrong. What this
CAN detect is the model predicting a different level from what the method measures
nearby, which is exactly the open question.

Usage:
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/aoi_level_check.py
    ... --max-km 50          # tighten the comparison radius
    ... --out-json outputs/soiling/aoi_level_check.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

#: AOI centroid (Santa Cruz). Same constant as risk.rates.SITE_LAT/SITE_LON.
AOI_LAT, AOI_LON = 36.97, -122.03

SYSTEMS_CSV = "https://oedi-data-lake.s3.amazonaws.com/pvdaq/csv/systems_20250729.csv"
SYSTEMS_CACHE = REPO / ".cache/soiling/pvdaq_systems.csv"

#: Every file that may carry a fitted label, newest last so later runs win. The
#: fleet shards are included so this is answerable while the fleet run is still
#: going -- the nearby systems are scored long before the run completes.
LABEL_SOURCES = [
    "outputs/soiling/pvdaq_0a_csb12.json",
    "outputs/soiling/pvdaq_phase2_nearstation_gamma.json",
    "outputs/soiling/pvdaq_0a_withincell75_gamma.json",
    "outputs/soiling/pvdaq_fleet_labels.json",
]
FLEET_GLOB = "outputs/soiling/fleet/shard*.json"

#: Distance bands, km. The first is "in the county" in any useful sense.
BANDS = [(0, 25), (25, 50), (50, 120), (120, 300)]


def _haversine_km(lat, lon, lat2=AOI_LAT, lon2=AOI_LON):
    import numpy as np
    R = 6371.0
    p = np.radians
    a = (np.sin(p(lat2 - lat) / 2) ** 2
         + np.cos(p(lat)) * np.cos(p(lat2)) * np.sin(p(lon2 - lon) / 2) ** 2)
    return 2 * R * np.arcsin(np.sqrt(a))


def _load_catalogue():
    import pandas as pd
    if SYSTEMS_CACHE.is_file():
        return pd.read_csv(SYSTEMS_CACHE)
    SYSTEMS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(SYSTEMS_CSV)
    df.to_csv(SYSTEMS_CACHE, index=False)
    print(f"[cache] wrote {SYSTEMS_CACHE}")
    return df


def _records(obj):
    """Yield fit records from any of the shapes these files have taken."""
    if isinstance(obj, list):
        for x in obj:
            if isinstance(x, dict):
                yield x
    elif isinstance(obj, dict):
        for key in ("results", "systems", "rows"):
            if isinstance(obj.get(key), list):
                yield from (x for x in obj[key] if isinstance(x, dict))
                return


def _load_labels(min_valid):
    """system_id -> (loss_pct, n_valid, climate, kw, source). Later files win."""
    from scripts.analyze.pvdaq_daily_srr_probe import MIN_VALID_INTERVALS  # noqa: F401
    paths = [REPO / p for p in LABEL_SOURCES] + sorted(REPO.glob(FLEET_GLOB))
    out, skipped = {}, {"degenerate": 0, "low_n_valid": 0, "no_fit": 0}
    for path in paths:
        if not path.is_file():
            continue
        try:
            obj = json.loads(path.read_text())
        except (ValueError, OSError):
            continue  # a shard caught mid-write; it will be there next run
        for r in _records(obj):
            meta = r.get("meta") or {}
            sid = meta.get("system_id", r.get("system_id"))
            pc = ((r.get("fits") or {}).get("energy") or {}).get("perfect_clean") or {}
            if sid is None or "loss_pct" not in pc:
                skipped["no_fit"] += 1
                continue
            if pc.get("degenerate"):
                skipped["degenerate"] += 1
                continue
            n_valid = pc.get("n_valid_intervals", 0)
            if n_valid < min_valid:
                skipped["low_n_valid"] += 1
                continue
            out[int(sid)] = dict(
                loss_pct=float(pc["loss_pct"]), n_valid=int(n_valid),
                climate=meta.get("climate"), kw=meta.get("capacity_kw"),
                location=meta.get("location"), source=path.name,
            )
    return out, skipped


def main(argv=None) -> int:
    import numpy as np
    import pandas as pd
    from scripts.analyze.pvdaq_daily_srr_probe import MIN_VALID_INTERVALS

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aoi", default="santa-cruz-w2-21cm")
    ap.add_argument("--max-km", type=float, default=120.0,
                    help="headline comparison radius (default 120)")
    ap.add_argument("--climate", default="Csb",
                    help="restrict to this Koppen class; '' for no restriction")
    ap.add_argument("--min-valid", type=int, default=MIN_VALID_INTERVALS)
    ap.add_argument("--out-json", type=Path,
                    default=REPO / "outputs/soiling/aoi_level_check.json")
    args = ap.parse_args(argv)

    # ── what the model says about the AOI ─────────────────────────────────────
    site_csv = REPO / "outputs/aoi" / args.aoi / "site_economics.csv"
    if not site_csv.is_file():
        print(f"[fatal] {site_csv} missing — run rebuild_aoi_economics.py first")
        return 1
    sites = pd.read_csv(site_csv)
    pred = sites["loss_pct_p50"]
    model = dict(n_sites=int(len(sites)), p10=float(pred.quantile(.10)),
                 p50=float(pred.median()), p90=float(pred.quantile(.90)))

    # ── what nearby roofs actually measure ────────────────────────────────────
    labels, skipped = _load_labels(args.min_valid)
    if not labels:
        print("[fatal] no QC-passing PVDAQ labels found — has the extraction run?")
        return 1

    cat = _load_catalogue().dropna(subset=["latitude", "longitude"])
    cat["km"] = _haversine_km(cat["latitude"].values, cat["longitude"].values)
    km = dict(zip(cat["system_id"].astype(int), cat["km"]))

    rows = [dict(system_id=sid, km=km.get(sid), **v) for sid, v in labels.items()]
    d = pd.DataFrame([r for r in rows if r["km"] is not None])
    if args.climate:
        d = d[d["climate"] == args.climate]

    print(f"AOI model prediction   p10 {model['p10']:.2f}%   p50 {model['p50']:.2f}%"
          f"   p90 {model['p90']:.2f}%   (n={model['n_sites']} sites)")
    print(f"PVDAQ labels           {len(labels)} QC-passing"
          f"   ({args.climate or 'all climates'}: {len(d)})")
    print(f"  skipped: {skipped['degenerate']} degenerate, "
          f"{skipped['low_n_valid']} under n_valid>={args.min_valid}, "
          f"{skipped['no_fit']} with no energy fit\n")

    print("nearest measured roofs")
    near = d.nsmallest(10, "km")
    print(near[["system_id", "km", "loss_pct", "n_valid", "kw", "location"]]
          .round(2).to_string(index=False))

    bands = []
    print("\nby distance from the AOI centroid")
    print(f"{'band':>12} {'n':>4} {'median':>8} {'mean':>7} {'p10':>6} {'p90':>6}"
          f"  {'model/measured':>15}")
    for lo, hi in BANDS:
        v = d[(d["km"] >= lo) & (d["km"] < hi)]["loss_pct"]
        if v.empty:
            continue
        ratio = model["p50"] / float(v.median()) if float(v.median()) > 0 else float("nan")
        bands.append(dict(lo_km=lo, hi_km=hi, n=int(len(v)), median=float(v.median()),
                          mean=float(v.mean()), p10=float(v.quantile(.1)),
                          p90=float(v.quantile(.9)), model_over_measured=float(ratio)))
        print(f"{f'{lo}-{hi} km':>12} {len(v):>4} {v.median():>8.2f} {v.mean():>7.2f}"
              f" {v.quantile(.1):>6.2f} {v.quantile(.9):>6.2f}  {ratio:>14.2f}x")

    head = d[d["km"] <= args.max_km]["loss_pct"]
    verdict = None
    if not head.empty:
        ratio = model["p50"] / float(head.median())
        verdict = dict(max_km=args.max_km, n=int(len(head)),
                       measured_median=float(head.median()),
                       model_p50=model["p50"], model_over_measured=float(ratio))
        print(f"\nHEADLINE  within {args.max_km:.0f} km: n={len(head)}, "
              f"measured median {head.median():.2f}%, model {model['p50']:.2f}%"
              f"  ->  model is {ratio:.2f}x the measured level")
        # The dollar consequence, which is the reason this matters at all.
        print(f"          every published dollar figure is therefore ~{ratio:.2f}x "
              f"too high, if the measured level is right.")

    out = dict(aoi=args.aoi, climate=args.climate or None,
               min_valid_intervals=args.min_valid,
               model=model, bands=bands, headline=verdict,
               n_labels_qc_pass=len(labels), n_in_climate=int(len(d)),
               skipped=skipped,
               caveat=("PVDAQ and NREL agree where they overlap (+0.16 pts, 95% CI "
                       "[-0.62,+0.99], n=24) but 881 of NREL's 891 rows came from this "
                       "same class of method, so both inherit its systematic error. "
                       "This detects the MODEL predicting a different level from what "
                       "the method measures nearby; it is not independent validation "
                       "of the method itself."))
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(out, indent=2))
    print(f"\n[ok] -> {args.out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
