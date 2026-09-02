"""SOMOSclean physics-based soiling risk scorer.

Replaces the XGBoost model for V1. Each detected array is scored by running
the SOMOSclean trajectory on its trailing weather window and reporting the
terminal soiling loss as the risk score.

Risk score is normalized to [0, 1] by dividing by sl_sat so that the
downstream recommend rule (threshold at 0.6) is on a consistent scale:
  0.0 = clean (no accumulated soiling)
  0.5 = 50% of saturation ceiling reached
  1.0 = at or above saturation ceiling

Output columns added to the array GeoDataFrame:
  risk_score        float [0, 1] — primary signal consumed by recommend
  soiling_loss_pct  float — raw SL in percent (e.g. 6.3)
  eqd               float — equivalent days of accumulation at scoring date
  last_rain_date    str | None — ISO date of last heavy rain event (≥10 mm)
  scored_at         str — ISO datetime of scoring
  scoring_method    str — always "somosclean-physics-v1"
"""

from __future__ import annotations

import logging
import signal
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd

from risk.labels import somosclean_eqd_trajectory
from risk.weather_client import CacheMiss, OpenMeteoQuotaError, fetch_combined

logger = logging.getLogger(__name__)

# Hard ceiling on how long a single home may wait out quota resets before we
# give up and NaN it. 26h covers a full daily-reset cycle plus slack; a value
# this large only ever trips on a misparsed reason, not normal operation.
_MAX_QUOTA_WAIT_TOTAL_S = 26 * 3600


def _seconds_until_quota_reset(scope: str) -> float:
    """Seconds until Open-Meteo's hourly/daily budget window rolls over (UTC).

    +30s buffer past the boundary so the window has definitely reset.
    """
    now = datetime.now(timezone.utc)
    if scope == "daily":
        nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:  # hourly (default)
        nxt = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return max(1.0, (nxt - now).total_seconds() + 30.0)

# SOMOSclean parameters — mirror configs/soiling/features.yaml:somosclean
# sl_sat=0.08 and k=15 calibrated against 36 coastal CA NREL station-years
# (RMSE 0.033 vs 0.036 for Spanish-plant defaults; observed CA max soiling ~10.6%)
_DEFAULT_PARAMS = {
    "sl_sat": 0.08,
    "k": 15.0,
    "heavy_rain_mm": 10.0,
    "rain_min_mm": 1.0,
    "pm10_dust_threshold": 50.0,
    "pm10_dust_scale": 0.02,
}


@contextmanager
def _deadline(seconds: int):
    """Hard wall-clock cap on the wrapped block (main thread, Unix).

    Defends the per-home loop against a wedged HTTP socket: a rate-limit
    slow-loris can trickle bytes and defeat ``requests``' between-bytes read
    timeout, hanging a fetch indefinitely. SIGALRM interrupts the blocked
    syscall and raises, so the caller's ``except`` drops that home to NaN and
    the run continues instead of stalling. No-op when seconds<=0 or off-main-thread.
    """
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return
    try:
        signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(
            TimeoutError(f"per-home scoring exceeded {seconds}s")))
    except ValueError:  # not in main thread — can't use signals; run uncapped
        yield
        return
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, signal.SIG_DFL)


