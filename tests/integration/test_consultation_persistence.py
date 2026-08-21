"""Saving and reading consultations, against a real Postgres.

Skipped when DATABASE_URL is unset, same as the other persistence tests — see
scripts/validate_migrations.sh for standing up a scratch database locally.

What these exist to catch: that a conversation upserts onto one row as it grows rather than
accumulating a row per answer (the `(user_id, client_id)` unique constraint is what makes that
work, and an ON CONFLICT clause that stopped matching it would fail loudly here), and that one
user can never read another's transcript.
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.db import pool
from app.db.repositories import consultations as repo

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="no DATABASE_URL — see scripts/validate_migrations.sh to stand up a scratch database",
)


@pytest.fixture(autouse=True)
async def _clean_pool():
    yield
    await pool.close_pool()


async def _user() -> str:
    new_id = str(uuid.uuid4())
    await pool.execute(
        "insert into auth.users (id, email) values ($1, $2)", new_id, f"{new_id}@advisoros.dev"
    )
    return new_id


def _turns(n: int) -> list[dict]:
    out: list[dict] = []
    for i in range(n):
        out.append({"role": "user", "text": f"question {i}", "advisor_responses": []})
        out.append({"role": "committee", "text": f"answer {i}", "advisor_responses": []})
    return out


async def _save(user_id: str, client_id: str, *, questions: int, title: str = "A consultation"):
    await repo.upsert_consultation(
        owner_user_id=user_id,
        client_id=client_id,
        title=title,
        advisor_ids=["buffett", "munger"],
        model="claude-sonnet-5",
        depth="quick",
        turns=_turns(questions),
        synthesis={"headline": f"after {questions}", "unresolved_disagreement": False},
        candidates=[{"candidate_id": "act"}],
        question_count=questions,
    )


async def test_a_growing_conversation_stays_one_row():
    """Written after every round, so the row has to be updated rather than inserted again."""
    user_id = await _user()
    await _save(user_id, "c1", questions=1)
    await _save(user_id, "c1", questions=2)
    await _save(user_id, "c1", questions=3)

    rows = await repo.list_consultations(user_id)
    assert len(rows) == 1
    assert rows[0]["question_count"] == 3


async def test_the_latest_conclusion_replaces_the_previous_one():
    user_id = await _user()
    await _save(user_id, "c1", questions=1)
    await _save(user_id, "c1", questions=2)

    detail = await repo.get_consultation(user_id, "c1")
    assert detail is not None
    assert detail["synthesis"]["headline"] == "after 2"
    assert len(detail["turns"]) == 4


async def test_the_time_and_the_committee_are_recorded():
    """The three things Reports has to show: when, who, and what it concluded."""
    user_id = await _user()
    await _save(user_id, "c1", questions=1)

    row = (await repo.list_consultations(user_id))[0]
    assert row["created_at"] is not None and row["updated_at"] is not None
    assert list(row["advisor_ids"]) == ["buffett", "munger"]
    assert row["synthesis"]["headline"] == "after 1"


async def test_separate_conversations_are_separate_rows():
    user_id = await _user()
    await _save(user_id, "c1", questions=1, title="First")
    await _save(user_id, "c2", questions=1, title="Second")

    rows = await repo.list_consultations(user_id)
    assert {r["title"] for r in rows} == {"First", "Second"}


async def test_one_user_cannot_read_anothers_transcript():
    """Scoped by owner in the query, never by trusting the caller."""
    mine, theirs = await _user(), await _user()
    await _save(theirs, "secret", questions=1)

    assert await repo.get_consultation(mine, "secret") is None
    assert await repo.list_consultations(mine) == []


async def test_the_same_client_id_from_two_users_does_not_collide():
    """The unique constraint is scoped per user, so ids generated in two browsers are safe."""
    a, b = await _user(), await _user()
    await _save(a, "same", questions=1, title="Mine")
    await _save(b, "same", questions=2, title="Theirs")

    assert (await repo.get_consultation(a, "same"))["title"] == "Mine"
    assert (await repo.get_consultation(b, "same"))["question_count"] == 2
