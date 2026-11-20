"""Persistence and analysis orchestration.

This is the only place that knows about both the ORM and the analytics engine.
The analytics module stays pure; this module does the loading and saving.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DataSource, ImportRecord, MetricSnapshot, Page, Post, User
from app.services import graph_api
from app.services.analytics import (
    FollowerSnapshot,
    PageAnalysis,
    PageRecord,
    PostRecord,
    analyse_page,
)
from app.services.demo_data import generate_demo_page

# The dashboard reports in the page owner's local time, not UTC. Posting-time
# analysis in UTC would put a 9pm Karachi post at 4pm and produce advice that is
# wrong by five hours.
REPORTING_TZ = timezone(timedelta(hours=5))
REPORTING_TZ_LABEL = "Asia/Karachi (UTC+5)"


class PageNotFoundError(LookupError):
    """The page does not exist, or does not belong to this user."""


class PageAlreadyExistsError(ValueError):
    """This user has already added this page.

    Checked before the insert rather than caught from the database, so the API
    can answer 409 instead of surfacing a constraint violation as a 500.
    """


def _as_utc(moment: datetime | None) -> datetime | None:
    """Attach UTC to a naive datetime.

    SQLite has no native timezone type, so a value written as timezone-aware
    comes back naive. Comparing that against an aware datetime raises
    TypeError, so every datetime is normalised at the read boundary rather than
    trusting the driver to preserve the offset.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _to_post_record(post: Post) -> PostRecord:
    return PostRecord(
        external_id=post.external_id,
        message=post.message,
        media_type=post.media_type,
        posted_at=_as_utc(post.posted_at) or datetime.now(UTC),
        likes=post.likes,
        comments=post.comments,
        shares=post.shares,
        video_views=post.video_views,
        reach=post.reach,
        impressions=post.impressions,
        watch_seconds=post.watch_seconds,
        permalink=post.permalink,
        thumbnail_url=post.thumbnail_url,
    )


def _to_page_record(page: Page) -> PageRecord:
    return PageRecord(
        external_id=page.external_id,
        name=page.name,
        category=page.category,
        description=page.description,
        website=page.website,
        phone=page.phone,
        address=page.address,
        cover_url=page.cover_url,
        followers=page.followers,
        likes=page.likes,
        talking_about=page.talking_about,
        is_verified=page.is_verified,
        baseline_followers=page.baseline_followers,
    )


def get_page_for_user(db: Session, page_id: int, user: User) -> Page:
    """Load a page, enforcing ownership.

    Returns PageNotFoundError rather than a permission error for someone else's
    page, so the API does not confirm that a page id exists.
    """
    page = db.get(Page, page_id)
    if page is None or page.owner_id != user.id:
        raise PageNotFoundError(f"Page {page_id} not found.")
    return page


def list_pages(db: Session, user: User) -> list[Page]:
    """All pages belonging to a user, newest first."""
    stmt = select(Page).where(Page.owner_id == user.id).order_by(Page.created_at.desc())
    return list(db.scalars(stmt).all())


def analyse_stored_page(
    db: Session,
    page: Page,
    *,
    days: int = 180,
    now: datetime | None = None,
) -> PageAnalysis:
    """Run the analytics engine over a stored page.

    The window is split in half so the current period can be compared against the
    previous one. Without that split, "engagement is up" is a claim with nothing
    behind it.
    """
    now = now or datetime.now(UTC)
    window_start = now - timedelta(days=days)
    midpoint = now - timedelta(days=days / 2)

    all_posts = [_to_post_record(p) for p in page.posts]

    current = [p for p in all_posts if p.posted_at >= midpoint]
    previous = [p for p in all_posts if window_start <= p.posted_at < midpoint]

    snapshots = [
        FollowerSnapshot(
            captured_at=_as_utc(s.captured_at) or datetime.now(UTC),
            followers=s.followers,
            likes=s.likes,
            talking_about=s.talking_about,
        )
        for s in page.snapshots
        if (_as_utc(s.captured_at) or datetime.now(UTC)) >= window_start
    ]

    return analyse_page(
        page=_to_page_record(page),
        posts=current,
        snapshots=snapshots,
        previous_posts=previous,
        tz=REPORTING_TZ,
        window_days=days,
        now=now,
    )


