#!/usr/bin/env python3
"""Share of an AOI recommended to clean, as a function of the electricity rate.

Task 1 of the 2026-08-09 economics grounding pass. ``BASE_RATE`` was an unsourced flat
0.25 $/kWh; ``src/risk/rates.py`` replaces it with a sourced two-component model
(retail offset for self-consumed kWh, ACC export credit for exported kWh). This script
reports the consequence: **what fraction of the Santa Cruz AOI gets a "clean"
recommendation at each rate**, so the decision boundary is visible rather than implied.

Run:
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/rate_sensitivity.py
    ... --show-stack              # print the derived Santa Cruz tariff stack and exit
    ... --aoi santa-cruz-w2-21cm  # which risk.geojson to score
    ... --legacy-recovery         # use the retired 0.90 recovery, for the A/B

Outputs under --out-dir (default outputs/economics/rate_sensitivity/):
    rate_sensitivity.csv   one row per (rate, scenario) with clean-share and net-$ stats
    regime_summary.csv     the same at each billing regime's central value
    report.md              written findings
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

from risk import rates  # noqa: E402
from risk.economics import (  # noqa: E402
    BASE_SOILING_PCT, BASE_SUN, DEFAULT_SCENARIOS, LEGACY_SCENARIOS, M2_PER_KW,
    annual_loss_usd, array_recommendation, breakeven_soiling_pct, breakeven_system_kw,
    system_kw_from_area,
)


def show_stack() -> None:
    print("Santa Cruz volumetric rate stack (PG&E E-TOU-C delivery + 3CE generation)")
    print("  source: energy-advisor tariff specs, bill-reconciled to +/-$0.22/mo over 11 bills\n")
    print(f"  {'period':16s} {'$/kWh':>8s}  {'prod share':>10s}")
    for k, v in rates.TOU_TOTAL_USD_PER_KWH.items():
        print(f"  {k:16s} {v:8.5f}  {rates.PRODUCTION_SHARE[k]*100:9.1f}%")
    pk = rates.PRODUCTION_SHARE["summer_peak"] + rates.PRODUCTION_SHARE["winter_peak"]
    print(f"\n  share of PV output in the 4-9pm peak window: {pk*100:.1f}%")
    print(f"  production-weighted RETAIL offset : ${rates.RETAIL_OFFSET_USD_PER_KWH:.4f}/kWh")
    print(f"  production-weighted ACC EXPORT    : ${rates.ACC_EXPORT_PROD_WEIGHTED_USD_PER_KWH:.4f}/kWh"
          f"   (flat 8760 mean would be ${rates.ACC_EXPORT_FLAT_MEAN_USD_PER_KWH:.4f})")
    print(f"  EIA CA residential average        : ${rates.EIA_CA_RESIDENTIAL_AVG_USD_PER_KWH:.4f}/kWh"
          f"   (avg revenue/kWh, NOT a marginal rate)\n")
    print("  billing regimes:")
    for key, reg in rates.REGIMES.items():
        lo, c, hi = rates.regime_value_band(key)
        print(f"    {key:16s} sigma {reg.sigma:4.2f}  ->  ${c:.4f}/kWh  band [${lo:.4f}, ${hi:.4f}]")
        print(f"      {reg.note}")


def load_sites(aoi: str, use_parcels: bool, loss_col: str | None,
               risk_file: str = "risk.geojson"):
    """Return a list of (site_kw, loss_pct) for the AOI, clustered to sites."""
    import geopandas as gpd
    from risk.site_cluster import aggregate_to_sites, assign_sites, cluster_diagnostics

    path = REPO / f"outputs/aoi/{aoi}/{risk_file}"
    gdf = gpd.read_file(path)
    parcels = None
    if use_parcels:
        p = REPO / "data/external/santa_cruz_parcels/aoi_santa-cruz-outreach-v1.geojson"
        if p.is_file():
            parcels = gpd.read_file(p)
    gdf = assign_sites(gdf, parcels=parcels)
    diag = cluster_diagnostics(gdf)

    # Loss %: prefer a regression-head column, else the measured coastal-CA base case.
    # NOTE we deliberately do NOT fall back to risk_score * 8 — that is the category
    # error this pass removed.
    cols = tuple(c for c in ("loss_pct_p10", "loss_pct_p50", "loss_pct_p90") if c in gdf.columns)
    agg = aggregate_to_sites(gdf, loss_cols=cols or ())
    kws = [system_kw_from_area(a) or 0.0 for a in agg["area_m2"]]
    if loss_col and loss_col in agg.columns:
        losses = [float(v) for v in agg[loss_col]]
    else:
        losses = [BASE_SOILING_PCT] * len(agg)
    return list(zip(kws, losses)), diag


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show-stack", action="store_true")
    ap.add_argument("--aoi", default="santa-cruz-w2-21cm")
    ap.add_argument("--out-dir", type=Path, default=REPO / "outputs/economics/rate_sensitivity")
    ap.add_argument("--loss-col", default="loss_pct_p50")
    ap.add_argument("--no-parcels", action="store_true")
    ap.add_argument("--risk-file", default="risk.geojson",
                    help="filename under outputs/aoi/<aoi>/ (use risk_lossreg.geojson "
                         "for regression-head loss columns)")
    ap.add_argument("--legacy-recovery", action="store_true",
                    help="use the retired recovery_frac=0.90/0.70 for the A/B")
    ap.add_argument("--rate-min", type=float, default=0.02)
    ap.add_argument("--rate-max", type=float, default=0.70)
    ap.add_argument("--rate-step", type=float, default=0.02)
    args = ap.parse_args(argv)

    if args.show_stack:
        show_stack()
        return 0

    scens = LEGACY_SCENARIOS if args.legacy_recovery else DEFAULT_SCENARIOS
    sites, diag = load_sites(args.aoi, not args.no_parcels, args.loss_col, args.risk_file)
    sites = [(kw, lp) for kw, lp in sites if kw and kw > 0]
    print(f"[data] {args.aoi}: {diag['n_polygons']} polygons -> {diag['n_sites']} sites "
          f"(fragmentation {diag['fragmentation_factor']:.2f}x)")
    kws = sorted(kw for kw, _ in sites)
    print(f"[data] site kW: p10 {kws[len(kws)//10]:.2f}  p50 {kws[len(kws)//2]:.2f}  "
          f"p90 {kws[9*len(kws)//10]:.2f}")
    print(f"[data] recovery: pro {scens['professional']['recovery_frac']}, "
          f"rinse {scens['rinse_service']['recovery_frac']}"
          f"{'  (LEGACY)' if args.legacy_recovery else '  (measured)'}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    rate = args.rate_min
    curve = []
    while rate <= args.rate_max + 1e-9:
        n_clean = 0
        nets = []
        for kw, lp in sites:
            rec = array_recommendation(lp, kw, BASE_SUN, rate, scens)
            if rec["worth_cleaning"]:
                n_clean += 1
            nets.append(rec["expected_net_usd"])
        share = n_clean / len(sites) * 100.0
        curve.append((rate, share))
        rows.append({"rate_usd_per_kwh": round(rate, 4),
                     "n_sites": len(sites),
                     "n_recommended_clean": n_clean,
                     "pct_recommended_clean": round(share, 3),
                     "total_net_usd": round(sum(nets), 2),
                     "mean_net_usd": round(sum(nets) / len(nets), 2)})
        rate += args.rate_step

    with (out_dir / "rate_sensitivity.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print("\n  $/kWh   % of sites recommended to clean")
    for r, s in curve:
        bar = "#" * int(round(s / 2))
        print(f"  {r:5.2f}   {s:6.2f}%  {bar}")

    # Regime summary at the sourced values.
    reg_rows = []
    print("\n  billing regime            $/kWh    % clean")
    for key in rates.REGIMES:
        lo, c, hi = rates.regime_value_band(key)
        n_clean = sum(1 for kw, lp in sites
                      if array_recommendation(lp, kw, BASE_SUN, c, scens)["worth_cleaning"])
        share = n_clean / len(sites) * 100
        reg_rows.append({"regime": key, "rate_usd_per_kwh": round(c, 4),
                         "rate_lo": round(lo, 4), "rate_hi": round(hi, 4),
                         "pct_recommended_clean": round(share, 3)})
        print(f"  {key:22s} {c:8.4f}   {share:6.2f}%")
    with (out_dir / "regime_summary.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(reg_rows[0].keys()))
        w.writeheader()
        w.writerows(reg_rows)

    # What rate would be needed to flip the median site?
    flip = next((r for r, s in curve if s >= 50.0), None)
    flip_any = next((r for r, s in curve if s > 0.0), None)

    md = [
        "# Rate sensitivity — share of AOI recommended to clean vs $/kWh\n",
        f"_Generated {datetime.now(timezone.utc):%Y-%m-%d} · AOI `{args.aoi}` · "
        f"{diag['n_polygons']} polygons clustered to {diag['n_sites']} sites "
        f"({diag['fragmentation_factor']:.2f}x fragmentation)._\n",
        "## Sourced rate\n",
        f"- Production-weighted **retail offset**: **${rates.RETAIL_OFFSET_USD_PER_KWH:.4f}/kWh** "
        "(PG&E E-TOU-C delivery + 3CE generation + CCA adders + 8.5% Santa Cruz UUT, "
        "above-baseline marginal; bill-reconciled specs).",
        f"- Production-weighted **NBT export credit**: **${rates.ACC_EXPORT_PROD_WEIGHTED_USD_PER_KWH:.4f}/kWh** "
        f"(vs ${rates.ACC_EXPORT_FLAT_MEAN_USD_PER_KWH:.4f} unweighted — midday is when export is worth least).",
        f"- Only **{(rates.PRODUCTION_SHARE['summer_peak']+rates.PRODUCTION_SHARE['winter_peak'])*100:.1f}%** "
        "of PV output falls in the 4-9 p.m. peak window, so soiling losses are ~94% off-peak.",
        f"- The retired flat **$0.25/kWh** implies a midday self-consumption share of "
        f"**{(0.25 - rates.ACC_EXPORT_PROD_WEIGHTED_USD_PER_KWH)/(rates.RETAIL_OFFSET_USD_PER_KWH - rates.ACC_EXPORT_PROD_WEIGHTED_USD_PER_KWH):.2f}** — "
        "plausible for a battery home, too high for the no-battery majority.\n",
        "## Clean-share by billing regime\n",
        "| Regime | $/kWh | % of sites recommended to clean |",
        "|---|---:|---:|",
    ]
    for r in reg_rows:
        md.append(f"| {r['regime']} | {r['rate_usd_per_kwh']:.4f} | {r['pct_recommended_clean']:.2f}% |")
    md.append("")
    md.append("## Where the decision boundary sits\n")
    md.append(f"- Rate at which **any** site is recommended to clean: "
              f"**{f'${flip_any:.2f}/kWh' if flip_any else 'never in the swept range'}**.")
    md.append(f"- Rate at which the **median** site is recommended to clean: "
              f"**{f'${flip:.2f}/kWh' if flip else 'never in the swept range'}**.")
    md.append(f"- Highest CA retail rate available for comparison: "
              f"${max(rates.TOU_TOTAL_USD_PER_KWH.values()):.4f}/kWh (summer peak).\n")
    md.append("_Reproduce: `PYTHONPATH=. python scripts/analyze/rate_sensitivity.py`._\n")
    (out_dir / "report.md").write_text("\n".join(md), encoding="utf-8")

    (out_dir / "manifest.json").write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "aoi": args.aoi, "cluster_diagnostics": diag,
        "legacy_recovery": args.legacy_recovery,
        "recovery_frac": {k: v["recovery_frac"] for k, v in scens.items()},
        "m2_per_kw": M2_PER_KW,
        "retail_offset": rates.RETAIL_OFFSET_USD_PER_KWH,
        "export_credit": rates.ACC_EXPORT_PROD_WEIGHTED_USD_PER_KWH,
        "rate_to_flip_any_site": flip_any, "rate_to_flip_median_site": flip,
    }, indent=2), encoding="utf-8")

    print(f"\n[ok] -> {out_dir}")
    print(f"[result] any site cleans at {flip_any}; median site cleans at {flip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
