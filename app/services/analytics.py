"""Pure analytics over page posts.

Nothing in this module touches the database or the network. It takes plain
dataclasses in and returns plain dataclasses out, which means every number the
dashboard shows can be tested in microseconds.

Design choices worth stating up front, because they decide what the numbers mean:

* **Engagement rate needs a denominator.** Facebook's real engagement rate is
  engagement / reach. Reach is only available for some post types and often not
  at all. When reach is missing we fall back to engagement / followers and the
  result says which denominator it used, because the two numbers are not
  comparable and silently mixing them produces fiction.
* **Typical means median.** One viral post drags a mean so far that it stops
  describing anything. Median engagement is reported as "typical", mean as
  "average", and both are labelled.
* **A best time needs a sample.** Ranking six hour-buckets where five contain a
  single post each is noise dressed as insight. Buckets below the minimum sample
  size are reported as insufficient rather than ranked.
* **Growth needs a baseline.** Growth from a baseline of zero is undefined, not
  infinite. That case returns no percentage.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum
from statistics import mean, median

# ---------------------------------------------------------------------------
# Input types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PostRecord:
    """One post, already parsed. Times must be timezone-aware UTC."""

    external_id: str
    message: str
    media_type: str
    posted_at: datetime
    likes: int = 0
    comments: int = 0
    shares: int = 0
    video_views: int | None = None
    reach: int | None = None
    impressions: int | None = None
    watch_seconds: float | None = None
    permalink: str = ""
    thumbnail_url: str = ""

    @property
    def engagement(self) -> int:
        return int(self.likes) + int(self.comments) + int(self.shares)

    @property
    def is_video(self) -> bool:
        return self.media_type in {"video", "reel"}


@dataclass(frozen=True, slots=True)
class PageRecord:
    """Page-level identity and current counters."""

    external_id: str
    name: str
    category: str = ""
    description: str = ""
    website: str = ""
    phone: str = ""
    address: str = ""
    cover_url: str = ""
    followers: int = 0
    likes: int = 0
    talking_about: int = 0
    is_verified: bool = False
    baseline_followers: int = 0


@dataclass(frozen=True, slots=True)
class FollowerSnapshot:
    """A follower reading at a point in time."""

    captured_at: datetime
    followers: int
    likes: int = 0
    talking_about: int = 0


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


class EngagementBasis(StrEnum):
    """Which denominator the engagement rate used."""

    REACH = "reach"
    FOLLOWERS = "followers"
    UNAVAILABLE = "unavailable"


class Confidence(StrEnum):
    """How much weight a finding deserves given its sample size."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT = "insufficient"


@dataclass(frozen=True, slots=True)
class EngagementSummary:
    """Engagement across a set of posts."""

    post_count: int
    total_engagement: int
    median_engagement: float
    mean_engagement: float
    rate_percent: float | None
    basis: EngagementBasis
    posts_with_basis_data: int
    total_posts_considered: int

    @property
    def basis_share_percent(self) -> float | None:
        """What share of the window's posts the rate was actually computed over."""
        if not self.total_posts_considered:
            return None
        return round(self.posts_with_basis_data / self.total_posts_considered * 100, 1)

    @property
    def explained(self) -> str:
        """A sentence stating exactly what the rate measures, and over what."""
        if self.basis is EngagementBasis.REACH:
            return (
                f"{self.rate_percent:.2f}% median engagement per reach, computed over "
                f"{self.posts_with_basis_data} of {self.total_posts_considered} posts "
                f"({self.basis_share_percent:.0f}% of the window) — the ones that "
                "reported reach, not the whole page."
            )
        if self.basis is EngagementBasis.FOLLOWERS:
            return (
                f"{self.rate_percent:.2f}% median engagement per follower. Reach was "
                "not reported, so this is not comparable with a reach-based benchmark."
            )
        return "Engagement rate unavailable: no reach data and no follower count."


@dataclass(frozen=True, slots=True)
class CadenceSummary:
    """How often the page actually posts."""

    posts_per_week: float
    window_days: int
    post_count: int
    longest_gap_days: float | None
    median_gap_days: float | None
    consistency: str  # steady | bursty | dormant -> judgement, with numbers attached


