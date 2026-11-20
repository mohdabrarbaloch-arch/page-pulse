"""Page management, analysis and import endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Body, HTTPException, Query, Response, status
from sqlalchemy import func, select

from app.deps import CurrentUser, DbSession
from app.models import DataSource, ImportRecord, Page, Post
from app.schemas import (
    AnalysisOut,
    CreateDemoPageRequest,
    CreateLivePageRequest,
    DemoPageOut,
    ImportRecordOut,
    ImportResultOut,
    PageListOut,
    PageOut,
)
from app.services import graph_api
from app.services.demo_data import available_demo_pages
from app.services.pages import (
    REPORTING_TZ_LABEL,
    PageAlreadyExistsError,
    PageNotFoundError,
    analyse_stored_page,
    create_page_from_demo,
    create_page_from_graph,
    get_page_for_user,
    import_posts_json,
    list_pages,
)

router = APIRouter(prefix="/api/pages", tags=["pages"])

_SOURCE_NOTES = {
    DataSource.DEMO: (
        "This page is running on generated sample data. Every number on this "
        "dashboard is synthetic. Add a Facebook access token and connect a real "
        "page to see live figures."
    ),
    DataSource.LIVE: (
        "Figures were pulled from the Facebook Graph API. Fields the API did not "
        "return are shown as unavailable rather than as zero."
    ),
    DataSource.IMPORT: (
        "Figures come from a file you imported. The dashboard analyses exactly "
        "what was in that file."
    ),
}


def _page_out(page: Page, post_count: int = 0) -> PageOut:
    return PageOut(
        id=page.id,
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
        source=page.source.value if isinstance(page.source, DataSource) else str(page.source),
        last_synced_at=page.last_synced_at,
        created_at=page.created_at,
        post_count=post_count,
    )


@router.get("/demo", response_model=list[DemoPageOut])
def list_demo_pages() -> list[DemoPageOut]:
    """The built-in sample pages, so the dashboard can be explored without a token."""
    return [DemoPageOut(**entry) for entry in available_demo_pages()]


@router.get("", response_model=PageListOut)
def get_pages(user: CurrentUser, db: DbSession) -> PageListOut:
    """Every page this account has added."""
    pages = list_pages(db, user)

    counts: dict[int, int] = {}
    if pages:
        rows = db.execute(
            select(Post.page_id, func.count(Post.id))
            .where(Post.page_id.in_([p.id for p in pages]))
            .group_by(Post.page_id)
        ).all()
        counts = dict(rows)

    return PageListOut(
        pages=[_page_out(p, counts.get(p.id, 0)) for p in pages],
        total=len(pages),
    )


@router.post("/demo", response_model=PageOut, status_code=status.HTTP_201_CREATED)
def add_demo_page(payload: CreateDemoPageRequest, user: CurrentUser, db: DbSession) -> PageOut:
    """Add one of the built-in sample pages."""
    try:
        page = create_page_from_demo(db, user, payload.slug, days=payload.days)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No demo page with slug {payload.slug!r}. See GET /api/pages/demo.",
        ) from exc
    except PageAlreadyExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return _page_out(page, len(page.posts))


@router.post("/live", response_model=PageOut, status_code=status.HTTP_201_CREATED)
def add_live_page(payload: CreateLivePageRequest, user: CurrentUser, db: DbSession) -> PageOut:
    """Connect a real Facebook page using the configured access token."""
    if not graph_api.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No FB_GRAPH_TOKEN is configured, so live Facebook data cannot be "
                "fetched. Add a page access token to .env and restart, or use the "
                "demo dataset at POST /api/pages/demo."
            ),
        )

    try:
        page = create_page_from_graph(db, user, payload.external_id, days=payload.days)
    except graph_api.GraphNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except graph_api.GraphPermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except graph_api.GraphError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except PageAlreadyExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return _page_out(page, len(page.posts))


@router.get("/{page_id}", response_model=PageOut)
def get_page(page_id: int, user: CurrentUser, db: DbSession) -> PageOut:
    """One page's profile."""
    try:
        page = get_page_for_user(db, page_id, user)
    except PageNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _page_out(page, len(page.posts))


