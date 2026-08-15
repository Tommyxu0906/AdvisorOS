"""FastAPI application entry point.

The app starts and serves every deterministic endpoint with no Anthropic credentials present
anywhere in the environment. That is a hard requirement, not an incidental property — see
`tests/security/test_no_developer_fallback.py`.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.advisors.registry import get_registry
from app.api.routes import analysis, auth, committee, profile, runs
from app.core.redaction import install_redaction, redact_text
from app.llm.pricing import load_pricing

logging.basicConfig(
    level=os.environ.get("AIFA_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
install_redaction()

app = FastAPI(
    title="AdvisorOS",
    version="0.1.0",
    description=(
        "A BYOK multi-agent financial decision system. Deterministic analytics and advisor "
        "routing run for free; LLM reasoning runs on the user's own Anthropic API key."
    ),
)

_origins = os.environ.get("AIFA_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")

# Must cover every method the routers actually serve. A method missing here does not fail on the
# server — the browser refuses the preflight and the caller sees an opaque "Failed to fetch",
# which looks like the backend being down rather than a config gap. Held as a module constant so
# tests/unit/test_cors.py can assert it against the real route table.
CORS_ALLOW_METHODS = ["GET", "POST", "PUT"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins.split(",") if o.strip()],
    allow_credentials=False,
    allow_methods=CORS_ALLOW_METHODS,
    # Authorization carries the Supabase session JWT, not the Anthropic key — that stays in the
    # request body (see CredentialedRequest). allow_credentials stays False: the JWT is a bearer
    # header, not a cookie, so no cross-site cookie exposure is introduced by adding it here.
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(analysis.router, prefix="/api")
app.include_router(committee.router, prefix="/api")
app.include_router(profile.router, prefix="/api")
app.include_router(runs.router, prefix="/api")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last line of defense: never let an unhandled traceback carry a key to the client."""
    logging.getLogger(__name__).exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "code": "internal_error",
            "message": redact_text(f"An internal error occurred ({type(exc).__name__})."),
        },
    )


@app.get("/api/health")
def health() -> dict:
    """Deliberately touches neither the database nor the network — see app/db/pool.py and
    app/core/supabase_auth.py. Both are read here only as `bool(env var)`, so this route stays
    truthful about "not configured" even when nothing downstream can be reached at all."""
    pricing = load_pricing()
    return {
        "status": "ok",
        "advisors": len(get_registry()),
        "pricing_version": pricing.pricing_version,
        "models_priced": sorted(pricing.models),
        # True when the server can run without any project-owned key — the BYOK invariant.
        "byok_only": os.environ.get("AIFA_ALLOW_DEV_KEY") != "1",
        "storage_configured": bool(os.environ.get("DATABASE_URL", "").strip()),
        "accounts_configured": bool(os.environ.get("SUPABASE_URL", "").strip()),
    }


@app.get("/api/capabilities")
def capabilities() -> dict:
    """What works without a key, and what needs one. Drives the frontend's gating."""
    return {
        "free": [
            "POST /api/profiles/analyze",
            "POST /api/portfolio/analyze",
            "GET  /api/advisors",
            "GET  /api/market/quotes",
            "POST /api/committee/select",
            "POST /api/committee/estimate",
        ],
        "requires_api_key": [
            "POST /api/auth/anthropic/validate",
            "POST /api/committee/analyze",
            "POST /api/advisors/distill",
        ],
        "requires_account": [
            "GET /api/runs",
            "GET /api/runs/{run_id}",
            "GET /api/profile",
            "PUT /api/profile",
        ],
        "key_storage": "none — held in browser memory and request memory only",
        "account_note": (
            "Accounts are independent of the Anthropic key: signing in unlocks saved run "
            "history, never inference. An anonymous caller can run POST /api/committee/analyze "
            "exactly as a signed-in one can — it just isn't saved anywhere afterward."
        ),
    }
