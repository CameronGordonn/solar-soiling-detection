"""Guard the module temperature coefficient against the bug class that produced it.

Two scripts each hardcoded `GAMMA_PDC` -- `pvdaq_daily_srr_probe.py` at -0.0045,
`washable_share_probe.py` at -0.0035 -- and a comment in the first asserted the two
matched. Nothing threw, nothing logged, the suite passed, and the discrepancy was
worth 1.045 pts of annual soiling label on system 10109, against a method-noise SD
of 0.72 pts. It was found by a sensitivity sweep, not by review.

So the guard here is structural, not a value check: no module outside
`src/risk/module_gamma.py` may define its own gamma literal. A future author who
adds one gets a failing test with the history in it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.risk.module_gamma import (
    FLEET_DEFAULT_GAMMA, TECH_MAP, resolve_gamma,
)

REPO = Path(__file__).resolve().parents[1]
#: The single place a gamma constant is allowed to be written down.
OWNER = REPO / "src" / "risk" / "module_gamma.py"
#: Names that mean "module temperature coefficient of Pmax".
GAMMA_NAMES = {"GAMMA_PDC", "GAMMA", "FLEET_DEFAULT_GAMMA", "gamma_pdc_default"}


def _python_files() -> list[Path]:
    out = []
    for sub in ("src", "scripts"):
        out += [p for p in (REPO / sub).rglob("*.py") if p.resolve() != OWNER.resolve()]
    return out


def test_no_module_defines_its_own_gamma_literal():
    """A gamma constant may be imported or resolved, never re-typed."""
    offenders = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if not (names & GAMMA_NAMES):
                continue
            # A bare numeric literal is the bug. A call (resolver) or a Name
            # (re-export of the import) is fine.
            v = node.value
            if isinstance(v, ast.Constant) and isinstance(v.value, (int, float)):
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno} "
                                 f"{sorted(names)} = {v.value}")
            elif isinstance(v, ast.UnaryOp) and isinstance(v.operand, ast.Constant):
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno} "
                                 f"{sorted(names)} = -{v.operand.value}")
    assert not offenders, (
        "gamma must come from src/risk/module_gamma.py, not a fresh literal.\n"
        "Two files once disagreed by 0.0010, worth 1.045 pts of soiling label:\n  "
        + "\n  ".join(offenders)
    )


def test_the_two_pvdaq_probes_agree():
    """The exact drift that happened. Both must read the same definition."""
    from scripts.analyze import pvdaq_daily_srr_probe, washable_share_probe

    assert pvdaq_daily_srr_probe.GAMMA_PDC == washable_share_probe.GAMMA_PDC, (
        "the two PVDAQ probes disagree about gamma again"
    )


def test_fleet_default_is_the_cec_xsi_median():
    """The labelled fallback is sourced, not picked. Both x-Si classes sit on -0.45."""
    assert FLEET_DEFAULT_GAMMA == pytest.approx(-0.0045)


@pytest.mark.parametrize("man,model,tech,expect_tier", [
    ("Trina TSM-245PA05", "Trina TSM-245PA05", "multi-Si", "cec_module"),
    ("LG", "LG", "mono-Si", "cec_manufacturer"),
    ("Unknown", "Unknown", "multi-Si", "cec_technology"),
    ("Unknown", "Unknown", "", "fleet_default"),
])
def test_resolution_ladder_degrades_in_order(man, model, tech, expect_tier):
    r = resolve_gamma(man, model, tech)
    assert r.tier == expect_tier
    # Every tier must return a physically plausible x-Si/thin-film coefficient.
    assert -0.007 < r.gamma_pdc < -0.002


def test_every_pvdaq_technology_string_is_mapped():
    """The `type` values PVDAQ actually publishes for the residential fleet. A new
    one appearing silently drops a system to the fleet default, which is exactly
    the kind of quiet degradation this repo keeps getting bitten by."""
    seen_in_fleet = {
        "mono-si", "multi-si", "n-pert", "mono-si ibc", "n-shj", "perc",
        "n-pert ibc", "sjt", "amorphous si", "n-topcon",
        "ribbon polycrystalline si", "cylindrical cigs", "perc bifacial",
        "perc ibc", "cis family thin-film",
    }
    assert seen_in_fleet <= set(TECH_MAP), (
        f"unmapped PVDAQ technology strings: {sorted(seen_in_fleet - set(TECH_MAP))}"
    )


# ── the same bug class, other constants ──────────────────────────────────────
def test_soiling_thresholds_have_exactly_one_home():
    """`heavy_rain_mm` was written out four times across the tree, each with a comment
    asserting it matched the others. That is the gamma failure verbatim. They now all
    read `recovery.DEFAULT_PARAMS`; this fails if anyone types the literal again."""
    from src.risk import band_soiling, recovery
    from scripts.analyze import band_rain_regime
    from scripts.predict import calibrate_somosclean

    owner = recovery.DEFAULT_PARAMS
    assert band_soiling.HEAVY_RAIN_MM == owner["heavy_rain_mm"]
    assert band_rain_regime.HEAVY_MM == owner["heavy_rain_mm"]
    assert calibrate_somosclean.HEAVY_RAIN_MM == owner["heavy_rain_mm"]
    assert calibrate_somosclean.RAIN_MIN_MM == owner["rain_min_mm"]
    assert calibrate_somosclean.PM10_DUST_THRESHOLD == owner["pm10_dust_threshold"]
    assert calibrate_somosclean.PM10_DUST_SCALE == owner["pm10_dust_scale"]
    assert band_rain_regime.TRACE_MM == band_soiling.TRACE_MM
