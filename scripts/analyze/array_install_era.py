"""Attach install year, tariff regime and inverter architecture to each array.

WHY THIS IS THE HIGHEST-VALUE MISSING LAYER
-------------------------------------------
`aoi_cleaning_threshold.py` swings from **0 of 2,494 arrays clearing** to **231
(9.3%)** on one binary: whether the array runs a string inverter or module-level
power electronics. Nothing else in the model moves the answer that far, because
the substring physics is what makes biological soiling expensive and MLPE is what
defuses it.

    portrait, f=0.30 band:   string 18.39% of array output  |  MLPE 4.01%

So a per-roof answer is worthless without this field, and it is free to derive.

HOW ARCHITECTURE IS INFERRED, AND WHY IT IS DEFENSIBLE
-----------------------------------------------------
Not from equipment records: only 129 of 5,950 Santa Cruz PV permits mention any
inverter, and 10 name Enphase. The inference runs off install YEAR instead,
because a building-code change made the choice for installers:

  **NEC 690.12(B)(2), effective 2019-01-01**, limits conductors inside the array
  boundary to 80 V within 30 s of shutdown. A conventional 600 V string cannot
  comply. The two compliant designs are microinverters and DC optimisers, both
  module-level. California adopted NEC 2014 on 2017-01-01 and NEC 2017 after.

That makes install year a strong instrument for architecture:

  <= 2016  : string plausible; Enphase already had large CA residential share,
             so treat as MIXED and carry the fork rather than assuming.
  2017-18  : transitional. Rapid-shutdown rules tightening, MLPE rising.
  >= 2019  : effectively ALL MLPE. This is a code requirement, not a preference.

Tariff regime comes from the same date, against a different cliff: **NEM 2.0
legacy requires interconnection before 2023-04-15** (Cal. P.U.C. Schedule NEM2,
legacy period 20 years from PTO), after which NBT/NEM 3.0 applies. Permit issue
date precedes PTO, so using it slightly OVER-counts NEM 2.0; flagged in output.

The join is parcel-based (APN), matching `permit_recall_audit.py`, because
geocoded points scatter and the APN join was measured as the defensible one
(73.6% imaged-era recall vs 29.0% for a 15 m point match).

Usage:
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/array_install_era.py \
        --partner-id santa-cruz-w2-21cm
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd
import pyproj

_SHARE = os.path.dirname(pyproj.datadir.get_data_dir())
os.environ.setdefault("PROJ_DATA", pyproj.datadir.get_data_dir())
os.environ.setdefault("PROJ_LIB", pyproj.datadir.get_data_dir())
os.environ.setdefault("GDAL_DATA", os.path.join(_SHARE, "gdal"))

import geopandas as gpd  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
UTM = "EPSG:32610"

#: NEC 690.12(B)(2) effective date. From here, module-level electronics are
#: effectively mandatory on residential rooftops.
MLPE_MANDATORY_YEAR = 2019
#: Below this, string inverters were still routinely installed.
STRING_PLAUSIBLE_YEAR = 2017
#: NEM 2.0 legacy requires interconnection before this date.
NEM2_CUTOFF = pd.Timestamp("2023-04-15")


def apn_base(apn) -> str | None:
    """Normalise an APN to digits only, so 109-171-29 == 10917129."""
    if pd.isna(apn):
        return None
    d = "".join(ch for ch in str(apn) if ch.isdigit())
    return d or None


def architecture(year: float | None) -> str:
    if year is None or pd.isna(year):
        return "unknown"
    if year >= MLPE_MANDATORY_YEAR:
        return "mlpe"
    if year >= STRING_PLAUSIBLE_YEAR:
        return "transitional"
    return "mixed_pre2017"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--partner-id", default="santa-cruz-w2-21cm")
    ap.add_argument("--permits", type=Path,
                    default=REPO / "data/external/sc_solar_permits.csv")
    ap.add_argument("--parcels", type=Path,
                    default=REPO / "data/external/santa_cruz_parcels/aoi_santa-cruz-outreach-v1.geojson")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    arrays_path = REPO / "outputs" / "aoi" / args.partner_id / "arrays.geojson"
    arrays = gpd.read_file(arrays_path).to_crs(UTM)
    arrays["array_idx"] = range(len(arrays))
    print(f"arrays: {len(arrays)}")

    if not args.parcels.exists():
        cands = sorted((REPO / "data/external").glob("*parcel*"))
        print(f"parcels not at {args.parcels}; candidates: {[c.name for c in cands]}")
        if not cands:
            sys.exit("no parcel layer found; cannot do the APN join")
        args.parcels = cands[0]
        print(f"using {args.parcels}")

    parcels = gpd.read_file(args.parcels).to_crs(UTM)
    apn_col = next((c for c in parcels.columns if c.upper() == "APN"), None)
    if apn_col is None:
        sys.exit(f"no APN column in parcels; have {list(parcels.columns)[:12]}")
    parcels["apn_base"] = parcels[apn_col].map(apn_base)

    # Which parcel does each array sit on?
    pts = arrays.copy()
    pts["geometry"] = arrays.representative_point()
    joined = gpd.sjoin(pts[["array_idx", "geometry"]], parcels[["apn_base", "geometry"]],
                       predicate="within", how="left")
    joined = joined.drop_duplicates("array_idx")
    print(f"arrays landing on a parcel: {joined.apn_base.notna().sum()}")

    perm = pd.read_csv(args.permits, low_memory=False)
    perm = perm[perm.description.str.contains("photovolt|solar electric|PV ",
                                              case=False, na=False)].copy()
    perm["apn_base"] = perm["apn"].map(apn_base)
    perm["issued"] = pd.to_datetime(perm["date_issued"], errors="coerce")
    perm = perm.dropna(subset=["apn_base"])
    # A parcel can carry several permits (re-roof, expansion). The FIRST PV permit
    # is what sets both the tariff vintage and the era the hardware came from.
    first = (perm.sort_values("issued").groupby("apn_base")
             .agg(install_year=("year", "first"), issued=("issued", "first"),
                  permit_kw=("kw", "first")).reset_index())
    print(f"parcels with a PV permit: {len(first)}")

    out = joined[["array_idx", "apn_base"]].merge(first, on="apn_base", how="left")
    out["architecture"] = out.install_year.map(architecture)
    out["tariff"] = out.issued.map(
        lambda d: "unknown" if pd.isna(d) else
        ("nem2_legacy" if d < NEM2_CUTOFF else "nbt_no_battery"))

    matched = out.install_year.notna()
    print(f"\narrays matched to a PV permit: {int(matched.sum())} "
          f"({100*matched.mean():.1f}%)")
    print("\ninstall era:")
    print(out.architecture.value_counts().to_string())
    print("\ntariff regime:")
    print(out.tariff.value_counts().to_string())
    print("\ninstall year (matched only):")
    print(out.loc[matched, "install_year"].astype(int).value_counts().sort_index().to_string())

    n_string_nem2 = int(((out.architecture == "mixed_pre2017")
                         & (out.tariff == "nem2_legacy")).sum())
    print(f"\nBOTH favourable (pre-2017 hardware AND NEM 2.0): {n_string_nem2} arrays "
          f"({100*n_string_nem2/len(out):.1f}% of {len(out)})")
    print("  This is the ONLY population `aoi_cleaning_threshold.py` shows clearing.")
    print("\n  THIS COUNT IS BIASED IN BOTH DIRECTIONS. Do not read it as a bound.")
    print("   TOO LOW: the permit file starts in 2016, so every pre-2016 install is")
    print("     invisible and lands in 'unknown'. California residential solar boomed")
    print("     2010-2015, so the genuine pre-2017 population is materially larger")
    print(f"     than {n_string_nem2}; {int((~matched).sum())} arrays are unmatched.")
    print("   TOO HIGH: 'pre-2017' is NOT 'string inverter'. Enphase already held")
    print("     large California residential share well before 2017, so an unknown")
    print("     share of this group is already MLPE and does not qualify.")
    print("   TOO HIGH: permit ISSUE date precedes PTO, so NEM 2.0 is over-counted")
    print("     near the 2023-04-15 cliff.")
    print("\n  Closing the first gap needs pre-2016 permit records; closing the second")
    print("  needs equipment data the permits do not carry. Until then this layer")
    print("  narrows the question, it does not settle it.")

    dest = args.out or (REPO / "outputs" / "aoi" / args.partner_id / "array_install_era.csv")
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(dest, index=False)
    print(f"\nwrote {dest}")


if __name__ == "__main__":
    main()
