"""Submit outreach postcards via the Lob.com print-and-mail API.

Always runs in dry-run mode by default (validates addresses, prints cost estimate,
does NOT charge your card). Add --send to commit the mailing.

Usage:
    # Validate addresses and estimate cost (safe — no charge)
    python scripts/22_mail_via_lob.py \\
        --targets outputs/outreach/santa_cruz_top50.csv \\
        --mailers-dir outputs/outreach/mailers

    # Actually send the postcards (~$1–2 each)
    python scripts/22_mail_via_lob.py \\
        --targets outputs/outreach/santa_cruz_top50.csv \\
        --mailers-dir outputs/outreach/mailers \\
        --send

Prerequisites:
    pip install lob-python
    Set LOB_API_KEY in your .env (test keys start with "test_", live keys "live_").
    Set SOLARSOILED_RETURN_ADDRESS="Name|Line1|City|State|Zip" in your .env.

    Get a Lob account at https://lob.com — test mode is free.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

import pandas as pd

try:
    from _addressing import parse_us_address
except ImportError:  # when run as a package module (python -m scripts.outreach...)
    from scripts.outreach._addressing import parse_us_address

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from a repo-root .env into os.environ (no dependency).

    The docstring promises keys can live in .env, but nothing read it; this does,
    without overriding values already set in the shell. Lines starting with # and
    blank lines are ignored; surrounding quotes on the value are stripped.
    """
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


def _parse_return_address() -> dict | None:
    """Return-address dict for Lob, or None to mail with NO return address.

    Returns None when SOLARSOILED_RETURN_ADDRESS is unset or still a placeholder
    (any part containing 'REPLACE'). Lob accepts a postcard with no from_address —
    USPS doesn't require one — at the cost of undeliverable pieces not being returned
    to sender. To print a return address, set a real Name|Line1|City|State|Zip.
    """
    raw = os.environ.get("SOLARSOILED_RETURN_ADDRESS", "")
    if not raw or "REPLACE" in raw.upper():
        return None
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 5:
        sys.exit("ERROR: SOLARSOILED_RETURN_ADDRESS must be 'Name|Line1|City|State|Zip'")
    return {
        "name": parts[0],
        "address_line1": parts[1],
        "address_city": parts[2],
        "address_state": parts[3],
        "address_zip": parts[4],
        "address_country": "US",
    }


def _parse_to_address(row: pd.Series) -> dict | None:
    """Parse a CSV row into a Lob address dict, or return None to skip the card.

    Uses the shared parser (parses ZIP→state→city from the end, validates the
    ZIP, routes apt/unit to line2) so a malformed row is skipped rather than
    mailed to a garbage address at ~$1.50 a card.
    """
    a = parse_us_address(str(row.get("mailing_address", "")))
    if a is None:
        return None

    addr = {
        "name": str(row.get("owner_name", "Solar Panel Owner"))[:40],
        "address_line1": a["line1"][:50],
        "address_city": a["city"][:30],
        "address_state": a["state"],
        "address_zip": a["zip"],
        "address_country": "US",
    }
    if a["line2"]:
        addr["address_line2"] = a["line2"][:50]
    return addr


def _split_front_back(pdf_path: Path) -> tuple[io.BytesIO, io.BytesIO]:
    """Split a 2-page mailer PDF into single-page front/back buffers for Lob.

    Lob's Postcard API takes a separate ``front`` and ``back``; our generator
    writes page 1 = front (marketing), page 2 = back (address side).
    """
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(pdf_path))
    if len(reader.pages) < 2:
        raise ValueError(
            f"{pdf_path.name} has {len(reader.pages)} page(s); expected 2 (front+back). "
            "Regenerate with the current generate_mailers.py."
        )
    bufs: list[io.BytesIO] = []
    for idx, tag in ((0, "front"), (1, "back")):
        writer = PdfWriter()
        writer.add_page(reader.pages[idx])
        buf = io.BytesIO()
        writer.write(buf)
        buf.seek(0)
        buf.name = f"{pdf_path.stem}_{tag}.pdf"  # Lob's uploader keys off a filename
        bufs.append(buf)
    return bufs[0], bufs[1]


