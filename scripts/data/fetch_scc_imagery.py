"""Fetch Santa Cruz County high-res (~21 cm) aerial imagery for labeled AOIs, georeference
from the request bbox, and reproject to EPSG:3857 so it aligns with data/interim/tile_index.json.

W2 of docs/PERMISSIVE_STACK_MIGRATION.md ("exploit 30 cm NAIP for the recall win"). This script
covers steps 1-3 only: download -> georeference -> reproject. The re-tile + label re-render (steps
4-5) are deliberately NOT here -- re-rendering 2022 labels onto 2025 imagery injects vintage noise
(panels added/removed 2023-25) and must be eyeballed per-AOI first.

Design:
  - One export request per labeled tile footprint (each is ~1500 px at 21 cm -- always under the
    ImageServer max, so no chunking needed).
  - The georef transform is built from the REQUEST bbox (rasterio.transform.from_bounds), never from
    embedded georef -- we know it exactly (see .claude/rules/data-pipeline.md: CRS/affine are sacred).
  - --dry-run synthesizes imagery and runs a geometry self-test (georef -> reproject -> bounds match
    within tolerance), so the coordinate math is verifiable offline. Only the HTTP leg needs the live
    endpoint.

Usage:
  # offline geometry self-test (no network):
  PYTHONPATH=. conda run -n solar-soiling python scripts/data/fetch_scc_imagery.py --dry-run --tiles tile_000000.png

  # real fetch for a few AOIs (supply the county service URL you verified):
  PYTHONPATH=. conda run -n solar-soiling python scripts/data/fetch_scc_imagery.py \
      --service-url "https://<county-host>/.../Cache/Imagery_2025/MapServer/export" \
      --tiles tile_000000.png,tile_000107.png
"""

from pathlib import Path
import argparse
import io
import json
import logging
import sys
import time

import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds
from rasterio.warp import reproject, Resampling, transform_bounds, calculate_default_transform

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

GRID_CRS = "EPSG:3857"        # what tile_index.json is in -- the alignment target
DEFAULT_EXPORT_SR = 2227      # NAD83 California zone III (US survey feet) -- covers Santa Cruz
# WRONG, AND KEPT ONLY FOR REPRODUCIBILITY OF THE 2026-08 LABEL SET.
# 0.208 came from the cache's finest LOD resolution, 0.20833 -- but the service reports
# `units: esriFeet`, so that is 0.20833 FEET = 0.0635 m. Feet were read as metres, and every tile
# we have fetched is 3.3x coarser (11x fewer pixels) than the county actually serves. The layer
# list says the same: DBO.ORTHO_2025_03IN is 3-inch = 7.6 cm imagery.
# Verified empirically 2026-08-07, not just from metadata: bicubic-upsampling a 0.2083 fetch to a
# 0.0635 grid leaves residual RMS at 35% of signal with 46% of it above half-Nyquist -- real detail,
# not interpolation. The 6.3 cm fetch resolves painted road text; the 21 cm fetch does not.
# Consequence: median array 26.7 px at 21 cm (84% COCO-small) vs ~87 px at 6.3 cm.
DEFAULT_NATIVE_GSD_M = 0.208   # what the 2026-08 scc21 label set was built at -- do not "fix" in
                               # place, it would silently desync the labels from their imagery
TRUE_NATIVE_GSD_M = 0.0635     # what the service can actually deliver (cache LOD 16)
MAX_EXPORT_PX = 4096           # service maxImageWidth/maxImageHeight
FT_PER_M = 1.0 / 0.3048       # US survey foot is ~identical at this precision; EPSG:2227 is ftUS
MAX_DIM = 4000                # ImageServer export size guard


def load_footprints(tile_index_path: Path, tiles, use_all: bool):
    idx = json.loads(tile_index_path.read_text())
    if isinstance(idx, dict) and "tiles" in idx and isinstance(idx["tiles"], dict):
        idx = idx["tiles"]  # canonical layout: {"tiles": {...}, "metadata": {...}}
    if not isinstance(idx, dict):
        raise ValueError("tile_index.json is not a dict of {tile: meta}")
    if use_all:
        wanted = list(idx.keys())
    else:
        wanted = []
        for t in tiles:
            if t not in idx:
                raise KeyError(f"{t} not in {tile_index_path} (have {len(idx)} tiles)")
            wanted.append(t)
    out = {}
    for t in wanted:
        m = idx[t]
        if m.get("crs") != GRID_CRS:
            raise ValueError(f"{t} crs={m.get('crs')} != {GRID_CRS}; alignment assumption broken")
        b = m["bounds"]
        out[t] = (b["minx"], b["miny"], b["maxx"], b["maxy"])
    return out