def _upsert_posts(db: Session, page: Page, records: list[PostRecord]) -> tuple[int, int]:
    """Insert new posts, update existing ones. Returns (imported, skipped)."""
    existing = {p.external_id: p for p in page.posts}
    imported = 0
    skipped = 0

    for record in records:
        if not record.external_id:
            skipped += 1
            continue

        current = existing.get(record.external_id)
        if current is None:
            db.add(
                Post(
                    page_id=page.id,
                    external_id=record.external_id,
                    message=record.message,
                    media_type=record.media_type,
                    posted_at=record.posted_at,
                    likes=record.likes,
                    comments=record.comments,
                    shares=record.shares,
                    video_views=record.video_views,
                    reach=record.reach,
                    impressions=record.impressions,
                    watch_seconds=record.watch_seconds,
                    permalink=record.permalink,
                    thumbnail_url=record.thumbnail_url,
                )
            )
            imported += 1
        else:
            # Counters move. Refresh them rather than treating the post as new.
            current.likes = record.likes
            current.comments = record.comments
            current.shares = record.shares
            current.video_views = record.video_views
            current.reach = record.reach
            current.watch_seconds = record.watch_seconds
            current.message = record.message or current.message
            skipped += 1

    return imported, skipped


def create_page_from_demo(db: Session, user: User, slug: str, *, days: int = 180) -> Page:
    """Create a page from the labelled demo dataset."""
    bundle = generate_demo_page(slug, days=days)

    existing = db.scalar(
        select(Page).where(Page.owner_id == user.id, Page.external_id == bundle.page.external_id)
    )
    if existing is not None:
        raise PageAlreadyExistsError(
            f"You have already added {bundle.page.name!r}. Delete it first to "
            "re-import, or open it from your page list."
        )

    page = Page(
        owner_id=user.id,
        external_id=bundle.page.external_id,
        name=bundle.page.name,
        category=bundle.page.category,
        description=bundle.page.description,
        website=bundle.page.website,
        phone=bundle.page.phone,
        address=bundle.page.address,
        cover_url=bundle.page.cover_url,
        followers=bundle.page.followers,
        likes=bundle.page.likes,
        talking_about=bundle.page.talking_about,
        is_verified=bundle.page.is_verified,
        baseline_followers=bundle.page.baseline_followers,
        source=DataSource.DEMO,
        last_synced_at=datetime.now(UTC),
    )
    db.add(page)
    db.flush()

    for record in bundle.posts:
        db.add(
            Post(
                page_id=page.id,
                external_id=record.external_id,
                message=record.message,
                media_type=record.media_type,
                posted_at=record.posted_at,
                likes=record.likes,
                comments=record.comments,
                shares=record.shares,
                video_views=record.video_views,
                reach=record.reach,
                watch_seconds=record.watch_seconds,
                permalink=record.permalink,
            )
        )

    for snapshot in bundle.snapshots:
        db.add(
            MetricSnapshot(
                page_id=page.id,
                captured_at=snapshot.captured_at,
                followers=snapshot.followers,
                likes=snapshot.likes,
                talking_about=snapshot.talking_about,
            )
        )

    db.add(
        ImportRecord(
            page_id=page.id,
            source="demo",
            posts_imported=len(bundle.posts),
            posts_skipped=0,
            detail={"slug": slug, "days": days, "note": "Generated sample data."},
        )
    )

    db.commit()
    db.refresh(page)
    return page


