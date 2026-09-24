#!/usr/bin/env python3
"""Re-score an AOI's dollars through the grounded economics engine.

Every cached ``risk.geojson`` / ``recommendations.json`` / ``manifest.json`` predates the
economics grounding pass and may encode retired defaults such as a flat ``$0.25/kWh``.
The current public regular-soiling scenario values a July clean across the remaining
April-September dry-season production; the weather-trajectory ``0.045`` result remains
a historical sensitivity, not the public planning default. This regenerates the dollar
layer so what is served matches the code.

.. note:: **Corrected 2026-09-24.** The two sentences above described the state between
   2026-09-04 and 2026-09-24, when ``DEFAULT_RECOVERY_PRO`` had been silently set to the
   modelled dry-season figure (0.445). That was a regression: it is a DRY-SEASON share
   being multiplied by an ANNUAL loss, and re-running this script on it would have
   reported 80 of 1,865 sites worth cleaning instead of zero. The measured 0.045 is the
   default again and is what this script records. See
   ``docs/RECOVERY_RECONCILIATION_PLAN.md``.

    PYTHONPATH=. python scripts/analyze/rebuild_aoi_economics.py --aoi santa-cruz-w2-21cm

**It does not renumber anything.** ``array_id`` is carried through untouched, because 97
QR codes are printed on cards already in people's homes and resolve by that id
(``configs/outreach/published_qr_ids.csv``). Writes ``risk_econ.geojson`` alongside the
input rather than overwriting ``risk.geojson``, so a bad run is a no-op on the served
artifact and the diff is inspectable before anything is promoted.

Outputs under ``outputs/aoi/<aoi>/``:
    risk_econ.geojson          per-array, with site grouping + loss PI + net-$ + P(net>0)
    site_economics.csv         one row per SITE (parcel), the costing unit
    econ_summary.json          AOI rollup + the constants used
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))


def main(argv=None) -> int:
    import geopandas as gpd
    import pandas as pd

    from risk import rates
    from risk.economics import (
        BASE_SOILING_PCT, BASE_SUN, DEFAULT_RECOVERY_PRO, DEFAULT_SCENARIOS, M2_PER_KW,
        PACKING_FACTOR, Uncertainty, array_recommendation_mc, sun_hours_from_poa_rel,
    )
    from risk.degradation import (
        MEDIAN_DEGRADATION_PCT_PER_YR, age_years, production_factor,
    )
    from risk.level_calibration import level_factor
    from risk.loss_model import load_bundle
    from risk.site_cluster import assign_sites, cluster_diagnostics

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aoi", default="santa-cruz-w2-21cm")
    ap.add_argument("--risk-file", default="risk.geojson")
    ap.add_argument("--features", default="features/inference_matrix.parquet")
    ap.add_argument("--loss-model", default="runs/soiling/run_lossreg/loss_regressor")
    ap.add_argument("--parcels",
                    default="data/external/santa_cruz_parcels/aoi_santa-cruz-outreach-v1.geojson")
    ap.add_argument("--regime", default="nbt_no_battery", choices=list(rates.REGIMES))
    ap.add_argument("--mc-samples", type=int, default=1000)
    ap.add_argument("--roof-planes", default="roof_planes.csv",
                    help="per-array lidar tilt/azimuth/POA, relative to the AOI dir; "
                         "sites without a usable fit keep the flat BASE_SUN")
    ap.add_argument("--vintage", default="site_vintage.csv",
                    help="per-site install dates, for tariff regime and degradation")
    ap.add_argument("--site-group-map", default="public_site_group_map.csv",
                    help="anonymous array_id -> public site-key grouping, relative to the AOI dir; "
                         "preserves an established dashboard household unit when private parcels are unavailable")
    ap.add_argument("--degradation-pct-yr", type=float, default=None,
                    help="override the Jordan & Kurtz median 0.5%%/yr")
    ap.add_argument("--level-calibration", type=float, default=None,
                    help="override the measured level factor; 1.0 disables it and "
                         "restores the pre-2026-08-30 published level")
    ap.add_argument("--out-prefix", default="risk_econ")
    args = ap.parse_args(argv)

    aoi_dir = REPO / "outputs/aoi" / args.aoi
    gdf = gpd.read_file(aoi_dir / args.risk_file)
    n_in = len(gdf)
    assert "array_id" in gdf.columns, "array_id is the QR key — refusing to run without it"
    ids_before = set(gdf["array_id"].astype(str))

    # ── predicted loss + interval ─────────────────────────────────────────────
    feat_path = aoi_dir / args.features
    if feat_path.is_file():
        bundle = load_bundle(REPO / args.loss_model)
        feats = pd.read_parquet(feat_path)
        pred = bundle.predict(feats)
        pred["array_id"] = feats["array_id"].values
        gdf = gdf.merge(pred, on="array_id", how="left")
        loss_source = "regression_head"
    else:
        for c, v in (("loss_pct_p10", BASE_SOILING_PCT * 1.10 / 4.70),
                     ("loss_pct_p50", BASE_SOILING_PCT),
                     ("loss_pct_p90", BASE_SOILING_PCT * 9.05 / 4.70)):
            gdf[c] = v
        loss_source = "coastal_ca_median_fallback"
    print(f"[loss] source={loss_source}")

    # ── level calibration ─────────────────────────────────────────────────────
    # The loss head's LEVEL was measured high against roofs near this AOI, and that
    # had been flagged-but-uncorrected in known_limitations since 2026-08-12. It is
    # corrected here rather than at the model, because the cause is the training
    # geography and not the fit (see src/risk/level_calibration for the full
    # argument, the evidence, and how to re-measure it).
    #
    # Applied to all three quantiles by the same factor, so the interval keeps its
    # relative width. Nothing about the ranking or the spread is claimed to improve;
    # the model still cannot discriminate within this AOI and known_limitations still
    # says so. Only the level moves.
    if args.level_calibration is not None:
        lvl, lvl_why = args.level_calibration, "explicit --level-calibration override"
    elif loss_source == "regression_head":
        lvl, lvl_why = level_factor(args.aoi, REPO / "outputs/soiling/aoi_level_check.json")
    else:
        # BASE_SOILING_PCT is already the independently measured coastal-CA
        # reference level. Applying the regression head's 0.5 correction a
        # second time would turn a 2.80% fallback into an unsupported 1.40%.
        lvl, lvl_why = 1.0, "coastal-CA fallback already supplies the calibrated level"
    if lvl != 1.0:
        for c in ("loss_pct_p10", "loss_pct_p50", "loss_pct_p90"):
            if c in gdf.columns:
                gdf[c] = gdf[c] * lvl
        print(f"[loss] level calibration x{lvl:.4f} — {lvl_why}")
    else:
        print(f"[loss] no level calibration — {lvl_why}")

    # ── site grouping: one truck roll per parcel ──────────────────────────────
    parcels = None
    pp = REPO / args.parcels
    if pp.is_file():
        parcels = gpd.read_file(pp)
    # ── measured roof orientation ─────────────────────────────────────────────
    # Replaces the flat BASE_SUN (a GHI figure) with per-roof plane-of-array irradiance.
    # Fleet-mean POA/GHI is 1.029, so AOI totals barely move (+3.0% measured), but per-home
    # the multiplier spans 0.889-1.156. Note the sign against THIS baseline: 69% of sites go
    # UP, because BASE_SUN is a GHI figure and every tilted roof beats the horizontal -- it
    # is only against the south-20deg reference that most roofs score low. The current
    # public model uses these inputs for the Regular Soiling planning scenario and keeps
    # Persistent Soiling as a separate inspection screen.
    rp_path = aoi_dir / args.roof_planes
    if rp_path.is_file():
        rp = pd.read_csv(rp_path)
        rp = rp.loc[rp["fit_ok"].astype(bool), ["index", "poa_rel", "tilt_deg", "azimuth_deg"]]
        gdf = gdf.merge(rp.rename(columns={"index": "array_id"}), on="array_id", how="left")
        print(f"[roof] {gdf['poa_rel'].notna().sum()}/{len(gdf)} arrays with measured "
              f"orientation (median poa_rel {gdf['poa_rel'].median():.3f})")
    else:
        print(f"[roof] no {rp_path.name}; falling back to flat BASE_SUN for every site")

    # Reuse the established public household grouping when it is present. The
    # private parcel layer is intentionally gitignored and is often unavailable
    # in a developer checkout; silently falling back to proximity can change the
    # published system count and, therefore, the per-site truck-roll economics.
    # The group map contains only array ids and already-public salted site keys.
    group_map_path = aoi_dir / args.site_group_map
    if group_map_path.is_file():
        group_map = pd.read_csv(group_map_path)
        required = {"array_id", "site_key"}
        missing = required.difference(group_map.columns)
        if missing:
            raise ValueError(f"{group_map_path} missing columns: {sorted(missing)}")
        if group_map["array_id"].duplicated().any():
            raise ValueError(f"{group_map_path} has duplicate array_id values")
        group_by_array = dict(zip(group_map["array_id"].astype(int), group_map["site_key"].astype(str)))
        live_ids = set(gdf["array_id"].astype(int))
        map_ids = set(group_by_array)
        if live_ids != map_ids:
            missing_ids = sorted(live_ids - map_ids)
            extra_ids = sorted(map_ids - live_ids)
            raise ValueError(
                f"{group_map_path} does not match {args.risk_file}: "
                f"missing={missing_ids[:5]} extra={extra_ids[:5]}. "
                "Regenerate the group map deliberately; refusing to change household grouping."
            )
        gdf["site_id"] = gdf["array_id"].astype(int).map(lambda aid: f"public:{group_by_array[aid]}")
        gdf["site_source"] = "public_group_map"
        print(f"[sites] reused {group_map_path.name} ({gdf['site_id'].nunique()} established sites)")
    else:
        gdf = assign_sites(gdf, parcels=parcels)
        print(f"[sites] no {group_map_path.name}; using parcel/proximity grouping")
    diag = cluster_diagnostics(gdf)
    print(f"[sites] {diag['n_polygons']} polygons -> {diag['n_sites']} sites "
          f"({diag['fragmentation_factor']:.2f}x fragmentation)")

    # ── per-site tariff vintage + age ─────────────────────────────────────────
    # Both come from the same install date. They pull in OPPOSITE directions: a legacy
    # NEM home is worth 2.78x more per lost kWh, and is also older and therefore more
    # degraded. Neither was in the chain before 2026-08-19.
    vin = {}
    vp = aoi_dir / args.vintage
    if vp.is_file():
        v = pd.read_csv(vp)
        vin = dict(zip(v["site_id"], v["install_date"]))
        n_dated = sum(1 for x in vin.values() if isinstance(x, str))
        print(f"[vintage] {n_dated}/{len(vin)} sites carry an install date; the rest are "
              f"priced on the MEASURED AOI tariff mix "
              f"({100*rates.AOI_TARIFF_MIX['nem2_legacy']:.1f}% legacy)")
    else:
        print(f"[vintage] no {vp.name}; every site priced on the AOI tariff mix")
    deg_rate = args.degradation_pct_yr
    rate = rates.marginal_value_usd_per_kwh(regime=args.regime)
    print(f"[rate] fallback regime {args.regime} = ${rate:.4f}/kWh   "
          f"AOI blend ${rates.AOI_BLENDED_USD_PER_KWH:.4f}   recovery={DEFAULT_RECOVERY_PRO}")

    # ── economics, per SITE ───────────────────────────────────────────────────
    site_rows = []
    per_array: dict = {}
    for site_id, grp in gdf.groupby("site_id", sort=False):
        area = pd.to_numeric(grp["area_m2"], errors="coerce").fillna(0.0)
        site_area = float(area.sum())
        w = area.to_numpy() if site_area > 0 else None

        def _wavg(col):
            v = pd.to_numeric(grp[col], errors="coerce").to_numpy(dtype=float) \
                if col in grp.columns else None
            if v is None:
                return None
            import numpy as np
            ok = np.isfinite(v)
            return float(np.average(v[ok], weights=(w[ok] if w is not None else None))) \
                if ok.any() else None

        p10, p50, p90 = _wavg("loss_pct_p10"), _wavg("loss_pct_p50"), _wavg("loss_pct_p90")
        if p50 is None:
            p50 = BASE_SOILING_PCT
        kw = site_area / M2_PER_KW if site_area > 0 else None
        if not kw:
            continue

        site_poa_rel = _wavg("poa_rel")
        sun_hours = sun_hours_from_poa_rel(site_poa_rel, BASE_SUN)

        # Tariff and degradation, both keyed off the install date where we have one.
        install = vin.get(site_id)
        install = install if isinstance(install, str) else None
        site_rate, rate_src = rates.marginal_value_for_site(install)
        age = age_years(install)
        deg = (production_factor(age) if deg_rate is None
               else production_factor(age, deg_rate))
        sun_hours = sun_hours * deg     # degradation scales delivered energy, like a derate

        econ = array_recommendation_mc(
            p50, kw, sun_hours, site_rate,
            unc=Uncertainty(loss_pct_p10=p10, loss_pct_p90=p90),
            n_samples=args.mc_samples, regime=args.regime,
        )
        rec = {
            "site_id": site_id, "site_source": grp["site_source"].iloc[0],
            "n_polygons": int(len(grp)), "area_m2": round(site_area, 2),
            "system_kw": round(kw, 3),
            "loss_pct_p10": p10, "loss_pct_p50": p50, "loss_pct_p90": p90,
            "poa_rel": round(site_poa_rel, 4) if site_poa_rel is not None else None,
            "install_date": install,
            "age_years": round(age, 1) if age is not None else None,
            "degradation_factor": round(deg, 4),
            "usd_per_kwh": round(site_rate, 4),
            "rate_source": rate_src,
            "sun_hours": round(sun_hours, 3),
            "sun_hours_source": "lidar_orientation" if site_poa_rel is not None else "flat_ghi",
            "annual_loss_usd": econ["annual_loss_usd"],
            "economic_action": econ["recommended_action"],
            "worth_cleaning": econ["worth_cleaning"],
            "expected_net_usd": econ["expected_net_usd"],
            "expected_net_usd_p10": econ["expected_net_usd_p10"],
            "expected_net_usd_p90": econ["expected_net_usd_p90"],
            "prob_net_positive": econ["prob_net_positive"],
            "decision_robust": econ["decision_robust"],
        }
        site_rows.append(rec)
        primary = area.idxmax()
        for idx in grp.index:
            per_array[idx] = {**rec, "site_primary": bool(idx == primary)}

    sites = pd.DataFrame(site_rows)
    # Copy the SITE's economics onto each of its polygons, but NEVER over a per-polygon
    # geometric column. `area_m2` is the polygon's own area and the dashboard renders it
    # per-array; overwriting it with the site total made a 2-polygon roof report its
    # neighbour's area too (a 22-polygon parcel read as a 1.4 MW "array").
    PER_POLYGON = {"area_m2", "perimeter_m", "compactness", "n_polygons"}
    RENAME = {"area_m2": "site_area_m2", "n_polygons": "site_n_polygons"}
    econ_cols = [c for c in sites.columns if c != "site_id"]
    for c in econ_cols:
        dst = RENAME.get(c, c)
        if c in PER_POLYGON and dst == c:
            continue
        gdf[dst] = [per_array.get(i, {}).get(c) for i in gdf.index]
    gdf["site_primary"] = [per_array.get(i, {}).get("site_primary", False) for i in gdf.index]
    gdf["site_id"] = [per_array.get(i, {}).get("site_id") for i in gdf.index]

    # ── the QR invariant ──────────────────────────────────────────────────────
    ids_after = set(gdf["array_id"].astype(str))
    assert ids_after == ids_before, "array_id set changed — that would break mailed QR codes"
    assert len(gdf) == n_in, "row count changed"
    print(f"[qr] array_id set preserved exactly ({len(ids_after)} ids)")

    out_geo = aoi_dir / f"{args.out_prefix}.geojson"
    gdf.to_file(out_geo, driver="GeoJSON")
    sites.to_csv(aoi_dir / "site_economics.csv", index=False)

    n_clean = int(sites.worth_cleaning.sum())
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "aoi": args.aoi,
        # `regime` is only the FALLBACK now. Sites are priced per-site from their own
        # install date where one exists and from the measured AOI mix otherwise, so
        # recording a single regime here would misdescribe the run.
        "fallback_regime": args.regime,
        "tariff_mix": rates.AOI_TARIFF_MIX,
        "aoi_blended_usd_per_kwh": round(rates.AOI_BLENDED_USD_PER_KWH, 4),
        "rate_source_counts": sites["rate_source"].value_counts().to_dict()
        if "rate_source" in sites.columns else {},
        "degradation_pct_per_yr": (args.degradation_pct_yr
                                   if args.degradation_pct_yr is not None
                                   else MEDIAN_DEGRADATION_PCT_PER_YR),
        "loss_source": loss_source,
        "n_polygons": n_in, "n_sites": int(len(sites)),
        "cluster_diagnostics": diag,
        "n_sites_worth_cleaning": n_clean,
        "pct_sites_worth_cleaning": round(100 * n_clean / max(1, len(sites)), 3),
        "max_prob_net_positive": float(sites.prob_net_positive.max()),
        "constants": {
            "elec_rate_usd_per_kwh": rate,
            "retail_offset": rates.RETAIL_OFFSET_USD_PER_KWH,
            "export_credit": rates.ACC_EXPORT_PROD_WEIGHTED_USD_PER_KWH,
            "recovery_frac_professional": DEFAULT_RECOVERY_PRO,
            "m2_per_kw": M2_PER_KW, "packing_factor": PACKING_FACTOR,
            "min_pro_service": DEFAULT_SCENARIOS["professional"]["cost_fn"](6),
            "min_basic_service": DEFAULT_SCENARIOS["rinse_service"]["cost_fn"](6),
            "level_calibration": lvl,
            "level_calibration_source": lvl_why,
        },
        "known_limitations": [
            "Loss model does not discriminate within an AOI (0.76 pts p10-p90 across "
            "1,865 sites) — valid for level, NOT for ranking homes against each other. "
            "58.1% of model importance sits on features that are effectively constant "
            "across the AOI (measured 2026-08-12; the earlier note said 21.8%, which "
            "counted only the absent features and missed the all-NaN and low-variance "
            "ones). This is STRUCTURAL, not a plumbing gap: see next entry.",
            "Supplying the missing features was tested on 2026-08-12 and does NOT fix "
            "the ranking limitation. Backfilling all five worldcover_* one-hots moved "
            "dead importance 64.9% -> 58.1% and the p10-p90 spread 0.776 -> 0.758 pts, "
            "i.e. nothing. Cause: the training medians were already almost exactly right "
            "for this AOI (worldcover_cropland median 0.0, and the AOI is 0.0). A model "
            "trained to separate 15 states has little left when shown 1,865 homes in one "
            "town. Do not re-attempt without a new hypothesis.",
            "LEVEL WAS BIASED HIGH AND IS NOW CORRECTED, 2026-08-30. The raw head "
            "predicts ~5.53% annual soiling loss for this AOI. PVDAQ residential systems "
            "within 120 km measure a median of 2.76% (n=118), so the head runs 2.00x the "
            "level measured on real roofs nearby; the four systems within 25 km say 2.68x. "
            "A multiplicative calibration of "
            f"{level_factor(args.aoi, REPO / 'outputs/soiling/aoi_level_check.json')[0]:.3f} "
            "is applied to all three quantiles (src/risk/level_calibration.py), putting "
            "the AOI at 2.76% against NREL's own coastal-CA station p50 of 2.80% — an "
            "independent reference that was not used to derive the factor. The two "
            "candidate CAUSES tested on 2026-08-12 both failed (worldcover median-fill, "
            "and the PM2.5/PM10 fill which moved p50 the WRONG way 5.53% -> 5.85%), so "
            "this corrects the symptom, not the cause: the cause is the training "
            "geography, and the fix is the PVDAQ per-array model. Caveat that travels "
            "with it: 881 of NREL's 891 rows came from the same class of method PVDAQ "
            "uses, so both inherit its systematic error. Re-measure with "
            "scripts/analyze/aoi_level_check.py.",
            "Recovery is the MEASURED 0.045 (professional) / 0.032 (rinse): the share of "
            "a year's soiling loss one wash recovers on the real-weather SOMOSclean "
            "trajectory for coastal Santa Cruz, corroborated by 0.0634 median over 505 "
            "observed cleans on 149 metered systems. The modelled April-September "
            "planning figure (0.4944 x efficacy = 0.445) is a DRY-SEASON share and is NOT "
            "interchangeable with this one. Neither quantity estimates Persistent Soiling.",
            "ACC export table is SDG&E's standing in for PG&E's.",
        ],
        "beta": True,
    }
    (aoi_dir / "econ_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"[econ] sites worth cleaning: {n_clean} / {len(sites)} "
          f"({summary['pct_sites_worth_cleaning']:.2f}%)   "
          f"max P(net>0) = {summary['max_prob_net_positive']:.4f}")
    print(f"[ok] -> {out_geo}\n     -> {aoi_dir/'site_economics.csv'}"
          f"\n     -> {aoi_dir/'econ_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