@dataclass(frozen=True, slots=True)
class MediaPerformance:
    """Aggregate performance for one media type."""

    media_type: str
    post_count: int
    median_engagement: float
    share_of_posts_percent: float
    share_of_engagement_percent: float
    median_engagement_rate_percent: float | None
    confidence: Confidence

    @property
    def outperforms_share(self) -> bool:
        """True when this format earns more engagement than its posting share."""
        return self.share_of_engagement_percent > self.share_of_posts_percent


@dataclass(frozen=True, slots=True)
class TimeSlot:
    """Performance for one weekday+time-band bucket, in the page's local timezone."""

    weekday: int  # 0 = Monday
    weekday_label: str
    hour_start: int
    hour_end: int
    post_count: int
    median_engagement: float
    confidence: Confidence

    @property
    def label(self) -> str:
        """e.g. 'Friday, 20:00-23:00'."""
        return f"{self.weekday_label}, {self.hour_start:02d}:00-{self.hour_end:02d}:00"


@dataclass(frozen=True, slots=True)
class GrowthSummary:
    """Follower change over the observed window."""

    start_followers: int | None
    current_followers: int
    absolute_change: int | None
    percent_change: float | None
    window_days: int | None
    per_week: float | None
    note: str


@dataclass(frozen=True, slots=True)
class PostHighlight:
    """A post singled out for the leaderboard."""

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


@dataclass(frozen=True, slots=True)
class VideoMetrics:
    """Video-specific figures, only for posts that actually reported them."""

    videos_analysed: int
    median_views: float
    # Average seconds watched per view. NOT total watch time -- a 30s clip and a
    # 4-minute clip produce wildly different totals for the same retention, so
    # the per-view figure is the only one comparable across a mixed feed.
    median_watch_seconds_per_view: float | None
    total_views: int


