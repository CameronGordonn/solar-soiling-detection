"""Join solar permit dates onto detected sites: tariff vintage + lidar-era flag.

Two products from one join, both needed downstream:

**Tariff vintage.** A NEM 1.0/2.0 home is worth **2.78x** more per soiling-lost kWh than an
identical NBT home (``src/risk/rates.py``) -- the sharpest targeting signal in the project,
and a public-records lookup rather than a modelling problem.

**Lidar era.** ``roof_planes.csv`` fits a plane to a 2020 flight. An array installed BEFORE
that flight was itself scanned, so the fit is true **racking angle**; one installed after it
means the laser saw bare roof, so the fit is **roof pitch** -- correct for a flush mount and
wrong for anything tilt-racked. Only the permit date can tell these apart.

    PYTHONPATH=. python3 scripts/analyze/join_permit_vintage.py \
        --sites   outputs/aoi/santa-cruz-w2-21cm/site_economics.csv \
        --permits data/external/sc_solar_permits.csv \
        --out     outputs/aoi/santa-cruz-w2-21cm/site_vintage.csv

**The censoring is the important output, not a nuisance.** County permit records only begin
in 2016, and the City of Santa Cruz is a separate jurisdiction we hold nothing for. So a site
with no permit match is NOT "no solar" -- it is a detected array whose paperwork we cannot
see, and it is disproportionately likely to be *pre-2016*, i.e. NEM 1.0, i.e. **the most
valuable homes in the AOI**. That population is reported explicitly rather than defaulted
into any tariff.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

# PG&E closed NEM 1.0 when it hit the 5% cap; the NBT (NEM 3.0) transition followed CPUC
# D.22-12-056. Both are HARD CLIFFS in a 2.78x multiplier, so they are named constants and
# not inline literals.
#
# VERIFY BEFORE PUBLISHING either date against the CPUC decision -- an off-by-weeks error
# mis-prices every home near the boundary, and the boundary is where the money is.
NEM1_CLOSE = pd.Timestamp("2016-12-15")
NBT_START = pd.Timestamp("2023-04-14")

#: Permit ISSUE date is a proxy for INTERCONNECTION date, which is what the tariff actually
#: attaches to. PTO commonly lags issue by 1-3 months, so sites issued within this window of
#: a cliff are genuinely ambiguous and are flagged rather than assigned.
CLIFF_AMBIGUITY = pd.Timedelta(days=120)

#: The 3DEP collection is named CA_SantaCruzCounty_2020 but no per-swath acquisition date is
#: published in the EPT metadata, so the flight is bracketed rather than pinned. Installs
#: inside the bracket cannot be classified.
LIDAR_BEFORE = pd.Timestamp("2020-01-01")
LIDAR_AFTER = pd.Timestamp("2021-01-01")


def classify_vintage(d: pd.Timestamp) -> str:
    if pd.isna(d):
        return "unknown"
    if d < NEM1_CLOSE:
        return "nem1"
    if d < NBT_START:
        return "nem2"
    return "nbt"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", required=True)
    ap.add_argument("--permits", default="data/external/sc_solar_permits.csv")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    sites = pd.read_csv(args.sites)
    permits = pd.read_csv(args.permits)
    permits["dt"] = pd.to_datetime(permits["date_issued"], errors="coerce")
    permits = permits.dropna(subset=["dt", "apn"])

    # Earliest permit per parcel = the original install, which is what sets tariff vintage.
    # Later permits on the same APN are battery adds, expansions and re-roofs; they do not
    # move a legacy home off its grandfathered tariff.
    first = (permits.sort_values("dt").groupby("apn", as_index=False)
             .agg(install_date=("dt", "first"), permit_kw=("kw", "first"),
                  n_permits=("dt", "size"), last_permit_date=("dt", "last")))

    sites = sites.copy()
    sites["apn"] = sites["site_id"].where(sites["site_id"].str.startswith("apn:"))\
                                   .str.replace("apn:", "", regex=False)
    out = sites.merge(first, on="apn", how="left")

    out["tariff_vintage"] = out["install_date"].map(classify_vintage)
    # Distance to the nearer cliff, so an ambiguous assignment is visible as such.
    d = out["install_date"]
    near = pd.concat([(d - NEM1_CLOSE).abs(), (d - NBT_START).abs()], axis=1).min(axis=1)
    out["vintage_ambiguous"] = near < CLIFF_AMBIGUITY

    out["lidar_era"] = "unknown"
    out.loc[d < LIDAR_BEFORE, "lidar_era"] = "pre_flight"        # lidar scanned the modules
    out.loc[d >= LIDAR_AFTER, "lidar_era"] = "post_flight"       # lidar scanned bare roof
    out.loc[d.between(LIDAR_BEFORE, LIDAR_AFTER), "lidar_era"] = "ambiguous"
    out["roof_geom_meaning"] = out["lidar_era"].map(
        {"pre_flight": "racking_angle", "post_flight": "roof_pitch"}).fillna("indeterminate")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)

    n = len(out)
    matched = out["install_date"].notna()
    print(f"wrote {args.out}\n")
    print(f"sites: {n}   matched to a permit: {matched.sum()} ({100*matched.mean():.1f}%)")
    print(f"UNMATCHED: {(~matched).sum()} -- county records start 2016 and the City of "
          f"Santa Cruz\n  is a separate jurisdiction, so these skew pre-2016 (NEM 1.0), "
          f"the most valuable\n  homes in the AOI. Not assigned a tariff.\n")

    print("=== tariff vintage ===")
    vc = out["tariff_vintage"].value_counts()
    for k in ("nem1", "nem2", "nbt", "unknown"):
        if k in vc:
            print(f"  {k:8s} {vc[k]:5d}  ({100*vc[k]/n:5.1f}%)")
    amb = out["vintage_ambiguous"].fillna(False).sum()
    print(f"  within {CLIFF_AMBIGUITY.days}d of a tariff cliff (issue-vs-PTO lag): {amb}")

    print("\n=== what roof_planes.csv actually measures, per site ===")
    for k, v in out["roof_geom_meaning"].value_counts().items():
        print(f"  {k:15s} {v:5d}  ({100*v/n:5.1f}%)")

    if matched.any() and "system_kw" in out.columns:
        print("\n=== permit kW vs area-estimated kW (sanity on the detector) ===")
        both = out[matched & out["permit_kw"].notna() & out["system_kw"].notna()]
        if len(both):
            r = both["system_kw"] / both["permit_kw"]
            print(f"  n={len(both)}  ratio p10 {r.quantile(.1):.2f}  "
                  f"median {r.median():.2f}  p90 {r.quantile(.9):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
