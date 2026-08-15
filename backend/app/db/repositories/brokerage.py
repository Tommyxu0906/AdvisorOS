"""Persistence for brokerage provider identity and institution links.

The one rule that separates this module from every other repository: **the secret goes in and
never comes back out through anything but `provider_secret()`.** There is no `load()` returning a
row dict, no model with the ciphertext on it, and no function that hands a caller something they
could accidentally serialize into a response. `provider_identity()` returns the provider's user
id and nothing else, because that is what almost every call site actually needs; the one place
that needs the secret asks for it by name and gets a `BrokerSecret` that resists being printed.

Encryption and decryption happen in `core/broker_credentials.py`, not here. This module never
sees plaintext except in the moment it is handed one to seal, which keeps the audit surface for
"where could a secret leak" down to two files.

Authorization is the same as everywhere else in this layer and is worth restating because it is
easy to assume RLS is doing it: FastAPI connects with the service-role key and bypasses RLS
entirely, so `where user_id = $1` in these queries *is* the access control. Every function here
takes a `user_id` and filters on it. None of them accept a provider row id alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.connectors.base import BrokerageConnection
from app.core.broker_credentials import (
    BrokerSecret,
    EncryptedSecret,
    active_key_version,
    decrypt_secret,
    encrypt_secret,
)
from app.db import pool
from app.domain.connection import ConnectionStatus


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    """Who this user is to the provider. Deliberately carries no secret.

    Most call sites need only this — the provider user id to put in a request — so the type they
    get back cannot leak a credential even if it is logged or returned wholesale.
    """

    user_id: str
    provider: str
    provider_user_id: str
    key_version: int
    created_at: datetime | None = None


async def provider_identity(user_id: str, provider: str) -> ProviderIdentity | None:
    """The user's identity at a provider, or None if they have never registered."""
    row = await pool.fetchrow(
        """
        select user_id, provider, provider_user_id, key_version, created_at
        from public.brokerage_provider_users
        where user_id = $1 and provider = $2
        """,
        user_id,
        provider,
    )
    if row is None:
        return None
    return ProviderIdentity(
        user_id=str(row["user_id"]),
        provider=row["provider"],
        provider_user_id=row["provider_user_id"],
        key_version=row["key_version"],
        created_at=row["created_at"],
    )


async def provider_secret(user_id: str, provider: str) -> BrokerSecret | None:
    """The decrypted provider secret. The only path out of the database for this value.

    Named so that every call site is trivially greppable, and returns a `BrokerSecret` rather
    than a `str` so that passing it somewhere careless still does not print it.
    """
    row = await pool.fetchrow(
        """
        select secret_ciphertext, key_version
        from public.brokerage_provider_users
        where user_id = $1 and provider = $2
        """,
        user_id,
        provider,
    )
    if row is None:
        return None
    return decrypt_secret(
        EncryptedSecret(ciphertext=row["secret_ciphertext"], key_version=row["key_version"]),
        user_id=user_id,
    )


async def save_provider_user(
    user_id: str,
    provider: str,
    provider_user_id: str,
    secret: BrokerSecret,
) -> ProviderIdentity:
    """Store or replace a user's provider identity and secret.

    Upsert rather than insert: re-registering must be idempotent. A provider that hands back a
    new secret for an existing user has to overwrite, or the row keeps a credential that no
    longer authenticates and every later call fails in a way that looks like an outage.
    """
    sealed = encrypt_secret(secret, user_id=user_id)
    row = await pool.fetchrow(
        """
        insert into public.brokerage_provider_users
            (user_id, provider, provider_user_id, secret_ciphertext, key_version)
        values ($1, $2, $3, $4, $5)
        on conflict (user_id) do update set
            provider          = excluded.provider,
            provider_user_id  = excluded.provider_user_id,
            secret_ciphertext = excluded.secret_ciphertext,
            key_version       = excluded.key_version
        returning user_id, provider, provider_user_id, key_version, created_at
        """,
        user_id,
        provider,
        provider_user_id,
        sealed.ciphertext,
        sealed.key_version,
    )
    return ProviderIdentity(
        user_id=str(row["user_id"]),
        provider=row["provider"],
        provider_user_id=row["provider_user_id"],
        key_version=row["key_version"],
        created_at=row["created_at"],
    )


