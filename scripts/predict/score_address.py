#!/usr/bin/env python3
"""Score the soiling risk + net-$ cleaning value for ANY address (on demand).

The residential funnel: a homeowner gives an address → we geocode it, pull the
local weather history, run the SOMOSclean physics model for a real annual
soiling-loss %, and turn that into a dollar decision. No detection needed (the
owner already knows they have panels), so this works **statewide** today —
Santa Cruz is preloaded (outputs/aoi/santa-cruz-outreach-v1), everywhere else is
computed lazily here.

Coastal/rain-reset addresses (e.g. Santa Cruz) score low; inland/dusty addresses
(Central Valley, Inland Empire) score high — which is where the residential
light-pro opportunity actually is.

Usage:
    PYTHONPATH=. python scripts/predict/score_address.py --address "Fresno, CA" --system-kw 6
    PYTHONPATH=. python scripts/predict/score_address.py --lat 36.74 --lon -119.77 --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import pyproj  # set GDAL/PROJ data dirs before geopandas import

_SHARE = os.path.dirname(pyproj.datadir.get_data_dir())
os.environ.setdefault("PROJ_DATA", pyproj.datadir.get_data_dir())
os.environ.setdefault("GDAL_DATA", os.path.join(_SHARE, "gdal"))

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import geopandas as gpd  # noqa: E402
from shapely.geometry import Point  # noqa: E402

from risk.economics import BASE_RATE, BASE_SUN, array_recommendation  # noqa: E402
from risk.physics_score import score_arrays  # noqa: E402

_GEOCODE_URL = "https://nominatim.openstreetmap.org/search"


def geocode(address: str) -> tuple[float, float, str]:
    q = urllib.parse.urlencode({"q": address, "format": "json", "limit": 1, "countrycodes": "us"})
    req = urllib.request.Request(f"{_GEOCODE_URL}?{q}", headers={"User-Agent": "SolarSoiled/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.load(r)
    if not res:
        raise SystemExit(f"Could not geocode: {address!r}")
    return float(res[0]["lat"]), float(res[0]["lon"]), res[0]["display_name"]


def score_point(lat: float, lon: float, system_kw: float, sun_hours: float, elec_rate: float,
                as_of: date | None = None) -> dict:
    gdf = gpd.GeoDataFrame({"id": [0]}, geometry=[Point(lon, lat)], crs="EPSG:4326")
    scored = score_arrays(gdf, as_of=as_of)
    # Annual-AVERAGE soiling drives the $-math (panels reset on rain, so the year-
    # average is well below the dry-season peak). Terminal = "how dirty right now".
    annual_pct = float(scored["soiling_loss_annual_pct"].iloc[0])
    current_pct = float(scored["soiling_loss_pct"].iloc[0])
    risk = float(scored["risk_score"].iloc[0])
    rec = array_recommendation(annual_pct, system_kw, sun_hours, elec_rate)
    return {
        "lat": round(lat, 5), "lon": round(lon, 5),
        "current_soiling_pct": round(current_pct, 2),
        "annual_avg_soiling_pct": round(annual_pct, 2),
        "risk_score": round(risk, 3),
        "system_kw": system_kw,
        **rec,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--address", help="Street address or place, e.g. 'Fresno, CA'")
    g.add_argument("--latlon", nargs=2, type=float, metavar=("LAT", "LON"))
    p.add_argument("--system-kw", type=float, default=6.0)
    p.add_argument("--sun-hours", type=float, default=BASE_SUN)
    p.add_argument("--elec-rate", type=float, default=BASE_RATE)
    p.add_argument("--json", action="store_true", help="emit JSON only")
    args = p.parse_args(argv)

    if args.address:
        lat, lon, label = geocode(args.address)
    else:
        lat, lon = args.latlon
        label = f"{lat:.5f}, {lon:.5f}"

    out = score_point(lat, lon, args.system_kw, args.sun_hours, args.elec_rate)
    out["query"] = args.address or label
    out["resolved"] = label

    if args.json:
        print(json.dumps(out))
        return out

    action = out["recommended_action"]
    print(f"\n{label}")
    print(f"  current soiling     : {out['current_soiling_pct']:.1f}%   "
          f"annual-avg: {out['annual_avg_soiling_pct']:.1f}%   (risk {out['risk_score']:.2f})")
    print(f"  {args.system_kw:.0f} kW system        : ${out['annual_loss_usd']:.0f}/yr lost (annual-avg soiling)")
    if out["worth_cleaning"]:
        per = out["per_scenario"][action]
        print(f"  recommendation      : {action}  →  net +${out['expected_net_usd']:.0f}/yr "
              f"(cost ${per['cost_usd']:.0f}, payback {out['payback_years']} yr)")
    else:
        print(f"  recommendation      : don't clean — soiling too low to beat the cost here")
    print()
    return out


if __name__ == "__main__":
    main()
