"""Spectral screen for biological growth on detected arrays, from NAIP near-infrared.

WHAT THIS CAN AND CANNOT ANSWER -- read before quoting any number from it
------------------------------------------------------------------------
The physics in ``docs/CRAIG_BRIEF_2026-08-19.md`` §4 turns on the *thickness* of a
continuous moss band along the lower edge of a module: f = 0.1 (~16 mm) kills the thesis,
f = 0.2-0.3 (~30-50 mm) makes it. **No aerial imagery we have or can get resolves that.**

    NAIP 60 cm        a 30 mm band spans 0.05 px
    SCC 6 cm          0.38 px
    Street View 20 m  2.73 px   <- the only instrument in reach, and it is marginal

So this script does **not** substitute for the on-foot / Street View thickness survey. It
answers the *other* question -- Option 3 in the plan, blind prevalence -- automatically and
spectrally instead of by eye over two days: **how many arrays carry biological growth at a
scale large enough to see at all?**

That is worth having on its own. Chlorophyll reflects strongly in the near-infrared while a
clean PV module absorbs broadly across visible and NIR, so growth raises NDVI over the panel
well above the clean-module baseline. It cannot see a hairline band; it can see an array
that is substantially colonised, and those are exactly the roofs worth spending a Street
View request on.

A NULL RESULT HERE IS NOT A NULL RESULT FOR THE THESIS. It means "no array in this AOI is
grossly colonised", which leaves the thin-band case wide open. Say it that way.

MEASURED LIMIT: NAIP IS TOO COARSE FOR RESIDENTIAL ARRAYS (2026-08-19)
----------------------------------------------------------------------
Run on the Santa Cruz AOI this returns roughly **one usable pixel per array**, and the
reason is arithmetic, not a bug:

    array pixels at NAIP 60 cm    p10 17   p50 44   p90 109
    ...after the 25% edge shrink  p50 ~25, and in practice 1-2 survive masking

A median 15 m2 residential array is 6.7 x 6.7 px before shrinking. Every surviving pixel is
a mix of module, rail, roof and orthorectification smear, so the panel-vs-surroundings
contrast measured here (-0.004 vs +0.009 NDVI) is noise, not signal.

**Where it does work: 45 arrays in this AOI exceed 200 m2 and carry ~985 NAIP pixels each.**
That is a real measurement. It is also, independently, the population the tilt work points
at -- C&I ballasted flat roofs soil ~2x steep residential ones
(``src/risk/tilt_response.py``). Point this at large arrays; do not quote it on houses.

For residential the resolution needed is the 6 cm SCC imagery (p50 2,587 px/array), which
is RGB with no NIR band, so a spectral read is not available there at all. That is a real
dead end, recorded so nobody re-derives it.

    PYTHONPATH=. python3 scripts/analyze/nir_growth_probe.py \
        --arrays outputs/aoi/santa-cruz-w2-21cm/arrays.geojson \
        --planes outputs/aoi/santa-cruz-w2-21cm/roof_planes.csv \
        --out    outputs/aoi/santa-cruz-w2-21cm/nir_growth.csv

Imagery is 4-band NAIP from the Microsoft Planetary Computer, the same STAC endpoint
``src/risk/location_features.py`` already uses. The tiles cached under ``data/raw/naip/``
are RGB only -- the NIR band was dropped at fetch time -- so this re-reads from source.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

#: NDVI at or above this is vegetation by any conventional reading. Clean PV sits near or
#: below zero. Deliberately conservative: we would rather miss marginal growth than report
#: a colonised roof that is really a bright rail or an orthorectification smear.
NDVI_VEG_THRESHOLD = 0.20

#: Shrink before sampling, for the same reason as everywhere else in this pipeline: the
#: polygon comes from 2025 imagery and NAIP is a different flight, so the edge pixels are a
#: mix of panel, rail, roof and whatever the orthorectification smeared in.
SHRINK_FRAC = 0.25

MIN_PIXELS = 6


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arrays", required=True)
    ap.add_argument("--planes", default=None,
                    help="roof_planes.csv, to carry tilt/azimuth onto the output")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--year-min", type=int, default=2020)
    args = ap.parse_args()

    import geopandas as gpd
    import planetary_computer
    import pystac_client
    import rasterio
    import shapely
    from rasterio.mask import mask as rio_mask

    g = gpd.read_file(args.arrays)
    if args.limit:
        g = g.iloc[: args.limit]
    minx, miny, maxx, maxy = g.total_bounds

    cat = pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=planetary_computer.sign_inplace)
    items = list(cat.search(collections=["naip"],
                            bbox=[minx, miny, maxx, maxy]).items())
    items = [i for i in items if i.datetime.year >= args.year_min]
    if not items:
        print("no NAIP items cover the AOI at that year floor", file=sys.stderr)
        return 1
    items.sort(key=lambda i: i.datetime, reverse=True)
    print(f"{len(items)} NAIP items; newest {items[0].datetime.date()}")

    rows = []
    for item in items:
        href = item.assets["image"].href
        with rasterio.open(href) as src:
            if src.count < 4:
                print(f"  {item.id}: only {src.count} bands, skipping")
                continue
            gm = g.to_crs(src.crs)
            done = {r["index"] for r in rows}
            for idx, geom in gm.geometry.items():
                if idx in done:
                    continue
                # a metric shrink is not available in an arbitrary CRS; use a fraction of
                # the polygon's own scale, which is what SHRINK_FRAC means here
                b = geom.bounds
                r = SHRINK_FRAC * 0.5 * float(np.hypot(b[2] - b[0], b[3] - b[1]))
                inner = geom.buffer(-r)
                if inner.is_empty or inner.area <= 0:
                    inner = geom
                halo = geom.buffer(3 * r).difference(geom)
                try:
                    arr, _ = rio_mask(src, [inner], crop=True, filled=False)
                    ring, _ = rio_mask(src, [halo], crop=True, filled=False)
                except Exception:
                    continue

                def ndvi(a):
                    """NDVI of the unmasked, non-black pixels. None if too few.

                    rio_mask returns a MaskedArray; fancy-indexing one keeps the mask and
                    can yield an empty compressed result, so drop to plain ndarrays first.
                    """
                    if a is None or a.shape[0] < 4:
                        return None
                    valid = ~np.ma.getmaskarray(a[0])
                    red = np.ma.filled(a[0], 0).astype(float)
                    nir = np.ma.filled(a[3], 0).astype(float)
                    m = valid & ((red + nir) > 0)
                    if int(m.sum()) < MIN_PIXELS:
                        return None
                    v = (nir[m] - red[m]) / (nir[m] + red[m])
                    v = np.asarray(v, dtype=float)
                    v = v[np.isfinite(v)]
                    return v if v.size >= MIN_PIXELS else None

                va, vr = ndvi(arr), ndvi(ring)
                if va is None:
                    continue
                rows.append({
                    "index": int(idx), "n_px": int(va.size),
                    "ndvi_p50": round(float(np.median(va)), 4),
                    "ndvi_p90": round(float(np.percentile(va, 90)), 4),
                    "veg_px_frac": round(float((va >= NDVI_VEG_THRESHOLD).mean()), 4),
                    "roof_ndvi_p50": round(float(np.median(vr)), 4) if vr is not None else None,
                    "naip_date": str(item.datetime.date()),
                })
        if len(rows) >= len(g):
            break

    if not rows:
        print("no arrays sampled", file=sys.stderr)
        return 1
    df = pd.DataFrame(rows)
    if args.planes:
        pl = pd.read_csv(args.planes)
        df = df.merge(pl.loc[pl.fit_ok.astype(bool),
                             ["index", "tilt_deg", "azimuth_deg"]], on="index", how="left")
    df["ndvi_excess"] = (df.ndvi_p50 - df.roof_ndvi_p50).round(4)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)

    print(f"\nwrote {args.out}  ({len(df)} arrays sampled, {df.n_px.median():.0f} px median)")
    print("\n=== NDVI over the panels ===")
    print(df.ndvi_p50.describe([.1, .5, .9, .99]).round(4).to_string())
    print("\n=== instrument control: panels must read far below their surroundings ===")
    print(f"  panel  NDVI p50 {df.ndvi_p50.median():+.3f}")
    print(f"  roof   NDVI p50 {df.roof_ndvi_p50.median():+.3f}")
    print("  (if these are equal the sampling is off-target and nothing below means anything)")
    n_veg = int((df.veg_px_frac >= 0.10).sum())
    print(f"\n=== PREVALENCE: arrays with >=10% of panel pixels reading NDVI >= "
          f"{NDVI_VEG_THRESHOLD} ===")
    lo, hi = _wilson(n_veg, len(df))
    print(f"  {n_veg} / {len(df)} = {100*n_veg/len(df):.2f}%   95% CI [{100*lo:.2f}%, {100*hi:.2f}%]")
    print("\nREMINDER: this bounds GROSS colonisation only. A 30 mm edge band is 0.05 NAIP")
    print("pixels wide and is invisible here, so a low number does NOT clear the thesis.")
    if n_veg:
        print("\ntop candidates for a Street View look:")
        print(df.nlargest(8, "veg_px_frac")[
            ["index", "veg_px_frac", "ndvi_p50", "roof_ndvi_p50", "tilt_deg"]
        ].to_string(index=False))
    return 0


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval -- correct near 0, where a normal approximation is not."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (max(0.0, c - h), min(1.0, c + h))


if __name__ == "__main__":
    raise SystemExit(main())
