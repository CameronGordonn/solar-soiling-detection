"""Portrait or landscape? Measure module orientation from 6.3 cm county imagery.

WHY THIS IS THE NEXT LAYER. Substring loss swings roughly 3x on orientation alone:
for the same moss line at f=0.5, `substring_shade_loss.py` gives string loss
**21.00% portrait against 7.00% landscape**, because a band along the low edge
crosses every substring of a portrait module but only the bottom substring of a
landscape one. Every downstream dollar figure carries that fork unresolved.

FEASIBILITY, MEASURED FIRST. The obvious objection is that a module is too small to
see. It is not, at the resolution the county service can actually serve. Probing the
six largest AOI arrays at **6.35 cm** (not the 21 cm the label set was built at):
directional autocorrelation shows unambiguous module-scale periodicity, peak
coefficients **0.55 to 0.65** at lags of 17 to 32 px, i.e. 1.1 to 2.0 m. A 60-cell
module is 1.65 x 0.99 m. The grid is there in the pixels.

WHAT THIS DOES.
  1. Fetch the array's footprint at 6.35 cm and mask to the detected polygon.
  2. Sweep autocorrelation over ANGLE, not just the image axes. Arrays sit at
     arbitrary roof azimuths, so an axis-aligned scan measures pitch/cos(theta)
     and silently reports the wrong module size.
  3. Take the two dominant, near-orthogonal directions and their pitches.
  4. **De-foreshorten.** A nadir view compresses the up-slope dimension by
     cos(tilt). Tilt comes from the lidar plane fits already in `roof_planes.csv`,
     so the correction is measured rather than assumed. Skipping it makes every
     tilted portrait array look landscape, which is the failure mode that would
     quietly flip the whole result.
  5. Classify: portrait when the module's LONG axis runs up-slope, landscape when
     it runs across-slope. Report both pitches and the peak strengths so a wrong
     call is visible rather than silent.

WHAT IT DOES NOT DO. It does not decide the moss question. Orientation feeds
`substring_shade_loss.py`; the AOI economics still return zero (see
`docs/PIPELINE_AUDIT_AND_PLAIN_REPORT_20260827.md` §2). This removes a 3x fork from
the physics, it does not reopen the product.

Usage:
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/module_orientation.py \
        --all --resume --out-csv outputs/aoi/santa-cruz-w2-21cm/module_orientation.csv
"""

from __future__ import annotations

import argparse
import io
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from PIL import Image
from rasterio.features import geometry_mask
from rasterio.transform import from_bounds

SERVICE = ("https://sccgis.santacruzcountyca.gov/server/rest/services/"
           "Cache/Imagery_2025/MapServer/export")
#: What the cache can actually deliver. `fetch_scc_imagery.DEFAULT_NATIVE_GSD_M` is
#: 0.208 because that is what the 21 cm label set was built at; do not use it here.
GSD_M = 0.0635
EXPORT_SR = 2227          # NAD83 CA zone III, US survey feet
FT_PER_M = 1.0 / 0.3048
MAX_DIM = 4000

#: 60/72-cell module dimensions, metres. The long axis is what orientation names.
MODULE_LONG_M = 1.65
MODULE_SHORT_M = 0.99
#: A pitch must land within this factor of a plausible module dimension to be used.
PITCH_TOL = 0.30

#: Lag range to search, px. 12 px = 0.76 m, 40 px = 2.54 m at 6.35 cm.
LAG_MIN, LAG_MAX = 10, 42
MIN_PEAK_AC = 0.15
#: Roughly six standard modules at 6.35 cm. This leaves enough overlapping pixels
#: after a one-module shift to see a repeated edge, while not treating a two- or
#: three-module patch as a reliable grid.
MIN_MASK_PX = 2500
#: Below this tilt the roof's azimuth does not define a usable slope direction, so
#: "up-slope" is arbitrary and portrait/landscape cannot be assigned. This matters
#: more than it sounds: the LARGEST arrays in the AOI are flat commercial roofs
#: fitted at 0.1-1.7 degrees, so an unguarded run classifies exactly the cohort it
#: cannot classify, at high confidence, and returns a population split that means
#: nothing. Found on the first run of this script.
MIN_TILT_FOR_SLOPE_DEG = 5.0

CACHE = Path(".cache/soiling/scc_6cm_chips")


