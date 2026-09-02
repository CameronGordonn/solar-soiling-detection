"""Does the bottom-edge band channel clear the cleaning bar in Santa Cruz?

Closes the loop the dust channel failed to close. Same arithmetic, same rates, same
cost model as `src/risk/economics.py`; the only thing that changes is which soiling
channel is being cleaned.

  dust  : rain resets it ~27x/yr, so one wash buys ~2 weeks -> recovery 0.045
          -> breakeven exceeds 100% of annual output -> DEAD (measured)
  band  : only heavy rain resets it, a scrub removes it outright, and biological
          material takes 1-4 years to recolonise -> recovery should be near 1
          -> breakeven 2.2-10.1% of output

This computes the band recovery fraction from real AOI weather rather than
assuming it, then asks what band coverage f a roof needs before a wash pays.

READ THE OUTPUT AS A SENSITIVITY, NOT A FORECAST. `F_SAT` is unmeasured; the
ground survey exists to measure it. What this script establishes is the SHAPE:
which roofs, at what band thickness, under which mounting, and how hard the
answer depends on the one number nobody has measured yet.

Usage:
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/band_channel_economics.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.analyze.band_rain_regime import daily_precip
from scripts.analyze.biological_growth_potential import (
    DEW_DEPRESSION_C, Q10, RH_WET, T_FLOOR_C, T_GROWTH_FLOOR_C, T_REF_C, hourly,
)
from src.risk import economics as E
from src.risk.band_soiling import (
    F_SAT, band_loss_pct, band_recovery_fraction, band_trajectory,
    bio_recovery_fraction, bio_trajectory,
)


def daily_wet_hours(lat: float, lon: float, start: str, end: str):
    """Growth-weighted wet hours per day. Same weighting as
    `biological_growth_potential.py`, resampled daily."""
    h = hourly(round(lat, 3), round(lon, 3), start, end).dropna()
    t, rh, dp = h.temperature_2m, h.relative_humidity_2m, h.dew_point_2m
    wet = ((rh >= RH_WET) & (t > T_FLOOR_C)) | ((t - dp) < DEW_DEPRESSION_C)
    w = np.where(t >= T_GROWTH_FLOOR_C, Q10 ** ((t - T_REF_C) / 10.0), 0.0)
    return pd.Series(wet.to_numpy() * w, index=h.index).resample("D").sum()

AOI_LAT, AOI_LON = 36.974, -122.031


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--system-kw", type=float, default=5.0)
    args = ap.parse_args()

    p = daily_precip(AOI_LAT, AOI_LON, args.start, args.end).dropna()
    daily = pd.DataFrame({"precipitation_sum": p.values}, index=p.index)
    print(f"Santa Cruz daily precip {args.start}..{args.end}  ({len(daily)} days)\n")

    print("=== 1. band recovery fraction, by roof tilt ===")
    print("    (dust-channel equivalent, measured: 0.045)\n")
    print(f"{'tilt':>6} {'tilt_fac':>9} {'f_end':>7} {'mean loss %':>12} {'recovery':>9}")
    print("-" * 50)
    # Clean at the end of the dry season, the best case, matching how
    # `recovery.best_clean_date` treats the dust channel.
    clean_idx = int(np.argmax(daily.index.month == 9))
    rows = []
    for tilt in (0, 5, 10, 20, 30):
        r = band_recovery_fraction(daily, clean_idx, tilt_deg=float(tilt),
                                   horizon_days=365, case="portrait_full_string")
        f = band_trajectory(daily, float(tilt))
        from src.risk.tilt_response import tilt_soiling_factor
        print(f"{tilt:>6} {tilt_soiling_factor(float(tilt)):>9.2f} {f.iloc[-1]:>7.3f} "
              f"{r['mean_loss_pct_no_clean']:>12.2f} {r['recovery_frac']:>9.3f}")
        rows.append((tilt, r["recovery_frac"], r["mean_loss_pct_no_clean"]))

    band_rec = float(np.median([r[1] for r in rows]))
    print(f"\n  median band recovery_frac = {band_rec:.3f}"
          f"   vs dust 0.045  ->  {band_rec / 0.045:.0f}x better")

    print("\n=== 2. breakeven band coverage f, by rate and mounting ===")
    print("    'what fraction of the bottom cell row must be covered before")
    print("     one professional wash nets > $0'\n")
    nem2 = E.BASE_RATE * 2.8
    scen = dict(E.DEFAULT_SCENARIOS["professional"])
    scen["recovery_frac"] = band_rec
    kw = args.system_kw
    cost = scen["cost_fn"](kw)
    annual_kwh = kw * E.BASE_SUN * 365 * E.SYSTEM_DERATE
    print(f"    {kw:.0f} kW system, wash cost ${cost:.0f}, "
          f"annual output {annual_kwh:.0f} kWh, band recovery {band_rec:.2f}\n")
    print(f"{'case':>24} {'rate':>10} {'loss% needed':>13} {'f needed':>9}")
    print("-" * 60)
    for case in ("portrait_full_string", "portrait_full_mlpe",
                 "landscape_full_string", "landscape_full_mlpe"):
        for label, rate in (("NBT $0.165", E.BASE_RATE), ("NEM2 $0.461", nem2)):
            need_pct = 100.0 * cost / (annual_kwh * rate * band_rec)
            fs = np.linspace(0.01, 1.0, 400)
            losses = np.array([band_loss_pct(f, case) for f in fs])
            hit = fs[losses >= need_pct]
            fneed = f"{hit[0]:.2f}" if len(hit) else "never"
            print(f"{case:>24} {label:>10} {need_pct:>12.1f}% {fneed:>9}")

    # ── the channel that is actually alive ──────────────────────────────────
    print("\n=== 3. the BIOLOGICAL channel (rain does NOT reset it) ===\n")
    wh = daily_wet_hours(AOI_LAT, AOI_LON, args.start, args.end)
    ci = int(np.argmax(wh.index.month == 9))
    print(f"{'shade':>18} {'f_end':>7} {'mean loss %':>12} {'recovery':>9}")
    print("-" * 50)
    bio = {}
    for label, sf in (("open roof (1.0x)", 1.0), ("part canopy (1.5x)", 1.5),
                      ("heavy canopy (2.0x)", 2.0)):
        r = bio_recovery_fraction(wh, ci, horizon_days=365,
                                  case="portrait_full_string", shade_factor=sf)
        bio[label] = r
        print(f"{label:>18} {r['f_final_no_clean']:>7.3f} "
              f"{r['mean_loss_pct_no_clean']:>12.2f} {r['recovery_frac']:>9.3f}")
    bio_rec = bio["part canopy (1.5x)"]["recovery_frac"]
    print(f"\n  biological recovery_frac ~ {bio_rec:.2f}   vs mineral band "
          f"{band_rec:.2f}   vs dust 0.045")
    print("  Rain feeds biofilm instead of flushing it, so a wash is not undone")
    print("  within the year. THAT is where the economics live.\n")

    print(f"{'case':>24} {'rate':>10} {'loss% needed':>13} {'f needed':>9}")
    print("-" * 60)
    for case in ("portrait_full_string", "portrait_full_mlpe", "landscape_full_string"):
        for label, rate in (("NBT $0.165", E.BASE_RATE), ("NEM2 $0.461", nem2)):
            need = 100.0 * cost / (annual_kwh * rate * bio_rec)
            fs = np.linspace(0.01, 1.0, 400)
            losses = np.array([band_loss_pct(f, case) for f in fs])
            hit = fs[losses >= need]
            print(f"{case:>24} {label:>10} {need:>12.1f}% "
                  f"{(f'{hit[0]:.2f}' if len(hit) else 'never'):>9}")

    print(f"\n=== 4. does the AOI plausibly get there? ===")
    print(f"    F_SAT is currently ASSUMED at {F_SAT}. Documented stop rule: if the")
    print("    thickest band findable in town is below f = 0.10, the thesis is dead.")
    print("    The table above converts that stop rule into a per-case bar, which is")
    print("    what the survey should actually be measured against.\n")
    print("    Note the fork: the same physical moss line can be economic on a")
    print("    portrait string-inverter roof and worthless on a landscape MLPE roof.")
    print("    Module orientation and install era are free to extract and are not")
    print("    yet extracted. That is step 1 of the path doc, and this is why.")


if __name__ == "__main__":
    main()
