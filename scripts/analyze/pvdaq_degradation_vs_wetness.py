"""Does biological soiling show up as APPARENT DEGRADATION across the PVDAQ fleet?

The scalable, free test of the moss thesis, and the one route that does not depend
on the ground survey.

The argument. `soiling_srr` with `recenter=True` is blind to a standing layer
present from day one (see `docs/PVDAQ_PRODUCT_RELEVANCE_20260826.md` section 2).
But biofilm is not present from day one: Shirakawa et al. (2015, Sao Paulo)
measured coverage building 42% -> 53% -> 58% over 6/12/18 months with power loss
reaching 7% then 11%. Something that ACCUMULATES over years is not invisible to a
year-on-year method. It shows up as apparent degradation.

So: if biological soiling matters, systems in wet climates should show
systematically steeper apparent degradation than systems in dry ones, beyond what
module physics explains. `rdtools.degradation_year_on_year` on the same
normalised series the soiling fits use.

The anchor we already have. PVDAQ 2107 (Arbuckle CA, growth-weighted TOW 720 h/yr,
the driest CA site here) measured -0.162 %/yr, CI [-0.520, +0.175], with no
standing layer in 8 of 8 years. If wet-climate systems come in materially steeper,
that difference is the biological channel.

WHAT WOULD FALSIFY IT: a flat or noisy relationship between growth-weighted TOW
and apparent degradation. That is a real possible outcome and it would substantially
weaken the moss thesis, which is why this test is worth running before a proposal
rather than after.

CONFOUNDS, stated up front because they are serious:
  * Module technology and vintage drive real degradation and are NOT randomised
    across climates. Install year is partially controlled for by using `years`.
  * Wet climates are also cooler, and heat drives real degradation, so the
    confound runs OPPOSITE to the hypothesis. That makes a positive result harder
    to get and therefore more credible, and a null result less informative.
  * Inverter replacements and repowering show as steps, not trends.

Usage:
    PYTHONPATH=. conda run -n solar-soiling python \
        scripts/analyze/pvdaq_degradation_vs_wetness.py --n-per-bin 6
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.analyze.biological_growth_potential import hourly as tow_hourly
from scripts.analyze.biological_growth_potential import (
    DEW_DEPRESSION_C, Q10, RH_WET, T_FLOOR_C, T_GROWTH_FLOOR_C, T_REF_C,
)
from scripts.analyze.pvdaq_daily_srr_probe import (
    SYSTEMS_CSV, build_pi, fetch_hourly, load_daily, modeled_daily, system_meta,
)


def growth_tow(lat: float, lon: float, start="2019-01-01", end="2024-12-31") -> float:
    """Growth-weighted wet hours per year at a location. Cached per coordinate."""
    h = tow_hourly(round(lat, 3), round(lon, 3), start, end).dropna()
    if h.empty:
        return float("nan")
    t, rh, dp = h.temperature_2m, h.relative_humidity_2m, h.dew_point_2m
    wet = ((rh >= RH_WET) & (t > T_FLOOR_C)) | ((t - dp) < DEW_DEPRESSION_C)
    w = np.where(t >= T_GROWTH_FLOOR_C, Q10 ** ((t - T_REF_C) / 10.0), 0.0)
    return float((wet * w).sum()) / (len(h) / (365.25 * 24))


def degradation(sid: int, systems: pd.DataFrame) -> dict | None:
    from rdtools import degradation_year_on_year

    meta = system_meta(sid, systems)
    daily = load_daily(sid)
    s, e = str(daily.index.min().date()), str(daily.index.max().date())
    model = modeled_daily(meta, fetch_hourly(meta, s, e, at="system"))
    pi, _ = build_pi(daily, model, "energy")
    pi = pi.dropna()
    if len(pi) < 730:  # need >= 2 years for a YoY slope to mean anything
        return None
    rd, ci, _ = degradation_year_on_year(pi, confidence_level=68.2)
    return {
        "system_id": sid, "lat": meta["lat"], "lon": meta["lon"],
        "climate": meta["climate"], "kw": meta["capacity_kw"],
        "tilt": meta["tilt"], "years": meta["years"], "n_days": int(len(pi)),
        "rd_pct_per_yr": float(rd), "rd_ci68": [float(ci[0]), float(ci[1])],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-per-bin", type=int, default=6)
    ap.add_argument("--max-cells", type=int, default=60,
                    help="Score wetness for at most this many cells. Each is one "
                         "Open-Meteo hourly call; the free tier is the binding "
                         "constraint on this script, not compute.")
    ap.add_argument("--out-json", type=Path,
                    default=Path("outputs/soiling/degradation_vs_wetness.json"))
    args = ap.parse_args()

    systems = pd.read_csv(SYSTEMS_CSV)
    for c in ("latitude", "longitude", "elevation_m", "dc_capacity_kW", "tilt",
              "azimuth", "years", "available_sensor_channels"):
        systems[c] = pd.to_numeric(systems[c], errors="coerce")
    res = systems[(systems.qa_status.str.lower() == "pass") & (systems.years >= 5)
                  & (systems.dc_capacity_kW <= 15)
                  & (systems.available_sensor_channels <= 2)
                  & systems.tilt.notna()].copy()

    # Score every candidate's climate ONCE per 0.5-deg cell to stay inside quota.
    res["cell"] = ((res.latitude / .5).round().astype(int).astype(str) + "_"
                   + (res.longitude / .5).round().astype(int).astype(str))
    cells = res.groupby("cell")[["latitude", "longitude"]].first()
    if len(cells) > args.max_cells:
        # Spread the sampled cells over latitude so the wetness gradient survives
        # the subsample; picking at random would over-weight wherever PVDAQ is dense.
        cells = cells.sort_values("latitude")
        idx = np.linspace(0, len(cells) - 1, args.max_cells).round().astype(int)
        cells = cells.iloc[np.unique(idx)]
    print(f"scoring wetness for {len(cells)} cells...", flush=True)
    tow = {}
    for cell, r in cells.iterrows():
        try:
            tow[cell] = growth_tow(r.latitude, r.longitude)
        except Exception as exc:  # noqa: BLE001
            print(f"  {cell} failed: {type(exc).__name__}", flush=True)
    res["growth_tow"] = res.cell.map(tow)
    res = res.dropna(subset=["growth_tow"])
    print(f"{len(res)} systems scored; TOW range "
          f"{res.growth_tow.min():.0f} to {res.growth_tow.max():.0f} h/yr", flush=True)

    # Stratify by wetness so the sample spans the gradient rather than clustering
    # wherever PVDAQ happens to be dense.
    res["bin"] = pd.qcut(res.growth_tow, 4, labels=["driest", "dry", "wet", "wettest"])
    rng = np.random.default_rng(26)
    pick = pd.concat([
        g.iloc[rng.permutation(len(g))[: args.n_per_bin]] for _, g in res.groupby("bin")
    ])
    print(f"sampled {len(pick)} systems across 4 wetness bins\n", flush=True)

    out = []
    for _, r in pick.iterrows():
        sid = int(r.system_id)
        try:
            d = degradation(sid, systems)
        except Exception as exc:  # noqa: BLE001
            print(f"  {sid} FAILED {type(exc).__name__}: {exc}", flush=True)
            continue
        if d is None:
            continue
        d["growth_tow"] = float(r.growth_tow)
        d["bin"] = str(r.bin)
        out.append(d)
        print(f"  {sid}  TOW {r.growth_tow:6.0f}  Rd {d['rd_pct_per_yr']:+.3f} %/yr",
              flush=True)

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"results": out}, open(args.out_json, "w"), indent=2)
    d = pd.DataFrame(out)
    if len(d) < 8:
        print(f"\nonly {len(d)} systems fitted; inconclusive")
        return

    print("\n" + "=" * 66)
    print(d.groupby("bin", observed=True)
          .agg(n=("rd_pct_per_yr", "size"), tow=("growth_tow", "median"),
               rd_med=("rd_pct_per_yr", "median"), rd_sd=("rd_pct_per_yr", "std"))
          .round(3).to_string())
    from scipy import stats
    rho, p = stats.spearmanr(d.growth_tow, d.rd_pct_per_yr)
    print(f"\nSpearman(growth_tow, degradation %/yr) = {rho:+.2f}  p = {p:.3f}  n = {len(d)}")
    print("  (biofilm hypothesis predicts NEGATIVE rho: wetter -> more negative Rd)")
    print(f"\n  anchor: PVDAQ 2107 Arbuckle, TOW 720 h/yr, Rd -0.162 %/yr")
    # PLAUSIBILITY GATE, and it fires. A p-value on implausible quantities is not
    # evidence of anything. c-Si degradation is ~-0.5 %/yr in the literature; a
    # fleet median far from that means the FITS are broken, not that biofilm eats
    # several percent a year.
    med = d.rd_pct_per_yr.median()
    bad = int((d.rd_pct_per_yr < -2).sum()) + int((d.rd_pct_per_yr > 0).sum())
    print(f"\n  PLAUSIBILITY: median Rd {med:+.2f} %/yr vs ~-0.5 %/yr expected for c-Si;"
          f" {bad}/{len(d)} systems implausible (steeper than -2 or positive)")
    if med < -1.5 or bad > len(d) / 3:
        print("\n  RESULT REJECTED. The degradation fits are not credible, so the")
        print("  correlation cannot be interpreted. Daily data with modeled irradiance and")
        print("  no clear-sky filtering does not support a YoY degradation fit.")
        print("  A second, fatal confound: wetness is collinear with GEOGRAPHY here")
        print("  (driest bins are California, wettest are Cfa/Cfb), so module vintage,")
        print("  install practice, cloudiness and data quality all covary with the")
        print("  predictor. This design cannot separate biofilm from data quality.")
        print("\n  The salvage is to ask the SAME question WITHIN one cell, using canopy")
        print("  as the varying term. That is Phase 3, so this collapses into Phase 3")
        print("  rather than standing as an independent fleet-scale test.")
    elif p < 0.05 and rho < 0:
        print("\n  SIGNAL, and the magnitudes are credible. Wetter sites degrade faster,")
        print("  in the direction biofilm predicts, and AGAINST the temperature confound.")
    elif p >= 0.05:
        print("\n  NO DETECTABLE RELATIONSHIP at this n. Either the biological channel is")
        print("  small at fleet scale, or module/vintage variance swamps it. Do not claim")
        print("  fleet-scale support for the moss thesis on this evidence.")
    else:
        print("\n  Relationship runs OPPOSITE to the biofilm prediction. Investigate before")
        print("  using any of this.")


if __name__ == "__main__":
    main()