def export_bbox_and_size(bounds_3857, export_sr, native_gsd_m):
    """3857 footprint -> (export-SR bbox, pixel w, h) for the export request."""
    minx, miny, maxx, maxy = transform_bounds(GRID_CRS, f"EPSG:{export_sr}", *bounds_3857)
    # EPSG:2227 is in US feet; convert the requested GSD to the export CRS unit.
    gsd_unit = native_gsd_m * FT_PER_M if export_sr == 2227 else native_gsd_m
    w = int(round((maxx - minx) / gsd_unit))
    h = int(round((maxy - miny) / gsd_unit))
    if w > MAX_DIM or h > MAX_DIM:
        raise ValueError(f"export {w}x{h} exceeds {MAX_DIM}px -- footprint too large, add chunking")
    return (minx, miny, maxx, maxy), w, h


def export_timeout_s(w, h, floor=120.0, per_mpx=45.0):
    """Read timeout scaled to the size of the render being asked for.

    The 21cm sweep asked for 1.4 Mpx and 120 s was ample. A 6.3cm tile is 15.4 Mpx (~46 MB of
    TIFF) and the county server takes materially longer to compose it -- at a flat 120 s the
    first request timed out before the server had done anything wrong. Scale with pixel count
    so one constant serves both resolutions.
    """
    return max(floor, per_mpx * (w * h) / 1e6)


def fetch_export(service_url, bbox, export_sr, w, h, session=None, retries=4, backoff=3.0):
    """Hit the ArcGIS MapServer/ImageServer export endpoint; return TIFF bytes.

    Retries transient network faults with exponential backoff: a --all sweep is ~250 sequential
    requests against a public county server, and a single read timeout used to abort the whole run.
    """
    import requests
    params = dict(
        bbox=",".join(f"{v:.4f}" for v in bbox),
        bboxSR=export_sr, imageSR=export_sr,
        size=f"{w},{h}", format="tiff", f="image",
    )
    sess = session or requests
    for attempt in range(retries):
        try:
            r = sess.get(service_url, params=params, timeout=export_timeout_s(w, h))
            r.raise_for_status()
            # Cached MapServers send an empty Content-Type and may serve PNG even when format=tiff,
            # so sniff magic bytes instead of trusting the header. Embedded georef (if any) is
            # ignored anyway -- we rebuild the transform from the request bbox downstream.
            head = r.content[:4]
            is_img = (head[:4] in (b"II*\x00", b"MM\x00*") or head[:4] == b"\x89PNG"
                      or head[:2] == b"\xff\xd8")
            if not is_img:
                raise RuntimeError(
                    f"endpoint returned non-image ({r.headers.get('Content-Type','?')}): {r.text[:200]}")
            return r.content
        except (requests.exceptions.RequestException, RuntimeError) as e:
            if attempt == retries - 1:
                raise
            wait = backoff * (2 ** attempt)
            logger.warning(f"  fetch attempt {attempt + 1}/{retries} failed ({type(e).__name__}); "
                           f"retrying in {wait:.0f}s")
            time.sleep(wait)


def gsd_tag(native_gsd_m: float) -> str:
    """Filename tag for a fetch resolution, e.g. 0.208 -> '21cm', 0.0635 -> '6cm'.

    The resolution MUST be in the filename. The 2026-08 set is all `*_21cm_3857.tif`, and a
    6.3cm re-fetch writing to that same name would both overwrite the imagery the scc21 labels
    were drawn on and be silently skipped by the resume check.
    """
    return f"{int(round(native_gsd_m * 100))}cm"


