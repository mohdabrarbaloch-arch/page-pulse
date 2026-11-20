"""Facebook Graph API adapter.

Two rules govern this module:

1. **No token, no live data.** If ``FB_GRAPH_TOKEN`` is unset the caller gets a
   clear :class:`GraphNotConfigured` and the API surfaces that to the UI. It never
   silently falls back to fabricated numbers.
2. **A field we could not read is ``None``, not zero.** Graph omits ``shares`` on
   most post types and ``video_views`` on anything that is not a video. Writing
   ``0`` there would make a photo look like a video that nobody watched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import get_settings
from app.services.analytics import FollowerSnapshot, PageRecord, PostRecord

logger = logging.getLogger(__name__)

_settings = get_settings()

PAGE_FIELDS = (
    "id,name,category,description,website,phone,location,cover,"
    "followers_count,fan_count,is_verified,link,talking_about_count"
)

POST_FIELDS = (
    "id,message,created_time,permalink_url,full_picture,"
    "shares,reactions.summary(total_count),comments.summary(total_count),"
    "attachments{media_type,type}"
)

# Graph reports attachment types, not our vocabulary. Map them explicitly so an
# unknown type stays unknown rather than being guessed into a category.
_MEDIA_MAP = {
    "video_inline": "video",
    "video": "video",
    "reel": "reel",
    "photo": "photo",
    "album": "photo",
    "share": "link",
    "link": "link",
    "native_templates": "photo",
    "sticker": "photo",
    "status": "status",
}


class GraphError(RuntimeError):
    """A Graph API call failed."""


class GraphNotConfigured(GraphError):
    """No access token is configured, so live data is unavailable."""


class GraphPermissionError(GraphError):
    """The token is valid but the page is not accessible with it."""


@dataclass(frozen=True, slots=True)
class GraphBundle:
    """Live page data in the same shape the analytics engine consumes."""

    page: PageRecord
    posts: list[PostRecord]
    snapshots: list[FollowerSnapshot]
    fetched_at: datetime
    posts_truncated: bool
    notes: list[str]


def is_configured() -> bool:
    """Whether a Graph access token is present."""
    return bool(_settings.fb_graph_token.strip())


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=f"https://graph.facebook.com/{_settings.fb_graph_version}",
        timeout=_settings.fb_graph_timeout_seconds,
        headers={"Accept": "application/json"},
    )


def _get(client: httpx.Client, path: str, params: dict[str, Any]) -> dict[str, Any]:
    """GET a Graph path, translating errors into our own exception types."""
    payload = {**params, "access_token": _settings.fb_graph_token}
    try:
        response = client.get(path, params=payload)
    except httpx.HTTPError as exc:
        raise GraphError(f"Could not reach the Facebook Graph API: {exc}") from exc

    if response.status_code == 401:
        raise GraphPermissionError(
            "Facebook rejected the access token (401). It may have expired, or it "
            "may not be a token for this page."
        )

    if response.status_code == 403:
        detail = _error_message(response)
        raise GraphPermissionError(
            f"Facebook refused access (403): {detail}. The token needs the "
            "pages_read_engagement and pages_show_list permissions, and the page "
            "must be granted to the app."
        )

    if response.status_code == 400:
        raise GraphError(f"Facebook rejected the request (400): {_error_message(response)}")

    if response.status_code >= 400:
        raise GraphError(
            f"Facebook returned HTTP {response.status_code}: {_error_message(response)}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise GraphError("Facebook returned a response that was not JSON.") from exc


def _error_message(response: httpx.Response) -> str:
    """Extract Graph's own error message, which is far more useful than the code."""
    try:
        body = response.json()
        return str(body.get("error", {}).get("message", "no detail given"))
    except ValueError:
        return "no detail given"


def _parse_time(value: str | None) -> datetime | None:
    """Parse Graph's ISO-8601 timestamps, which end in +0000."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("+0000", "+00:00"))
    except ValueError:
        logger.warning("Could not parse Graph timestamp: %r", value)
        return None


def _media_type(raw: dict[str, Any]) -> str:
    """Determine the post's media type without guessing."""
    attachments = raw.get("attachments", {}).get("data") or []
    if attachments:
        attachment = attachments[0]
        media = attachment.get("media_type") or attachment.get("type") or ""
        mapped = _MEDIA_MAP.get(str(media))
        if mapped:
            return mapped
    # A post with a photo in full_picture and no attachment metadata is a photo.
    if raw.get("full_picture"):
        return "photo"
    return "status"


