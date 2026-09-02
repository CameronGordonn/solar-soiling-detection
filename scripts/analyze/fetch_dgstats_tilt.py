"""Cache the CaliforniaDGStats installer-reported tilt for the AOI county, and
check it against our 3DEP lidar plane fits.

WHY THIS SCRIPT EXISTS. `docs/HANDOFF_roof_geometry_for_paper.md` §3 "Result 0"
calls the two-method tilt agreement the credibility anchor of the whole geometry
method: PG&E interconnection paperwork and our lidar fits both land on a 19.0
degree median, from different instruments a decade apart. That document then
flags the anchor as the one number in it that does NOT reproduce -- the DGStats
side had no cached file and no script behind it. This closes that gap.

THE CONVENTION THAT DECIDES THE NUMBER. `Tilt` uses **0.0 as "not reported"**,
not as "flat array". 30.9% of Santa Cruz residential PV rows are exactly 0.0.
The evidence that 0.0 is a null and not a measurement: requiring
`Mounting Method == "Rooftop"` drops the share of exact zeros from 30.9% to
1.7%, i.e. the zeros are concentrated in rows where the mounting field was also
left blank. Treating them as real flattens the median from 19.0 to 18.0 degrees.
So the filter below drops them, and the script prints both so the choice is
visible rather than buried.

WHY THE PRIMARY FILTER IS NOT "Rooftop". `Mounting Method` is itself null on
30% of rows, so requiring it discards a third of the sample on the basis of a
field whose absence is what we are already correcting for. `tilt > 0` is the
primary cut and Rooftop is reported as a robustness check. Every variant lands
on 19.0; the anchor does not depend on the choice.

Usage:
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/fetch_dgstats_tilt.py
    # --refresh to re-download; --keep-zip to keep the 142 MB source archive

Writes:
    data/external/dgstats/santa_cruz_interconnected_pv.csv   the cached extract
    data/external/dgstats/provenance.json                    source, date, filter, counts

`/data/` is gitignored, so `git add -f` the two files above or a fresh clone
cannot reproduce the anchor -- the same hazard as the force-added JSONs in
`docs/HANDOFF_20260827.md`.
"""

from __future__ import annotations

import argparse
import io
import json
import zipfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The "Interconnected Project Sites Data Set" download on californiadgstats.ca.gov.
#: The URL looks like a directory listing and is in fact the zip itself.
SOURCE_URL = "https://www.californiadgstats.ca.gov/download/interconnection_rule21_projects/"
#: Santa Cruz County is PG&E territory, so the SCE and SDG&E members are skipped.
UTILITY_PREFIX = "PGE_"

COUNTY = "SANTA CRUZ"
CACHE_DIR = REPO_ROOT / "data" / "external" / "dgstats"
EXTRACT_CSV = CACHE_DIR / "santa_cruz_interconnected_pv.csv"
PROVENANCE = CACHE_DIR / "provenance.json"
ZIP_PATH = CACHE_DIR / "interconnected_project_sites.zip"

ROOF_PLANES = REPO_ROOT / "outputs" / "aoi" / "santa-cruz-w2-21cm" / "roof_planes.csv"

KEEP_COLS = [
    "Application Id", "Service City", "Service County", "Technology Type",
    "System Size DC", "Tilt", "Azimuth", "Mounting Method", "Tracking",
    "Customer Sector", "App Approved Date", "NEM Tariff", "Interconnection Program",
]

QUANTILES = [0.10, 0.25, 0.50, 0.75, 0.90]


def download(refresh: bool) -> Path:
    if ZIP_PATH.exists() and not refresh:
        print(f"using cached archive {ZIP_PATH} ({ZIP_PATH.stat().st_size/1e6:.0f} MB)")
        return ZIP_PATH
    import urllib.request
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"downloading {SOURCE_URL} ...")
    tmp = ZIP_PATH.with_suffix(".zip.part")
    urllib.request.urlretrieve(SOURCE_URL, tmp)
    tmp.rename(ZIP_PATH)
    print(f"  -> {ZIP_PATH} ({ZIP_PATH.stat().st_size/1e6:.0f} MB)")
    return ZIP_PATH