async def delete_provider_user(user_id: str, provider: str) -> bool:
    """Remove the provider identity and its secret. Returns whether a row existed.

    Deleting locally is only half of the job — the provider holds an account too, and leaving it
    behind means a user who asked to be forgotten still exists at a third party. The caller is
    responsible for the provider-side deletion; this is the local half.
    """
    result = await pool.execute(
        """
        delete from public.brokerage_provider_users
        where user_id = $1 and provider = $2
        """,
        user_id,
        provider,
    )
    return result.endswith(" 1")


async def secret_needs_rotation(user_id: str, provider: str) -> bool:
    """Whether this row is readable but sealed under a superseded key."""
    identity = await provider_identity(user_id, provider)
    return identity is not None and identity.key_version != active_key_version()


async def rotate_secret(user_id: str, provider: str) -> bool:
    """Re-seal an existing secret under the active key. Returns whether anything moved.

    This is what makes a compromised key survivable: rows migrate in the background and the
    retired key is dropped once none remain, instead of every user being asked to re-link their
    brokerage accounts.
    """
    identity = await provider_identity(user_id, provider)
    if identity is None or identity.key_version == active_key_version():
        return False
    secret = await provider_secret(user_id, provider)
    if secret is None:
        return False
    await save_provider_user(user_id, provider, identity.provider_user_id, secret)
    return True


# --- institution links ---------------------------------------------------------------------


async def upsert_connections(
    user_id: str, provider: str, connections: list[BrokerageConnection]
) -> None:
    """Reconcile the stored links against what the provider currently reports.

    Reconnecting an institution must update the existing row. Inserting instead would leave two
    rows for one brokerage, and every downstream count — accounts, total value, institutions
    connected — would double.
    """
    if not connections:
        return
    async with pool.acquire() as conn:
        async with conn.transaction():
            for connection in connections:
                await conn.execute(
                    """
                    insert into public.brokerage_connections
                        (user_id, provider, provider_connection_id, institution,
                         status, needs_reconnect, last_successful_sync)
                    values ($1, $2, $3, $4, $5, $6, $7)
                    on conflict (user_id, provider, provider_connection_id) do update set
                        institution          = excluded.institution,
                        status               = excluded.status,
                        needs_reconnect      = excluded.needs_reconnect,
                        last_successful_sync = excluded.last_successful_sync
                    """,
                    user_id,
                    provider,
                    connection.connection_id,
                    connection.institution,
                    connection.status.value,
                    connection.needs_reconnect,
                    _as_datetime(connection.last_successful_sync),
                )


async def list_connections(user_id: str) -> list[BrokerageConnection]:
    """Every institution this user has linked, across providers."""
    rows = await pool.fetch(
        """
        select provider, provider_connection_id, institution, status,
               needs_reconnect, last_successful_sync, created_at
        from public.brokerage_connections
        where user_id = $1
        order by created_at
        """,
        user_id,
    )
    return [
        BrokerageConnection(
            connection_id=row["provider_connection_id"],
            provider=row["provider"],
            institution=row["institution"] or row["provider"],
            status=ConnectionStatus(row["status"]),
            needs_reconnect=row["needs_reconnect"],
            last_successful_sync=(
                row["last_successful_sync"].isoformat() if row["last_successful_sync"] else None
            ),
            created_at=row["created_at"].isoformat() if row["created_at"] else None,
        )
        for row in rows
    ]


async def delete_connection(user_id: str, provider: str, connection_id: str) -> bool:
    """Forget one institution link. Returns whether a row existed.

    Scoped by `user_id` so a guessed connection id from another account deletes nothing — the
    service-role connection would happily honour an unscoped delete.
    """
    result = await pool.execute(
        """
        delete from public.brokerage_connections
        where user_id = $1 and provider = $2 and provider_connection_id = $3
        """,
        user_id,
        provider,
        connection_id,
    )
    return result.endswith(" 1")


async def delete_all_for_user(user_id: str) -> None:
    """Remove every local trace of a user's brokerage integration.

    Distinct from deleting the AdvisorOS account: the profile, portfolio, and run history all
    survive this. "Stop the data feed" and "erase me" are different requests and the UI must not
    conflate them.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "delete from public.brokerage_connections where user_id = $1", user_id
            )
            await conn.execute(
                "delete from public.brokerage_provider_users where user_id = $1", user_id
            )


def _as_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
