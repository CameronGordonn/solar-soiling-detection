#!/usr/bin/env python3
"""Is a permanent soiling floor inside NREL's measured IWSR, or normalised out of it?

Follow-up to the 2026-08-09 economics grounding pass (docs/ECONOMICS_GROUNDING_20260809.md
§7, §11). The BBF front-end tools model a PERSISTENT soiling component — grime rain never
removes, 3%/yr, compounding with system age. SOMOSclean has no such term (precip >= 10mm
sets f=0, so the trajectory returns to exactly zero), yet it reproduces NREL's measured
coastal-CA annual loss. Either the permanent component is absorbed into the calibrated
sl_sat, or it is absent from both the model and the measurement.

This probe answers that from the NREL data itself, with no modelling argument:

  1. CENSORING vs OBSERVATION SPAN. If a permanent layer accumulated inside the measured
     quantity, no system observed for several years could report < 1% annual loss.
  2. WITHIN-STATION YEAR-OVER-YEAR TREND. A 3%/yr compounding term inside the measurement
     must show up as a ~+3 pts/yr within-station slope in loss_pct.
  3. UPPER BOUND. A soiling ratio normalised to the recovered state is bounded by 1.0.
  4. SAWTOOTH CLOSURE. Reconstruct annual loss from the reported monthly soiling RATES
     assuming full recovery to 1.0 at each reset. If the implied reset interval is
     physically plausible, the measurement closes with no floor term at all.

Then, because tests 1-4 can only show whether the permanent term is INSIDE the
measurement and not whether it exists at all, section 5 asks the question that actually
decides the product:

  5. HOW BIG WOULD IT HAVE TO BE. For every site in the Santa Cruz AOI, the percentage
     points of permanent, wash-only soiling needed before one professional clean nets
     positive — and, at a given accumulation rate, how old the system would have to be.
     Compared against the permit-derived age distribution of the AOI itself.

    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/persistent_soiling_probe.py

Sections 1-4 read data/external/nrel_soiling_map_raw.json + nrel_soiling_map_annual.csv
only: no network, no weather cache, no imagery. Section 5 additionally reads the cached
AOI risk file, parcels and permit registry, and skips itself if they are absent.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))
RAW = REPO / "data/external/nrel_soiling_map_raw.json"
PANEL = REPO / "data/external/nrel_soiling_map_annual.csv"

# The site's persistent term, for comparison (BBF-Website public/tools/dashboard.js).
YOY_PERSIST_PCT = 3.0

# Coastal-CA box used by scripts/predict/calibrate_somosclean_slsat.py --region coastal.
COASTAL = dict(lon_max=-121.0, lat_min=36.0, lat_max=39.0)


def _rule(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def load_raw() -> dict:
    with RAW.open() as fh:
        return json.load(fh)


def test_censoring(raw: dict) -> None:
    """Systems reporting < 1% annual loss, against how long they were observed."""
    _rule("1. Censored (< 1% annual loss) systems vs observation span")

    rows = []
    for sid, rec in raw.items():
        iwsr = rec.get("IWSR")
        months = rec.get("Months in data set")
        if months is None:
            continue
        rows.append({
            "station_id": sid,
            "censored": isinstance(iwsr, str) and str(iwsr).strip().startswith(">"),
            "months": float(months),
            "state": rec.get("State"),
        })
    df = pd.DataFrame(rows)
    cen = df[df["censored"]]

    print(f"  stations total                     {len(df)}")
    print(f"  reporting IWSR '> 0.99' (< 1% loss) {len(cen)}  ({len(cen)/len(df)*100:.1f}%)")
    print(f"  observation span of those, months  "
          f"p50 {cen['months'].median():.1f}  p90 {cen['months'].quantile(0.90):.1f}  "
          f"max {cen['months'].max():.1f}")
    for yrs in (2, 3, 5):
        n = int((cen["months"] >= yrs * 12).sum())
        print(f"    ... observed >= {yrs} year(s): {n:4d} stations")

    n5 = int((cen["months"] >= 60).sum())
    print(f"\n  A {YOY_PERSIST_PCT:.0f}%/yr permanent layer implies "
          f"{YOY_PERSIST_PCT * 4:.0f}% loss by year 5.")
    print(f"  {n5} stations were observed >= 5 years and still report < 1% TOTAL loss.")


def test_yoy_trend(raw: dict) -> pd.DataFrame:
    """Within-station year-over-year slope in measured loss."""
    _rule("2. Within-station year-over-year trend in measured loss (pts/yr)")

    panel = pd.read_csv(PANEL)
    panel = panel[~panel["iwsr_censored"].astype(bool)].copy()
    panel["loss_pct"] = (1.0 - panel["iwsr"]) * 100.0

    def slopes(df: pd.DataFrame, min_years: int = 3) -> tuple[np.ndarray, int]:
        out = []
        for _, g in df.groupby("station_id"):
            g = g.dropna(subset=["loss_pct", "year"])
            if g["year"].nunique() < min_years:
                continue
            out.append(np.polyfit(g["year"].to_numpy(float), g["loss_pct"].to_numpy(float), 1)[0])
        return np.array(out), len(out)

    def fixed_effects(df: pd.DataFrame) -> tuple[float, float, int]:
        """Pooled station-fixed-effects slope: demean both sides within station."""
        g = df.groupby("station_id")
        x = (df["year"] - g["year"].transform("mean")).to_numpy(float)
        y = (df["loss_pct"] - g["loss_pct"].transform("mean")).to_numpy(float)
        keep = np.isfinite(x) & np.isfinite(y)
        x, y = x[keep], y[keep]
        if (x**2).sum() == 0:
            return float("nan"), float("nan"), 0
        beta = float((x * y).sum() / (x**2).sum())
        resid = y - beta * x
        dof = max(1, len(x) - df["station_id"].nunique() - 1)
        se = float(np.sqrt((resid**2).sum() / dof / (x**2).sum()))
        return beta, se, len(x)

    coastal_ca = panel[
        (panel["longitude"] < COASTAL["lon_max"])
        & (panel["longitude"] > -124.5)
        & (panel["latitude"].between(COASTAL["lat_min"], COASTAL["lat_max"]))
    ]

    for label, df in (("all stations", panel), ("coastal CA", coastal_ca)):
        if df.empty:
            print(f"  {label}: no rows")
            continue
        s, n = slopes(df)
        beta, se, nobs = fixed_effects(df)
        print(f"\n  {label}  (n={len(df)} station-years, {df['station_id'].nunique()} stations)")
        print(f"    mean measured loss             {df['loss_pct'].mean():.2f}%  "
              f"p50 {df['loss_pct'].median():.2f}%  max {df['loss_pct'].max():.2f}%")
        if n:
            print(f"    per-station slope (>=3 yrs)    n={n}  "
                  f"mean {s.mean():+.3f}  median {np.median(s):+.3f} pts/yr")
            print(f"      share with a POSITIVE slope  {(s > 0).mean() * 100:.0f}%")
        print(f"    station-fixed-effects slope    {beta:+.3f} +/- {se:.3f} pts/yr "
              f"(n={nobs})")
        lo, hi = beta - 1.96 * se, beta + 1.96 * se
        print(f"      95% CI                       [{lo:+.3f}, {hi:+.3f}]")
        print(f"      site's persistent term is    {YOY_PERSIST_PCT:+.1f} pts/yr "
              f"-> {'INSIDE' if lo <= YOY_PERSIST_PCT <= hi else 'OUTSIDE'} the CI")
    return panel


def test_upper_bound(raw: dict, panel: pd.DataFrame) -> None:
    """Is the reported soiling ratio bounded above by 1.0?"""
    _rule("3. Is IWSR bounded above by 1.0 (i.e. normalised to the recovered state)?")

    numeric = [rec["IWSR"] for rec in raw.values() if isinstance(rec.get("IWSR"), (int, float))]
    annual = []
    for rec in raw.values():
        for e in (rec.get("Annual IWSR") or [])[1:]:
            if len(e) > 1 and isinstance(e[1], (int, float)):
                annual.append(e[1])
    print(f"  summary IWSR   n={len(numeric)}  max {max(numeric):.4f}  min {min(numeric):.4f}")
    print(f"  annual IWSR    n={len(annual)}  max {max(annual):.4f}  min {min(annual):.4f}")
    print(f"  values > 1.0:  summary {sum(v > 1.0 for v in numeric)}, "
          f"annual {sum(v > 1.0 for v in annual)}")
    print("\n  A ratio that never exceeds 1.0, with a '>0.99' censoring floor, is a ratio")
    print("  measured AGAINST THE CLEANEST OBSERVED STATE, not against factory output.")


def test_sawtooth(raw: dict) -> None:
    """Does the annual loss close from the monthly RATES with full recovery to 1.0?"""
    _rule("4. Sawtooth closure: implied reset interval assuming FULL recovery to 1.0")

    rows = []
    for sid, rec in raw.items():
        iwsr = rec.get("IWSR")
        if not isinstance(iwsr, (int, float)):
            continue
        monthly = rec.get("Monthly soiling rates")
        if not monthly or len(monthly) < 2:
            continue
        rates = [abs(float(e[1])) for e in monthly[1:]
                 if len(e) > 1 and isinstance(e[1], (int, float))]
        if not rates:
            continue
        rate = float(np.mean(rates))          # fraction/day lost while soiling
        loss = 1.0 - float(iwsr)              # mean fractional loss over the year
        if rate <= 0:
            continue
        # Mean of a linear sawtooth from 0 to rate*T is rate*T/2, so T = 2*loss/rate.
        rows.append({"station_id": sid, "state": rec.get("State"),
                     "rate_pct_per_day": rate * 100, "loss_pct": loss * 100,
                     "implied_interval_days": 2.0 * loss / rate})
    df = pd.DataFrame(rows)
    d = df["implied_interval_days"]
    print(f"  stations with both a rate and a numeric IWSR: {len(df)}")
    print(f"  mean |soiling rate|   p50 {df['rate_pct_per_day'].median():.4f} %/day")
    print(f"  implied reset interval (days between full recoveries):")
    for q in (10, 25, 50, 75, 90):
        print(f"    p{q:<3d} {d.quantile(q / 100):7.1f}")
    plausible = ((d >= 7) & (d <= 180)).mean() * 100
    print(f"  share landing in a physically plausible 7-180 day window: {plausible:.0f}%")
    print("\n  If annual loss closes from the soiling RATES alone under full recovery,")
    print("  the measurement needs no permanent floor term to explain its level.")


def _apn_base(s) -> str | None:
    """8-digit book-page-parcel base, matching scripts/analyze/join_permits_to_arrays.py."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    digits = "".join(ch for ch in str(s) if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else None


