"""Measure the WASHABLE share of an age-compounding soiling layer, with RdTools.

Context: docs/ECONOMICS_GROUNDING_20260809.md section 12 established that a
"permanent" soiling layer, one rain never removes, is absent from every quantity
this repo measures *by construction*: NREL defines its published soiling ratio as
the insolation-weighted mean of daily soiling ratios "assuming perfect cleaning
and no soiling between detected soiling intervals". A layer that survives a wash
is outside that definition, and outside ``sl_sat``, which was fitted against it.
Section 12 could bound the layer only from outside the project, at 0.6 pts/yr,
which is Jordan et al. 2016's median x-Si field degradation rate used on the
deliberately false assumption that ALL field degradation is washable grime.

This script measures it instead, on the one public series that can carry the
measurement: PVDAQ system 2107, "Farm Solar Array", Arbuckle CA. 893 kW DC,
fixed 25 deg / 180 az, mono-Si, revenue-grade AC meter, a class pyranometer in
the plane of array, and ambient temperature, from 2017 to 2025. Csa climate, the
same Koeppen class as coastal Santa Cruz.

Three reads, in the order section 12 asked for them:

  1. ``soiling_srr`` with ``method='perfect_clean'`` against the fitted
     ``'half_norm_clean'`` default. NREL's map takes the perfect-clean branch,
     so the gap between the two is incomplete recovery estimated from the same
     toolchain that produced our labels.
  2. CODS (Skomedal & Deceglie), which separates degradation, soiling and
     seasonality self-consistently. Its soiling-ratio series carries no
     degradation, so a downward drift across years in that series is a layer
     the cleanings did not restore. That is the candidate.
  3. The year-on-year degradation rate on the same normalised series, which is
     the "not washable" side of the split: cell cracking, encapsulant browning,
     interconnect failure and PID, none of which a wash touches.

The number the product turns on is (2), in pts/yr, and how much of (3) it is
allowed to be. Anything at or above 0.6 pts/yr of WASHABLE layer should be read
as a fault in the method rather than a result.

Data (gitignored, ~40 MB, no API key needed):

    B=https://oedi-data-lake.s3.amazonaws.com/pvdaq/2023-solar-data-prize/2107_OEDI/data
    mkdir -p data/external/pvdaq_2107 && cd data/external/pvdaq_2107
    for f in 2107_irradiance_data.csv 2107_irradiance_data_2024.csv \
             2107_environment_data.csv 2107_environment_data_2024.csv \
             2107_meter_15m_data.csv 2107_meter_15m_data_2024.csv \
             2107_meter_15m_data_2025.csv 2107_irradiance_15m_data_2025.csv \
             2107_environment_15m_data_2025.csv; do curl -sSO "$B/$f"; done

Run:

    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/washable_share_probe.py
"""

from __future__ import annotations

import glob
import os
import warnings

import numpy as np
import pandas as pd

from src.risk.module_gamma import resolve_system_gamma

warnings.filterwarnings("ignore")

DATA_DIR = "data/external/pvdaq_2107"
TZ = "Etc/GMT+8"  # the site's own note: "actual timezone is US/Pacific but
# needed to use special PST8PDT", i.e. fixed-offset standard time, no DST.

# From the OEDI system metadata for 2107.
LAT, LON, ELEV_M = 38.996306, -122.134111, 10.0
TILT, AZIMUTH = 25.0, 180.0
DC_CAPACITY_KW = 893.0
# Module temperature coefficient.
#
# THIS FILE USED -0.0035 UNTIL 2026-08-27, and `pvdaq_daily_srr_probe.py` used
# -0.0045 while carrying a comment claiming the two matched. They never did. The
# gap is worth 1.045 pts of annual soiling label on system 10109, against a
# method-noise SD of 0.72 -- one undocumented constant with more leverage than the
# signal. Both files now resolve the coefficient through the same module so they
# cannot drift again; see `src/risk/module_gamma.py`.
#
# PVDAQ publishes NO module record for system 2107, so this resolves to the
# labelled fallback: the CEC database median for x-Si, -0.0045. -0.0035 was a
# modern high-efficiency figure (SunPower/Panasonic HIT class) with nothing in
# 2107's metadata to support it.
#
# Numbers recorded from this probe BEFORE 2026-08-27 (degradation -0.162 %/yr,
# standing layer 0.0 pts, CODS 1.0000 in 8/8 years) were produced at -0.0035.
# See the sensitivity note in `docs/HANDOFF_20260827.md`.
GAMMA_PDC = resolve_system_gamma(2107, capacity_kw=DC_CAPACITY_KW).gamma_pdc

SEED = 1234
N_BOOTSTRAP = 1000    # soiling_srr default
N_CODS_REPS = 128     # CODS default is 512; 128 runs in minutes and the CI moves
                      # in the third decimal between the two


