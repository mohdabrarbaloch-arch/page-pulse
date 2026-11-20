"""Deterministic demo data.

This exists so the app can be run and explored without a Facebook token. It is
generated, not scraped: every number here is synthetic, and every surface that
displays it says so. Nothing in this module is ever presented as real page data.

The generator is seeded from the page slug, so the same page always produces the
same numbers -- a demo that changes on every refresh is impossible to reason
about or screenshot.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.models import DataSource
from app.services.analytics import FollowerSnapshot, PageRecord, PostRecord

# Real businesses in Karachi, so the demo reads like a page someone actually runs.
# The numbers attached to them are invented; the businesses are just familiar
# context, which is why every one of them is marked DEMO in the database.
DEMO_PAGES: tuple[dict[str, object], ...] = (
    {
        "slug": "robo-pk",
        "external_id": "demo-robo-pk",
        "name": "ROBO .PK",
        "category": "Home & Garden",
        "description": (
            "Water pump and tank automation built in Karachi since 1998. "
            "Automatic motor controllers, level sensors, and dry-run protection."
        ),
        "website": "https://robopk.example.com",
        "phone": "0313 287 5775",
        "address": "Shahrah-e-Faisal, Karachi, Pakistan",
        "followers": 4180,
        "baseline_followers": 3610,
        "likes": 4025,
        "talking_about": 268,
        "is_verified": False,
        "profile": "product",
    },
    {
        "slug": "chai-khana",
        "external_id": "demo-chai-khana",
        "name": "Chai Khana Karachi",
        "category": "Restaurant",
        "description": (
            "A small chai spot in Bahadurabad. Cardamom karak, bun maska, "
            "and the least Instagrammable seating you have ever loved."
        ),
        "website": "",
        "phone": "0300 111 2233",
        "address": "Bahadurabad, Karachi, Pakistan",
        "followers": 12480,
        "baseline_followers": 10940,
        "likes": 11960,
        "talking_about": 1840,
        "is_verified": True,
        "profile": "food",
    },
    {
        "slug": "thread-studio",
        "external_id": "demo-thread-studio",
        "name": "Thread Studio",
        "category": "Clothing Store",
        "description": (
            "Everyday lawn and khaddar, cut and stitched in Karachi. "
            "Small batches, no seasonal drops, sizes XS to 3XL."
        ),
        "website": "https://threadstudio.example.com",
        "phone": "0321 445 8890",
        "address": "Clifton Block 4, Karachi, Pakistan",
        "followers": 8640,
        "baseline_followers": 8720,
        "likes": 8210,
        "talking_about": 190,
        "is_verified": False,
        "profile": "retail",
    },
)

_PROFILE_SHAPE = {
    # (posts/week, video share, photo share, link share, engagement multiplier)
    "product": (3.4, 0.45, 0.40, 0.15, 1.0),
    "food": (5.2, 0.55, 0.35, 0.10, 1.8),
    "retail": (2.6, 0.28, 0.62, 0.10, 1.3),
}

_MESSAGES = {
    "product": [
        "Motor dry run ho raha hai? Ye 3 cheezein check karein before you call anyone.",
        "Tank overflow ka asli nuqsan paani nahi — seepage hai. 1998 se yahi dekh rahe hain.",
        "New controller installed in Gulshan today. Bas ek din, aur woh bhi 2 ghante.",
        "Barish ke baad motor ki wiring check karwana zaroori hai. Ek call bachata hai hazaron ka.",
        "Automatic level sensor — install ke baad bijli ka bill bhi kam hota hai.",
        "Why your motor trips at 3am, and what the thermal cutout is actually telling you.",
        "Client ne poocha: sensor ya float valve? Jawab depend karta hai tank ki height pe.",
        "Dry run protection explained in 40 seconds. Save this before summer.",
    ],
    "food": [
        "Karak chai, no sugar, extra cardamom. That is the whole menu order.",
        "Bun maska at 7am hits different when the street is still quiet.",
        "We ran out of doodh patti by 9pm on Saturday. Sorry, and also not sorry.",
        "Three rupees more for double cream. That is the entire price increase.",
        "Behind the counter: 40 seconds of the only thing we do.",
        "New seating in the back. Same chairs from 2019, just repainted twice.",
        "Ye chai ka secret sirf doodh nahi — patti ka ratio hai. 60:40, never 50:50.",
        "Rain outside, chai inside. This is the whole business model.",
    ],
    "retail": [
        "Khaddar restock: 40 pieces, three colours, no re-run once they are gone.",
        "Size 3XL in stock across all four cuts. No waiting list this time.",
        "Why we do not do seasonal drops: you should be able to buy the same shirt twice.",
        "Stitching detail on the cuff — 14 stitches per inch, done in our own unit.",
        "Two new lawn prints. Small batch, 25 of each.",
        "Every piece is pre-washed. It will not shrink on you.",
    ],
}

_VIDEO_LINES = {
    "product": [
        "Install walkthrough — 38 seconds from box to running motor.",
        "What happens when the tank overflows, filmed at an actual house.",
        "Dry run test on the bench: listen for the cutout.",
    ],
    "food": [
        "60-second pour. That is it. That is the video.",
        "How we make 200 cups before 9am.",
        "The bun-toasting station at peak hour.",
    ],
    "retail": [
        "Fabric close-up under daylight. No filter.",
        "Cuff stitching, shot at the unit.",
        "Three colours, one light source, no editing.",
    ],
}


@dataclass(frozen=True, slots=True)
class DemoBundle:
    """A complete generated page, ready to persist."""

    page: PageRecord
    posts: list[PostRecord]
    snapshots: list[FollowerSnapshot]
    source: DataSource = DataSource.DEMO


def _rng_for(slug: str, seed_salt: str = "") -> random.Random:
    """Deterministic RNG from the slug, so runs are reproducible."""
    digest = hashlib.sha256(f"{slug}:{seed_salt}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def available_demo_pages() -> list[dict[str, object]]:
    """Metadata for the demo pages, for the picker in the UI."""
    return [
        {
            "slug": str(entry["slug"]),
            "name": str(entry["name"]),
            "category": str(entry["category"]),
            "followers": int(entry["followers"]),  # type: ignore[arg-type]
        }
        for entry in DEMO_PAGES
    ]


def get_demo_spec(slug: str) -> dict[str, object] | None:
    """Look up a demo page definition by slug."""
    for entry in DEMO_PAGES:
        if entry["slug"] == slug:
            return entry
    return None


def generate_demo_page(
    slug: str,
    *,
    days: int = 180,
    now: datetime | None = None,
) -> DemoBundle:
    """Generate a full page, post history and follower series.

    The generated history deliberately contains realistic flaws -- a gap in
    posting, one format that underperforms, a flat period -- because a demo where
    every metric looks good teaches the reader nothing about the dashboard.
    """
    spec = get_demo_spec(slug)
    if spec is None:
        raise KeyError(f"Unknown demo page: {slug}")

    rng = _rng_for(slug)
    now = (now or datetime.now(UTC)).replace(microsecond=0)

    profile = str(spec["profile"])
    posts_per_week, video_share, photo_share, link_share, multiplier = _PROFILE_SHAPE[profile]
    followers = int(spec["followers"])  # type: ignore[arg-type]

    media_pool = (
        ["reel"] * int(video_share * 100)
        + ["photo"] * int(photo_share * 100)
        + ["link"] * int(link_share * 100)
    )

    messages = _MESSAGES[profile]
    video_lines = _VIDEO_LINES[profile]

    # Engagement baseline scales with follower count: roughly 1.5-4% per post.
    base_engagement = followers * rng.uniform(0.015, 0.032) * multiplier

    posts: list[PostRecord] = []
    cursor = now - timedelta(days=days)

    index = 0
    while cursor < now:
        # Typical gap between posts, jittered.
        gap_days = 7.0 / posts_per_week * rng.uniform(0.55, 1.6)

        # One deliberate silence so the cadence analysis has something to find.
        if index == max(2, int(posts_per_week * 4)):
            gap_days += rng.uniform(11, 16)

        cursor = cursor + timedelta(days=gap_days)
        if cursor >= now:
            break

        # Post at a plausible local hour. Karachi is UTC+5, so subtract 5 to get
        # the UTC instant the post was published.
        hour_weights = {
            9: 3,
            10: 3,
            11: 2,
            12: 2,
            13: 2,
            17: 4,
            18: 3,
            19: 5,
            20: 7,
            21: 6,
            22: 4,
        }
        local_hour = rng.choices(
            list(hour_weights.keys()), weights=list(hour_weights.values()), k=1
        )[0]
        posted_at = cursor.replace(
            hour=(local_hour - 5) % 24,
            minute=rng.choice([0, 5, 10, 15, 20, 30, 40, 45]),
        )

        media_type = rng.choice(media_pool)

        # Format effect: video earns more per post, links earn much less. This is
        # the signal the mix analysis is supposed to surface.
        format_factor = {"reel": 1.55, "photo": 1.0, "link": 0.42}[media_type]

        # Mild upward trend plus noise, so growth is not a flat line.
        progress = (cursor - (now - timedelta(days=days))).days / max(days, 1)
        trend = 0.82 + 0.4 * progress

        # A random viral spike, roughly one in twenty posts.
        spike = rng.uniform(3.5, 9.0) if rng.random() < 0.05 else 1.0

        engagement = max(
            0,
            int(base_engagement * format_factor * trend * spike * rng.uniform(0.55, 1.45)),
        )

        # Split engagement into likes/comments/shares. The split mirrors what a
        # real page looks like: likes dominate, comments are a few percent, shares
        # sit between. Getting this wrong is what makes an "insight" fire on every
        # page, so the ratios here are deliberately conservative.
        likes = int(engagement * rng.uniform(0.80, 0.89))
        comments = int(engagement * rng.uniform(0.02, 0.06))
        shares = max(0, engagement - likes - comments)

        views = None
        watch_seconds = None
        reach = None
        if media_type == "reel":
            # A reel is seen many times per engagement -- roughly one engagement
            # per 25-70 views, which puts the reach-based engagement rate in the
            # 2-5% band real pages report.
            views = int(engagement * rng.uniform(25, 70))
            # Total watch time across all views, at 3-11 seconds per view. That is
            # a per-view figure, which is what the analytics divides by.
            watch_seconds = round(views * rng.uniform(3.0, 11.0), 1)
            reach = int(views * rng.uniform(0.75, 0.95))

        message = rng.choice(video_lines) if media_type == "reel" else rng.choice(messages)

        posts.append(
            PostRecord(
                external_id=f"{spec['external_id']}-{index:04d}",
                message=message,
                media_type=media_type,
                posted_at=posted_at,
                likes=likes,
                comments=comments,
                shares=shares,
                video_views=views,
                reach=reach,
                watch_seconds=watch_seconds,
                permalink="",
                thumbnail_url="",
            )
        )
        index += 1

    # Follower series: weekly readings ending at the current count. The series is
    # built backwards from today so the last point always matches the page.
    snapshots: list[FollowerSnapshot] = []
    current = followers
    weeks = max(4, days // 7)
    for week in range(weeks):
        captured = now - timedelta(weeks=week)
        # Walk backwards with a plausible net gain per week.
        if week > 0:
            weekly_gain = rng.uniform(-0.006, 0.021) * current
            current = max(1, int(current - weekly_gain))
        snapshots.append(
            FollowerSnapshot(
                captured_at=captured,
                followers=current,
                likes=int(current * 0.96),
                talking_about=int(current * rng.uniform(0.04, 0.22)),
            )
        )
    snapshots.reverse()

    page = PageRecord(
        external_id=str(spec["external_id"]),
        name=str(spec["name"]),
        category=str(spec["category"]),
        description=str(spec["description"]),
        website=str(spec["website"]),
        phone=str(spec["phone"]),
        address=str(spec["address"]),
        cover_url="",
        followers=followers,
        likes=int(spec["likes"]),  # type: ignore[arg-type]
        talking_about=int(spec["talking_about"]),  # type: ignore[arg-type]
        is_verified=bool(spec["is_verified"]),
        baseline_followers=int(spec["baseline_followers"]),  # type: ignore[arg-type]
    )

    return DemoBundle(page=page, posts=posts, snapshots=snapshots)