def create_page_from_graph(db: Session, user: User, external_id: str, *, days: int = 180) -> Page:
    """Create or refresh a page from the live Graph API."""
    bundle = graph_api.fetch_page(external_id, days=days)

    existing = db.scalar(
        select(Page).where(Page.owner_id == user.id, Page.external_id == bundle.page.external_id)
    )

    if existing is None:
        page = Page(
            owner_id=user.id,
            external_id=bundle.page.external_id,
            name=bundle.page.name,
            category=bundle.page.category,
            description=bundle.page.description,
            website=bundle.page.website,
            phone=bundle.page.phone,
            address=bundle.page.address,
            cover_url=bundle.page.cover_url,
            followers=bundle.page.followers,
            likes=bundle.page.likes,
            talking_about=bundle.page.talking_about,
            is_verified=bundle.page.is_verified,
            baseline_followers=bundle.page.followers,
            source=DataSource.LIVE,
            last_synced_at=bundle.fetched_at,
        )
        db.add(page)
        db.flush()
    else:
        page = existing
        page.name = bundle.page.name
        page.category = bundle.page.category
        page.description = bundle.page.description
        page.website = bundle.page.website
        page.phone = bundle.page.phone
        page.address = bundle.page.address
        page.cover_url = bundle.page.cover_url
        page.followers = bundle.page.followers
        page.likes = bundle.page.likes
        page.talking_about = bundle.page.talking_about
        page.is_verified = bundle.page.is_verified
        page.source = DataSource.LIVE
        page.last_synced_at = bundle.fetched_at
        if page.baseline_followers == 0:
            page.baseline_followers = bundle.page.followers

    imported, skipped = _upsert_posts(db, page, bundle.posts)

    for snapshot in bundle.snapshots:
        db.add(
            MetricSnapshot(
                page_id=page.id,
                captured_at=snapshot.captured_at,
                followers=snapshot.followers,
                likes=snapshot.likes,
                talking_about=snapshot.talking_about,
            )
        )

    db.add(
        ImportRecord(
            page_id=page.id,
            source="live",
            posts_imported=imported,
            posts_skipped=skipped,
            detail={"notes": bundle.notes, "truncated": bundle.posts_truncated},
        )
    )

    db.commit()
    db.refresh(page)
    return page


def _int(row: dict, key: str) -> int:
    """Read an int from a row, defaulting to 0.

    Takes the row as an argument rather than closing over it: a closure defined
    inside the loop would capture the loop variable by reference, which works
    only as long as it is called in the same iteration.
    """
    value = row.get(key)
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _opt_int(row: dict, key: str) -> int | None:
    """Read an optional int from a row, returning None when absent or invalid."""
    value = row.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def import_posts_json(
    db: Session, page: Page, payload: list[dict[str, Any]]
) -> tuple[int, int, list[str]]:
    """Import posts from a user-supplied JSON array.

    Returns (imported, skipped, warnings). Malformed rows are skipped with a
    warning rather than aborting the whole import, because a 200-row file with
    one bad row should not be a total loss.
    """
    warnings: list[str] = []
    records: list[PostRecord] = []

    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            warnings.append(f"Row {index}: not an object, skipped.")
            continue

        external_id = str(row.get("id") or row.get("external_id") or "").strip()
        if not external_id:
            warnings.append(f"Row {index}: missing 'id', skipped.")
            continue

        raw_time = row.get("created_time") or row.get("posted_at")
        posted_at: datetime | None = None
        if isinstance(raw_time, str):
            try:
                posted_at = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            except ValueError:
                warnings.append(f"Row {index}: unparseable date {raw_time!r}, skipped.")
                continue
        if posted_at is None:
            warnings.append(f"Row {index}: missing date, skipped.")
            continue
        if posted_at.tzinfo is None:
            posted_at = posted_at.replace(tzinfo=UTC)

        records.append(
            PostRecord(
                external_id=external_id,
                message=str(row.get("message") or ""),
                media_type=str(row.get("media_type") or "status"),
                posted_at=posted_at,
                likes=_int(row, "likes"),
                comments=_int(row, "comments"),
                shares=_int(row, "shares"),
                video_views=_opt_int(row, "video_views"),
                reach=_opt_int(row, "reach"),
                watch_seconds=(
                    float(row["watch_seconds"]) if row.get("watch_seconds") is not None else None
                ),
                permalink=str(row.get("permalink") or ""),
            )
        )

    imported, skipped = _upsert_posts(db, page, records)

    db.add(
        ImportRecord(
            page_id=page.id,
            source="import",
            posts_imported=imported,
            posts_skipped=skipped,
            detail={"warnings": warnings[:50], "rows_received": len(payload)},
        )
    )
    db.commit()
    return imported, skipped, warnings