# ── loading ───────────────────────────────────────────────────────────────────
def _read_group(pattern: str) -> pd.DataFrame:
    """Read every CSV matching `pattern` onto one tz-aware local index.

    The 2025 files stamp `utc_measured_on`; the rest stamp local `measured_on`.
    Mixing them without converting silently shifts eight hours of a series that
    the whole soiling estimate is read off, so both cases are handled here and
    nowhere else.
    """
    frames = []
    for path in sorted(glob.glob(os.path.join(DATA_DIR, pattern))):
        df = pd.read_csv(path)
        if "utc_measured_on" in df.columns:
            idx = pd.to_datetime(df.pop("utc_measured_on"), utc=True).dt.tz_convert(TZ)
        else:
            idx = pd.to_datetime(df.pop("measured_on")).dt.tz_localize(TZ)
        df.index = idx
        frames.append(df)
    if not frames:
        raise FileNotFoundError(f"no files matched {pattern} under {DATA_DIR}")
    out = pd.concat(frames).sort_index()
    return out[~out.index.duplicated(keep="first")]


def load() -> pd.DataFrame:
    meter = _read_group("2107_meter_*.csv").iloc[:, 0].rename("power_kw")
    poa = _read_group("2107_irradiance_*.csv").iloc[:, 0].rename("poa")
    env = _read_group("2107_environment_*.csv")
    temp = env.iloc[:, 0].rename("temp_raw")
    wind = env.iloc[:, 1].rename("wind")

    df = pd.concat([meter, poa, temp, wind], axis=1)
    # One clock for everything. The meter is 15-minute; irradiance and
    # environment are 5-15 minute, so resample all four to 15 minutes rather
    # than forward-filling irradiance across a quarter hour of changing sky.
    df = df.resample("15min").mean()
    return df


