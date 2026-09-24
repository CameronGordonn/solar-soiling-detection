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
from solarsoiled.decision import (
    DecisionError,
    decide,
    resolve_inputs,
    scenarios_for,
)

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


def test_recovery_basis_defaults_to_the_measured_pair(client):
    """The optimistic modelled pair must never be the silent default.

    This is the field most able to move the verdict, and the repo carries two values for
    it that differ tenfold. Defaulting to the modelled one would make the API contradict
    the project's own measurement.
    """
    body = client.post("/decision", json={"system_kw": 6.0}).json()
    assert body["inputs"]["recovery_basis"] == "measured"
    assert body["recovery_basis"].startswith("measured:")
    assert scenarios_for("measured")["professional"]["recovery_frac"] == 0.045


def test_seasonal_basis_is_labelled_as_optimistic(client):
    body = client.post(
        "/decision", json={"system_kw": 6.0, "recovery_basis": "seasonal_planning"}
    ).json()
    assert "MODELLED" in body["recovery_basis"]
    assert "10x" in body["recovery_basis"]
    assert "MODELLED" in body["provenance"]["recovery_basis"]


def test_the_two_bases_differ_about_tenfold():
    m = scenarios_for("measured")["professional"]["recovery_frac"]
    s = scenarios_for("seasonal_planning")["professional"]["recovery_frac"]
    assert 8.0 < (s / m) < 12.0


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
    scen = scenarios_for(at.recovery_basis)["professional"]
    loss = E.annual_loss_usd(at.system_kw, at.sun_hours, at.loss_pct / 100.0, at.elec_rate)
    _, _, net = E.scenario_net(loss, scen, at.system_kw)
    assert net == pytest.approx(0.0, abs=0.5)


def _net(kw, **kw2):
    return decide(resolve_inputs(system_kw=kw, regime="nem2_legacy", **kw2),
                  n_samples=1)["best_action_net_usd"]


def test_net_improves_only_while_the_service_floor_binds():
    """The real shape, which is not monotone and was asserted to be.

    Below roughly 12 kW the bill is pinned at the $90/$150 one-truck-roll floor, so a
    bigger array recovers more for the same price and the net improves. Above it the
    per-panel schedule takes over and cost grows faster than a 4.5% recovery can, so the
    net degrades again. An earlier version of this test asserted monotonicity, which held
    only under the optimistic modelled recovery.
    """
    rising = [_net(kw) for kw in (2.0, 4.0, 8.0, 12.0)]
    assert rising == sorted(rising)
    falling = [_net(kw) for kw in (15.0, 20.0, 30.0, 50.0)]
    assert falling == sorted(falling, reverse=True)


def test_under_the_measured_basis_cleaning_never_pays_at_any_size():
    """The project's finding, pinned. Even at the most favourable tariff regime."""
    assert all(_net(kw) < 0 for kw in (2.0, 6.0, 12.0, 20.0, 50.0, 100.0))


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


# ---------- agreement with the paper ------------------------------------------------

def test_the_api_agrees_with_the_paper_on_the_papers_own_median_system():
    """The regression this module was rewritten to prevent.

    `paper/paper.tex` reports that on 149 metered California rooftops the median system
    needs $2.44/kWh to break even and that no action pays. Its median system is 5.72 kW
    at 9.53% annual loss and 5.21 peak sun hours, valued at the NEM 2.0 retail rate.

    Fed that system, the default (measured) basis returns no_clean and a break-even
    tariff far above any real tariff, which is the paper's conclusion. The
    seasonal_planning basis returns "clean it" on the same inputs, because its recovery
    fraction is ten times larger. That disagreement is the reason `measured` is the
    default and the reason the basis is named in every response.
    """
    paper = dict(system_kw=5.72, loss_pct=9.5285, sun_hours=5.2106, elec_rate=0.4573)

    measured = decide(resolve_inputs(**paper), n_samples=1)
    assert measured["verdict"] == "no_clean"
    assert measured["best_action_net_usd"] < 0
    assert measured["thresholds"]["professional"]["breakeven_tariff_usd_per_kwh"] > 2.0

    optimistic = decide(
        resolve_inputs(**paper, recovery_basis="seasonal_planning"), n_samples=1
    )
    assert optimistic["verdict"] != "no_clean"  # documents the hazard, not an endorsement


def test_measured_recovery_matches_the_live_site_calculator():
    """BBF-Website/public/tools/breakeven.html ships CLEAN.professional.recovery = 0.045
    and CLEAN.lightpro.recovery = 0.032. A reimplementation that silently drifts from the
    library is a bug class this project has already hit, so pin the pair here."""
    scens = scenarios_for("measured")
    assert scens["professional"]["recovery_frac"] == 0.045
    assert scens["rinse_service"]["recovery_frac"] == 0.032


def test_api_reproduces_the_papers_headline_breakeven_exactly():
    """Fed the paper's own inputs, the API must return the paper's own number.

    This is the strongest available statement that the decision chain and
    `paper/paper.tex` implement the same arithmetic: across the paper's 149 metered
    California systems, each with its own measured recovery fraction, the median
    professional break-even tariff comes out at $2.4410/kWh, which is the figure the
    paper reports. Any structural divergence -- a different derate, cost curve, or
    loss model -- would move it.

    Skips without the audit artifacts, which are gitignored (see CANONICAL_NUMBERS.md).
    """
    pd = pytest.importorskip("pandas")
    path = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "outputs/soiling/audit/real_systems_economics.parquet"
    )
    if not path.exists():
        pytest.skip("audit artifact absent (expected on a clone without data)")

    systems = pd.read_parquet(path)
    assert len(systems) == 149

    breakevens = []
    for _, row in systems.iterrows():
        out = decide(
            resolve_inputs(
                system_kw=float(row.capacity_kw),
                loss_pct=float(row.annual_loss_pct),
                sun_hours=float(row.sun_hours),
                elec_rate=0.4573,                       # NEM 2.0 retail
                recovery_frac=float(row.recovery_frac),  # this system's measured value
            ),
            n_samples=1,
        )
        breakevens.append(out["thresholds"]["professional"]["breakeven_tariff_usd_per_kwh"])

    assert pd.Series(breakevens).median() == pytest.approx(2.4410, abs=5e-4)


def test_the_export_rate_verdict_reproduces_and_retail_is_not_hidden():
    """The paper's headline verdict, plus the nuance the headline does not carry.

    The paper reports 0 of 149 clearing **at the marginal export rate**, and that
    reproduces exactly. At full NEM 2.0 retail -- 11.7x the export rate -- 3 systems
    (2%) do clear. That is not a contradiction: it is the tariff-vintage effect this
    project already identifies as its sharpest targeting signal, and it is asserted here
    so nobody later "discovers" it as a regression and rounds it back down to zero.
    """
    pd = pytest.importorskip("pandas")
    path = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "outputs/soiling/audit/real_systems_economics.parquet"
    )
    if not path.exists():
        pytest.skip("audit artifact absent (expected on a clone without data)")

    systems = pd.read_parquet(path)

    def cleared_at(rate):
        return sum(
            decide(
                resolve_inputs(
                    system_kw=float(r.capacity_kw), loss_pct=float(r.annual_loss_pct),
                    sun_hours=float(r.sun_hours), elec_rate=rate,
                    recovery_frac=float(r.recovery_frac),
                ),
                n_samples=1,
            )["verdict"] != "no_clean"
            for _, r in systems.iterrows()
        )

    assert cleared_at(0.0392) == 0        # marginal export — the paper's figure
    assert cleared_at(0.4573) <= 5        # NEM 2.0 retail — a handful, currently 3
