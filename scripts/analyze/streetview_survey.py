"""Street View substitute for the on-foot moss survey.

`docs/CRAIG_BRIEF_2026-08-19.md` §5 Option 1 is an afternoon photographing the mossiest
arrays in town from public roads, measuring edge-band thickness against the module frame
(35-40 mm) or cell pitch (156 mm) for scale. Street View is the same measurement from the
same viewpoint on imagery already captured -- no travel, and it can be pointed at a ranked
list instead of wandered.

    export GOOGLE_MAPS_API_KEY=...
    PYTHONPATH=. python3 scripts/analyze/streetview_survey.py \
        --candidates outputs/aoi/santa-cruz-w2-21cm/moss_candidates.csv \
        --top 60 --out outputs/moss_survey

Writes ``<out>/images/*.jpg``, ``<out>/survey.csv`` and ``<out>/contact_sheet.html`` -- an
offline review page with a scoring form, because the measurement is a human read.

READ THIS BEFORE TRUSTING A NULL RESULT
---------------------------------------
The Street View Static API caps standard-plan images at **640x640**, and minimum FOV is
about 10 degrees, so ground resolution is bounded:

    GSD ~= distance * radians(FOV) / width

At 20 m with FOV 20 and width 640 that is **~11 mm/px**, so a 30 mm band is under 3 pixels
-- detectable as a dark line, but *thickness is not reliably measurable at the boundary the
physics cares about* (16 mm kills the thesis, 30 mm does not). This tool can therefore
establish **presence** confidently and **thickness** only weakly. It is a screen, not the
verdict. Every row carries its own predicted GSD so a reviewer can throw out the rows where
the measurement was never possible.

Two further limits worth stating in any writeup: Street View captures are dated (often
years old, and the pano date is recorded per row), and the camera sits ~2.5 m up, so a roof
20 m away is seen at a shallow angle that foreshortens the panel plane.

**Privacy.** Targets are private homes. The tool writes no address, no APN and no owner
data; images are keyed by array index only and land outside both git repos by default.
Do not republish the imagery -- it is Google-licensed and it is of people's houses.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

META_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"
IMG_URL = "https://maps.googleapis.com/maps/api/streetview"

#: Standard-plan cap. Premium allows 2048; if you have it, raise this and the GSD improves
#: proportionally, which is the single biggest lever on whether thickness is measurable.
MAX_SIZE = 640

#: Narrow FOV is the whole game -- it is the zoom. 20 deg is near the practical minimum.
DEFAULT_FOV = 20

#: Panoramas further than this from the array are not worth a request.
MAX_PANO_DISTANCE_M = 60.0


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bearing_deg(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def predicted_gsd_mm(distance_m: float, fov_deg: float, width_px: int) -> float:
    return 1000.0 * distance_m * math.radians(fov_deg) / width_px


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--fov", type=int, default=DEFAULT_FOV)
    ap.add_argument("--size", type=int, default=MAX_SIZE)
    ap.add_argument("--pitch", type=int, default=18, help="tilt up toward the roofline")
    ap.add_argument("--api-key", default=os.environ.get("GOOGLE_MAPS_API_KEY"))
    ap.add_argument("--metadata-only", action="store_true",
                    help="check coverage and cost nothing; no images fetched")
    args = ap.parse_args()

    if not args.api_key:
        print("No API key. Set GOOGLE_MAPS_API_KEY or pass --api-key.\n"
              "Needs a Google Cloud project with billing enabled; metadata requests are\n"
              "documented as free, image requests are billed per fetch.", file=sys.stderr)
        return 2

    cand = pd.read_csv(args.candidates).head(args.top)
    out = Path(args.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    s = requests.Session()

    rows = []
    for _, c in cand.iterrows():
        lat, lon = float(c["lat"]), float(c["lon"])
        # Metadata first: free, and tells us whether an image request would be wasted.
        m = s.get(META_URL, params={"location": f"{lat},{lon}", "radius": 80,
                                    "source": "outdoor", "key": args.api_key}, timeout=30)
        meta = m.json() if m.ok else {"status": f"HTTP {m.status_code}"}
        rec = {"index": int(c["index"]), "lat": lat, "lon": lon,
               "moss_score": c.get("moss_score"), "tilt_deg": c.get("tilt_deg"),
               "canopy_frac": c.get("canopy_frac"), "status": meta.get("status")}
        if meta.get("status") != "OK":
            rows.append(rec)
            continue

        plat, plon = meta["location"]["lat"], meta["location"]["lng"]
        dist = haversine_m(plat, plon, lat, lon)
        rec.update(pano_date=meta.get("date"), pano_id=meta.get("pano_id"),
                   distance_m=round(dist, 1),
                   heading=round(bearing_deg(plat, plon, lat, lon), 1),
                   predicted_gsd_mm=round(predicted_gsd_mm(dist, args.fov, args.size), 1))
        if dist > MAX_PANO_DISTANCE_M:
            rec["status"] = "TOO_FAR"
            rows.append(rec)
            continue

        if not args.metadata_only:
            fn = out / "images" / f"array_{int(c['index']):05d}.jpg"
            if not fn.exists():
                r = s.get(IMG_URL, params={
                    "size": f"{args.size}x{args.size}", "location": f"{lat},{lon}",
                    "heading": rec["heading"], "pitch": args.pitch, "fov": args.fov,
                    "source": "outdoor", "return_error_code": "true", "key": args.api_key,
                }, timeout=45)
                if r.ok and r.content[:2] == b"\xff\xd8":
                    fn.write_bytes(r.content)
                else:
                    rec["status"] = f"IMG_{r.status_code}"
            rec["image"] = f"images/{fn.name}" if fn.exists() else None
            time.sleep(0.1)
        rows.append(rec)

    df = pd.DataFrame(rows)
    df.to_csv(out / "survey.csv", index=False)
    ok = df[df.status == "OK"]
    print(f"wrote {out}/survey.csv   {len(ok)}/{len(df)} with usable Street View coverage")
    if len(ok):
        print(f"  distance   p50 {ok.distance_m.median():.0f} m   p90 {ok.distance_m.quantile(.9):.0f} m")
        print(f"  GSD        p50 {ok.predicted_gsd_mm.median():.1f} mm/px   "
              f"best {ok.predicted_gsd_mm.min():.1f}")
        good = (ok.predicted_gsd_mm <= 10).sum()
        print(f"  rows where a 30 mm band spans >=3 px (GSD <= 10 mm): {good} "
              f"({100*good/len(ok):.0f}%)")
        if "pano_date" in ok:
            print(f"  pano dates {ok.pano_date.min()} -> {ok.pano_date.max()}")
    if not args.metadata_only:
        _contact_sheet(out, df)
        print(f"  review page: {out}/contact_sheet.html")
    return 0


def _contact_sheet(out: Path, df: pd.DataFrame) -> None:
    """Offline review page. The measurement is a human read; this is the instrument."""
    cards = []
    for _, r in df[df.get("image").notna()].iterrows() if "image" in df else []:
        cards.append(f"""
      <figure class="card">
        <img src="{r['image']}" alt="array {int(r['index'])}" loading="lazy">
        <figcaption>
          <b>#{int(r['index'])}</b>
          <span>score {r.get('moss_score')}</span>
          <span>canopy {r.get('canopy_frac')}</span>
          <span>tilt {r.get('tilt_deg')}&deg;</span>
          <span>{r.get('distance_m')} m &middot; <b>{r.get('predicted_gsd_mm')} mm/px</b></span>
          <span>pano {r.get('pano_date')}</span>
          <label>band <select data-i="{int(r['index'])}">
            <option value="">-</option><option>none</option><option>hairline</option>
            <option>&lt;1 cell</option><option>1-2 cells</option><option>&gt;2 cells</option>
            <option>obscured</option></select></label>
        </figcaption>
      </figure>""")
    html = f"""<!doctype html><meta charset="utf-8"><title>Moss survey contact sheet</title>