def score_arrays(
    gdf: gpd.GeoDataFrame,
    *,
    as_of: Optional[date] = None,
    lookback_days: int = 365,
    cache_dir: Path = Path(".cache/soiling"),
    params: Optional[dict] = None,
    per_home_timeout: int = 150,
    cache_only: bool = False,
    grid_deg: float = 0.0,
) -> gpd.GeoDataFrame:
    """Run SOMOSclean on each array and attach risk columns.

    Arrays with missing centroids or failed weather fetches get risk_score=NaN.
    The GeoDataFrame must have a geometry column in any CRS (centroids are
    reprojected to WGS84 for the weather fetch).

    With ``cache_only=True`` no network is used: homes whose weather is already
    cached are scored (weather-only, no AQ), and uncached homes are quietly left
    as NaN. Lets you publish a partial result from what's cached so far while the
    full background run keeps filling the cache.

    With ``grid_deg > 0`` each home's centroid is snapped to a lat/lon grid of
    that spacing before the weather lookup, and homes sharing a cell reuse a
    single fetch (in-memory this run, and via the on-disk cache across runs).
    The snap spacing is meant to be <= the reanalysis native resolution
    (ERA5 ~0.25 deg), so the per-home score is materially unchanged — Open-Meteo
    already returns the same grid cell for every home within it — while a dense
    AOI of ~1,300 homes collapses to a few dozen fetches, turning a multi-day
    free-tier grind into a single-pass run. ``grid_deg=0`` (default) keeps the
    exact per-home coordinate, preserving behavior for calibration/validation.
    """
    p = {**_DEFAULT_PARAMS, **(params or {})}
    sl_sat = float(p["sl_sat"])
    as_of = as_of or datetime.now(timezone.utc).date()
    start = as_of - timedelta(days=lookback_days)
    scored_at = datetime.now(timezone.utc).isoformat()

    gdf = gdf.copy()
    # Compute centroids in a projected CRS then reproject to WGS84 for lookups.
    centroids_wgs84 = gdf.geometry.to_crs("EPSG:3857").centroid.to_crs("EPSG:4326")

    risk_scores: list[float] = []
    soiling_loss_pcts: list[float] = []
    soiling_loss_annual_pcts: list[float] = []
    eqds: list[float] = []
    last_rain_dates: list[Optional[str]] = []

    n = len(gdf)
    n_cache_miss = 0
    n_fetches = 0
    # Homes sharing a grid cell share one weather series: memoize per snapped
    # cell so a dense AOI fetches each cell once, not once per home (see grid_deg).
    weather_by_cell: dict[tuple[float, float], pd.DataFrame] = {}

    def _snap(v: float) -> float:
        return round(round(v / grid_deg) * grid_deg, 4) if grid_deg > 0 else v

    for i, (_, geom) in enumerate(zip(gdf.index, centroids_wgs84)):
        lat, lon = geom.y, geom.x
        cell = (_snap(lat), _snap(lon))
        try:
            daily = weather_by_cell.get(cell)
            if daily is None:
                # Fetch with quota-aware waiting: a minute-window blip is retried
                # inside fetch_combined; an hourly/daily budget exhaustion raises
                # OpenMeteoQuotaError, which we sleep out *here* (outside the per-home
                # SIGALRM deadline, so a 50-min reset wait can't trip it) and then
                # retry the SAME cell — no row is dropped just because we ran out of
                # free-tier budget. The cache makes the whole loop resumable.
                waited_total = 0.0
                while daily is None:
                    try:
                        with _deadline(per_home_timeout):
                            daily = fetch_combined(cell[0], cell[1], start, as_of,
                                                   cache_dir=cache_dir, cache_only=cache_only)
                    except OpenMeteoQuotaError as quota:
                        wait_s = _seconds_until_quota_reset(quota.scope)
                        waited_total += wait_s
                        if waited_total > _MAX_QUOTA_WAIT_TOTAL_S:
                            raise  # pathological — fall through to NaN rather than hang forever
                        logger.warning(
                            "Open-Meteo %s budget exhausted at home %d/%d — pausing %.0f min "
                            "for the window to reset, then resuming (no rows lost).",
                            quota.scope, i + 1, n, wait_s / 60.0)
                        time.sleep(wait_s)  # outside _deadline; cache makes this resumable
                weather_by_cell[cell] = daily
                n_fetches += 1
            traj_kwargs = {k: p[k] for k in (
                "sl_sat", "k", "heavy_rain_mm", "rain_min_mm",
                "pm10_dust_threshold", "pm10_dust_scale",
            )}
            eqd_series, sl_series = somosclean_eqd_trajectory(daily, **traj_kwargs)

            sl_terminal = float(sl_series.iloc[-1])      # soiling on the scoring date (clean-now signal)
            sl_mean = float(sl_series.mean())            # annual-average soiling (drives $; accounts for rain resets)
            risk = min(1.0, sl_terminal / sl_sat) if sl_sat > 0 else 0.0
            eqd = float(eqd_series.iloc[-1])

            # Last heavy rain: last date where precip >= heavy_rain_mm
            precip = daily.get("precipitation_sum")
            last_rain: Optional[str] = None
            if precip is not None:
                heavy = precip[pd.to_numeric(precip, errors="coerce").fillna(0) >= float(p["heavy_rain_mm"])]
                if not heavy.empty:
                    last_rain = heavy.index[-1].date().isoformat()

            risk_scores.append(risk)
            soiling_loss_pcts.append(round(sl_terminal * 100, 3))
            soiling_loss_annual_pcts.append(round(sl_mean * 100, 3))
            eqds.append(round(eqd, 1))
            last_rain_dates.append(last_rain)

        except CacheMiss:
            # cache-only mode: weather not cached yet — skip quietly (NaN = pending)
            n_cache_miss += 1
            risk_scores.append(float("nan"))
            soiling_loss_pcts.append(float("nan"))
            soiling_loss_annual_pcts.append(float("nan"))
            eqds.append(float("nan"))
            last_rain_dates.append(None)

        except Exception as exc:
            logger.warning("Physics score failed for array %d (%.4f, %.4f): %s", i, lat, lon, exc)
            risk_scores.append(float("nan"))
            soiling_loss_pcts.append(float("nan"))
            soiling_loss_annual_pcts.append(float("nan"))
            eqds.append(float("nan"))
            last_rain_dates.append(None)

        if (i + 1) % 10 == 0 or (i + 1) == n:
            logger.info("Scored %d / %d arrays", i + 1, n)

    if grid_deg > 0:
        logger.info("grid_deg=%.3f: %d homes served by %d unique cell fetches", grid_deg, n, n_fetches)
    if cache_only:
        logger.info("cache-only: %d/%d homes scored from cache, %d not yet cached (left NaN)",
                    n - n_cache_miss, n, n_cache_miss)

    gdf["risk_score"] = risk_scores
    gdf["soiling_loss_pct"] = soiling_loss_pcts                # terminal: soiling on the scoring date
    gdf["soiling_loss_annual_pct"] = soiling_loss_annual_pcts  # mean over the year: drives $-economics
    gdf["eqd"] = eqds
    gdf["last_rain_date"] = last_rain_dates
    gdf["scored_at"] = scored_at
    gdf["scoring_method"] = "somosclean-physics-v1"
    return gdf
