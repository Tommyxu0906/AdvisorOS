"""The decision layer reaches a user, and keeps reaching one.

Every component here — typed actions, feasibility, house/persona split, provenance, water-filling,
tax ranges, counterfactual validation, sensitivity — was built and tested in isolation, and then
called by nothing. Two hundred and fifty passing tests, zero effect on the product.

These tests exist because unit coverage cannot detect that. They assert the seam: that a plain
HTTP request to the *free* endpoint comes back with computed actions attached, that the derived
judgments the UI needs are serialized rather than left as Python properties, and that the paid
path carries the same object. If someone deletes the `compute_scenario` call, unit tests stay
green and these go red.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

CONCENTRATED = {
    "profile": {
        "age": 34,
        "risk_tolerance": "moderate_aggressive",
    },
    "portfolio": {
        "holdings": [
            {
                "symbol": "NVDA",
                "asset_class": "us_equity",
                "quantity": 400,
                "market_value": 60_000,
                "cost_basis": 20_000,
            },
            {
                "symbol": "VTI",
                "asset_class": "us_equity",
                "quantity": 26,
                "market_value": 10_000,
                "cost_basis": 9_000,
            },
            {
                "symbol": "BND",
                "asset_class": "bonds",
                "quantity": 26,
                "market_value": 10_000,
                "cost_basis": 10_000,
            },
            {
                "symbol": "VXUS",
                "asset_class": "intl_developed_equity",
                "quantity": 26,
                "market_value": 10_000,
            },
            {"symbol": "VNQ", "asset_class": "reit", "quantity": 26, "market_value": 10_000},
        ]
    },
    "question": "Should I sell some NVDA to pay off my credit card?",
}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _scenario(client, payload=None) -> dict:
    response = client.post("/api/committee/select", json=payload or CONCENTRATED)
    assert response.status_code == 200, response.text
    scenario = response.json()["scenario"]
    assert scenario is not None, "the free path returned no computed scenario"
    return scenario


# --- the seam ---------------------------------------------------------------------------------


def test_the_free_endpoint_returns_computed_actions_without_any_api_key(client):
    """The whole point of a deterministic engine: candidate actions cost nothing to compute."""
    actions = _scenario(client)["action_set"]["actions"]

    kinds = {(a["kind"], a["sequence"]) for a in actions}
    assert ("trim_position", 10) in kinds

    trim = next(a for a in actions if a["symbol"] == "NVDA")
    assert trim["shares"] > 0
    assert "20% single-name threshold" in trim["rationale"]


def test_no_api_key_is_present_on_the_request_that_produced_them(client):
    """Belt and braces on the free/paid line: the payload carries no credential at all."""
    assert "anthropic_api_key" not in CONCENTRATED
    assert _scenario(client)["action_set"]["actions"]


def test_house_actions_are_attributed_to_the_house(client):
    """A house rule must never be served under an advisor's name.

    Uses a near-term horizon, because that is the only thing the house acts on now: the old
    trigger was a credit-card balance, which this product no longer asks about.
    """
    payload = {
        "profile": {**CONCENTRATED["profile"], "horizon_years": 1.5},
        "portfolio": CONCENTRATED["portfolio"],
    }
    response = client.post("/api/profiles/analyze", json=payload)
    assert response.status_code == 200

    actions = response.json()["scenario"]["action_set"]["actions"]
    house = [a for a in actions if a["proposed_by"] == "house"]
    assert house, "a near-term need should produce a house action"
    assert all("house rule, not an advisor's view" in a["rationale"] for a in house)


def test_the_scenario_says_the_threshold_is_ours(client):
    """No manifest carries authored policy parameters yet, so every number here is the house's
    and the payload has to say so rather than implying six advisors agreed."""
    scenario = _scenario(client)
    assert scenario["is_house_policy"] is True
    assert scenario["policy_owner"] == "AdvisorOS"


# --- the derived judgements are serialized, not left for the client to guess ---------------------


def test_the_client_is_told_whether_the_plan_survives_its_own_arithmetic(client):
    """`holds_up` is the bar for showing a plan. Re-deriving it in TSX would put the definition
    in two languages and let them drift."""
    counterfactual = _scenario(client)["counterfactual"]

    assert counterfactual["holds_up"] is True
    assert counterfactual["feasible"] is True
    # Nothing *blocking* fired on this book. What the trim resolves is the concentration
    # caution, and it raises cash on the way, which clears the no-cash note too.
    assert "POSITION_CONCENTRATION" in counterfactual["resolved_guardrails"]


def test_before_and_after_numbers_arrive_with_their_direction(client):
    changes = {c["label"]: c for c in _scenario(client)["counterfactual"]["changes"]}

    largest = changes["largest position weight"]
    assert largest["before"] == pytest.approx(0.60)
    assert largest["after"] == pytest.approx(0.20)
    assert largest["improved"] is True

    # Selling does not read as getting richer, and nothing claims a direction it does not have.
    worth = changes["total capital"]
    assert worth["improved"] is None


def test_the_tax_of_acting_arrives_as_a_range(client):
    tax = _scenario(client)["counterfactual"]["estimated_tax"]
    assert tax["low_usd"] < tax["high_usd"]
    assert "holding dates are not collected" in tax["assumption"]


def test_robustness_reaches_the_client_as_a_verdict_and_a_sentence(client):
    sensitivity = _scenario(client)["sensitivity"]

    assert sensitivity["fragile"] is False
    assert sensitivity["flip_at"] == pytest.approx(0.60)
    assert any("reversal" in line for line in sensitivity["summary"])
    assert sensitivity["points"], "the threshold grid should be renderable"


def test_a_fragile_conclusion_is_labelled_as_one(client):
    """A holding barely over the threshold: the answer reverses two points away."""
    payload = {
        **CONCENTRATED,
        "portfolio": {
            "holdings": [
                {"symbol": "AAPL", "quantity": 100, "market_value": 22_000, "cost_basis": 15_000},
                {"symbol": "VTI", "quantity": 26, "market_value": 20_000, "cost_basis": 18_000},
                {"symbol": "BND", "quantity": 26, "market_value": 20_000, "cost_basis": 20_000},
                {"symbol": "VXUS", "quantity": 26, "market_value": 19_000},
                {"symbol": "VNQ", "quantity": 26, "market_value": 19_000},
            ]
        },
    }
    scenario = _scenario(client, payload)

    assert scenario["sensitivity"]["fragile"] is True
    assert "direction is more reliable than the size" in scenario["headline"]


# --- the honest empty states ---------------------------------------------------------------------


def test_a_portfolio_within_every_threshold_produces_no_actions_and_says_why(client):
    payload = {
        **CONCENTRATED,
        "portfolio": {
            "holdings": [
                {"symbol": "VTI", "quantity": 26, "market_value": 20_000},
                {"symbol": "BND", "quantity": 26, "market_value": 20_000},
                {"symbol": "VXUS", "quantity": 26, "market_value": 20_000},
                {"symbol": "VNQ", "quantity": 26, "market_value": 20_000},
                {"symbol": "TIP", "quantity": 26, "market_value": 20_000},
            ]
        },
    }
    scenario = _scenario(client, payload)

    assert scenario["has_actions"] is False
    assert scenario["worth_showing"] is False
    assert "Nothing in this portfolio exceeds" in scenario["headline"]


def test_no_portfolio_still_returns_a_scenario_rather_than_null(client):
    """A profile-only user gets an empty scenario, not a missing key the UI has to special-case."""
    payload = {k: v for k, v in CONCENTRATED.items() if k != "portfolio"}
    scenario = _scenario(client, payload)

    assert scenario["has_actions"] is False
    assert scenario["sensitivity"] is None


# --- the paid path carries the same object ---------------------------------------------------------


def test_the_scenario_is_declared_on_the_paid_response_too():
    """Deterministic actions are not a free-tier consolation prize — the committee argues about
    the same computed object, so it must be on both responses."""
    schema = app.openapi()["components"]["schemas"]["RunCommitteeResponse"]
    assert "scenario" in schema["properties"]

    free = app.openapi()["components"]["schemas"]["SelectCommitteeResponse"]
    assert "scenario" in free["properties"]


def test_the_scenario_sits_beside_the_report_not_inside_it():
    """The architecture's load-bearing line is 'what code decided' versus 'what Claude wrote'.
    Nesting the scenario inside CommitteeReport would blur exactly that."""
    report = app.openapi()["components"]["schemas"]["CommitteeReport"]
    assert "scenario" not in report["properties"]
    assert "action_set" not in report["properties"]
