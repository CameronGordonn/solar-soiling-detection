#!/usr/bin/env python3
"""Parse County of Santa Cruz "Building Permits Issued" PDFs → solar-PV permit table.

Extracts every solar/photovoltaic permit with its **APN** (parcel number), issue
date, system **kW** (when stated in the description), and mount type, across all
year PDFs. APN is the join key back to detected arrays (APN → parcel polygon →
array footprint), giving each array a *real* permitted system size instead of the
detection-area estimate — the biggest accuracy lever on the net-$ math.

It also yields, for free:
  * market sizing — the kW distribution of local installs (where cleaning pays), and
  * a known-install registry to measure detector recall against (risk M1).

PII: output carries parcel/owner/address, so it writes under ``data/external/``
(gitignored) and must never reach the public repo (see public SYNC.md).

Usage:
    PYTHONPATH=. python scripts/analyze/ingest_permits.py \\
        --permits-dir "/mnt/c/Users/camer/Downloads" \\
        --out data/external/sc_solar_permits.csv
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd
import pdfplumber

# A record starts with an application number then ... Status ... Date ... APN.
REC_START = re.compile(r"^([A-Z]{1,4}-\d{3,7})\s+.*?\b(\d{2}/\d{2}/\d{4})\s+(\d{3}-\d{3}-\d{2})\b")
# Repeating page furniture to drop before splitting into records.
SKIP = re.compile(
    r"C O U N T Y|BUILDING PERMITS ISSUED|Application No\s+Master No|"
    r"^\s*Situs\s*$|OwnerName\s+OwnerAddress|^\d{2}/\d{2}/\d{4}\s*-\s*\d{2}/\d{2}/\d{4}"
)
KW = re.compile(r"(\d{1,4}(?:\.\d{1,2})?)\s*kw", re.I)
SOLAR = re.compile(r"solar|photovolta|\bPV\b", re.I)
MOUNT = re.compile(r"ground[- ]?mount|roof[- ]?mount", re.I)
YEAR = re.compile(r"(20\d{2})")


def parse_pdf(path: Path) -> list[dict]:
    """Return one dict per solar-PV permit found in a single year's PDF."""
    year_m = YEAR.search(path.stem)
    year = int(year_m.group(1)) if year_m else None

    lines: list[str] = []
    with pdfplumber.open(path) as pdf:
        for pg in pdf.pages:
            for ln in (pg.extract_text() or "").splitlines():
                if SKIP.search(ln):
                    continue
                lines.append(ln.rstrip())

    # Split the line stream into records anchored on the header pattern.
    records: list[dict] = []
    cur: dict | None = None
    for ln in lines:
        m = REC_START.match(ln)
        if m:
            if cur:
                records.append(cur)
            cur = {"app_no": m.group(1), "date_issued": m.group(2), "apn": m.group(3), "_body": [ln]}
        elif cur is not None:
            cur["_body"].append(ln)
    if cur:
        records.append(cur)

    out: list[dict] = []
    for r in records:
        blob = " ".join(r["_body"])
        if not SOLAR.search(blob):
            continue
        kw = KW.search(blob)
        mt = MOUNT.search(blob)
        # First body line after the header carries owner + situs + valuation.
        situs_raw = r["_body"][1].strip() if len(r["_body"]) > 1 else ""
        out.append({
            "year": year,
            "app_no": r["app_no"],
            "date_issued": r["date_issued"],
            "apn": r["apn"],
            "kw": float(kw.group(1)) if kw else None,
            "mount": mt.group(0).lower().replace(" ", "-") if mt else None,
            "situs_raw": situs_raw[:160],
            "description": blob[:300],
            "source_pdf": path.name,
        })
    return out


def summarize(df: pd.DataFrame) -> None:
    print(f"\n=== {len(df)} solar permits across {df['year'].nunique()} years "
          f"({df['apn'].nunique()} unique parcels) ===")
    with_kw = df["kw"].notna()
    print(f"kW stated: {with_kw.sum()}/{len(df)} ({100*with_kw.mean():.0f}%)")
    if with_kw.any():
        k = df.loc[with_kw, "kw"]
        print(f"kW: median={k.median():.1f}  mean={k.mean():.1f}  p90={k.quantile(0.9):.0f}  max={k.max():.0f}")
        print(f"  systems >8 kW (pro cleaning likely pays): {(k > 8).sum()} "
              f"| >100 kW (commercial): {(k > 100).sum()}")
    print("\nper-year counts:")
    yr = df.groupby("year").agg(permits=("app_no", "count"), with_kw=("kw", lambda s: s.notna().sum()),
                                median_kw=("kw", "median"))
    print(yr.to_string())


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--permits-dir", default="/mnt/c/Users/camer/Downloads", type=Path)
    p.add_argument("--glob", default="Building Permits Issued*.pdf")
    p.add_argument("--out", default="data/external/sc_solar_permits.csv", type=Path)
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    pdfs = sorted(args.permits_dir.glob(args.glob))
    if not pdfs:
        raise SystemExit(f"No permit PDFs matching {args.glob!r} in {args.permits_dir}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    # Write incrementally (one year at a time) so a long run is resumable/visible
    # and partial progress survives an interrupt.
    rows: list[dict] = []
    header_written = False
    for pdf in pdfs:
        recs = parse_pdf(pdf)
        print(f"[ok] {pdf.name}: {len(recs)} solar permits", flush=True)
        rows.extend(recs)
        df_pdf = pd.DataFrame(recs)
        df_pdf.to_csv(args.out, mode="a" if header_written else "w",
                      header=not header_written, index=False)
        header_written = True

    df = pd.DataFrame(rows)
    summarize(df)
    print(f"\n[ok] wrote {len(df)} rows -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
