"""The paper harness simulates execution and must never reach a venue that costs money.

`tests/security/test_brokerage_read_only.py` guards `app/connectors/` by asserting *absence*:
no order-placing identifier exists there at all, so no call site can reach one. That technique
cannot work here, because submitting orders is what this package is for.

So the guarantee is made on the other axis — not "cannot place an order" but "cannot place one
anywhere real". These tests assert it by reading the source, which is unusual and deliberate:
the property is about what the package *contains*, and no runtime assertion can express that.

The tests exist now, before any real adapter does. That ordering is the point. A guard written
after the thing it guards is a guard written by someone who already knows which cases they
handled, and the first Alpaca adapter should have to satisfy a rule it did not get to write.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.paper import broker as broker_module
from app.paper.broker import LIVE_TRADING_HOSTS, PAPER_ONLY, HarnessMode

PAPER_DIR = Path(broker_module.__file__).parent
SOURCES = sorted(PAPER_DIR.glob("*.py"))

# Vendor SDKs that bundle live trading with everything else. Same reasoning as the connectors
# guard: an adapter should speak HTTP to the one endpoint it needs, so that live trading is not
# merely unused but absent from the dependency tree.
TRADING_SDKS = frozenset({"alpaca", "alpaca_trade_api", "ib_insync", "ibapi", "tradier"})


def test_the_package_declares_itself_simulation_only():
    assert PAPER_ONLY is True


def test_paper_only_is_a_constant_and_not_an_environment_lookup():
    """A switch that can be flipped at runtime is a switch that eventually gets flipped."""
    source = Path(broker_module.__file__).read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if node.attr in {"getenv", "environ"}:
            pytest.fail(
                "app/paper/broker.py reads the environment. PAPER_ONLY must be a constant so "
                "that no deployment can turn simulation off."
            )


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_live_trading_host_appears_anywhere(path: Path):
    """Checked as raw text, not identifiers: a hostname in a comment is still a hostname."""
    text = path.read_text()
    # The declaration of the forbidden list is the one legitimate mention.
    if path.name == "broker.py":
        return
    for host in LIVE_TRADING_HOSTS:
        assert host not in text, (
            f"{path.name} mentions the live trading host {host!r}. Paper adapters must use "
            "their provider's paper host."
        )


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_trading_sdk_is_imported(path: Path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [(node.module or "").split(".")[0]]
        else:
            continue
        for name in names:
            assert name not in TRADING_SDKS, (
                f"{path.name} imports {name!r}, which ships live trading alongside paper. "
                "Speak HTTP to the paper endpoint instead."
            )


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_nothing_in_the_package_opens_a_network_connection(path: Path):
    """v1 is entirely offline. When an adapter is added this test should be narrowed to it,
    not deleted — the mock path must stay network-free so CI never depends on a venue."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [(node.module or "").split(".")[0]]
        else:
            continue
        for name in names:
            assert name not in {"httpx", "requests", "urllib", "socket", "aiohttp"}, (
                f"{path.name} imports {name!r}. The v1 paper harness runs entirely offline."
            )


def test_execution_is_not_the_default_mode():
    """Someone who forgets to pass a mode must not end up submitting orders."""
    import inspect

    from app.paper.harness import run_once

    default = inspect.signature(run_once).parameters["mode"].default
    assert default is HarnessMode.recommend_only
    assert not default.may_execute


def test_the_mode_ladder_is_ordered_and_only_the_top_executes():
    assert not HarnessMode.observe_only.may_decide
    assert HarnessMode.recommend_only.may_decide
    assert HarnessMode.paper_execute.may_decide

    assert not HarnessMode.observe_only.may_execute
    assert not HarnessMode.recommend_only.may_execute
    assert HarnessMode.paper_execute.may_execute


def test_every_fill_and_account_is_marked_simulated():
    """The flag is on the record, so a fill serialized into a log stays identifiable."""
    from app.paper.broker import OrderSide, PaperOrder
    from app.paper.mock_broker import MockPaperBroker

    broker = MockPaperBroker(cash=10_000, prices={"AAPL": 100.0})
    fills, rejected = broker.submit(
        [
            PaperOrder(
                client_order_id="o1",
                symbol="AAPL",
                side=OrderSide.buy,
                quantity=10,
                action_id="a1",
            )
        ]
    )
    assert not rejected
    assert all(f.is_simulated for f in fills)
    assert broker.get_account().is_simulated is True


def test_no_provider_claims_to_be_a_language_model():
    """The naming rule, made structural. `is_language_model` may only be True when one ran."""
    from app.paper.frozen_policy import FrozenPolicyProvider
    from app.paper.mock_policy import MockInvestorPolicy
    from tests.unit.paper_fixtures import sample_portfolio, sample_profile

    profile, portfolio = sample_profile(), sample_portfolio()
    for provider in (MockInvestorPolicy(), FrozenPolicyProvider.from_path()):
        view = provider.decide(profile, portfolio)
        assert view.is_language_model is False, (
            f"{provider.provider_id} reports is_language_model=True but no language model ran. "
            "A deterministic rule table is not inference."
        )
