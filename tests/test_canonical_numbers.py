"""Guards `docs/CANONICAL_NUMBERS.md` against the artifacts it claims to describe.

WHY THIS EXISTS. On 2026-08-31 a cross-doc audit found the same quantity quoted at
different values in different docs, and in several places a number whose reasoning had
moved on while the number had not. The worst case was Stage 2's spatial-CV AUC: ten
documents said 0.728, which is genuinely what `run_optionb/metrics.json` records, while
re-running the same feature set under the current config gives 0.712. Nobody was wrong on
purpose. Nothing checked, so the drift was invisible until someone went looking.

`CANONICAL_NUMBERS.md` fixes that once. This file keeps it fixed, in both directions:

  1. **artifact -> doc.** Each number below is read out of the artifact that produced it
     and asserted. Replace a run without updating the doc and this fails.
  2. **doc -> artifact.** Each number is also asserted to still appear in the doc. Edit
     the doc away from the measurement and this fails too.

A number that is hard to check is a number that will drift, so prefer adding a row here
over adding a sentence to the doc.

NO DATA REQUIRED. Every case skips when its artifact is absent, so this runs green on a
fresh clone and only bites once the data is actually present. That is deliberate: it must
not turn `make test-fast` into something that needs the hand-off bundle.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs/CANONICAL_NUMBERS.md"

# (id, artifact, extractor, expected, how it must appear in the doc)
CASES = [
    # ---- Stage 1: the GA gate ----
    ("stage1_f1", "outputs/eval/rfdetr_w2/gate.json",
     lambda d: round(d["f1"], 4), 0.826, "0.8260"),
    ("stage1_precision", "outputs/eval/rfdetr_w2/gate.json",
     lambda d: round(d["precision"], 4), 0.8499, "0.8499"),
    ("stage1_recall", "outputs/eval/rfdetr_w2/gate.json",
     lambda d: round(d["recall"], 4), 0.8034, "0.8034"),
    ("stage1_n_gt", "outputs/eval/rfdetr_w2/gate.json",
     lambda d: d["n_gt"], 585, "585"),
    ("stage1_conf_star", "outputs/eval/rfdetr_w2/gate.json",
     lambda d: d["conf_star"], 0.5, "0.50"),
    ("stage1_w1_anchor_f1", "outputs/eval/rfdetr_w1_anchor/gate.json",
     lambda d: round(d["f1"], 4), 0.8015, "0.8015"),

    # ---- Stage 2: the three spatial-CV AUCs that kept getting conflated ----
    ("optionb_cv", "runs/soiling/run_optionb/metrics.json",
     lambda d: round(d["mean_auc"], 4), 0.7276, "0.7276"),
    ("abl_full40_cv", "runs/soiling/abl_full40/metrics.json",
     lambda d: round(d["mean_auc"], 4), 0.7118, "0.7118"),
    ("regularized2022_cv", "runs/soiling/run_regularized2022/metrics.json",
     lambda d: round(d["mean_auc"], 4), 0.7459, "0.7459"),

    # ---- Stage 2: the two 2022 holdouts, which belong to different runs ----
    ("optionb_holdout", "runs/soiling/run_optionb/metrics.json",
     lambda d: round(d["holdout_auc"], 4), 0.6789, "0.67886"),
    ("regularized2022_holdout", "runs/soiling/run_regularized2022/metrics.json",
     lambda d: round(d["holdout_auc"], 4), 0.6803, "0.68035"),

    # ---- Stage 2: the live temporal gate ----
    ("pooled_ooy_auc", "runs/soiling/run_optionb/holdout_ci.json",
     lambda d: round(d["pooled_auc"], 4), 0.7095, "0.7095"),
    ("pooled_n", "runs/soiling/run_optionb/holdout_ci.json",
     lambda d: d["pooled_n"], 891, "891"),
    ("pooled_se", "runs/soiling/run_optionb/holdout_ci.json",
     lambda d: round(d["hanley_mcneil_se"], 5), 0.01725, "0.01725"),
    ("calibration_retained", "runs/soiling/run_optionb/holdout_ci.json",
     lambda d: d["calibration_retained"], True, "True"),

    # ---- Stage 2: regional generalization, the limit that must keep being stated ----
    ("regional_production", "outputs/soiling/regional_holdout.json",
     lambda d: round(d["results"]["production (40)"]["pooled_auc"], 4), 0.6774, "0.6774"),

    # ---- Product: polygons vs sites, which are not the same number ----
    ("aoi_polygons", "../BBF-Website/public/tools/arrays_data.manifest.json",
     lambda d: d["n_arrays"], 3362, "3,362"),
    ("aoi_sites", "../BBF-Website/public/tools/arrays_data.manifest.json",
     lambda d: d["n_sites"], 1865, "1,865"),
]


def _load(rel: str):
    p = (REPO / rel).resolve()
    if not p.exists():
        pytest.skip(f"artifact absent (expected on a clone without data): {rel}")
    return json.loads(p.read_text())


@pytest.mark.parametrize("case_id,rel,extract,expected,_doc", CASES,
                         ids=[c[0] for c in CASES])
def test_artifact_still_matches_canonical_doc(case_id, rel, extract, expected, _doc):
    """artifact -> doc: the run on disk still produces the documented number."""
    got = extract(_load(rel))
    assert got == expected, (
        f"{case_id}: {rel} now gives {got!r}, but docs/CANONICAL_NUMBERS.md says {expected!r}.\n"
        "If the run was legitimately replaced, update CANONICAL_NUMBERS.md AND every doc that "
        "quotes it, then update this test. Do not update this test alone."
    )


@pytest.mark.parametrize("case_id,_rel,_extract,_expected,doc_text", CASES,
                         ids=[c[0] for c in CASES])
def test_canonical_doc_still_states_the_number(case_id, _rel, _extract, _expected, doc_text):
    """doc -> artifact: the number has not been edited out of the doc."""
    assert DOC.exists(), f"{DOC} is missing; it is the source of truth for every metric"
    assert doc_text in DOC.read_text(), (
        f"{case_id}: '{doc_text}' no longer appears in docs/CANONICAL_NUMBERS.md. "
        "The doc is the precedence root for every number in this repo; if this value moved, "
        "move it there first."
    )


def test_label_matrix_row_counts():
    """The 891/111/1002 split, miscopied as 891/109/1000 across several docs."""
    p = REPO / "outputs/soiling/training_matrix.parquet"
    if not p.exists():
        pytest.skip("training_matrix.parquet absent (expected on a clone without data)")
    import pandas as pd
    df = pd.read_parquet(p)
    doc = DOC.read_text()
    for label, got, expected, shown in [
        ("total rows", len(df), 1002, "1,002"),
        ("panel rows", int((~df.is_summary).sum()), 891, "891"),
        ("summary rows", int(df.is_summary.sum()), 111, "111"),
        ("distinct stations", df.station_id.nunique(), 257, "257"),
        ("panel stations", df[~df.is_summary].station_id.nunique(), 146, "146"),
    ]:
        assert got == expected, f"{label}: matrix has {got}, doc says {expected}"
        assert shown in doc, f"{label}: '{shown}' missing from CANONICAL_NUMBERS.md"


def test_precedence_rule_is_stated():
    """The rule is the whole point: without it the file is just one more opinion."""
    assert DOC.exists(), f"{DOC} is missing"
    text = DOC.read_text().lower()
    assert "the doc is stale" in text, "CANONICAL_NUMBERS.md must state that it outranks other docs"
    assert "the artifact wins" in text, "CANONICAL_NUMBERS.md must state that artifacts outrank it"
