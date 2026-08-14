"""Identity is optional infrastructure, same shape of guarantee as app/db/pool.py.

The Anthropic key and the Supabase session are different credential types with different blast
radii — see app/core/supabase_auth.py's module docstring. These tests only cover the identity
half; tests/security/test_api_key_handling.py covers the key.
"""

from __future__ import annotations

import pytest

from app.core import supabase_auth as auth


@pytest.fixture(autouse=True)
def _no_supabase_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(auth.SUPABASE_URL_ENV, raising=False)
    auth.reset_jwks_cache_for_tests()
    yield
    auth.reset_jwks_cache_for_tests()


def test_is_configured_is_false_without_a_url():
    assert auth.is_configured() is False


async def test_current_user_optional_is_none_with_no_header():
    assert await auth.current_user_optional(authorization=None) is None


async def test_current_user_optional_is_none_without_bearer_prefix():
    assert await auth.current_user_optional(authorization="Token abc123") is None


async def test_current_user_optional_is_none_when_unconfigured_even_with_a_token():
    """A present token cannot make the app try to reach a Supabase project that was never
    configured — this is what keeps identity off the boot path."""
    assert await auth.current_user_optional(authorization="Bearer sometoken") is None


async def test_current_user_optional_degrades_on_an_invalid_token_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv(auth.SUPABASE_URL_ENV, "https://nonexistent-project.supabase.co")
    # No network reachable at that host in the test environment: this exercises the same
    # "can't verify" path a malformed or expired token would take, and it must not raise —
    # an optional dependency degrades to anonymous, it never 401s the caller.
    result = await auth.current_user_optional(authorization="Bearer not-a-real-jwt")
    assert result is None


async def test_current_user_required_raises_401_with_no_header(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(auth.SUPABASE_URL_ENV, "https://nonexistent-project.supabase.co")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await auth.current_user_required(authorization=None)
    assert exc.value.status_code == 401


async def test_current_user_required_raises_503_when_unconfigured():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await auth.current_user_required(authorization="Bearer sometoken")
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "auth_unavailable"


def test_module_does_not_import_jwt_at_import_time():
    """jwt is imported inside functions, so importing this module never pays for it — and
    never makes a network request for JWKS just by being on the import graph."""
    import ast
    import pathlib

    source = pathlib.Path(auth.__file__).read_text()
    tree = ast.parse(source)

    for node in tree.body:
        if isinstance(node, ast.Import):
            assert all(a.name != "jwt" for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module != "jwt"


def test_app_boots_and_serves_with_no_supabase_url_configured():
    """The property this whole module exists to protect, exercised at the app level."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        health = client.get("/api/health").json()
        assert health["accounts_configured"] is False
        assert client.get("/api/advisors").status_code == 200
        # Run history requires an account, but the route itself must still resolve (503, not a
        # crash) when accounts aren't configured at all.
        resp = client.get("/api/runs")
        assert resp.status_code in (401, 503)
