"""CORS has to keep up with the routes.

This exists because it once did not. `PUT /api/profile` shipped while the CORS policy still
listed only GET and POST, so every browser save died in preflight with "Failed to fetch" — a
message that points at the network, or the backend being down, or the user's session, and never
at the one line of configuration actually responsible. The server logs were silent, because the
request never reached the server.

The assertion below is derived from the app's real route table rather than a list someone has to
remember to update, so adding a route with a new method fails here instead of in a browser.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import CORS_ALLOW_METHODS, app

# Starlette adds these to every route automatically; they are not part of the app's surface.
_IMPLICIT = {"HEAD", "OPTIONS"}


def _routed_methods() -> set[str]:
    """Every HTTP method the app serves, read off the OpenAPI schema.

    Not by walking `app.routes`: this FastAPI version keeps included routers as opaque
    `_IncludedRouter` objects whose children hide behind a private attribute, so a naive walk
    finds only `/api/health` and this file passes while asserting nothing. The generated schema
    is the app's own description of its surface and does not move between versions.
    """
    methods: set[str] = set()
    for operations in app.openapi()["paths"].values():
        methods |= {m.upper() for m in operations}
    return methods - _IMPLICIT


def test_cors_allows_every_method_the_app_routes():
    missing = _routed_methods() - set(CORS_ALLOW_METHODS)
    assert not missing, (
        f"{sorted(missing)} routed but not in CORS_ALLOW_METHODS — browsers will fail preflight"
    )


def test_cors_does_not_allow_methods_the_app_never_serves():
    """The other direction: an allowance nothing uses is a widened surface with no purpose."""
    unused = set(CORS_ALLOW_METHODS) - _routed_methods()
    assert not unused, f"{sorted(unused)} allowed by CORS but not served by any route"


def test_preflight_for_a_profile_save_is_accepted():
    """The exact request the browser makes before PUT /api/profile."""
    client = TestClient(app)
    resp = client.options(
        "/api/profile",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert resp.status_code == 200, resp.text
    assert "PUT" in resp.headers["access-control-allow-methods"]
