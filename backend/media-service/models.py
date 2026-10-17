from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from common.database import Base
from common.ids import new_id

SCHEMA = "media"


class Asset(Base):
    """An uploaded file plus its provenance record.

    Provenance is set at upload and travels with every derivative. C2PA content
    credentials go in ``c2pa_manifest`` once signing is wired up (B5); until then
    the label is declared rather than cryptographically proven, and
    ``provenance_signed`` says which of the two it is — so the UI never shows a
    trust badge the platform cannot back.
    """

    __tablename__ = "assets"
    __table_args__ = {"schema": SCHEMA}

    PROVENANCE = ("original", "edited", "ai_assisted", "ai_generated", "verified_source")

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("mda"))
    owner_id: Mapped[str] = mapped_column(String(40), index=True)
    # Text, not String(255): a sealed name is longer than the name it hides.
    filename: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_path: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(20))  # image|video|audio|document

    # public     - avatars, marketing art: served to anyone, no ticket.
    # restricted - anything attached to a post, i.e. anything that can carry an
    #              age rating. Served only against a short-lived signed ticket
    #              minted by a service that has already run the age check.
    #
    # Assets are marked restricted server-side when they are attached to a
    # post, not declared by the uploader: a client that could label its own
    # media "public" would be the age gate.
    access: Mapped[str] = mapped_column(String(20), default="public", index=True)

    provenance: Mapped[str] = mapped_column(String(20), default="original")
    provenance_signed: Mapped[bool] = mapped_column(Boolean, default=False)
    c2pa_manifest: Mapped[str | None] = mapped_column(Text)
    derived_from: Mapped[str | None] = mapped_column(String(40), index=True)

    # Where the bytes live. "local" is the media volume; "uploadcenter" holds
    # avatars and covers, with the file's id there in external_id and its CDN
    # address in url. Post media stays local: its signed-ticket age gate would be
    # bypassed by a public CDN URL.
    provider: Mapped[str] = mapped_column(String(20), default="local")
    external_id: Mapped[str | None] = mapped_column(String(80))
    # avatar | cover for profile images, null for everything else.
    purpose: Mapped[str | None] = mapped_column(String(20))
    # pending (upload URL issued) -> uploading (one PUT claimed it, bytes being
    # stored) -> processing (stored, being checked) -> ready | failed. Only a
    # ready asset may be put on a profile.
    status: Mapped[str] = mapped_column(String(20), default="ready")
    # How often complete has asked UploadCenter whether this file is ready, and
    # when it last did: the throttle on a client polling in a loop.
    status_checks: Mapped[int] = mapped_column(Integer, default=0)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    alt_text: Mapped[str | None] = mapped_column(String(500))

    # The picture's shape, read from its header at upload (imagesize.py).
    # The feed reserves a box from it before the bytes arrive, so the card does
    # not open at a placeholder height and jump when the image lands. Null for
    # everything uploaded before this existed, for video and audio, and for any
    # header that would not parse - all of which fall back to being measured in
    # the browser.
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)

    # A display-sized copy on the media volume (preview.py). The original is
    # whole and untouched wherever it lives; this is what a feed is served.
    # Null means there is none and the original is sent, which is what every
    # picture did before this existed.
    preview_path: Mapped[str | None] = mapped_column(String(500))
    preview_bytes: Mapped[int | None] = mapped_column(BigInteger)
    # Usually WebP, but a copy kept only to put the bytes on this disk keeps
    # the format it arrived in, so the type has to be recorded rather than
    # assumed.
    preview_type: Mapped[str | None] = mapped_column(String(100))
    # A video's cover picture (poster.py), on the media volume. Null for
    # anything else, and for a video ffmpeg could not read.
    poster_path: Mapped[str | None] = mapped_column(String(500))

    # A chat attachment: served only on a link signed by the service that knows
    # who may see it, never on its bare URL.
    private: Mapped[bool] = mapped_column(Boolean, default=False)
    # The key id the bytes on disk (and the file name) are sealed with; None
    # means stored as uploaded. See common/crypto.py.
    sealed_with: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
