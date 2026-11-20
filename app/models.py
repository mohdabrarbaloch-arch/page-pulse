"""ORM models for pages, posts and analysis snapshots."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    """Timezone-aware UTC now. Naive datetimes are a bug waiting to happen."""
    return datetime.now(UTC)


class DataSource(str, enum.Enum):
    """Where a page's data came from. Surfaced in the UI, never hidden."""

    LIVE = "live"  # pulled from the Facebook Graph API
    DEMO = "demo"  # generated sample data, labelled as such
    IMPORT = "import"  # uploaded by the user as JSON/CSV


class User(Base):
    """A dashboard account. Each user only sees the pages they added."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True
    )

    pages: Mapped[list[Page]] = relationship(
        back_populates="owner", cascade="all, delete-orphan", lazy="selectin"
    )


class Page(Base):
    """A Facebook page plus the snapshot data collected for it."""

    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("owner_id", "external_id", name="uq_pages_owner_external"),
        Index("ix_pages_owner_created", "owner_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Facebook's page id. For demo data this is a synthetic id, clearly marked.
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    website: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    address: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    cover_url: Mapped[str] = mapped_column(String(1000), default="", nullable=False)

    followers: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    likes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    talking_about: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_on_facebook: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True
    )

    source: Mapped[DataSource] = mapped_column(
        Enum(DataSource, native_enum=False, length=16),
        default=DataSource.DEMO,
        nullable=False,
    )
    # How many followers the page had when first seen, so growth has a baseline.
    baseline_followers: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    owner: Mapped[User] = relationship(back_populates="pages")
    posts: Mapped[list[Post]] = relationship(
        back_populates="page",
        cascade="all, delete-orphan",
        order_by="Post.posted_at.desc()",
        lazy="selectin",
    )
    snapshots: Mapped[list[MetricSnapshot]] = relationship(
        back_populates="page",
        cascade="all, delete-orphan",
        order_by="MetricSnapshot.captured_at",
        lazy="selectin",
    )


class Post(Base):
    """A single page post, with its performance counters."""

    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint("page_id", "external_id", name="uq_posts_page_external"),
        Index("ix_posts_page_posted", "page_id", "posted_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_id: Mapped[int] = mapped_column(
        ForeignKey("pages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(120), nullable=False)

    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # reel | video | photo | link | status | carousel
    media_type: Mapped[str] = mapped_column(String(20), default="status", nullable=False)
    permalink: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    thumbnail_url: Mapped[str] = mapped_column(String(1000), default="", nullable=False)

    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    likes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    comments: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    shares: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Video/reel only. Null means "not applicable", not zero.
    video_views: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True)
    reach: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True)
    impressions: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True)
    # Total watch time in seconds, used to derive average watch duration.
    watch_seconds: Mapped[float | None] = mapped_column(Float, default=None, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    page: Mapped[Page] = relationship(back_populates="posts")

    @property
    def total_engagement(self) -> int:
        """Likes + comments + shares. The denominator for engagement rate."""
        return int(self.likes or 0) + int(self.comments or 0) + int(self.shares or 0)


class MetricSnapshot(Base):
    """A point-in-time follower/like reading, used for the growth chart."""

    __tablename__ = "metric_snapshots"
    __table_args__ = (Index("ix_snapshots_page_captured", "page_id", "captured_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_id: Mapped[int] = mapped_column(
        ForeignKey("pages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    followers: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    likes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    talking_about: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    page: Mapped[Page] = relationship(back_populates="snapshots")


class ImportRecord(Base):
    """Audit trail of every data import, including what failed and why."""

    __tablename__ = "import_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_id: Mapped[int] = mapped_column(
        ForeignKey("pages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    posts_imported: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    posts_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