def _load_aoi_sites(aoi: str) -> pd.DataFrame | None:
    """Site-level (kW, loss_pct, apn) for the AOI, clustered exactly as §7/§8 did."""
    import geopandas as gpd

    from risk.economics import SYSTEM_DERATE, system_kw_from_area
    from risk.site_cluster import aggregate_to_sites, assign_sites

    risk_path = REPO / f"outputs/aoi/{aoi}/risk_lossreg.geojson"
    if not risk_path.is_file():
        return None
    gdf = gpd.read_file(risk_path)
    parcel_path = REPO / "data/external/santa_cruz_parcels/aoi_santa-cruz-outreach-v1.geojson"
    parcels = gpd.read_file(parcel_path) if parcel_path.is_file() else None
    gdf = assign_sites(gdf, parcels=parcels)
    agg = aggregate_to_sites(gdf, loss_cols=("loss_pct_p10", "loss_pct_p50", "loss_pct_p90"))
    agg["system_kw"] = [system_kw_from_area(a) or 0.0 for a in agg["area_m2"]]
    agg["apn_base"] = [
        _apn_base(str(s).split(":", 1)[1]) if str(s).startswith("apn:") else None
        for s in agg["site_id"]
    ]
    return agg[agg["system_kw"] > 0].reset_index(drop=True)


