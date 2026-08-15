"""AdvisorOS reads brokerage data and never acts on it.

Aggregators sell trading. SnapTrade exposes order placement; so does Plaid's partner network.
Declining to use that is a product decision, and the difference between a decision and a
comment in a design document is whether something fails when it is violated.

The strong form of the guarantee is *absence*, not restraint. A connector holding a
`place_order` the application merely never calls is one careless call site away from executing
a trade against real money, and no amount of "we don't call that" survives a future contributor
who has a reason to. So the vendor SDK — which ships trading services next to the read ones —
is not a dependency at all: the adapter speaks HTTP to the specific endpoints it needs, and
trading code is not merely unused but not present.

These tests read the connector source directly. That is unusual and deliberate: the property
being asserted is about what the package *contains*, and no runtime assertion can express that.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.connectors import base
from app.connectors.mock import MockPortfolioConnector

CONNECTORS_DIR = Path(base.__file__).parent
SOURCES = sorted(CONNECTORS_DIR.glob("*.py"))

# Anything that would move money or place an order. Matched against identifiers in the parsed
# source rather than raw text, so the prose in these docstrings does not trip it.
TRADING_IDENTIFIERS = frozenset(
    {
        "place_order",
        "place_force_order",
        "preview_order",
        "cancel_order",
        "replace_order",
        "submit_order",
        "place_trade",
        "execute_trade",
        "place_bracket_order",
        "trading",
        "trade_api",
        "TradingApi",
        "OrderApi",
    }
)


def _identifiers(path: Path) -> set[str]:
    """Every name, attribute, function, class, and import alias in a module."""
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            found.add(node.name)
        elif isinstance(node, ast.alias):
            found.add(node.name.split(".")[-1])
            if node.asname:
                found.add(node.asname)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.update(node.module.split("."))
    return found


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_trading_service_is_not_wired(path: Path):
    offending = _identifiers(path) & TRADING_IDENTIFIERS
    assert not offending, (
        f"{path.name} references trading capability: {sorted(offending)}. AdvisorOS is read-only; "
        "brokerage data comes in and nothing goes out."
    )


BANNED_DISTRIBUTIONS = frozenset(
    {"snaptrade", "snaptrade_client", "snaptrade-python-sdk", "alpaca", "alpaca-py", "ib_insync"}
)


def _imported_roots(path: Path) -> set[str]:
    """Top-level package of every absolute import in a module.

    Roots rather than identifiers: `from app.connectors import snaptrade` binds the *name*
    `snaptrade` to our own module, which a name-based check cannot tell apart from the PyPI
    package of the same name. The distinction that matters is where the code comes from, and
    the import root is what carries it.
    """
    tree = ast.parse(path.read_text())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_vendor_sdk_that_bundles_trading_is_imported(path: Path):
    """Keeping trading out of the dependency tree is what makes the guarantee structural.

    The SnapTrade SDK ships order placement beside the read calls. `app/connectors/snaptrade.py`
    speaks HTTP to named endpoints instead, so there is no `place_order` anywhere to be called
    by accident — which is a stronger claim than "we don't call it".
    """
    offending = _imported_roots(path) & BANNED_DISTRIBUTIONS
    assert not offending, f"{path.name} imports a vendor SDK: {sorted(offending)}"


def test_no_vendor_sdk_is_declared_as_a_dependency():
    """The check above only sees today's imports. This one closes the door on adding one."""
    pyproject = (Path(base.__file__).parents[3] / "pyproject.toml").read_text().lower()
    for banned in BANNED_DISTRIBUTIONS:
        assert f'"{banned}' not in pyproject, f"{banned} is declared as a dependency"


def test_the_protocol_exposes_no_write_method():
    """`disconnect` removes a data feed. Nothing else leaves the application."""
    methods = {
        name
        for name in dir(base.PortfolioConnector)
        if not name.startswith("_") and callable(getattr(base.PortfolioConnector, name, None))
    }
    assert methods == {
        "create_connection_session",
        "list_connections",
        "list_accounts",
        "get_positions",
        "get_balances",
        "get_transactions",
        "disconnect",
    }


def test_the_access_mode_is_declared_read_only():
    assert base.BROKERAGE_ACCESS_MODE == "READ_ONLY"
    assert base.READ_ONLY_CONNECTION_TYPE == "read"


async def test_connection_requests_read_only_access():
    """The portal is where trading rights would be granted, so this is the decisive call."""
    session = await MockPortfolioConnector().create_connection_session(
        "6f2a1b3c-0000-4000-8000-000000000001", "https://app.example/return"
    )
    assert session.connection_type == base.READ_ONLY_CONNECTION_TYPE
    assert session.connection_type not in base.FORBIDDEN_CONNECTION_TYPES


def test_the_forbidden_modes_are_named_so_the_prohibition_is_testable():
    assert base.FORBIDDEN_CONNECTION_TYPES == {"trade", "trade-if-available"}
    assert base.READ_ONLY_CONNECTION_TYPE not in base.FORBIDDEN_CONNECTION_TYPES


def test_no_trading_routes_exist():
    from app.main import app

    paths = app.openapi()["paths"]
    forbidden = [
        p for p in paths if any(w in p for w in ("order", "trade", "execute", "buy", "sell"))
    ]
    assert not forbidden, f"trading-shaped routes exist: {forbidden}"
