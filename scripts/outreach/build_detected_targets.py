#!/usr/bin/env python3
"""Build the test batch from DETECTED arrays, addresses resolved via the county parcel service.

The "smart APN reader": every detected array sits on a parcel with an APN. The Santa Cruz County
maillists parcel layer (layer 5) maps APN -> owner name + owner mailing address + situs. So we can
mail any detected rooftop — not just the ~32 that happened to have a solar permit on record.

  detected array -> parcel APN -> county parcel service -> OWNNAME + SITEADD/SITCITY/SITZIP

Mail goes to the **situs** (the property the panels are on) — always inside the Santa Cruz AOI —
not the owner's mailing address (which, for absentee owners, is often out of state). By default we
also keep only **owner-occupied** parcels (HOMEOWNER='HOE'); rentals/LLCs rarely convert on a
panel-soiling pitch. Pass --include-absentee to override.

Each card: QR -> dashboard.html?id=<array_id>&addr=...  (lands on the rich detected-array view).
Owner name + situs address are used for the physical mail only; the public dashboard layer still
carries no address. Ranked by net-$ recoverable, top N.

Run:  PYTHONPATH=. conda run -n solar-soiling python scripts/outreach/build_detected_targets.py --top 50
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import quote

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))
from risk.economics import M2_PER_KW, array_recommendation  # noqa: E402

PARCEL_URL = ("https://sccgis.santacruzcountyca.gov/server/rest/services/"
              "maillists/MapServer/5/query")
DASH = "https://betterbehaviorfoundation.com/tools/dashboard.html?id="
LOSS_PCT = 4.6
norm = lambda s: re.sub(r"\D", "", str(s))            # noqa: E731
COLS = ["array_id", "expected_net_usd", "system_kw", "loss_pct", "risk_score",
        "area_m2", "apn", "year", "owner_name", "mailing_address", "qr_url"]

# Assessor names are "LAST FIRST M <legal-suffix>" — strip the legal codes and title-case.
_SUFFIX = re.compile(r"\b(H/W|M/W|W/W|JT|CP|SS|TR|TRUST|LLC|REV|FAMILY|ETAL|ET AL|LIV|LIVING|"
                     r"TRUSTEE|TTEE|SURV|JTRS|LP|INC)\b", re.I)


def clean_owner(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        return "Current Resident"
    n = _SUFFIX.sub("", name)
    n = re.sub(r"[&/].*$", "", n)          # drop co-owner tail after & or /
    n = re.sub(r"\s+", " ", n).strip(" ,")
    return n.title() or "Current Resident"


def resolve_apns(apns: list[str]) -> dict[str, dict]:
    """Batch-query the county parcel service: APNNODASH -> owner/address attributes."""
    out: dict[str, dict] = {}
    for i in range(0, len(apns), 80):
        chunk = apns[i:i + 80]
        where = "APNNODASH IN (%s)" % ",".join("'%s'" % a for a in chunk)
        q = urllib.parse.urlencode({
            "where": where,
            "outFields": ("APNNODASH,APN,OWNNAME,HOMEOWNER,"
                          "SITEADD,SITCITY,SITZIP"),
            "returnGeometry": "false", "f": "json"})
        try:
            r = json.load(urllib.request.urlopen(f"{PARCEL_URL}?{q}", timeout=60))
            for f in r.get("features", []):
                out[f["attributes"]["APNNODASH"]] = f["attributes"]
        except Exception as e:  # noqa: BLE001
            print(f"  chunk {i} error: {e}")
        time.sleep(0.3)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--arrays", type=Path,
                   default=REPO / "outputs/aoi/santa-cruz-outreach-v1/risk_permitkw.geojson")
    p.add_argument("--top", type=int, default=50)
    p.add_argument("--min-area", type=float, default=12.0, help="residential floor (m^2)")
    p.add_argument("--max-area", type=float, default=85.0, help="residential ceiling (m^2, drops commercial)")
    p.add_argument("--include-absentee", action="store_true",
                   help="also mail non-owner-occupied parcels (default: owner-occupied only — "
                        "HOMEOWNER='HOE'. Absentee parcels are rentals/LLCs where the resident "
                        "isn't the decision-maker, so the soiling pitch rarely converts).")
    p.add_argument("--out", type=Path, default=REPO / "outputs/outreach/detected_targets.csv")
    args = p.parse_args(argv)
    owner_occupied_only = not args.include_absentee

    d = json.load(open(args.arrays))
    best: dict[str, dict] = {}  # one (largest) detected array per parcel
    for f in d["features"]:
        pr = f["properties"]; a = norm(pr.get("apn"))
        if not a:
            continue
        if a not in best or (pr.get("area_m2") or 0) > (best[a].get("area_m2") or 0):
            best[a] = pr
    print(f"unique detected parcels: {len(best)}")

    parcels = resolve_apns(sorted(best))
    print(f"resolved by county service: {len(parcels)}")

    rows = []
    n_absentee = 0
    for a, pr in best.items():
        area = pr.get("area_m2") or 0
        if not (args.min_area <= area <= args.max_area):
            continue  # residential band only — drops commercial roofs and noise
        att = parcels.get(a)
        if not att:
            continue
        # Owner-occupied filter: HOMEOWNER='HOE' = a homeowner's-exemption parcel (owner lives
        # there). Absentee parcels are rentals/LLCs/trusts — the resident isn't the decision-maker
        # and the owner mails out-of-area, so the soiling pitch is a poor spend. Skip by default.
        homeowner = str(att.get("HOMEOWNER") or "").strip().upper() == "HOE"
        if owner_occupied_only and not homeowner:
            n_absentee += 1
            continue
        # Mail to the SITUS (the property the panels are on) — always inside the Santa Cruz AOI,
        # never the owner's out-of-state mailing address.
        siteadd = str(att.get("SITEADD") or "").strip()
        zipc = str(att.get("SITZIP") or "").strip()[:5]
        if not re.match(r"^\d", siteadd) or not zipc:
            continue  # need a house-numbered, zipped situs address
        city = str(att.get("SITCITY") or "").strip().title()
        mailing = f"{siteadd.title()}, {city}, CA {zipc}"
        # Area-derived kW — permit_kw mismatches (e.g. a 95 kW permit on a 38 m^2 roof).
        kw = round(area / M2_PER_KW, 2)
        aid = int(pr["array_id"])
        # Greeting address on the dashboard = the situs street (house # + street).
        street = siteadd.split(",")[0].title()
        rows.append({
            "array_id": aid,
            "expected_net_usd": round(array_recommendation(LOSS_PCT, kw)["expected_net_usd"], 2),
            "system_kw": kw, "loss_pct": LOSS_PCT,
            "risk_score": round(float(pr.get("risk_score") or 0), 3),
            "area_m2": round(float(pr.get("area_m2") or 0), 1),
            "apn": att.get("APN") or pr.get("apn"), "year": "",
            "owner_name": clean_owner(att.get("OWNNAME")),
            "mailing_address": mailing,
            "qr_url": f"{DASH}{aid}&addr={quote(street)}",
        })
    if owner_occupied_only:
        print(f"skipped absentee (non owner-occupied) parcels: {n_absentee}")
    df = pd.DataFrame(rows).sort_values("expected_net_usd", ascending=False)
    print(f"owner-occupied homes with a mailable situs address: {len(df)}")
    df = df.head(args.top)
    # Renumber array_id to a 1..N card number for unique PDF filenames; real id lives in qr_url.
    df["card_no"] = range(1, len(df) + 1)
    df_out = df.copy()
    df_out["array_id"] = df_out["card_no"]
    df_out[COLS].to_csv(args.out, index=False)
    print(f"[ok] {len(df_out)} detected cards → {args.out}")
    print("  sample:", df_out.iloc[0]["owner_name"], "|", df_out.iloc[0]["mailing_address"],
          "|", df_out.iloc[0]["qr_url"])


if __name__ == "__main__":
    main()