def extract_county(zip_path: Path) -> pd.DataFrame:
    """Stream the PG&E members and keep only the AOI county.

    The members total 615 MB uncompressed, so they are read through the zip in
    chunks rather than expanded to disk. Several columns carry embedded newlines
    inside quoted headers (the VNEM / NEM-V / NEM-Agg block), so this must go
    through a real CSV parser -- awk or grep on these files silently mis-splits.
    """
    frames, scanned = [], 0
    with zipfile.ZipFile(zip_path) as zf:
        members = [n for n in zf.namelist() if n.startswith(UTILITY_PREFIX)]
        if not members:
            raise SystemExit(f"no {UTILITY_PREFIX}* members in {zip_path}")
        for name in sorted(members):
            with zf.open(name) as fh:
                reader = pd.read_csv(
                    io.TextIOWrapper(fh, encoding="utf-8", errors="replace"),
                    usecols=KEEP_COLS, chunksize=200_000, dtype=str,
                    low_memory=False, on_bad_lines="skip",
                )
                for chunk in reader:
                    scanned += len(chunk)
                    hit = chunk["Service County"].astype(str).str.strip().str.upper()
                    sel = chunk[hit.str.contains(COUNTY, na=False)]
                    if len(sel):
                        frames.append(sel)
            print(f"  {name}: scanned, running county total "
                  f"{sum(len(f) for f in frames):,}")
    df = pd.concat(frames, ignore_index=True)
    # The Historical and current members are disjoint in the 2026-07 release,
    # but the guard is cheap and a future release could overlap.
    before = len(df)
    df = df.drop_duplicates(subset=["Application Id"])
    print(f"scanned {scanned:,} PG&E rows -> {before:,} in {COUNTY} County "
          f"({before - len(df)} duplicate application ids dropped)")
    return df


