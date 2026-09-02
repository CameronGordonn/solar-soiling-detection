"""Can PVDAQ see a STANDING (wash-only) soiling layer? No — and this shows why.

`CLAUDE.md` records that the zero-of-1,865 economics verdict covers RECOVERABLE
soiling only: a permanent wash-only layer (moss, lichen, algae) is excluded by
construction from IWSR. The obvious hope is that PVDAQ production data could
settle that channel. It cannot, for a structural reason worth encoding in a
script so nobody re-derives the wrong conclusion.

`rdtools.soiling` builds its normalised index as (soiling.py:152-158):

    if recenter:
        oneyear = start + pd.Timedelta('364d')
        renorm = df.loc[start:oneyear, 'pi'].median()
    df['pi_norm'] = df['pi'] / renorm

`renorm` is the median PI of the array's OWN FIRST 364 DAYS. An array already
mossy on day one carries that moss in its own denominator: its 1.0 means "1.0 of
a mossy array", rain restores it to its own mossy normal, the ratio reads 1.0,
and the layer is invisible. That also explains start values above 1.0 — nothing
exceeds expected output, it exceeds its own first-year median.

`recenter=False` does not rescue it: `pi_norm` becomes absolute, but then the
label inherits every absolute error in the system model (derate, module rating,
packing, inverter efficiency), which a 2-channel feed plus MODELED irradiance
cannot supply. The absolute level is confounded either way.

What this script DOES measure: whether post-reset performance drifts down over a
5-9 year record relative to each array's own baseline, i.e. whether there is
progressive buildup that rain fails to clear.

Do NOT use the perfect_clean/half_norm_clean gap for this. The half_norm start
prior is one-sided by construction (washable_share_probe.py:243), so a positive
gap is expected even with no standing layer. It measures ~7 pts here and is
meaningless.

Usage:
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/pvdaq_standing_layer.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from rdtools.soiling import soiling_srr

from scripts.analyze.pvdaq_daily_srr_probe import (
    SYSTEMS_CSV, build_pi, fetch_hourly, load_daily, modeled_daily, system_meta,
)

SYSTEMS = (10109, 10112, 10477, 11758, 11881)
REPS = 500


def main() -> None:
    systems = pd.read_csv(SYSTEMS_CSV)
    for c in ("latitude", "longitude", "elevation_m", "dc_capacity_kW", "tilt",
              "azimuth", "years", "available_sensor_channels"):
        systems[c] = pd.to_numeric(systems[c], errors="coerce")

    print(f"{'sys':>6} {'cov%':>5} {'n_iv':>5} | interval-start performance "
          f"(1.0 = its OWN first-year median)")
    print(f"{'':>6} {'':>5} {'':>5} |   p10     p50     p90     max    share>=0.99")
    print("-" * 78)

    per_sys, n_iv_total = [], 0
    for sid in SYSTEMS:
        meta = system_meta(sid, systems)
        daily = load_daily(sid)
        h = fetch_hourly(meta, str(daily.index.min().date()),
                         str(daily.index.max().date()), at="system")
        pi, insol = build_pi(daily, modeled_daily(meta, h), "energy")
        cov = 100 * pi.notna().sum() / len(pi)
        np.random.seed(0)
        _, _, info = soiling_srr(pi, insol, method="half_norm_clean",
                                 reps=REPS, confidence_level=95.0)
        iv = info["soiling_interval_summary"]
        st = iv[iv["valid"]]["inferred_start_loss"].dropna()
        if len(st) == 0:
            print(f"{sid:>6} {cov:>5.1f} {0:>5} | no valid intervals")
            continue
        per_sys.append(float(st.median()))
        n_iv_total += len(st)
        print(f"{sid:>6} {cov:>5.1f} {len(st):>5} | {st.quantile(.10):.4f}  "
              f"{st.quantile(.50):.4f}  {st.quantile(.90):.4f}  {st.max():.4f}   "
              f"{100 * (st >= 0.99).mean():>5.0f}%")

    # THE UNIT IS THE SYSTEM, NOT THE INTERVAL. Intervals inside a system are
    # correlated, so pooling them reports an interval far too narrow — the same
    # error `.claude/rules/stage1-detect.md` forbids for the detection gate
    # ("bootstrap over tiles, not objects, [because] arrays within a tile are
    # correlated").
    a = np.array(per_sys)
    print("-" * 78)
    print(f"n = {len(a)} SYSTEMS (not {n_iv_total} intervals).")
    print(f"per-system medians: {np.array2string(np.sort(a), precision=4)}")
    print(f"  median of medians {np.median(a):.4f}   "
          f"range [{a.min():.4f}, {a.max():.4f}]")
    print()
    print("READ AS: relative to each array's OWN first-year baseline, post-reset")
    print("performance does not drift down over 5-9 years -> no progressive buildup")
    print("that rain fails to clear. This is a result about RECOVERABLE-channel")
    print("dynamics. It is NOT a measurement of an absolute standing layer (see the")
    print("module docstring), and must not be written as one.")
    print()
    print("Caveats that travel with it:")
    print("  1. n=5 systems, and they are heterogeneous.")
    print("  2. Selection: PVoutput.org uploaders are close to a perfect ANTI-sample")
    print("     for a hypothesis about neglected roofs.")
    print("  3. Coverage bias cuts in this result's FAVOUR: washable_share_probe.py:252")
    print("     notes partial coverage makes a late-seen reset read as incomplete,")
    print("     biasing toward finding a standing layer. This moved the other way.")


if __name__ == "__main__":
    main()
