#!/usr/bin/env python3
"""Thread 1 — geocode + SOMOSclean-score the county's permitted solar homes for the dashboard.

Pipeline (see docs/PERMIT_HOMES_DASHBOARD_HANDOFF.md):
  1. Load county solar permits, dedup to one row per parcel (max kW, latest year).
  2. Extract a clean street from the messy ``situs_raw`` (+ ZIP from the description when present).
  3. Geocode ALL parcels in one pass via the **US Census batch geocoder** (free, bulk).
     The permits have no coordinates, so we geocode first, *then* spatially clip to the AOI.
  4. Spatially clip the matched points to the AOI polygon (~1,300 homes).
  5. Score each home with SOMOSclean physics (``score_arrays``) — the dashboard's ``somos_score``.
  6. Write the PRIVATE full output (``permit_homes_scored.geojson``, all attributes).
  7. Emit the REDACTED public artifact (``permit_homes.js`` — only somos_score + system_kw + Point).

The geocode result is cached to ``outputs/permit_homes/geocoded.parquet`` so re-runs skip the
network; pass ``--regeocode`` to force a fresh geocode.

Run (geopandas needs the conda env):
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/score_permit_homes.py
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

# Reuse the battle-tested situs → street extractor from the outreach path.
from scripts.outreach.select_targets_from_permits import clean_street  # noqa: E402

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
CENSUS_BENCHMARK = "Public_AR_Current"
# "CITY CA 95003" or "CITY CA 95003-1234" anywhere in the free text.
CITYZIP_RE = re.compile(r"([A-Z][A-Za-z]+(?:\s[A-Z][A-Za-z]+)?)\s+CA\s+(\d{5})(?:-\d{4})?")


def extract_city_zip(text: str) -> tuple[str | None, str | None]:
    """Pull the first 'CITY CA ZIP' out of the free-text description, if any."""
    if not isinstance(text, str):
        return None, None
    m = CITYZIP_RE.search(text)
    if not m:
        return None, None
    return m.group(1).title(), m.group(2)


def census_batch(rows: list[dict], chunk: int = 1000, max_retries: int = 3) -> pd.DataFrame:
    """Geocode rows via the Census batch endpoint. rows: id/street/city/state/zip dicts.

    Returns a DataFrame with id, matched (bool), lat, lon, matched_address.
    """
    out: list[dict] = []
    n = len(rows)
    for start in range(0, n, chunk):
        batch = rows[start:start + chunk]
        buf = io.StringIO()
        for r in batch:
            # No header; Census wants: id, street, city, state, zip
            buf.write(f'{r["id"]},"{r["street"]}","{r.get("city","") or ""}",'
                      f'{r.get("state","") or ""},{r.get("zip","") or ""}\n')
        payload = buf.getvalue().encode("utf-8")

        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.post(
                    CENSUS_URL,
                    files={"addressFile": ("addrs.csv", payload, "text/csv")},
                    data={"benchmark": CENSUS_BENCHMARK},
                    timeout=300,
                )
                resp.raise_for_status()
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == max_retries:
                    raise
                print(f"  chunk {start//chunk} attempt {attempt} failed ({exc}); retrying…")
                time.sleep(5 * attempt)

        # Response is headerless CSV. Quoting is present, so let pandas parse.
        cols = ["id", "input_address", "match", "match_type",
                "matched_address", "coords", "tigerline_id", "side"]
        rdf = pd.read_csv(io.StringIO(resp.text), header=None, names=cols, dtype=str)
        for _, row in rdf.iterrows():
            matched = str(row["match"]).strip() == "Match"
            lat = lon = None
            if matched and isinstance(row["coords"], str) and "," in row["coords"]:
                lon_s, lat_s = row["coords"].split(",")[:2]  # Census returns lon,lat
                try:
                    lon, lat = float(lon_s), float(lat_s)
                except ValueError:
                    matched = False
            out.append({"id": str(row["id"]), "matched": matched, "lat": lat, "lon": lon,
                        "matched_address": row["matched_address"] if matched else None})
        print(f"  geocoded {min(start+chunk, n)}/{n} "
              f"(running matches: {sum(o['matched'] for o in out)})")
    return pd.DataFrame(out)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--permits", default="data/external/sc_solar_permits.csv", type=Path)
    p.add_argument("--aoi", default="outputs/aoi/santa-cruz-outreach-v1/aoi.geojson", type=Path)
    p.add_argument("--state", default="CA")
    p.add_argument("--outdir", default="outputs/permit_homes", type=Path)
    p.add_argument("--public-js",
                   default=str(REPO.parent / "BBF-Website" / "public" / "tools" / "permit_homes.js"),
                   help="where to write the redacted window.PERMIT_HOMES artifact")
    p.add_argument("--regeocode", action="store_true", help="ignore cached geocode, hit the network")
    p.add_argument("--limit", type=int, help="cap parcels (debug)")
    p.add_argument("--cache-only", action="store_true",
                   help="score only homes whose weather is already cached (no network, no quota) "
                        "and publish that partial result now; safe to run alongside the full grind")
    p.add_argument("--grid-deg", type=float, default=0.1,
                   help="snap home centroids to a lat/lon grid of this spacing so homes in the same "
                        "cell share one weather fetch (default 0.1 deg, <= ERA5's ~0.25 deg native "
                        "resolution, so scores are materially unchanged). Collapses ~1,300 fetches to "
                        "a few dozen — a single-pass run instead of a multi-day free-tier grind. "
                        "Pass 0 to fetch every home's exact coordinate.")
    p.add_argument("--as-of", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(), default=None,
                   help="pin the scoring date (YYYY-MM-DD). Default: start of the current ISO week "
                        "(Monday). Pinning keeps the Open-Meteo date window — and therefore the cache "
                        "key — STABLE across days, so a free-tier grind accumulates instead of "
                        "re-fetching from scratch every midnight when end_date rolls.")
    args = p.parse_args(argv)

    # Snap to this week's Monday unless overridden: a stable window the grind can
    # converge within (~5-6 days of free-tier quota) before it shifts again.
    today = date.today()
    as_of = args.as_of or (today - timedelta(days=today.weekday()))

    import geopandas as gpd  # imported here so --help works without the conda env

    args.outdir.mkdir(parents=True, exist_ok=True)
    geocode_cache = args.outdir / "geocoded.parquet"

    # ---- 1. load + dedup to one row per parcel ----
    df = pd.read_csv(args.permits)
    df = df.sort_values(["apn", "kw", "year"]).groupby("apn", as_index=False).last()
    if args.limit:
        df = df.head(args.limit)
    n_parcels = len(df)

    # ---- 2. clean street (+ city/zip from description) ----
    df["street"] = df["situs_raw"].map(clean_street)
    cz = df["description"].map(extract_city_zip)
    df["city"] = [c for c, _ in cz]
    df["zip"] = [z for _, z in cz]
    df = df[df["street"].notna()].copy().reset_index(drop=True)
    df["pid"] = df.index.astype(str)
    print(f"{n_parcels} parcels → {len(df)} with a usable street "
          f"({df['zip'].notna().sum()} also have a ZIP)")

    # ---- 3. geocode (cached) ----
    if geocode_cache.exists() and not args.regeocode:
        geo = pd.read_parquet(geocode_cache)
        print(f"[cache] reusing {len(geo)} geocoded rows from {geocode_cache}")
    else:
        rows = [{"id": r.pid, "street": r.street, "city": r.city or "",
                 "state": args.state, "zip": r.zip or ""} for r in df.itertuples()]
        print(f"Geocoding {len(rows)} addresses via US Census batch geocoder…")
        geo = census_batch(rows)
        geo.to_parquet(geocode_cache, index=False)
    matched = geo[geo["matched"]].copy()
    print(f"Match rate: {len(matched)}/{len(geo)} = {len(matched)/max(len(geo),1):.1%}")

    merged = df.merge(matched[["id", "lat", "lon", "matched_address"]],
                      left_on="pid", right_on="id", how="inner")

    # ---- 4. spatial clip to AOI ----
    pts = gpd.GeoDataFrame(
        merged, geometry=gpd.points_from_xy(merged["lon"], merged["lat"]), crs="EPSG:4326")
    aoi = gpd.read_file(args.aoi)
    in_aoi = gpd.sjoin(pts, aoi[["geometry"]].to_crs(4326), predicate="within").drop(
        columns=["index_right"])
    print(f"In AOI: {len(in_aoi)}/{len(pts)} matched homes fall inside the AOI polygon")

    # ---- 5. SOMOSclean score ----
    mode = "cache-only (offline, partial)" if args.cache_only else "per-home weather fetch, cached"
    grid_note = f"grid {args.grid_deg} deg" if args.grid_deg > 0 else "exact per-home coords"
    print(f"Scoring {len(in_aoi)} homes with SOMOSclean ({mode}, {grid_note}); scoring date pinned "
          f"to {as_of} (stable cache window)…")
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from risk.physics_score import score_arrays
    scored = score_arrays(in_aoi, as_of=as_of, cache_only=args.cache_only, grid_deg=args.grid_deg)
    ok = scored["risk_score"].notna().sum()
    print(f"Scored OK: {ok}/{len(scored)} (NaN scores dropped from public output)")

    # ---- 6. private full output ----
    priv = args.outdir / "permit_homes_scored.geojson"
    scored.to_file(priv, driver="GeoJSON")
    print(f"[private] {len(scored)} homes → {priv}")

    # ---- 7. redacted public artifact ----
    pub = scored[scored["risk_score"].notna()].copy()
    feats = []
    for r in pub.itertuples():
        props = {"somos_score": round(float(r.risk_score), 4)}
        if pd.notna(getattr(r, "kw", None)):
            props["system_kw"] = round(float(r.kw), 2)
        feats.append({
            "type": "Feature",
            "properties": props,
            "geometry": {"type": "Point",
                         "coordinates": [round(r.geometry.x, 6), round(r.geometry.y, 6)]},
        })
    import json
    fc = {"type": "FeatureCollection", "features": feats}
    js_path = Path(args.public_js)
    js_path.parent.mkdir(parents=True, exist_ok=True)
    js_path.write_text("window.PERMIT_HOMES=" + json.dumps(fc, separators=(",", ":")) + ";\n")
    print(f"[public] {len(feats)} redacted homes → {js_path}")
    print("    fields per feature: somos_score, system_kw? (no address/owner/APN)")


if __name__ == "__main__":
    main()
