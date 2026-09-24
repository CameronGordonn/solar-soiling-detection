"""The site's JavaScript and this library are two implementations of one model.

`BBF-Website/public/tools/breakeven.html` reimplements the cleaning economics client-side
so the dashboard needs no backend. That was a deliberate, good decision -- it removed a
single point of failure -- but it created a second copy of every constant, and nothing
enforced that the copies agree.

They diverged exactly once, and it mattered. On 2026-09-04 `DEFAULT_RECOVERY_PRO` moved
from the measured 0.045 to a modelled 0.445 in Python while the site kept 0.045. For three
weeks the site and the library disagreed tenfold about the number that decides whether
cleaning pays, and nothing failed. See `docs/RECOVERY_RECONCILIATION_PLAN.md`.

These tests parse the constants straight out of the shipped HTML and assert them against
the Python source of truth. They **skip** when the site repo is not checked out beside
this one, so a clone without it still runs green.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from src.risk import economics as E

TOOLS = pathlib.Path(__file__).resolve().parents[2] / "BBF-Website/public/tools"
SITE = TOOLS / "breakeven.html"
DASHBOARD = TOOLS / "dashboard.js"


@pytest.fixture(scope="module")
def html() -> str:
    if not SITE.exists():
        pytest.skip(f"site repo not checked out beside this one: {SITE}")
    return SITE.read_text()


def _number(html_text: str, pattern: str) -> float:
    """Pull one numeric literal out of the page, failing loudly if the shape changed."""
    match = re.search(pattern, html_text)
    if not match:
        pytest.fail(
            f"could not find {pattern!r} in breakeven.html. The page was restructured; "
            "update this test rather than deleting it -- it is the only thing keeping "
            "the site and the library in agreement."
        )
    return float(match.group(1))


# ---------- the constant that actually drifted --------------------------------------

def test_recovery_fractions_match(html):
    """The 2026-09-04 divergence, pinned so it cannot recur silently."""
    pro = _number(html, r"professional:\s*\{[^}]*?recovery:\s*([0-9.]+)")
    rinse = _number(html, r"lightpro:\s*\{[^}]*?recovery:\s*([0-9.]+)")
    assert pro == pytest.approx(E.DEFAULT_RECOVERY_PRO, abs=1e-6), (
        f"site ships recovery {pro} but economics.DEFAULT_RECOVERY_PRO is "
        f"{E.DEFAULT_RECOVERY_PRO}. These decide whether cleaning pays."
    )
    assert rinse == pytest.approx(E.DEFAULT_RECOVERY_RINSE, abs=1e-6)


# ---------- the rest of the shared chain --------------------------------------------

def test_service_cost_floors_and_panel_size_match(html):
    assert _number(html, r"const PANEL_KW\s*=\s*([0-9.]+)") == pytest.approx(E.PANEL_KW)
    assert _number(html, r"MIN_PRO\s*=\s*([0-9.]+)") == pytest.approx(E.MIN_PRO_SERVICE)
    assert _number(html, r"MIN_RINSE\s*=\s*([0-9.]+)") == pytest.approx(E.MIN_RINSE_SERVICE)
    assert _number(html, r"RINSE_FACTOR\s*=\s*([0-9.]+)") == pytest.approx(E.RINSE_COST_FACTOR)


def test_system_derate_matches(html):
    assert _number(html, r"const SYSTEM_DERATE\s*=\s*([0-9.]+)") == pytest.approx(E.SYSTEM_DERATE)


def test_per_panel_rate_ladder_matches(html):
    """Both rungs and prices. A ladder that drifts silently mis-prices commercial roofs."""
    def _array(name: str) -> list[float]:
        match = re.search(rf"const {name}\s*=\s*\[([^\]]+)\]", html)
        if not match:
            pytest.fail(f"could not find {name} in breakeven.html")
        return [float(x) for x in match.group(1).split(",")]

    assert _array("RATE_KW") == list(E._RATE_KW)
    assert _array("RATE_USD") == list(E._RATE_USD)


def test_cleaning_efficacies_match(html):
    """`removes` on the site is the same quantity as *_CLEAN_EFFICACY here."""
    pro = _number(html, r"professional:\s*\{[^}]*?removes:\s*([0-9.]+)")
    rinse = _number(html, r"lightpro:\s*\{[^}]*?removes:\s*([0-9.]+)")
    assert pro == pytest.approx(E.PROFESSIONAL_CLEAN_EFFICACY, abs=1e-6)
    assert rinse == pytest.approx(E.RINSE_CLEAN_EFFICACY, abs=1e-6)


# ---------- the second JS copy ------------------------------------------------------
#
# `dashboard.js` carries its OWN recovery constants, independent of breakeven.html. Two
# reimplementations of one model is bad enough; three is how a number quietly diverges in
# one place and not the others. Both are asserted here.


@pytest.fixture(scope="module")
def dashboard_js() -> str:
    if not DASHBOARD.exists():
        pytest.skip(f"site repo not checked out beside this one: {DASHBOARD}")
    return DASHBOARD.read_text()


def test_dashboard_js_recovery_matches(dashboard_js):
    pro = _number(dashboard_js, r"const RECOVERY_PRO\s*=\s*([0-9.]+)")
    basic = _number(dashboard_js, r"const RECOVERY_BASIC\s*=\s*([0-9.]+)")
    assert pro == pytest.approx(E.DEFAULT_RECOVERY_PRO, abs=1e-6), (
        f"dashboard.js ships RECOVERY_PRO {pro} but economics.DEFAULT_RECOVERY_PRO is "
        f"{E.DEFAULT_RECOVERY_PRO}."
    )
    assert basic == pytest.approx(E.DEFAULT_RECOVERY_RINSE, abs=1e-6)


def test_the_two_js_copies_agree_with_each_other(html, dashboard_js):
    """Even if both drifted from Python together, they must not disagree between themselves."""
    assert _number(html, r"professional:\s*\{[^}]*?recovery:\s*([0-9.]+)") == pytest.approx(
        _number(dashboard_js, r"const RECOVERY_PRO\s*=\s*([0-9.]+)"), abs=1e-6
    )
    assert _number(html, r"lightpro:\s*\{[^}]*?recovery:\s*([0-9.]+)") == pytest.approx(
        _number(dashboard_js, r"const RECOVERY_BASIC\s*=\s*([0-9.]+)"), abs=1e-6
    )
