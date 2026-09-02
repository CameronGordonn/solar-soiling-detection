"""Extract soiling labels for the whole PVDAQ residential fleet, resumably.

WHY THE WHOLE FLEET. `scripts/predict/regional_holdout.py` measured a ~0.20 AUC
collapse when the model is pointed at a region it has not seen: Arizona held out
scores 0.53 against 0.76 for a random holdout of identical size and an identical
349-row training set. That is geography, not sample size. The NREL label set cannot
fix it -- 81% of its rows sit west of -114 and only 5.2% east of -100.

PVDAQ can. The residential fleet is 1,629 systems and ~11,700 system-years spread
over 16 Koppen classes, 50.5% west of -114 and **42.5% east of -100**, with humid
subtropical (Cfa, 403 systems) as its largest class -- a climate NREL barely covers.
The extraction is already validated against NREL on shared ground (+0.16 pts,
95% CI [-0.62, +0.91], n=24).

TWO THINGS THIS SCRIPT EXISTS TO GET RIGHT.

**Cache warming is serial, fitting is parallel.** Open-Meteo is fetched once per
0.5-degree CELL over a fixed era window, so 1,629 systems need only ~321 requests.
Doing that inside parallel workers would race on the parquet cache files and hammer
a free-tier API that already 429s under load. So cells are warmed in one serial pass
first, and the fit workers then only ever read the cache.

**Every shard is resumable.** A full pass is ~23 CPU-hours single-threaded. The probe
used to write its JSON only at the end, so a crash, a reboot or a closed session lost
everything; it now checkpoints, and each shard skips what it has already recorded.
Re-running this script after an interruption picks up where it stopped.

Usage:
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/pvdaq_fleet_extract.py \
        --warm-only                      # just fill the irradiance cache
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/pvdaq_fleet_extract.py \
        --workers 4 --launch             # warm, then background the sharded fits
    ... --merge                          # combine shards into one JSON when done
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from scripts.analyze.pvdaq_daily_srr_probe import (
    CACHE_DIR, CELL_DEG, ERA_END, ERA_START, SYSTEMS_CSV, cell_key, fetch_hourly,
    system_meta,
)

OUT_DIR = Path("outputs/soiling/fleet")
MERGED = Path("outputs/soiling/pvdaq_fleet_labels.json")
MAX_KW = 15.0


def residential(systems: pd.DataFrame, two_channel_only: bool = True) -> pd.DataFrame:
    """Residential systems, by default only the 2-channel ones.

    The extractor reads the DAILY aggregate feed: ac_energy_..._daily_sum and
    ac_power_..._daily_mean. The 270 multi-channel systems publish a sub-daily schema
    instead (ac_power_inv_<id>, dc_voltage_inv_<id>, temperature_inverter_...), which
    this reader does not understand, so every one of them costs an S3 download and a
    column scan to produce the same "no recognised AC columns" failure. Excluding them
    is not a loss of coverage: they are 17% of the fleet and would need a different
    reader regardless.

    It also costs nothing in geography, which is the whole point of this exercise. The
    1,359 two-channel systems are **46.9% west of -114 and 45.6% east of -100** -- far
    better balanced than the NREL label set they replace (81.0% / 5.2%).
    """
    s = systems.copy()
    for c in ("latitude", "longitude", "dc_capacity_kW", "available_sensor_channels"):
        s[c] = pd.to_numeric(s[c], errors="coerce")
    s = s[(s.dc_capacity_kW <= MAX_KW)].dropna(subset=["latitude", "longitude"])
    if two_channel_only:
        s = s[s.available_sensor_channels == 2]
    return s


def cell_ranges(res: pd.DataFrame) -> dict[tuple[int, int], tuple[str, str, int]]:
    """Per cell: the union of its systems' data spans, and one representative system.

    The old warm asked every cell for the full 2009-2025 era, ~745,000 values a call.
    Open-Meteo prices by volume, so that is what produced 316 rejections out of 345.
    The union span is what the fits actually need and is 45% less data overall
    (3,217 cell-years against 5,865); the median cell needs 9 years, not 17.
    """
    r = res.copy()
    r["t0"] = pd.to_datetime(r.first_timestamp, errors="coerce", utc=True)
    r["t1"] = pd.to_datetime(r.last_timestamp, errors="coerce", utc=True)
    r = r.dropna(subset=["t0", "t1"])
    out: dict[tuple[int, int], tuple[str, str, int]] = {}
    for _, row in r.iterrows():
        k = cell_key(row.latitude, row.longitude)
        t0 = str(row.t0.date())
        t1 = str(row.t1.date())
        if k in out:
            p0, p1, sid = out[k]
            out[k] = (min(p0, t0), max(p1, t1), sid)
        else:
            out[k] = (t0, t1, int(row.system_id))
    # Clamp to the era the archive can actually serve. One cell claimed a 67-year span,
    # which is a bad timestamp rather than real coverage.
    #
    # DROP cells the clamp inverts. A cell whose systems all predate ERA_START comes out
    # as start=2009-01-01, end=2004-12-16 -- a range that is not merely empty but
    # backwards, which the archive answers with HTTP 400. Two cells did this, and
    # because a failed cell is never cached they were retried on every single pass,
    # burning two of each pass's twelve slots indefinitely. There is nothing to fetch
    # for them: the data lies wholly outside the window the archive covers.
    clamped = {k: (max(a, ERA_START), min(b, ERA_END), s_) for k, (a, b, s_) in out.items()}
    dropped = {k: v for k, v in clamped.items() if v[0] >= v[1]}
    if dropped:
        print(f"[cells] dropping {len(dropped)} cell(s) whose span falls outside "
              f"{ERA_START}..{ERA_END}: {sorted(dropped)}", flush=True)
    return {k: v for k, v in clamped.items() if v[0] < v[1]}


def cell_cached(ci: int, cj: int, start: str, end: str) -> bool:
    """True when some cached file for this cell already covers [start, end]."""
    for cand in CACHE_DIR.glob(f"cell_{ci}_{cj}_*.parquet"):
        parts = cand.stem.split("_")
        if len(parts) >= 5 and parts[-2] <= start and parts[-1] >= end:
            return True
    return False


def warm_cells(res: pd.DataFrame, max_cells: int | None = None,
               sleep_s: float = 8.0, order: str = "east-first") -> tuple[int, int]:
    """Serial, paced, resumable cache warm. Returns (ok, failed).

    Paced deliberately. The quota is on data volume over time, so the only thing that
    actually keeps a fleet-scale warm inside the free tier is spreading it out; the
    backoff in the probe handles a burst, pacing handles the budget.
    """
    import time

    ranges = cell_ranges(res)
    todo = [(k, v) for k, v in sorted(ranges.items())
            if not cell_cached(k[0], k[1], v[0], v[1])]
    if order == "east-first":
        # Warm order is a SCIENTIFIC choice, not a scheduling detail, and defaulting to
        # sorted-by-cell-index quietly wasted three days of quota. Cell keys sort by
        # latitude then longitude, which walks the fleet in roughly geographic order, so
        # the first 225 cells produced 429 labels that were 83.2% west of -114 -- no
        # better than the 81% NREL bias this whole exercise exists to escape. The
        # experiment needs eastern coverage, so fetch the east first: 109 uncached
        # eastern cells unlock 360 eastern systems.
        todo.sort(key=lambda kv: -(kv[0][1] * CELL_DEG))
    print(f"[warm] {len(res)} systems -> {len(ranges)} cells; "
          f"{len(ranges) - len(todo)} already cached, {len(todo)} to fetch", flush=True)
    if max_cells:
        todo = todo[:max_cells]
        print(f"[warm] capped at {len(todo)} cells this pass", flush=True)

    ok = failed = 0
    for i, ((ci, cj), (t0, t1, sid)) in enumerate(todo, 1):
        try:
            meta = system_meta(sid, res)
            fetch_hourly(meta, t0, t1, at="cell", era=(t0, t1))
            ok += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[warm] cell {ci}_{cj} FAILED {type(e).__name__}: "
                  f"{str(e)[:120]}", file=sys.stderr, flush=True)
        if i % 10 == 0:
            print(f"[warm] {i}/{len(todo)}  ok={ok} failed={failed}", flush=True)
        if sleep_s and i < len(todo):
            time.sleep(sleep_s)
    print(f"[warm] pass done: {ok} ok, {failed} failed", flush=True)
    return ok, failed


def fittable(res: pd.DataFrame) -> list[int]:
    """Systems whose cell irradiance is already on disk.

    The first version launched the fit workers only after the whole warm finished, so a
    warm that failed 92% of its cells produced zero fits instead of the 29 cells' worth
    it had actually earned. Fitting now runs against whatever is cached, and later
    passes pick up the rest.
    """
    ranges = cell_ranges(res)
    out = []
    for _, row in res.iterrows():
        k = cell_key(row.latitude, row.longitude)
        if k in ranges and cell_cached(k[0], k[1], *ranges[k][:2]):
            out.append(int(row.system_id))
    return sorted(out)


def launch(res: pd.DataFrame, workers: int, gamma: str) -> None:
    """Write one runnable script per shard, plus a launcher.

    It does NOT spawn them itself. The first version used subprocess.Popen with setsid
    from inside this process; every worker died on launch leaving a zero-byte log, and a
    ten-minute warm produced nothing at all. The shard command is fine -- it runs
    correctly in the foreground -- so the fault was the detachment, and the fix is to
    stop doing detachment from Python and let the shell do it, which is the pattern the
    rest of this repo's long jobs already use.
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ids = fittable(res)
    print(f"[launch] {len(ids)} of {len(res)} systems have cached irradiance", flush=True)
    if not ids:
        print("[launch] nothing fittable yet; warm more cells first", flush=True)
        return

    launcher = OUT_DIR / "run_shards.sh"
    lines = ["#!/usr/bin/env bash", "# generated by pvdaq_fleet_extract.py --launch", ""]
    for w in range(workers):
        shard = ids[w::workers]
        if not shard:
            continue
        sh = OUT_DIR / f"shard{w}.sh"
        sh.write_text(
            "#!/usr/bin/env bash\n"
            "cd /home/cameron/repos/solar-soiling-ml\n"
            "PYTHONPATH=. conda run -n solar-soiling python "
            "scripts/analyze/pvdaq_daily_srr_probe.py --irradiance-at cell "
            f"--gamma {gamma} --resume --checkpoint-every 5 "
            f"--out-json {OUT_DIR}/shard{w}.json --system {' '.join(map(str, shard))} "
            f">> {OUT_DIR}/shard{w}.log 2>&1\n"
            f"echo \"SHARD {w} EXIT $?\" >> {OUT_DIR}/shard{w}.log\n"
        )
        sh.chmod(0o755)
        lines.append(f"setsid nohup bash {sh} </dev/null >/dev/null 2>&1 & disown")
        print(f"[launch] shard {w}: {len(shard)} systems -> {sh}", flush=True)
    lines.append('echo "launched"')
    launcher.write_text("\n".join(lines) + "\n")
    launcher.chmod(0o755)
    print(f"\n[launch] wrote {launcher}\n[launch] start it with:  bash {launcher}", flush=True)


