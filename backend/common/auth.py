"""FastAPI dependencies for reading the caller's identity out of a JWT.

Every service imports these instead of re-implementing token parsing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from common import security, settings


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str
    handle: str | None
    kyc_verified: bool
    lang: str

    # The staff roles, from narrowest to widest:
    # - moderator:  review and moderation (posts, appeals, memorial death
    #               reports, contact risk, age review) - no money, no accounts;
    # - admin:      the whole console;
    # - superadmin: the console, and who is on the team.
    @property
    def is_moderator(self) -> bool:
        return self.role in ("moderator", "admin", "superadmin")

    @property
    def is_admin(self) -> bool:
        return self.role in ("admin", "superadmin")

    @property
    def is_superadmin(self) -> bool:
        return self.role == "superadmin"


def _extract(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


def optional_principal(
    authorization: Annotated[str | None, Header()] = None,
    x_lang: Annotated[str | None, Header(alias="X-Lang")] = None,
) -> Principal | None:
    """Resolve the caller when a valid token is present, else ``None``.

    Used by endpoints that serve both visitors and members (public feeds,
    memorial pages, the assistant).
    """
    token = _extract(authorization)
    if not token:
        return None
    claims = security.decode_token(token, expected_type=security.ACCESS)
    if not claims:
        return None
    lang = x_lang or claims.get("lang") or settings.DEFAULT_LANG
    return Principal(
        user_id=str(claims["sub"]),
        role=claims.get("role", "member"),
        handle=claims.get("handle"),
        kyc_verified=bool(claims.get("kyc", False)),
        lang=lang if lang in settings.SUPPORTED_LANGS else settings.DEFAULT_LANG,
    )


def current_principal(principal: Annotated[Principal | None, Depends(optional_principal)]) -> Principal:
    """Require an authenticated caller."""
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def require_admin(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return principal


def require_moderator(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
    if not principal.is_moderator:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Moderator access required")
    return principal


def require_superadmin(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
    if not principal.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only a superadmin can do that")
    return principal


def require_kyc(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
    """Gate for anything that pays money out (affiliate participation, cashouts)."""
    if not principal.kyc_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="KinjyKYC verification required for this action",
        )
    return principal


CurrentUser = Annotated[Principal, Depends(current_principal)]
MaybeUser = Annotated[Principal | None, Depends(optional_principal)]
AdminUser = Annotated[Principal, Depends(require_admin)]
ModeratorUser = Annotated[Principal, Depends(require_moderator)]
SuperAdminUser = Annotated[Principal, Depends(require_superadmin)]
KycUser = Annotated[Principal, Depends(require_kyc)]
