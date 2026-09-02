#!/usr/bin/env python3
"""Shared parcel locator for the permit threads.

The Census batch geocoder positions an address by interpolating along the TIGER
street segment, which lands 50-150 m off the actual house (often on the wrong
stretch of the street). For *positioning* a chip or a label seed that error is
fatal — the frame can miss the permitted roof entirely.

The permit's recorded **APN** is exact, and we have the county parcel polygons, so
position on the parcel centroid + outline instead. This module maps APN -> parcel
geometry (EPSG:3857) for both `render_miss_review_sheet.py` and
`generate_permit_seeds.py`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pyproj

_SHARE = os.path.dirname(pyproj.datadir.get_data_dir())
os.environ.setdefault("PROJ_DATA", pyproj.datadir.get_data_dir())
os.environ.setdefault("PROJ_LIB", pyproj.datadir.get_data_dir())
os.environ.setdefault("GDAL_DATA", os.path.join(_SHARE, "gdal"))

import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402

MERC = "EPSG:3857"


def apn_base(s) -> str | None:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    digits = "".join(ch for ch in str(s) if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else None


def load_parcel_index(parcels_path: Path) -> gpd.GeoDataFrame:
    """Parcels (EPSG:3857) indexed by 8-digit APN base; one row per APN."""
    par = gpd.read_file(parcels_path).to_crs(MERC)
    par["ab"] = par["APN"].map(apn_base)
    par = par.dropna(subset=["ab"]).drop_duplicates("ab").set_index("ab")
    return par


def parcel_geom(par_index: gpd.GeoDataFrame, apn):
    """Parcel polygon (3857) for a permit APN, or None if not in the layer."""
    ab = apn_base(apn)
    if ab is not None and ab in par_index.index:
        return par_index.geometry.loc[ab]
    return None
