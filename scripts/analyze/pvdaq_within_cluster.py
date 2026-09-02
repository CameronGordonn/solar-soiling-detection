"""The gating measurement: is there signal to find INSIDE a shared-weather cell?

`PVDAQ_LABEL_PIPELINE_SPEC.md` Phase 3 regresses per-system soiling loss on
array-level features with cluster fixed effects. That design is only worth
running if between-system spread *within* a cell exceeds the per-label noise. The
first 12-system read gave a spread-to-noise ratio of 2.8x, but those systems
spanned different metros, so an unknown share of that spread was climate, which
the fixed effects would absorb.

This measures the ratio the design actually depends on:

    within-cell between-system SD   vs   median per-label 95% CI width

Two things it deliberately does NOT do. It does not pool systems across cells
into one SD (that would re-import the climate variance the design removes), and
it does not treat the interval as the unit anywhere. The unit is the system,
nested in the cell.

Reads the output of:
    pvdaq_daily_srr_probe.py --irradiance-at cell --out-json <...>

`--irradiance-at cell` matters here: every system in a cell then receives
IDENTICAL modeled irradiance, so a within-cell difference cannot be an artefact
of two systems carrying different irradiance-model error.

Usage:
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/pvdaq_within_cluster.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.analyze.pvdaq_daily_srr_probe import CELL_DEG, MIN_VALID_INTERVALS, cell_key

DEFAULT = Path("outputs/soiling/pvdaq_0a_withincell75.json")
#: 2x2 (irradiance source x gamma) noise budget. The pre-2026-08-27 file
#: `method_noise.json` varied irradiance only and returned 0.72 pts.
DEFAULT_NOISE = Path("outputs/soiling/method_noise_gamma.json")
#: Used only when no measured file is present, and always announced as such.
FALLBACK_NOISE_SD = 0.72


def _method_noise(path: Path) -> tuple[float, str]:
    """Total per-label method-noise SD, from the 2x2 run. Loud when it falls back."""
    if path and path.exists():
        try:
            summary = json.load(open(path)).get("summary") or {}
            sd = (summary.get("sd_pts") or {}).get("total")
            if sd:
                n = summary.get("n_systems", "?")
                return float(sd), f"{path.name}, irradiance x gamma, n={n}"
        except (ValueError, KeyError):
            pass
    return FALLBACK_NOISE_SD, ("HARDCODED FALLBACK -- irradiance-only, gamma held "
                               "fixed, so OPTIMISTIC")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fits", type=Path, default=DEFAULT)
    ap.add_argument("--min-per-cell", type=int, default=3)
    ap.add_argument("--method-noise", type=Path, default=DEFAULT_NOISE,
                    help="JSON from pvdaq_method_noise.py; supplies the per-label "
                         "method-noise SD the spread-to-noise ratio divides by.")
    args = ap.parse_args()

    rows = []
    for r in json.load(open(args.fits))["results"]:
        pc = r.get("fits", {}).get("energy", {}).get("perfect_clean", {})
        if "loss_pct" not in pc:
            continue
        m = r["meta"]
        ci, cj = cell_key(m["lat"], m["lon"])
        rows.append(dict(
            sid=m["system_id"], cell=f"{ci}_{cj}", climate=m["climate"],
            kw=m["capacity_kw"], tilt=m["tilt"], az=m["azimuth"], yrs=m["years"],
            loss=pc["loss_pct"], ciw=100 * (pc["ci95"][1] - pc["ci95"][0]),
            n_valid=pc["n_valid_intervals"],
            gamma=m.get("gamma_pdc"), gamma_tier=m.get("gamma_tier"),
        ))
    d = pd.DataFrame(rows)
    print(f"fits returned: {len(d)}")
    if "gamma_tier" in d and d.gamma_tier.notna().any():
        print("  gamma resolution: " + ", ".join(
            f"{k} {v}" for k, v in d.gamma_tier.value_counts().items())
            + f"  |  median {d.gamma.median():+.5f}")
    ok = d[d.n_valid >= MIN_VALID_INTERVALS].copy()
    print(f"passing QC (n_valid >= {MIN_VALID_INTERVALS}): {len(ok)}\n")
    if ok.empty:
        return

    g = ok.groupby("cell")
    cells = pd.DataFrame({
        "n": g.size(), "loss_sd": g.loss.std(), "loss_p50": g.loss.median(),
        "loss_min": g.loss.min(), "loss_max": g.loss.max(),
        "ciw_med": g.ciw.median(), "tilt_sd": g.tilt.std(),
    })
    cells["spread_to_noise"] = cells.loss_sd / cells.ciw_med
    cells = cells[cells.n >= args.min_per_cell].sort_values("n", ascending=False)
    print(f"cells with >= {args.min_per_cell} QC-passing systems: {len(cells)}")
    print(cells.round(3).to_string())
    if cells.empty:
        print("\nNo cell retained enough systems. The gating question is unresolved.")
        return

    # The headline. Median across cells, so one big cell cannot carry the result.
    med = cells.spread_to_noise.median()
    print(f"\nWITHIN-CELL spread-to-noise, median across {len(cells)} cells: {med:.2f}x")
    print(f"  per-cell range: {cells.spread_to_noise.min():.2f}x to "
          f"{cells.spread_to_noise.max():.2f}x")
    print(f"  within-cell between-system SD, median: {cells.loss_sd.median():.2f} pts")
    print(f"  per-label 95% CI width, median:        {cells.ciw_med.median():.2f} pts")

    # How much of the total variance is WITHIN cells vs between them? The fixed
    # effects absorb the between-cell part, so only the within part is available
    # to the array-level features.
    grand = ok.loss.var(ddof=1)
    within = ok.groupby("cell").loss.transform("mean")
    within_var = (ok.loss - within).var(ddof=1)
    print(f"\n  total variance {grand:.3f}  |  within-cell {within_var:.3f} "
          f"({100 * within_var / grand:.0f}% of total)")
    print("  Only the within-cell share is available to array-level features;")
    print("  cluster fixed effects absorb the rest.")

    # Method noise is READ, not hardcoded. It was hardcoded at 0.72 until
    # 2026-08-27, and that figure was measured with gamma held fixed -- it excluded
    # the module temperature coefficient, the largest known term in the budget. A
    # constant copied out of another script's output is exactly how this repo has
    # been bitten before, so the number now travels with its provenance.
    noise_sd, noise_src = _method_noise(args.method_noise)
    real = (cells.loss_sd / noise_sd).median()
    print(f"\n  spread-to-noise vs MEASURED method error "
          f"({noise_sd:.2f} pts, {noise_src}): {real:.2f}x")
    print(f"    (bootstrap-CI version, for reference: {med:.2f}x)")
    med = real

    print("\nVERDICT")
    # `pess` was a leftover from the version that reported a BRACKET, before
    # pvdaq_method_noise.py measured method error directly. It was deleted from the
    # rest of this function and left here, so every run reached this line and died
    # with a NameError AFTER printing the whole table -- which looks like a clean
    # run if you only read stdout. Found 2026-08-27 on the resolved-gamma refit.
    if med >= 2.0:
        print(f"  {med:.2f}x -- within-cell spread clearly exceeds label noise.")
        print("  Phase 3 has signal to fit. Proceed.")
    elif med >= 1.0:
        print(f"  {med:.2f}x -- within-cell spread is comparable to label noise.")
        print("  Phase 3 is underpowered as specified. Either tighten labels")
        print("  (more years per system, better irradiance) or expand n per cell")
        print("  before committing to the design.")
    else:
        print(f"  {med:.2f}x -- label noise EXCEEDS within-cell spread.")
        print("  Phase 3 as specified cannot resolve array-level effects. A null")
        print("  result would measure our noise floor, not nature. Do not run it")
        print("  as the headline experiment without fixing precision first.")
    print("\n  Caveat: method sensitivity (energy vs power_max PI) moved the 12-system")
    print("  labels by ~1.6 pts median. The CI width above is WITHIN-method precision")
    print("  and understates total uncertainty, so treat this ratio as optimistic.")


if __name__ == "__main__":
    main()
