"""Tests for the cleaning-decision endpoints.

These cover the two things most likely to rot: the *arithmetic identities* that make the
break-even answer meaningful, and the *honesty contract* — that a caller is told when a
number came from a default rather than a measurement, and that no response quietly
implies a capability the project has measured itself not to have.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.risk import economics as E
from solarsoiled.decision import DecisionError, decide, resolve_inputs

try:
    import solarsoiled.api as api_mod
except Exception:  # pragma: no cover - api extra not installed
    api_mod = None

pytestmark = pytest.mark.skipif(api_mod is None, reason="api extra not installed")


@pytest.fixture(scope="module")
def client():
    return TestClient(api_mod.app)


# ---------- the honesty contract ----------------------------------------------------

def test_default_loss_is_flagged_as_not_a_measurement(client):
    """A verdict on BASE_SOILING_PCT must not look like a verdict on this roof."""
    body = client.post("/decision", json={"system_kw": 6.0}).json()
    assert "NOT a measurement of this roof" in body["provenance"]["loss_pct"]


def test_caller_supplied_loss_is_not_flagged(client):
    body = client.post("/decision", json={"system_kw": 6.0, "loss_pct": 4.0}).json()
    assert body["provenance"]["loss_pct"] == "caller"


def test_every_response_carries_its_limitations(client):
    """Quality metadata travels in the payload, not in a doc the caller will not read."""
    body = client.post("/decision", json={"system_kw": 6.0}).json()
    joined = " ".join(body["limitations"]).lower()
    assert "not a ranking" in joined            # the label-unit limit
    assert "recoverable" in joined              # permanent soiling excluded
    assert "min_pro_service" in joined          # the unsourced constant that decides cases
    assert body["unsourced_constants"]


def test_no_risk_score_input_is_accepted(client):
    """RISK_TO_LOSS_PCT was removed as unsound; nothing may reconstruct it."""
    assert "risk_score" not in api_mod.DecisionRequest.model_fields
    body = client.post("/decision", json={"system_kw": 6.0, "risk_score": 0.9}).json()
    # Extra field ignored rather than silently used as a loss proxy.
    assert body["provenance"]["loss_pct"].startswith("BASE_SOILING_PCT")


def test_recovery_band_names_its_denominator(client):
    """The annual bracket (0.031-0.217) and this seasonal band are different quantities."""
    body = client.post("/decision", json={"system_kw": 6.0}).json()
    assert "NOT the annual recovery fraction" in body["recovery_basis"]


# ---------- arithmetic identities ---------------------------------------------------

def test_breakeven_tariff_is_invariant_to_current_rate():
    """Loss is linear in rate and cost is not, so the break-even price cannot move.

    If this fails, either annual_loss_usd stopped being linear in elec_rate or a cost
    function started depending on it — both of which would invalidate the closed form.
    """
    a = resolve_inputs(system_kw=6.0, elec_rate=0.15)
    b = resolve_inputs(system_kw=6.0, elec_rate=0.45)
    ta = decide(a, n_samples=1)["thresholds"]["professional"]
    tb = decide(b, n_samples=1)["thresholds"]["professional"]
    assert ta["breakeven_tariff_usd_per_kwh"] == pytest.approx(
        tb["breakeven_tariff_usd_per_kwh"], rel=1e-6
    )


def test_at_the_breakeven_tariff_the_net_is_about_zero():
    """The threshold has to mean what it says."""
    inp = resolve_inputs(system_kw=30.0, loss_pct=8.0, elec_rate=0.30)
    rate = decide(inp, n_samples=1)["thresholds"]["professional"]["breakeven_tariff_usd_per_kwh"]
    at = resolve_inputs(system_kw=30.0, loss_pct=8.0, elec_rate=rate)
    scen = E.DEFAULT_SCENARIOS["professional"]
    loss = E.annual_loss_usd(at.system_kw, at.sun_hours, at.loss_pct / 100.0, at.elec_rate)
    _, _, net = E.scenario_net(loss, scen, at.system_kw)
    assert net == pytest.approx(0.0, abs=0.5)


def test_bigger_system_never_has_a_worse_verdict():
    """Monotonicity: cost grows sublinearly in kW against a linear benefit."""
    nets = []
    for kw in (3.0, 6.0, 12.0, 25.0, 50.0):
        out = decide(resolve_inputs(system_kw=kw, regime="nem2_legacy"), n_samples=1)
        nets.append(out["best_action_net_usd"])
    assert nets == sorted(nets)


def test_area_and_kw_routes_agree():
    kw = E.system_kw_from_area(40.0)
    a = decide(resolve_inputs(area_m2=40.0), n_samples=1)
    b = decide(resolve_inputs(system_kw=kw), n_samples=1)
    assert a["annual_loss_usd"] == pytest.approx(b["annual_loss_usd"], rel=1e-9)


def test_nem2_is_worth_more_than_nbt():
    """The sharpest targeting signal in the project must survive refactors."""
    legacy = decide(resolve_inputs(system_kw=6.0, regime="nem2_legacy"), n_samples=1)
    nbt = decide(resolve_inputs(system_kw=6.0, regime="nbt_no_battery"), n_samples=1)
    assert legacy["annual_loss_usd"] > 2.0 * nbt["annual_loss_usd"]


def test_the_headline_verdict_still_holds_for_a_typical_roof():
    """A median residential coastal roof does not clear. This is the project's finding."""
    out = decide(resolve_inputs(system_kw=6.0), n_samples=2000)
    assert out["verdict"] == "no_clean"
    assert out["best_action_net_usd"] < 0
    assert out["uncertainty"]["decision_robust"] is True


