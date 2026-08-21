"""Saving and reading consultations.

One row per conversation, upserted after every completed round rather than written once at the
end. A conversation has no natural end — the user simply stops asking — so waiting for one would
mean the most interesting transcripts are the ones never saved.

The `client_id` is generated in the browser when the conversation starts. That is what makes the
upsert idempotent without a round trip to learn a server id first, and it is scoped per user by
a unique constraint, so one user's id can never collide with another's.

Writes are best-effort at the call site, exactly like `runs.save_run_best_effort`: a consultation
that answered correctly and failed to save is a worse outcome than one that did not save, and
neither is worth failing the user's request over.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.db import pool

logger = logging.getLogger(__name__)


async def upsert_consultation(
    *,
    owner_user_id: str,
    client_id: str,
    title: str,
    advisor_ids: list[str],
    model: str,
    depth: str,
    turns: list[dict[str, Any]],
    synthesis: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
    question_count: int,
) -> None:
    """Create or update one consultation. Last write wins, which is what a transcript wants."""
    await pool.execute(
        """
        insert into public.consultations
          (user_id, client_id, title, advisor_ids, model, depth,
           turns, synthesis, candidates, question_count)
        values ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9::jsonb, $10)
        on conflict (user_id, client_id) do update set
          title          = excluded.title,
          advisor_ids    = excluded.advisor_ids,
          model          = excluded.model,
          depth          = excluded.depth,
          turns          = excluded.turns,
          synthesis      = excluded.synthesis,
          candidates     = excluded.candidates,
          question_count = excluded.question_count
        """,
        owner_user_id,
        client_id,
        title,
        advisor_ids,
        model,
        depth,
        json.dumps(turns),
        json.dumps(synthesis) if synthesis is not None else None,
        json.dumps(candidates),
        question_count,
    )


async def save_consultation_best_effort(**kwargs: Any) -> None:
    """Never raises. A failed save must not fail the answer the user already has."""
    try:
        await upsert_consultation(**kwargs)
    except Exception:  # noqa: BLE001 - history is not worth failing a request over
        logger.warning("consultation not saved", exc_info=True)


def _decode(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """asyncpg hands jsonb back as text unless a codec is registered.

    Decoded here rather than by registering a global codec, matching `runs.get_run` — a codec
    would change the behaviour of every existing query at once, and this is the narrower change.
    """
    for field in fields:
        if isinstance(row.get(field), str):
            row[field] = json.loads(row[field])
    return row


async def list_consultations(owner_user_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """Most recently updated first — the one still being worked on belongs at the top."""
    rows = await pool.fetch(
        """
        select client_id, title, advisor_ids, model, depth, synthesis,
               question_count, created_at, updated_at
        from public.consultations
        where user_id = $1
        order by updated_at desc
        limit $2
        """,
        owner_user_id,
        limit,
    )
    return [_decode(dict(r), ("synthesis",)) for r in rows]


async def get_consultation(owner_user_id: str, client_id: str) -> dict[str, Any] | None:
    """One full transcript. Scoped by owner in the query, never by trusting the caller."""
    row = await pool.fetchrow(
        """
        select client_id, title, advisor_ids, model, depth, turns, synthesis, candidates,
               question_count, created_at, updated_at
        from public.consultations
        where user_id = $1 and client_id = $2
        """,
        owner_user_id,
        client_id,
    )
    return _decode(dict(row), ("turns", "synthesis", "candidates")) if row else None
