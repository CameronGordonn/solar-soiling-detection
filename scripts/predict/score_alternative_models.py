"""Score Santa Cruz arrays with SOMOSclean and Kimber physics models.

Reads cached Open-Meteo weather data from the SQLite cache written by the
pipeline (no new API calls), runs SOMOSclean and Kimber trajectories on each
array centroid, and writes:

  outputs/outreach/alt_scores.json       — {array_id: {somos, kimber}} mapping
  BBF-Website/public/tools/arrays_data.js — updated with somos_score + kimber_score

The dashboard now lives in the BBF site (../BBF-Website/public/tools), not the
retired solarsoiled-landing/ GitHub Pages surface.

Usage:
  PYTHONPATH=. python scripts/predict/score_alternative_models.py \\
      --partner-id santa-cruz-outreach-v1 \\
      --landing-dir /path/to/tools-dir   # default: ../BBF-Website/public/tools
"""

from __future__ import annotations

import argparse
import json
import logging
import pickle
import re
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from risk.labels import somosclean_eqd_trajectory, kimber_soiling_ratio

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

_SOMOS_PARAMS = dict(
    sl_sat=0.08,
    k=15.0,
    heavy_rain_mm=10.0,
    rain_min_mm=1.0,
    pm10_dust_threshold=50.0,
    pm10_dust_scale=0.02,
)
_KIMBER_PARAMS = dict(deposition_per_pm25=0.0006, rain_clean_mm=1.0)

_LOOKBACK_DAYS = 180


# ── cache reader ──────────────────────────────────────────────────────────────

def _load_weather_cache(cache_dir: Path) -> list[dict]:
    """Extract all successful Open-Meteo weather responses from the SQLite cache.

    Returns a list of dicts with keys: lat, lon, end_date, daily (DataFrame).
    """
    db = cache_dir / "openmeteo.sqlite"
    if not db.exists():
        raise FileNotFoundError(f"Open-Meteo cache not found: {db}")

    entries = []
    con = sqlite3.connect(db)
    rows = con.execute("SELECT value FROM responses").fetchall()
    con.close()

    for (blob,) in rows:
        try:
            data = pickle.loads(blob)
            if data.get("status_code") != 200:
                continue
            url = data.get("url", "")
            # Only weather (not CAMS AQ) entries
            if "archive-api.open-meteo.com" not in url:
                continue
            content = json.loads(data.get("_content", b"{}"))
            daily_raw = content.get("daily")
            if not daily_raw or "time" not in daily_raw:
                continue
            # Parse end_date from URL
            m_end = re.search(r"end_date=([0-9\-]+)", url)
            if not m_end:
                continue
            daily_df = pd.DataFrame(daily_raw)
            daily_df["time"] = pd.to_datetime(daily_df["time"])
            daily_df = daily_df.set_index("time")
            entries.append({
                "lat": content["latitude"],
                "lon": content["longitude"],
                "end_date": m_end.group(1),
                "daily": daily_df,
            })
        except Exception:
            pass

    log.info("Loaded %d weather entries from cache", len(entries))
    return entries


def _load_aq_cache(cache_dir: Path) -> list[dict]:
    """Extract CAMS air-quality (pm2_5, pm10) responses from cache."""
    db = cache_dir / "openmeteo.sqlite"
    if not db.exists():
        return []

    entries = []
    con = sqlite3.connect(db)
    rows = con.execute("SELECT value FROM responses").fetchall()
    con.close()

    for (blob,) in rows:
        try:
            data = pickle.loads(blob)
            if data.get("status_code") != 200:
                continue
            url = data.get("url", "")
            if "air-quality-api" not in url and "cams" not in url.lower():
                continue
            content = json.loads(data.get("_content", b"{}"))
            hourly = content.get("hourly") or content.get("daily")
            if not hourly or "time" not in hourly:
                continue
            m_end = re.search(r"end_date=([0-9\-]+)", url)
            if not m_end:
                continue
            df = pd.DataFrame(hourly)
            df["time"] = pd.to_datetime(df["time"])
            # Resample hourly to daily mean if needed
            df = df.set_index("time")
            if df.index.hour.any():
                df = df.resample("D").mean()
            entries.append({
                "lat": content.get("latitude", 0),
                "lon": content.get("longitude", 0),
                "end_date": m_end.group(1),
                "daily": df,
            })
        except Exception:
            pass

    log.info("Loaded %d AQ entries from cache", len(entries))
    return entries


def _find_closest(entries: list[dict], lat: float, lon: float,
                  end_date: str | None = None, tol: float = 1.0) -> pd.DataFrame | None:
    """Return the daily DataFrame from the closest cached entry.

    end_date filtering is optional — pass None to ignore (used for AQ whose
    end_date differs from weather due to CAMS lag).  tol is in degrees (~110 km).
    """
    best = None
    best_dist = float("inf")
    for e in entries:
        if end_date is not None and e["end_date"] != end_date:
            continue
        dist = (e["lat"] - lat) ** 2 + (e["lon"] - lon) ** 2
        if dist < best_dist and dist < tol ** 2:
            best_dist = dist
            best = e["daily"]
    return best


_SC_PM25_DEFAULT = 8.0   # µg/m³ — typical Santa Cruz coastal background
_SC_PM10_DEFAULT = 20.0  # µg/m³

