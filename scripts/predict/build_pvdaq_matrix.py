"""Build a training matrix from the PVDAQ fleet labels, so the regional gate can run.

WHY THIS EXISTS. `regional_holdout.py` consumes a training MATRIX -- per-row weather
aggregates and static features -- not a label list. That matrix exists only for the
NREL rows, which is why "just re-run the gate on the fleet labels" is not a thing you
can do. This builds the missing half.

The gate it unblocks: a model trained on the NREL set scores **0.5313** on a region it
has never seen, against 0.7569 for a random holdout of identical size and identical
training-set size. That collapse is geographic -- 81% of NREL's rows sit west of -114.
The fleet labels carry 4.5x the eastern representation, so the question is whether
that fixes it. If it does not, the problem was never geography and that needs knowing.

Built to be run in quota-sized batches and resumed:
  * `--limit N` caps systems per pass. Open-Meteo's free tier is a hard DAILY ceiling
    priced on data volume, and a fleet-scale hourly warm has already exhausted it twice
    this week -- once taking two Stage-2 contract tests down with it, since
    build_risk_features.py calls the same API live.
  * Every pass appends to the parquet and skips systems already in it.
  * `--cache-only` builds from cached weather with no network at all, which is the
    right first pass: it costs nothing and tells you how many systems are already free.

Usage:
    PYTHONPATH=. conda run -n solar-soiling python scripts/predict/build_pvdaq_matrix.py \
        --cache-only                      # free: how much is already cached?
    ... --limit 150                       # one quota-sized batch, then stop
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src.risk.feature_engineering import build_feature_row
from src.risk.location_features import load_static_lookup
from src.risk.weather_client import (
    OpenMeteoQuotaError, fetch_combined, fetch_weather,
)

REPO = Path(__file__).resolve().parents[2]
LABELS = REPO / "outputs/soiling/pvdaq_fleet_labels.csv"
OUT = REPO / "outputs/soiling/pvdaq_training_matrix.parquet"
STATIC = REPO / "data/external/static_features.csv"
REGION_CFG = REPO / "configs/soiling/california.yaml"
FEATURES_CFG = REPO / "configs/soiling/features.yaml"

log = logging.getLogger("build_pvdaq_matrix")


def _seconds_to_next_hour() -> int:
    now = datetime.now()
    nxt = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return max(1, int((nxt - now).total_seconds()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", type=Path, default=LABELS)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--limit", type=int, default=None,
                    help="Max systems to add this pass. Use to stay inside the daily quota.")
    ap.add_argument("--cache-only", action="store_true",
                    help="No network. Builds only systems whose weather is already cached.")
    ap.add_argument("--order", default="east-first", choices=("east-first", "csv"),
                    help="Order systems are built in. Default east-first so a partial "
                         "matrix stays geographically balanced; 'csv' is the natural "
                         "order, which is western-heavy.")
    ap.add_argument("--with-aq", action="store_true",
                    help="Also fetch air quality. OFF by default and that is deliberate: "
                         "the six PM features carry 0%% gain in the production model, "
                         "they are entirely empty in the AOI matrix already, and the AQ "
                         "join is what makes a row take over two minutes instead of two "
                         "seconds. Skipping it turns a 27-hour build into well under an "
                         "hour and costs nothing the model uses.")
    ap.add_argument("--checkpoint-every", type=int, default=25)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    region = yaml.safe_load(open(REGION_CFG))
    feats_cfg = yaml.safe_load(open(FEATURES_CFG))
    windows = feats_cfg.get("rolling_windows", [7, 30, 90])
    thr = float(region.get("iwsr_risk_threshold", 0.97))
    cache_dir = REPO / region.get("cache_dir", ".cache/soiling")
    static_lookup = load_static_lookup(STATIC) if STATIC.exists() else {}

    labels = pd.read_csv(args.labels)
    done: set[int] = set()
    rows: list[dict] = []
    if args.out.exists():
        prev = pd.read_parquet(args.out)
        rows = prev.to_dict("records")
        done = set(prev.system_id.astype(int))
        log.info("[resume] %d systems already in %s", len(done), args.out.name)

    todo = labels[~labels.system_id.astype(int).isin(done)]

    # Build EAST FIRST, and this is not a scheduling preference. The label CSV's
    # natural order is western-heavy: its first 109 rows are 86.2% west of -114
    # against 70.0% for the whole set. Since Open-Meteo's hourly and daily ceilings
    # guarantee this build stops partway, natural order means a partial matrix
    # reproduces precisely the NREL geographic bias (81% west) the experiment exists
    # to escape -- and it would do so silently, reporting a regional result computed
    # on the wrong geography.
    #
    # Eastern systems are also the scarce half (193 of 821), so pulling them first
    # maximises balance at every possible stopping point rather than only at the end.
    if args.order == "east-first":
        todo = todo.sort_values("lon", ascending=False)

    if args.limit:
        todo = todo.head(args.limit)
    if len(todo):
        log.info("build order %s: first 100 queued are %.0f%% east of -100",
                 args.order, 100 * (todo.lon.head(100) > -100).mean())
    log.info("%d systems to add (%d labelled total, %d already done)",
             len(todo), len(labels), len(done))

    added = miss = err = 0
    for i, s in enumerate(todo.itertuples(), 1):
        # The label's own span. `years` is the panel record length, so as_of is the end
        # of the observed period rather than today; using today would attach weather the
        # soiling fit never saw.
        try:
            yrs = float(s.years) if np.isfinite(s.years) else 8.0
        except (TypeError, ValueError):
            yrs = 8.0
        as_of = date(2025, 1, 1)
        start = date(max(2009, int(as_of.year - yrs)), 1, 1)
        # Sleep through an hourly ceiling instead of dropping the row. The client
        # raises OpenMeteoQuotaError with scope precisely so a caller can do this, and
        # nothing in the repo was using it: a run that met the ceiling burned its
        # retry budget on every remaining system and recorded them all as errors. The
        # hourly window resets on the hour, so waiting costs minutes and dropping
        # costs the row.
        # Reset per system. Without this, a system whose six attempts ALL end in an
        # hourly-quota sleep leaves `daily` holding the PREVIOUS system's weather, and
        # the row gets built from it -- one roof's features attached to another roof's
        # label, silently, with no error and a perfectly plausible-looking matrix.
        daily = None
        for attempt in range(6):
            try:
                if args.with_aq:
                    daily = fetch_combined(float(s.lat), float(s.lon), start, as_of,
                                           cache_dir=cache_dir,
                                           cache_only=args.cache_only)
                else:
                    daily = fetch_weather(float(s.lat), float(s.lon), start, as_of,
                                          cache_dir=cache_dir,
                                          cache_only=args.cache_only)
                break
            except OpenMeteoQuotaError as q:
                if args.cache_only:
                    miss += 1
                    daily = None
                    break
                if q.scope == "daily":
                    # Nothing to wait for within a session. Checkpoint and stop
                    # cleanly so a rerun tomorrow resumes rather than restarts.
                    log.warning("daily quota exhausted at %d added; checkpointing "
                                "and stopping. Rerun tomorrow to resume.", added)
                    if rows:
                        pd.DataFrame(rows).to_parquet(args.out, index=False)
                    return
                wait = _seconds_to_next_hour() + 30
                log.warning("hourly quota exhausted; sleeping %d s for the window to "
                            "reset (attempt %d/6, %d added so far)",
                            wait, attempt + 1, added)
                time.sleep(wait)
            except Exception as e:  # noqa: BLE001 - CacheMiss under --cache-only expected
                if args.cache_only:
                    miss += 1
                else:
                    err += 1
                    log.warning("system %s: %s: %s", s.system_id, type(e).__name__,
                                str(e)[:100])
                daily = None
                break
        if daily is None:
            continue

        row = {
            "system_id": int(s.system_id),
            "latitude": float(s.lat), "longitude": float(s.lon),
            "as_of": as_of, "year": as_of.year,
            # PVDAQ gives a loss in points; the NREL rows carry IWSR. Convert so the
            # label means the same thing in both: 1 = at-risk = IWSR below threshold.
            "iwsr": 1.0 - float(s.loss_pct) / 100.0,
            "label": int((1.0 - float(s.loss_pct) / 100.0) < thr),
            "is_feedback": False, "is_summary": False,
            "tilt_deg": float(s.tilt) if pd.notna(s.tilt) else np.nan,
        }
        row.update(build_feature_row(daily, as_of, windows=windows,
                                     kimber_cfg=feats_cfg.get("kimber", {}),
                                     somosclean_cfg=feats_cfg.get("somosclean", {})))
        key = (round(float(s.lat), 3), round(float(s.lon), 3))
        row.update(static_lookup.get(key, {}))
        rows.append(row)
        added += 1

        if args.checkpoint_every and added % args.checkpoint_every == 0:
            pd.DataFrame(rows).to_parquet(args.out, index=False)
            log.info("[checkpoint] %d added (%d/%d scanned)", added, i, len(todo))

    if rows:
        d = pd.DataFrame(rows)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        d.to_parquet(args.out, index=False)
        base = d.label.mean() if "label" in d else float("nan")
        log.info("\nwrote %s: %d rows, %d cols", args.out, len(d), d.shape[1])
        log.info("  label base rate %.3f   west of -114 %.1f%%   east of -100 %.1f%%",
                 base, 100 * (d.longitude < -114).mean(), 100 * (d.longitude > -100).mean())
        n_static = sum(1 for r in rows if "worldcover_tree" in r)
        log.info("  rows with static features: %d of %d", n_static, len(rows))
    if args.cache_only:
        log.info("  cache misses (would need network): %d", miss)
    if err:
        log.info("  errors: %d", err)


if __name__ == "__main__":
    main()
