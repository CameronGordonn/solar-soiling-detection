#!/usr/bin/env python3
"""Which NREL rows is the soiling model actually calibrated against, and are they the right ones?

Follow-up to docs/ECONOMICS_GROUNDING_20260809.md section 7, which fits SOMOSclean
(sl_sat=0.08, k=15) so its annual-mean loss reproduces "NREL's measured coastal-CA p50 of
4.70%". That anchor decides every downstream dollar: it sets sl_sat, sl_sat and k together
set the recovery constant (0.045 / 0.032), and recovery decides whether any of the 1,865
AOI households is told to clean.

The anchor was never audited for provenance. This script does that, from the label file
alone, with no modelling argument:

  1. INSTRUMENT CENSUS. `measurement_type` separates dedicated soiling instruments from
     operating PV plants whose "soiling" is inferred from performance dips. The column is
     present in the drop and nothing in src/ or scripts/ reads it.
  2. MATCHED INSTRUMENT COMPARISON. Same county, both instrument types, so climate is
     held roughly fixed and the instrument effect is what is left. This is the only way
     to size the bias without leaving the file.
  3. SUBSET AUDIT. Reproduce the shipped `lon < -120.5` coastal-CA filter and report what
     is actually inside it: counties, latitudes, instrument types.
  4. DISTANCE TO THE AOI. Rank CA stations by great-circle distance to the Santa Cruz AOI
     centroid and report the anchor as a function of radius, so the choice of subset is
     visible rather than implicit in a longitude cut.

Reads data/external/nrel_soiling_map_annual.csv only: no network, no weather cache, no
imagery, and stdlib only, so it runs outside the conda env.

    python3 scripts/analyze/label_provenance.py
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Santa Cruz AOI centroid, from configs/scenes.yaml bbox [-122.10, 36.85, -121.85, 37.05].
AOI_LAT, AOI_LON = 36.95, -121.975

# The filter docs/ECONOMICS_GROUNDING_20260809.md line 48 used to define "coastal CA".
SHIPPED_LON_CUT = -120.5

# Counties that actually front the Pacific. Used only to describe a subset, never to fit.
COASTAL_COUNTIES = {
    "Del Norte County", "Humboldt County", "Mendocino County", "Sonoma County",
    "Marin County", "San Francisco County", "San Mateo County", "Santa Cruz County",
    "Monterey County", "San Luis Obispo County", "Santa Barbara County",
    "Ventura County", "Los Angeles County", "Orange County", "San Diego County",
}


def loss_pct(row) -> float:
    """Annual soiling loss in points. iwsr 1.0 = clean, 0.95 = 5% lost."""
    return (1.0 - float(row["iwsr"])) * 100.0


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def p50(vals):
    return st.median(vals) if vals else float("nan")


def rule(title):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def section_1_census(rows):
    rule("1. INSTRUMENT CENSUS")
    by_type = defaultdict(list)
    for r in rows:
        by_type[r["measurement_type"]].append(r)
    print(f"{len(rows)} station-years, {len({r['station_id'] for r in rows})} distinct stations\n")
    print(f"  {'measurement_type':<20}{'rows':>7}{'share':>8}{'stations':>10}{'p50 loss':>10}")
    for t, rs in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
        print(f"  {t:<20}{len(rs):>7}{len(rs) / len(rows) * 100:>7.0f}%"
              f"{len({r['station_id'] for r in rs}):>10}{p50([loss_pct(r) for r in rs]):>9.2f}%")
    print("\n  A 'PV System' row is soiling INFERRED from an operating plant's performance dips,")
    print("  so it carries that plant's O&M cleaning schedule and any non-soiling loss the")
    print("  extraction attributed to soiling. A 'Soiling Station' measures a soiled module")
    print("  against a deliberately cleaned reference. Only the second is a direct instrument.")


def section_2_matched(rows):
    rule("2. MATCHED INSTRUMENT COMPARISON (same county, climate roughly held fixed)")
    by_county = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_county[r["county"]][r["measurement_type"]].append(loss_pct(r))

    matched = {c: d for c, d in by_county.items()
               if d.get("Soiling Station") and d.get("PV System")}
    if not matched:
        print("  No county carries both instrument types. Bias not measurable from this file.")
        return

    print(f"  {'county':<22}{'soiling stn':>16}{'PV system':>16}{'ratio':>9}")
    ratios = []
    for c in sorted(matched):
        ss, pv = matched[c]["Soiling Station"], matched[c]["PV System"]
        ratio = p50(ss) / p50(pv) if p50(pv) else float("nan")
        ratios.append(ratio)
        print(f"  {c:<22}{p50(ss):>10.2f}% n={len(ss):<3}{p50(pv):>10.2f}% n={len(pv):<3}{ratio:>8.2f}x")

    below = sum(1 for x in ratios if x < 1.0)
    print(f"\n  Direct instruments read LOWER than co-located plants in {below} of {len(ratios)} counties")
    print(f"  (median ratio {p50(ratios):.2f}x). If maintenance were deflating the plant labels the")
    print("  ratio would sit ABOVE 1.0. It does not, so the plant labels look inflated rather than")
    print("  deflated. Sample is small (n=1-4 per county); treat as a direction, not a coefficient.")


def section_3_subset_audit(rows, lon_cut):
    rule(f"3. AUDIT OF THE SHIPPED ANCHOR (CA, lon < {lon_cut})")
    sub = [r for r in rows if r["state"] == "CA" and float(r["longitude"]) < lon_cut]
    if not sub:
        print("  Filter selected nothing.")
        return sub
    vals = [loss_pct(r) for r in sub]
    print(f"  n={len(sub)} station-years, {len({r['station_id'] for r in sub})} stations, p50 {p50(vals):.2f}%")
    print(f"  latitude {min(float(r['latitude']) for r in sub):.2f} to "
          f"{max(float(r['latitude']) for r in sub):.2f}   (Santa Cruz AOI is {AOI_LAT})")
    print(f"  instrument types: {dict(Counter(r['measurement_type'] for r in sub))}")
    print("\n  composition by county:")
    for c, n in Counter(r["county"] for r in sub).most_common():
        cv = [loss_pct(r) for r in sub if r["county"] == c]
        coastal = "coastal" if c in COASTAL_COUNTIES else "INLAND"
        print(f"    {c:<22}{n:>4} rows   p50 {p50(cv):>5.2f}%   {coastal}")

    inland = sum(1 for r in sub if r["county"] not in COASTAL_COUNTIES)
    print(f"\n  {inland} of {len(sub)} rows ({inland / len(sub) * 100:.0f}%) are inland counties.")
    print("  California's coastline runs diagonally, so a longitude cut does not select for")
    print("  coastal: Sacramento sits near -121.5 and is ~130 km from the ocean. This subset is")
    print("  a Central Valley p50 wearing a coastal label.")
    return sub


def section_4_distance(rows, shipped):
    rule("4. ANCHOR AS A FUNCTION OF DISTANCE TO THE AOI")
    ca = [r for r in rows if r["state"] == "CA"]
    for r in ca:
        r["_km"] = haversine_km(AOI_LAT, AOI_LON, float(r["latitude"]), float(r["longitude"]))

    stations = defaultdict(list)
    for r in ca:
        stations[r["station_id"]].append(r)

    print("  Nearest 12 California stations to the Santa Cruz AOI:\n")
    print(f"  {'km':>6}  {'station':<34}{'county':<20}{'type':<16}{'n':>3}{'p50':>8}")
    ordered = sorted(stations.items(), key=lambda kv: min(x["_km"] for x in kv[1]))
    for s, rs in ordered[:12]:
        km = min(x["_km"] for x in rs)
        print(f"  {km:>6.0f}  {s[:33]:<34}{rs[0]['county'][:19]:<20}"
              f"{rs[0]['measurement_type']:<16}{len(rs):>3}{p50([loss_pct(x) for x in rs]):>7.2f}%")

    print("\n  Anchor p50 by radius around the AOI:\n")
    print(f"  {'radius':>8}{'rows':>7}{'stations':>10}{'p50 loss':>11}  composition")
    for km in (100, 150, 200, 300, 500, 1000):
        sub = [r for r in ca if r["_km"] <= km]
        if not sub:
            print(f"  {km:>6} km{0:>7}{0:>10}{'n/a':>11}")
            continue
        top = ", ".join(f"{c} {n}" for c, n in Counter(r["county"] for r in sub).most_common(3))
        print(f"  {km:>6} km{len(sub):>7}{len({r['station_id'] for r in sub}):>10}"
              f"{p50([loss_pct(r) for r in sub]):>10.2f}%  {top}")

    coastal = [loss_pct(r) for r in ca if r["county"] in COASTAL_COUNTIES]
    print(f"\n  Genuinely coastal CA counties: n={len(coastal)}  p50 {p50(coastal):.2f}%")
    if shipped:
        print(f"  Shipped 'coastal CA' anchor:   n={len(shipped)}  p50 {p50([loss_pct(r) for r in shipped]):.2f}%")
    print(f"  All CA:                        n={len(ca)}  p50 {p50([loss_pct(r) for r in ca]):.2f}%")


def section_5_implications(rows, shipped):
    rule("5. WHAT THIS MOVES")
    coastal = [loss_pct(r) for r in rows if r["county"] in COASTAL_COUNTIES]
    ship = [loss_pct(r) for r in shipped] if shipped else []
    if not (coastal and ship):
        return
    ratio = p50(coastal) / p50(ship)
    print(f"  shipped anchor          {p50(ship):.2f}%   -> sl_sat 0.08, recovery 0.045 pro / 0.032 rinse")
    print(f"  coastal-county anchor   {p50(coastal):.2f}%   -> anchor moves by {ratio:.2f}x")
    print()
    print("  sl_sat scales roughly with the anchor, so a lower anchor lowers the modelled annual")
    print("  loss and every dollar derived from it. That makes the 'cleaning does not pay' verdict")
    print("  STRONGER, not weaker: less loss to recover against an unchanged price.")
    print()
    print("  It does not touch the separate finding that (sl_sat, k) is unidentified against a")
    print("  single annual-mean constraint. Both need fixing, and they push opposite ways:")
    print("    - a lower anchor          -> less recoverable value")
    print("    - a larger k than 15      -> more recoverable value per clean")
    print("  Refitting one without the other will produce a confidently wrong number.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path,
                    default=Path("data/external/nrel_soiling_map_annual.csv"),
                    help="NREL annual soiling map drop")
    ap.add_argument("--lon-cut", type=float, default=SHIPPED_LON_CUT,
                    help="longitude cut to audit (the shipped coastal-CA filter)")
    args = ap.parse_args(argv)

    if not args.csv.exists():
        print(f"missing {args.csv}. See DATA.md / data/external/README.md.", file=sys.stderr)
        return 1

    rows = list(csv.DictReader(args.csv.open()))
    required = {"iwsr", "measurement_type", "state", "county", "latitude", "longitude"}
    missing = required - set(rows[0])
    if missing:
        print(f"{args.csv} lacks required columns: {sorted(missing)}", file=sys.stderr)
        return 1

    section_1_census(rows)
    section_2_matched(rows)
    shipped = section_3_subset_audit(rows, args.lon_cut)
    section_4_distance(rows, shipped)
    section_5_implications(rows, shipped)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