@dataclass(frozen=True, slots=True)
class Insight:
    """One actionable finding, with the numbers that produced it."""

    key: str
    severity: str  # positive | opportunity | warning | critical
    title: str
    body: str
    evidence: dict[str, float | int | str | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PageAnalysis:
    """Everything the dashboard needs, computed in one pass."""

    page_name: str
    generated_at: datetime
    window_days: int
    post_count: int
    engagement: EngagementSummary
    cadence: CadenceSummary
    media_mix: list[MediaPerformance]
    best_slots: list[TimeSlot]
    growth: GrowthSummary
    top_posts: list[PostHighlight]
    weakest_posts: list[PostHighlight]
    video: VideoMetrics | None
    insights: list[Insight]
    follower_ratio: float | None
    talking_about_rate_percent: float | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WEEKDAY_LABELS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# Below this many posts a bucket's median is not worth ranking.
MIN_SLOT_SAMPLE = 3
# Width of the posting-time bands. See analyse_time_slots for why not one hour.
BAND_HOURS = 4
# Below this many posts a media type's rate is stated but flagged.
MIN_MEDIA_SAMPLE = 3


def _percentile(values: list[float], fraction: float) -> float:
    """Linear-interpolation percentile. Used for the p90 'viral' threshold."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    """Division that returns None instead of raising or inventing infinity."""
    if not denominator:
        return None
    return numerator / denominator


def _to_local(moment: datetime, tz: timezone) -> datetime:
    """Convert a UTC timestamp to the reporting timezone."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(tz)


def _excerpt(message: str, limit: int = 120) -> str:
    """Collapse whitespace and cut on a word boundary."""
    flat = " ".join((message or "").split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit].rsplit(" ", 1)[0]
    return f"{cut}…" if cut else flat[:limit]


def engagement_rate_for_post(
    post: PostRecord, followers: int
) -> tuple[float | None, EngagementBasis]:
    """Engagement rate for one post, plus which denominator it used.

    Reach is preferred because it is what the platforms themselves report. When
    reach is absent the follower count is used, and the caller is told so it can
    avoid comparing the two.
    """
    if post.reach and post.reach > 0:
        return (post.engagement / post.reach) * 100.0, EngagementBasis.REACH
    if followers and followers > 0:
        return (post.engagement / followers) * 100.0, EngagementBasis.FOLLOWERS
    return None, EngagementBasis.UNAVAILABLE


# ---------------------------------------------------------------------------
# Individual metric builders
# ---------------------------------------------------------------------------


def summarise_engagement(posts: list[PostRecord], followers: int) -> EngagementSummary:
    """Engagement summary that refuses to blend two different denominators."""
    if not posts:
        return EngagementSummary(
            post_count=0,
            total_engagement=0,
            median_engagement=0.0,
            mean_engagement=0.0,
            rate_percent=None,
            basis=EngagementBasis.UNAVAILABLE,
            posts_with_basis_data=0,
            total_posts_considered=0,
        )

    rates: list[float] = []
    basis_counts: dict[EngagementBasis, int] = defaultdict(int)
    for post in posts:
        rate, basis = engagement_rate_for_post(post, followers)
        if rate is not None:
            rates.append(rate)
            basis_counts[basis] += 1

    # Prefer reach if even one post reported it, but only average over posts that
    # used that same basis -- mixing reach-based and follower-based rates in one
    # mean would produce a number that means nothing.
    basis = EngagementBasis.UNAVAILABLE
    usable: list[float] = []
    for candidate in (EngagementBasis.REACH, EngagementBasis.FOLLOWERS):
        if basis_counts[candidate]:
            basis = candidate
            for post in posts:
                rate, used = engagement_rate_for_post(post, followers)
                if used is candidate and rate is not None:
                    usable.append(rate)
            break

    engagements = [post.engagement for post in posts]

    return EngagementSummary(
        post_count=len(posts),
        total_engagement=sum(engagements),
        median_engagement=float(median(engagements)),
        mean_engagement=float(mean(engagements)),
        rate_percent=float(median(usable)) if usable else None,
        basis=basis,
        posts_with_basis_data=len(usable),
        total_posts_considered=len(posts),
    )


def summarise_cadence(posts: list[PostRecord], window_days: int) -> CadenceSummary:
    """Posting frequency, plus how evenly spaced it is.

    The window is measured from the requested range, not from the first post, so
    a page that posted twice and then went quiet does not appear productive.
    """
    if not posts:
        return CadenceSummary(
            posts_per_week=0.0,
            window_days=window_days,
            post_count=0,
            longest_gap_days=None,
            median_gap_days=None,
            consistency="dormant",
        )

    ordered = sorted(posts, key=lambda p: p.posted_at)
    gaps = [
        (ordered[i + 1].posted_at - ordered[i].posted_at).total_seconds() / 86400
        for i in range(len(ordered) - 1)
    ]

    effective_days = max(window_days, 1)
    per_week = len(posts) / effective_days * 7

    longest_gap = max(gaps) if gaps else None
    median_gap = float(median(gaps)) if gaps else None

    # Consistency is a judgement, so state the rule that produced it.
    if longest_gap is not None and median_gap and longest_gap > max(median_gap * 3, 14):
        consistency = "bursty"
    elif per_week >= 1:
        consistency = "steady"
    else:
        consistency = "sparse"

    return CadenceSummary(
        posts_per_week=round(per_week, 2),
        window_days=effective_days,
        post_count=len(posts),
        longest_gap_days=round(longest_gap, 1) if longest_gap is not None else None,
        median_gap_days=round(median_gap, 1) if median_gap is not None else None,
        consistency=consistency,
    )


def _confidence_for(count: int, minimum: int = MIN_MEDIA_SAMPLE) -> Confidence:
    """Map a sample size onto a confidence label."""
    if count < minimum:
        return Confidence.INSUFFICIENT
    if count < minimum * 2:
        return Confidence.LOW
    if count < minimum * 4:
        return Confidence.MEDIUM
    return Confidence.HIGH


def analyse_media_mix(posts: list[PostRecord], followers: int) -> list[MediaPerformance]:
    """Per-format performance, ordered by median engagement.

    Formats with very small samples are kept in the list but marked
    INSUFFICIENT so the UI never presents them as a finding.
    """
    if not posts:
        return []

    by_type: dict[str, list[PostRecord]] = defaultdict(list)
    for post in posts:
        by_type[post.media_type or "status"].append(post)

    total_engagement = sum(post.engagement for post in posts)
    total_posts = len(posts)

    results: list[MediaPerformance] = []
    for media_type, group in by_type.items():
        engagements = [post.engagement for post in group]
        group_engagement = sum(engagements)

        rates: list[float] = []
        for post in group:
            rate, _basis = engagement_rate_for_post(post, followers)
            if rate is not None:
                rates.append(rate)

        results.append(
            MediaPerformance(
                media_type=media_type,
                post_count=len(group),
                median_engagement=float(median(engagements)),
                share_of_posts_percent=round(len(group) / total_posts * 100, 1),
                share_of_engagement_percent=(
                    round(group_engagement / total_engagement * 100, 1) if total_engagement else 0.0
                ),
                median_engagement_rate_percent=round(float(median(rates)), 2) if rates else None,
                confidence=_confidence_for(len(group)),
            )
        )

    results.sort(key=lambda item: item.median_engagement, reverse=True)
    return results


def analyse_time_slots(posts: list[PostRecord], tz: timezone, limit: int = 4) -> list[TimeSlot]:
    """Best weekday+hour buckets in local time.

    Only buckets that clear MIN_SLOT_SAMPLE are eligible to be ranked. Buckets
    below it are still returned (marked INSUFFICIENT) so the UI can say "not
    enough data yet" instead of inventing a winner.
    """
    if not posts:
        return []

    # Bucket into four-hour bands, not exact hours. A 48-post page spread over 24
    # hours has at most two posts per exact hour, so an hour-level ranking would
    # compare single posts and present the winner as a pattern. A four-hour band
    # is the smallest unit where a handful of posts can actually land together.
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for post in posts:
        local = _to_local(post.posted_at, tz)
        buckets[(local.weekday(), local.hour // BAND_HOURS)].append(post.engagement)

    slots: list[TimeSlot] = []
    for (weekday, band), engagements in buckets.items():
        count = len(engagements)
        hour_start = band * BAND_HOURS
        hour_end = (band + 1) * BAND_HOURS
        label_end = 24 if hour_end > 24 else hour_end
        slots.append(
            TimeSlot(
                weekday=weekday,
                weekday_label=WEEKDAY_LABELS[weekday],
                hour_start=hour_start,
                hour_end=label_end,
                post_count=count,
                median_engagement=float(median(engagements)),
                confidence=_confidence_for(count, MIN_SLOT_SAMPLE),
            )
        )

    eligible = [s for s in slots if s.confidence is not Confidence.INSUFFICIENT]
    eligible.sort(key=lambda s: (-s.median_engagement, -s.post_count))

    if eligible:
        return eligible[:limit]

    # Nothing is rankable. Return the busiest buckets, honestly unranked.
    slots.sort(key=lambda s: (-s.post_count, -s.median_engagement))
    return slots[:limit]


def compute_growth(
    snapshots: list[FollowerSnapshot], current_followers: int, baseline_followers: int = 0
) -> GrowthSummary:
    """Follower change. Percentage is None when the baseline is zero."""
    if not snapshots:
        if baseline_followers > 0 and current_followers >= baseline_followers:
            delta = current_followers - baseline_followers
            return GrowthSummary(
                start_followers=baseline_followers,
                current_followers=current_followers,
                absolute_change=delta,
                percent_change=round(delta / baseline_followers * 100, 2),
                window_days=None,
                per_week=None,
                note="Baseline from the first observation; no time series recorded yet.",
            )
        return GrowthSummary(
            start_followers=None,
            current_followers=current_followers,
            absolute_change=None,
            percent_change=None,
            window_days=None,
            per_week=None,
            note="No follower history recorded, so no growth can be shown yet.",
        )

    ordered = sorted(snapshots, key=lambda s: s.captured_at)
    first = ordered[0]
    last = ordered[-1]

    span = last.captured_at - first.captured_at
    window_days = span.total_seconds() / 86400

    start = first.followers
    delta = current_followers - start

    if start <= 0:
        return GrowthSummary(
            start_followers=start,
            current_followers=current_followers,
            absolute_change=delta,
            percent_change=None,
            window_days=round(window_days, 1),
            per_week=None,
            note="The first recorded follower count was zero, so a percentage would be undefined.",
        )

    per_week = (delta / window_days * 7) if window_days >= 1 else None

    return GrowthSummary(
        start_followers=start,
        current_followers=current_followers,
        absolute_change=delta,
        percent_change=round(delta / start * 100, 2),
        window_days=round(window_days, 1),
        per_week=round(per_week, 1) if per_week is not None else None,
        note=f"Measured across {len(ordered)} recorded snapshots.",
    )


def _highlight(post: PostRecord, followers: int) -> PostHighlight:
    rate, _basis = engagement_rate_for_post(post, followers)
    return PostHighlight(
        external_id=post.external_id,
        excerpt=_excerpt(post.message),
        media_type=post.media_type,
        posted_at=post.posted_at,
        engagement=post.engagement,
        likes=post.likes,
        comments=post.comments,
        shares=post.shares,
        engagement_rate_percent=round(rate, 2) if rate is not None else None,
        permalink=post.permalink,
    )


def rank_posts(
    posts: list[PostRecord], followers: int, limit: int = 5
) -> tuple[list[PostHighlight], list[PostHighlight]]:
    """Top and weakest posts by raw engagement."""
    if not posts:
        return [], []
    ordered = sorted(posts, key=lambda p: p.engagement, reverse=True)
    top = [_highlight(p, followers) for p in ordered[:limit]]
    weakest = [_highlight(p, followers) for p in ordered[-limit:]][::-1]
    return top, weakest


def summarise_video(posts: list[PostRecord]) -> VideoMetrics | None:
    """View and watch-time figures, over video posts that reported views."""
    videos = [p for p in posts if p.is_video]
    if not videos:
        return None

    with_views = [p for p in videos if p.video_views and p.video_views > 0]
    if not with_views:
        return VideoMetrics(
            videos_analysed=len(videos),
            median_views=0.0,
            median_watch_seconds_per_view=None,
            total_views=0,
        )

    # watch_seconds is TOTAL watch time across all views, so it has to be divided
    # by views before it means anything. Reporting the raw total as "watch time"
    # produced a median of 600 seconds against sub-minute clips.
    per_view = [
        p.watch_seconds / p.video_views
        for p in with_views
        if p.watch_seconds and p.video_views and p.watch_seconds > 0
    ]

    median_per_view = float(median(per_view)) if per_view else None

    return VideoMetrics(
        videos_analysed=len(videos),
        median_views=float(median([p.video_views for p in with_views if p.video_views])),
        median_watch_seconds_per_view=(
            round(median_per_view, 1) if median_per_view is not None else None
        ),
        total_views=int(sum(p.video_views or 0 for p in with_views)),
    )


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------


def _format_number(value: float) -> str:
    """Human-readable number: 12_400 -> '12.4k'."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return f"{value:.0f}"


def build_insights(
    *,
    page: PageRecord,
    posts: list[PostRecord],
    engagement: EngagementSummary,
    cadence: CadenceSummary,
    media_mix: list[MediaPerformance],
    best_slots: list[TimeSlot],
    growth: GrowthSummary,
    video: VideoMetrics | None,
    previous_engagement: EngagementSummary | None = None,
) -> list[Insight]:
    """Derive findings from the computed metrics.

    Every insight interpolates its own numbers. A rule that cannot cite a number
    raises nothing, because "post more consistently" is advice, not analysis.
    """
    insights: list[Insight] = []

    # --- Cadence: the most common genuinely-fixable problem ------------------
    if cadence.post_count == 0:
        insights.append(
            Insight(
                key="no_posts",
                severity="critical",
                title="No posts in this window",
                body=(
                    "There is nothing to analyse in the selected period. Widen the "
                    "date range, or import a longer history, before drawing "
                    "conclusions about what works."
                ),
            )
        )
        return insights

    if cadence.consistency == "dormant":
        insights.append(
            Insight(
                key="dormant_page",
                severity="critical",
                title="The page reads as dormant",
                body=(
                    f"{cadence.post_count} posts in {cadence.window_days} days is "
                    f"{cadence.posts_per_week}/week. Below one post a week the page "
                    "stops appearing in followers' feeds often enough to stay familiar."
                ),
                evidence={"posts_per_week": cadence.posts_per_week},
            )
        )
    elif cadence.consistency == "bursty" and cadence.longest_gap_days:
        insights.append(
            Insight(
                key="bursty_cadence",
                severity="warning",
                title="Posting is bursty rather than steady",
                body=(
                    f"The longest gap between posts was {cadence.longest_gap_days:.0f} days "
                    f"against a typical gap of {cadence.median_gap_days:.0f} days. "
                    "Reach decays across a silence like that, so the post after it "
                    "starts from a colder audience."
                ),
                evidence={
                    "longest_gap_days": cadence.longest_gap_days,
                    "median_gap_days": cadence.median_gap_days,
                },
            )
        )
    elif cadence.posts_per_week >= 3:
        insights.append(
            Insight(
                key="healthy_cadence",
                severity="positive",
                title="Publishing rhythm is solid",
                body=(
                    f"{cadence.posts_per_week}/week across {cadence.window_days} days, "
                    f"with a typical gap of {cadence.median_gap_days or 0:.0f} days. "
                    "This is the range where the algorithm keeps treating you as active."
                ),
                evidence={"posts_per_week": cadence.posts_per_week},
            )
        )

    # --- Format mix ----------------------------------------------------------
    eligible_mix = [m for m in media_mix if m.confidence is not Confidence.INSUFFICIENT]
    if len(eligible_mix) >= 2:
        best = eligible_mix[0]
        worst = eligible_mix[-1]
        if best.median_engagement > 0 and worst.median_engagement >= 0:
            multiple = _safe_ratio(best.median_engagement, max(worst.median_engagement, 1))
            if multiple and multiple >= 1.5:
                insights.append(
                    Insight(
                        key="format_mix",
                        severity="opportunity",
                        title=f"{best.media_type.title()} posts earn the most engagement",
                        body=(
                            f"{best.media_type} posts draw a median of "
                            f"{_format_number(best.median_engagement)} engagements against "
                            f"{_format_number(worst.median_engagement)} for "
                            f"{worst.media_type} — about {multiple:.1f}x more. "
                            f"They are only {best.share_of_posts_percent:.0f}% of what you "
                            "publish, so there is room to shift the mix."
                        ),
                        evidence={
                            "best_format": best.media_type,
                            "best_median": best.median_engagement,
                            "worst_format": worst.media_type,
                            "worst_median": worst.median_engagement,
                            "multiple": round(multiple, 2),
                            "best_share_of_posts": best.share_of_posts_percent,
                        },
                    )
                )

    # --- Over-represented format --------------------------------------------
    for item in media_mix:
        if item.confidence is Confidence.INSUFFICIENT:
            continue
        if (
            item.share_of_posts_percent >= 30
            and item.share_of_engagement_percent < item.share_of_posts_percent * 0.6
        ):
            insights.append(
                Insight(
                    key=f"overweight_{item.media_type}",
                    severity="opportunity",
                    title=f"{item.media_type.title()} posts take more effort than they return",
                    body=(
                        f"{item.media_type} makes up {item.share_of_posts_percent:.0f}% of "
                        f"output but only {item.share_of_engagement_percent:.0f}% of "
                        "engagement. Cutting one a week frees the slot for a format "
                        "that is already outperforming it."
                    ),
                    evidence={
                        "media_type": item.media_type,
                        "share_of_posts": item.share_of_posts_percent,
                        "share_of_engagement": item.share_of_engagement_percent,
                    },
                )
            )
            break

    # --- Timing -------------------------------------------------------------
    ranked_slots = [s for s in best_slots if s.confidence is not Confidence.INSUFFICIENT]
    if ranked_slots:
        top_slot = ranked_slots[0]
        insights.append(
            Insight(
                key="best_time",
                severity="positive" if top_slot.confidence is not Confidence.LOW else "opportunity",
                title=f"{top_slot.label} is your strongest slot",
                body=(
                    f"Posts in that window average {_format_number(top_slot.median_engagement)} "
                    f"engagements across {top_slot.post_count} posts. Confidence is "
                    f"{top_slot.confidence.value} at this sample size — it becomes a "
                    "reliable pattern past about six posts in the slot."
                ),
                evidence={
                    "slot": top_slot.label,
                    "weekday": top_slot.weekday_label,
                    "hour_start": top_slot.hour_start,
                    "hour_end": top_slot.hour_end,
                    "median_engagement": top_slot.median_engagement,
                    "post_count": top_slot.post_count,
                },
            )
        )

    # --- Growth -------------------------------------------------------------
    if growth.absolute_change is not None and growth.absolute_change != 0:
        if growth.absolute_change > 0:
            insights.append(
                Insight(
                    key="growth_positive",
                    severity="positive",
                    title=f"Gained {growth.absolute_change:,} followers",
                    body=(
                        f"That is {growth.percent_change}% growth"
                        if growth.percent_change is not None
                        else "Growth is positive"
                    )
                    + (
                        f" over {growth.window_days:.0f} days, or about "
                        f"{growth.per_week:,.0f} a week."
                        if growth.per_week is not None and growth.window_days
                        else "."
                    ),
                    evidence={
                        "absolute_change": growth.absolute_change,
                        "percent_change": growth.percent_change,
                        "per_week": growth.per_week,
                    },
                )
            )
        else:
            insights.append(
                Insight(
                    key="growth_negative",
                    severity="warning",
                    title=f"Lost {abs(growth.absolute_change):,} followers",
                    body=(
                        f"A decline of {abs(growth.percent_change)}% over "
                        f"{growth.window_days:.0f} days. Follower loss usually follows a "
                        "format change or a run of promotional posts — check what "
                        "shifted at the start of the decline."
                        if growth.percent_change is not None and growth.window_days
                        else "The follower count has declined since the first snapshot."
                    ),
                    evidence={
                        "absolute_change": growth.absolute_change,
                        "percent_change": growth.percent_change,
                    },
                )
            )

    # --- Week-on-week --------------------------------------------------------
    if previous_engagement and previous_engagement.post_count >= 3 and engagement.post_count >= 3:
        delta = engagement.median_engagement - previous_engagement.median_engagement
        if previous_engagement.median_engagement > 0:
            change_pct = delta / previous_engagement.median_engagement * 100
            if abs(change_pct) >= 15:
                direction = "up" if change_pct > 0 else "down"
                insights.append(
                    Insight(
                        key="wow_change",
                        severity="positive" if change_pct > 0 else "warning",
                        title=(
                            f"Median engagement is {direction} {abs(change_pct):.0f}% "
                            "on the previous period"
                        ),
                        body=(
                            f"{_format_number(engagement.median_engagement)} now against "
                            f"{_format_number(previous_engagement.median_engagement)} before. "
                            f"Based on {engagement.post_count} posts here and "
                            f"{previous_engagement.post_count} in the prior window."
                        ),
                        evidence={
                            "change_percent": round(change_pct, 1),
                            "current_median": engagement.median_engagement,
                            "previous_median": previous_engagement.median_engagement,
                        },
                    )
                )

    # --- Engagement quality --------------------------------------------------
    if engagement.post_count >= 5:
        spread_ratio = _safe_ratio(
            _percentile([float(p.engagement) for p in posts], 0.9),
            max(engagement.median_engagement, 1),
        )
        if spread_ratio and spread_ratio >= 5:
            p90 = _percentile([float(p.engagement) for p in posts], 0.9)
            insights.append(
                Insight(
                    key="engagement_skew",
                    severity="opportunity",
                    title="Engagement is concentrated in a few outliers",
                    body=(
                        f"The top tenth of posts clear {_format_number(p90)} engagements, "
                        f"while the typical post gets "
                        f"{_format_number(engagement.median_engagement)}. "
                        "A gap that wide means one format occasionally lands hard and "
                        "most of the rest is filler — worth finding what the outliers "
                        "share."
                    ),
                    evidence={
                        "p90_engagement": round(p90, 1),
                        "median_engagement": engagement.median_engagement,
                        "ratio": round(spread_ratio, 2),
                    },
                )
            )

    # --- Audience conversation ----------------------------------------------
    if engagement.total_engagement > 0:
        comment_share = sum(p.comments for p in posts) / engagement.total_engagement * 100
        if comment_share < 3 and engagement.post_count >= 5:
            insights.append(
                Insight(
                    key="low_comments",
                    severity="opportunity",
                    title="Almost nobody is replying",
                    body=(
                        f"Comments are {comment_share:.1f}% of all engagement. Likes are "
                        "cheap and comments are not, so a number this low usually means "
                        "the captions do not ask anything and there is no reason to "
                        "answer. One question at the end of a post is a free test."
                    ),
                    evidence={"comment_share_percent": round(comment_share, 1)},
                )
            )
        elif comment_share >= 8:
            insights.append(
                Insight(
                    key="high_comments",
                    severity="positive",
                    title="People are actually talking",
                    body=(
                        f"Comments make up {comment_share:.1f}% of engagement. That is "
                        "unusually high and it is the signal the reach algorithm "
                        "weights most heavily."
                    ),
                    evidence={"comment_share_percent": round(comment_share, 1)},
                )
            )

    # --- Video ---------------------------------------------------------------
    if video and video.videos_analysed >= 3 and video.median_views > 0:
        per_view = video.median_watch_seconds_per_view
        if per_view is not None:
            insights.append(
                Insight(
                    key="video_retention",
                    severity="opportunity",
                    title="Viewers watch only a short stretch of each video",
                    body=(
                        f"The average viewer watches {per_view:.0f} seconds of a video, "
                        f"across {video.videos_analysed} videos averaging "
                        f"{_format_number(video.median_views)} views. That is the "
                        "number to move: the first three seconds decide it, and "
                        "nothing later in the clip can recover a viewer who left."
                    ),
                    evidence={
                        "median_watch_seconds_per_view": per_view,
                        "median_views": video.median_views,
                    },
                )
            )

    # --- Small sample caveat -------------------------------------------------
    if engagement.post_count < 10:
        insights.append(
            Insight(
                key="small_sample",
                severity="warning",
                title=f"Only {engagement.post_count} posts in this window",
                body=(
                    "Every median on this page is computed from a small sample, so "
                    "treat rankings as hints rather than findings. Fifteen posts is "
                    "roughly where a format comparison starts to hold up."
                ),
                evidence={"post_count": engagement.post_count},
            )
        )

    # --- Page health ---------------------------------------------------------
    if page.followers > 0 and page.talking_about > 0:
        rate = page.talking_about / page.followers * 100
        if rate >= 10:
            insights.append(
                Insight(
                    key="high_talking_about",
                    severity="positive",
                    title="Conversation rate is unusually high",
                    body=(
                        f"{page.talking_about:,} people are 'talking about' this page — "
                        f"{rate:.1f}% of {page.followers:,} followers. Anything above "
                        "5% is a page whose audience actively reacts rather than "
                        "passively follows."
                    ),
                    evidence={"talking_about_rate": round(rate, 1)},
                )
            )

    severity_order = {"critical": 0, "warning": 1, "opportunity": 2, "positive": 3}
    insights.sort(key=lambda i: severity_order.get(i.severity, 9))
    return insights


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def analyse_page(
    *,
    page: PageRecord,
    posts: list[PostRecord],
    snapshots: list[FollowerSnapshot] | None = None,
    previous_posts: list[PostRecord] | None = None,
    tz: timezone = timezone(timedelta(hours=5)),
    window_days: int = 30,
    now: datetime | None = None,
) -> PageAnalysis:
    """Run every metric and insight in one pass."""
    snapshots = snapshots or []
    previous_posts = previous_posts or []
    now = now or datetime.now(UTC)

    engagement = summarise_engagement(posts, page.followers)
    cadence = summarise_cadence(posts, window_days)
    media_mix = analyse_media_mix(posts, page.followers)
    best_slots = analyse_time_slots(posts, tz)
    growth = compute_growth(snapshots, page.followers, page.baseline_followers)
    top_posts, weakest_posts = rank_posts(posts, page.followers)
    video = summarise_video(posts)

    previous_engagement = (
        summarise_engagement(previous_posts, page.followers) if previous_posts else None
    )

    insights = build_insights(
        page=page,
        posts=posts,
        engagement=engagement,
        cadence=cadence,
        media_mix=media_mix,
        best_slots=best_slots,
        growth=growth,
        video=video,
        previous_engagement=previous_engagement,
    )

    follower_ratio = _safe_ratio(page.likes, page.followers) if page.followers else None
    talking_rate = _safe_ratio(page.talking_about, page.followers) * 100 if page.followers else None

    return PageAnalysis(
        page_name=page.name,
        generated_at=now,
        window_days=window_days,
        post_count=len(posts),
        engagement=engagement,
        cadence=cadence,
        media_mix=media_mix,
        best_slots=best_slots,
        growth=growth,
        top_posts=top_posts,
        weakest_posts=weakest_posts,
        video=video,
        insights=insights,
        follower_ratio=round(follower_ratio, 3) if follower_ratio is not None else None,
        talking_about_rate_percent=round(talking_rate, 2) if talking_rate is not None else None,
    )