def send_mailers(
    targets_csv: Path,
    mailers_dir: Path,
    *,
    dry_run: bool = True,
) -> None:
    _load_dotenv()
    api_key = os.environ.get("LOB_API_KEY", "")
    if not api_key:
        sys.exit(
            "ERROR: LOB_API_KEY not set.\n"
            "Set it in your .env file (test keys from https://lob.com)."
        )

    try:
        import lob
    except ImportError:
        sys.exit("ERROR: lob-python not installed. Run: pip install lob-python")

    lob.api_key = api_key
    is_test = api_key.startswith("test_")

    df = pd.read_csv(str(targets_csv))
    return_address = _parse_return_address()

    # Idempotency scope: same campaign + array_id always maps to the same key, so a
    # re-run (accidental, or a retry after a partial failure) is deduped by Lob and
    # never double-charges. The mailers dir is the campaign identity — regenerating
    # into a *new* dir is what intentionally allows a fresh send.
    campaign = mailers_dir.name

    print(f"{'[DRY RUN] ' if dry_run else ''}Submitting {len(df)} postcards via Lob.com …")
    print(f"  Key mode: {'TEST' if is_test else 'LIVE'}")
    print(f"  Idempotency campaign: {campaign} (re-runs are deduped, not re-charged)")
    if return_address:
        print(f"  Return address: {return_address['name']}, {return_address['address_line1']}, {return_address['address_city']}")
    else:
        print("  Return address: (none — mailing without a return address)")
    if not dry_run and not is_test:
        confirm = input(f"\nThis will charge your Lob account for {len(df)} live postcards. Continue? [y/N] ")
        if confirm.strip().lower() != "y":
            print("Aborted.")
            return

    results = {"sent": 0, "skipped": 0, "failed": 0}
    skipped_ids = []

    for _, row in df.iterrows():
        array_id = int(row["array_id"])
        pdf_path = mailers_dir / f"{array_id}.pdf"

        if not pdf_path.exists():
            print(f"  SKIP  #{array_id}: PDF not found at {pdf_path}")
            results["skipped"] += 1
            skipped_ids.append(array_id)
            continue

        to_addr = _parse_to_address(row)
        if to_addr is None:
            print(f"  SKIP  #{array_id}: no usable address")
            results["skipped"] += 1
            skipped_ids.append(array_id)
            continue

        if dry_run:
            print(f"  OK    #{array_id}: {to_addr['name']} — {to_addr['address_line1']}, {to_addr['address_city']}, {to_addr['address_state']} {to_addr['address_zip']}")
            results["sent"] += 1
            continue

        try:
            front_buf, back_buf = _split_front_back(pdf_path)
            create_kwargs = dict(
                description=f"SolarSoiled outreach — Array #{array_id}",
                to_address=to_addr,
                front=front_buf,
                back=back_buf,
                size="4x6",  # Lob's name for a 6x4 landscape postcard (art = 6.25x4.25)
                use_type="marketing",  # required by Lob; this is promotional outreach
            )
            if return_address:  # omit entirely to mail with no return address
                create_kwargs["from_address"] = return_address
            # Deterministic per-card key → Lob returns the original postcard on a
            # re-run instead of creating (and billing) a second one. The SDK forwards
            # a `headers` dict straight onto the HTTP request (api_requestor.request).
            create_kwargs["headers"] = {"Idempotency-Key": f"{campaign}:{array_id}"}
            postcard = lob.Postcard.create(**create_kwargs)
            print(f"  SENT  #{array_id}: {postcard.id} → {to_addr['name']}")
            results["sent"] += 1
        except Exception as exc:
            print(f"  FAIL  #{array_id}: {exc}")
            results["failed"] += 1

    print(f"\nResults: {results['sent']} {'queued (dry-run)' if dry_run else 'sent'}, "
          f"{results['skipped']} skipped, {results['failed']} failed")

    if skipped_ids:
        print(f"Skipped array IDs: {skipped_ids}")

    if dry_run:
        approx_cost = results["sent"] * 1.50
        print(f"\nEstimated cost if sent live: ~${approx_cost:.2f} "
              f"({results['sent']} × ~$1.50/postcard)")
        print("Re-run with --send to submit.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Send outreach postcards via Lob.com.")
    parser.add_argument("--targets", required=True, help="Targets CSV from script 20")
    parser.add_argument("--mailers-dir", required=True, help="Directory with per-array PDFs from script 21")
    parser.add_argument("--send", action="store_true", help="Actually send (default is dry-run only)")
    args = parser.parse_args(argv)

    send_mailers(
        targets_csv=Path(args.targets),
        mailers_dir=Path(args.mailers_dir),
        dry_run=not args.send,
    )


if __name__ == "__main__":
    main()