def fetch_page(
    external_id: str,
    *,
    days: int = 180,
    post_limit: int = 100,
) -> GraphBundle:
    """Pull a page and its recent posts from the Graph API.

    ``post_limit`` caps how many posts one call returns. Graph pages the results;
    we follow ``paging.next`` until the cap, so a very active page does not get a
    single truncated batch presented as complete history.
    """
    if not is_configured():
        raise GraphNotConfigured(
            "No FB_GRAPH_TOKEN is configured, so live Facebook data cannot be "
            "fetched. Add a page access token to .env, or use the demo dataset."
        )

    notes: list[str] = []
    now = datetime.now(UTC)

    with _client() as client:
        page_payload = _get(client, f"/{external_id}", {"fields": PAGE_FIELDS})

        posts: list[PostRecord] = []
        truncated = False
        params: dict[str, Any] = {"fields": POST_FIELDS, "limit": 100}
        path = f"/{external_id}/posts"
        page_count = 0

        while path and len(posts) < post_limit:
            payload = _get(client, path, params if page_count == 0 else {})
            page_count += 1

            for raw in payload.get("data", []):
                posted_at = _parse_time(raw.get("created_time"))
                if posted_at is None:
                    continue
                # Graph returns posts newest-first; stop once past the window.
                if (now - posted_at).days > days:
                    truncated = True
                    break

                reactions = raw.get("reactions", {}).get("summary", {}).get("total_count", 0)
                comments = raw.get("comments", {}).get("summary", {}).get("total_count", 0)
                # shares is absent on most post types. Absent is not zero.
                shares_raw = raw.get("shares")
                shares = int(shares_raw.get("count", 0)) if shares_raw else None

                posts.append(
                    PostRecord(
                        external_id=str(raw.get("id", "")),
                        message=str(raw.get("message", "")),
                        media_type=_media_type(raw),
                        posted_at=posted_at,
                        likes=int(reactions or 0),
                        comments=int(comments or 0),
                        shares=int(shares or 0),
                        video_views=None,
                        reach=None,
                        watch_seconds=None,
                        permalink=str(raw.get("permalink_url", "")),
                        thumbnail_url=str(raw.get("full_picture", "")),
                    )
                )

            if len(posts) >= post_limit:
                truncated = True
                break

            next_url = payload.get("paging", {}).get("next")
            if not next_url:
                path = ""
            else:
                # Follow the next URL by path+query so the base_url still applies.
                path = next_url.split(f"/{_settings.fb_graph_version}", 1)[-1]
                if not path.startswith("/"):
                    path = ""

        if truncated:
            notes.append(
                f"History was capped at {len(posts)} posts (or {days} days). "
                "The window shown is not the page's entire lifetime."
            )
        if posts and all(p.shares == 0 for p in posts):
            notes.append(
                "No post reported a share count. Graph omits that field on most "
                "post types, so shares are shown as zero rather than guessed."
            )

    location = page_payload.get("location") or {}
    page = PageRecord(
        external_id=str(page_payload.get("id", external_id)),
        name=str(page_payload.get("name", "Unnamed page")),
        category=str(page_payload.get("category", "")),
        description=str(page_payload.get("description", "")),
        website=str(page_payload.get("website", "")),
        phone=str(page_payload.get("phone", "")),
        address=", ".join(part for part in (location.get("city"), location.get("country")) if part),
        cover_url=str((page_payload.get("cover") or {}).get("source", "")),
        followers=int(page_payload.get("followers_count") or 0),
        likes=int(page_payload.get("fan_count") or 0),
        talking_about=int(page_payload.get("talking_about_count") or 0),
        is_verified=bool(page_payload.get("is_verified", False)),
        baseline_followers=0,
    )

    snapshot = FollowerSnapshot(
        captured_at=now,
        followers=page.followers,
        likes=page.likes,
        talking_about=page.talking_about,
    )

    return GraphBundle(
        page=page,
        posts=posts,
        snapshots=[snapshot],
        fetched_at=now,
        posts_truncated=truncated,
        notes=notes,
    )
