"""Getting a user registered at a provider exactly once, and surviving the ways that fails.

The naive version — "register, then save" — has a failure mode that costs the user real work.
A `userSecret` is issued once and cannot be recovered. If registration succeeds and the write
fails, the provider now holds an account whose credential nobody has: the user cannot connect,
retrying creates a *second* orphaned account, and the only repair is an operator deleting the
provider-side user by hand.

So the order here is deliberate and the checks are pessimistic:

  **Refuse before asking.** If no encryption key is configured, this raises *before* the
  provider call. Accepting a secret that cannot be stored is the one outcome worse than
  refusing to start.

  **Look before creating.** An existing local identity short-circuits, so a double-clicked
  button or a retried request does not register twice.

  **Roll back what we cannot keep.** If the write fails after the provider created the user,
  the provider-side user is deleted so the account is not left orphaned. If that cleanup also
  fails, it is logged loudly with the ids needed to finish it by hand — an orphan someone knows
  about is recoverable, one nobody knows about is not.
"""

from __future__ import annotations

import logging

from app.connectors import snaptrade
from app.core import broker_credentials
from app.db.repositories import brokerage
from app.db.repositories.brokerage import ProviderIdentity

logger = logging.getLogger(__name__)


class RegistrationUnavailable(RuntimeError):
    """Brokerage connection cannot be offered right now, and it is not the user's fault."""


async def ensure_registered(
    user_id: str, *, client: snaptrade.SnapTradeClient | None = None
) -> ProviderIdentity:
    """The user's SnapTrade identity, registering them if this is their first connection.

    Idempotent: safe to call on every visit to the connect screen.
    """
    if not user_id:
        raise RegistrationUnavailable("registration requires a signed-in user")

    existing = await brokerage.provider_identity(user_id, snaptrade.PROVIDER)
    if existing is not None:
        return existing

    # Checked before the provider call, not after. A secret we cannot store is worse than a
    # registration we never attempted.
    if not broker_credentials.is_configured():
        raise RegistrationUnavailable(
            f"{broker_credentials.ACTIVE_KEY_ENV} is not set, so a provider secret could not be "
            "stored — refusing to register rather than issuing a credential we would drop"
        )
    if client is None:
        if not snaptrade.is_configured():
            raise RegistrationUnavailable(
                f"{snaptrade.CLIENT_ID_ENV} and {snaptrade.CONSUMER_KEY_ENV} are not set"
            )
        client = snaptrade.SnapTradeClient(snaptrade.SnapTradeConfig.from_env())

    provider_user_id, secret = await client.register_user(user_id)

    try:
        return await brokerage.save_provider_user(
            user_id, snaptrade.PROVIDER, provider_user_id, secret
        )
    except Exception:
        # The provider created a user whose secret we just failed to keep. Leaving it behind
        # means an account nobody can authenticate to and a retry that orphans another one.
        logger.exception(
            "failed to store SnapTrade secret for %s; rolling back provider user %s",
            user_id,
            provider_user_id,
        )
        await _rollback(client, user_id, provider_user_id)
        raise


async def _rollback(client: snaptrade.SnapTradeClient, user_id: str, provider_user_id: str) -> None:
    try:
        await client.delete_user(provider_user_id)
    except Exception:  # noqa: BLE001 - the original failure is what the caller must see
        # An orphan someone knows about can be cleaned up; one nobody knows about cannot. Both
        # ids are logged precisely so this is finishable by hand.
        logger.error(
            "ORPHANED SnapTrade user %s for AdvisorOS user %s — created at the provider, not "
            "stored locally, and could not be deleted. Delete it manually.",
            provider_user_id,
            user_id,
        )


async def deregister(user_id: str, *, client: snaptrade.SnapTradeClient | None = None) -> bool:
    """Remove the user from the provider and delete every local trace. Returns whether one existed.

    Both halves, in this order. Deleting locally first would lose the provider user id and with
    it any way to reach the provider-side account, so a failure there would strand it forever.
    """
    identity = await brokerage.provider_identity(user_id, snaptrade.PROVIDER)
    if identity is None:
        return False

    if client is None and snaptrade.is_configured():
        client = snaptrade.SnapTradeClient(snaptrade.SnapTradeConfig.from_env())

    if client is not None:
        try:
            await client.delete_user(identity.provider_user_id)
        except Exception:  # noqa: BLE001 - local deletion must still proceed
            # A user who asked to be forgotten should not stay connected here because a third
            # party was unreachable. The local rows go; the provider-side orphan is logged.
            logger.error(
                "could not delete SnapTrade user %s for %s; removing local records anyway",
                identity.provider_user_id,
                user_id,
            )

    await brokerage.delete_all_for_user(user_id)
    return True
