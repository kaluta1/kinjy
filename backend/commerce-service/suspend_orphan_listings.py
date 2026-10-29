"""Find - and, with --apply, suspend - active listings whose seller account no
longer exists.

    docker exec -w /app kaluta-commerce python suspend_orphan_listings.py          # dry run: report only
    docker exec -w /app kaluta-commerce python suspend_orphan_listings.py --apply  # suspend what was found

Why: rebuilding the member base from DSM (26/10, scripts/import_members.py)
removed the accounts that were not DSM members - profiles, follows, sessions -
but its table list never included commerce.products, so their listings stayed
active. They show "Seller" with no picture, have no photo or description to
recover, and can still be ordered: a buyer would pay into escrow for a seller
who cannot hand anything over.

What it does, and only that: a listing is suspended when auth-service answers
that its seller does not exist (404) or is deleted - an unanswered lookup is
not an answer, and that listing is left alone. Suspending is the admin
take-down that already exists: the row, its photos and its orders are kept,
new purchases stop, an audit row records the reason, and an admin can
reinstate it. Running it again finds nothing more to do.
"""
from __future__ import annotations

import argparse
import json
import sys

import httpx
from sqlalchemy import select

from common.database import SessionLocal

import models

AUTH_URL = "http://auth-service:8000"
REASON = "The seller's account no longer exists on Kinjy"


def seller_gone(vendor_id: str) -> bool | None:
    """True when auth-service says the account is gone, None when it did not answer."""
    try:
        response = httpx.get(f"{AUTH_URL}/internal/users/{vendor_id}", timeout=6)
    except httpx.HTTPError:
        return None
    if response.status_code == 404:
        return True
    if response.status_code != 200:
        return None
    return response.json().get("status") == "deleted"


def run(apply: bool) -> dict:
    report: dict = {"mode": "apply" if apply else "dry-run", "orphans": [], "unverified": [], "suspended": 0}
    with SessionLocal() as db:
        rows = db.scalars(select(models.Product).where(models.Product.status == "active")).all()
        verdicts: dict[str, bool | None] = {}
        for product in rows:
            if product.vendor_id not in verdicts:
                verdicts[product.vendor_id] = seller_gone(product.vendor_id)
            gone = verdicts[product.vendor_id]
            entry = {"id": product.id, "title": product.title, "vendor_id": product.vendor_id,
                     "created_at": product.created_at.isoformat() if product.created_at else None,
                     "open_orders": db.scalar(select(models.Order.id).where(
                         models.Order.product_id == product.id,
                         models.Order.status.in_(("pending", "in_escrow", "delivered"))).limit(1)) is not None}
            if gone is None:
                report["unverified"].append(entry)
            elif gone:
                report["orphans"].append(entry)
                if apply:
                    product.status = "suspended"
                    product.suspended_reason = REASON
                    db.add(models.CommerceAudit(actor_id="repair", action="suspend_listing", target_type="product",
                                                target_id=product.id, reason=REASON))
                    report["suspended"] += 1
        if apply:
            db.commit()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="suspend the listings found (default: report only)")
    print(json.dumps(run(parser.parse_args().apply), indent=2, default=str))
    sys.exit(0)
