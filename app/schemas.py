"""Request and response schemas.

Response models are explicit rather than ``dict`` so the OpenAPI schema is real
and the frontend has a contract to code against.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    display_name: str = Field(default="", max_length=120)

    @field_validator("password")
    @classmethod
    def _password_bytes(cls, value: str) -> str:
        """bcrypt truncates past 72 bytes; reject rather than silently truncate."""
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 bytes when UTF-8 encoded.")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str
    is_admin: bool
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserOut


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


class DemoPageOut(BaseModel):
    slug: str
    name: str
    category: str
    followers: int


class CreateDemoPageRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    days: int = Field(default=180, ge=7, le=730)


class CreateLivePageRequest(BaseModel):
    external_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    days: int = Field(default=180, ge=7, le=730)


class PageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_id: str
    name: str
    category: str
    description: str
    website: str
    phone: str
    address: str
    cover_url: str
    followers: int
    likes: int
    talking_about: int
    is_verified: bool
    source: str
    last_synced_at: datetime | None
    created_at: datetime
    post_count: int = 0


class PageListOut(BaseModel):
    pages: list[PageOut]
    total: int


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


class EngagementOut(BaseModel):
    # The analytics engine returns frozen dataclasses, not dicts. from_attributes
    # lets Pydantic read them by attribute name, which keeps the engine free of
    # any dependency on the API layer.
    model_config = ConfigDict(from_attributes=True)

    post_count: int
    total_engagement: int
    median_engagement: float
    mean_engagement: float
    rate_percent: float | None
    basis: str
    posts_with_basis_data: int
    total_posts_considered: int
    basis_share_percent: float | None
    explained: str


class CadenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    posts_per_week: float
    window_days: int
    post_count: int
    longest_gap_days: float | None
    median_gap_days: float | None
    consistency: str


class MediaPerformanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    media_type: str
    post_count: int
    median_engagement: float
    share_of_posts_percent: float
    share_of_engagement_percent: float
    median_engagement_rate_percent: float | None
    confidence: str
    outperforms_share: bool


class TimeSlotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    weekday: int
    weekday_label: str
    hour_start: int
    hour_end: int
    label: str
    post_count: int
    median_engagement: float
    confidence: str


class GrowthOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start_followers: int | None
    current_followers: int
    absolute_change: int | None
    percent_change: float | None
    window_days: float | None
    per_week: float | None
    note: str


class PostHighlightOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    external_id: str
    excerpt: str
    media_type: str
    posted_at: datetime
    engagement: int
    likes: int
    comments: int
    shares: int
    engagement_rate_percent: float | None
    permalink: str


class VideoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    videos_analysed: int
    median_views: float
    median_watch_seconds_per_view: float | None
    total_views: int


class InsightOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    severity: str
    title: str
    body: str
    evidence: dict[str, Any]


class TimelinePoint(BaseModel):
    date: str
    posts: int
    engagement: int
    followers: int | None


class AnalysisOut(BaseModel):
    page: PageOut
    generated_at: datetime
    window_days: int
    reporting_timezone: str
    post_count: int
    engagement: EngagementOut
    cadence: CadenceOut
    media_mix: list[MediaPerformanceOut]
    best_slots: list[TimeSlotOut]
    growth: GrowthOut
    top_posts: list[PostHighlightOut]
    weakest_posts: list[PostHighlightOut]
    video: VideoOut | None
    insights: list[InsightOut]
    follower_ratio: float | None
    talking_about_rate_percent: float | None
    timeline: list[TimelinePoint]
    data_source: str
    data_source_note: str


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


class ImportResultOut(BaseModel):
    imported: int
    skipped: int
    warnings: list[str]


class ImportRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    posts_imported: int
    posts_skipped: int
    detail: dict[str, Any]
    created_at: datetime


class HealthOut(BaseModel):
    status: Literal["ok"]
    version: str
    graph_configured: bool
    environment: str
