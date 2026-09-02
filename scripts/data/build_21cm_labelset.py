"""Package the fetched 21cm SCC tiles into a Roboflow-ready labeling batch (+ integrity QA).

Consumes scripts/data/fetch_scc_imagery.py output (data/interim/scc_2025/*_21cm_3857.tif) and the
existing 2022 NAIP polygon labels, and emits everything the two-annotator labeling sprint needs:

  <out>/images/<tile>.png            lossless RGB at native ~0.21 m/px  (NEVER jpg -- see §resolution)
  <out>/seeds/<tile>.txt             existing 2022 polygons, YOLO-seg normalized (train tiles only)
  <out>/tile_index_21cm.json         CRS + affine + bounds for every emitted tile (data-pipeline rule)
  <out>/assignment.csv               tile -> annotator -> split -> mode (fresh|seeded), east/west blocks
  <out>/qa_report.json               per-tile integrity checks; upload is BLOCKED if any FAIL

Why the seeds transfer with no coordinate math: each 21cm tile is reprojected onto the EXACT 3857
footprint of its NAIP tile (fetch_scc_imagery.py:georeference_and_reproject), so normalized 0-1
polygon coords are identical between the two rasters. We assert that footprint equality per tile
rather than assuming it.

Split policy (docs/LABELING_SPRINT_21CM.md):
  - val/test -> mode=fresh   : NO seeds uploaded. These are the measurement instrument; anchoring
                               annotators to 60cm-era polygons biases every metric we report.
  - train    -> mode=seeded  : seeds uploaded as Roboflow predictions to confirm/correct/extend.

Usage:
  PYTHONPATH=. conda run -n solar-soiling python scripts/data/build_21cm_labelset.py
  PYTHONPATH=. conda run -n solar-soiling python scripts/data/build_21cm_labelset.py --limit 5
"""

from pathlib import Path
import argparse
import csv
import json
import logging

import numpy as np
import rasterio

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

GRID_CRS = "EPSG:3857"
BOUNDS_TOL_M = 0.5      # 3857 footprint agreement between 21cm raster and tile_index
MIN_GSD_M = 0.30        # emitted pixels must be finer than this (else something downsampled us)
MERC_K_LAT37 = 1.2523   # 1/cos(37.0N): 3857 units -> ground metres at Santa Cruz


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scc-dir", default="data/interim/scc_2025", type=Path)
    p.add_argument("--naip-root", default="data/yolo/naip", type=Path)
    p.add_argument("--tile-index", default="data/interim/tile_index.json", type=Path)
    p.add_argument("--out-dir", default="data/interim/scc21_labelset", type=Path)
    p.add_argument("--annotators", default="cameron,akshitha")
    p.add_argument("--limit", type=int, default=0, help="cap tiles (smoke test)")
    return p.parse_args(argv)


def load_tile_splits(naip_root: Path):
    """tile_000123 -> split, from the Roboflow-mangled filenames (tile_000123_png.rf.<hash>.jpg)."""
    out = {}
    for split in ("train", "val", "test"):
        for f in (naip_root / "images" / split).glob("*"):
            out[f.name.split("_png.rf.")[0]] = (split, f.name)
    return out


def read_seed_polygons(naip_root: Path, split: str, image_name: str):
    """Existing 2022 polygons as normalized YOLO-seg lines. Coords transfer 1:1 (same footprint)."""
    lab = (naip_root / "labels" / split / image_name).with_suffix(".txt")
    if not lab.exists():
        return []
    lines = []
    for raw in lab.read_text().splitlines():
        parts = raw.split()
        if len(parts) < 7:
            continue
        coords = np.asarray(parts[1:], dtype=float)
        if coords.size % 2 or not np.all((coords >= -0.01) & (coords <= 1.01)):
            logger.warning(f"{lab.name}: skipping malformed/out-of-range polygon")
            continue
        lines.append("0 " + " ".join(f"{v:.6f}" for v in np.clip(coords, 0.0, 1.0)))
    return lines