def merge() -> None:
    results, failures, seen = [], [], set()
    for f in sorted(OUT_DIR.glob("shard*.json")):
        try:
            d = json.loads(f.read_text())
        except (ValueError, OSError) as e:
            # A shard caught mid-checkpoint. The writer is write-then-rename, so the
            # previous good version is never truncated -- but a merge run while workers
            # are live can still read a file between the two. Skip and say so, rather
            # than aborting a merge over a race that resolves in seconds.
            print(f"[merge] skipping {f.name}: {type(e).__name__}", flush=True)
            continue
        for r in d.get("results", []):
            sid = int(r["meta"]["system_id"])
            if sid not in seen:
                seen.add(sid); results.append(r)
        failures += d.get("failures", [])
    MERGED.parent.mkdir(parents=True, exist_ok=True)
    MERGED.write_text(json.dumps({"results": results, "failures": failures},
                                 indent=2, default=str))
    ok = sum(1 for r in results
             if not r.get("fits", {}).get("energy", {})
                      .get("perfect_clean", {}).get("degenerate", True))
    print(f"[merge] {len(results)} systems, {ok} non-degenerate, "
          f"{len(failures)} failed -> {MERGED}")


def status() -> None:
    tot = 0
    for f in sorted(OUT_DIR.glob("shard*.json")):
        try:
            d = json.loads(f.read_text())
        except (ValueError, OSError):
            print(f"  {f.name}: unreadable (mid-write)"); continue
        n = len(d.get("results", [])); tot += n
        print(f"  {f.name}: {n} done, {len(d.get('failures', []))} failed")
    print(f"  TOTAL {tot}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--gamma", default="resolved")
    ap.add_argument("--warm-only", action="store_true")
    ap.add_argument("--max-cells", type=int, default=None,
                    help="Cap cells fetched this pass, so the warm can be run in paced "
                         "daily batches inside the free tier.")
    ap.add_argument("--sleep", type=float, default=8.0,
                    help="Seconds between cell fetches. The quota is on data volume "
                         "over time, so pacing is what keeps a fleet warm inside it.")
    ap.add_argument("--all-channels", action="store_true",
                    help="Include the 270 multi-channel systems. They publish a "
                         "sub-daily schema this reader cannot parse and will simply "
                         "fail; off by default.")
    ap.add_argument("--order", default="east-first", choices=("east-first", "index"),
                    help="Which cells to warm first. Default east-first: the label set "
                         "this replaces is 81%% western, so eastern cells are the ones "
                         "that make the regional experiment answerable.")
    ap.add_argument("--no-warm", action="store_true",
                    help="Skip warming and fit whatever is already cached.")
    ap.add_argument("--launch", action="store_true")
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        return status()
    if args.merge:
        return merge()

    res = residential(pd.read_csv(SYSTEMS_CSV),
                      two_channel_only=not args.all_channels)
    print(f"residential systems (<= {MAX_KW:g} kW): {len(res)}  "
          f"system-years {res.years.sum():.0f}", flush=True)
    if not args.no_warm:
        warm_cells(res, max_cells=args.max_cells, sleep_s=args.sleep,
                   order=args.order)
    if args.warm_only:
        return
    if args.launch:
        launch(res, args.workers, args.gamma)


if __name__ == "__main__":
    main()