def _q(s: pd.Series) -> dict:
    return {f"p{int(q*100)}": round(float(s.quantile(q)), 2) for q in QUANTILES}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh", action="store_true", help="re-download the archive")
    ap.add_argument("--keep-zip", action="store_true",
                    help="keep the 142 MB source archive after extracting")
    args = ap.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = download(args.refresh)
    df = extract_county(zip_path)

    df["tilt"] = pd.to_numeric(df["Tilt"], errors="coerce")
    df["kw_dc"] = pd.to_numeric(df["System Size DC"], errors="coerce")

    is_pv = df["Technology Type"].astype(str).str.contains("Photovoltaic", na=False)
    is_res = df["Customer Sector"].astype(str).str.strip().eq("Residential")
    is_roof = df["Mounting Method"].astype(str).str.strip().str.lower().eq("rooftop")

    base = df[is_pv & is_res].copy()
    base.to_csv(EXTRACT_CSV, index=False)

    tilt_all = base["tilt"].dropna()
    zeros = int((tilt_all == 0).sum())
    tilt_pos = tilt_all[tilt_all > 0]

    print()
    print("=" * 78)
    print(f"THE FILTER: Service County = {COUNTY}, Technology Type contains "
          f"'Photovoltaic',\n            Customer Sector = 'Residential', Tilt > 0")
    print("=" * 78)
    print(f"  county rows                        {len(df):>7,}")
    print(f"  + PV + residential                 {len(base):>7,}")
    print(f"  + Tilt reported (non-null)         {len(tilt_all):>7,}")
    print(f"  - Tilt == 0.0 ('not reported')     {zeros:>7,}   "
          f"({100*zeros/len(tilt_all):.1f}% of reported)")
    print(f"  = ANCHOR SAMPLE                    {len(tilt_pos):>7,}")
    print()
    print("  median tilt, zeros DROPPED (the filter)   "
          f"{tilt_pos.median():.1f} deg")
    print("  median tilt, zeros KEPT as flat arrays    "
          f"{tilt_all.median():.1f} deg   <- the convention costs 1.0 deg")
    print()

    print("ROBUSTNESS -- every variant lands on the same median")
    print(f"  {'variant':<44} {'n':>7}  {'p50':>6}")
    variants = {
        "PV + residential, tilt>0 (the filter)": df[is_pv & is_res],
        "PV + residential + rooftop, tilt>0": df[is_pv & is_res & is_roof],
        "PV + rooftop, tilt>0 (any sector)": df[is_pv & is_roof],
        "PV, tilt>0 (any sector, any mount)": df[is_pv],
        "all technologies, tilt>0": df,
    }
    for name, sub in variants.items():
        t = pd.to_numeric(sub["Tilt"], errors="coerce").dropna()
        t = t[t > 0]
        print(f"  {name:<44} {len(t):>7,}  {t.median():>6.1f}")
    print()

    lidar_stats = None
    if ROOF_PLANES.exists():
        roof = pd.read_csv(ROOF_PLANES)
        ok = roof[roof["fit_ok"] == True]  # noqa: E712 -- csv round-trips as bool
        lidar = ok["tilt_deg"].dropna()
        lidar_stats = {"n": int(len(lidar)), **_q(lidar)}
        print("THE ANCHOR -- two instruments, one decade apart")
        print(f"  {'source':<44} {'n':>7}  {'p10':>6} {'p50':>6} {'p90':>6}")
        print(f"  {'PG&E interconnection paperwork (DGStats)':<44} {len(tilt_pos):>7,}  "
              f"{tilt_pos.quantile(.10):>6.1f} {tilt_pos.quantile(.50):>6.1f} "
              f"{tilt_pos.quantile(.90):>6.1f}")
        print(f"  {'3DEP lidar plane fits (fit_ok)':<44} {len(lidar):>7,}  "
              f"{lidar.quantile(.10):>6.1f} {lidar.quantile(.50):>6.1f} "
              f"{lidar.quantile(.90):>6.1f}")
        print(f"  {'3DEP lidar, UNFILTERED (do not quote)':<44} "
              f"{roof['tilt_deg'].notna().sum():>7,}  "
              f"{roof['tilt_deg'].quantile(.10):>6.1f} "
              f"{roof['tilt_deg'].quantile(.50):>6.1f} "
              f"{roof['tilt_deg'].quantile(.90):>6.1f}")
        print(f"\n  median difference  {abs(tilt_pos.median()-lidar.median()):.2f} deg")
        print()
        print("  BUT THE TAILS DO NOT AGREE, AND THE REASON IS THE ANCHOR'S OWN FILTER.")
        print(f"  {'share below 5 deg':<44} {'DGStats':>9} {'lidar':>9}")
        print(f"  {'':<44} {100*(tilt_pos<5).mean():>8.2f}% {100*(lidar<5).mean():>8.2f}%")
        print(f"  {'share below 10 deg':<44} "
              f"{100*(tilt_pos<10).mean():>8.2f}% {100*(lidar<10).mean():>8.2f}%")
        print("  A genuinely flat array is exactly the row an installer reports as 0.0,")
        print("  which this filter cannot distinguish from 'not reported' and therefore")
        print("  drops. So the DGStats low tail is biased high BY CONSTRUCTION and the")
        print("  lidar low tail is not. That is not a discrepancy to explain away: the")
        print("  sub-5-degree arrays are the ones Mejia & Kleissl (2013) measured soiling")
        print("  ~5x faster, and the permit record structurally cannot see them.")
        print("  Quote the medians as the agreement; quote the low tail as a capability")
        print("  the lidar has and the paperwork does not.")
        print("  Read as agreement on the CENTRAL TENDENCY only. The two samples are")
        print("  not the same arrays -- DGStats is county-wide permits, the lidar is")
        print("  the 15 km2 imaged AOI -- so the tails differ and no paired test is")
        print("  available without an address join.")
    else:
        print(f"NOTE: {ROOF_PLANES} not present; skipping the lidar comparison.")

    prov = {
        "generated": date.today().isoformat(),
        "source_url": SOURCE_URL,
        "source_files": sorted(
            n for n in zipfile.ZipFile(zip_path).namelist() if n.startswith(UTILITY_PREFIX)
        ),
        "county": COUNTY,
        "filter": {
            "service_county_contains": COUNTY,
            "technology_type_contains": "Photovoltaic",
            "customer_sector": "Residential",
            "tilt": "> 0 (0.0 is the 'not reported' convention, see module docstring)",
            "mounting_method": "not filtered (null on ~30% of rows); reported as robustness",
            "date_range": "not filtered",
            "dedup_key": "Application Id",
        },
        "counts": {
            "county_rows": int(len(df)),
            "pv_residential": int(len(base)),
            "tilt_reported": int(len(tilt_all)),
            "tilt_zero_not_reported": zeros,
            "anchor_sample": int(len(tilt_pos)),
        },
        "app_approved_date_range": [
            str(base["App Approved Date"].dropna().min()),
            str(base["App Approved Date"].dropna().max()),
        ],
        "dgstats_tilt_deg": {"n": int(len(tilt_pos)), **_q(tilt_pos)},
        "lidar_tilt_deg_fit_ok": lidar_stats,
        "low_tilt_share_pct": {
            "note": "DGStats low tail is biased high because tilt==0 is dropped as "
                    "'not reported'; the lidar low tail is unbiased.",
            "dgstats_below_5deg": round(float(100 * (tilt_pos < 5).mean()), 2),
            "dgstats_below_10deg": round(float(100 * (tilt_pos < 10).mean()), 2),
            "lidar_below_5deg": (round(float(100 * (lidar < 5).mean()), 2)
                                 if lidar_stats else None),
            "lidar_below_10deg": (round(float(100 * (lidar < 10).mean()), 2)
                                  if lidar_stats else None),
        },
    }
    PROVENANCE.write_text(json.dumps(prov, indent=2) + "\n")
    print(f"\nwrote {EXTRACT_CSV.relative_to(REPO_ROOT)} "
          f"({EXTRACT_CSV.stat().st_size/1e6:.1f} MB, {len(base):,} rows)")
    print(f"wrote {PROVENANCE.relative_to(REPO_ROOT)}")

    if not args.keep_zip and ZIP_PATH.exists():
        ZIP_PATH.unlink()
        print(f"removed {ZIP_PATH.name} (pass --keep-zip to retain)")


if __name__ == "__main__":
    main()