def check_and_write_png(tif: Path, footprint, png_out: Path):
    """Integrity-check one 21cm raster and write it losslessly. Returns (checks, meta)."""
    from PIL import Image

    with rasterio.open(tif) as src:
        checks, meta = {}, {}
        checks["crs_is_3857"] = (src.crs is not None and src.crs.to_epsg() == 3857)

        err = max(abs(a - b) for a, b in zip(src.bounds, footprint))
        checks["bounds_match_naip_tile"] = err <= BOUNDS_TOL_M
        meta["bounds_err_m"] = round(err, 3)

        gsd_units = abs(src.transform.a)
        gsd_ground = gsd_units / MERC_K_LAT37
        checks["native_resolution_kept"] = gsd_ground <= MIN_GSD_M
        meta.update(gsd_3857_units=round(gsd_units, 5), gsd_ground_m=round(gsd_ground, 4),
                    width=src.width, height=src.height)

        # A 512px NAIP tile re-fetched at 21cm must be ~2.3x more pixels; anything near 512 means
        # the fetch silently came back at the cached/lower zoom level.
        checks["pixel_count_gained"] = src.width >= 900 and src.height >= 900

        arr = src.read()
        checks["has_rgb"] = arr.shape[0] >= 3
        arr = arr[:3]
        checks["dtype_uint8"] = (arr.dtype == np.uint8)
        # A flat/near-flat tile means the endpoint served a blank or "no data" image.
        meta["pixel_std"] = round(float(arr.std()), 2)
        checks["not_blank"] = float(arr.std()) > 3.0

    if all(checks.values()):
        png_out.parent.mkdir(parents=True, exist_ok=True)
        # Reuse an existing PNG of the right size: re-encoding 249 tiles costs minutes, and reruns
        # are usually about the assignment, not the pixels. Wrong-size means stale -> rewrite.
        stale = True
        if png_out.exists():
            with Image.open(png_out) as prev:
                stale = prev.size != (meta["width"], meta["height"])
        if stale:
            # PNG = lossless. JPEG re-encoding at this GSD smears the 6-13px panels we hunt for.
            Image.fromarray(np.transpose(arr, (1, 2, 0))).save(png_out, format="PNG", optimize=True)
        meta["png_kb"] = round(png_out.stat().st_size / 1024)
    return checks, meta


def assign(tiles_by_split, annotators):
    """Split train/val/test evenly between the two annotators, each half contiguous west->east.

    Balancing *within* each split matters more than one global cut: it stops either annotator from
    owning an entire measurement split, so a systematic quirk in one person's labeling shows up in
    both halves of val rather than defining val outright. Within a split the halves stay contiguous,
    which keeps shared tile edges (the main disagreement source) down to one seam per split.

    The odd tile alternates between annotators across splits so the totals stay level.
    """
    owner, first = {}, 0
    for split in sorted(tiles_by_split):
        tiles = tiles_by_split[split]              # already sorted west->east
        # The larger half always goes to annotators[first], and `first` flips after every odd
        # split — so across three odd splits the extra tile lands 2/1, not 3/0.
        half = (len(tiles) + 1) // 2
        for i, (t, _) in enumerate(tiles):
            owner[t] = annotators[first] if i < half else annotators[1 - first]
        if len(tiles) % 2:
            first = 1 - first
    return owner


