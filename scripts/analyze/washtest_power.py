"""How many days of before/after production does a wash test actually need?

The AOI cleaning plan (``docs/AOI_CLEANING_TARGETING_PLAN.md``) proposes measuring what a
cleaning recovers by comparing irradiance-normalised daily production before and after a
wash. That design lives or dies on one number nobody in this repo has measured: the
**day-to-day scatter of an irradiance-normalised daily production series**. Every sample
size, every minimum detectable effect, every "is this experiment even worth running"
follows from it, and assuming it would be exactly the failure mode the economics grounding
pass was written to stop.

PVDAQ system 2107 (Arbuckle CA, 893 kW ground mount) carries a co-located POA pyranometer
and a revenue-grade AC meter at 15-minute cadence, so the noise floor is directly
measurable there. What this establishes and what it does not:

* It **is** a floor. 2107 has an on-site pyranometer, one plane of array, no roof
  obstructions, and a large array that averages out local effects. A residential rooftop
  normalised against satellite irradiance is strictly noisier, so the sample sizes computed
  here are the optimistic end.
* It is **not** the residential number. Do not quote the MDE as "the study needs N days".
  Quote it as "the study needs at least N days, on the most favourable instrumentation
  available".

Run:
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/washtest_power.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path("data/external/pvdaq_2107")

# Two-sided alpha=0.05, power=0.80 -> z_{1-a/2} + z_{1-b} = 1.960 + 0.842.
Z_SUM = 1.959964 + 0.841621


def _load(path: Path, value_col_hint: str) -> pd.Series:
    # The 2025 vintage renames the stamp to `utc_measured_on` AND changes its time zone,
    # while the meter file beside it stays local. Verified: the meter series peaks at hour
    # 12, the irradiance series at hour 20. Binning both by naive calendar day would
    # misalign them by eight hours and shred the daily ratio, which is the most likely
    # cause of the unexplained 2025 drop §13 of the economics grounding doc left open.
    # Convert UTC stamps to site-local time so the two series bin to the same day.
    df = pd.read_csv(path)
    stamp = "measured_on" if "measured_on" in df.columns else "utc_measured_on"
    ts = pd.to_datetime(df[stamp], errors="coerce", format="mixed", utc=False)
    if stamp == "utc_measured_on":
        ts = ts.dt.tz_localize("UTC").dt.tz_convert("America/Los_Angeles").dt.tz_localize(None)
    cols = [c for c in df.columns if c != stamp]
    col = next((c for c in cols if value_col_hint in c), cols[0])
    s = pd.to_numeric(df[col], errors="coerce")
    s.index = ts
    return s.dropna().sort_index()


def daily_index(meter: pd.Series, poa: pd.Series, min_insolation: float) -> pd.DataFrame:
    """Daily energy / daily insolation, on days with enough sun to mean anything.

    Both series are instantaneous readings (meter in kW, POA in W/m2), and the two files
    do NOT share a cadence: the 2024 irradiance export runs about 6-minutely against the
    meter's 15, and the 2025 irradiance export changes cadence part-way through, giving
    per-day sample counts that are bimodal at 95 and 240. Summing raw samples would make
    each daily total proportional to how often the logger happened to write that day, and
    that artefact alone inflated the measured scatter from 4% to 11%. Integrating hourly
    means instead makes both sides cadence-invariant, which is the whole point: a wash test
    reads a *step change* in this ratio, so any nuisance that moves it is a direct
    subtraction from the experiment's power.
    """
    e = meter.resample("h").mean().resample("D").sum()
    h = poa.resample("h").mean().resample("D").sum()
    n = poa.resample("h").mean().resample("D").count()
    df = pd.DataFrame({"energy": e, "insol": h, "n_hours": n}).dropna()
    df = df[(df["n_hours"] >= 24) & (df["insol"] > 0)]  # whole days only
    thresh = df["insol"].quantile(min_insolation)
    df = df[df["insol"] >= thresh]
    df["index"] = df["energy"] / df["insol"]
    return df


def residual_stats(pi: pd.Series, window: int) -> dict:
    """Scatter left after removing slow drift, which a before/after test also removes.

    A wash test compares two adjacent windows, so any trend slower than the window is
    common to both sides and is not noise the test has to fight. Detrending with a rolling
    median of the same order as the test window is therefore the right reference, and
    reporting the raw CV instead would overstate the required sample size.
    """
    base = pi.rolling(window, center=True, min_periods=max(3, window // 3)).median()
    resid = np.log(pi / base).dropna()
    resid = resid[np.isfinite(resid)]
    sigma = float(resid.std(ddof=1))
    rho = float(resid.autocorr(lag=1)) if len(resid) > 5 else float("nan")
    return {"sigma_frac": sigma, "lag1_autocorr": rho, "n_days": int(len(resid))}


def mde(sigma: float, rho: float, n_per_side: int) -> float:
    """Minimum detectable step change, as a fraction of output.

    ``rho`` inflates the variance of a window mean because weather arrives in multi-day
    blocks: the effective sample size of an AR(1) series is n*(1-rho)/(1+rho), so ignoring
    it would report an interval that is too narrow, the same error the Stage 1 gate had to
    fix by bootstrapping over tiles rather than objects.
    """
    rho = 0.0 if not np.isfinite(rho) else max(0.0, min(0.95, rho))
    n_eff = n_per_side * (1.0 - rho) / (1.0 + rho)
    return Z_SUM * sigma * np.sqrt(2.0 / max(n_eff, 1.0))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", default="2024", choices=["2024", "2025"])
    ap.add_argument("--min-insolation-q", type=float, default=0.25,
                    help="drop the dimmest q of days (overcast days carry no information)")
    ap.add_argument("--windows", type=int, nargs="+", default=[7, 14, 21, 30, 45, 60])
    args = ap.parse_args()

    suffix = f"_{args.year}" if args.year == "2024" else "_15m_data_2025"
    meter_p = DATA / (f"2107_meter_15m_data{suffix}.csv" if args.year == "2024"
                      else "2107_meter_15m_data_2025.csv")
    poa_p = DATA / (f"2107_irradiance_data_{args.year}.csv" if args.year == "2024"
                    else "2107_irradiance_15m_data_2025.csv")

    meter = _load(meter_p, "meter")
    poa = _load(poa_p, "poa")
    df = daily_index(meter, poa, args.min_insolation_q)

    print(f"PVDAQ 2107 wash-test power probe — {args.year}")
    print(f"  meter      {meter_p.name}  n={len(meter):,}")
    print(f"  irradiance {poa_p.name}  n={len(poa):,}")
    print(f"  usable days after the dimmest {args.min_insolation_q:.0%} are dropped: {len(df)}")
    print()
    print("  window   sigma    lag1     MDE (% of output)   $/yr on a 6 kW home")
    print("  (days)   (frac)   rho      at that n/side      NBT $0.165 | NEM2 $0.457")

    # 6 kW x ~1,500 kWh/kWp/yr, Santa Cruz. Only used to render the MDE in money.
    annual_kwh = 6.0 * 1500.0
    for w in args.windows:
        st = residual_stats(df["index"], w)
        m = mde(st["sigma_frac"], st["lag1_autocorr"], w)
        d_nbt = m * annual_kwh * 0.1646
        d_nem = m * annual_kwh * 0.4573
        print(f"  {w:>5}   {st['sigma_frac']:.4f}  {st['lag1_autocorr']:+.3f}   "
              f"{m * 100:>6.2f}%              ${d_nbt:>6.0f} | ${d_nem:>6.0f}")

    print()
    print("  Read: a wash has to recover more than the MDE column for the test to see it")
    print("  at all. Compare against the cost of the wash ($90 light-pro / $150 pro).")
    print("  These are a FLOOR — on-site pyranometer, ground mount, 893 kW.")


if __name__ == "__main__":
    main()