@router.delete(
    "/{page_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_page(page_id: int, user: CurrentUser, db: DbSession) -> Response:
    """Remove a page and everything collected for it."""
    try:
        page = get_page_for_user(db, page_id, user)
    except PageNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    db.delete(page)
    db.commit()
    # 204 must carry no body, so return an empty Response rather than None.
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{page_id}/analysis", response_model=AnalysisOut)
def get_analysis(
    page_id: int,
    user: CurrentUser,
    db: DbSession,
    days: int = Query(default=180, ge=7, le=730, description="Analysis window in days."),
) -> AnalysisOut:
    """The full analysis for a page: metrics, insights and the timeline."""
    try:
        page = get_page_for_user(db, page_id, user)
    except PageNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    analysis = analyse_stored_page(db, page, days=days)

    # Daily timeline for the chart. Built from the same window the metrics used,
    # so the chart and the numbers above it cannot disagree.
    now = datetime.now(UTC)
    window_start = now - timedelta(days=days)
    buckets: dict[str, dict[str, int]] = {}
    for offset in range(days + 1):
        day = (window_start + timedelta(days=offset)).date().isoformat()
        buckets[day] = {"posts": 0, "engagement": 0}

    for post in page.posts:
        posted_at = post.posted_at
        # SQLite hands back naive datetimes; normalise before comparing.
        if posted_at.tzinfo is None:
            posted_at = posted_at.replace(tzinfo=UTC)
        if posted_at < window_start:
            continue
        key = posted_at.date().isoformat()
        if key in buckets:
            buckets[key]["posts"] += 1
            buckets[key]["engagement"] += post.total_engagement

    follower_by_day: dict[str, int] = {}
    for snapshot in sorted(page.snapshots, key=lambda s: s.captured_at):
        captured = snapshot.captured_at
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=UTC)
        follower_by_day[captured.date().isoformat()] = snapshot.followers

    timeline = [
        {
            "date": day,
            "posts": values["posts"],
            "engagement": values["engagement"],
            "followers": follower_by_day.get(day),
        }
        for day, values in sorted(buckets.items())
    ]

    source = page.source if isinstance(page.source, DataSource) else DataSource.DEMO

    return AnalysisOut(
        page=_page_out(page, len(page.posts)),
        generated_at=analysis.generated_at,
        window_days=analysis.window_days,
        reporting_timezone=REPORTING_TZ_LABEL,
        post_count=analysis.post_count,
        engagement=analysis.engagement,  # type: ignore[arg-type]
        cadence=analysis.cadence,  # type: ignore[arg-type]
        media_mix=analysis.media_mix,  # type: ignore[arg-type]
        best_slots=analysis.best_slots,  # type: ignore[arg-type]
        growth=analysis.growth,  # type: ignore[arg-type]
        top_posts=analysis.top_posts,  # type: ignore[arg-type]
        weakest_posts=analysis.weakest_posts,  # type: ignore[arg-type]
        video=analysis.video,  # type: ignore[arg-type]
        insights=analysis.insights,  # type: ignore[arg-type]
        follower_ratio=analysis.follower_ratio,
        talking_about_rate_percent=analysis.talking_about_rate_percent,
        timeline=timeline,  # type: ignore[arg-type]
        data_source=source.value,
        data_source_note=_SOURCE_NOTES.get(source, ""),
    )


@router.post("/{page_id}/import", response_model=ImportResultOut)
def import_posts(
    page_id: int,
    user: CurrentUser,
    db: DbSession,
    payload: list[dict] = Body(
        ...,
        description=(
            "A JSON array of posts. Each needs at least 'id' and a date "
            "('created_time' or 'posted_at'). Optional: message, media_type, "
            "likes, comments, shares, video_views, reach, watch_seconds, permalink."
        ),
    ),
) -> ImportResultOut:
    """Import posts from JSON, so the dashboard works on exported data too."""
    try:
        page = get_page_for_user(db, page_id, user)
    except PageNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The import payload was empty. Send a JSON array with at least one post.",
        )

    if len(payload) > 5000:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Imports are limited to 5000 posts per request.",
        )

    imported, skipped, warnings = import_posts_json(db, page, payload)

    if imported == 0 and skipped == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No usable rows were found. Each post needs an 'id' and a date.",
        )

    return ImportResultOut(imported=imported, skipped=skipped, warnings=warnings)


@router.get("/{page_id}/imports", response_model=list[ImportRecordOut])
def list_imports(page_id: int, user: CurrentUser, db: DbSession) -> list[ImportRecordOut]:
    """The import history for a page, including what was skipped and why."""
    try:
        page = get_page_for_user(db, page_id, user)
    except PageNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    rows = db.scalars(
        select(ImportRecord)
        .where(ImportRecord.page_id == page.id)
        .order_by(ImportRecord.created_at.desc())
        .limit(50)
    ).all()
    return [ImportRecordOut.model_validate(row) for row in rows]
