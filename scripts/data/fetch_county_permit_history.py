"""Pull Santa Cruz County permit history (1985-present) from the public status portal.

Our `data/external/sc_solar_permits.csv` starts in **2016**, which is a scraping limit, not
a records limit: the County's public portal serves permit history back to **1985** and it is
plain HTTP GET, no login, no records request. Everything before 2016 is the NEM 1.0 era --
the oldest, most grandfathered, highest-value homes -- and it is the population we are most
blind to (81.7% of AOI sites have no permit match today).

    PYTHONPATH=. python3 scripts/data/fetch_county_permit_history.py \
        --apns-from outputs/aoi/santa-cruz-w2-21cm/site_vintage.csv \
        --only-unmatched \
        --out data/external/sc_permit_history.csv

**PII.** The detail page exposes a `Primary Applicant` field -- homeowner names. This script
never parses or stores it. Only application number, APN, dates and project description are
kept, mirroring the no-PII contract in `BBF-Website/scripts/check-no-pii.mjs`. Do not add
the applicant field "for matching"; the APN already is the join key.

**Politeness.** This is a small county ASP.NET app, not an API. Requests are serialised with
a delay, every response is cached to disk, and a re-run resumes rather than refetching. Keep
`--delay` at or above the default unless you have a reason.
"""

from __future__ import annotations

import argparse
import csv
import html as htmllib
import re
import sys
import time
from pathlib import Path

import requests

BASE = "http://planningapplicationstatus.co.santa-cruz.ca.us"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/125.0 Safari/537.36")

#: Rooftop PV effectively does not predate this in the county, and every skipped permit is
#: a request not made against a public server. Raise it and you risk missing early adopters.
DEFAULT_SINCE_YEAR = 1998

#: A permit counts as PV if its description matches this and not the thermal pattern below.
#: Solar *thermal* (pool and domestic hot water) is a different technology with no bearing on
#: a net-metering tariff; it is 13 of 8,780 rows in the existing file, small but not zero.
PV_RE = re.compile(r"photovolt|\bpv\b|\bsolar\b", re.I)
THERMAL_RE = re.compile(r"thermal|solar (?:hot )?water|pool heat|water heat", re.I)


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


class Portal:
    def __init__(self, cache: Path, delay: float):
        self.cache = cache
        self.delay = delay
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self._last = 0.0

    def get(self, path: str, key: str) -> str | None:
        f = self.cache / f"{key}.html"
        if f.exists():
            return f.read_text(errors="replace")
        wait = self.delay - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            r = self.s.get(BASE + path, timeout=45)
        except requests.RequestException:
            return None
        finally:
            self._last = time.time()
        if r.status_code != 200:
            return None
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(r.text, errors="replace")
        return r.text

    def permits_for(self, apn8: str) -> list[dict]:
        h = self.get(f"/ActiveApplications?pn={apn8}", f"parcel/{apn8}")
        if not h:
            return []
        out = []
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", h, re.S):
            link = re.search(r'href="/Bldg\?n=([^"]+)"', row)
            if not link:
                continue
            cells = [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            date = next((c for c in cells if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", c)), "")
            out.append({"app_no": htmllib.unescape(link.group(1)), "list_date": date})
        return out

    def detail(self, app_no: str) -> dict | None:
        h = self.get(f"/Bldg?n={app_no}", f"permit/{app_no.replace('/', '_')}")
        if not h:
            return None
        t = _text(h)

        def field(label: str, stop: str) -> str:
            m = re.search(re.escape(label) + r"\s*(.*?)\s*" + re.escape(stop), t)
            return m.group(1).strip() if m else ""

        # NOTE: `Primary Applicant` is deliberately NOT read. See the module docstring.
        return {
            "app_no": field("Application Number:", "APN:"),
            "apn": field("APN:", "Application Date:"),
            "application_date": field("Application Date:", "Master Permit No:"),
            "issued_date": field("Issued Date:", "Application Status:"),
            "description": field("Project Description:", "Reviewer Comments"),
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apns-from", required=True,
                    help="CSV with a site_id (apn:...) or apn column")
    ap.add_argument("--only-unmatched", action="store_true",
                    help="skip APNs that already have an install_date")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache-dir", default=".cache/permits")
    ap.add_argument("--delay", type=float, default=0.8, help="seconds between requests")
    ap.add_argument("--since-year", type=int, default=DEFAULT_SINCE_YEAR)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    import pandas as pd

    df = pd.read_csv(args.apns_from)
    if "apn" in df.columns:
        apns = df["apn"]
    else:
        apns = df["site_id"].str.replace("apn:", "", regex=False)
    if args.only_unmatched and "install_date" in df.columns:
        apns = apns[df["install_date"].isna()]
    apns = sorted({a for a in apns.dropna().astype(str) if re.fullmatch(r"[\d-]+", a)})
    if args.limit:
        apns = apns[:args.limit]
    print(f"{len(apns)} parcels to query  (delay {args.delay}s, cached in {args.cache_dir})",
          flush=True)

    portal = Portal(Path(args.cache_dir), args.delay)
    rows, t0 = [], time.time()
    for i, apn in enumerate(apns, 1):
        for p in portal.permits_for(apn.replace("-", "")):
            yr = p["list_date"][-4:]
            if yr.isdigit() and int(yr) < args.since_year:
                continue
            d = portal.detail(p["app_no"])
            if not d or not d.get("description"):
                continue
            desc = d["description"]
            if not PV_RE.search(desc) or THERMAL_RE.search(desc):
                continue
            d["apn"] = d["apn"] or apn
            d["source"] = "county_portal"
            rows.append(d)
        if i % 25 == 0:
            print(f"  {i}/{len(apns)} parcels  {time.time()-t0:.0f}s  "
                  f"{len(rows)} PV permits", flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["apn", "app_no", "application_date",
                                          "issued_date", "description", "source"])
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {out}: {len(rows)} PV permits across "
          f"{len({r['apn'] for r in rows})} parcels")
    yrs = sorted(r["issued_date"][-4:] for r in rows if r.get("issued_date"))
    if yrs:
        print(f"issued-date range {yrs[0]}-{yrs[-1]}   "
              f"pre-2016 (invisible until now): {sum(1 for y in yrs if y.isdigit() and int(y)<2016)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
