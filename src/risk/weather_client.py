"""Open-Meteo historical weather + air quality client with on-disk caching.

Open-Meteo is free with no API key and exposes ERA5 reanalysis back to 1940 plus
CAMS air quality (PM2.5/PM10) going back several years. Daily aggregates are
sufficient for soiling-risk rolling-window features.

MERRA-2 (NASA) extends AQ coverage back to 1980. Set the environment variable
NASA_EARTHDATA_TOKEN to enable it. When set, MERRA-2 replaces CAMS as the AQ
source for the full date range (no 2013 floor). Requires approving the
"NASA GESDISC DATA ARCHIVE" app in your Earthdata profile.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Free vs commercial endpoints ─────────────────────────────────────────────
# Buying an Open-Meteo subscription does NOT raise the limit on the free hosts.
# A paid plan moves you to a dedicated `customer-` endpoint authenticated with an
# `apikey` query parameter; the request syntax is otherwise identical. So paying
# without pointing the client at that endpoint changes nothing at all -- the free
# host keeps enforcing the same sliding minute/hour/day windows. That is the whole
# reason this indirection exists instead of two bare constants.
#
# Set OPEN_METEO_API_KEY to switch both endpoints over. If the host convention
# ever differs from the documented `customer-` prefix, override the full URL via
# OPEN_METEO_ARCHIVE_URL / OPEN_METEO_AQ_URL rather than editing this file.
OPEN_METEO_API_KEY = os.environ.get("OPEN_METEO_API_KEY", "").strip()

_FREE_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_FREE_AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"


def _commercial_url(free_url: str) -> str:
    """Map a free-tier Open-Meteo URL to its paid `customer-` equivalent."""
    return free_url.replace("https://", "https://customer-", 1)


def _resolve_endpoint(env_override: str, free_url: str) -> str:
    explicit = os.environ.get(env_override, "").strip()
    if explicit:
        return explicit
    return _commercial_url(free_url) if OPEN_METEO_API_KEY else free_url


ARCHIVE_URL = _resolve_endpoint("OPEN_METEO_ARCHIVE_URL", _FREE_ARCHIVE_URL)
AIR_QUALITY_URL = _resolve_endpoint("OPEN_METEO_AQ_URL", _FREE_AIR_QUALITY_URL)

# ── MERRA-2 constants ────────────────────────────────────────────────────────
# GES DISC OPeNDAP base for the 2D hourly aerosol dataset (M2T1NXAER).
MERRA2_BASE = "https://goldsmr4.gesdisc.eosdis.nasa.gov/opendap/MERRA2/M2T1NXAER.5.12.4"

# MERRA-2 grid (fixed 0.5° lat × 0.625° lon).
_M2_LAT_STEP = 0.5
_M2_LON_STEP = 0.625
_M2_LAT_MIN = -90.0
_M2_LON_MIN = -180.0
_M2_N_LON = 576  # wrap-around guard

# Aerosol variables to fetch (all in kg/m³; ×1e9 → µg/m³).
# PM2.5 = dust25 + BC + OC + SO4 + seasalt25
# PM10  = dust_total + seasalt_total + BC + OC + SO4
_M2_VARS = ("DUSMASS25", "BCSMASS", "OCSMASS", "SO4SMASS", "SSSMASS25", "DUSMASS", "SSSMASS")

# Open-Meteo's free tier uses a sliding window (by minute and by hour/day). The
# reactive defence is to back off and retry on a 429 rather than drop the row.
# But reacting alone isn't enough for large runs (e.g. ~3.3k permit homes ×2
# endpoints): firing requests as fast as possible trips the minute window almost
# immediately and most rows 429 out. So we *also* pace proactively — a global
# minimum interval between live (non-cached) requests keeps us under the limit so
# 429s become rare, and the retry path is just a safety net.
#
# OPENMETEO_MIN_INTERVAL_S sets the floor between consecutive live requests
# (default 0.7s ≈ 1.4 req/s, comfortably under the ~600/min cap and within the
# ~5000/hr cap). Cached hits never wait, so re-runs over warm cache stay fast.
MAX_RETRIES = 4
BASE_BACKOFF_S = 5.0
MAX_BACKOFF_S = 30.0  # cap a single backoff so one 429 can't blow the per-home deadline

# Proactive pacing — see above. Module-global so it spans both Open-Meteo
# endpoints (archive + air-quality) within a process.
# A paid plan has no daily cap and runs on dedicated servers, so the proactive
# pacing that keeps the FREE tier under its sliding window is dead weight there.
# Default to no pacing when a key is set; an explicit env value still wins.
_RATE_MIN_INTERVAL_S = float(
    os.environ.get("OPENMETEO_MIN_INTERVAL_S", "0.0" if OPEN_METEO_API_KEY else "0.7")
)
_last_live_request_ts = [0.0]  # monotonic timestamp of the last non-cached GET


def _pace_live_request() -> None:
    """Sleep just enough to keep live requests ≥ _RATE_MIN_INTERVAL_S apart.

    Paces off the timestamp of the last *live* request only, so cached responses
    (which leave the timestamp stale) compute wait ≤ 0 and never block.
    """
    if _RATE_MIN_INTERVAL_S <= 0:
        return
    wait = _last_live_request_ts[0] + _RATE_MIN_INTERVAL_S - time.monotonic()
    if wait > 0:
        time.sleep(wait)

DEFAULT_DAILY_VARS: tuple[str, ...] = (
    "precipitation_sum",
    "wind_speed_10m_max",
    "relative_humidity_2m_mean",
    "temperature_2m_max",
    "temperature_2m_min",
    "shortwave_radiation_sum",
)

DEFAULT_AQ_VARS: tuple[str, ...] = ("pm2_5", "pm10")

# Open-Meteo's CAMS air-quality reanalysis starts hard at 2013-01-01. Requests
# with start_date < this floor 400 with "Parameter 'start_date' is out of
# allowed range". We clamp transparently so a 365d lookback for panel-year
# 2013 (which would ask for 2012-12-31) still works — we lose at most one day
# of AQ history vs the requested window.
AQ_MIN_DATE = date(2013, 1, 1)


def _session(cache_dir: Path, expire_after_days: int = 30):
    """Build a requests-cache Session so repeated lat/lon queries hit disk."""
    try:
        from requests_cache import CachedSession
    except ImportError as err:
        raise ImportError(
            "requests-cache is required. Install via `pip install requests-cache`."
        ) from err
    cache_dir.mkdir(parents=True, exist_ok=True)
    return CachedSession(
        cache_name=str(cache_dir / "openmeteo"),
        backend="sqlite",
        expire_after=timedelta(days=expire_after_days),
    )


class CacheMiss(Exception):
    """Raised in cache-only mode when a request isn't already on disk.

    Lets callers harvest everything already cached (zero network, zero quota)
    and skip the rest — e.g. publish a partial result now and fill it in as the
    background grind caches more.
    """


class OpenMeteoQuotaError(RuntimeError):
    """Raised on an Open-Meteo 429 that is a *budget* exhaustion (hourly/daily),
    not a transient minute-window blip.

    Carries ``scope`` ("hourly" | "daily") so the caller can sleep until the
    window resets and retry the same request instead of dropping the row. A
    minute-window 429 is handled inline (short backoff) and never surfaces here.
    """

    def __init__(self, scope: str, reason: str = ""):
        self.scope = scope
        self.reason = reason
        super().__init__(f"Open-Meteo {scope} quota exceeded: {reason}")


def _classify_429(resp) -> tuple[str | None, str]:
    """Map a 429 body to ("hourly"|"daily"|"minutely"|None, raw_reason).

    Open-Meteo returns e.g. {"error":true,"reason":"Hourly API request limit
    exceeded. Please try again in the next hour."}.
    """
    try:
        reason = str((resp.json() or {}).get("reason", ""))
    except Exception:  # noqa: BLE001 — non-JSON body
        reason = resp.text or ""
    low = reason.lower()
    if "hour" in low:
        return "hourly", reason
    if "dai" in low or "per day" in low:
        return "daily", reason
    if "minute" in low:
        return "minutely", reason
    return None, reason


def _get_with_retry(sess, url: str, params: dict, timeout: int, only_if_cached: bool = False):
    """GET with proactive pacing, inline backoff on minute-window 429s, and a
    typed escalation on hourly/daily budget exhaustion.

    Live requests are spaced ≥ _RATE_MIN_INTERVAL_S apart to stay under
    Open-Meteo's sliding-window limit; cached responses skip both the pacing and
    the backoff. A *minute-window* 429 (transient) is retried inline with capped
    backoff. An *hourly/daily* 429 won't clear within seconds, so we raise
    ``OpenMeteoQuotaError`` immediately and let the caller wait for the reset —
    burning retries (and the per-home deadline) on it would just drop the row.

    With ``only_if_cached=True`` the request never touches the network: a cache
    hit is returned, a miss raises ``CacheMiss`` (requests-cache answers an
    uncached ``only_if_cached`` request with a 504 sentinel).
    """
    if OPEN_METEO_API_KEY and "open-meteo.com" in url:
        params = {**params, "apikey": OPEN_METEO_API_KEY}

    for attempt in range(MAX_RETRIES + 1):
        if not only_if_cached:
            _pace_live_request()
        resp = sess.get(url, params=params, timeout=timeout, only_if_cached=only_if_cached)
        if only_if_cached and (resp.status_code == 504 or not getattr(resp, "from_cache", False)):
            raise CacheMiss(url.split("/")[-1])
        if not getattr(resp, "from_cache", False):
            _last_live_request_ts[0] = time.monotonic()
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp

        scope, reason = _classify_429(resp)
        if scope in ("hourly", "daily"):
            raise OpenMeteoQuotaError(scope, reason)

        # Minute-window or unlabeled transient: short inline backoff + retry.
        if attempt >= MAX_RETRIES:
            resp.raise_for_status()  # raises
        retry_after = resp.headers.get("Retry-After")
        if retry_after and retry_after.strip().isdigit():
            wait = min(float(retry_after), MAX_BACKOFF_S)
        else:
            wait = min(BASE_BACKOFF_S * (2 ** attempt), MAX_BACKOFF_S)
        logger.info("Open-Meteo 429 (%s) on %s; retrying in %.0fs (attempt %d/%d)",
                    scope or "transient", url.split("/")[-1], wait, attempt + 1, MAX_RETRIES)
        time.sleep(wait)
    return resp  # unreachable


def _merra2_version(year: int, month: int = 1) -> str:  # noqa: ARG001
    """MERRA-2 base stream label embedded in filenames (100/200/300/400).

    NASA GES DISC also publishes select months as stream 401 (reprocessed data).
    _fetch_merra2_day handles the 400 → 401 fallback on 404.
    """
    if year >= 2011:
        return "400"
    if year >= 2001:
        return "300"
    if year >= 1992:
        return "200"
    return "100"


def _merra2_lat_idx(lat: float) -> int:
    return round((lat - _M2_LAT_MIN) / _M2_LAT_STEP)


def _merra2_lon_idx(lon: float) -> int:
    return int(round((lon - _M2_LON_MIN) / _M2_LON_STEP)) % _M2_N_LON


def _parse_merra2_ascii(text: str) -> dict[str, np.ndarray]:
    """Parse OPeNDAP ASCII response into {varname: array of 24 hourly floats}.

    Actual format returned by GES DISC OPeNDAP (one value per line):
      BCSMASS.BCSMASS[BCSMASS.time=0][BCSMASS.lat=32], 1.55524e-10
      BCSMASS.BCSMASS[BCSMASS.time=60][BCSMASS.lat=32], 1.83888e-10
      ...
    """
    result: dict[str, list[float]] = {}
    # Match lines of the form "VARNAME.VARNAME[...], value"
    _data_re = re.compile(r'^([A-Z0-9]+)\.\1\[.*\],\s*([-\d.eE+naN]+)\s*$')
    for line in text.splitlines():
        m = _data_re.match(line.strip())
        if not m:
            continue
        varname = m.group(1)
        try:
            val = float(m.group(2))
        except ValueError:
            val = float("nan")
        result.setdefault(varname, []).append(val)
    return {k: np.array(v) for k, v in result.items()}


def _merra2_session(token: str, cache_dir: Path):
    """CachedSession with Earthdata Bearer token for MERRA-2 requests.

    Uses a custom session subclass that preserves the Authorization header
    across NASA's cross-host redirects (GES DISC → urs.earthdata.nasa.gov).
    """
    try:
        from requests_cache import CachedSession
    except ImportError as err:
        raise ImportError("requests-cache is required: pip install requests-cache") from err

    class _EarthdataCachedSession(CachedSession):
        def __init__(self, _token: str, **kwargs):
            super().__init__(**kwargs)
            self._token = _token
            self.headers.update({"Authorization": f"Bearer {_token}"})

        def rebuild_auth(self, prepared_request, response):  # noqa: ARG002
            prepared_request.headers["Authorization"] = f"Bearer {self._token}"

    cache_dir.mkdir(parents=True, exist_ok=True)
    return _EarthdataCachedSession(
        _token=token,
        cache_name=str(cache_dir / "merra2"),
        backend="sqlite",
        expire_after=timedelta(days=365),  # reanalysis data is immutable
    )


def _fetch_merra2_day(
    sess,
    lat: float,
    lon: float,
    d: date,
) -> dict[str, float]:
    """Fetch daily-mean PM2.5 and PM10 for one day from MERRA-2 OPeNDAP.

    NASA GES DISC uses stream 400 for most post-2010 dates but has reprocessed
    specific months as stream 401 (e.g. 2020-09, 2021-06 to 2021-09). We try
    stream 400 first and transparently retry with 401 on a 404.
    """
    year, month = d.year, d.month
    date_str = d.strftime("%Y%m%d")
    lat_idx = _merra2_lat_idx(lat)
    lon_idx = _merra2_lon_idx(lon)
    constraint = ",".join(f"{v}[0:23][{lat_idx}][{lon_idx}]" for v in _M2_VARS)

    # Try stream 400 first; fall back to 401 if the file was reprocessed.
    versions_to_try = [_merra2_version(year, month)]
    if versions_to_try[0] == "400":
        versions_to_try.append("401")

    resp = None
    for version in versions_to_try:
        filename = f"MERRA2_{version}.tavg1_2d_aer_Nx.{date_str}.nc4"
        url = f"{MERRA2_BASE}/{year}/{month:02d}/{filename}.ascii?{constraint}"
        for attempt in range(MAX_RETRIES + 1):
            resp = sess.get(url, timeout=(15, 60))  # (connect, read) timeout
            if resp.status_code == 429:
                if attempt >= MAX_RETRIES:
                    resp.raise_for_status()
                time.sleep(BASE_BACKOFF_S * (2 ** attempt))
                continue
            break  # got a non-429 response
        if resp.status_code == 404 and version != versions_to_try[-1]:
            continue  # retry with next version
        resp.raise_for_status()
        break

    arrays = _parse_merra2_ascii(resp.text)

    def _mean(varname: str) -> float:
        return float(np.nanmean(arrays[varname])) if varname in arrays else 0.0

    # All values in kg/m³ → µg/m³
    scale = 1e9
    pm25 = (_mean("DUSMASS25") + _mean("BCSMASS") + _mean("OCSMASS")
            + _mean("SO4SMASS") + _mean("SSSMASS25")) * scale
    pm10 = (_mean("DUSMASS") + _mean("SSSMASS") + _mean("BCSMASS")
            + _mean("OCSMASS") + _mean("SO4SMASS")) * scale
    return {"pm2_5": pm25, "pm10": pm10}


_MERRA2_LAG_DAYS = 90  # reprocessed product typically lags ~3 months behind today


def fetch_air_quality_merra2(
    lat: float,
    lon: float,
    start: date,
    end: date,
    token: str,
    cache_dir: Path = Path(".cache/soiling"),
) -> pd.DataFrame:
    """Fetch daily PM2.5 and PM10 from MERRA-2 (1980-present).

    Requires a NASA Earthdata Bearer token. Coverage has no floor date unlike
    CAMS (which starts 2013-01-01), making it suitable for historical NREL rows.

    Dates within _MERRA2_LAG_DAYS of today are silently skipped (NaN) because
    the reprocessed M2T1NXAER product is not yet available for recent dates.
    """
    cutoff = date.today() - timedelta(days=_MERRA2_LAG_DAYS)
    effective_end = min(end, cutoff)
    if effective_end < start:
        # Entire window is within the lag period — return all NaN
        rows = [
            {"time": pd.Timestamp(start + timedelta(days=i)), "pm2_5": float("nan"), "pm10": float("nan")}
            for i in range((end - start).days + 1)
        ]
        return pd.DataFrame(rows)

    sess = _merra2_session(token, Path(cache_dir))
    rows = []
    current = start
    while current <= end:
        if current > cutoff:
            # Within processing lag — skip without a 404 attempt
            rows.append({"time": pd.Timestamp(current), "pm2_5": float("nan"), "pm10": float("nan")})
            current += timedelta(days=1)
            continue
        try:
            row = _fetch_merra2_day(sess, lat, lon, current)
            row["time"] = pd.Timestamp(current)
            rows.append(row)
        except Exception as exc:
            logger.warning("MERRA-2 fetch failed for %s at (%.3f, %.3f): %s", current, lat, lon, exc)
            rows.append({"time": pd.Timestamp(current), "pm2_5": float("nan"), "pm10": float("nan")})
        current += timedelta(days=1)

    if not rows:
        return pd.DataFrame(columns=["pm2_5", "pm10"])
    df = pd.DataFrame(rows).set_index("time").sort_index()
    return df


def fetch_weather(
    lat: float,
    lon: float,
    start: date,
    end: date,
    daily_vars: Sequence[str] = DEFAULT_DAILY_VARS,
    cache_dir: Path = Path(".cache/soiling"),
    timeout: int = 30,
    cache_only: bool = False,
) -> pd.DataFrame:
    """Fetch daily ERA5 reanalysis for (lat, lon) between start and end dates.

    Returns a DataFrame indexed by date with one column per requested variable.
    With ``cache_only=True`` an uncached (lat, lon, window) raises ``CacheMiss``
    instead of hitting the network.
    """
    sess = _session(Path(cache_dir))
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": ",".join(daily_vars),
        "timezone": "UTC",
    }
    resp = _get_with_retry(sess, ARCHIVE_URL, params, timeout, only_if_cached=cache_only)
    payload = resp.json()
    if "daily" not in payload:
        raise RuntimeError(f"Open-Meteo returned no daily block: {payload}")
    df = pd.DataFrame(payload["daily"])
    df["time"] = pd.to_datetime(df["time"])
    return df.set_index("time").sort_index()


def fetch_air_quality(
    lat: float,
    lon: float,
    start: date,
    end: date,
    aq_vars: Sequence[str] = DEFAULT_AQ_VARS,
    cache_dir: Path = Path(".cache/soiling"),
    timeout: int = 30,
) -> pd.DataFrame:
    """Fetch hourly CAMS air quality and downsample to daily means.

    PM2.5 / PM10 are the dust-deposition proxies most correlated with soiling.
    """
    aq_start = max(start, AQ_MIN_DATE)
    if aq_start > end:
        return pd.DataFrame()
    sess = _session(Path(cache_dir))
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": aq_start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": ",".join(aq_vars),
        "timezone": "UTC",
    }
    resp = _get_with_retry(sess, AIR_QUALITY_URL, params, timeout)
    payload = resp.json()
    if "hourly" not in payload:
        raise RuntimeError(f"Open-Meteo AQ returned no hourly block: {payload}")
    df = pd.DataFrame(payload["hourly"])
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time").sort_index()
    return df.resample("D").mean()


def fetch_combined(
    lat: float,
    lon: float,
    start: date,
    end: date,
    cache_dir: Path = Path(".cache/soiling"),
    cache_only: bool = False,
) -> pd.DataFrame:
    """Convenience: weather + AQ joined on the daily index, NaNs where AQ missing.

    AQ source priority:
      1. MERRA-2 (1980-present) if NASA_EARTHDATA_TOKEN is set in the environment.
         Consistent source for all rows regardless of date — avoids mixing MERRA-2
         and CAMS aerosol models within the same training set.
      2. CAMS via Open-Meteo (2013-present) otherwise. Pre-2013 rows return NaN
         for PM columns (median-imputed downstream).

    With ``cache_only=True`` the weather fetch is cache-only (raises ``CacheMiss``
    on an uncached home) and AQ is skipped entirely — no network at all, so the
    score is weather-only (the dust term is 0). Used to publish a partial result
    now; the full background run later overwrites it with AQ-inclusive scores.
    """
    w = fetch_weather(lat, lon, start, end, cache_dir=cache_dir, cache_only=cache_only)
    if cache_only:
        return w  # offline preview: weather-only, no AQ network calls
    token = os.environ.get("NASA_EARTHDATA_TOKEN")
    try:
        if token:
            aq = fetch_air_quality_merra2(lat, lon, start, end, token=token, cache_dir=cache_dir)
        else:
            aq = fetch_air_quality(lat, lon, start, end, cache_dir=cache_dir)
    except Exception as exc:
        source = "MERRA-2" if token else "CAMS"
        logger.warning("%s AQ fetch failed for (%.4f, %.4f): %s — continuing without it", source, lat, lon, exc)
        aq = pd.DataFrame(index=w.index)
    return w.join(aq, how="left")