def already_fetched(out_path: Path, expect_w: int | None = None, expect_h: int | None = None):
    """True if out_path is a readable raster at the expected size -- lets --all resume.

    Guards against a truncated file from an interrupted run: a partial GeoTIFF either fails to
    open or reports the wrong size, and gets refetched. The expected size is passed in rather
    than a fixed floor, so a file left over from a coarser fetch cannot satisfy a finer one.
    """
    if not out_path.exists():
        return False
    try:
        with rasterio.open(out_path) as src:
            if src.count < 3:
                return False
            if expect_w is None or expect_h is None:
                return src.width >= 900 and src.height >= 900
            # Allow 1px of rounding slack between the request size and what came back.
            return abs(src.width - expect_w) <= 1 and abs(src.height - expect_h) <= 1
    except Exception:
        return False


def _synthetic_tiff_bytes(w, h, bbox, export_sr):
    """Dry-run: a recognizable georeferenced raster in the export CRS (for the self-test)."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = (255 * xx / max(1, w - 1)).astype(np.uint8)
    g = (255 * yy / max(1, h - 1)).astype(np.uint8)
    b = np.full((h, w), 128, np.uint8)
    arr = np.stack([r, g, b])
    transform = from_bounds(*bbox, w, h)
    with MemoryFile() as mf:
        with mf.open(driver="GTiff", height=h, width=w, count=3, dtype="uint8",
                     crs=f"EPSG:{export_sr}", transform=transform) as ds:
            ds.write(arr)
        return mf.read()


def aoi_grid(aoi_bounds_3857, native_gsd_m, tile_px, name_prefix="tile"):
    """Cover an AOI with a grid of `tile_px`-square footprints. -> {name: bounds_3857}.

    The grid is defined in PIXELS, not metres, so tiles come back at the size the detector
    was trained to see (1200 px at 21cm, matching data/yolo/scc21) regardless of the GSD
    requested. Ground extent per tile is therefore tile_px * native_gsd_m, converted into
    3857 by the local Mercator factor -- 3857 "metres" are not ground metres, and using them
    directly would make tiles 1/cos(lat) too large on the ground (~25% here).

    tile_px is capped at the service's maxImageWidth, so a single export request always
    suffices and no chunking is needed. 4096 is the efficient setting for a wide sweep; 1200
    is the setting that matches training framing.
    """
    import math
    minx, miny, maxx, maxy = aoi_bounds_3857
    lat = math.degrees(2 * math.atan(math.exp(((miny + maxy) / 2) / 6378137.0)) - math.pi / 2)
    cell_3857 = tile_px * native_gsd_m / math.cos(math.radians(lat))

    n_cols = max(1, math.ceil((maxx - minx) / cell_3857))
    n_rows = max(1, math.ceil((maxy - miny) / cell_3857))
    out = {}
    for r in range(n_rows):
        for c in range(n_cols):
            x0 = minx + c * cell_3857
            y1 = maxy - r * cell_3857
            out[f"{name_prefix}_r{r:03d}c{c:03d}.png"] = (x0, y1 - cell_3857, x0 + cell_3857, y1)
    return out


def index_entry(bounds_3857, width, height):
    """tile_index entry for a fetched tile, in the shape every consumer here expects."""
    from utils.tile_metadata import ground_gsd_m
    tf = from_bounds(*bounds_3857, width, height)
    cy = (bounds_3857[1] + bounds_3857[3]) / 2
    return {
        "transform": [tf.a, tf.b, tf.c, tf.d, tf.e, tf.f],
        # Explicit, so nothing has to infer it. The two legacy indices disagree on order and
        # reading one with the other's convention silently relocates the tile (see
        # utils.tile_metadata.affine_from_entry).
        "transform_order": "affine",
        "crs": GRID_CRS,
        "width": width, "height": height,
        "vintage": "scc_2025",
        # Required by rfdetr_infer.py, and what turns detected pixels into m2 -> kW -> $.
        # NOT transform.a, which in 3857 is inflated by 1/cos(lat).
        "gsd_ground_m": round(ground_gsd_m(tf, GRID_CRS, center_y=cy), 4),
        "bounds": {"minx": bounds_3857[0], "miny": bounds_3857[1],
                   "maxx": bounds_3857[2], "maxy": bounds_3857[3]},
    }


def georeference_and_reproject(tiff_bytes, bbox, export_sr, bounds_3857, out_path: Path):
    """Attach the KNOWN request-bbox transform, then reproject onto the exact 3857 footprint grid
    from tile_index.json -- so the output aligns to the tile grid by construction (the re-tile step
    needs this). Returns actual 3857 bounds (== the footprint, modulo float epsilon)."""
    with MemoryFile(tiff_bytes) as mf, mf.open() as src:
        w, h = src.width, src.height
        data = src.read()  # (bands, h, w)
    if data.shape[0] >= 4:
        data = data[:3]  # drop the constant alpha band -> clean RGB for NAIP tone-mapping
    src_transform = from_bounds(*bbox, w, h)  # authoritative: from the request, not embedded georef
    src_crs = rasterio.crs.CRS.from_epsg(export_sr)

    # Destination grid = the known 3857 footprint, preserving native pixel count (no resolution loss).
    dst_w, dst_h = w, h
    dst_transform = from_bounds(*bounds_3857, dst_w, dst_h)
    dst = np.zeros((data.shape[0], dst_h, dst_w), dtype=data.dtype)
    for b in range(data.shape[0]):
        reproject(
            source=data[b], destination=dst[b],
            src_transform=src_transform, src_crs=src_crs,
            dst_transform=dst_transform, dst_crs=GRID_CRS,
            resampling=Resampling.bilinear,
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.suffix.lower() == ".png":
        # PNG for the AOI path: it is what the detector was trained on, and lossless. Never
        # JPEG -- an 8x8 DCT block is 1.7 m of ground at 21cm, about half a panel
        # (docs/LABELING_SPRINT_21CM.md). Georef lives in the sidecar tile_index.json.
        from PIL import Image
        Image.fromarray(np.transpose(dst[:3], (1, 2, 0)), mode="RGB").save(out_path)
    else:
        with rasterio.open(out_path, "w", driver="GTiff", height=dst_h, width=dst_w,
                           count=dst.shape[0], dtype=dst.dtype, crs=GRID_CRS,
                           transform=dst_transform) as ds:
            ds.write(dst)
    b = rasterio.transform.array_bounds(dst_h, dst_w, dst_transform)  # (minx,miny,maxx,maxy)
    gsd_out = dst_transform.a
    return b, gsd_out, (dst_w, dst_h)


def process(tile, bounds_3857, args, session=None):
    # Dry-run writes synthetic gradients; keep them out of the live out-dir or a self-test will
    # silently overwrite real fetched imagery with fake pixels.
    out_dir = Path(args.out_dir) / "_dryrun" if args.dry_run else Path(args.out_dir)
    if args.aoi:
        # AOI mode keys the index on the tile's own name, so the file must BE that name --
        # export_polygons_geojson looks up `label_file.stem + ".png"`.
        out_path = out_dir / tile
    else:
        out_path = out_dir / f"{Path(tile).stem}_{gsd_tag(args.native_gsd)}_3857.tif"
    bbox_exp, w, h = export_bbox_and_size(bounds_3857, args.export_sr, args.native_gsd)
    if not args.overwrite and not args.dry_run and already_fetched(out_path, w, h):
        logger.info(f"{tile}: already fetched -> {out_path.name} [SKIP]")
        return True
    if args.dry_run:
        tiff = _synthetic_tiff_bytes(w, h, bbox_exp, args.export_sr)
    else:
        if not args.service_url:
            raise SystemExit("--service-url is required for a real fetch (or use --dry-run)")
        try:
            tiff = fetch_export(args.service_url, bbox_exp, args.export_sr, w, h, session)
        except Exception as e:
            # One tile's network failure must not kill a 249-tile sweep. This runs on a laptop
            # that gets closed: suspend drops every socket, and the retry budget inside
            # fetch_export can be spent entirely while the lid is shut. Record and move on --
            # the tile is simply absent from disk, so the next run refetches it.
            logger.warning(f"{tile}: FAILED ({type(e).__name__}: {str(e)[:120]}) -- will retry on "
                           f"the next run")
            return False
    # Write via a temp file then rename: a partial file from a kill/suspend mid-write would
    # otherwise sit on disk and, if it happened to have the right dimensions, be treated as done.
    tmp_path = out_path.with_suffix(out_path.suffix + ".part")
    out_bounds, gsd_out, (dw, dh) = georeference_and_reproject(
        tiff, bbox_exp, args.export_sr, bounds_3857, tmp_path)
    tmp_path.replace(out_path)

    # Verify: reprojected output must cover the same 3857 footprint as the source tile.
    err = max(abs(a - b) for a, b in zip(out_bounds, bounds_3857))
    status = "OK" if err <= args.tol_m else "MISMATCH"
    logger.info(f"{tile}: {w}x{h}@{args.native_gsd:.3f}m -> 3857 {dw}x{dh}@{gsd_out:.3f}m/px  "
                f"bounds err={err:.2f}m [{status}]  -> {out_path.name}")
    return err <= args.tol_m


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--service-url", help="ArcGIS MapServer/ImageServer export URL (county 2025 imagery)")
    p.add_argument("--tile-index", default="data/interim/tile_index.json")
    p.add_argument("--tiles", default="", help="comma-separated tile ids (e.g. tile_000000.png)")
    p.add_argument("--all", action="store_true", help="process every tile in the index")
    p.add_argument("--aoi", default="",
                   help="WGS84 bbox 'minx,miny,maxx,maxy' or path to a GeoJSON polygon. Tiles the "
                        "AOI on a fresh grid instead of re-fetching tile_index footprints, and "
                        "writes its own tile_index.json (with gsd_ground_m) next to the imagery.")
    p.add_argument("--tile-px", type=int, default=1200,
                   help="tile size in pixels (default 1200, matching the scc21 training framing). "
                        f"Capped at the service max of {MAX_EXPORT_PX}; 4096 covers ~12x more "
                        "ground per request, which is what makes a county-scale sweep tractable.")
    p.add_argument("--out-index", default="",
                   help="where to write the AOI tile_index.json (default: <out-dir>/tile_index.json)")
    p.add_argument("--out-dir", default="data/interim/scc_2025")
    p.add_argument("--native-gsd", type=float, default=DEFAULT_NATIVE_GSD_M, help="native m/px (default 0.208)")
    p.add_argument("--export-sr", type=int, default=DEFAULT_EXPORT_SR, help="EPSG for the export request")
    p.add_argument("--tol-m", type=float, default=5.0, help="max allowed 3857 bounds error (meters)")
    p.add_argument("--overwrite", action="store_true",
                   help="refetch tiles already on disk (default: resume, skipping valid ones)")
    p.add_argument("--dry-run", action="store_true", help="synthesize imagery + run geometry self-test (no HTTP)")
    p.add_argument("--max-consecutive-failures", type=int, default=8,
                   help="abort the sweep after this many failures in a row (default 8) -- a closed "
                        "laptop fails every tile, and there is no point burning through the rest")
    return p.parse_args(argv)


def aoi_bounds_3857(spec: str):
    """WGS84 bbox string or GeoJSON path -> (minx, miny, maxx, maxy) in EPSG:3857."""
    p = Path(spec)
    if p.is_file():
        gj = json.loads(p.read_text())
        geoms = ([f["geometry"] for f in gj["features"]] if gj.get("type") == "FeatureCollection"
                 else [gj.get("geometry", gj)])
        xs = [c[0] for g in geoms for ring in _rings(g) for c in ring]
        ys = [c[1] for g in geoms for ring in _rings(g) for c in ring]
        bbox_wgs = (min(xs), min(ys), max(xs), max(ys))
    else:
        parts = [float(v) for v in spec.split(",")]
        if len(parts) != 4:
            raise SystemExit(f"--aoi bbox needs 4 comma-separated numbers, got {len(parts)}")
        bbox_wgs = tuple(parts)
    if not (-180 <= bbox_wgs[0] < bbox_wgs[2] <= 180 and -90 <= bbox_wgs[1] < bbox_wgs[3] <= 90):
        raise SystemExit(f"--aoi must be WGS84 lon/lat with min < max, got {bbox_wgs}")
    return transform_bounds("EPSG:4326", GRID_CRS, *bbox_wgs)


def _rings(geom):
    """Yield coordinate rings from a Polygon/MultiPolygon geometry dict."""
    if geom["type"] == "Polygon":
        return geom["coordinates"]
    if geom["type"] == "MultiPolygon":
        return [ring for poly in geom["coordinates"] for ring in poly]
    raise SystemExit(f"--aoi GeoJSON must be Polygon or MultiPolygon, got {geom['type']}")


def main(argv=None):
    args = parse_args(argv)
    tiles = [t.strip() for t in args.tiles.split(",") if t.strip()]
    if args.aoi:
        if args.tile_px > MAX_EXPORT_PX:
            raise SystemExit(f"--tile-px {args.tile_px} exceeds the service cap {MAX_EXPORT_PX}")
        footprints = aoi_grid(aoi_bounds_3857(args.aoi), args.native_gsd, args.tile_px)
        logger.info(f"AOI -> {len(footprints)} tiles of {args.tile_px}px at "
                    f"{args.native_gsd:.4f} m/px ground "
                    f"({args.tile_px * args.native_gsd:.0f} m per tile edge)")
    elif not tiles and not args.all:
        raise SystemExit("pass --tiles <ids>, --all, or --aoi <bbox|geojson>")
    else:
        footprints = load_footprints(Path(args.tile_index), tiles, args.all)
    logger.info(f"{'DRY-RUN ' if args.dry_run else ''}processing {len(footprints)} AOI(s) "
                f"-> {args.out_dir} (target {GRID_CRS}, tol {args.tol_m}m)")
    # One keep-alive session across ~250 sequential requests: fewer TLS handshakes and fewer
    # sockets for a public county server than a fresh connection per tile.
    session = None
    if not args.dry_run:
        import requests
        session = requests.Session()
        session.headers["User-Agent"] = "solar-soiling-ml/1.0 (research)"
    total = len(footprints)
    ok = failed = 0
    consecutive_failures = 0
    started = time.time()
    for i, (t, b) in enumerate(footprints.items(), 1):
        if process(t, b, args, session):
            ok += 1
            consecutive_failures = 0
        else:
            failed += 1
            consecutive_failures += 1
            # A closed laptop or a dropped VPN fails EVERY tile. Spinning through the remaining
            # 200 to fail each one wastes an hour and hammers the county server with doomed
            # requests; stop and let the next run resume from disk.
            if consecutive_failures >= args.max_consecutive_failures:
                logger.error(f"aborting: {consecutive_failures} consecutive failures -- network is "
                             f"probably down. {ok} fetched this run; re-run to resume.")
                break
        if i % 10 == 0 or i == total:
            elapsed = time.time() - started
            rate = elapsed / max(1, ok) if ok else 0
            remaining = (total - i) * rate
            logger.info(f"progress {i}/{total} | ok {ok} failed {failed} | "
                        f"{elapsed / 60:.0f}m elapsed"
                        + (f", ~{remaining / 60:.0f}m left" if rate else ""))

    # Must match the directory process() actually wrote to, or the dry-run self-test reports
    # 0 present while its own tiles sit in the _dryrun sandbox.
    out_dir = Path(args.out_dir) / "_dryrun" if args.dry_run else Path(args.out_dir)
    if args.aoi:
        # Index only the tiles that actually landed, so a partial run yields a CONSISTENT index
        # rather than one promising tiles that are not there -- rfdetr_infer hard-fails the whole
        # AOI on one missing entry.
        present = {n: b for n, b in footprints.items() if (out_dir / n).is_file()}
        on_disk = len(present)
        index_path = Path(args.out_index) if args.out_index else out_dir / "tile_index.json"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        entries = {}
        for n, b in present.items():
            # Per tile, not once: the 3857->2227 round trip rounds differently across the grid,
            # so tiles can differ by a pixel. Reusing one size would offset the georeferencing.
            _, tw, th = export_bbox_and_size(b, args.export_sr, args.native_gsd)
            entries[n] = index_entry(b, tw, th)
        index_path.write_text(json.dumps({
            "tiles": entries,
            "metadata": {"crs": GRID_CRS, "vintage": "scc_2025",
                         "source": "SCC Imagery_2025 MapServer",
                         "native_gsd_m": args.native_gsd, "tile_px": args.tile_px,
                         "n_tiles": on_disk, "aoi": args.aoi},
        }, indent=1))
        logger.info(f"tile_index -> {index_path} ({on_disk} tiles)")
    else:
        on_disk = sum(1 for _ in out_dir.glob(f"*_{gsd_tag(args.native_gsd)}_3857.tif"))
    logger.info(f"done: {ok} fetched/verified this run, {failed} failed, "
                f"{on_disk}/{total} present on disk at {gsd_tag(args.native_gsd)}")
    if on_disk < total:
        logger.info("re-run the same command to fetch what is missing (it skips what is done)")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
