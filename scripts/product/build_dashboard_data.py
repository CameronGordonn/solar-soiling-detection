#!/usr/bin/env python3
"""Generate the homeowner dashboard's embedded data from a scored AOI.

Emits, into ``../BBF-Website/public/tools/`` by default:

    arrays_data.js   window.FALLBACK_ARRAYS — one polygon per detected array
    qr_aliases.js    window.QR_ALIASES — printed QR id -> current array_id

**The QR alias table is the whole point of this script.** 97 QR codes are printed on
cards already in people's homes and resolve as ``dashboard.html?id=<qr_id>``
(``configs/outreach/published_qr_ids.csv``). 50 of them are integer ids indexing the
*60cm* AOI, which has 334 arrays. The 21cm rebuild has 3,362 arrays under entirely
different numbers — the relabel changed the convention, so one roof is now several
polygons. Swapping the data without an alias step does not 404; it silently shows a
**different house's** numbers under a mailed link, which is worse.

Aliases resolve in two hops, most reliable first:

1. ``legacy_array_id`` — written by the stable-identity pass, a direct 60cm -> 21cm
   match. Covers all 50 published integer ids.
2. **APN** — parcel identity, which survives re-detection, renumbering and imagery
   changes when a geometric match does not.

The script refuses to write if any published id would stop resolving.

**The emitted site key is a salted hash, never the APN.** Grouping polygons by roof is
the whole reason the dashboard can quote a household total, but the parcel number is
homeowner-identifying data and the BBF site publicly promises it is never published.
An *unsalted* hash would be no protection: the county's parcel list is public and only
~15k APNs sit in the AOI, so anyone could hash all of them and invert the mapping in a
second. The salt lives in ``secrets/`` (gitignored) and is generated on first run.

    PYTHONPATH=. python scripts/product/build_dashboard_data.py
    ... --aoi santa-cruz-outreach-v1     # rebuild from the 60cm AOI instead
    ... --out-dir /tmp/preview           # write somewhere harmless first
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

DEFAULT_OUT = REPO.parent / "BBF-Website/public/tools"
CONTRACT = REPO / "configs/outreach/published_qr_ids.csv"
SALT_FILE = REPO / "secrets/site_id_salt.txt"

# Anything shaped like a Santa Cruz APN (###-###-##) must never reach the payload.
# Mirrors BBF-Website/scripts/check-no-pii.mjs, which fails that build on the same
# pattern; catching it here means the leak never leaves this repo in the first place.
APN_SHAPE = re.compile(r"\b\d{3}-\d{3}-\d{2}(?!\d)")

# Private-schema field names, mirrored from that same guard. Kept in sync by hand;
# both lists are short and change rarely.
SCHEMA_KEYS = ("situs_raw", "situs_addr", "owner_name", "mail_addr", "mailing_addr",
               '"apn"', "'apn'", "parcel_number")


def _digits(v) -> str:
    return re.sub(r"\D", "", str(v))


def _load_salt() -> str:
    """Read the site-key salt, generating it on first run.

    Stability matters as much as secrecy: the same parcel must hash to the same key
    across regenerations, or every rebuild silently reshuffles which polygons the
    dashboard groups together. Losing this file is recoverable (site keys are opaque
    and nothing links to them), but it does change every key, so it is worth keeping.
    """
    env = os.environ.get("SOLARSOILED_SITE_SALT")
    if env:
        return env
    if not SALT_FILE.is_file():
        SALT_FILE.parent.mkdir(parents=True, exist_ok=True)
        SALT_FILE.write_text(secrets.token_hex(32) + "\n", encoding="utf-8")
        SALT_FILE.chmod(0o600)
        print(f"[salt] generated {SALT_FILE} (gitignored — keep it; it pins site keys)")
    return SALT_FILE.read_text(encoding="utf-8").strip()


def _site_key(site_id, salt: str) -> str | None:
    """APN (or any parcel identifier) -> opaque, stable, non-invertible site key."""
    raw = _digits(str(site_id).replace("apn:", ""))
    if not raw:
        return None
    return "s:" + hashlib.sha256(f"{salt}:{raw}".encode()).hexdigest()[:10]


def _round_geom(geom: dict, nd: int) -> dict:
    """Round coordinates in place-ish. 6 dp is ~11 cm at this latitude.

    Shapely's ``to_json`` emits full float64 repr — ~17 significant digits per ordinate,
    which for 3,362 polygons is most of the file. The detector's own boundary error is
    several centimetres at 6.3 cm GSD, so digits past the 6th are noise being shipped to
    a phone over cellular.
    """
    def _r(c):
        if isinstance(c, (list, tuple)):
            if c and isinstance(c[0], (int, float)):
                return [round(float(x), nd) for x in c]
            return [_r(x) for x in c]
        return c

    return {"type": geom["type"], "coordinates": _r(geom["coordinates"])}


def main(argv=None) -> int:
    import geopandas as gpd
    import pandas as pd

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aoi", default="santa-cruz-w2-21cm")
    ap.add_argument("--risk-file", default="risk_econ.geojson")
    ap.add_argument("--alt-scores", default="outputs/outreach/alt_scores.json")
    ap.add_argument("--roof-planes", default="roof_planes.csv",
                    help="per-array lidar tilt/azimuth/POA, relative to the AOI dir")
    ap.add_argument("--module-orientation", default="module_orientation.csv",
                    help="per-array portrait/landscape from 6.3cm imagery, if measured")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--precision", type=int, default=6,
                    help="coordinate decimal places (6 ~ 0.1 m; keeps the file small)")
    args = ap.parse_args(argv)

    aoi_dir = REPO / "outputs/aoi" / args.aoi
    gdf = gpd.read_file(aoi_dir / args.risk_file).to_crs("EPSG:4326")
    print(f"[in] {args.aoi}/{args.risk_file}: {len(gdf)} arrays")

    # ── alternative-model scores ──────────────────────────────────────────────
    # These backed a 3-tab model switcher that the dashboard no longer renders, and the
    # reason it went is worth recording: **SOMOSclean and Kimber never discriminated
    # within this AOI.** The 60cm run scored 334 arrays and produced exactly 2 distinct
    # SOMOSclean values and 2 distinct Kimber values. Both are driven purely by weather
    # at the array centroid, and a 15 km coastal AOI spans about two Open-Meteo grid
    # cells, so a rescore on the 3,362-array set would reproduce the same 2 values at
    # 3,362 rows. That is not a model comparison, it is a constant printed three ways.
    #
    # So a column is emitted only if it actually varies. Shipping 3,362 copies of one
    # float to a phone over cellular is ~180 KB of noise, and worse, it invites the
    # dashboard to present agreement between models that were never in a position to
    # disagree. The same limitation applies to `risk_score` (see the loss-regressor
    # section of docs/ECONOMICS_GROUNDING_20260809.md); the difference is that the
    # XGBoost head at least varies, so it is kept and its weakness is stated in the UI.
    alt_path = REPO / args.alt_scores
    somos = kimber = {}
    if alt_path.is_file():
        raw = json.loads(alt_path.read_text())
        somos = {int(k): v.get("somos") for k, v in raw.items() if v.get("somos") is not None}
        kimber = {int(k): v.get("kimber") for k, v in raw.items() if v.get("kimber") is not None}
    # ── measured roof geometry ────────────────────────────────────────────────
    # tilt/azimuth fitted to USGS 3DEP returns inside each polygon (src/risk/roof_geometry).
    # Unlike everything else on this dashboard these are genuinely PER POLYGON: the lidar
    # sits ~0.22 m proud of the surrounding roof, i.e. it is reading the module surfaces,
    # and 40% of same-roof array pairs are gable mirrors facing opposite directions. So a
    # two-array house can legitimately show two different orientations, and should.
    #
    # Failed fits (~9%: ridge-straddling and hip-roof arrays) are emitted as null, never
    # median-filled. Median-filling a missing geometric feature is exactly how `tilt_deg`
    # came to be fabricated across 21.8% of risk-model importance.
    roof = {}
    rp_path = aoi_dir / args.roof_planes
    if rp_path.is_file():
        rp = pd.read_csv(rp_path)
        rp = rp[rp["fit_ok"].astype(bool)]
        roof = {int(i): (t, a, p) for i, t, a, p
                in zip(rp["index"], rp["tilt_deg"], rp["azimuth_deg"], rp["poa_rel"])}
        print(f"[in] {args.roof_planes}: {len(roof)} fitted roof planes "
              f"({100*len(roof)/max(len(gdf),1):.0f}% of arrays)")
    else:
        print(f"[warn] no {rp_path.name} — tilt/azimuth omitted from the payload")

    # ── measured module orientation ───────────────────────────────────────────
    # Portrait or landscape, read off 6.35 cm imagery by directional autocorrelation
    # (scripts/analyze/module_orientation.py). This is a DIFFERENT quantity from the
    # roof tilt above and has nothing like its coverage: the run behind this file
    # classified the largest tilted arrays only, and refused 56% of even those rather
    # than guess. So it is emitted where it exists and simply absent everywhere else.
    #
    # Never median-fill it and never infer it from tilt. It is emitted at all because
    # it is the one field on this payload measured from the panels themselves rather
    # than from the roof plane under them, and because an unresolved portrait/landscape
    # fork is worth 3x in substring loss if the moss channel is ever re-opened.
    orient = {}
    mo_path = aoi_dir / args.module_orientation
    if mo_path.is_file():
        mo = pd.read_csv(mo_path)
        mo = mo[mo["orientation"].isin(("portrait", "landscape"))]
        orient = {int(i): str(o) for i, o in zip(mo["id"], mo["orientation"])}
        print(f"[in] {args.module_orientation}: {len(orient)} classified "
              f"({100*len(orient)/max(len(gdf),1):.1f}% of arrays; the rest refused, not guessed)")

    med_s = pd.Series(list(somos.values())).median() if somos else None
    med_k = pd.Series(list(kimber.values())).median() if kimber else None

    def _alt(row, table, med):
        lid = row.get("legacy_array_id")
        if pd.notna(lid) and int(lid) in table:
            return round(float(table[int(lid)]), 4)
        return None if med is None else round(float(med), 4)

    # ── features ──────────────────────────────────────────────────────────────
    # Every economic quantity below is measured **per site, not per polygon**, and is
    # named to say so. A 21cm relabel splits one physical array into 1.8 polygons on
    # average (48% of sites are multi-polygon, one has 22), so a per-polygon kW or
    # dollar figure answers a question no homeowner asked: they own one system and pay
    # for one truck roll. Emitting these under bare names like `system_kw` invited the
    # dashboard to print a whole parcel's 1,442 kW next to one 505 m2 fragment, which
    # is exactly what it did. The fields that legitimately vary within a site are
    # `area_m2`, `risk_score`, and the three measured roof-geometry fields.
    salt = _load_salt()
    site_counts = gdf["site_id"].value_counts().to_dict() if "site_id" in gdf.columns else {}

    feats = []
    for _, r in gdf.iterrows():
        geom = r.geometry
        if geom is None or geom.is_empty:
            continue
        props = {
            "array_id": int(r["array_id"]),
            "risk_score": None if pd.isna(r.get("risk_score")) else round(float(r["risk_score"]), 4),
            "area_m2": None if pd.isna(r.get("area_m2")) else round(float(r["area_m2"]), 1),
            "somos_score": _alt(r, somos, med_s),
            "kimber_score": _alt(r, kimber, med_k),
        }
        # Measured module-plane geometry. `poa_rel` is annual clear-sky plane-of-array
        # irradiance relative to a south-facing 20-degree roof, so 0.85 reads directly as
        # "collects 15% less sun than an ideally-oriented roof in this town".
        tap = roof.get(int(r["array_id"]))
        if tap is not None:
            props["tilt_deg"] = round(float(tap[0]), 1)
            props["azimuth_deg"] = round(float(tap[1]), 1)
            props["poa_rel"] = round(float(tap[2]), 3)
        ori = orient.get(int(r["array_id"]))
        if ori is not None:
            props["module_orientation"] = ori
        # Grounded economics, so the map and the sidebar cannot drift apart again.
        # `usd_per_kwh` and `sun_hours` added 2026-08-28, and they are not decoration:
        # they are the two inputs that make the dashboard's own arithmetic reproduce
        # the pipeline's `annual_loss_usd` instead of contradicting it.
        #
        # Both tools compute the same product — kW x sun_hours x 365 x SYSTEM_DERATE x
        # loss% x $/kWh. The pipeline resolves the last two per site (the tariff from
        # the permit date, the sun hours from the fitted roof plane). dashboard.js could
        # not see either, so it fell back to a flat $0.1646 and a flat 5.5 h. While the
        # AOI was priced at one blended rate that was close enough to hide. Once the
        # tariff join landed — 90% of these homes are on legacy NEM 2.0 at $0.4573 — the
        # same household read $113/yr in the sidebar and $338/yr in the ranked list on
        # the same screen. Shipping these two fields closes it at the source rather than
        # by hardcoding a second copy of the rate in the page.
        for src, dst in (("system_kw", "site_kw"),
                         ("loss_pct_p50", "site_loss_pct"),
                         ("annual_loss_usd", "site_annual_loss_usd"),
                         ("usd_per_kwh", "site_usd_per_kwh"),
                         ("sun_hours", "site_sun_hours"),
                         ("expected_net_usd", "site_expected_net_usd"),
                         ("economic_action", "site_economic_action"),
                         ("prob_net_positive", "site_prob_net_positive")):
            v = r.get(src)
            if v is not None and not (isinstance(v, float) and pd.isna(v)):
                props[dst] = round(float(v), 4) if isinstance(v, (int, float)) else str(v)

        # Opaque grouping key — never the APN. See _site_key.
        sid = r.get("site_id")
        if sid is not None and not (isinstance(sid, float) and pd.isna(sid)):
            key = _site_key(sid, salt)
            if key:
                props["site_key"] = key
                props["site_array_count"] = int(site_counts.get(sid, 1))

        feats.append({
            "type": "Feature",
            "properties": props,
            "geometry": _round_geom(geom.__geo_interface__, args.precision),
        })

    # Drop any alt-model column that turned out to be a constant in disguise (see the
    # alt-scores note above). Threshold is deliberately low: a column needs to separate
    # more than a handful of roofs before it can be called a per-array score at all.
    MIN_DISTINCT = 10
    dropped_alt = []
    for col in ("somos_score", "kimber_score"):
        vals = {f["properties"].get(col) for f in feats}
        vals.discard(None)
        if len(vals) < MIN_DISTINCT:
            for f in feats:
                f["properties"].pop(col, None)
            dropped_alt.append(f"{col} ({len(vals)} distinct)")
    if dropped_alt:
        print(f"[alt] dropped near-constant column(s): {', '.join(dropped_alt)}")

    fc = {"type": "FeatureCollection", "features": feats}

    # ── QR alias table + the invariant ────────────────────────────────────────
    pub = pd.read_csv(CONTRACT)
    by_legacy = {int(r["legacy_array_id"]): int(r["array_id"])
                 for _, r in gdf.iterrows() if pd.notna(r.get("legacy_array_id"))}
    by_apn: dict[str, int] = {}
    if "site_id" in gdf.columns:
        for _, r in gdf.iterrows():
            a = _digits(str(r["site_id"]).replace("apn:", ""))
            if a and a not in by_apn:
                by_apn[a] = int(r["array_id"])

    aliases: dict[str, int] = {}
    unresolved: list[str] = []
    # Route names land in the published manifest, so none of them may be a private
    # schema word: BBF-Website/scripts/check-no-pii.mjs greps the built site for the
    # literal "apn" as a JSON key and fails the build on it. That guard is blunt on
    # purpose, and a route *name* is not worth arguing with it over.
    via = {"legacy": 0, "parcel": 0, "native": 0, "external_layer": 0}
    live_ids = {int(f["properties"]["array_id"]) for f in feats}

    for _, row in pub.iterrows():
        qid = str(row["qr_id"])
        if not qid.isdigit():
            # permit-N ids are served by mailer_homes.js, a separate point layer that
            # this script does not touch. Nothing to alias.
            via["external_layer"] += 1
            continue
        n = int(qid)
        if n in by_legacy:
            aliases[qid] = by_legacy[n]
            via["legacy"] += 1
        elif _digits(row.get("apn")) in by_apn:
            aliases[qid] = by_apn[_digits(row["apn"])]
            via["parcel"] += 1
        elif n in live_ids:
            via["native"] += 1          # id still valid as-is; no alias row needed
        else:
            unresolved.append(qid)

    print(f"[qr] published={len(pub)}  aliased via legacy={via['legacy']} "
          f"parcel={via['parcel']}  native={via['native']}  other-layer={via['external_layer']}")
    if unresolved:
        print(f"[FAIL] {len(unresolved)} mailed QR ids would stop resolving: {unresolved[:10]}")
        print("       Refusing to write. Those cards are in people's homes.")
        return 1

    payload = json.dumps(fc, separators=(",", ":"))
    manifest = json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "aoi": args.aoi,
        # Repo-relative: an absolute path would publish the operator's home directory.
        "source": str((aoi_dir / args.risk_file).relative_to(REPO)),
        "n_arrays": len(feats),
        "n_sites": len({f["properties"]["site_key"] for f in feats
                        if "site_key" in f["properties"]}),
        "n_qr_aliases": len(aliases), "alias_routes": via,
        "alt_scores_filled_from_median": bool(med_s is not None),
        "alt_scores_dropped": dropped_alt,
        "site_key": "salted sha256 of the parcel id, truncated to 10 hex; salt kept private",
        "beta": True,
    }, indent=2)
    alias_js_body = (
        "// printed QR id -> current array_id. Generated by "
        "scripts/product/build_dashboard_data.py.\n"
        "// See configs/outreach/README.md — these cards are already mailed.\n"
        f"window.QR_ALIASES={json.dumps(aliases, separators=(',', ':'))};\n"
    )

    # ── PII gate ──────────────────────────────────────────────────────────────
    # Last line of defence before anything is written toward a static host, where a
    # leak is permanent and cached. The BBF build runs the same check on `out/`, but
    # by then the data has been committed; failing here keeps it in this repo. Checks
    # every file we are about to write, manifest included — a route named "apn" in the
    # manifest is enough to fail that build, and did.
    for name, text in (("arrays_data.js", payload),
                       ("qr_aliases.js", alias_js_body),
                       ("arrays_data.manifest.json", manifest)):
        leaked = APN_SHAPE.findall(text)
        if leaked:
            print(f"[FAIL] {name}: {len(leaked)} parcel-number-shaped strings, "
                  f"e.g. {sorted(set(leaked))[:3]}")
            print("       Refusing to write. The public site promises it publishes no APNs.")
            return 1
        hit = next((k for k in SCHEMA_KEYS if k.lower() in text.lower()), None)
        if hit:
            print(f"[FAIL] {name}: contains private-schema key {hit!r}")
            print("       Refusing to write. Rename it; the BBF build greps for this.")
            return 1

    # ── write ─────────────────────────────────────────────────────────────────
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    arrays_js = out_dir / "arrays_data.js"
    aliases_js = out_dir / "qr_aliases.js"

    arrays_js.write_text(f"window.FALLBACK_ARRAYS={payload};\n", encoding="utf-8")
    aliases_js.write_text(alias_js_body, encoding="utf-8")
    (out_dir / "arrays_data.manifest.json").write_text(manifest, encoding="utf-8")

    print(f"[ok] {arrays_js}  ({len(feats)} arrays, {arrays_js.stat().st_size/1e6:.2f} MB)")
    print(f"[ok] {aliases_js} ({len(aliases)} aliases)")
    print("[note] add <script src=\"qr_aliases.js\"></script> before dashboard.js")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
