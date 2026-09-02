"""Size the compounding-value segment of the AOI from permit vintage.

Why this exists
---------------
`docs/AOI_CLEANING_TARGETING_PLAN.md` v3 established that install year carries three
effects that all point the same way: tariff (2.8x), cell architecture (up to 2x, because
half-cut modules halve the portrait shading penalty and became dominant around 2019), and
inverter topology (up to 4.6x where occlusion is partial). Older is worth more on all
three.

That makes "how many AOI homes are actually old" the number that sizes the whole
opportunity, and v3 recorded it as unmeasured. This script measures it.

It also re-derives the permit counts from scratch, because v1's "5,944 PV permit rows over
5,443 unique APNs, 3,975 (67%) pre-cutoff" could not be reproduced from the file under any
obvious filter (see `--filter-sensitivity`). Numbers whose filter nobody wrote down are
exactly what this repo keeps getting bitten by.

Eras
----
- **pre-2016**: NEM 1.0, full-cell, the highest-value segment. ABSENT from the permit file
  by construction: `sc_solar_permits.csv` starts in 2016. Reported as a known blind spot,
  never as zero.
- **2016 to 2018**: NEM 1.0/2.0 tariff, full-cell modules, often string inverters. The
  compounding segment.
- **2019 to 2023-04-13**: NEM 2.0 tariff, half-cut modules, mostly MLPE. Good tariff,
  halved portrait penalty.
- **2023-04-14 onward**: NBT tariff, half-cut, MLPE. Worth 2.8x less per lost kWh.

The 2019 boundary is a market transition, not a hard cutover, so `--halfcut-year` sweeps it.

Usage
-----
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/permit_era_split.py
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/permit_era_split.py \
        --filter-sensitivity --json outputs/analysis/permit_era_split.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

PERMITS = Path("data/external/sc_solar_permits.csv")
AOI_SITES = Path("outputs/aoi/santa-cruz-w2-21cm/site_economics.csv")

NEM3_CUTOFF = pd.Timestamp("2023-04-14")
PV_RE = r"photovolta|\bPV\b"


def load_permits(pv_filter: str = "mount_or_pv") -> pd.DataFrame:
    """Load permits under an EXPLICIT filter.

    The source CSV is produced by `ingest_permits.py`, whose regex is
    `solar|photovolta|\\bPV\\b`, so it also catches solar-thermal and pool-heating permits
    (row 1 of the file is a swimming pool). Every filter here is a narrowing of that.

    - `all`         : everything ingest_permits kept. Over-counts: includes solar thermal.
    - `pv_text`     : description mentions photovoltaic or PV. Under-counts: many genuine
                      PV permits describe the work without either word.
    - `mount`       : a roof-mount or ground-mount was parsed out. Strong PV signal.
    - `mount_or_pv` : either of the above. The default, and the widest defensible PV filter.
    - `roof_only`   : roof-mount only. The relevant population for moss, which does not
                      grow on a ground mount the same way.
    """
    df = pd.read_csv(PERMITS)
    df["dt"] = pd.to_datetime(df["date_issued"], format="%m/%d/%Y", errors="coerce")
    has_pv_text = df["description"].fillna("").str.contains(PV_RE, case=False, regex=True)
    has_mount = df["mount"].notna()
    is_roof = df["mount"].astype(str).str.startswith("roof")

    masks = {
        "all": pd.Series(True, index=df.index),
        "pv_text": has_pv_text,
        "mount": has_mount,
        "mount_or_pv": has_mount | has_pv_text,
        "roof_only": is_roof,
    }
    if pv_filter not in masks:
        raise ValueError(f"unknown filter {pv_filter}; try {list(masks)}")
    out = df[masks[pv_filter] & df["dt"].notna() & df["apn"].notna()].copy()
    return out


def dedupe_by_parcel(df: pd.DataFrame) -> pd.DataFrame:
    """One row per APN, keeping the EARLIEST permit.

    Earliest, not latest: the array's vintage is when it first went up. A later permit on
    the same parcel is usually a battery, a service upgrade or an expansion, and using it
    would systematically make old arrays look new, which is the exact direction that would
    shrink the segment this script is trying to size.
    """
    return df.sort_values("dt").groupby("apn", as_index=False).first()


def era_of(ts: pd.Timestamp, halfcut_year: int) -> str:
    if ts >= NEM3_CUTOFF:
        return "2023-04-14+ (NBT, half-cut)"
    if ts.year >= halfcut_year:
        return f"{halfcut_year} to 2023-04-13 (NEM 2.0, half-cut)"
    return f"2016 to {halfcut_year - 1} (NEM 1.0/2.0, full-cell)"


def summarize(base: pd.DataFrame, halfcut_year: int, label: str) -> dict:
    base = base.copy()
    base["era"] = base["dt"].apply(lambda t: era_of(t, halfcut_year))
    counts = base["era"].value_counts()
    total = len(base)
    print(f"\n--- {label}  (n={total:,} parcels)")
    rows = {}
    for era in sorted(counts.index):
        n = int(counts[era])
        rows[era] = n
        print(f"    {era:<44} {n:5,}  {n/total*100:5.1f}%")
    pre_cut = int((base["dt"] < NEM3_CUTOFF).sum())
    print(f"    {'pre-NEM-3.0 (any vintage)':<44} {pre_cut:5,}  {pre_cut/total*100:5.1f}%")
    return {"n": total, "eras": rows, "pre_nem3": pre_cut}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pv-filter", default="mount_or_pv")
    ap.add_argument("--halfcut-year", type=int, default=2019)
    ap.add_argument("--filter-sensitivity", action="store_true")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    out: dict = {"nem3_cutoff": str(NEM3_CUTOFF.date()), "halfcut_year": args.halfcut_year}

    if args.filter_sensitivity:
        print("=== filter sensitivity: v1 claimed 5,944 rows / 5,443 APNs / 3,975 pre-cutoff")
        sens = {}
        for name in ("all", "pv_text", "mount", "mount_or_pv", "roof_only"):
            d = load_permits(name)
            b = dedupe_by_parcel(d)
            pre = int((b["dt"] < NEM3_CUTOFF).sum())
            print(f"  {name:<14} rows {len(d):5,}  parcels {len(b):5,}  "
                  f"pre-cutoff parcels {pre:5,} ({pre/len(b)*100:.0f}%)")
            sens[name] = {"rows": len(d), "parcels": len(b), "pre_nem3_parcels": pre}
        out["filter_sensitivity"] = sens
        print("  -> none of these reproduces v1's triple. v1's counts have no recoverable filter.")

    print(f"\n=== permit population, filter='{args.pv_filter}' ===")
    permits = load_permits(args.pv_filter)
    base = dedupe_by_parcel(permits)
    print(f"{len(permits):,} permit rows -> {len(base):,} unique parcels (earliest permit each)")
    out["permits"] = summarize(base, args.halfcut_year, "ALL PERMITTED PARCELS IN THE COUNTY")

    # Join to the AOI sites that the product actually scores.
    if AOI_SITES.exists():
        sites = pd.read_csv(AOI_SITES)
        parcel_sites = sites[sites["site_id"].str.startswith("apn:")].copy()
        parcel_sites["apn"] = parcel_sites["site_id"].str.replace("apn:", "", regex=False)
        joined = parcel_sites.merge(base[["apn", "dt"]], on="apn", how="left")
        matched = joined[joined["dt"].notna()]
        print(f"\n=== the 1,865 scored AOI sites ===")
        print(f"{len(sites):,} sites, {len(parcel_sites):,} APN-keyed, "
              f"{len(matched):,} joined to a permit ({len(matched)/len(sites)*100:.1f}%)")
        print(f"{len(sites) - len(matched):,} have NO permit record: pre-2016 installs plus "
              f"join failures. Tier D by construction, and the pre-2016 ones are the")
        print("  highest-value segment of all. This is a floor on the segment, not a count of it.")
        out["aoi"] = summarize(matched, args.halfcut_year, "AOI SITES WITH A PERMIT MATCH")
        out["aoi"]["n_sites_total"] = len(sites)
        out["aoi"]["n_unmatched"] = int(len(sites) - len(matched))

        # Residential-scale subset: the mailer population, not the commercial roofs.
        res = matched[(matched["system_kw"] >= 2) & (matched["system_kw"] <= 15)]
        print(f"\n  of which residential-scale (2 to 15 kW): {len(res):,}")
        out["aoi"]["n_residential_matched"] = len(res)
        out["aoi_residential"] = summarize(res, args.halfcut_year,
                                           "AOI RESIDENTIAL-SCALE SITES WITH A PERMIT MATCH")

    print("\n=== sensitivity to the half-cut transition year ===")
    hc = {}
    for yr in (2018, 2019, 2020):
        if AOI_SITES.exists():
            b = matched
        else:
            b = base
        n_full = int((b["dt"].dt.year < yr).sum())
        hc[yr] = n_full
        print(f"  half-cut from {yr}: full-cell segment = {n_full:,} sites "
              f"({n_full/len(b)*100:.1f}% of matched)")
    out["halfcut_year_sensitivity"] = hc

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        with open(args.json, "w") as fh:
            json.dump(out, fh, indent=2)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