def _permit_years(sites: pd.DataFrame) -> pd.Series:
    """First PV permit year per site, from the Santa Cruz registry (2016+ only)."""
    path = REPO / "data/external/sc_solar_permits.csv"
    if not path.is_file():
        return pd.Series(dtype=float)
    p = pd.read_csv(path)
    p = p[p["kw"].notna()].copy()
    p["apn_base"] = p["apn"].map(_apn_base)
    first = p.dropna(subset=["apn_base"]).groupby("apn_base")["year"].min()
    return sites["apn_base"].map(first)


def test_flip_threshold(aoi: str = "santa-cruz-w2-21cm") -> None:
    """How much permanent, wash-only soiling would it take to flip 'zero of 1,865'?"""
    _rule("5. What a permanent term would have to be to change the decision")

    try:
        sites = _load_aoi_sites(aoi)
    except Exception as exc:  # pragma: no cover - environment-dependent
        print(f"  [skip] could not load AOI {aoi}: {exc}")
        return
    if sites is None or sites.empty:
        print(f"  [skip] no cached risk_lossreg.geojson for AOI {aoi}")
        return

    from risk import rates
    from risk.economics import (
        BASE_SUN, DAYS_PER_YEAR, DEFAULT_RECOVERY_PRO, professional_cost,
    )

    n_all = len(sites)
    finite = np.isfinite(pd.to_numeric(sites["loss_pct_p50"], errors="coerce").to_numpy(float))
    if not finite.all():
        print(f"  [note] {int((~finite).sum())}/{n_all} sites have no regression-head loss "
              f"estimate and are dropped")
        sites = sites[finite].reset_index(drop=True)

    n = len(sites)
    kw = sites["system_kw"].to_numpy(float)
    loss = sites["loss_pct_p50"].to_numpy(float)
    cost = np.array([professional_cost(k) for k in kw])
    print(f"  AOI {aoi}: {n} sites  |  kW p50 {np.median(kw):.2f}  "
          f"loss_pct_p50 p50 {np.median(loss):.2f}%")

    for regime in ("nbt_no_battery", "nem2_legacy"):
        if regime not in rates.REGIMES:
            continue
        _, rate, _ = rates.regime_value_band(regime)
        # $ recovered per percentage point of output restored for a full year.
        per_pt = kw * BASE_SUN * DAYS_PER_YEAR * SYSTEM_DERATE * 0.01 * rate
        seasonal = per_pt * loss * DEFAULT_RECOVERY_PRO
        # Permanent points needed on top, credited at FULL recovery for a FULL year —
        # the most generous accounting the annual model admits.
        p_be = (cost - seasonal) / per_pt

        print(f"\n  regime {regime}  (${rate:.4f}/kWh)")
        print(f"    seasonal recovery already credited: ${seasonal.mean():.2f}/site/yr "
              f"vs cost ${cost.mean():.2f}")
        print(f"    permanent pts needed to break even: "
              f"p10 {np.percentile(p_be, 10):.1f}  p50 {np.percentile(p_be, 50):.1f}  "
              f"p90 {np.percentile(p_be, 90):.1f}")
        print(f"      (for reference, the largest TOTAL annual soiling loss measured "
              f"anywhere in NREL's 891 rows is 22.9 pts)")

        # (A) A clean REPEATED every year only ever removes one year's accumulation, so
        #     in steady state the permanent channel is worth exactly p pts/yr.
        print(f"\n    (A) recurring annual clean — needs an accumulation rate of "
              f"{np.percentile(p_be, 50):.1f} pts/yr at the median site.")
        print(f"        the site's term is {YOY_PERSIST_PCT:.1f} pts/yr, so a repeated "
              f"clean "
              f"{'clears' if YOY_PERSIST_PCT >= np.percentile(p_be, 50) else 'never clears'} "
              f"on this channel.")

        # (B)/(C) The FIRST wash of a never-washed array is the different case: it removes
        #     everything accumulated since install. (B) credits one year of that; (C)
        #     credits the whole undiscounted stream until the layer regrows.
        for p_rate, src in ((YOY_PERSIST_PCT, "the site's unsourced term"),
                            (0.6, "Jordan 2016 median x-Si degradation, ALL of it washable"),
                            (0.375, "coastal-CA 95% upper bound from test 2")):
            age_b = 1.0 + p_be / p_rate           # site models p * (year - 1)
            age_c = 1.0 + np.sqrt(2.0 * p_be / p_rate)
            print(f"\n    first wash at {p_rate:4.2f} pts/yr ({src})")
            print(f"      (B) one-year credit    age needed  "
                  f"p10 {np.percentile(age_b, 10):5.1f}  p50 {np.percentile(age_b, 50):5.1f}  "
                  f"p90 {np.percentile(age_b, 90):5.1f} yrs | "
                  f"clear by age 10: {float((age_b <= 10).mean() * 100):5.1f}%")
            print(f"      (C) full regrowth      age needed  "
                  f"p10 {np.percentile(age_c, 10):5.1f}  p50 {np.percentile(age_c, 50):5.1f}  "
                  f"p90 {np.percentile(age_c, 90):5.1f} yrs | "
                  f"clear by age 10: {float((age_c <= 10).mean() * 100):5.1f}%")

    years = _permit_years(sites)
    matched = years.dropna()
    print(f"\n  AOI age, from the permit registry: {len(matched)}/{n} sites carry a "
          f"PV permit year (registry starts 2016)")
    if not matched.empty:
        age_now = 2026 - matched
        print(f"    system age p10 {age_now.quantile(0.10):.0f}  "
              f"p50 {age_now.quantile(0.50):.0f}  max {age_now.max():.0f} years")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aoi", default="santa-cruz-w2-21cm")
    ap.add_argument("--skip-aoi", action="store_true", help="run sections 1-4 only")
    args = ap.parse_args(argv)

    raw = load_raw()
    test_censoring(raw)
    panel = test_yoy_trend(raw)
    test_upper_bound(raw, panel)
    test_sawtooth(raw)
    if not args.skip_aoi:
        test_flip_threshold(args.aoi)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
