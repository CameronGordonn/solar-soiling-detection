"""Do ANY arrays in the Santa Cruz AOI reach the cleaning threshold?

The question the whole programme reduces to, asked against real per-roof data
rather than a representative system.

Inputs, all already computed and all free:
  * `moss_candidates.csv` - 2,494 arrays with canopy fraction (3DEP lidar returns
    >2.5 m above the array's own fitted plane, 9 m radius), fitted tilt, fitted
    azimuth, and polygon area.
  * AOI wetness - growth-weighted Time of Wetness, Open-Meteo hourly.
  * `src/risk/band_soiling.py` - biological coverage trajectory.
  * `src/risk/economics.py` - unchanged cost and rate model.

THE INVERTER ARCHITECTURE PROBLEM, and it is the crux
-----------------------------------------------------
The substring physics says the same moss line costs ~21% of array output on a
string inverter and ~8% on MLPE, because MLPE stops one shaded module dragging
down the whole series string. So which architecture a roof has decides whether a
wash pays.

**NEC 690.12(B)(2), effective 2019-01-01, limits conductors inside the array
boundary to 80 V on shutdown, which in practice mandates module-level electronics
(microinverters or optimisers) on residential rooftops.** California adopted NEC
2014 on 2017-01-01 and NEC 2017 subsequently. Microinverters alone are ~65% of the
US MLPE market and residential is ~93% of microinverter demand.

So post-2019 California residential is effectively ALL MLPE, and MLPE is the
unfavourable case. This is a code requirement, not a market preference, so it
cannot be assumed away. Treating a Santa Cruz roof as a string-inverter roof is
almost certainly wrong unless it predates ~2017.

Note what MLPE does and does not fix: it operates at MODULE level, so it stops
loss propagating between modules, but the BYPASS DIODES INSIDE a module still cut
a substring when the bottom cell row is occluded. MLPE reduces the nonlinearity,
it does not remove it. That is why `portrait_full_mlpe` still shows 8.20% at
f=0.5 rather than 0%.

Usage:
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/aoi_cleaning_threshold.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.analyze.band_channel_economics import AOI_LAT, AOI_LON, daily_wet_hours
from src.risk import economics as E
from src.risk.band_soiling import F_SAT, K_BIO_HOURS, band_loss_pct, bio_trajectory

CANDIDATES = Path("outputs/aoi/santa-cruz-w2-21cm/moss_candidates.csv")

#: Per-roof drying-time multiplier. ASSUMED weights, but they mirror
#: `rank_moss_candidates.py` (0.60 canopy / 0.25 low tilt / 0.15 north) and the
#: facade-biofilm literature, where shading, slow drying and north aspect are the
#: recognised drivers of algal growth. Scaled so a worst-case roof is ~2x the
#: regional mean wetness exposure.
SHADE_MAX_BOOST = 1.0


def shade_factor(canopy: float, tilt: float, azimuth: float) -> float:
    """1.0 = average roof; higher = stays wet longer, so grows more biofilm."""
    c = float(np.clip(canopy, 0, 1))
    # Low tilt holds water. Cano's curve is about dust, so it is not reused here;
    # this is a simple monotone term over the residential tilt range.
    t = float(np.clip((35.0 - tilt) / 35.0, 0, 1))
    # North-facing dries slowest in the northern hemisphere (180 = south).
    n = float(np.clip(abs(azimuth - 180.0) / 180.0, 0, 1))
    return 1.0 + SHADE_MAX_BOOST * (0.60 * c + 0.25 * t + 0.15 * n)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--f-sat", type=float, default=F_SAT)
    ap.add_argument("--k-bio", type=float, default=K_BIO_HOURS)
    args = ap.parse_args()

    d = pd.read_csv(CANDIDATES)
    d["system_kw"] = d.area_m2.apply(E.system_kw_from_area)
    d = d[d.system_kw.notna() & (d.system_kw > 0.5)].copy()
    d["shade"] = [shade_factor(c, t, a) for c, t, a
                  in zip(d.canopy_frac, d.tilt_deg, d.azimuth_deg)]

    wh = daily_wet_hours(AOI_LAT, AOI_LON, args.start, args.end)
    print(f"AOI arrays with usable geometry: {len(d)}")
    print(f"  system kW   p10 {d.system_kw.quantile(.1):.1f}  p50 {d.system_kw.median():.1f}"
          f"  p90 {d.system_kw.quantile(.9):.1f}")
    print(f"  canopy frac p50 {d.canopy_frac.median():.3f}  p90 {d.canopy_frac.quantile(.9):.3f}")
    print(f"  shade mult  p50 {d.shade.median():.2f}  p90 {d.shade.quantile(.9):.2f}"
          f"  max {d.shade.max():.2f}\n")

    # Equilibrium coverage per roof: run the trajectory to its asymptote for that
    # roof's own wetness exposure. Ten years of AOI weather is the horizon.
    uniq = np.round(d.shade, 2).unique()
    fmap = {s: float(bio_trajectory(wh, None, f_sat=args.f_sat,
                                    k_hours=args.k_bio, shade_factor=float(s)).iloc[-1])
            for s in uniq}
    d["f"] = np.round(d.shade, 2).map(fmap)
    print(f"modelled band coverage f:  p50 {d.f.median():.3f}  p90 {d.f.quantile(.9):.3f}"
          f"  max {d.f.max():.3f}   (F_SAT ceiling {args.f_sat})\n")

    nem2 = E.BASE_RATE * 2.8
    scen = E.DEFAULT_SCENARIOS["professional"]
    # DERIVED, not hardcoded. An earlier version pinned this at 0.61, a figure
    # computed BEFORE the `roof_equilibrium_f` correction; post-correction it is
    # 0.39, so the hardcoded value was 1.6x optimistic. Recompute it here so the
    # two scripts cannot drift apart again.
    from src.risk.band_soiling import bio_recovery_fraction
    ci = int(np.argmax(wh.index.month == 9))
    BIO_RECOVERY = bio_recovery_fraction(
        wh, ci, horizon_days=365, case="portrait_full_string",
        shade_factor=float(d.shade.median()), f_sat=args.f_sat, k_hours=args.k_bio,
    )["recovery_frac"]
    print(f"biological recovery fraction (derived, median roof): {BIO_RECOVERY:.3f}\n")

    print("=" * 74)
    print("HOW MANY AOI ARRAYS CLEAR, by inverter architecture and tariff")
    print("=" * 74)
    print(f"{'architecture':>22} {'tariff':>12} {'n clearing':>11} {'share':>7} {'best $net':>10}")
    print("-" * 74)
    results = {}
    for case, arch in (("portrait_full_mlpe", "MLPE (post-2019)"),
                       ("portrait_full_string", "string (pre-2017)"),
                       ("landscape_full_mlpe", "MLPE landscape"),
                       ("landscape_full_string", "string landscape")):
        for tname, rate in (("NBT $0.165", E.BASE_RATE), ("NEM2 $0.461", nem2)):
            loss = np.array([band_loss_pct(f, case) for f in d.f])
            annual_kwh = d.system_kw.values * E.BASE_SUN * 365 * E.SYSTEM_DERATE
            recovered = annual_kwh * (loss / 100.0) * rate * BIO_RECOVERY
            cost = np.array([scen["cost_fn"](k) for k in d.system_kw.values])
            net = recovered - cost
            n = int((net > 0).sum())
            results[(case, tname)] = net
            print(f"{arch:>22} {tname:>12} {n:>11} {100*n/len(d):>6.1f}% "
                  f"{net.max():>10.0f}")

    print("\n" + "=" * 74)
    print("SENSITIVITY: the answer is dominated by F_SAT, which is UNMEASURED")
    print("=" * 74)
    print(f"{'F_SAT':>7} " + "".join(f"{a:>20}" for a in
          ("MLPE/NEM2", "MLPE/NBT", "string/NEM2")))
    print("-" * 70)
    for fs in (0.10, 0.20, 0.30, 0.50, 0.80):
        fmap2 = {s: float(bio_trajectory(wh, None, f_sat=fs, k_hours=args.k_bio,
                                         shade_factor=float(s)).iloc[-1]) for s in uniq}
        f2 = np.round(d.shade, 2).map(fmap2).values
        annual_kwh = d.system_kw.values * E.BASE_SUN * 365 * E.SYSTEM_DERATE
        cost = np.array([scen["cost_fn"](k) for k in d.system_kw.values])
        row = ""
        for case, rate in (("portrait_full_mlpe", nem2),
                           ("portrait_full_mlpe", E.BASE_RATE),
                           ("portrait_full_string", nem2)):
            loss = np.array([band_loss_pct(x, case) for x in f2])
            net = annual_kwh * (loss / 100.0) * rate * BIO_RECOVERY - cost
            row += f"{int((net > 0).sum()):>12} ({100*(net>0).mean():>4.1f}%)"
        print(f"{fs:>7.2f} {row}")

    # ── join the real per-roof install era, if it has been built ────────────
    era_path = CANDIDATES.parent / "array_install_era.csv"
    if era_path.exists():
        era = pd.read_csv(era_path)
        print("\n" + "=" * 74)
        print("WITH REAL PER-ROOF INSTALL ERA (array_install_era.py)")
        print("=" * 74)
        m = d.merge(era, left_on="index", right_on="array_idx", how="left")
        m["architecture"] = m.architecture.fillna("unknown")
        m["tariff"] = m.tariff.fillna("unknown")
        print(m.groupby(["architecture", "tariff"]).size().to_string())

        # Only the pre-2017 + NEM 2.0 cell can clear. Score it honestly.
        fav = m[(m.architecture == "mixed_pre2017") & (m.tariff == "nem2_legacy")]
        print(f"\nfavourable cell: {len(fav)} arrays")
        if len(fav):
            annual_kwh = fav.system_kw.values * E.BASE_SUN * 365 * E.SYSTEM_DERATE
            cost = np.array([scen["cost_fn"](k) for k in fav.system_kw.values])
            for case in ("portrait_full_string", "portrait_full_mlpe"):
                loss = np.array([band_loss_pct(f, case) for f in fav.f])
                net = annual_kwh * (loss / 100.0) * nem2 * BIO_RECOVERY - cost
                print(f"  if {case:22s}: {int((net>0).sum()):>3} clear, "
                      f"best net ${net.max():>7.0f}, median net ${np.median(net):>7.0f}")
            print("\n  The two rows bracket the truth, because we cannot tell which")
            print("  hardware these roofs actually have. Only the string row clears.")

    print("\nREAD THIS CAREFULLY")
    print("  NEC 690.12(B)(2) has effectively mandated MLPE on California residential")
    print("  rooftops since 2019, and MLPE is the UNFAVOURABLE row. The string-inverter")
    print("  rows describe pre-2017 installs only, which are a minority and shrinking.")
    print("  Treat the MLPE rows as the real answer for this AOI.")


if __name__ == "__main__":
    main()
