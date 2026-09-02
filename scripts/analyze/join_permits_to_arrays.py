#!/usr/bin/env python3
"""Join Santa Cruz solar permits to detected arrays via parcel (APN).

For a partner AOI:
  1. fetch county parcel polygons intersecting the AOI bbox (ArcGIS REST),
  2. assign each detected array the APN of the parcel it sits on,
  3. attach the parcel's permitted system **kW** (from ingest_permits.py output),
     writing ``permit_kw`` onto risk.geojson — recommend.py then uses the real kW
     instead of the detection-area estimate, and
  4. report **detection recall**: of solar permits whose parcel is in the AOI, how
     many have a detected array (and which permitted installs we missed → a dent
     in risk M1 and a relabeling queue for Stage 1).

PII: writes under outputs/ and data/external/ (both gitignored). Never publish.

Usage:
    PYTHONPATH=. python scripts/analyze/join_permits_to_arrays.py \\
        --partner-id santa-cruz-outreach-v1 \\
        --permits data/external/sc_solar_permits.csv
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# Make GDAL/PROJ find their data dirs (conda env ships them; GDAL search path
# otherwise misses share/proj and errors on CRS lookups).
import pyproj  # noqa: E402

_SHARE = os.path.dirname(pyproj.datadir.get_data_dir())
os.environ.setdefault("PROJ_DATA", pyproj.datadir.get_data_dir())
os.environ.setdefault("PROJ_LIB", pyproj.datadir.get_data_dir())
os.environ.setdefault("GDAL_DATA", os.path.join(_SHARE, "gdal"))

import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PARCELS_LAYER = ("https://sccgis.santacruzcountyca.gov/server/rest/services/"
                 "gisweb/MapServer/28/query")
PAGE = 2000


def apn_base(s) -> str | None:
    """Normalize an APN to its 8-digit book-page-parcel base for matching."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    digits = "".join(ch for ch in str(s) if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else None


