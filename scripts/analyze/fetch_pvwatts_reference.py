#!/usr/bin/env python3
"""Cache an annual no-soiling PVWatts reference for one AOI.

This is deliberately an offline pipeline step. The API and dashboard only read its
small JSON artifact, so a homeowner interaction never depends on NREL availability.
The reference is 1 kWdc, fixed roof mount, 20-degree tilt, due south; recommendation
code applies measured per-roof POA ratios afterward.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


# PVWatts v8's default 14.08% aggregate loss includes 2% soiling. Remove that
# component because SolarSoiled prices soiling separately; inverter efficiency is
# passed as its own PVWatts input.
NO_SOILING_DC_LOSSES_PCT = (1.0 - 0.8592 / 0.98) * 100.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lat", required=True, type=float)
    parser.add_argument("--lon", required=True, type=float)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--api-key", default=os.environ.get("NREL_API_KEY"))
    args = parser.parse_args(argv)
    if not args.api_key:
        parser.error("set NREL_API_KEY or pass --api-key; do not commit API keys")

    params = {
        "api_key": args.api_key,
        "lat": args.lat,
        "lon": args.lon,
        "system_capacity": 1,
        "module_type": 0,
        "array_type": 1,
        "tilt": 20,
        "azimuth": 180,
        "losses": round(NO_SOILING_DC_LOSSES_PCT, 4),
        "dc_ac_ratio": 1.0,
        "inv_eff": 96.0,
    }
    url = "https://developer.nrel.gov/api/pvwatts/v8.json?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.load(response)
        annual_ac = float(payload["outputs"]["ac_annual"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"PVWatts request failed: {exc}") from exc
    if annual_ac <= 0:
        raise SystemExit("PVWatts returned a non-positive annual AC yield")

    out = {
        "annual_ac_kwh_per_kwdc": round(annual_ac, 3),
        "lat": args.lat,
        "lon": args.lon,
        "reference_tilt_deg": 20,
        "reference_azimuth_deg": 180,
        "array_type": "fixed_roof_mount",
        "dc_losses_pct_excluding_soiling": round(NO_SOILING_DC_LOSSES_PCT, 4),
        "inverter_efficiency_pct": 96.0,
        "source": "NREL PVWatts v8",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"PVWatts reference: {annual_ac:.1f} kWh/kWdc-year -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
