"""Does Santa Cruz's rain regime BUILD bottom-edge soiling bands, or wash them off?

The mechanism, from Zhao et al. 2021, "Characterization of Soiling Bands on the
Bottom Edges of PV Modules" (Front. Energy Res. 9:665411):

  * A module frame stands 1-3 mm proud of the front glass, so the bottom edge is a
    stagnant trap. Low tilt makes the trap deeper in effect.
  * LIGHT-TO-MODERATE rain is the band's friend: it mobilises dust off the open
    glass, carries it down, and deposits it in the trap where "the strength of the
    raindrops is weakened ... raindrops have little effect on particles deposited
    at the bottom." Bands "gradually become thicker" through such seasons.
  * Only HEAVY rain clears the band.

That inverts the intuition our recoverable-soiling model is built on, where all
rain is a reset. It also means a location's band risk is not annual rainfall but
the RATIO of band-building to band-clearing events.

`src/risk/recovery.py` already uses a >=10 mm/day threshold for a soiling reset,
measured for the dust channel in this AOI; it is reused here as the heavy-rain
cut so the two modules stay consistent. The light band is 0.2-10 mm/day: enough
to mobilise and transport, not enough to flush.

This compares our AOI against the locations where bands were actually observed.
If Santa Cruz looks like Guangzhou (bands seen despite 1,800 mm/yr) the mechanism
transfers. If it looks nothing like any of them, it does not, and that is a real
answer.

Usage:
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/band_rain_regime.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import requests

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
CACHE = Path(".cache/soiling/band_rain")

#: Heavy-rain reset threshold and trace floor. READ from the modules that own them,
#: not re-typed. These used to be three independent literals across the tree, each
#: with a comment claiming it matched the others.
from src.risk.band_soiling import HEAVY_RAIN_MM as HEAVY_MM, TRACE_MM  # noqa: E402

SITES = {
    # ours
    "Santa Cruz CA (AOI)": (36.974, -122.031),
    "Berkeley CA (microbial study)": (37.871, -122.273),
    # where bottom-edge bands were documented (Zhao et al. 2021)
    "Xi'an CN (bands, ~3 deg tilt)": (34.34, 108.94),
    "Kaifeng CN (bands, ~4 deg tilt)": (34.80, 114.30),
    "Guangzhou CN (bands, 1800 mm/yr)": (23.13, 113.26),
    # contrast: the dry-farm site where NO standing layer was found
    "Arbuckle CA (PVDAQ 2107)": (39.02, -122.06),
}


def daily_precip(lat: float, lon: float, start: str, end: str) -> pd.Series:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{lat:.3f}_{lon:.3f}_{start}_{end}.parquet"
    if f.exists():
        return pd.read_parquet(f)["precipitation_sum"]
    r = requests.get(ARCHIVE, params={
        "latitude": lat, "longitude": lon, "start_date": start, "end_date": end,
        "daily": "precipitation_sum", "timezone": "UTC",
    }, timeout=180)
    r.raise_for_status()
    d = pd.DataFrame(r.json()["daily"])
    d.index = pd.to_datetime(d.pop("time"))
    d = d.astype(float)
    d.to_parquet(f)
    return d["precipitation_sum"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--out-json", type=Path, default=None)
    args = ap.parse_args()

    rows = []
    for name, (lat, lon) in SITES.items():
        p = daily_precip(lat, lon, args.start, args.end).dropna()
        yrs = len(p) / 365.25
        light = int(((p >= TRACE_MM) & (p < HEAVY_MM)).sum())
        heavy = int((p >= HEAVY_MM).sum())
        rows.append(dict(
            site=name, mm_yr=round(p.sum() / yrs), light_yr=round(light / yrs, 1),
            heavy_yr=round(heavy / yrs, 1),
            ratio=round(light / heavy, 2) if heavy else float("inf"),
            dry_yr=round(int((p < TRACE_MM).sum()) / yrs),
        ))
    d = pd.DataFrame(rows)
    print(f"Daily precipitation {args.start} to {args.end}, per year\n")
    print(f"  light = {TRACE_MM}-{HEAVY_MM} mm/day (BUILDS the band)")
    print(f"  heavy = >= {HEAVY_MM} mm/day (CLEARS it)\n")
    print(d.to_string(index=False))

    sc = d[d.site.str.startswith("Santa Cruz")].iloc[0]
    bands = d[d.site.str.contains("bands")]
    print(f"\nSanta Cruz build:clear ratio = {sc.ratio}")
    print(f"Documented-band sites range   = {bands.ratio.min()} to {bands.ratio.max()}")
    print()
    if sc.ratio >= bands.ratio.min():
        print("  Santa Cruz sits INSIDE the range where bands were observed.")
        print("  The mechanism transfers on rain regime. Low-tilt roofs here should")
        print("  accumulate bottom-edge bands, and our >=10 mm 'reset' logic is")
        print("  measuring the WRONG channel for them: those resets clear the open")
        print("  glass while the trap keeps filling.")
    else:
        print("  Santa Cruz has proportionally MORE band-clearing rain than the sites")
        print("  where bands were documented. The mechanism may not transfer; treat")
        print("  band persistence here as unproven and prioritise the ground survey.")
    print("\n  Caveat: this compares rain regime only. Dust supply, air chemistry and")
    print("  the organic/biological component all differ, and Zhao et al. did not")
    print("  separate mineral from biological soiling at all.")

    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        json.dump(d.to_dict("records"), open(args.out_json, "w"), indent=2)
        print(f"\nwrote {args.out_json}")


if __name__ == "__main__":
    main()