def test_mc_is_deterministic_under_a_fixed_seed():
    a = decide(resolve_inputs(system_kw=6.0), n_samples=256, seed=7)
    b = decide(resolve_inputs(system_kw=6.0), n_samples=256, seed=7)
    assert a["uncertainty"]["expected_net_usd_p50"] == b["uncertainty"]["expected_net_usd_p50"]


# ---------- input validation --------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    {},
    {"system_kw": 6.0, "area_m2": 30.0},
    {"system_kw": -1.0},
    {"area_m2": 0.0},
    {"system_kw": 6.0, "elec_rate": 0.4, "regime": "nem2_legacy"},
    {"system_kw": 6.0, "regime": "not_a_regime"},
    {"system_kw": 6.0, "loss_pct_p10": 1.0},
    {"system_kw": 6.0, "loss_pct": 2.0, "loss_pct_p10": 3.0, "loss_pct_p90": 4.0},
    {"system_kw": 6.0, "loss_pct": -1.0},
    {"system_kw": 6.0, "sun_hours": 0.0},
])
def test_bad_inputs_raise_decision_error(kwargs):
    with pytest.raises(DecisionError):
        resolve_inputs(**kwargs)


def test_api_turns_decision_errors_into_422(client):
    r = client.post("/decision", json={"system_kw": 6.0, "area_m2": 30.0})
    assert r.status_code == 422
    assert "not both" in r.json()["detail"]


def test_n_samples_is_bounded(client):
    assert client.post("/decision", json={"system_kw": 6.0, "n_samples": 999999}).status_code == 422
    assert client.post("/decision", json={"system_kw": 6.0, "n_samples": 0}).status_code == 422


# ---------- readiness ---------------------------------------------------------------

def test_ready_reports_per_capability(client):
    body = client.get("/health/ready").json()
    assert set(body["capabilities"]) == {"decision", "risk_scoring", "detection"}
    assert body["status"] in {"ok", "partial", "degraded"}


def test_decision_stays_available_without_the_soiling_model(client, monkeypatch):
    """The regression that started this: a missing model file marked the whole API
    degraded, while the endpoint callers actually want needed no weights at all."""
    def boom(_name):
        raise RuntimeError("registry entry points at missing file")

    monkeypatch.setattr(api_mod, "resolve_soiling", boom)
    body = client.get("/health/ready").json()
    assert body["capabilities"]["decision"]["ready"] is True
    assert body["capabilities"]["risk_scoring"]["ready"] is False
    assert body["status"] == "partial"
    assert client.post("/decision", json={"system_kw": 6.0}).status_code == 200


def test_breakeven_returns_only_thresholds(client):
    body = client.get("/breakeven", params={"system_kw": 6.0}).json()
    assert set(body) == {"verdict", "thresholds", "inputs", "provenance", "limitations"}
    for action in ("rinse_service", "professional"):
        assert set(body["thresholds"][action]) == {
            "breakeven_tariff_usd_per_kwh",
            "breakeven_system_kw",
            "breakeven_soiling_pct",
        }
