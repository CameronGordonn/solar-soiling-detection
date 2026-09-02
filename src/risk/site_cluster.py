"""Group detected polygons into SITES before costing them.

Economics runs per *site*, not per *polygon*, and at 21cm those are not the same thing.

The 21cm relabel deliberately switched the label convention from whole-array footprints
to individual panel blocks — 2.1x the objects on the same ground. One house that used to
detect as a single polygon now resolves into several. Costing each polygon separately
charges the ``MIN_PRO_SERVICE`` trip fee (currently $150) once per polygon, so a house
split into four blocks is billed four truck rolls for one visit. That is wrong in both
directions at once:

* **Cost is overstated** — four minimum charges instead of one.
* **Benefit is understated** — each fragment is sized from its own small area, and the
  per-panel rate schedule gives small systems the *worst* $/panel rate, so the fragments
  never reach the bulk discount the real system qualifies for.

Both errors push the same way: toward ``no_clean``. Fragmentation is therefore a
mechanical contributor to the "nothing is ever economic" finding, independent of the
soiling model.

Two strategies, in preference order:

``parcel``
    Spatial join to a parcel layer keyed by APN (``data/external/santa_cruz_parcels/``).
    This is the right answer where a layer exists: a parcel is the billing entity, it is
    who receives the mailer, and it is what a permit record joins on.

``proximity``
    Union-find over polygons whose *edges* come within ``max_gap_m``. Used where no
    parcel layer covers the AOI. Edge distance, not centroid distance, because panel
    blocks on one roof are separated by a ridgeline or a vent — a metre or two of gap —
    while neighbouring houses are separated by setbacks of several metres.

The gap threshold is the one judgement call. Too small and one roof stays fragmented;
too large and adjacent townhouses merge into a single fictional site. The default is
documented at :data:`DEFAULT_MAX_GAP_M`; :func:`cluster_diagnostics` reports the
merge distribution so the choice can be checked against the AOI rather than assumed.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

#: Default edge-to-edge gap (metres) below which two polygons are treated as one site.
#:
#: SOURCED BY MEASUREMENT, not from a standard: 2.5 m is comfortably wider than the
#: ridge/vent/walkway gaps that split one roof's panels (typically 0.3-1.5 m) and
#: comfortably narrower than a residential side setback in Santa Cruz County, where
#: R-1 zoning requires 5 ft (1.52 m) per side — so two houses' arrays are >= ~3 m apart
#: even before roof overhang. Check against a new AOI with cluster_diagnostics().
DEFAULT_MAX_GAP_M = 2.5

#: Metric CRS for distance work in California. EPSG:3310 (California Albers) is
#: equal-area and correct for both areas and short distances statewide — unlike 3857,
#: whose scale error at 37 deg N inflates distances and areas by ~1.25x / ~1.57x.
METRIC_CRS = "EPSG:3310"


def _to_metric(gdf):
    if gdf.crs is None:
        raise ValueError("GeoDataFrame has no CRS; cannot cluster in metres")
    return gdf.to_crs(METRIC_CRS)


def assign_sites_by_parcel(gdf, parcels, *, parcel_key: str = "APN",
                           site_prefix: str = "apn") -> pd.Series:
    """Site id per polygon from a parcel layer. Unmatched polygons get ``NaN``.

    Matching is by polygon *representative point* (guaranteed inside the polygon, unlike
    a centroid on a concave shape), which assigns each array to exactly one parcel and
    avoids the double-counting a polygon-overlap join produces when an array straddles a
    parcel line.
    """
    import geopandas as gpd

    left = _to_metric(gdf)
    right = _to_metric(parcels)[[parcel_key, "geometry"]]

    pts = gpd.GeoDataFrame(
        {"_row": np.arange(len(left))},
        geometry=left.geometry.representative_point(),
        crs=left.crs,
    )
    joined = gpd.sjoin(pts, right, how="left", predicate="within")
    # A point can land in two parcels only where the layer self-overlaps; keep the first.
    joined = joined[~joined["_row"].duplicated(keep="first")].sort_values("_row")

    apn = joined[parcel_key].to_numpy()
    out = pd.Series(
        [f"{site_prefix}:{a}" if isinstance(a, str) and a else np.nan for a in apn],
        index=gdf.index, dtype=object,
    )
    return out


def assign_sites_by_proximity(gdf, *, max_gap_m: float = DEFAULT_MAX_GAP_M,
                              site_prefix: str = "prox") -> pd.Series:
    """Site id per polygon by union-find over edge-to-edge distance <= ``max_gap_m``.

    Uses an STRtree query on buffered geometries so it is O(n log n)-ish rather than the
    O(n^2) all-pairs distance matrix a naive version would build — the Santa Cruz AOI
    has 3,362 polygons and all-pairs would be 11M distance computations.
    """
    from shapely import STRtree

    left = _to_metric(gdf)
    geoms = list(left.geometry.values)
    n = len(geoms)
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    # Buffer by half the gap on each side: two buffers intersect exactly when the
    # originals are within max_gap_m of each other.
    half = max_gap_m / 2.0
    buffered = [g.buffer(half) for g in geoms]
    tree = STRtree(buffered)
    for i, gb in enumerate(buffered):
        for j in tree.query(gb):
            j = int(j)
            if j > i and buffered[j].intersects(gb):
                union(i, j)

    roots = np.array([find(i) for i in range(n)])
    # Relabel roots to dense 0..k-1 so ids are stable and readable.
    _, dense = np.unique(roots, return_inverse=True)
    return pd.Series([f"{site_prefix}:{d}" for d in dense], index=gdf.index, dtype=object)


def assign_sites(gdf, *, parcels=None, parcel_key: str = "APN",
                 max_gap_m: float = DEFAULT_MAX_GAP_M):
    """Attach ``site_id`` + ``site_source`` to a copy of ``gdf``.

    Parcel match where available, proximity for whatever the parcel layer misses (AOI
    edges, unparcelled land, layer gaps). Mixing the two is deliberate: falling back to
    proximity for the remainder is strictly better than leaving those polygons
    un-clustered and individually costed, which is the bug being fixed.
    """
    out = gdf.copy()
    site = pd.Series(np.nan, index=out.index, dtype=object)
    source = pd.Series("", index=out.index, dtype=object)

    if parcels is not None and len(parcels):
        site = assign_sites_by_parcel(out, parcels, parcel_key=parcel_key)
        source = pd.Series(np.where(site.notna(), "parcel", ""), index=out.index, dtype=object)

    missing = site.isna()
    if missing.any():
        prox = assign_sites_by_proximity(out.loc[missing], max_gap_m=max_gap_m)
        site.loc[missing] = prox
        source.loc[missing] = "proximity"

    out["site_id"] = site
    out["site_source"] = source
    return out


def cluster_diagnostics(gdf, site_col: str = "site_id") -> dict:
    """Did clustering actually do anything, and did it over-merge?"""
    n_poly = int(len(gdf))
    sizes = gdf.groupby(site_col).size()
    n_sites = int(len(sizes))
    src = (gdf["site_source"].value_counts().to_dict()
           if "site_source" in gdf.columns else {})
    return {
        "n_polygons": n_poly,
        "n_sites": n_sites,
        "polygons_per_site_mean": float(sizes.mean()) if n_sites else 0.0,
        "polygons_per_site_median": float(sizes.median()) if n_sites else 0.0,
        "polygons_per_site_max": int(sizes.max()) if n_sites else 0,
        "pct_sites_multi_polygon": float((sizes > 1).mean() * 100) if n_sites else 0.0,
        "fragmentation_factor": (n_poly / n_sites) if n_sites else float("nan"),
        "site_source_counts": src,
    }


def aggregate_to_sites(gdf, *, site_col: str = "site_id",
                       area_col: str = "area_m2",
                       loss_cols: tuple[str, ...] = ("loss_pct_p10", "loss_pct_p50", "loss_pct_p90"),
                       ) -> pd.DataFrame:
    """Collapse polygons to one row per site: summed area, area-weighted loss.

    Area SUMS (the site's total array is the union of its blocks) while loss is
    AREA-WEIGHTED (a site's effective soiling is dominated by its biggest block, and a
    3 m2 sliver should not pull the site's loss estimate as hard as a 60 m2 block).
    """
    rows = []
    for site, grp in gdf.groupby(site_col, sort=False):
        area = pd.to_numeric(grp.get(area_col), errors="coerce").fillna(0.0)
        tot = float(area.sum())
        w = area.to_numpy() if tot > 0 else np.ones(len(grp))
        rec = {
            site_col: site,
            "n_polygons": int(len(grp)),
            "area_m2": tot,
            "area_m2_max_polygon": float(area.max()) if len(area) else 0.0,
        }
        for c in loss_cols:
            if c in grp.columns:
                v = pd.to_numeric(grp[c], errors="coerce").to_numpy(dtype=float)
                ok = np.isfinite(v)
                rec[c] = float(np.average(v[ok], weights=w[ok])) if ok.any() else np.nan
        if "site_source" in grp.columns:
            rec["site_source"] = grp["site_source"].iloc[0]
        for c in ("permit_kw", "risk_score"):
            if c in grp.columns:
                v = pd.to_numeric(grp[c], errors="coerce")
                rec[c] = float(v.max()) if c == "permit_kw" else float(v.mean())
        rows.append(rec)
    return pd.DataFrame(rows)