def fetch_chip(bounds_3857, pad_m: float = 2.0, array_id: int | None = None):
    """6.35 cm greyscale chip covering `bounds_3857`, plus its padded 3857 bounds."""
    import pyproj

    minx, miny, maxx, maxy = bounds_3857
    minx, miny, maxx, maxy = minx - pad_m, miny - pad_m, maxx + pad_m, maxy + pad_m
    if array_id is not None:
        CACHE.mkdir(parents=True, exist_ok=True)
        f = CACHE / f"{array_id}.png"
        if f.exists():
            im = np.asarray(Image.open(f).convert("L"), dtype=float)
            return im, (minx, miny, maxx, maxy)

    tr = pyproj.Transformer.from_crs(3857, EXPORT_SR, always_xy=True)
    x0, y0 = tr.transform(minx, miny)
    x1, y1 = tr.transform(maxx, maxy)
    gsd_unit = GSD_M * FT_PER_M
    w = int(round((x1 - x0) / gsd_unit))
    h = int(round((y1 - y0) / gsd_unit))
    if max(w, h) > MAX_DIM or min(w, h) < 40:
        return None, None
    r = requests.get(SERVICE, params={
        "bbox": f"{x0},{y0},{x1},{y1}", "bboxSR": EXPORT_SR, "imageSR": EXPORT_SR,
        "size": f"{w},{h}", "format": "png32", "transparent": "false", "f": "image",
    }, timeout=180)
    r.raise_for_status()
    img = Image.open(io.BytesIO(r.content)).convert("L")
    if array_id is not None:
        img.save(CACHE / f"{array_id}.png")
    return np.asarray(img, dtype=float), (minx, miny, maxx, maxy)


def ac_along(x: np.ndarray, theta_deg: float, lags: np.ndarray) -> np.ndarray:
    """Autocorrelation of masked image `x` (NaN outside) along direction `theta_deg`.

    Shifting by a real-valued offset is done by nearest-pixel indexing: at these lags
    the sub-pixel error is under half a pixel and the peak we are looking for is
    ~26 px wide, so interpolation would buy nothing and would smear the very edges
    that carry the signal.
    """
    th = math.radians(theta_deg)
    dx, dy = math.cos(th), math.sin(th)
    h, w = x.shape
    yy, xx = np.mgrid[0:h, 0:w]
    out = np.full(len(lags), np.nan)
    for k, lag in enumerate(lags):
        sx = xx + int(round(dx * lag))
        sy = yy + int(round(dy * lag))
        ok = (sx >= 0) & (sx < w) & (sy >= 0) & (sy < h)
        a = np.where(ok, x[np.clip(sy, 0, h - 1), np.clip(sx, 0, w - 1)], np.nan)
        b = np.where(ok, x, np.nan)
        m = np.isfinite(a) & np.isfinite(b)
        if m.sum() < 500:
            continue
        aa, bb = a[m], b[m]
        sa, sb = aa.std(), bb.std()
        if sa < 1e-9 or sb < 1e-9:
            continue
        out[k] = float(((aa - aa.mean()) * (bb - bb.mean())).mean() / (sa * sb))
    return out


def _peak(ac: np.ndarray, lags: np.ndarray) -> tuple[float, float]:
    """(best lag px, its AC) over interior local maxima. NaN-safe."""
    best, best_ac = np.nan, -np.inf
    for i in range(1, len(ac) - 1):
        if not np.isfinite(ac[i]):
            continue
        left = ac[i - 1] if np.isfinite(ac[i - 1]) else -np.inf
        right = ac[i + 1] if np.isfinite(ac[i + 1]) else -np.inf
        if ac[i] > left and ac[i] > right and ac[i] > best_ac:
            best, best_ac = float(lags[i]), float(ac[i])
    return best, (best_ac if np.isfinite(best_ac) else np.nan)


def measure(img: np.ndarray, mask: np.ndarray, angle_step: int = 10) -> dict:
    """Dominant grid direction, its orthogonal partner, and both pitches in px."""
    x = img.astype(float).copy()
    x[~mask] = np.nan
    x = x - np.nanmean(x)
    lags = np.arange(LAG_MIN, LAG_MAX + 1)

    scan = {}
    for theta in range(0, 180, angle_step):
        lag, ac = _peak(ac_along(x, theta, lags), lags)
        scan[theta] = (lag, ac)

    theta1 = max(scan, key=lambda t: (scan[t][1] if np.isfinite(scan[t][1]) else -np.inf))
    lag1, ac1 = scan[theta1]
    theta2 = (theta1 + 90) % 180
    lag2, ac2 = scan.get(theta2, (np.nan, np.nan))
    return {"theta1_deg": float(theta1), "pitch1_px": lag1, "ac1": ac1,
            "theta2_deg": float(theta2), "pitch2_px": lag2, "ac2": ac2}


