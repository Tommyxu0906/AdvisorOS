"""Registering once, and what happens when the two halves disagree.

A `userSecret` is issued once and cannot be recovered. That single fact makes the ordering here
the whole design: if the provider creates a user and the local write then fails, the provider
holds an account whose credential nobody has. The user cannot connect, retrying orphans a second
account, and the only repair is an operator deleting the provider-side user by hand.

`test_a_failed_write_deletes_the_provider_user_it_could_not_keep` is the test that matters. The
repository is stubbed rather than backed by Postgres because what is under test is the ordering
and the rollback, not SQL — the SQL has its own coverage in the integration suite and the
migration validator.
"""

from __future__ import annotations

import logging

import pytest

from app.connectors import registration, snaptrade
from app.core import broker_credentials as bc
from app.core.broker_credentials import BrokerSecret
from app.db.repositories.brokerage import ProviderIdentity

USER = "6f2a1b3c-0000-4000-8000-000000000001"
PROVIDER_USER = "provider-side-id"
SECRET = "st-usersecret-9f8a7b6c5d4e"


class FakeClient:
    """Stands in for `SnapTradeClient`. Records calls; fails on demand."""

    def __init__(self, *, register_fails=False, delete_fails=False) -> None:
        self.registered: list[str] = []
        self.deleted: list[str] = []
        self._register_fails = register_fails
        self._delete_fails = delete_fails

    async def register_user(self, user_id: str) -> tuple[str, BrokerSecret]:
        if self._register_fails:
            raise snaptrade.SnapTradeError("provider refused")
        self.registered.append(user_id)
        return PROVIDER_USER, BrokerSecret(SECRET)

    async def delete_user(self, provider_user_id: str) -> None:
        if self._delete_fails:
            raise snaptrade.SnapTradeError("provider unreachable")
        self.deleted.append(provider_user_id)


@pytest.fixture
def store(monkeypatch):
    """An in-memory stand-in for the brokerage repository."""

    rows: dict[str, ProviderIdentity] = {}
    state = {"save_fails": False, "deleted_all": []}

    async def provider_identity(user_id: str, provider: str):
        return rows.get(user_id)

    async def save_provider_user(user_id, provider, provider_user_id, secret):
        if state["save_fails"]:
            raise RuntimeError("database is down")
        identity = ProviderIdentity(
            user_id=user_id,
            provider=provider,
            provider_user_id=provider_user_id,
            key_version=1,
        )
        rows[user_id] = identity
        return identity

    async def delete_all_for_user(user_id: str):
        rows.pop(user_id, None)
        state["deleted_all"].append(user_id)

    monkeypatch.setattr(registration.brokerage, "provider_identity", provider_identity)
    monkeypatch.setattr(registration.brokerage, "save_provider_user", save_provider_user)
    monkeypatch.setattr(registration.brokerage, "delete_all_for_user", delete_all_for_user)
    monkeypatch.setenv(bc.ACTIVE_KEY_ENV, bc.generate_key(1))

    state["rows"] = rows
    return state


# --- the happy path is idempotent -------------------------------------------------------------


async def test_a_first_time_user_is_registered_and_stored(store):
    client = FakeClient()
    identity = await registration.ensure_registered(USER, client=client)

    assert client.registered == [USER]
    assert identity.provider_user_id == PROVIDER_USER
    assert store["rows"][USER].provider_user_id == PROVIDER_USER


async def test_an_existing_user_is_not_registered_twice(store):
    client = FakeClient()
    await registration.ensure_registered(USER, client=client)
    again = await registration.ensure_registered(USER, client=client)

    # A double-clicked button must not create a second provider account.
    assert client.registered == [USER]
    assert again.provider_user_id == PROVIDER_USER


# --- the failure that costs the user real work --------------------------------------------------


async def test_a_failed_write_deletes_the_provider_user_it_could_not_keep(store):
    """The secret is issued once. Keeping a provider account we cannot authenticate to would
    strand the user behind a credential nobody has."""
    store["save_fails"] = True
    client = FakeClient()

    with pytest.raises(RuntimeError, match="database is down"):
        await registration.ensure_registered(USER, client=client)

    assert client.registered == [USER]
    assert client.deleted == [PROVIDER_USER]  # rolled back
    assert USER not in store["rows"]


async def test_an_unrollbackable_orphan_is_logged_loudly_with_both_ids(store, caplog):
    """An orphan someone knows about is recoverable; one nobody knows about is not."""
    store["save_fails"] = True
    client = FakeClient(delete_fails=True)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError):
            await registration.ensure_registered(USER, client=client)

    assert "ORPHANED" in caplog.text
    assert PROVIDER_USER in caplog.text
    assert USER in caplog.text
    # The original failure is what propagates, not the cleanup failure.
    assert client.deleted == []


async def test_no_encryption_key_refuses_before_the_provider_is_called(store, monkeypatch):
    """Accepting a secret that cannot be stored is the one outcome worse than not starting."""
    monkeypatch.delenv(bc.ACTIVE_KEY_ENV, raising=False)
    client = FakeClient()

    with pytest.raises(registration.RegistrationUnavailable, match="AIFA_BROKER_ENCRYPTION_KEY"):
        await registration.ensure_registered(USER, client=client)

    assert client.registered == []  # never asked for a credential it could not keep


async def test_a_provider_failure_stores_nothing(store):
    client = FakeClient(register_fails=True)
    with pytest.raises(snaptrade.SnapTradeError):
        await registration.ensure_registered(USER, client=client)
    assert USER not in store["rows"]


async def test_registration_requires_a_signed_in_user(store):
    with pytest.raises(registration.RegistrationUnavailable, match="signed-in user"):
        await registration.ensure_registered("", client=FakeClient())


async def test_missing_provider_credentials_are_reported_as_configuration(store, monkeypatch):
    monkeypatch.delenv(snaptrade.CLIENT_ID_ENV, raising=False)
    monkeypatch.delenv(snaptrade.CONSUMER_KEY_ENV, raising=False)

    with pytest.raises(registration.RegistrationUnavailable, match="SNAPTRADE_CLIENT_ID"):
        await registration.ensure_registered(USER)  # no injected client


# --- deletion does both halves, in the order that cannot strand anything --------------------------


async def test_deregistering_deletes_at_the_provider_then_locally(store):
    client = FakeClient()
    await registration.ensure_registered(USER, client=client)

    assert await registration.deregister(USER, client=client) is True
    assert client.deleted == [PROVIDER_USER]
    assert store["deleted_all"] == [USER]
    assert USER not in store["rows"]


async def test_local_records_are_removed_even_if_the_provider_is_unreachable(store, caplog):
    """Someone who asked to be forgotten should not stay connected here because a third party
    was down. The provider-side orphan is logged instead."""
    await registration.ensure_registered(USER, client=FakeClient())
    failing = FakeClient(delete_fails=True)

    with caplog.at_level(logging.ERROR):
        assert await registration.deregister(USER, client=failing) is True

    assert USER not in store["rows"]
    assert store["deleted_all"] == [USER]
    assert "could not delete SnapTrade user" in caplog.text


async def test_deregistering_someone_who_never_connected_is_a_no_op(store):
    client = FakeClient()
    assert await registration.deregister(USER, client=client) is False
    assert client.deleted == []


async def test_the_provider_id_is_read_before_local_rows_are_deleted(store):
    """Deleting locally first would lose the only handle on the provider-side account."""
    client = FakeClient()
    await registration.ensure_registered(USER, client=client)
    await registration.deregister(USER, client=client)

    assert client.deleted == [PROVIDER_USER]
