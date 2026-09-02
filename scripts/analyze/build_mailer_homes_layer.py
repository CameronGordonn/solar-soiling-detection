#!/usr/bin/env python3
"""Build the deep-linkable "my home" dashboard layer for the test-50 mailer recipients.

Each postcard QR resolves to ``dashboard.html?id=permit-<N>`` where N is the mailer's
``array_id``. This script turns the 50 mailer rows into the public ``window.MAILER_HOMES``
artifact the dashboard focuses + highlights on QR arrival:

  1. Read the mailer targets CSV (array_id, system_kw, mailing_address, apn).
  2. Geocode each ``mailing_address`` via the US Census batch geocoder (free, one call).
  3. SOMOSclean-score each home (``score_arrays``) — the dashboard's ``somos_score`` model.
  4. Write the PRIVATE full output (mailer_homes_scored.geojson — all attributes).
  5. Emit the REDACTED public artifact (mailer_homes.js — array_id + somos_score + system_kw
     + Point only; NO address/owner/APN, matching the permit_homes.js privacy split).

The ``array_id`` is published as the opaque token ``permit-<N>`` so it never collides with the
detected-array ids (1..334) the dashboard's ``?id=`` handler also searches.

Run (geopandas needs the conda env):
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/build_mailer_homes_layer.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

# Reuse the battle-tested Census batch geocoder from the full permit-homes pipeline.
from scripts.analyze.score_permit_homes import census_batch  # noqa: E402

ZIP_RE = re.compile(r"\b(\d{5})\b")


def parse_address(addr: str) -> tuple[str, str, str]:
    """Split 'street, city, CA zip' (city optional) into (street, city, zip)."""
    parts = [p.strip() for p in str(addr).split(",")]
    street = parts[0] if parts else ""
    m = ZIP_RE.search(addr)
    zipc = m.group(1) if m else ""
    city = ""
    for i, p in enumerate(parts):
        u = p.upper()
        if u == "CA" or u.startswith("CA "):
            if i > 0 and not ZIP_RE.search(parts[i - 1]):
                city = parts[i - 1]
            break
    # Guard: if the "city" we found is actually the street (no comma'd city), blank it.
    if city == street:
        city = ""
    return street, city, zipc


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--targets", default="outputs/outreach/test50_permit_geocoded.csv", type=Path)
    p.add_argument("--state", default="CA")
    p.add_argument("--outdir", default="outputs/permit_homes", type=Path)
    p.add_argument("--public-js",
                   default=str(REPO.parent / "BBF-Website" / "public" / "tools" / "mailer_homes.js"),
                   help="where to write the redacted window.MAILER_HOMES artifact")
    args = p.parse_args(argv)

    import geopandas as gpd  # imported here so --help works without the conda env

    args.outdir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(args.targets)
    parsed = df["mailing_address"].map(parse_address)
    df["street"] = [s for s, _, _ in parsed]
    df["city"] = [c for _, c, _ in parsed]
    df["zip"] = [z for _, _, z in parsed]
    print(f"{len(df)} mailer homes; {(df['city'] != '').sum()} have a city, "
          f"{(df['zip'] != '').sum()} have a ZIP")

    rows = [{"id": str(r.array_id), "street": r.street, "city": r.city,
             "state": args.state, "zip": r.zip} for r in df.itertuples()]
    print(f"Geocoding {len(rows)} addresses via US Census batch geocoder…")
    geo = census_batch(rows)
    matched = geo[geo["matched"]].copy()
    print(f"Match rate: {len(matched)}/{len(geo)} = {len(matched)/max(len(geo),1):.1%}")

    merged = df.merge(matched[["id", "lat", "lon", "matched_address"]],
                      left_on=df["array_id"].astype(str), right_on="id", how="inner")
    if len(merged) < len(df):
        missed = sorted(set(df["array_id"].astype(str)) - set(merged["id"]))
        print(f"⚠️  {len(missed)} homes did not geocode and are dropped: {missed}")

    pts = gpd.GeoDataFrame(
        merged, geometry=gpd.points_from_xy(merged["lon"], merged["lat"]), crs="EPSG:4326")

    print(f"Scoring {len(pts)} homes with SOMOSclean (per-home weather fetch, cached)…")
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from risk.physics_score import score_arrays
    scored = score_arrays(pts)
    ok = scored["risk_score"].notna().sum()
    print(f"Scored OK: {ok}/{len(scored)} (NaN scores dropped from public output)")

    # ---- private full output ----
    priv = args.outdir / "mailer_homes_scored.geojson"
    scored.to_file(priv, driver="GeoJSON")
    print(f"[private] {len(scored)} homes → {priv}")

    # ---- redacted public artifact ----
    pub = scored[scored["risk_score"].notna()].copy()
    feats = []
    for r in pub.itertuples():
        props = {"array_id": f"permit-{int(r.array_id)}",
                 "somos_score": round(float(r.risk_score), 4)}
        kw = getattr(r, "system_kw", None)
        if pd.notna(kw):
            props["system_kw"] = round(float(kw), 2)
        feats.append({
            "type": "Feature",
            "properties": props,
            "geometry": {"type": "Point",
                         "coordinates": [round(r.geometry.x, 6), round(r.geometry.y, 6)]},
        })
    fc = {"type": "FeatureCollection", "features": feats}
    js_path = Path(args.public_js)
    js_path.parent.mkdir(parents=True, exist_ok=True)
    js_path.write_text("window.MAILER_HOMES=" + json.dumps(fc, separators=(",", ":")) + ";\n")
    print(f"[public] {len(feats)} redacted mailer homes → {js_path}")
    print("    fields per feature: array_id (permit-N), somos_score, system_kw? (no PII)")


if __name__ == "__main__":
    main()