def fahrenheit_check(temp: pd.Series) -> tuple[pd.Series, str]:
    """The metadata calls this column Celsius. Decide from the data instead.

    Arbuckle summers run to about 38 C; a column whose 99th percentile is over
    50 and whose winter midnights sit near 40 is Fahrenheit with a wrong label.
    Getting this wrong biases the temperature correction by tens of degrees and
    would land entirely in the degradation term.
    """
    p99 = temp.quantile(0.99)
    if p99 > 50:
        return (temp - 32.0) * 5.0 / 9.0, f"Fahrenheit (p99 = {p99:.1f}), converted to C"
    return temp, f"Celsius (p99 = {p99:.1f}), used as given"


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    import pvlib
    import rdtools
    from rdtools import TrendAnalysis
    from rdtools.soiling import soiling_srr

    print(f"rdtools {rdtools.__version__} · pvlib {pvlib.__version__}\n")

    df = load()
    print(f"raw span      {df.index.min()} -> {df.index.max()}  ({len(df):,} 15-min rows)")

    temp_c, temp_note = fahrenheit_check(df["temp_raw"])
    df["temp_c"] = temp_c
    print(f"ambient temp  {temp_note}")

    # Keep only the window where all three instruments are reporting. The meter
    # runs to 2025 but the pyranometer stops in 2024, and a soiling ratio cannot
    # be read off a period with no measured plane-of-array irradiance.
    both = df[["power_kw", "poa", "temp_c"]].dropna()
    if both.empty:
        raise SystemExit("no overlap between meter, pyranometer and thermometer")
    lo, hi = both.index.min(), both.index.max()
    df = df.loc[lo:hi]
    years = (hi - lo).days / 365.25
    print(f"usable span   {lo.date()} -> {hi.date()}  ({years:.2f} years, all three instruments)\n")

    print("units sanity")
    print(f"  AC power    p50 {df.power_kw.median():8.1f}  p99 {df.power_kw.quantile(.99):8.1f}  "
          f"max {df.power_kw.max():8.1f}   (DC nameplate {DC_CAPACITY_KW:.0f} kW)")
    print(f"  POA         p50 {df.poa.median():8.1f}  p99 {df.poa.quantile(.99):8.1f}  "
          f"max {df.poa.max():8.1f}   W/m2")
    print(f"  ambient C   p01 {df.temp_c.quantile(.01):8.1f}  p50 {df.temp_c.median():8.1f}  "
          f"p99 {df.temp_c.quantile(.99):8.1f}")
    print(f"  wind m/s    p50 {df.wind.median():8.1f}  p99 {df.wind.quantile(.99):8.1f}\n")

    loc = pvlib.location.Location(LAT, LON, tz=TZ, altitude=ELEV_M)

    ta = TrendAnalysis(
        pv=df["power_kw"],
        poa_global=df["poa"],
        temperature_ambient=df["temp_c"],
        windspeed=df["wind"],
        gamma_pdc=GAMMA_PDC,
        pv_input="power",
        power_dc_rated=DC_CAPACITY_KW,
        temperature_model={"a": -3.56, "b": -0.075, "deltaT": 3},  # open rack, glass/poly
    )
    ta.set_clearsky(pvlib_location=loc, pv_tilt=TILT, pv_azimuth=AZIMUTH)

    print("── 3. degradation (the share a wash cannot touch) ─────────────────")
    ta.sensor_analysis(analyses=["yoy_degradation"])
    deg = ta.results["sensor"]["yoy_degradation"]
    rd_pct = deg["p50_rd"]
    rd_lo, rd_hi = deg["rd_confidence_interval"]
    print(f"  year-on-year Rd  {rd_pct:+.3f} %/yr   95% CI [{rd_lo:+.3f}, {rd_hi:+.3f}]")
    print(f"  Jordan et al. 2016 x-Si field median: -0.5 to -0.6 %/yr\n")

    # The daily normalised, filtered, aggregated performance index that the
    # degradation number above was read off. Both soiling methods run on it, so
    # the three reads are the same series seen three ways.
    daily = ta.sensor_aggregated_performance
    insol = ta.sensor_aggregated_insolation
    daily = daily.where(daily > 0)
    # rdtools requires a gap-free daily index and reads gaps as NaN. Dropping
    # them instead silently compresses the calendar, which would shrink every
    # per-year rate below by the duty cycle of the data logger.
    full = pd.date_range(daily.index.min(), daily.index.max(), freq="D", tz=daily.index.tz)
    daily = daily.reindex(full)
    insol = insol.reindex(full)
    daily.index.freq = "D"
    insol.index.freq = "D"
    n_obs = int(daily.notna().sum())
    print(f"  daily PI series  {len(daily):,} calendar days, "
          f"{daily.index.min().date()} -> {daily.index.max().date()}")
    print(f"  observed         {n_obs:,} of them ({100*n_obs/len(daily):.1f}% coverage), PI p50 {daily.median():.4f}")
    cov = daily.notna().groupby(daily.index.year).mean() * 100
    print("  coverage by year " + "  ".join(f"{y}:{v:.0f}%" for y, v in cov.items()) + "\n")

    print("── 1. soiling_srr: perfect_clean vs the fitted half_norm_clean ────")
    srr = {}
    for method in ("half_norm_clean", "perfect_clean"):
        np.random.seed(SEED)  # soiling_srr bootstraps and takes no seed argument
        sr, sr_ci, info = soiling_srr(
            daily, insol, method=method, reps=N_BOOTSTRAP, confidence_level=95.0
        )
        srr[method] = (sr, sr_ci, info)
        n_iv = len(info["soiling_interval_summary"])
        valid = int(info["soiling_interval_summary"]["valid"].sum())
        print(f"  {method:16s}  insolation-weighted mean SR {sr:.5f}"
              f"   95% CI [{sr_ci[0]:.5f}, {sr_ci[1]:.5f}]"
              f"   loss {100*(1-sr):.3f} pts"
              f"   ({valid}/{n_iv} valid soiling intervals)")
    gap_pts = 100 * (srr["perfect_clean"][0] - srr["half_norm_clean"][0])
    print(f"\n  gap (perfect - half_norm)  {gap_pts:.3f} pts of annual output")
    print("  perfect_clean forces every soiling interval to start at SR = 1.0, which is")
    print("  the branch NREL's published map takes. half_norm_clean fits the start of")
    print("  each interval below 1.0 instead, so the gap is the standing deficit that")
    print("  the resets in this record do not clear. It is a LEVEL, not a rate: it does")
    print("  not divide by the length of the record. Whether it accumulates is the")
    print("  separate question CODS answers below.")
    print("  Note what the resets here mostly ARE: rain. The method labels recovery")
    print("  events, not their cause, and no wash is recorded in this dataset. So this")
    print("  number is the layer RAIN leaves behind. How much of it a paid wash removes")
    print("  is not in this data and this script does not claim it.")

    # And read the gap for what it is. half_norm_clean draws each interval's
    # start as `1 - |N(0, (1 - inferred_recovery)/3)|`, which is one-sided: it
    # can only sit at or below 1.0, and it collapses to exactly 1.0 when every
    # inferred recovery is complete. So the gap is not an independent test of
    # incomplete recovery; it is the interval detector's own inferred recoveries
    # fed through a one-sided prior. Print them, so the gap can be read.
    iv = srr["half_norm_clean"][2]["soiling_interval_summary"]
    v = iv[iv["valid"]]
    st = v["inferred_start_loss"].dropna()
    print(f"\n  performance at the START of each of the {len(st)} valid intervals, i.e. just")
    print("  after a reset. 1.0 is a full recovery; the gap above is what these fall short by:")
    print(f"    p10 {st.quantile(.10):.4f}  p50 {st.quantile(.50):.4f}  "
          f"p90 {st.quantile(.90):.4f}  max {st.max():.4f}  share >= 0.99: {100*(st >= 0.99).mean():.0f}%")
    print("  These inherit the weakness of the input: 64% daily coverage means many")
    print("  resets are only partly observed, and a reset seen late reads as incomplete.\n")

    print("── 2. CODS: degradation, soiling and seasonality, separated ───────")
    from rdtools.soiling import soiling_cods

    np.random.seed(SEED)
    sr_cods, sr_cods_ci, deg_cods, deg_cods_ci, cods_df = soiling_cods(
        daily, reps=N_CODS_REPS, confidence_level=95.0, verbose=False
    )
    print(f"  CODS insolation-weighted mean SR  {sr_cods:.5f}  95% CI [{sr_cods_ci[0]:.5f}, {sr_cods_ci[1]:.5f}]")
    print(f"  CODS degradation                  {deg_cods:+.3f} %/yr  "
          f"95% CI [{deg_cods_ci[0]:+.3f}, {deg_cods_ci[1]:+.3f}]")

    # The washable-but-not-rain-removable candidate: a trend in CODS's own
    # soiling ratio, which by construction carries no degradation.
    soil = cods_df["soiling_ratio"].dropna()
    t_years = np.asarray((soil.index - soil.index[0]).days, dtype=float) / 365.25
    slope, intercept = np.polyfit(t_years, soil.values, 1)
    resid = soil.values - (slope * t_years + intercept)
    # OLS standard error on the slope, then a 95% interval. The residuals are
    # autocorrelated (a sawtooth is), so this interval is optimistic and is
    # reported as a floor on the uncertainty rather than the uncertainty.
    n = len(soil)
    se = np.sqrt((resid**2).sum() / (n - 2) / ((t_years - t_years.mean()) ** 2).sum())
    # Per-year behaviour of the CODS soiling ratio. This is the load-bearing
    # read, not the linear fit below it. If a permanent layer is building, the
    # BEST day of each year gets worse; if rain and cleanings fully restore, the
    # annual maximum stays pinned at 1.0. Same test as section 12's check 3 on
    # NREL's published values, but on a series with degradation removed.
    print("\n  annual behaviour of the CODS soiling ratio")
    print("  (a maximum pinned at 1.0000 means the array reached its clean state that year)")
    for y, s in soil.groupby(soil.index.year):
        if len(s) < 60:
            continue
        print(f"    {y}   n={len(s):3d}   max {s.max():.4f}   p95 {s.quantile(.95):.4f}   p50 {s.median():.4f}")

    print(f"\n  linear trend over the whole record   {-100*slope:+.3f} pts/yr  "
          f"(OLS 95% [{-100*(slope + 1.96*se):+.3f}, {-100*(slope - 1.96*se):+.3f}], n={n})")
    print("  Positive would mean the ratio falls with age, which is the layer we are")
    print("  looking for. Read the annual table above before this number: the series is")
    print("  not linear in time, so a straight line through it is a summary of the two")
    print("  end years more than of the trend, and its OLS interval ignores the")
    print("  autocorrelation a sawtooth necessarily has.")

    print("\n── the split ──────────────────────────────────────────────────────")
    rain_resistant = -100 * slope
    print(f"  degradation, which no wash touches         {rd_pct:+.3f} %/yr  (YoY)"
          f"   {deg_cods:+.3f} %/yr (CODS)")
    print(f"  standing layer the resets do not clear     {gap_pts:.3f} pts, as a level")
    print(f"  does that layer grow with age?             {rain_resistant:+.3f} pts/yr")
    print(f"  section 12's outside ceiling on growth      0.600 pts/yr")
    if rain_resistant >= 0.6:
        print("\n  AT OR ABOVE THE CEILING. Read this as a fault in the method, not a result:")
        print("  it would require every point of field degradation to be washable grime.")
    print("\n  What is still NOT measured, and cannot be from this dataset: the share of")
    print("  that standing layer a paid wash removes. Nothing here records a wash. The")
    print("  site currently assumes all of it (recovery 1.0), which is the generous end.")
    print("\n  And the site this is measured on is Arbuckle: 893 kW ground-mount beside")
    print("  Central Valley farmland, Csa, ~130 km inland of the AOI. Coastal Santa Cruz")
    print("  is Csb with 27 heavy-rain resets a year and no adjacent tillage. Treat every")
    print("  number above as an upper bound for a Santa Cruz rooftop, not a transfer.\n")


if __name__ == "__main__":
    main()