def _score_one(weather: pd.DataFrame, aq: pd.DataFrame | None) -> dict:
    """Run SOMOSclean and Kimber on pre-loaded daily DataFrames."""
    daily = weather.copy()
    if aq is not None:
        aq_aligned = aq.reindex(daily.index)
        for col in ("pm2_5", "pm10"):
            if col in aq_aligned.columns:
                daily[col] = aq_aligned[col]

    # Fill missing AQ columns with Santa Cruz background values so both models run.
    if "pm2_5" not in daily.columns:
        daily["pm2_5"] = _SC_PM25_DEFAULT
    if "pm10" not in daily.columns:
        daily["pm10"] = _SC_PM10_DEFAULT
    # Fill NaNs at the tail (AQ lags weather by ~30 days) with column median.
    daily["pm2_5"] = daily["pm2_5"].fillna(daily["pm2_5"].median())
    daily["pm10"] = daily["pm10"].fillna(daily["pm10"].median())

    _, sl_series = somosclean_eqd_trajectory(daily, **_SOMOS_PARAMS)
    somos_risk = round(min(1.0, float(sl_series.iloc[-1]) / _SOMOS_PARAMS["sl_sat"]), 4)

    ratio_series = kimber_soiling_ratio(daily, **_KIMBER_PARAMS)
    kimber_risk = round(float(1.0 - ratio_series.iloc[-1]), 4)

    return {"somos": somos_risk, "kimber": kimber_risk}


# ── main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--partner-id", default="santa-cruz-outreach-v1")
    ap.add_argument(
        "--landing-dir",
        type=Path,
        default=ROOT.parent / "BBF-Website" / "public" / "tools",
    )
    ap.add_argument(
        "--cache-dir",
        type=Path,
        default=ROOT / ".cache" / "soiling",
    )
    ap.add_argument(
        "--end-date",
        default=None,
        help="Filter to cached entries with this end_date (auto-detected if omitted)",
    )
    args = ap.parse_args(argv)

    risk_path = ROOT / "outputs" / "aoi" / args.partner_id / "risk.geojson"
    if not risk_path.exists():
        ap.error(f"risk.geojson not found at {risk_path}. Run the pipeline first.")

    out_dir = ROOT / "outputs" / "outreach"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── load weather from SQLite cache — no HTTP calls ─────────────────────
    weather_entries = _load_weather_cache(args.cache_dir)
    aq_entries = _load_aq_cache(args.cache_dir)

    if not weather_entries:
        ap.error("No cached weather entries found. Run the pipeline first to warm the cache.")

    # Pick the end_date with the most entries (= the pipeline run date)
    if args.end_date:
        end_date = args.end_date
    else:
        from collections import Counter
        counts = Counter(e["end_date"] for e in weather_entries)
        end_date, n_entries = counts.most_common(1)[0]
        log.info("Auto-detected end_date=%s (%d weather entries)", end_date, n_entries)

    # ── load arrays ────────────────────────────────────────────────────────
    log.info("Loading risk.geojson from %s", risk_path)
    gdf = gpd.read_file(risk_path)

    arrays_path = ROOT / "outputs" / "aoi" / args.partner_id / "arrays.geojson"
    gdf_geom = gpd.read_file(arrays_path) if arrays_path.exists() else gdf
    centroids = gdf_geom.to_crs("EPSG:4326").geometry.centroid

    n = len(gdf)
    alt_scores: dict[str, dict] = {}
    n_found = 0
    n_missing = 0

    for i, (row, centroid) in enumerate(zip(gdf.itertuples(), centroids)):
        array_id = str(getattr(row, "array_id", i))
        lat, lon = centroid.y, centroid.x

        weather_df = _find_closest(weather_entries, lat, lon, end_date)
        if weather_df is None:
            log.warning("No cached weather for array %s (%.4f, %.4f)", array_id, lat, lon)
            alt_scores[array_id] = {"somos": None, "kimber": None}
            n_missing += 1
            continue

        # AQ end_date differs from weather (CAMS lags ~30 days) — match by lat/lon only
        aq_df = _find_closest(aq_entries, lat, lon, end_date=None) if aq_entries else None

        try:
            scores = _score_one(weather_df, aq_df)
            alt_scores[array_id] = scores
            n_found += 1
        except Exception as exc:
            log.warning("Array %s scoring failed: %s", array_id, exc)
            alt_scores[array_id] = {"somos": None, "kimber": None}
            n_missing += 1

        if (i + 1) % 50 == 0 or (i + 1) == n:
            log.info("Processed %d / %d arrays (%d scored, %d missing)", i + 1, n, n_found, n_missing)

    log.info("Done: %d scored, %d missing (no cache match)", n_found, n_missing)

    scores_path = out_dir / "alt_scores.json"
    scores_path.write_text(json.dumps(alt_scores, indent=2))
    log.info("Alt scores written to %s", scores_path)

    # ── regenerate arrays_data.js ──────────────────────────────────────────
    arrays_js = args.landing_dir / "arrays_data.js"
    if not arrays_js.exists():
        log.error("arrays_data.js not found at %s — skipping JS update", arrays_js)
        return

    text = arrays_js.read_text()
    prefix = "window.FALLBACK_ARRAYS="
    if not text.startswith(prefix):
        log.error("arrays_data.js does not start with expected prefix; skipping")
        return

    fc = json.loads(text[len(prefix):].rstrip().rstrip(";"))
    for feat in fc["features"]:
        aid = str(feat["properties"].get("array_id", ""))
        s = alt_scores.get(aid, {})
        feat["properties"]["somos_score"] = s.get("somos")
        feat["properties"]["kimber_score"] = s.get("kimber")

    arrays_js.write_text(prefix + json.dumps(fc, separators=(",", ":")))
    log.info("arrays_data.js updated (%d features)", len(fc["features"]))


if __name__ == "__main__":
    main()
