"""Phase 0a — can `soiling_srr` produce a label from PVDAQ's DAILY residential feed?

`docs/PVDAQ_LABEL_PIPELINE_SPEC.md` §3 assumed the 2-channel residential bulk is
5-minute AC power, reading `number_records` (~102k/system-year) off the systems
table. That field counts NREL's internal pre-aggregation records. What is actually
published under ``pvdaq/csv/pvdata/`` for those systems is a DAILY aggregate:

    Unnamed: 0, ac_power_inv_<id>_daily_max, ..._daily_mean, ac_energy_inv_<id>_daily_sum

~365 rows/year, 4 columns. That fires the spec's own §4a stop rule ("if they are
daily energy totals only, the SRR approach is dead for the bulk"). This script
tests whether that stop rule is correctly shaped, because `soiling_srr` consumes
DAILY insolation-weighted aggregates natively — daily input is the granularity the
fitter wants. What daily-only actually costs is sub-daily clear-sky filtering and
temperature-correction resolution, which raises noise. Whether that noise is fatal
is an empirical question, and this answers it.

Method, and why each piece:
  * Daily measured energy comes from `ac_energy_..._daily_sum` (kWh).
  * Modeled energy is built at HOURLY resolution from Open-Meteo archive
    irradiance, transposed to plane-of-array with pvlib, temperature-corrected
    per hour, then summed to the day. Summing the model hourly and dividing the
    measured daily total by it yields a genuinely insolation-weighted daily PI,
    which is exactly what `soiling_srr` documents as its input. This is the step
    that makes daily-only data usable at all.
  * `recenter=True` (rdtools default) absorbs the absolute offset of a crude
    system model, so the missing loss terms (soiling-free derate, wiring,
    inverter efficiency) do not need to be right — only their time-invariance.
  * Both `perfect_clean` (NREL's map convention, what the existing 891 labels
    use) and the `half_norm_clean` default are run, per spec §5.

Degeneracy is the actual output. A fit that returns SR = 1.0000 with zero valid
intervals is a null result, not a zero-soiling finding.

Usage:
    PYTHONPATH=. conda run -n solar-soiling python \
        scripts/analyze/pvdaq_daily_srr_probe.py --system 10109
    ... --system 10109 10746 10979 11927 --out-json outputs/soiling/pvdaq_0a.json
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from src.risk.module_gamma import fetch_system_metadata, resolve_system_gamma

S3 = "https://oedi-data-lake.s3.amazonaws.com"
SYSTEMS_CSV = f"{S3}/pvdaq/csv/systems_20250729.csv"
NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Temperature coefficient of Pmax.
#
# RESOLVED PER SYSTEM as of 2026-08-27; this constant is now only the labelled
# fallback for systems whose module PVDAQ does not record. See
# `src/risk/module_gamma.py` for the resolution ladder and
# `scripts/analyze/pvdaq_module_gamma.py` for the fleet measurement.
#
# History, because this constant nearly invalidated every label in the repo. Two
# scripts disagreed -- this one used -0.0045, `washable_share_probe.py` -0.0035 --
# and a comment here asserted they matched, which was simply false. MEASURED
# sensitivity on system 10109: -0.0035 -> 2.841 pts, -0.0045 -> 1.796 pts, a delta
# of 1.045 pts against a method-noise SD of 0.72 and a within-cell spread of 1.41.
#
# What the fleet measurement then showed (n=1,629 residential systems, CEC module
# database via pvlib): resolved gamma has median -0.00430 and SD 0.00045, and only
# 9.0% of systems sit 0.0010 or further from -0.0045. So the 0.0010 contrast that
# raised the alarm was a p91 excursion, not a typical one, and -0.0045 was a
# defensible central value all along -- it is the CEC median for both Mono-c-Si
# (-0.450 %/degC) and Multi-c-Si (-0.4515). The recorded labels are therefore
# biased, not invalidated. -0.0035 was the wrong number.
#
# Both files now import this single definition so they cannot drift again.
from src.risk.module_gamma import FLEET_DEFAULT_GAMMA as GAMMA_PDC  # noqa: E402
# SAPM cell-temperature params, open rack glass/poly. Residential roof mounts run
# hotter than open rack; `close_mount_glass_glass` is the pessimistic bound. The
# choice moves the level, not the trend, and `recenter` absorbs the level.
TEMP_MODEL = {"a": -2.98, "b": -0.0471, "deltaT": 1}

N_BOOTSTRAP = 1000
SEED = 0

# A fit off a handful of intervals is an artefact, not a measurement. See the
# note in `run_srr`; system 10912 is the worked example.
MIN_VALID_INTERVALS = 5

# Open-Meteo archive is the quota constraint on Phase 1, not compute or storage.
# Irradiance is cached per 0.5-degree CELL rather than per system: the Phase 3
# design compares systems *within* a cell, which presumes they share weather, so
# cell-level irradiance is the methodologically correct input as well as the
# cheap one. It cuts ~1,381 residential fetches to ~321.
CACHE_DIR = Path(".cache/soiling/pvdaq_irradiance")
CELL_DEG = 0.5

# Cell caching only actually saves quota if every system in a cell hits the SAME
# key. Keying on each system's own date range does not: a first pass produced 31
# cache files across just 9 cells, because systems in a cell start and end in
# different years. So a cell is fetched once over a fixed era window and sliced
# per system. `build_pi` inner-joins model to measured, so the surplus is free.
ERA_START = "2009-01-01"
ERA_END = "2025-12-31"


# ── S3 ────────────────────────────────────────────────────────────────────────
def s3_list(prefix: str) -> list[str]:
    """List every key under `prefix`, following continuation tokens."""
    token, keys = None, []
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        r = requests.get(f"{S3}/", params=params, timeout=60)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        keys += [e.find("s3:Key", NS).text for e in root.findall("s3:Contents", NS)]
        trunc = root.find("s3:IsTruncated", NS)
        if trunc is None or trunc.text != "true":
            return keys
        token = root.find("s3:NextContinuationToken", NS).text


#: Failure types that are the network having a bad moment rather than the system being
#: unusable. Four workers pulling S3 concurrently produced 332 of these in one evening,
#: against 176 genuine "this system has no AC columns" failures.
TRANSIENT_ERRORS = ("SSLError", "ConnectionError", "ReadTimeout", "Timeout",
                    "ChunkedEncodingError", "ProtocolError", "HTTPError")


def _s3_get(url: str, tries: int = 4):
    """GET with backoff. S3 refusing a connection under concurrency is not a verdict
    on the system, and treating it as one silently drops that system from the fleet."""
    import time

    for attempt in range(tries):
        try:
            r = requests.get(url, timeout=120)
            r.raise_for_status()
            return r
        except Exception:  # noqa: BLE001
            if attempt == tries - 1:
                raise
            time.sleep(3.0 * (2 ** attempt))
    raise RuntimeError("unreachable")


def load_daily(system_id: int) -> pd.DataFrame:
    """Concatenate a system's published per-year daily CSVs.

    Returns a frame indexed by naive local date with whatever of
    {energy_kwh, power_max_kw, power_mean_kw} the system publishes.
    """
    keys = [k for k in s3_list(f"pvdaq/csv/pvdata/system_id={system_id}/") if k.endswith(".csv")]
    if not keys:
        raise FileNotFoundError(f"no CSV files under system_id={system_id}")
    frames = []
    for k in sorted(keys):
        r = _s3_get(f"{S3}/{k}")
        df = pd.read_csv(io.StringIO(r.text))
        tcol = df.columns[0]
        df.index = pd.to_datetime(df[tcol], errors="coerce")
        frames.append(df.drop(columns=[tcol]))
    out = pd.concat(frames).sort_index()
    out = out[~out.index.duplicated(keep="first")]
    out = out[out.index.notna()]

    ren = {}
    for c in out.columns:
        lc = c.lower()
        if "ac_energy" in lc and "daily_sum" in lc:
            ren[c] = "energy_kwh"
        elif "ac_power" in lc and "daily_max" in lc:
            ren[c] = "power_max_kw"
        elif "ac_power" in lc and "daily_mean" in lc:
            ren[c] = "power_mean_kw"
    out = out.rename(columns=ren)
    keep = [c for c in ("energy_kwh", "power_max_kw", "power_mean_kw") if c in out.columns]
    if not keep:
        raise ValueError(f"system {system_id}: no recognised AC columns in {list(out.columns)[:8]}")
    return out[keep]


def system_meta(system_id: int, systems: pd.DataFrame) -> dict:
    row = systems.loc[systems.system_id == system_id]
    if row.empty:
        raise KeyError(f"system {system_id} not in systems table")
    row = row.iloc[0]
    # One cached read serves both the timezone and the module coefficient; this used
    # to be an uncached HTTP round-trip per system that read only the timezone.
    sysmeta = fetch_system_metadata(int(system_id))
    tz = (sysmeta.get("System") or {}).get("timezone_code")
    gam = resolve_system_gamma(int(system_id), capacity_kw=float(row.dc_capacity_kW))
    return {
        "system_id": int(system_id),
        "lat": float(row.latitude),
        "lon": float(row.longitude),
        "elev_m": float(row.elevation_m) if pd.notna(row.elevation_m) else 0.0,
        "capacity_kw": float(row.dc_capacity_kW),
        "tilt": float(row.tilt) if pd.notna(row.tilt) else 20.0,
        "azimuth": float(row.azimuth) if pd.notna(row.azimuth) else 180.0,
        "tz": tz or row.timezone_or_utc_offset,
        "climate": str(row.kg_climate),
        "years": float(row.years),
        "channels": float(row.available_sensor_channels),
        "location": str(row.site_location),
        # Per-system module temperature coefficient. `gamma_tier` records how it was
        # resolved, so a fleet-default fallback is never mistaken for a lookup.
        "gamma_pdc": gam.gamma_pdc,
        "gamma_tier": gam.tier,
        "gamma_matched": gam.matched,
    }


# ── modeled irradiance ────────────────────────────────────────────────────────
def cell_key(lat: float, lon: float) -> tuple[int, int]:
    """0.5-degree cell index. Must match the Phase 3 clustering exactly."""
    return int(round(lat / CELL_DEG)), int(round(lon / CELL_DEG))


def _get_with_backoff(params: dict, tries: int = 6):
    """Open-Meteo's free tier prices a call by DATA VOLUME, not by count, so one
    17-year 5-variable hourly request is worth tens of thousands of ordinary ones.
    A fleet warm that ignored this took HTTP 429 on 316 of 345 cells in ten minutes.

    Retries 429 and 5xx with exponential backoff, honouring `Retry-After` when sent.
    Re-raises once the budget is spent so the caller can record the cell as failed and
    move on, rather than stalling an entire multi-hour run on one bad cell.
    """
    import time

    delay = 20.0
    for attempt in range(tries):
        r = requests.get(ARCHIVE_URL, params=params, timeout=300)
        if r.status_code < 400:
            return r
        if r.status_code != 429 and r.status_code < 500:
            r.raise_for_status()
        if attempt == tries - 1:
            r.raise_for_status()
        wait = float(r.headers.get("Retry-After") or delay)
        print(f"      [backoff] HTTP {r.status_code}, sleeping {wait:.0f}s "
              f"(attempt {attempt + 1}/{tries})", flush=True)
        time.sleep(wait)
        delay = min(delay * 2, 900.0)
    raise RuntimeError("unreachable")


def fetch_hourly(meta: dict, start: str, end: str, at: str = "cell",
                 era: tuple[str, str] | None = None) -> pd.DataFrame:
    """Open-Meteo archive hourly irradiance + ambient, localised to the site tz.

    Free, no key, and already the repo's weather source (`src/risk/weather_client`).
    Requested in the site's own timezone so the daily boundaries line up with the
    daily energy totals without a second conversion.
    """
    ci, cj = cell_key(meta["lat"], meta["lon"])
    # `at` is a real methodological choice, not just a caching detail. Measured on
    # system 10109 (6.4 km from its cell centre) it moves the label 1.80 -> 2.10
    # pts, which is inside the 0.72-pt per-label CI but systematic.
    #   cell   - every system in a cluster gets IDENTICAL irradiance. Correct for
    #            Phase 3: a within-cluster difference then cannot be an artefact of
    #            two systems having different irradiance-model error. Also the only
    #            option that fits Open-Meteo's free tier (321 fetches, not 1,381).
    #   system - per-system coordinates. Better for Phase 2, where the question is
    #            absolute agreement with a nearby NREL station rather than a
    #            within-cluster contrast.
    lat = round(ci * CELL_DEG, 4) if at == "cell" else meta["lat"]
    lon = round(cj * CELL_DEG, 4) if at == "cell" else meta["lon"]
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": (era[0] if era else ERA_START) if at == "cell" else start,
        "end_date": (era[1] if era else ERA_END) if at == "cell" else end,
        "hourly": "shortwave_radiation,direct_normal_irradiance,diffuse_radiation,"
                  "temperature_2m,wind_speed_10m",
        "timezone": meta["tz"],
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if at == "cell":
        e0, e1 = era if era else (ERA_START, ERA_END)
        cache = CACHE_DIR / f"cell_{ci}_{cj}_{e0}_{e1}.parquet"
        # Tolerant lookup, and it is load-bearing. The cell cache used to be keyed on
        # one fixed era window, so a file fetched over any DIFFERENT window was
        # invisible and got re-fetched. Now that the fleet warm asks each cell only for
        # the span its own systems need, an exact-key lookup would orphan every cell
        # already on disk and re-spend the quota that fetched them.
        if not cache.exists():
            for cand in sorted(CACHE_DIR.glob(f"cell_{ci}_{cj}_*.parquet")):
                parts = cand.stem.split("_")
                if len(parts) < 5:
                    continue
                c0, c1 = parts[-2], parts[-1]
                if c0 <= start and c1 >= end:
                    cache = cand
                    break
    else:
        cache = CACHE_DIR / f"sys_{meta['system_id']}_{start}_{end}.parquet"
    if cache.exists():
        return pd.read_parquet(cache)

    r = _get_with_backoff(params)
    h = r.json()["hourly"]
    df = pd.DataFrame(h)
    df.index = pd.to_datetime(df.pop("time"))
    df = df.astype(float)
    df.to_parquet(cache)
    return df


def modeled_daily(meta: dict, hourly: pd.DataFrame,
                  gamma: float | None = None) -> pd.DataFrame:
    """Hourly POA + cell temp -> daily modeled energy and daily POA insolation.

    The model is deliberately minimal: DC nameplate x POA/1000 x temperature
    correction. Every omitted derate is time-invariant and therefore absorbed by
    `soiling_srr(recenter=True)`. What must be right is the SHAPE over time.
    """
    import pvlib

    loc = pvlib.location.Location(
        meta["lat"], meta["lon"], tz=meta["tz"], altitude=meta["elev_m"]
    )
    idx = hourly.index.tz_localize(meta["tz"], ambiguous="NaT", nonexistent="NaT")
    hourly = hourly[idx.notna()]
    idx = idx[idx.notna()]
    # Irradiance is an hour-average; the sun position that produced it is the
    # hour's midpoint. Using the label instant biases transposition at dawn/dusk.
    solpos = loc.get_solarposition(idx + pd.Timedelta("30min"))
    solpos.index = idx

    dni_extra = pvlib.irradiance.get_extra_radiation(idx)
    poa = pvlib.irradiance.get_total_irradiance(
        surface_tilt=meta["tilt"],
        surface_azimuth=meta["azimuth"],
        solar_zenith=solpos["apparent_zenith"],
        solar_azimuth=solpos["azimuth"],
        dni=hourly["direct_normal_irradiance"].values,
        ghi=hourly["shortwave_radiation"].values,
        dhi=hourly["diffuse_radiation"].values,
        dni_extra=dni_extra,
        model="perez",
    )["poa_global"].clip(lower=0).fillna(0.0)

    tcell = pvlib.temperature.sapm_cell(
        poa_global=poa,
        temp_air=hourly["temperature_2m"].values,
        wind_speed=hourly["wind_speed_10m"].values / 3.6,  # km/h -> m/s
        **TEMP_MODEL,
    )
    # Hourly modeled AC energy (kWh), nameplate-scaled.
    # Default to the system's own resolved coefficient; the fleet constant is only
    # reached when `system_meta` could not resolve one.
    g = float(meta.get("gamma_pdc") or GAMMA_PDC) if gamma is None else float(gamma)
    e_model = meta["capacity_kw"] * (poa / 1000.0) * (1.0 + g * (tcell - 25.0))
    e_model = e_model.clip(lower=0)

    day = pd.DataFrame({"e_model_kwh": e_model, "poa_wh": poa}).groupby(
        e_model.index.tz_localize(None).normalize()
    ).sum()
    day["insolation_kwh_m2"] = day.pop("poa_wh") / 1000.0
    return day


# ── the fit ───────────────────────────────────────────────────────────────────
def build_pi(daily: pd.DataFrame, model: pd.DataFrame, source: str) -> tuple[pd.Series, pd.Series]:
    """Daily insolation-weighted performance index + matching daily insolation.

    `source='energy'` divides the published daily energy total by the summed
    hourly model. `source='power_max'` uses the daily peak instead, a crude
    clear-sky proxy that discards the cloud-weighting entirely; it is here to
    show how much the energy route is actually buying.
    """
    j = daily.join(model, how="inner")
    j = j[(j["e_model_kwh"] > 0.05) & (j["insolation_kwh_m2"] > 0.1)]
    if source == "energy":
        if "energy_kwh" not in j:
            return pd.Series(dtype=float), pd.Series(dtype=float)
        pi = j["energy_kwh"] / j["e_model_kwh"]
    else:
        if "power_max_kw" not in j:
            return pd.Series(dtype=float), pd.Series(dtype=float)
        # Peak power against peak modeled irradiance-equivalent capacity.
        pi = j["power_max_kw"] / (j["e_model_kwh"] / j["insolation_kwh_m2"])
    pi = pi.replace([np.inf, -np.inf], np.nan)
    # Physically impossible values are logger artefacts, not soiling.
    pi = pi.where((pi > 0) & (pi < pi.quantile(0.999) * 1.5))

    full = pd.date_range(pi.index.min(), pi.index.max(), freq="D")
    pi = pi.reindex(full)
    insol = j["insolation_kwh_m2"].reindex(full)
    pi.index.freq = "D"
    insol.index.freq = "D"
    return pi, insol


def run_srr(pi: pd.Series, insol: pd.Series) -> dict:
    from rdtools.soiling import soiling_srr

    out = {}
    for method in ("perfect_clean", "half_norm_clean"):
        try:
            np.random.seed(SEED)  # soiling_srr bootstraps and takes no seed argument
            sr, ci, info = soiling_srr(
                pi, insol, method=method, reps=N_BOOTSTRAP, confidence_level=95.0
            )
            summ = info["soiling_interval_summary"]
            n_valid = int(summ["valid"].sum())
            out[method] = {
                "soiling_ratio": float(sr),
                "ci95": [float(ci[0]), float(ci[1])],
                "loss_pct": float(100 * (1 - sr)),
                "n_intervals": int(len(summ)),
                "n_valid_intervals": n_valid,
                # The degeneracy test. SR pinned at 1.0 is "the fitter found
                # nothing", which is not the same as "no soiling". The n_valid
                # floor was raised from 0 to MIN_VALID_INTERVALS after system
                # 10912 returned SR 0.99899 (loss 0.10 pts) off just 2 valid
                # intervals with a PI median of 1.10 — a model-mismatch artefact
                # that a bare `n_valid == 0` test waves through. Filter Phase 1
                # output on this field, not on the soiling ratio.
                "degenerate": bool(n_valid < MIN_VALID_INTERVALS or sr >= 0.99995),
            }
        except Exception as e:  # noqa: BLE001 - a failed fit is a result here
            out[method] = {"error": f"{type(e).__name__}: {e}", "degenerate": True}
    return out


def probe(system_id: int, systems: pd.DataFrame, at: str = "cell",
          gamma: float | None = None) -> dict:
    meta = system_meta(system_id, systems)
    print(f"\n{'=' * 78}\nsystem {system_id}  {meta['location']}  {meta['climate']}  "
          f"{meta['capacity_kw']:.2f} kW  tilt {meta['tilt']:.0f} az {meta['azimuth']:.0f}  "
          f"{meta['channels']:.0f} channels")
    print(f"  gamma_pdc  {meta['gamma_pdc']:+.5f} /degC  "
          f"[{meta['gamma_tier']}: {meta['gamma_matched']}]"
          + ("" if gamma is None else f"  OVERRIDDEN -> {gamma:+.5f}"))

    daily = load_daily(system_id)
    span_days = (daily.index.max() - daily.index.min()).days
    print(f"  published  {len(daily):,} rows  {daily.index.min().date()} -> "
          f"{daily.index.max().date()}  ({span_days / 365.25:.2f} yr)  cols {list(daily.columns)}")
    med_dt = pd.Series(daily.index).diff().median()
    print(f"  median dt  {med_dt}   <- confirms the published cadence")

    hourly = fetch_hourly(
        meta, str(daily.index.min().date()), str(daily.index.max().date()), at=at
    )
    model = modeled_daily(meta, hourly, gamma=gamma)
    print(f"  modeled    {len(model):,} days, POA insolation p50 "
          f"{model['insolation_kwh_m2'].median():.2f} kWh/m2/day")

    res = {"meta": meta, "irradiance_at": at,
           "gamma_used": float(gamma if gamma is not None else meta["gamma_pdc"]),
           "n_published_rows": int(len(daily)),
           "median_dt_days": float(med_dt / pd.Timedelta("1D")), "fits": {}}

    for source in ("energy", "power_max"):
        pi, insol = build_pi(daily, model, source)
        if pi.empty:
            continue
        n_obs = int(pi.notna().sum())
        cov = 100 * n_obs / len(pi)
        print(f"\n  -- PI from {source}: {len(pi):,} calendar days, {n_obs:,} observed "
              f"({cov:.1f}%), p50 {pi.median():.3f}")
        if n_obs < 180:
            print("     too few observed days to fit; skipping")
            res["fits"][source] = {"skipped": "n_obs<180", "n_obs": n_obs}
            continue
        fits = run_srr(pi, insol)
        for m, f in fits.items():
            if "error" in f:
                print(f"     {m:16s} FAILED  {f['error']}")
            else:
                flag = "DEGENERATE" if f["degenerate"] else "ok"
                print(f"     {m:16s} SR {f['soiling_ratio']:.5f} "
                      f"CI [{f['ci95'][0]:.5f}, {f['ci95'][1]:.5f}]  "
                      f"loss {f['loss_pct']:5.2f} pts  "
                      f"{f['n_valid_intervals']}/{f['n_intervals']} valid  {flag}")
        res["fits"][source] = {"n_obs": n_obs, "coverage_pct": cov,
                               "pi_median": float(pi.median()), **fits}
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--system", type=int, nargs="+", default=[10109],
                    help="PVDAQ system_id(s). Default 10109 (Scotts Valley, in the AOI).")
    ap.add_argument("--irradiance-at", choices=("cell", "system"), default="cell",
                    help="Fetch modeled irradiance at the 0.5-deg cell centre (default, "
                         "correct for Phase 3 within-cluster contrasts and the only option "
                         "inside Open-Meteo's free tier) or at the system's own coordinate "
                         "(better for Phase 2 agreement with a nearby NREL station).")
    ap.add_argument("--gamma", default="resolved",
                    help="Module temperature coefficient. 'resolved' (default) looks it "
                         "up per system from PVDAQ module metadata against the CEC "
                         "database; 'fleet' forces the labelled fallback "
                         f"({GAMMA_PDC}), which is what every label recorded before "
                         "2026-08-27 used; a float forces that value.")
    ap.add_argument("--out-json", type=Path, default=None)
    ap.add_argument("--resume", action="store_true",
                    help="Skip systems already present in --out-json and append to it. "
                         "Required for fleet-scale runs: a full-fleet pass is ~23 CPU-hours "
                         "and this script previously wrote its JSON only at the very end, "
                         "so any crash, reboot or session close lost the entire run.")
    ap.add_argument("--checkpoint-every", type=int, default=5,
                    help="Flush --out-json every N systems.")
    args = ap.parse_args()

    if args.gamma == "resolved":
        gamma = None
    elif args.gamma == "fleet":
        gamma = GAMMA_PDC
    else:
        gamma = float(args.gamma)

    systems = pd.read_csv(SYSTEMS_CSV)
    for c in ("latitude", "longitude", "elevation_m", "dc_capacity_kW", "tilt",
              "azimuth", "years", "available_sensor_channels"):
        systems[c] = pd.to_numeric(systems[c], errors="coerce")

    results, failures = [], []
    done: set[int] = set()
    if args.resume and args.out_json:
        # Read every sibling shard, not just this one. The shard split is
        # ids[w::workers], so changing the worker count re-deals the systems: a shard
        # that only consults its own file then re-fits everything a differently-numbered
        # shard already did. Going 4 workers -> 3 would have re-run ~150 systems
        # overnight. Sibling results are only ever used to SKIP work, never merged in,
        # so this cannot double-count.
        for sib in sorted(args.out_json.parent.glob("shard*.json")):
            if sib == args.out_json:
                continue
            try:
                d = json.loads(sib.read_text())
            except (ValueError, OSError):
                continue  # mid-write; its systems just get re-checked
            done |= {int(r["meta"]["system_id"]) for r in d.get("results", [])}
        if done:
            print(f"[resume] {len(done)} systems already done by sibling shards",
                  flush=True)
    if args.resume and args.out_json and args.out_json.exists():
        try:
            prev = json.loads(args.out_json.read_text())
            results = prev.get("results", [])
            failures = prev.get("failures", [])
            done |= {int(r["meta"]["system_id"]) for r in results}
            # Only PERMANENT failures count as done. A system that failed on an SSL
            # reset is not a system without usable data, and folding the two together
            # meant one bad minute of network permanently removed it from the fleet.
            retryable = {int(x["system_id"]) for x in failures
                         if any(t in x.get("error", "") for t in TRANSIENT_ERRORS)}
            done |= {int(x["system_id"]) for x in failures} - retryable
            failures = [x for x in failures if int(x["system_id"]) not in retryable]
            if retryable:
                print(f"[resume] retrying {len(retryable)} systems that failed on "
                      f"transient network errors", flush=True)
            print(f"[resume] {len(done)} systems already recorded in {args.out_json}",
                  flush=True)
        except (ValueError, KeyError) as e:
            print(f"[resume] could not read {args.out_json} ({e}); starting fresh",
                  file=sys.stderr)

    def flush():
        if not args.out_json:
            return
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename: a crash mid-write must not truncate the checkpoint that
        # already holds hours of completed fits.
        tmp = args.out_json.with_suffix(args.out_json.suffix + ".tmp")
        tmp.write_text(json.dumps({"results": results, "failures": failures},
                                  indent=2, default=str))
        tmp.replace(args.out_json)

    todo = [s for s in args.system if int(s) not in done]
    print(f"[plan] {len(todo)} systems to fit ({len(done)} skipped)", flush=True)
    for i, sid in enumerate(todo, 1):
        try:
            results.append(probe(sid, systems, at=args.irradiance_at, gamma=gamma))
        except Exception as e:  # noqa: BLE001
            print(f"\nsystem {sid}: FAILED  {type(e).__name__}: {e}", file=sys.stderr)
            failures.append({"system_id": sid, "error": f"{type(e).__name__}: {e}"})
        if args.checkpoint_every and i % args.checkpoint_every == 0:
            flush()
            print(f"[checkpoint] {i}/{len(todo)} done", flush=True)
    flush()

    # Verdict against the spec's §4a stop rule ("<~30% non-degenerate -> stop").
    print(f"\n{'=' * 78}\nVERDICT vs spec §4a stop rule")
    ok = [r for r in results
          if not r["fits"].get("energy", {}).get("perfect_clean", {}).get("degenerate", True)]
    n = len(results)
    if n:
        print(f"  non-degenerate perfect_clean fits from daily energy: {len(ok)}/{n} "
              f"({100 * len(ok) / n:.0f}%)")
        print(f"  spec threshold: >=30% -> "
              f"{'PROCEED' if 100 * len(ok) / n >= 30 else 'STOP / reconsider'}")
    if failures:
        print(f"  systems that errored entirely: {len(failures)}")

    if args.out_json:
        flush()
        print(f"\nwrote {args.out_json}")


if __name__ == "__main__":
    main()