def classify(m: dict, tilt_deg: float, azimuth_deg: float) -> dict:
    """Turn two image-space pitches into portrait/landscape.

    The up-slope direction in a nadir image points along the roof azimuth, and only
    that direction is foreshortened, by cos(tilt). Both pitches are therefore
    corrected by how much of each direction lies up-slope.
    """
    out = {
        "orientation": "unknown",
        "orientation_method": "unresolved",
        "reason": "",
        "tilt_deg": tilt_deg,
    }
    if not np.isfinite(m["pitch1_px"]) or not np.isfinite(m["pitch2_px"]):
        out["reason"] = "no periodic peak in one or both directions"
        return out
    if max(m["ac1"], m["ac2"]) < MIN_PEAK_AC:
        out["reason"] = f"weak periodicity (max ac {max(m['ac1'], m['ac2']):.2f})"
        return out
    if not np.isfinite(tilt_deg) or tilt_deg < MIN_TILT_FOR_SLOPE_DEG:
        out["reason"] = (f"tilt {tilt_deg:.1f} deg < {MIN_TILT_FOR_SLOPE_DEG:g}: no "
                         "defined slope direction, so portrait/landscape is meaningless")
        return out

    cos_t = math.cos(math.radians(tilt_deg or 0.0))
    # Image y increases downward; the azimuth-aligned image direction, mod 180.
    up_slope = (90.0 - azimuth_deg) % 180.0

    def corrected(theta_deg, pitch_px):
        # Fraction of this direction that lies up-slope, so a direction across the
        # slope is uncorrected and one straight up it gets the full 1/cos(tilt).
        f = abs(math.cos(math.radians(theta_deg - up_slope)))
        shrink = cos_t * f + 1.0 * (1.0 - f)
        return pitch_px * GSD_M / max(shrink, 1e-6), f

    p1_m, f1 = corrected(m["theta1_deg"], m["pitch1_px"])
    p2_m, f2 = corrected(m["theta2_deg"], m["pitch2_px"])
    out.update(pitch1_m=round(p1_m, 3), pitch2_m=round(p2_m, 3),
               upslope_frac1=round(f1, 3), upslope_frac2=round(f2, 3))

    # Which measured pitch is the module's long axis?
    def plausible(p, target):
        return abs(p - target) / target <= PITCH_TOL

    long_dir = None
    long_method = None
    if plausible(p1_m, MODULE_LONG_M) and not plausible(p2_m, MODULE_LONG_M):
        long_dir, long_f, long_method = m["theta1_deg"], f1, "long_axis_measured"
    elif plausible(p2_m, MODULE_LONG_M) and not plausible(p1_m, MODULE_LONG_M):
        long_dir, long_f, long_method = m["theta2_deg"], f2, "long_axis_measured"
    elif plausible(p1_m, MODULE_LONG_M) and plausible(p2_m, MODULE_LONG_M):
        out["reason"] = "both pitches match a module long axis; ambiguous"
        return out
    else:
        # The short module edge (about 0.99 m) is often visible while the long edge is
        # replaced by row spacing. That still fixes the long axis: it is perpendicular
        # to the measured short edge. Accept this only when exactly one direction is a
        # short-edge-only match; a pitch in the tolerance overlap is deliberately not
        # enough to call either axis.
        short1 = plausible(p1_m, MODULE_SHORT_M) and not plausible(p1_m, MODULE_LONG_M)
        short2 = plausible(p2_m, MODULE_SHORT_M) and not plausible(p2_m, MODULE_LONG_M)
        if short1 ^ short2:
            short_theta = m["theta1_deg"] if short1 else m["theta2_deg"]
            long_dir = (short_theta + 90.0) % 180.0
            long_f = abs(math.cos(math.radians(long_dir - up_slope)))
            long_method = "short_axis_inferred"
        elif short1 and short2:
            out["reason"] = "both pitches match a module short axis; ambiguous"
            return out
        else:
            # Neither pitch is a module dimension. On tilted rows the dominant period
            # is usually ROW spacing (2.2-2.5 m here), which says nothing about layout.
            out["reason"] = (f"no pitch within {PITCH_TOL:.0%} of {MODULE_LONG_M} m or "
                             f"{MODULE_SHORT_M} m; likely row pitch, not module pitch")
            return out
    if long_dir is None:
        out["reason"] = out["reason"] or "pitches indistinguishable"
        return out

    # Portrait = long axis up the slope.
    out["orientation"] = "portrait" if long_f >= 0.5 else "landscape"
    out["orientation_method"] = long_method
    out["long_axis_upslope_frac"] = round(long_f, 3)
    return out


