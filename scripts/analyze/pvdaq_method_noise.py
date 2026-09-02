"""How much of our per-roof soiling label is measurement error?

THE DECIDING TEST for `PVDAQ_LABEL_PIPELINE_SPEC.md` Phase 3.

Phase 2 left the per-label noise bracketed rather than known:

  * the bootstrap CI says 0.63 pts (within-method precision only)
  * PVDAQ-vs-NREL disagreement says 2.14 pts, but that mixes method error with
    REAL spatial variation, since a system sits up to 5 km from its station

Within-cell between-system spread is 1.41 pts, so the two ends of that bracket
disagree about whether per-roof ranking is possible at all (2.55x vs 0.66x).

This separates them. Refit the SAME system, SAME years, changing only the
irradiance source:

  openmeteo : Open-Meteo archive (ERA5 reanalysis) -> pvlib Perez transposition
  pvgis     : PVGIS (PVGIS-NSRDB satellite over the Americas), POA served direct

Same roof, same period, so there is ZERO real spatial variation between the two
estimates. Everything that moves is method error. That is the number Phase 3's
viability actually turns on, and nothing else we have measures it cleanly.

The two paths differ in transposition as well as in the underlying irradiance,
which is deliberate: we want TOTAL method error for the pipeline as built, not
the irradiance term alone.

GAMMA ADDED AS A SECOND FACTOR, 2026-08-27. The first version of this test varied
only the irradiance source and returned SD 0.72 pts. That figure EXCLUDED the
largest known term in the budget: the module temperature coefficient was hardcoded
fleet-wide, unvalidated, and two scripts in the tree disagreed about it by 0.0010,
worth 1.045 pts of label on system 10109. So 0.72 was optimistic by construction.

This now runs a 2x2 factorial, irradiance source x gamma:

  gamma_resolved : per-system, looked up from PVDAQ module metadata against the CEC
                   database (`src/risk/module_gamma.py`) -- what the pipeline now does
  gamma_fleet    : the -0.0045 constant every recorded label was produced with

and reports three SDs that answer three different questions:

  irradiance-only   the original number, gamma held fixed -- comparable to the 0.72
  gamma-only        same irradiance, gamma swapped -- the term that was missing
  total             both varied -- the honest per-label method noise

Usage:
    PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/pvdaq_method_noise.py \
        --system 10109 10112 10477 11758 11881 --out-json outputs/soiling/method_noise.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.analyze.pvdaq_daily_srr_probe import (
    GAMMA_PDC, MIN_VALID_INTERVALS, SYSTEMS_CSV, TEMP_MODEL,
    build_pi, fetch_hourly, load_daily, modeled_daily, run_srr, system_meta,
)

CACHE = Path(".cache/soiling/pvgis")


def pvgis_modeled_daily(meta: dict, start: str, end: str,
                        gamma: float | None = None) -> pd.DataFrame:
    """Daily modeled energy from PVGIS hourly POA. Same downstream shape as
    `modeled_daily`, so `build_pi` cannot tell which source produced it.

    PVGIS serves plane-of-array components directly for the requested surface, so
    there is no transposition step on this path. That is the point: an
    independent estimate, not a re-run of ours with different numbers.
    """
    import pvlib
    from pvlib import iotools

    CACHE.mkdir(parents=True, exist_ok=True)
    # PVGIS-NSRDB serves 2005-2023 only; asking outside that is a hard HTTPError,
    # which silently killed 11 of 14 systems on the first run. Clamp, and let
    # build_pi's inner join drop the measured days the model cannot cover.
    y0, y1 = max(2005, int(start[:4])), min(2023, int(end[:4]))
    if y1 < y0:
        raise ValueError(f"system {meta['system_id']}: no overlap with PVGIS 2005-2023")
    f = CACHE / f"{meta['system_id']}_{y0}_{y1}.parquet"
    if f.exists():
        h = pd.read_parquet(f)
    else:
        # PVGIS surface_azimuth in pvlib >=0.11 follows the pvlib convention
        # (0 = north, 180 = south), matching what PVDAQ stores.
        h, _ = iotools.get_pvgis_hourly(
            meta["lat"], meta["lon"], start=y0, end=y1,
            surface_tilt=meta["tilt"], surface_azimuth=meta["azimuth"],
            components=True, outputformat="json", map_variables=True, timeout=180,
        )
        h.to_parquet(f)

    poa = (h["poa_direct"] + h["poa_sky_diffuse"] + h["poa_ground_diffuse"]).clip(lower=0)
    tcell = pvlib.temperature.sapm_cell(
        poa_global=poa, temp_air=h["temp_air"].values,
        wind_speed=h["wind_speed"].values, **TEMP_MODEL,  # PVGIS wind is m/s already
    )
    g = float(meta.get("gamma_pdc") or GAMMA_PDC) if gamma is None else float(gamma)
    e = (meta["capacity_kw"] * (poa / 1000.0)
         * (1.0 + g * (tcell - 25.0))).clip(lower=0)
    local = e.index.tz_convert(meta["tz"]).tz_localize(None).normalize()
    day = pd.DataFrame({"e_model_kwh": e.values, "poa_wh": poa.values},
                       index=local).groupby(level=0).sum()
    day["insolation_kwh_m2"] = day.pop("poa_wh") / 1000.0
    return day


ARMS = (("openmeteo", "resolved"), ("pvgis", "resolved"),
        ("openmeteo", "fleet"), ("pvgis", "fleet"))
#: Explicit, because deriving it as `source[:2]` gave "op" for openmeteo while the
#: contrasts below were written against "om". Every fit ran, then the summary died.
ARM_COL = {"openmeteo": "om", "pvgis": "pv"}


def one(sid: int, systems: pd.DataFrame) -> dict | None:
    """Fit the same roof and years four ways: 2 irradiance sources x 2 gamma choices."""
    meta = system_meta(sid, systems)
    daily = load_daily(sid)
    s, e = str(daily.index.min().date()), str(daily.index.max().date())
    out = {"meta": meta, "arms": {}}

    # Irradiance is fetched once per source and reused across the gamma arms, so the
    # gamma contrast is exact: identical POA and cell temperature, one constant moved.
    hourly = {}
    for src, gname in ARMS:
        arm = f"{src}__{gname}"
        gamma = None if gname == "resolved" else GAMMA_PDC
        try:
            if src == "openmeteo":
                if src not in hourly:
                    hourly[src] = fetch_hourly(meta, s, e, at="system")
                model = modeled_daily(meta, hourly[src], gamma=gamma)
            else:
                model = pvgis_modeled_daily(meta, s, e, gamma=gamma)
            pi, insol = build_pi(daily, model, "energy")
            if pi.dropna().empty:
                out["arms"][arm] = {"error": "empty PI"}
                continue
            fit = run_srr(pi, insol)["perfect_clean"]
            out["arms"][arm] = {
                "loss_pct": fit["loss_pct"], "ci95": fit["ci95"],
                "n_valid_intervals": fit["n_valid_intervals"],
                "degenerate": fit["degenerate"], "pi_median": float(pi.median()),
                "gamma": float(gamma if gamma is not None else meta["gamma_pdc"]),
            }
        except Exception as exc:  # noqa: BLE001 - one bad arm must not kill the run
            out["arms"][arm] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", type=int, nargs="+", required=True)
    ap.add_argument("--out-json", type=Path, default=None)
    args = ap.parse_args()

    systems = pd.read_csv(SYSTEMS_CSV)
    for c in ("latitude", "longitude", "elevation_m", "dc_capacity_kW", "tilt",
              "azimuth", "years", "available_sensor_channels"):
        systems[c] = pd.to_numeric(systems[c], errors="coerce")

    results = []
    for sid in args.system:
        print(f"== {sid}", flush=True)
        try:
            r = one(sid, systems)
        except Exception as exc:  # noqa: BLE001
            print(f"   FAILED {type(exc).__name__}: {exc}", flush=True)
            continue
        if r is None:
            continue
        results.append(r)
        for arm, v in r["arms"].items():
            txt = (f"{v['loss_pct']:.2f} pts (n_valid {v['n_valid_intervals']}, "
                   f"gamma {v['gamma']:+.5f})" if "loss_pct" in v
                   else v.get("error", "?"))
            print(f"   {arm:22s} {txt}", flush=True)

    # One row per system, only where all four arms produced a QC-passing fit, so
    # every SD below is computed on the SAME systems and the three are comparable.
    rows = []
    for r in results:
        a = r["arms"]
        if not all(isinstance(a.get(f"{s_}__{g_}"), dict) and "loss_pct" in a[f"{s_}__{g_}"]
                   and a[f"{s_}__{g_}"]["n_valid_intervals"] >= MIN_VALID_INTERVALS
                   for s_, g_ in ARMS):
            continue
        row = {"sid": r["meta"]["system_id"],
               "gamma_res": r["meta"]["gamma_pdc"],
               "gamma_tier": r["meta"]["gamma_tier"],
               "om_ciw": 100 * (a["openmeteo__resolved"]["ci95"][1]
                                - a["openmeteo__resolved"]["ci95"][0])}
        for s_, g_ in ARMS:
            row[f"{ARM_COL[s_]}_{g_[:3]}"] = a[f"{s_}__{g_}"]["loss_pct"]
        rows.append(row)
    d = pd.DataFrame(rows)

    print("\n" + "=" * 78)
    summary = {}
    if len(d) < 3:
        print(f"only {len(d)} systems produced all four arms; inconclusive")
    else:
        # Contrasts, each a paired difference on the same roof and years, in the
        # repo's existing convention (the prior 0.72 was an SD of om-minus-pvgis,
        # not a per-label sigma; a difference SD is sqrt(2) larger by construction,
        # and it is kept here only so the new number is comparable to the old one).
        d["d_irrad"] = d.om_res - d.pv_res     # gamma held at the resolved value
        d["d_gamma"] = d.om_res - d.om_fle     # irradiance held at Open-Meteo
        # BOTH diagonals, because the cancellation is diagonal-specific: measured
        # here one diagonal gives 0.72 and the other 0.92, so quoting either alone
        # is luck. A single corner-to-corner contrast is NOT a total-noise estimator.
        d["d_diag1"] = d.om_res - d.pv_fle
        d["d_diag2"] = d.om_fle - d.pv_res
        print(d.round(3).to_string(index=False))

        sd = {k: float(d[f"d_{k}"].std()) for k in ("irrad", "gamma", "diag1", "diag2")}
        mn = {k: float(d[f"d_{k}"].mean()) for k in ("irrad", "gamma", "diag1", "diag2")}
        sd["total"] = float(np.sqrt(np.mean([d.d_diag1.var(ddof=1),
                                             d.d_diag2.var(ddof=1)])))
        mn["total"] = float(np.mean([mn["diag1"], mn["diag2"]]))
        # Direct per-label sigma: each arm is one defensible way to build the same
        # label, so the spread ACROSS arms estimates sigma without the sqrt(2).
        arm_cols = [f"{ARM_COL[s_]}_{g_[:3]}" for s_, g_ in ARMS]
        sd["per_label_sigma"] = float(np.sqrt(np.mean(d[arm_cols].var(axis=1, ddof=1))))

        print(f"\nMETHOD NOISE, decomposed (same roof, same years, n = {len(d)} systems)")
        for k, lab in (("irrad", "irradiance source only (gamma held)"),
                       ("gamma", "gamma only (irradiance held)"),
                       ("total", "irradiance + gamma, both diagonals")):
            print(f"  {lab:<38s} mean {mn[k]:+.2f}   SD {sd[k]:.2f} pts")
        print(f"  {'per-label sigma (across all 4 arms)':<38s}            "
              f"SD {sd['per_label_sigma']:.2f} pts")

        gv = d.d_gamma.var(ddof=1)
        iv = d.d_irrad.var(ddof=1)
        print(f"\n  GAMMA'S SHARE of the two contrasts' variance: "
              f"{100 * gv / (gv + iv):.1f}%. Real, and secondary to the irradiance "
              "source.")
        print(f"  Resolved coefficients span {d.gamma_res.min():+.5f} to "
              f"{d.gamma_res.max():+.5f} (fleet constant {GAMMA_PDC:+.5f}).")
        print("  Tier mix: " + ", ".join(f"{k} {v}" for k, v in
                                         d.gamma_tier.value_counts().items()))
        print(f"\n  bootstrap CI width claimed, median: {d.om_ciw.median():.2f} pts")
        print(f"  -> the bootstrap understates total method noise by "
              f"{sd['total'] / d.om_ciw.median():.1f}x")

        # Owned by pvdaq_within_cluster.py, which is the script that measures it.
        # Refreshed 2026-08-27 from the RESOLVED-gamma refit of all 75 within-cell
        # systems (66 pass QC across 9 cells); the prior 1.41 was measured on
        # fleet-gamma labels, so pairing it with a gamma-aware noise figure mixed a
        # new numerator with an old denominator.
        within_cell_sd = 1.46
        print(f"\nPHASE 3 VERDICT  (within-cell between-system SD = {within_cell_sd} pts,")
        print("  measured by pvdaq_within_cluster.py on the resolved-gamma refit)")
        ratio = within_cell_sd / sd["total"] if sd["total"] > 0 else np.inf
        print(f"  spread-to-noise = {within_cell_sd} / {sd['total']:.2f} = {ratio:.2f}x")
        if ratio >= 2.0:
            print("  >= 2x: real roof differences clearly exceed method error.")
            print("  Per-roof ranking is measurable. Phase 3 proceeds as specified.")
        elif ratio >= 1.0:
            print("  1-2x: marginal. Ranking is possible but weak; expect wide CIs")
            print("  on any per-feature coefficient. Consider more years per system.")
        else:
            print("  < 1x: method error EXCEEDS real roof-to-roof differences.")
            print("  Per-roof ranking is NOT measurable with this pipeline. A Phase 3")
            print("  null would describe our own noise. Fix precision or change the")
            print("  question before building the label set.")
        summary = {"n_systems": int(len(d)), "sd_pts": sd, "mean_pts": mn,
                   "bootstrap_ciw_median": float(d.om_ciw.median()),
                   "spread_to_noise_total": float(ratio),
                   "within_cell_sd_assumed": within_cell_sd,
                   "per_system": d.to_dict(orient="records")}

    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        json.dump({"results": results, "summary": summary},
                  open(args.out_json, "w"), indent=2, default=str)
        print(f"\nwrote {args.out_json}")


if __name__ == "__main__":
    main()
