"""The database must stay optional infrastructure.

These tests exist because the easiest way to break this project is to make storage a boot
dependency. The application is required to start and serve its whole deterministic half with no
credentials and no database; if someone later adds a startup hook that opens the pool, or
imports asyncpg at module scope, these fail.
"""

from __future__ import annotations

import pytest

from app.db import pool


@pytest.fixture(autouse=True)
async def _clean_pool_state(monkeypatch: pytest.MonkeyPatch):
    """`_pool` is module-global, so a test that opens one would leak into the next."""
    monkeypatch.delenv(pool.DATABASE_URL_ENV, raising=False)
    pool._pool = None
    yield
    if pool._pool is not None:
        await pool.close_pool()


def test_is_configured_is_false_without_a_url():
    assert pool.is_configured() is False


def test_is_configured_ignores_whitespace(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(pool.DATABASE_URL_ENV, "   ")
    assert pool.is_configured() is False


async def test_get_pool_raises_storage_unavailable_without_a_url():
    with pytest.raises(pool.StorageUnavailable):
        await pool.get_pool()


async def test_storage_unavailable_message_is_actionable():
    """The message must distinguish 'not configured' from 'your request was bad'."""
    with pytest.raises(pool.StorageUnavailable) as exc:
        await pool.get_pool()
    assert "DATABASE_URL" in str(exc.value)


async def test_pool_creation_does_not_touch_the_network(monkeypatch: pytest.MonkeyPatch):
    """min_size=0 means an unreachable host does not fail until a connection is checked out.

    This is the property that keeps the database off the boot path, so it is worth pinning:
    if someone raises min_size, create_pool() starts dialing and a dead database becomes a
    startup failure again.
    """
    monkeypatch.setenv(pool.DATABASE_URL_ENV, "postgresql://u:p@127.0.0.1:1/nothing")
    created = await pool.get_pool()
    assert created is not None


async def test_connection_failure_does_not_leak_the_dsn(monkeypatch: pytest.MonkeyPatch):
    """A DSN carries a password. The driver's error must never reach the caller.

    The failure surfaces on checkout rather than on pool creation — see the test above.
    """
    secret = "s3cr3t-password"
    monkeypatch.setenv(pool.DATABASE_URL_ENV, f"postgresql://u:{secret}@127.0.0.1:1/none")

    with pytest.raises(pool.StorageUnavailable) as exc:
        await pool.fetchval("select 1")

    assert secret not in str(exc.value)
    assert secret not in repr(exc.value)
    assert secret not in "".join(str(a) for a in exc.value.args)


async def test_close_pool_is_safe_when_never_opened():
    await pool.close_pool()  # must not raise


def test_module_does_not_import_asyncpg_at_import_time():
    """asyncpg is imported inside get_pool(), so importing the app never pays for it."""
    import ast
    import pathlib

    source = pathlib.Path(pool.__file__).read_text()
    tree = ast.parse(source)

    for node in tree.body:  # module level only — nested imports are the point
        if isinstance(node, ast.Import):
            assert all(a.name != "asyncpg" for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module != "asyncpg"


def test_app_imports_and_serves_without_a_database(monkeypatch: pytest.MonkeyPatch):
    """The guarantee this whole module exists to protect."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/api/advisors").status_code == 200
