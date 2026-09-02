"""Select the top-N highest-priority outreach targets in a given AOI.

Pipeline outputs (risk.geojson + arrays.geojson + array_recommendations.json)
are scored, joined to county parcel data for owner addresses, and ranked.

Usage:
    python scripts/20_select_outreach_targets.py \\
        --partner-id santa-cruz-outreach-v1 \\
        --top 50 \\
        --out outputs/outreach/santa_cruz_top50.csv

    # Dry-run: prints table without writing files
    python scripts/20_select_outreach_targets.py \\
        --partner-id santa-cruz-outreach-v1 \\
        --dry-run

Parcel data:
    The script downloads the Santa Cruz County parcel shapefile from the county
    open-data portal on first run and caches it to data/external/santa_cruz_parcels/.
    This is free public data. Pass --parcel-shp to use a local file instead.

Address fallback:
    Arrays not matched to a parcel are reverse-geocoded via Nominatim (OpenStreetMap,
    free, 1 req/s rate limit). Owner name is set to "Solar Panel Owner".
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import shape

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from solarsoiled.paths import AoiPaths, AOI_ROOT
from risk.economics import BASE_SOILING_PCT, array_recommendation, system_kw_from_area

_PARCEL_CACHE = REPO_ROOT / "data" / "external" / "santa_cruz_parcels" / "parcels.geojson"

_PARCEL_INSTRUCTIONS = """
To get owner names and mailing addresses, download the Santa Cruz County parcel shapefile:
  1. Go to https://gis.santacruzcounty.us/gisweb/  (or search "Santa Cruz County GIS open data")
  2. Download the Assessor Parcels layer as a Shapefile or GeoJSON
  3. Pass the path with:  --parcel-shp /path/to/parcels.shp
