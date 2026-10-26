"""Central settings shared by every Kinjy service.

Everything is read from the environment (docker-compose injects it). Values are
plain attributes rather than a BaseSettings model so that a missing optional key
never blocks a service from booting in development.
"""
from __future__ import annotations

import os
from decimal import Decimal


def _str(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, "") or default)
    except ValueError:
        return default


def _dec(key: str, default: str) -> Decimal:
    try:
        return Decimal(os.getenv(key, "") or default)
    except Exception:
        return Decimal(default)


# --- Infrastructure ---------------------------------------------------------
DATABASE_URL = _str("DATABASE_URL", "postgresql://kaluta:kaluta@postgres:5432/kaluta")
REDIS_URL = _str("REDIS_URL", "redis://redis:6379/0")
RABBITMQ_URL = _str("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
SERVICE_NAME = _str("SERVICE_NAME", os.getenv("SERVICE", "kaluta-service"))
ENV = _str("ENV", "development")

# --- Security ---------------------------------------------------------------
JWT_SECRET = _str("JWT_SECRET", "dev-insecure-secret-change-me")
JWT_ALGORITHM = _str("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_TTL_MIN = _int("ACCESS_TOKEN_TTL_MIN", 30)
REFRESH_TOKEN_TTL_DAYS = _int("REFRESH_TOKEN_TTL_DAYS", 30)
WEBAUTHN_RP_ID = _str("WEBAUTHN_RP_ID", "localhost")
WEBAUTHN_RP_NAME = _str("WEBAUTHN_RP_NAME", "Kinjy")
WEBAUTHN_ORIGIN = _str("WEBAUTHN_ORIGIN", "http://localhost:3030")

# --- Public URLs ------------------------------------------------------------
FRONTEND_URL = _str("FRONTEND_URL", "http://localhost:3030")
GATEWAY_URL = _str("GATEWAY_URL", "http://localhost:8200")

# --- Outgoing mail ----------------------------------------------------------
# Plain SMTP rather than one provider's API, because every provider speaks it
# and the choice then belongs in .env instead of in the code.
#
# Nothing is sent unless SMTP_HOST and SMTP_FROM are both set. A password reset
# that cannot deliver its link is worse than one that is plainly switched off:
# the member waits for mail that will never come, and support hears about it
# instead of us. So the service reports itself unavailable rather than
# accepting a request it cannot honour.
SMTP_HOST = _str("SMTP_HOST")
SMTP_PORT = _int("SMTP_PORT", 587)
SMTP_USER = _str("SMTP_USER")
SMTP_PASSWORD = _str("SMTP_PASSWORD")
SMTP_FROM = _str("SMTP_FROM")
SMTP_FROM_NAME = _str("SMTP_FROM_NAME", "Kinjy")
# STARTTLS on the submission port is the common case; set SMTP_SSL=1 for an
# implicit-TLS port (usually 465).
SMTP_STARTTLS = _str("SMTP_STARTTLS", "1") not in ("0", "false", "no")
SMTP_SSL = _str("SMTP_SSL", "0") not in ("0", "false", "no")


def mail_configured() -> bool:
    return bool(SMTP_HOST and SMTP_FROM)


# --- Web Push ---------------------------------------------------------------
# VAPID identifies this server to the push services (Google's, Mozilla's,
# Apple's) so they will accept pushes addressed to their subscriptions. The
# private key is a credential: anyone holding it can push to every member who
# subscribed to this site, so it lives in .env and nowhere else.
#
# The public key is not secret - the browser needs it to create a subscription,
# and it is served to anybody who asks.
#
# Nothing is sent unless both are set, and the subscribe endpoint says so
# rather than taking subscriptions it can never use.
VAPID_PUBLIC_KEY = _str("VAPID_PUBLIC_KEY")
VAPID_PRIVATE_KEY = _str("VAPID_PRIVATE_KEY")
# A contact the push service can use if this server misbehaves. Required by the
# spec; a mailto: that nobody reads is worse than one that works.
VAPID_SUBJECT = _str("VAPID_SUBJECT", "mailto:support@kinjy.com")


def push_configured() -> bool:
    return bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)

# --- Economy ----------------------------------------------------------------
# Marketplace / agency: customer price = vendor price * (1 + markup). A vendor
# listing at $100 with a 20% markup is sold at $120; Kinjy's *revenue* on that
# deal is the $20 markup — the $100 is the seller's money passing through.
MARKETPLACE_MARKUP_PCT = _dec("MARKETPLACE_MARKUP_PCT", "20")

# The direct affiliate programme. One level, no depth: the member who sponsored
# the person generating the revenue is paid this percentage OF KINJY'S REVENUE.
# Where Kinjy acts as an agent between a buyer and a seller (digital products,
# private subscription clubs), Kinjy's revenue is the markup above — so the
# commission comes out of the markup and never out of the seller's price.
SPONSOR_COMMISSION_PCT = _dec("SPONSOR_COMMISSION_PCT", "20")

# Kinjy Leaders: this share of monthly company revenue is set aside as it is
# earned and distributed at month end to the members with the highest direct
# commissions, in proportion to those commissions.
LEADERS_POOL_PCT = _dec("LEADERS_POOL_PCT", "5")
LEADERS_POOL_SIZE = _int("LEADERS_POOL_SIZE", 10000)

# Creator ad revenue share. The creator's cut is an expense against the ad
# revenue; the sponsor commission is then computed on what Kinjy retains, so a
# single advertising dollar never pays commission twice.
CREATOR_SHARE_PCT = _dec("CREATOR_SHARE_PCT", "40")

# Referral pool: members buy units at REFERRAL_POOL_ENTRY_USD each, and
# sign-ups that arrive with no personal invitation are dealt to members in
# proportion to their units (auth-service/referral_pool.py). The cap counts
# units sold; the pool closes to new purchases once it is reached.
REFERRAL_POOL_ENTRY_USD = _dec("REFERRAL_POOL_ENTRY_USD", "100")
REFERRAL_POOL_CAP = _int("REFERRAL_POOL_CAP", 10000)
REFERRAL_POOL_MAX_UNITS_PER_PURCHASE = _int("REFERRAL_POOL_MAX_UNITS_PER_PURCHASE", 100)

# --- Escrow / custody -------------------------------------------------------
# Buyer money is held by an authorised financial institution, not by Kinjy, and
# is released to the seller only once the buyer confirms receipt. If the buyer
# neither confirms nor disputes, it auto-releases after this many days.
CUSTODIAN_PROVIDER = _str("CUSTODIAN_PROVIDER", "mangopay")
CUSTODIAN_NAME = _str("CUSTODIAN_NAME", "Mangopay S.A.")
CUSTODIAN_LICENCE = _str("CUSTODIAN_LICENCE", "Electronic Money Institution (Luxembourg, CSSF)")
ESCROW_AUTO_RELEASE_DAYS = _int("ESCROW_AUTO_RELEASE_DAYS", 7)

# Ebooks sold on the marketplace (PDF and EPUB). The complete file is stored
# privately and handed out only as a short-lived signed link, after the
# buyer's order has been confirmed paid by the server.
EBOOK_MAX_MB = _int("EBOOK_MAX_MB", 100)
# A sample (a free excerpt) is public, so it is kept small.
EBOOK_SAMPLE_MAX_MB = _int("EBOOK_SAMPLE_MAX_MB", 20)
# How long a download link works: 7 days, so a link saved or opened on another
# device still works for a week. Access is checked when the link is made, and a
# buyer can always get a fresh one from their library.
EBOOK_LINK_TTL_S = _int("EBOOK_LINK_TTL_S", 7 * 24 * 3600)
# Asking again for the same book within this window is a retry (an
# interrupted download), not another download: it is not counted.
EBOOK_RETRY_GRACE_S = _int("EBOOK_RETRY_GRACE_S", 30 * 60)
# Link requests per buyer per minute, against scripted abuse only.
EBOOK_LINKS_PER_MINUTE = _int("EBOOK_LINKS_PER_MINUTE", 20)
# Once a dispute is open the seller has this long to answer before the case is
# escalated to Kinjy arbitration on the buyer's side of the record.
DISPUTE_RESPONSE_DAYS = _int("DISPUTE_RESPONSE_DAYS", 3)
DISPUTE_ARBITRATION_DAYS = _int("DISPUTE_ARBITRATION_DAYS", 10)

# The smallest withdrawal, network fee included ($10 since 26/10). Set on the
# server to change it; the withdrawal screen reads it from the API.
PAYOUT_THRESHOLD_USD = _dec("PAYOUT_THRESHOLD_USD", "10.00")
KYC_ANNUAL_FEE_USD = _dec("KYC_ANNUAL_FEE_USD", "10.00")

# --- Payment rails ----------------------------------------------------------
NOWPAYMENTS_API_KEY = _str("NOWPAYMENTS_API_KEY")
NOWPAYMENTS_IPN_SECRET = _str("NOWPAYMENTS_IPN_SECRET")
NOWPAYMENTS_PAYOUT_CURRENCY = _str("NOWPAYMENTS_PAYOUT_CURRENCY", "usdtbsc")
MANGOPAY_CLIENT_ID = _str("MANGOPAY_CLIENT_ID")
MANGOPAY_API_KEY = _str("MANGOPAY_API_KEY")
MANGOPAY_BASE_URL = _str("MANGOPAY_BASE_URL", "https://api.sandbox.mangopay.com")

# Sonco Pay: every payment Kinjy takes, and every commission it pays out.
# Blank key => the old rails (mock in development). The key is an sp_live_ or
# sp_test_ key; a live key only answers from an IP address declared in the
# Sonco Pay dashboard, so the production server's address has to be listed
# there or every call comes back 403.
SONCOPAY_API_KEY = _str("SONCOPAY_API_KEY")
SONCOPAY_WEBHOOK_SECRET = _str("SONCOPAY_WEBHOOK_SECRET")
# Sonco Pay itself unless pointed at a test double. Never left to the SDK: its
# own default is a placeholder host that does not exist.
SONCOPAY_BASE_URL = _str("SONCOPAY_BASE_URL", "https://soncopay.com")
# Commissions are paid in USDT on BNB Smart Chain, to an address the member
# saved and an admin approved.
PAYOUT_ASSET = _str("PAYOUT_ASSET", "USDT")
PAYOUT_NETWORK = _str("PAYOUT_NETWORK", "BSC")
# Who carries Sonco Pay's network fee on a withdrawal: "member" sends the
# balance minus the fee (what most payout services do), "platform" sends the
# full balance and Kinjy pays the fee on top.
PAYOUT_FEE_PAID_BY = _str("PAYOUT_FEE_PAID_BY", "member")
# How long the link in the wallet confirmation e-mail stays valid.
PAYOUT_WALLET_CONFIRM_HOURS = _int("PAYOUT_WALLET_CONFIRM_HOURS", 24)

# Kaluta Pay (kalutagroup.com): takes Kinjy's payments and splits each one -
# what members are owed stays in escrow, the rest goes to Kinjy's address.
# Set the key and every payment goes through it; withdrawals stay on Sonco Pay.
KALUTAPAY_API_KEY = _str("KALUTAPAY_API_KEY")
KALUTAPAY_WEBHOOK_SECRET = _str("KALUTAPAY_WEBHOOK_SECRET")
KALUTAPAY_BASE_URL = _str("KALUTAPAY_BASE_URL", "https://kalutagroup.com")
# The annual KYC fee is Kinjy revenue like any other: the payer's sponsor is
# paid their 20% on it and the Leaders pool its 5%. Before 19/10 it was never
# written to the ledger, so it paid nobody.
KYC_FEE_COMMISSIONABLE = _str("KYC_FEE_COMMISSIONABLE", "1") not in ("0", "false", "no")

# A sponsor earns commissions only while their own identity check is valid.
# Until then (or once it lapses) their share goes to Kinjy, recorded as
# unclaimed. "0" turns the rule off: every sponsor earns, verified or not.
COMMISSION_REQUIRES_KYC = _str("COMMISSION_REQUIRES_KYC", "1") not in ("0", "false", "no")

# Kaluta KYC: the identity check behind KinjyKYC. Blank key => verification
# cannot start, and says so, rather than pretending to.
KALUTA_KYC_API_KEY = _str("KALUTA_KYC_API_KEY")
KALUTA_KYC_BASE_URL = _str("KALUTA_KYC_BASE_URL", "https://kalutakyc.com")
# Signs the webhooks declared on the company in Kaluta's dashboard. Checked when
# present - a wrong signature is refused - but never trusted on its own: the
# session is still read back from Kaluta. Session webhooks come unsigned.
KALUTA_KYC_WEBHOOK_SECRET = _str("KALUTA_KYC_WEBHOOK_SECRET")

# --- AI ---------------------------------------------------------------------
AI_DEFAULT_PROVIDER = _str("AI_DEFAULT_PROVIDER", "mock")

# A picture or a video no model has looked at (none is configured). "text"
# (decided 18/10): rated like its words, and 18+ when the author ticks
# "Adult content" - so a holiday photo is visible to everybody and its link
# can be shared. "restricted": held back from minors and signed-out
# visitors until a human reviews it, which hid every photo post from shared
# links. A minor's media stays "restricted" whatever this says.
UNREVIEWED_MEDIA_RATING = _str("UNREVIEWED_MEDIA_RATING", "text")

# --- Media ------------------------------------------------------------------
MEDIA_ROOT = _str("MEDIA_ROOT", "/data/media")
MEDIA_PUBLIC_BASE = _str("MEDIA_PUBLIC_BASE", "http://localhost:8200/media")
# Avatars and covers only. Blank key or project => local storage (mock mode).
UPLOADCENTER_API_BASE = _str("UPLOADCENTER_API_BASE", "https://api.uploadscenter.com")
UPLOADCENTER_API_KEY = _str("UPLOADCENTER_API_KEY")
UPLOADCENTER_PROJECT_ID = _str("UPLOADCENTER_PROJECT_ID")
# Hosts a stored file URL may point at (comma-separated). Anything else is
# neither redirected to nor fetched.
UPLOADCENTER_CDN_HOSTS = _str("UPLOADCENTER_CDN_HOSTS", "cdn.uploadscenter.com")
# Where new post media is stored: "local" (the media volume) or "uploadcenter"
# (private files there, relayed by media-service after the age check). Anything
# else, or UploadCenter being unconfigured, means local. Files already stored
# stay where they are; each asset records its own provider.
POST_MEDIA_PROVIDER = _str("POST_MEDIA_PROVIDER", "local")

# Who runs the welcome group - the one room every member is in. Set as an
# address rather than an account id: an id means nothing in a settings file and
# is wrong the moment the database is rebuilt, while an address is something an
# operator can read, check and change. Empty leaves the room with no admin,
# which is what it had before anybody was named.
WELCOME_GROUP_ADMIN_EMAIL = _str("WELCOME_GROUP_ADMIN_EMAIL", "godsonferdinand7@gmail.com")
# How long a media ticket lasts (common/mediasign.py). It was a constant five
# minutes, which is shorter than people look at a feed: a tab left open, or a
# picture opened six minutes after the page loaded, asked for a ticket that had
# already run out and got a broken tile. An hour is still short enough that a
# copied URL is a nuisance rather than a permanent address, and a ticket is
# refused outright for anyone signed in as somebody else whatever its age.
MEDIA_TICKET_TTL_SECONDS = _int("MEDIA_TICKET_TTL_SECONDS", 3600)

# --- Languages (platform-wide) ---------------------------------------------
SUPPORTED_LANGS = ["en", "sw", "fr", "ar", "zh"]
DEFAULT_LANG = "en"
RTL_LANGS = {"ar"}
