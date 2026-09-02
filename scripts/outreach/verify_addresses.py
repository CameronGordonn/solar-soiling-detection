"""Pre-flight: verify every outreach address against Lob before any send.

Runs Lob's US Verification API over each row of a targets CSV and prints a
deliverable / undeliverable report, plus writes the per-row result to
outputs/outreach/address_verification.csv. Nothing is printed or mailed — this
only checks addresses, so it never charges your card.

    PYTHONPATH=. python scripts/outreach/verify_addresses.py \\
        --targets outputs/outreach/detected_targets.csv

IMPORTANT — test vs live key:
    Lob US Verification only returns REAL deliverability results with a *live_*
    key. A *test_* key returns fixed sample responses (not a real check).
    Verification is free and mails nothing, so running this with the live key is
    a safe pre-flight: it tells you which of the addresses Lob will actually
    accept, without committing to (or paying for) the mailing.

Deliverability values Lob can return (see the printed legend):
    deliverable                     -> good to mail
    deliverable_missing_unit        -> mailable, but apt/unit missing
    deliverable_incorrect_unit      -> mailable, unit looks wrong
    deliverable_unnecessary_unit    -> mailable, extra unit info
    undeliverable                   -> Lob will reject; do NOT mail
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

try:
    from _addressing import parse_us_address
except ImportError:  # when run as a package module
    from scripts.outreach._addressing import parse_us_address

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from a repo-root .env into os.environ (no override)."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def verify(targets_csv: Path) -> None:
    _load_dotenv()
    api_key = os.environ.get("LOB_API_KEY", "")
    if not api_key:
        sys.exit("ERROR: LOB_API_KEY not set in .env (test or live key from https://lob.com).")

    try:
        import lob
    except ImportError:
        sys.exit("ERROR: lob-python not installed. Run: pip install lob-python")

    lob.api_key = api_key
    is_test = api_key.startswith("test_")

    df = pd.read_csv(str(targets_csv))
    print(f"Verifying {len(df)} addresses via Lob US Verification …")
    print(f"  Key mode: {'TEST' if is_test else 'LIVE'}")
    if is_test:
        print("  ⚠️  TEST key — Lob returns SAMPLE responses, not a real deliverability\n"
              "      check. Re-run with your live_ key for true results (still free, no mail).")
    print()

    rows = []
    counts: dict[str, int] = {}
    for _, row in df.iterrows():
        array_id = row.get("array_id", "?")
        a = parse_us_address(str(row.get("mailing_address", "")))
        if a is None:
            status = "unparseable_locally"
            print(f"  SKIP  #{array_id}: address did not parse: {row.get('mailing_address','')}")
            counts[status] = counts.get(status, 0) + 1
            rows.append({"array_id": array_id, "deliverability": status,
                         "mailing_address": row.get("mailing_address", "")})
            continue

        try:
            params = dict(
                primary_line=a["line1"],
                city=a["city"],
                state=a["state"],
                zip_code=a["zip"],
            )
            if a["line2"]:
                params["secondary_line"] = a["line2"]
            res = lob.USVerification.create(**params)
            status = res["deliverability"]
        except Exception as exc:  # network / auth / malformed
            status = f"error: {exc}"

        counts[status if not status.startswith("error") else "error"] = (
            counts.get(status if not status.startswith("error") else "error", 0) + 1
        )
        flag = "OK  " if status == "deliverable" else (
            "BAD " if status == "undeliverable" or status.startswith("error") else "WARN"
        )
        print(f"  {flag}  #{array_id}: {status}  ({a['line1']}, {a['city']}, {a['state']} {a['zip']})")
        rows.append({"array_id": array_id, "deliverability": status,
                     "mailing_address": row.get("mailing_address", "")})

    out_csv = REPO_ROOT / "outputs" / "outreach" / "address_verification.csv"
    pd.DataFrame(rows).to_csv(out_csv, index=False)

    print("\n— Summary —")
    for k in sorted(counts):
        print(f"  {counts[k]:>3}  {k}")
    mailable = sum(v for k, v in counts.items() if k.startswith("deliverable"))
    print(f"\n  {mailable}/{len(df)} addresses are mailable (deliverable*).")
    print(f"  Full report: {out_csv}")
    if is_test:
        print("\n  Reminder: these are TEST-key sample results. Run with the live_ key for real ones.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Verify outreach addresses via Lob (no mail, no charge).")
    parser.add_argument("--targets", required=True, help="Targets CSV (needs a mailing_address column)")
    args = parser.parse_args(argv)
    verify(Path(args.targets))


if __name__ == "__main__":
    main()