def fetch_aoi_parcels(bbox, cache: Path) -> gpd.GeoDataFrame:
    if cache.is_file():
        print(f"[cache] parcels <- {cache}")
        return gpd.read_file(cache)
    xmin, ymin, xmax, ymax = bbox
    common = {
        "where": "1=1",
        "geometry": f"{xmin},{ymin},{xmax},{ymax}",
        "geometryType": "esriGeometryEnvelope", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "APN,APNNODASH", "outSR": "4326", "f": "geojson",
    }
    feats: list[dict] = []
    offset = 0
    while True:
        q = dict(common, resultOffset=str(offset), resultRecordCount=str(PAGE))
        url = PARCELS_LAYER + "?" + urllib.parse.urlencode(q)
        with urllib.request.urlopen(url, timeout=90) as r:
            page = json.load(r)
        got = page.get("features", [])
        feats.extend(got)
        print(f"[fetch] parcels {offset}..{offset+len(got)}", flush=True)
        if len(got) < PAGE:
            break
        offset += PAGE
        time.sleep(0.3)
    if not feats:
        raise SystemExit("No parcels returned for the AOI bbox — check the bbox CRS (must be lon/lat).")
    gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")
    cache.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(cache, driver="GeoJSON")
    print(f"[ok] {len(gdf)} parcels -> {cache}")
    return gdf


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--partner-id", default="santa-cruz-outreach-v1")
    ap.add_argument("--permits", default="data/external/sc_solar_permits.csv", type=Path)
    ap.add_argument("--out-dir", default="outputs/economics", type=Path)
    args = ap.parse_args(argv)

    aoi = REPO / "outputs" / "aoi" / args.partner_id
    risk = gpd.read_file(aoi / "risk.geojson")
    risk4326 = risk.to_crs(4326)  # risk.geojson is stored in UTM; parcels are lon/lat
    print(f"arrays: {len(risk)}  | AOI bbox (4326): {[round(v,5) for v in risk4326.total_bounds]}")

    cache = REPO / "data" / "external" / "santa_cruz_parcels" / f"aoi_{args.partner_id}.geojson"
    parcels = fetch_aoi_parcels(risk4326.total_bounds, cache)
    parcels["apn_base"] = parcels["APN"].map(apn_base)

    # Each array -> the parcel under its representative interior point (in 4326).
    pts = risk4326.copy()
    pts["geometry"] = risk4326.representative_point()
    joined = gpd.sjoin(pts, parcels[["apn_base", "APN", "geometry"]], how="left", predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")].reindex(risk.index)
    risk["apn_base"] = joined["apn_base"].values
    risk["apn"] = joined["APN"].values
    matched = risk["apn_base"].notna().sum()
    print(f"arrays matched to a parcel: {matched}/{len(risk)}")

    # Permit kW per parcel (largest system on the parcel).
    permits = pd.read_csv(args.permits)
    permits["apn_base"] = permits["apn"].map(apn_base)
    pk = (permits[permits["kw"].notna()].sort_values("kw")
          .groupby("apn_base", as_index=False).last()[["apn_base", "kw", "date_issued", "mount"]]
          .rename(columns={"kw": "permit_kw", "date_issued": "permit_date"}))
    risk = risk.merge(pk, on="apn_base", how="left")
    with_kw = risk["permit_kw"].notna().sum()
    print(f"arrays stamped with a permitted kW: {with_kw}/{len(risk)}")

    out_geo = aoi / "risk_permitkw.geojson"
    risk.drop(columns=[c for c in risk.columns if c.startswith("index_")], errors="ignore").to_file(out_geo, driver="GeoJSON")
    print(f"[ok] enriched risk -> {out_geo}")

    # Detection recall: solar permits whose parcel is in the AOI.
    aoi_apns = set(parcels["apn_base"].dropna())
    aoi_permits = permits[permits["apn_base"].isin(aoi_apns)].drop_duplicates("apn_base")
    detected_apns = set(risk["apn_base"].dropna())
    found = aoi_permits["apn_base"].isin(detected_apns).sum()
    n = len(aoi_permits)
    recall = found / n if n else float("nan")

    lines = ["# Solar permits vs. detected arrays (AOI cross-check)", "",
             f"AOI: **{args.partner_id}**  ·  detected arrays: {len(risk)}  ·  "
             f"parcels in AOI bbox: {len(parcels):,}", "",
             f"- Solar-permitted (2016+) parcels in the AOI bbox: **{n}**",
             f"- ...that have a detected array: **{found}**",
             f"- Detected arrays matched to a parcel: {matched}/{len(risk)} "
             f"({with_kw} carry a 2016+ permitted kW)", "",
             "## Read this carefully — the raw overlap is **not** a clean recall number",
             "",
             "- **Coverage denominator is unknown.** The bbox is ~18 km² but only the tiled "
             "NAIP subset was actually run through detection; permitted parcels never imaged "
             "can't be detected. A true recall needs the tile_index footprint as the denominator.",
             "- **Permits start in 2016; CA's residential solar boom was 2013–2016.** Many "
             "detected arrays are legitimately older installs with no permit in this dataset — "
             "so a low permit-overlap is expected and not, by itself, a detector failure.",
             "",
             f"## Useful output: a relabel/validation queue ({n-found} parcels)",
             "",
             "The permitted-but-undetected parcels are concrete locations to check — each is "
             "either a real miss (Stage-1 relabel target, risk M1) or outside the tiled area. "
             "Cross-referencing against the tiled footprint turns this into a precise recall "
             "metric and a prioritized relabeling batch.",
             f"\n_raw overlap (lower bound, uncorrected): {found}/{n} = {recall:.0%}_"]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rep = args.out_dir / "permit_detection_recall.md"
    rep.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[ok] recall report -> {rep}")


if __name__ == "__main__":
    main()
