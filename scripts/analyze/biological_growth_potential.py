"""Time of Wetness: the physically correct free predictor for BIOLOGICAL soiling.

Why this and not rainfall. `band_rain_regime.py` shows Santa Cruz sits below every
site where MINERAL bottom-edge bands were documented, so that mechanism transfers
weakly. But moss, lichen and sub-aerial biofilm are not deposited, they GROW, and
what limits growth is not rain totals but how many hours a year the surface is wet
enough for metabolism.

Time of Wetness (TOW) is the standard measure of exactly that. ISO 9223 defines it
as hours with RH >= 80% and T > 0 C, and it is the accepted driver of atmospheric
corrosion and of sub-aerial biofilm colonisation. It is computable for free from
Open-Meteo hourly reanalysis anywhere on earth.

Two refinements this adds over bare ISO 9223, both mattering in a Mediterranean
coastal climate:

  * DEW hours. A panel radiatively cools below air temperature at night, so it
    wets when the sky is clear even with no rain and moderate RH. Approximated as
    dewpoint depression < 2 C. Coastal CA fog and marine layer make this large and
    it is invisible to a rainfall-based model.
  * GROWTH-WEIGHTED TOW. Wet hours only build biomass when it is warm enough to
    metabolise. Weighted by a simple Q10 response above a 5 C floor, so a wet
    winter hour at 6 C counts far less than a wet spring hour at 18 C.

What this does NOT do: it does not predict biomass or power loss. It is a relative
site-suitability index. Calibrating it to actual moss requires the ground survey,
which is the whole point of `rank_moss_candidates.py`.

Per-roof modifiers (canopy shade, low tilt, north aspect) are NOT applied here.
This is the REGIONAL term. Combining regional TOW with the lidar-derived per-roof
terms is what makes a per-roof prediction; see the writeup.

Usage:
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/biological_growth_potential.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
CACHE = Path(".cache/soiling/tow")

RH_WET = 80.0          # ISO 9223 wetness threshold, %
T_FLOOR_C = 0.0        # ISO 9223 temperature floor
DEW_DEPRESSION_C = 2.0 # within this of dewpoint -> condensation likely
T_GROWTH_FLOOR_C = 5.0 # below this, negligible biological growth
Q10 = 2.0              # metabolic rate doubling per 10 C
T_REF_C = 20.0

SITES = {
    "Santa Cruz CA (AOI)": (36.974, -122.031),
    "Berkeley CA (microbial study, biofilm CONFIRMED)": (37.871, -122.273),
    "Sao Paulo BR (Shirakawa, 11% loss @18mo)": (-23.55, -46.63),
    "Xi'an CN (mineral bands)": (34.34, 108.94),
    "Guangzhou CN (mineral bands, humid)": (23.13, 113.26),
    "Arbuckle CA (PVDAQ 2107, NO standing layer)": (39.02, -122.06),
    "Phoenix AZ (desert reference)": (33.45, -112.07),
}


def hourly(lat: float, lon: float, start: str, end: str) -> pd.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{lat:.3f}_{lon:.3f}_{start}_{end}.parquet"
    if f.exists():
        return pd.read_parquet(f)
    r = requests.get(ARCHIVE, params={
        "latitude": lat, "longitude": lon, "start_date": start, "end_date": end,
        "hourly": "temperature_2m,relative_humidity_2m,dew_point_2m",
        "timezone": "UTC",
    }, timeout=300)
    r.raise_for_status()
    d = pd.DataFrame(r.json()["hourly"])
    d.index = pd.to_datetime(d.pop("time"))
    d = d.astype(float)
    d.to_parquet(f)
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--out-json", type=Path, default=None)
    args = ap.parse_args()

    rows = []
    for name, (lat, lon) in SITES.items():
        h = hourly(lat, lon, args.start, args.end).dropna()
        yrs = len(h) / (365.25 * 24)
        t, rh, dp = h.temperature_2m, h.relative_humidity_2m, h.dew_point_2m

        iso = (rh >= RH_WET) & (t > T_FLOOR_C)
        dew = (t - dp) < DEW_DEPRESSION_C
        wet = iso | dew

        # Q10 growth weighting, zeroed below the metabolic floor.
        w = np.where(t >= T_GROWTH_FLOOR_C, Q10 ** ((t - T_REF_C) / 10.0), 0.0)
        rows.append(dict(
            site=name,
            tow_iso=round(int(iso.sum()) / yrs),
            dew_h=round(int(dew.sum()) / yrs),
            wet_h=round(int(wet.sum()) / yrs),
            wet_pct=round(100 * wet.mean(), 1),
            growth_tow=round(float((wet * w).sum()) / yrs),
        ))

    d = pd.DataFrame(rows).sort_values("growth_tow", ascending=False)
    print(f"Hourly reanalysis {args.start} to {args.end}, per year\n")
    print("  tow_iso    = ISO 9223 wet hours (RH>=80%, T>0C)")
    print("  dew_h      = hours within 2C of dewpoint (condensation likely)")
    print("  wet_h      = union of the two = total wet hours")
    print("  growth_tow = wet hours weighted by Q10 metabolic rate\n")
    print(d.to_string(index=False))

    sc = d[d.site.str.startswith("Santa Cruz")].iloc[0]
    bk = d[d.site.str.startswith("Berkeley")].iloc[0]
    ar = d[d.site.str.startswith("Arbuckle")].iloc[0]
    sp = d[d.site.str.startswith("Sao Paulo")].iloc[0]

    print(f"\nSanta Cruz growth-weighted TOW  {sc.growth_tow} h/yr")
    print(f"  vs Berkeley (biofilm confirmed on panels)  {bk.growth_tow}"
          f"   ratio {sc.growth_tow / bk.growth_tow:.2f}")
    print(f"  vs Sao Paulo (11% loss measured @18mo)     {sp.growth_tow}"
          f"   ratio {sc.growth_tow / sp.growth_tow:.2f}")
    print(f"  vs Arbuckle (NO standing layer, 8 yrs)     {ar.growth_tow}"
          f"   ratio {sc.growth_tow / ar.growth_tow:.2f}")
    print()
    if sc.growth_tow > ar.growth_tow:
        print(f"  Santa Cruz is {sc.growth_tow / ar.growth_tow:.1f}x wetter (growth-weighted)")
        print("  than the one site where we MEASURED no standing layer. That result")
        print("  therefore does not transfer here, and the AOI's own moss question")
        print("  stays genuinely open rather than answered-by-proxy.")
    print("\n  This is a RELATIVE index. It says where growth is favoured, not how")
    print("  much biomass or power loss results. Only the ground survey closes that.")

    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        json.dump(d.to_dict("records"), open(args.out_json, "w"), indent=2)
        print(f"\nwrote {args.out_json}")


if __name__ == "__main__":
    main()
