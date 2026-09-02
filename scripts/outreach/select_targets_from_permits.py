#!/usr/bin/env python3
"""Build a mailer target list from the COUNTY SOLAR-PERMIT REGISTRY (the ~20× sweep).

Detection finds ~334 arrays in one AOI; the public building-permit records list **7,399
solar parcels county-wide** (`data/external/sc_solar_permits.csv`). This script turns those
permits into a **net-$-ranked, generate_mailers-compatible** target list *without* detection —
the §8A bridge from `docs/MAILER_PIPELINE.md`.

Pipeline:
  1. Load permits → keep rows with a stated kW → **dedup to one row per parcel** (max kW, latest year).
  2. Optional system-size filter (`--min-kw` / `--max-kw`, e.g. a homeowner mailer = 4–15 kW).
  3. **Extract a clean street address** from the messy `situs_raw` (drop PO boxes / no-number rows).
  4. Compute **recoverable net-$** via the economics engine and **rank** by it.
  5. Write a CSV with the columns `generate_mailers.py` expects.

⚠️ **Deliverability:** `situs_raw` reliably yields a *street* but not city/ZIP, and the parcel
map has no address. So the output is **not Lob-ready until addresses are completed** — run with
`--geocode` to forward-geocode each street via Nominatim (network, ~1 req/s) into a full
city/state/ZIP, or hand the street list to a batch geocoder. Without `--geocode` you still get
the *ranked target pool* (who to mail, and the dollars), which is the hard part.

⚠️ **Soiling (v1):** uses one regional annual-loss assumption (`--loss-pct`, default 4.6% for
coastal Santa Cruz — note this is *low*; inland is higher). For per-address accuracy, score each
address with `scripts/predict/score_address.py` and merge its `loss_pct`/`net_usd`.

Usage:
    PYTHONPATH=. python scripts/outreach/select_targets_from_permits.py \
        --top 200 --min-kw 4 --max-kw 15 --out outputs/outreach/permit_targets.csv
    #   add --geocode to complete addresses for a real Lob send (slow, network)
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
try:
    from risk.economics import M2_PER_KW, array_recommendation
except ImportError:  # pragma: no cover
    from src.risk.economics import M2_PER_KW, array_recommendation

# situs_raw e.g.: "GARDINER-GARCIA DEANNA J 250 LITCHFIELD LN $0.00 Install a ground-mounted ..."
_STREET = (r"\b(\d{1,6})\s+"
           r"([A-Z0-9][A-Za-z0-9.'\-/ ]*?\b"
           r"(?:ST|AVE|RD|DR|LN|CT|WAY|BLVD|PL|TER|CIR|HWY|"
           r"STREET|AVENUE|ROAD|DRIVE|LANE|COURT|PLACE|TERRACE|CIRCLE|HIGHWAY|BOULEVARD)\b)")
ADDR_RE = re.compile(_STREET, re.I)
POBOX_RE = re.compile(r"\bP\.?\s*O\.?\s*BOX\b", re.I)


def clean_street(situs: str) -> str | None:
    """Pull a 'NUMBER STREET' address out of the messy permit situs text, or None."""
    if not isinstance(situs, str) or POBOX_RE.search(situs):
        return None
    m = ADDR_RE.search(situs)
    if not m:
        return None
    street = re.sub(r"\s+", " ", m.group(2)).strip().title()
    return f"{m.group(1)} {street}"


def _geocode(street: str, county: str, state: str) -> str | None:
    """Forward-geocode a street to a full deliverable address via Nominatim (1 req/s).

    Lob verifies a US address on the ZIP, so the ZIP is the hard requirement; the
    city is included when Nominatim returns it (it frequently returns a postcode
    with ``city=None`` for residential results). Requiring *both* city and ZIP —
    as the first cut did — silently dropped deliverable addresses, so we keep any
    result with a ZIP and fall back to the ZIP-only form (still parseable and
    Lob-verifiable) when the locality is missing.
    """
    import requests
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"street": street, "county": county, "state": state,
                    "country": "USA", "format": "json", "addressdetails": 1, "limit": 1},
            headers={"User-Agent": "SolarSoiled/outreach (betterbehaviorfoundation.com)"},
            timeout=20,
        )
        r.raise_for_status()
        js = r.json()
        if not js:
            return None
        a = js[0].get("address", {})
        zc = a.get("postcode")
        if not zc:
            return None
        city = (a.get("city") or a.get("town") or a.get("village")
                or a.get("hamlet") or a.get("municipality") or a.get("suburb"))
        if city:
            return f"{street}, {city}, {state} {zc}"
        return f"{street}, {state} {zc}"
    except Exception:
        return None
    return None


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--permits", default="data/external/sc_solar_permits.csv", type=Path)
    p.add_argument("--loss-pct", type=float, default=4.6, help="regional annual soiling-loss %% (v1)")
    p.add_argument("--min-kw", type=float)
    p.add_argument("--max-kw", type=float)
    p.add_argument("--top", type=int, default=200)
    p.add_argument("--geocode", action="store_true", help="complete addresses via Nominatim (network)")
    p.add_argument("--county", default="Santa Cruz County")
    p.add_argument("--state", default="CA")
    # Per-home deep-link base: the QR resolves to dashboard.html?id=permit-<array_id> so each
    # recipient lands on THEIR scored home (highlighted), not a generic calculator.
    p.add_argument("--qr-base",
                   default="https://betterbehaviorfoundation.com/tools/dashboard.html?id=permit-")
    p.add_argument("--out", default="outputs/outreach/permit_targets.csv", type=Path)
    args = p.parse_args(argv)

    df = pd.read_csv(args.permits)
    df = df[df["kw"].notna() & (df["kw"] > 0)].copy()
    # one row per parcel: highest kW, latest year
    df = df.sort_values(["apn", "kw", "year"]).groupby("apn", as_index=False).last()
    n_parcels = len(df)
    if args.min_kw is not None:
        df = df[df["kw"] >= args.min_kw]
    if args.max_kw is not None:
        df = df[df["kw"] <= args.max_kw]

    df["street"] = df["situs_raw"].map(clean_street)
    df = df[df["street"].notna()].copy()
    print(f"{n_parcels} parcels with kW → {len(df)} after size filter + a usable street address")

    # recoverable net-$ at the regional soiling assumption, ranked
    rec = df["kw"].apply(lambda kw: array_recommendation(args.loss_pct, float(kw)))
    df["expected_net_usd"] = [r["expected_net_usd"] for r in rec]
    df = df.sort_values("expected_net_usd", ascending=False).head(args.top).reset_index(drop=True)

    if args.geocode:
        print(f"Geocoding {len(df)} addresses via Nominatim (~{len(df)}s) …")
        full = []
        for s in df["street"]:
            full.append(_geocode(s, args.county, args.state))
            time.sleep(1.1)
        df["mailing_address"] = full
        dropped = df["mailing_address"].isna().sum()
        df = df[df["mailing_address"].notna()].reset_index(drop=True)
        if dropped:
            print(f"  dropped {dropped} that wouldn't geocode to a full address")
    else:
        df["mailing_address"] = df["street"] + f", {args.county}, {args.state}"  # NOT Lob-ready

    # generate_mailers-compatible columns
    df["array_id"] = range(1, len(df) + 1)
    df["system_kw"] = df["kw"].round(2)
    df["loss_pct"] = args.loss_pct
    df["risk_score"] = round(args.loss_pct / 8.0, 3)            # inverse of risk×8
    df["area_m2"] = (df["kw"] * M2_PER_KW).round(1)
    df["owner_name"] = "Solar Panel Owner"
    # QR carries the per-home deep-link + the street address. The address is NOT in the
    # public dashboard layer (PII) — it rides only in the recipient's own QR so the
    # dashboard can greet them by address without publishing all addresses.
    from urllib.parse import quote
    _street = df["mailing_address"].astype(str).str.split(",").str[0].str.strip()
    df["qr_url"] = (args.qr_base + df["array_id"].astype(str)
                    + "&addr=" + _street.map(lambda s: quote(s)))

    cols = ["array_id", "expected_net_usd", "system_kw", "loss_pct", "risk_score", "area_m2",
            "apn", "year", "owner_name", "mailing_address", "qr_url"]
    out = df[cols]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    print(f"[ok] {len(out)} permit-sourced targets → {args.out}")
    if not args.geocode:
        print("    NOTE: addresses are street-only — re-run with --geocode before a Lob send.")
    print(out[["system_kw", "expected_net_usd", "mailing_address"]].head(10).to_string(index=False))


if __name__ == "__main__":
    main()