<style>
 body{{font:14px/1.5 system-ui,sans-serif;margin:0;padding:24px;background:#111;color:#eee}}
 h1{{font-size:1.3rem;margin:0 0 4px}} p{{color:#aaa;max-width:70ch}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;margin-top:20px}}
 .card{{margin:0;background:#1b1b1b;border:1px solid #333;border-radius:4px;overflow:hidden}}
 .card img{{width:100%;display:block;background:#000}}
 figcaption{{padding:8px 10px;display:flex;flex-wrap:wrap;gap:8px;font-size:.78rem;color:#bbb}}
 select{{background:#222;color:#eee;border:1px solid #444}}
 .scale{{background:#1b1b1b;border:1px solid #444;padding:12px 14px;margin-top:12px;font-size:.85rem}}
</style>
<h1>Moss survey &mdash; contact sheet</h1>
<p>Scale references: module frame <b>35&ndash;40 mm</b>, cell pitch <b>156 mm</b>. Judge the
continuous band along the <i>lower</i> edge of the modules. Read the mm/px on each card
first &mdash; above ~10 mm/px a 30 mm band is under 3 pixels and thickness is not
measurable, so score those <i>obscured</i> rather than <i>none</i>.</p>
<div class="scale"><b>Stop rule:</b> if the thickest continuous band found is below
f = 0.1 (~16 mm), the moss thesis is dead on physics. Decide before scoring, not after.</div>
<div class="grid">{''.join(cards)}</div>
<script>
document.addEventListener('change', e => {{
  if (e.target.tagName !== 'SELECT') return;
  const v = JSON.parse(localStorage.getItem('mossScores') || '{{}}');
  v[e.target.dataset.i] = e.target.value;
  localStorage.setItem('mossScores', JSON.stringify(v));
}});
for (const [i, val] of Object.entries(JSON.parse(localStorage.getItem('mossScores') || '{{}}')))
  {{ const s = document.querySelector(`select[data-i="${{i}}"]`); if (s) s.value = val; }}
</script>"""
    (out / "contact_sheet.html").write_text(html)


if __name__ == "__main__":
    raise SystemExit(main())