def main(argv=None):
    args = parse_args(argv)
    idx = json.loads(args.tile_index.read_text())["tiles"]
    splits = load_tile_splits(args.naip_root)
    annotators = [a.strip() for a in args.annotators.split(",")]
    if len(annotators) != 2:
        raise SystemExit("--annotators expects exactly two names")

    tifs = sorted(args.scc_dir.glob("*_21cm_3857.tif"))
    if args.limit:
        tifs = tifs[:args.limit]
    if not tifs:
        raise SystemExit(f"no 21cm tiles in {args.scc_dir} — run fetch_scc_imagery.py --all first")

    # Order tiles west->east by footprint centre, then halve each split separately.
    ordered = []
    for tif in tifs:
        tile = tif.name.replace("_21cm_3857.tif", "")
        key = f"{tile}.png"
        if key not in idx:
            logger.warning(f"{tile}: not in tile_index — skipping")
            continue
        b = idx[key]["bounds"]
        ordered.append((tile, (b["minx"], b["miny"], b["maxx"], b["maxy"])))
    ordered.sort(key=lambda kv: (kv[1][0] + kv[1][2]) / 2.0)

    by_split = {}
    for tile, fp in ordered:
        by_split.setdefault(splits.get(tile, ("train", None))[0], []).append((tile, fp))
    owner = assign(by_split, annotators)

    out_index, rows, qa, failed = {}, [], {}, []
    for tile, footprint in ordered:
        tif = args.scc_dir / f"{tile}_21cm_3857.tif"
        checks, meta = check_and_write_png(tif, footprint, args.out_dir / "images" / f"{tile}.png")
        qa[tile] = {"checks": checks, **meta}
        if not all(checks.values()):
            failed.append((tile, [k for k, v in checks.items() if not v]))
            continue

        split, image_name = splits.get(tile, ("train", None))
        mode = "fresh" if split in ("val", "test") else "seeded"
        # Count existing polygons for every tile (reported for reference), but only WRITE a seed
        # file for seeded tiles — fresh tiles must reach the annotator blind.
        lines = read_seed_polygons(args.naip_root, split, image_name) if image_name else []
        n_seed = len(lines)
        if mode == "seeded" and lines:
            sp = args.out_dir / "seeds" / f"{tile}.txt"
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_text("\n".join(lines) + "\n")

        with rasterio.open(tif) as src:
            out_index[f"{tile}.png"] = {
                "transform": list(src.transform)[:6], "crs": GRID_CRS,
                "width": src.width, "height": src.height,
                "source": tif.name, "vintage": "scc_2025", "gsd_ground_m": meta["gsd_ground_m"],
                "bounds": dict(zip(("minx", "miny", "maxx", "maxy"), map(float, src.bounds))),
                "naip_tile": f"{tile}.png",
            }
        rows.append(dict(tile=tile, annotator=owner[tile], split=split, mode=mode,
                         n_seed_polygons=(n_seed if mode == "seeded" else 0), n_seed_ref=n_seed,
                         width=meta["width"], height=meta["height"],
                         gsd_ground_m=meta["gsd_ground_m"], centre_x=(footprint[0] + footprint[2]) / 2))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "tile_index_21cm.json").write_text(json.dumps(
        {"tiles": out_index,
         "metadata": {"crs": GRID_CRS, "vintage": "scc_2025", "source": "SCC Imagery_2025 MapServer",
                      "aligned_to": str(args.tile_index), "n_tiles": len(out_index)}}, indent=2))
    with (args.out_dir / "assignment.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    (args.out_dir / "qa_report.json").write_text(json.dumps(
        {"n_tiles": len(ordered), "n_passed": len(rows), "n_failed": len(failed),
         "failures": {t: c for t, c in failed}, "per_tile": qa}, indent=2))

    by_mode = {m: sum(1 for r in rows if r["mode"] == m) for m in ("fresh", "seeded")}
    by_who = {a: sum(1 for r in rows if r["annotator"] == a) for a in annotators}
    seeds = sum(r["n_seed_polygons"] for r in rows)
    logger.info(f"packaged {len(rows)}/{len(ordered)} tiles -> {args.out_dir}")
    logger.info(f"  mode: {by_mode}   annotator: {by_who}   seed polygons: {seeds}")
    for split in sorted({r["split"] for r in rows}):
        per = {a: sum(1 for r in rows if r["split"] == split and r["annotator"] == a)
               for a in annotators}
        logger.info(f"  {split:5s}: {per}")
    if failed:
        logger.error(f"  {len(failed)} tile(s) FAILED integrity — fix before uploading:")
        for t, c in failed[:10]:
            logger.error(f"    {t}: {c}")
        raise SystemExit(1)
    logger.info("  all integrity checks passed — safe to upload")


if __name__ == "__main__":
    main()
