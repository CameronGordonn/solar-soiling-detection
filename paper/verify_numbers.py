#!/usr/bin/env python3
# Copyright 2026 Cameron Gordon
# SPDX-License-Identifier: Apache-2.0
"""Check that every headline number in paper.tex still matches the artifact that produced it.

This repository has been bitten repeatedly by numbers whose provenance nobody recorded,
and this paper changed most of its headline figures during one audit. A claim that the
work is reproducible should be checkable in one command rather than asserted.

Two checks, because one is not enough.

**Present-and-correct.** Each entry in CHECKS names a claim, reads the value from the JSON
that produced it, and asserts the rounded form appears somewhere in paper.tex. This catches
a number that moved and was not propagated.

**Absent-and-retired.** RETIRED lists values this paper used to make and no longer stands
behind, with the reason. The first check is structurally blind to these: a superseded number
sitting in a figure caption or a conclusion still "appears in the manuscript", so nothing
flags it. Every round of review on this draft found at least one, which is the argument for
having it. Entries carry an `unless` string when the retired value may legitimately appear
in a sentence that disowns it.

Usage:
    python paper/verify_numbers.py          # exits non-zero if anything is stale
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
A = ROOT / "outputs/soiling/audit"
TEX = ROOT / "paper/paper.tex"


def load(name):
    return json.loads((A / name).read_text())


#: value -> (why it is retired, a phrase that legitimately mentions it or None)
RETIRED = {
    "0.065": ("a single 'measured recovery fraction'; it is denominator-dependent "
              "(0.031/0.063/0.217)", None),
    "0.216": ("superseded by 0.217 and no longer presented as an alternative answer", None),
    "29.22": ("mislabelled 'annual recoverable value'; the quantity is $28.10 per wash",
              "annual recoverable value"),
    "10.54": ("same mislabel at the export rate; the quantity is $10.14 per wash", None),
    "18 of 18": ("the verifier's own count moved", None),
    "34 honest": ("implies the 40-feature set was dishonest; say '34 in production'", None),
    "inflates tilt": ("web-Mercator flattens tilt, it does not inflate it", None),
}


NEAR = 400   # characters; roughly a paragraph either side


def check_retired(tex):
    """Flag retired values, allowing one that sits beside the text disowning it.

    The `unless` phrase has to appear WITHIN NEAR characters of the value, not merely
    somewhere in the file. A file-wide test is useless here: the disowning sentence is
    itself permanent, so it would excuse the value reappearing anywhere afterwards.
    """
    bad = []
    for val, (why, unless) in RETIRED.items():
        start = 0
        while (i := tex.find(val, start)) != -1:
            start = i + len(val)
            if unless:
                window = tex[max(0, i - NEAR): i + NEAR]
                if unless in window:
                    continue
            bad.append((val, why, i))
            break
    return bad


def main() -> int:
    tex = TEX.read_text()
    val, rec = load("validation_audit.json"), load("reconciliation.json")
    reg, emp = load("regional_holdout_audit.json"), load("recovery_empirical.json")
    eco, rank = load("real_systems_economics.json"), load("per_system_ranking.json")
    rob, phys = load("robustness.json"), load("physics_baselines.json")
    ts, vpc = load("trainsize_control.json"), load("value_per_clean.json")
    lv = load("label_variants.json")

    J = "joint_station_and_year_out"
    checks = [
        ("joint fold, 40 feats, panel-only pooled",
         rec["results"][f"{J}|full_40"]["pooled_panel_only"], 3),
        ("joint fold, lat/lon only",
         rec["results"][f"{J}|latlon_only"]["pooled_panel_only"], 3),
        ("joint fold CI lower", rec["bootstrap"][f"{J}|full_40"]["ci95"][0], 3),
        ("joint fold CI upper", rec["bootstrap"][f"{J}|full_40"]["ci95"][1], 2),
        ("station leak in leave-year-out",
         val["results"]["leave_year_out|xgb|full_40"]["station_leak_pct"], 1),
        ("regional, year axis + production params",
         reg["results"]["region_year|production"]["pooled_auc"], 3),
        ("regional, largest region",
         reg["results"]["region_year|production"]["per_region"]["0"]["auc"], 3),
        # The measured, assumption-free quantities. recovery_frac is deliberately NOT
        # checked as a headline: it is a ratio against a denominator that moves eightfold,
        # and the paper reports it only as a bracket.
        ("value per clean, retail", vpc["usd_per_clean_retail_median"], 2),
        ("value per clean, export", vpc["usd_per_clean_export_median"], 2),
        ("recovered point-days", vpc["point_days_median"], 0),
        ("recovery bracket, perfect_clean",
         vpc["recovery_frac_by_denominator"]["perfect_clean"]["median"], 3),
        ("recovery bracket, half_norm",
         vpc["recovery_frac_by_denominator"]["half_norm_clean"]["median"], 3),
        ("recovery bracket, random_clean",
         vpc["recovery_frac_by_denominator"]["random_clean"]["median"], 3),
        ("label level, perfect_clean", lv["levels"]["perfect_clean"]["median"], 2),
        ("label level, half_norm", lv["levels"]["half_norm_clean"]["median"], 2),
        ("label level, random_clean", lv["levels"]["random_clean"]["median"], 2),
        ("label rho, perfect vs half_norm",
         lv["spearman"]["perfect_clean|half_norm_clean"], 3),
        ("label rho, half_norm vs random",
         lv["spearman"]["half_norm_clean|random_clean"], 3),
        ("break-even tariff, median system",
         eco["breakeven_rate_quantiles"]["p50"], 2),
        ("within-cell concordance, per-system",
         rank["results"]["per-system only"]["concordance"], 3),
        ("within-cell concordance, location only",
         rank["results"]["location only (station-model analogue)"]["concordance"], 3),
        ("joint fold, 7-seed mean", rob["full_40|xgb"]["mean"], 3),
        ("joint fold, 7-seed sd", rob["full_40|xgb"]["sd"], 4),
        ("joint fold, logistic regression", rob["full_40|logreg"]["mean"], 3),
        ("Kimber baseline, unfitted", phys["Kimber, instantaneous"]["auc"], 3),
        ("SOMOSclean baseline, unfitted", phys["SOMOSclean, instantaneous"]["auc"], 3),
        ("size-matched control", ts["control"]["mean"], 4),
        ("leave-one-year-out reference", ts["loyo"]["mean"], 4),
        ("leak share CI lower", 100 * ts["decomposition"]["leak_share_ci95"][0], 0),
        ("leak share CI upper", 100 * ts["decomposition"]["leak_share_ci95"][1], 0),
    ]

    stale = []
    for name, v, nd in checks:
        want = f"{round(float(v), nd):.{nd}f}".rstrip("0").rstrip(".")
        ok = want in tex
        print(f"  {'ok ' if ok else 'STALE'}  {name:44s} {want}")
        if not ok:
            stale.append((name, want))

    retired = check_retired(tex)
    if retired:
        print("\nRETIRED values still present in paper.tex:")
        for v, why, pos in retired:
            line = tex[:pos].count("\n") + 1
            print(f"  line {line}: {v!r} -- {why}")
    else:
        print(f"\nno retired values present ({len(RETIRED)} checked).")

    print(f"{len(checks) - len(stale)}/{len(checks)} headline numbers match their artifact.")
    if retired:
        return 1
    if stale:
        print("\nSTALE, present in an artifact but not in paper.tex:")
        for n, w in stale:
            print(f"  {n}: expected {w}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