Without it, all addresses will be reverse-geocoded via Nominatim (no owner names).
"""

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
_NOMINATIM_HEADERS = {"User-Agent": "SolarSoiled-outreach/1.0 (solarsoil.app@gmail.com)"}


# ── parcel data ──────────────────────────────────────────────────────────────

def _download_parcels(parcel_url: str) -> gpd.GeoDataFrame:
    """Download parcel GeoJSON and cache locally."""
    _PARCEL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading Santa Cruz parcel data → {_PARCEL_CACHE} (one-time, ~30 MB) …")
    r = requests.get(parcel_url, timeout=120)
    r.raise_for_status()
    _PARCEL_CACHE.write_bytes(r.content)
    print("  Download complete.")
    return gpd.read_file(str(_PARCEL_CACHE))


def _load_parcels(parcel_shp: str | None) -> gpd.GeoDataFrame | None:
    """Load parcel data from a local file or cached file."""
    if parcel_shp:
        p = Path(parcel_shp)
        if not p.exists():
            print(f"WARNING: --parcel-shp {parcel_shp} not found — skipping parcel join", file=sys.stderr)
            return None
        return gpd.read_file(str(p))
    if _PARCEL_CACHE.exists():
        return gpd.read_file(str(_PARCEL_CACHE))
    print(_PARCEL_INSTRUCTIONS)
    return None


def _extract_parcel_address(row: Any) -> tuple[str, str]:
    """Pull owner name + mailing address from a parcel row. Adjust column names if your
    shapefile uses different attribute names."""
    cols = {c.lower(): c for c in row.index}

    def get(*candidates: str) -> str:
        for c in candidates:
            orig = cols.get(c)
            if orig and pd.notna(row[orig]) and str(row[orig]).strip():
                return str(row[orig]).strip()
        return ""

    owner = get("owner_name", "ownername", "owner", "own_name") or "Solar Panel Owner"
    addr1 = get("situs_addr", "situs", "site_addr", "address", "addr")
    city = get("situs_city", "city", "situs_cty")
    state = get("situs_state", "state") or "CA"
    zipcode = get("situs_zip", "zip", "zip_code", "zipcode")

    parts = [p for p in [addr1, city, state, zipcode] if p]
    address = ", ".join(parts) if parts else ""
    return owner, address


# ── geocoding fallback ────────────────────────────────────────────────────────

def _nominatim_reverse(lat: float, lon: float) -> str:
    """Return a street address string from a lat/lon pair via Nominatim."""
    try:
        r = requests.get(
            _NOMINATIM_URL,
            params={"lat": lat, "lon": lon, "format": "jsonv2"},
            headers=_NOMINATIM_HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        addr = data.get("address", {})
        parts = []
        house = addr.get("house_number", "")
        road = addr.get("road", "")
        if house and road:
            parts.append(f"{house} {road}")
        elif road:
            parts.append(road)
        city = addr.get("city") or addr.get("town") or addr.get("village") or ""
        if city:
            parts.append(city)
        state = addr.get("state", "CA")
        postcode = addr.get("postcode", "")
        parts.append(state)
        if postcode:
            parts.append(postcode)
        return ", ".join(parts)
    except Exception:
        return ""


# ── scoring ───────────────────────────────────────────────────────────────────

def _compute_outreach_score(risk_score: float, detection_confidence: float | None) -> float:
    conf = detection_confidence if detection_confidence is not None else 0.5
    return round(0.6 * risk_score + 0.4 * conf, 4)


def _loss_pct(feat, risk_score: float) -> float:
    """Annual soiling-loss % for an array: prefer the regression head, then physics.

    The ``risk_score * RISK_TO_LOSS_PCT`` fallback was REMOVED on 2026-08-09 — it
    multiplied a calibrated classification probability by 8 to manufacture a magnitude
    (see src/risk/loss_model.py). ``risk_score`` is now unused for dollars; an array with
    no predicted loss falls back to the measured coastal-CA median so that a missing
    model is visible as a constant rather than disguised as a per-array prediction.
    """
    for col in ("loss_pct_p50", "pred_loss_pct", "soiling_loss_annual_pct", "soiling_loss_pct"):
        v = feat.get(col)
        if v is not None and pd.notna(v):
            return float(v)
    return BASE_SOILING_PCT


# ── main ──────────────────────────────────────────────────────────────────────

def select_targets(
    *,
    partner_id: str,
    top: int = 50,
    parcel_shp: str | None = None,
    dashboard_base_url: str = "https://betterbehaviorfoundation.com/tools/dashboard.html",
    out_csv: Path | None = None,
    out_geojson: Path | None = None,
    min_kw: float | None = None,
    max_kw: float | None = None,
    dry_run: bool = False,
) -> pd.DataFrame:
    paths = AoiPaths(partner_id)

    if not paths.risk_geojson.exists():
        sys.exit(
            f"ERROR: {paths.risk_geojson} not found.\n"
            f"Run the pipeline first:\n"
            f"  solarsoiled run --aoi santa_cruz --weights production "
            f"--soiling-model soiling_production --last-cleaned 2025-12-01 "
            f"--partner-id {partner_id}"
        )

    # Load risk GeoJSON and ensure WGS84 so centroid lat/lon are in degrees
    risk_gdf = gpd.read_file(str(paths.risk_geojson))
    if risk_gdf.crs is None or risk_gdf.crs.to_epsg() != 4326:
        risk_gdf = risk_gdf.to_crs(epsg=4326)
    if "array_id" not in risk_gdf.columns:
        risk_gdf["array_id"] = range(len(risk_gdf))

    # Load arrays.geojson for detection confidence
    conf_map: dict[int, float] = {}
    if paths.arrays_geojson.exists():
        arrays_gdf = gpd.read_file(str(paths.arrays_geojson))
        if "array_id" in arrays_gdf.columns and "confidence" in arrays_gdf.columns:
            conf_map = dict(zip(arrays_gdf["array_id"].astype(int), arrays_gdf["confidence"].astype(float)))

    # Load per-array recommendations
    recs_map: dict[int, dict] = {}
    if paths.array_recommendations_json.exists():
        recs = json.loads(paths.array_recommendations_json.read_text())
        for r in recs:
            recs_map[int(r.get("array_id", -1))] = r

    # Build candidate rows
    rows = []
    for _, feat in risk_gdf.iterrows():
        aid = int(feat.get("array_id", feat.name))
        risk_score = float(feat.get("risk_score", 0.0))
        area_m2 = float(feat.get("area_m2", 0.0))
        conf = conf_map.get(aid)
        rec = recs_map.get(aid, {})
        action = rec.get("action", "monitor")
        priority = rec.get("priority", "low")
        cleaning_window = rec.get("cleaning_window", "")

        centroid = feat.geometry.centroid
        lat, lon = centroid.y, centroid.x

        # Net-$ targeting: rank by recoverable dollars, not raw risk. Prefer a
        # permitted kW, else estimate from detection area; loss% from the model.
        pk = feat.get("permit_kw")
        permit_kw = float(pk) if pk is not None and pd.notna(pk) and float(pk) > 0 else None
        system_kw = permit_kw if permit_kw else system_kw_from_area(area_m2)
        loss_pct = _loss_pct(feat, risk_score)
        net_usd = array_recommendation(loss_pct, system_kw)["expected_net_usd"] if system_kw else 0.0

        rows.append({
            "array_id": aid,
            "risk_score": risk_score,
            "expected_net_usd": round(net_usd, 2),
            "system_kw": round(system_kw, 2) if system_kw else None,
            "loss_pct": round(loss_pct, 2),
            "area_m2": round(area_m2, 1),
            "detection_confidence": conf,
            "outreach_score": _compute_outreach_score(risk_score, conf),
            "action": action,
            "priority": priority,
            "cleaning_window": cleaning_window,
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "geometry": feat.geometry,
        })

    df = pd.DataFrame(rows)

    # Optional system-size filter — e.g. a homeowner mailer targets residential
    # 4–15 kW; large commercial sites route to a different (direct) channel.
    if min_kw is not None:
        df = df[df["system_kw"].fillna(0) >= min_kw]
    if max_kw is not None:
        df = df[df["system_kw"].fillna(1e9) <= max_kw]
    if (min_kw is not None or max_kw is not None):
        print(f"Size filter [{min_kw or 0}–{max_kw or '∞'} kW]: {len(df)} arrays remain")

    # Filter to actionable arrays first; fall back to all if too few
    actionable = df[(df["action"] == "clean") | (df["risk_score"] >= 0.5)]
    if len(actionable) < top:
        actionable = df
    # Rank by recoverable net-$ (the dollars at stake), risk as tiebreaker.
    df_top = actionable.sort_values(
        ["expected_net_usd", "outreach_score"], ascending=False
    ).head(top).copy()

    # Spatial join to parcels
    parcels = _load_parcels(parcel_shp)
    df_top["owner_name"] = "Solar Panel Owner"
    df_top["mailing_address"] = ""

    if parcels is not None:
        if parcels.crs and parcels.crs.to_epsg() != 4326:
            parcels = parcels.to_crs(epsg=4326)
        target_gdf = gpd.GeoDataFrame(df_top, geometry="geometry", crs=4326)
        joined = gpd.sjoin(target_gdf, parcels, how="left", predicate="within")
        for idx, row in joined.iterrows():
            if row.get("index_right") is not None:
                owner, address = _extract_parcel_address(row)
                df_top.loc[idx, "owner_name"] = owner or "Solar Panel Owner"
                df_top.loc[idx, "mailing_address"] = address

    # Nominatim fallback for unmatched rows
    unmatched = df_top[df_top["mailing_address"] == ""].index.tolist()
    if unmatched:
        print(f"Reverse-geocoding {len(unmatched)} arrays via Nominatim …")
        for idx in unmatched:
            row = df_top.loc[idx]
            addr = _nominatim_reverse(row["lat"], row["lon"])
            df_top.loc[idx, "mailing_address"] = addr
            time.sleep(1.1)  # Nominatim rate limit: 1 req/s

    # QR URL
    df_top["qr_url"] = df_top["array_id"].apply(
        lambda aid: f"{dashboard_base_url}?id={aid}"
    )

    # Clean up for output
    out_cols = [
        "array_id", "expected_net_usd", "system_kw", "loss_pct", "outreach_score",
        "risk_score", "area_m2", "priority", "action", "cleaning_window",
        "owner_name", "mailing_address", "lat", "lon", "qr_url",
    ]
    result = df_top[out_cols].reset_index(drop=True)

    if dry_run:
        pd.set_option("display.max_colwidth", 40)
        print(f"\nTop-{top} outreach targets (dry run — no files written):")
        print(result.to_string(index=False))
        return result

    if out_csv is None:
        out_csv = REPO_ROOT / "outputs" / "outreach" / f"{partner_id}_top{top}.csv"
    if out_geojson is None:
        out_geojson = REPO_ROOT / "outputs" / "outreach" / f"{partner_id}_top{top}.geojson"

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(str(out_csv), index=False)
    print(f"Wrote {len(result)} targets → {out_csv}")

    # Write GeoJSON with geometry
    geo_df = gpd.GeoDataFrame(
        df_top[out_cols].reset_index(drop=True),
        geometry=df_top["geometry"].reset_index(drop=True),
        crs="EPSG:4326",
    )
    geo_df.to_file(str(out_geojson), driver="GeoJSON")
    print(f"Wrote GeoJSON → {out_geojson}")

    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Select top-N outreach targets from pipeline outputs.")
    parser.add_argument("--partner-id", required=True, help="Partner AOI ID (e.g. santa-cruz-outreach-v1)")
    parser.add_argument("--top", type=int, default=50, help="Number of targets to select (default: 50)")
    parser.add_argument("--parcel-shp", help="Path to local parcel shapefile/GeoJSON (optional; downloads from county portal if omitted)")
    parser.add_argument("--dashboard-url", default="https://betterbehaviorfoundation.com/tools/dashboard.html", help="Base URL for QR codes")
    parser.add_argument("--out-csv", help="Output CSV path (default: outputs/outreach/<partner_id>_top<N>.csv)")
    parser.add_argument("--out-geojson", help="Output GeoJSON path")
    parser.add_argument("--min-kw", type=float, help="Drop arrays below this system size (e.g. residential floor)")
    parser.add_argument("--max-kw", type=float, help="Drop arrays above this size (e.g. 15 for a homeowner mailer)")
    parser.add_argument("--dry-run", action="store_true", help="Print table without writing files")
    args = parser.parse_args(argv)

    select_targets(
        partner_id=args.partner_id,
        top=args.top,
        parcel_shp=args.parcel_shp,
        dashboard_base_url=args.dashboard_url,
        out_csv=Path(args.out_csv) if args.out_csv else None,
        out_geojson=Path(args.out_geojson) if args.out_geojson else None,
        min_kw=args.min_kw,
        max_kw=args.max_kw,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