def _unresolved_row(row, reason: str, method: str) -> dict:
    """Record an evaluated array without pretending its layout is known."""
    tilt = row.get("tilt_deg")
    return {
        "id": int(row.id),
        "mask_px": 0,
        "orientation": "unknown",
        "orientation_method": method,
        "reason": reason,
        "tilt_deg": float(tilt) if pd.notna(tilt) else np.nan,
    }


def _write_rows(rows: list[dict], out_csv: Path) -> None:
    """Checkpoint a deterministic table so a county-image run can resume."""
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).drop_duplicates("id", keep="last").sort_values("id").to_csv(
        out_csv, index=False
    )


def _measure_row(row, min_tilt: float, angle_step: int, min_mask_px: int) -> dict:
    """Measure one array, returning an explicit unresolved result on expected limits."""
    fit_ok = bool(row.fit_ok) if pd.notna(row.fit_ok) else False
    tilt = float(row.tilt_deg) if pd.notna(row.tilt_deg) else np.nan
    if not fit_ok:
        return _unresolved_row(row, "no usable lidar plane fit", "no_plane_fit")
    if not np.isfinite(tilt) or tilt < min_tilt:
        return _unresolved_row(
            row,
            f"tilt {tilt:.1f} deg < {min_tilt:g}: no defined slope direction",
            "flat_or_low_tilt",
        )

    try:
        img, bnds = fetch_chip(row.geometry.bounds, array_id=int(row.id))
    except (requests.RequestException, OSError) as exc:
        return _unresolved_row(row, f"county imagery fetch failed: {exc}", "fetch_failed")
    if img is None:
        return _unresolved_row(row, "chip exceeds imagery service limits", "chip_unavailable")

    h, w = img.shape
    mask = geometry_mask([row.geometry], out_shape=(h, w),
                         transform=from_bounds(*bnds, w, h), invert=True)
    if mask.sum() < min_mask_px:
        return _unresolved_row(
            row,
            f"only {int(mask.sum())} image pixels; need {min_mask_px} for a reliable grid",
            "insufficient_pixels",
        )
    m = measure(img, mask, angle_step)
    c = classify(m, float(row.tilt_deg or 0.0), float(row.azimuth_deg or 180.0))
    return {"id": int(row.id), "mask_px": int(mask.sum()), **m, **c}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arrays", type=Path,
                    default=Path("outputs/aoi/santa-cruz-w2-21cm/arrays.geojson"))
    ap.add_argument("--roof-planes", type=Path,
                    default=Path("outputs/aoi/santa-cruz-w2-21cm/roof_planes.csv"))
    ap.add_argument("--limit", type=int, default=40,
                    help="largest N arrays passing --min-tilt; the grid needs area to read")
    ap.add_argument("--all", action="store_true",
                    help="evaluate every detected array; requires --out-csv and can resume")
    ap.add_argument("--min-tilt", type=float, default=MIN_TILT_FOR_SLOPE_DEG,
                    help="skip roofs flatter than this; below it 'up-slope' is undefined "
                         "and portrait/landscape has no meaning")
    ap.add_argument("--angle-step", type=int, default=10)
    ap.add_argument("--out-csv", type=Path, default=None)
    ap.add_argument("--resume", action="store_true",
                    help="reuse completed rows in --out-csv and retry only failed fetches")
    ap.add_argument("--checkpoint-every", type=int, default=25,
                    help="write a resumable --out-csv after this many evaluated arrays")
    ap.add_argument("--workers", type=int, default=1,
                    help="concurrent county-imagery requests; use a small value such as 4")
    ap.add_argument("--min-mask-px", type=int, default=MIN_MASK_PX,
                    help="minimum 6.35 cm panel pixels required for a layout call")
    ap.add_argument("--retry-insufficient-pixels", action="store_true",
                    help="with --resume, re-evaluate only rows previously below --min-mask-px")
    args = ap.parse_args()
    if args.all and args.out_csv is None:
        ap.error("--all requires --out-csv so the long image run is resumable")
    if args.resume and args.out_csv is None:
        ap.error("--resume requires --out-csv")
    if args.limit < 1:
        ap.error("--limit must be at least 1; use --all for the full AOI")
    if args.checkpoint_every < 1:
        ap.error("--checkpoint-every must be at least 1")
    if args.workers < 1:
        ap.error("--workers must be at least 1")
    if args.min_mask_px < 1:
        ap.error("--min-mask-px must be at least 1")

    g = gpd.read_file(args.arrays).to_crs(3857)
    g["area_3857"] = g.area
    planes = pd.read_csv(args.roof_planes)
    g = g.merge(planes[["index", "tilt_deg", "azimuth_deg", "fit_ok"]],
                left_on="id", right_on="index", how="left")
    n_all = len(g)
    eligible = g[g.fit_ok.fillna(False) & (g.tilt_deg >= args.min_tilt)]
    print(f"{len(eligible)} of {n_all} arrays have a good lidar plane fit at tilt >= "
          f"{args.min_tilt:g} deg; the rest cannot be oriented at all")
    sel = (g if args.all else eligible).sort_values("area_3857", ascending=False)
    if not args.all:
        sel = sel.head(args.limit)

    previous: dict[int, dict] = {}
    if args.resume and args.out_csv.is_file():
        prior = pd.read_csv(args.out_csv)
        if "id" not in prior.columns:
            raise ValueError(f"{args.out_csv} is missing required id column")
        previous = {int(row["id"]): row.dropna().to_dict() for _, row in prior.iterrows()}
        print(f"resuming {len(previous)} previously evaluated arrays")

    rows = []
    pending = []
    for _, r in sel.iterrows():
        prior = previous.get(int(r.id))
        retryable = {"fetch_failed"}
        if args.retry_insufficient_pixels:
            retryable.add("insufficient_pixels")
        if prior and prior.get("orientation_method") not in retryable:
            rows.append(prior)
            continue
        pending.append(r)

    print(f"measuring {len(pending)} arrays with {args.workers} worker(s)")
    completed = 0

    def record(row: dict) -> None:
        nonlocal completed
        rows.append(row)
        completed += 1
        if not args.all or completed % args.checkpoint_every == 0:
            if "theta1_deg" in row:
                print(f"{int(row['id']):>6}  {row['orientation']:<9} "
                      f"pitch {row.get('pitch1_m', float('nan')):.2f}/"
                      f"{row.get('pitch2_m', float('nan')):.2f} m  "
                      f"ac {row.get('ac1', float('nan')):.2f}/"
                      f"{row.get('ac2', float('nan')):.2f}  tilt {row['tilt_deg']:.1f}  "
                      f"{row.get('reason', '')}", flush=True)
            else:
                print(f"completed {completed}/{len(pending)}; latest id {int(row['id'])}: "
                      f"{row['orientation_method']}", flush=True)
        if args.out_csv and completed % args.checkpoint_every == 0:
            _write_rows(rows, args.out_csv)
            print(f"checkpointed {len(rows)} arrays to {args.out_csv}", flush=True)

    if args.workers == 1:
        for r in pending:
            record(_measure_row(r, args.min_tilt, args.angle_step, args.min_mask_px))
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(_measure_row, r, args.min_tilt, args.angle_step, args.min_mask_px): r
                for r in pending
            }
            for future in as_completed(futures):
                source_row = futures[future]
                try:
                    record(future.result())
                except Exception as exc:
                    record(_unresolved_row(
                        source_row, f"layout processing failed: {exc}", "processing_failed"
                    ))

    d = pd.DataFrame(rows)
    print("\n" + "=" * 70)
    if d.empty:
        print("no array produced a measurable grid")
        return
    print(d.orientation.value_counts().to_string())
    ok = d[d.orientation != "unknown"]
    print(f"\nclassified {len(ok)}/{len(d)} evaluated arrays  "
          f"({100 * len(ok) / len(d):.0f}%)")
    if "orientation_method" in ok:
        print(ok.orientation_method.value_counts().to_string())
    if not ok.empty:
        print(f"median peak AC on classified arrays: "
              f"{ok[['ac1', 'ac2']].max(axis=1).median():.2f}")
    if args.out_csv:
        _write_rows(rows, args.out_csv)
        print(f"\nwrote {args.out_csv}")


if __name__ == "__main__":
    main()
