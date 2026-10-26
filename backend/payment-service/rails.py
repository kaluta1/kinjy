"""Payment rails.

Kinjy takes payments through **Sonco Pay**, directly or through **Kaluta Pay**
(which connects to Sonco Pay and carries the accounting split: commissions,
the Leaders pool, marketplace orders held until they are final). Both live in
their own modules (``sonco.py``, ``kalutapay.py``); which one a payment uses is
decided in ``main.checkout`` from what is configured.

Mangopay and NowPayments were removed on 27/10: no payment, payout or refund
can go through them any more. Rows they wrote earlier keep their ``rail``
value, so history and reconciliation still read correctly (see RAIL_NAMES in
main.py).

What is left here is the **test rail**: when no provider is configured on a
development machine, a checkout returns a clearly flagged ``"mock": true``
payment so the flows can be exercised end to end. It never exists in
production - there, no configured provider means the checkout is refused.
"""
from __future__ import annotations

import logging
from typing import Any

from common import settings
from common.ids import new_id

log = logging.getLogger("payment-rails")

# Rails that took payments once and are kept only so old rows still read.
REMOVED_RAILS = ("nowpayments", "mangopay")


class RailError(RuntimeError):
    pass


def test_rail_allowed() -> bool:
    """The test rail exists only off production."""
    return settings.ENV != "production"


def create_test_payment(*, reference: str) -> dict[str, Any]:
    """A payment that moves nothing, for development only."""
    if not test_rail_allowed():
        raise RailError("No payment provider is configured")
    return {
        "mock": True,
        "external_id": f"mock_{new_id('tp')}",
        "status": "waiting",
        "note": "Test rail: no provider is configured on this machine and no money moves.",
    }


def choose_rail() -> str:
    """The rail when neither Sonco Pay nor Kaluta Pay is configured: the test
    rail off production, nothing in production."""
    if test_rail_allowed():
        return "mock"
    raise RailError("Payments are unavailable: no payment provider is configured")
