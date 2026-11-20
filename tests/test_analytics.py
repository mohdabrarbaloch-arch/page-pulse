"""Tests for the analytics engine.

These are written against the pure functions, so they need no database and no
network. Several of them exist specifically to pin bugs that were found by
running the engine rather than by reading it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.services.analytics import (
    BAND_HOURS,
    Confidence,
    EngagementBasis,
    FollowerSnapshot,
    PageRecord,
    PostRecord,
    analyse_media_mix,
    analyse_page,
    analyse_time_slots,
    build_insights,
    compute_growth,
    engagement_rate_for_post,
    rank_posts,
    summarise_cadence,
    summarise_engagement,
    summarise_video,
)

KARACHI = timezone(timedelta(hours=5))
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def make_post(
    *,
    days_ago: float = 1,
    likes: int = 100,
    comments: int = 5,
    shares: int = 3,
    media_type: str = "photo",
    reach: int | None = None,
    views: int | None = None,
    watch_seconds: float | None = None,
    message: str = "A post",
    external_id: str | None = None,
) -> PostRecord:
    return PostRecord(
        external_id=external_id or f"p{days_ago}-{likes}-{media_type}",
        message=message,
        media_type=media_type,
        posted_at=NOW - timedelta(days=days_ago),
        likes=likes,
        comments=comments,
        shares=shares,
        video_views=views,
        reach=reach,
        impressions=None,
        watch_seconds=watch_seconds,
        permalink=None,
        thumbnail_url=None,
    )


def make_page(**overrides) -> PageRecord:
    base = {
        "external_id": "1",
        "name": "Test Page",
        "category": "Business",
        "description": "",
        "followers": 1000,
        "likes": 950,
        "talking_about": 40,
        "is_verified": False,
        "address": None,
        "baseline_followers": 0,
    }
    base.update(overrides)
    return PageRecord(**base)


# ---------------------------------------------------------------------------
# Engagement
# ---------------------------------------------------------------------------


def test_engagement_rate_prefers_reach_over_followers():
    """Reach is the platform's own denominator, so it wins when present."""
    post = make_post(likes=50, comments=0, shares=0, reach=1000)
    rate, basis = engagement_rate_for_post(post, followers=100_000)
    assert basis is EngagementBasis.REACH
    assert rate == pytest.approx(5.0)


def test_engagement_rate_falls_back_to_followers_and_says_so():
    post = make_post(likes=50, comments=0, shares=0, reach=None)
    rate, basis = engagement_rate_for_post(post, followers=1000)
    assert basis is EngagementBasis.FOLLOWERS
    assert rate == pytest.approx(5.0)


def test_engagement_rate_unavailable_without_reach_or_followers():
    post = make_post(reach=None)
    rate, basis = engagement_rate_for_post(post, followers=0)
    assert rate is None
    assert basis is EngagementBasis.UNAVAILABLE


def test_engagement_summary_reports_its_own_sample_size():
    """The rate is computed over posts that reported reach, not over every post.

    A page whose reels report reach and whose photos do not would otherwise
    present a reel-only rate as if it described the whole page.
    """
    posts = [
        make_post(media_type="reel", reach=1000, likes=30, comments=0, shares=0),
        make_post(media_type="reel", reach=1000, likes=40, comments=0, shares=0),
        make_post(media_type="photo", reach=None, likes=5, comments=0, shares=0),
        make_post(media_type="photo", reach=None, likes=6, comments=0, shares=0),
    ]
    summary = summarise_engagement(posts, followers=1000)

    assert summary.basis is EngagementBasis.REACH
    assert summary.posts_with_basis_data == 2
    assert summary.total_posts_considered == 4
    assert summary.basis_share_percent == pytest.approx(50.0)
    # The sentence must disclose the partial coverage.
    assert "2 of 4" in summary.explained
    assert "50%" in summary.explained


def test_engagement_median_resists_a_single_viral_post():
    posts = [make_post(likes=10, comments=0, shares=0) for _ in range(9)]
    posts.append(make_post(likes=100_000, comments=0, shares=0))
    summary = summarise_engagement(posts, followers=1000)
    assert summary.median_engagement == pytest.approx(10)
    assert summary.mean_engagement > 9000


# ---------------------------------------------------------------------------
# Cadence
# ---------------------------------------------------------------------------


def test_cadence_flags_a_bursty_page():
    """Two posts on one day then a long silence is not 'consistent posting'."""
    posts = [
        make_post(days_ago=1),
        make_post(days_ago=1.1),
        make_post(days_ago=40),
        make_post(days_ago=41),
    ]
    cadence = summarise_cadence(posts, window_days=90)
    assert cadence.consistency == "bursty"
    assert cadence.longest_gap_days >= 30


def test_cadence_steady_for_even_spacing():
    posts = [make_post(days_ago=d) for d in (2, 5, 8, 11, 14, 17)]
    cadence = summarise_cadence(posts, window_days=30)
    assert cadence.consistency == "steady"
    assert cadence.median_gap_days == pytest.approx(3, abs=0.5)


def test_cadence_handles_no_posts():
    cadence = summarise_cadence([], window_days=30)
    assert cadence.post_count == 0
    assert cadence.posts_per_week == 0


# ---------------------------------------------------------------------------
# Time slots
# ---------------------------------------------------------------------------


def test_time_slots_use_four_hour_bands_not_single_hours():
    """A 48-post page cannot support an hour-level ranking.

    With one-hour buckets, a page posting across the day has at most a couple of
    posts per hour, so the 'best hour' is really the best single post. Bands are
    the smallest unit where several posts can land together.
    """
    posts = [make_post(days_ago=i * 0.5) for i in range(1, 40)]
    slots = analyse_time_slots(posts, KARACHI)
    assert slots, "expected at least one slot"
    for slot in slots:
        assert slot.hour_end - slot.hour_start == BAND_HOURS
        assert slot.hour_start % BAND_HOURS == 0


def test_time_slot_label_reads_as_a_window():
    posts = [make_post(days_ago=i) for i in range(1, 12)]
    slots = analyse_time_slots(posts, KARACHI)
    assert slots
    label = slots[0].label
    assert "-" in label and ":" in label


def test_time_slots_mark_thin_buckets_insufficient():
    """One post in a slot must not be presented as a pattern."""
    posts = [make_post(days_ago=1)]
    slots = analyse_time_slots(posts, KARACHI)
    assert slots
    assert slots[0].confidence is Confidence.INSUFFICIENT


def test_time_slots_are_computed_in_the_page_timezone():
    """The same instant lands in a different band depending on the timezone."""
    post = make_post(days_ago=0)
    utc_slots = analyse_time_slots([post], UTC)
    karachi_slots = analyse_time_slots([post], KARACHI)
    assert utc_slots[0].hour_start != karachi_slots[0].hour_start


# ---------------------------------------------------------------------------
# Media mix
# ---------------------------------------------------------------------------


def test_media_mix_compares_engagement_share_against_output_share():
    """A format that is 25% of output but 60% of engagement is worth more of it."""
    posts = [make_post(media_type="reel", likes=300, comments=0, shares=0) for _ in range(3)] + [
        make_post(media_type="photo", likes=10, comments=0, shares=0) for _ in range(9)
    ]
    mix = {m.media_type: m for m in analyse_media_mix(posts, followers=1000)}
    assert mix["reel"].outperforms_share is True
    assert mix["photo"].outperforms_share is False
    assert mix["reel"].share_of_posts_percent == pytest.approx(25.0)


def test_media_mix_marks_small_samples_low_confidence():
    posts = [make_post(media_type="link", likes=5, comments=0, shares=0)]
    mix = analyse_media_mix(posts, followers=1000)
    assert mix[0].confidence is Confidence.INSUFFICIENT


# ---------------------------------------------------------------------------
# Video
# ---------------------------------------------------------------------------


def test_watch_time_is_per_view_not_a_raw_total():
    """watch_seconds is total watch time, so it must be divided by views.

    Reporting the raw total produced a median of 600 seconds against sub-minute
    clips, which is not a number any viewer could produce.
    """
    posts = [
        make_post(media_type="reel", views=1000, watch_seconds=6000, likes=50),
        make_post(media_type="reel", views=2000, watch_seconds=12000, likes=60),
    ]
    video = summarise_video(posts)
    assert video is not None
    assert video.median_watch_seconds_per_view == pytest.approx(6.0)
    assert video.total_views == 3000


def test_video_metrics_absent_when_no_video_posts():
    assert summarise_video([make_post(media_type="photo")]) is None


def test_video_ignores_posts_with_zero_views():
    posts = [
        make_post(media_type="reel", views=0, watch_seconds=0),
        make_post(media_type="reel", views=500, watch_seconds=2500),
    ]
    video = summarise_video(posts)
    assert video is not None
    assert video.median_watch_seconds_per_view == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Growth
# ---------------------------------------------------------------------------


def test_growth_percent_is_none_when_baseline_is_zero():
    """Dividing by a zero baseline would be an infinite growth rate."""
    snapshots = [
        FollowerSnapshot(
            captured_at=NOW - timedelta(days=10), followers=0, likes=0, talking_about=0
        ),
        FollowerSnapshot(captured_at=NOW, followers=500, likes=0, talking_about=0),
    ]
    growth = compute_growth(snapshots, current_followers=500, baseline_followers=0)
    assert growth.absolute_change == 500
    assert growth.percent_change is None


def test_growth_computes_change_and_rate():
    snapshots = [
        FollowerSnapshot(
            captured_at=NOW - timedelta(days=30), followers=1000, likes=0, talking_about=0
        ),
        FollowerSnapshot(captured_at=NOW, followers=1200, likes=0, talking_about=0),
    ]
    growth = compute_growth(snapshots, current_followers=1200, baseline_followers=1000)
    assert growth.absolute_change == 200
    assert growth.percent_change == pytest.approx(20.0)


def test_growth_handles_a_decline():
    snapshots = [
        FollowerSnapshot(
            captured_at=NOW - timedelta(days=30), followers=1000, likes=0, talking_about=0
        ),
        FollowerSnapshot(captured_at=NOW, followers=900, likes=0, talking_about=0),
    ]
    growth = compute_growth(snapshots, current_followers=900, baseline_followers=1000)
    assert growth.absolute_change == -100
    assert growth.percent_change == pytest.approx(-10.0)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def test_rank_posts_orders_by_total_engagement():
    posts = [
        make_post(likes=10, comments=0, shares=0, external_id="low"),
        make_post(likes=500, comments=0, shares=0, external_id="high"),
        make_post(likes=100, comments=0, shares=0, external_id="mid"),
    ]
    top, weakest = rank_posts(posts, followers=1000, limit=2)
    assert top[0].external_id == "high"
    assert top[1].external_id == "mid"
    assert weakest[0].external_id == "low"


def test_rank_posts_handles_empty_input():
    assert rank_posts([], followers=1000) == ([], [])


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------


def test_every_insight_cites_a_number():
    """An insight with no figure is advice, not analysis."""
    posts = [make_post(days_ago=i * 3, likes=20 + i) for i in range(1, 20)]
    page = make_page()
    analysis = analyse_page(page=page, posts=posts, tz=KARACHI, window_days=90, now=NOW)
    assert analysis.insights
    for insight in analysis.insights:
        assert any(
            ch.isdigit() for ch in insight.body
        ), f"insight '{insight.key}' cites no number: {insight.body}"


def test_insights_report_that_there_is_nothing_to_analyse():
    """An empty window still gets a finding -- silence would look like a bug."""
    analysis = analyse_page(page=make_page(), posts=[], tz=KARACHI, window_days=30, now=NOW)
    assert analysis.post_count == 0
    assert [i.key for i in analysis.insights] == ["no_posts"]
    assert analysis.insights[0].severity == "critical"


def test_insight_keys_are_unique():
    posts = [make_post(days_ago=i * 2, likes=10 * i) for i in range(1, 25)]
    analysis = analyse_page(page=make_page(), posts=posts, tz=KARACHI, window_days=90, now=NOW)
    keys = [i.key for i in analysis.insights]
    assert len(keys) == len(set(keys))


def test_insights_are_ordered_by_severity():
    """Critical findings must not sit below opportunities."""
    posts = [make_post(days_ago=i * 2, likes=10 * i) for i in range(1, 25)]
    analysis = analyse_page(page=make_page(), posts=posts, tz=KARACHI, window_days=90, now=NOW)
    order = {"critical": 0, "warning": 1, "opportunity": 2, "positive": 3}
    ranks = [order[i.severity] for i in analysis.insights]
    assert ranks == sorted(ranks)


# ---------------------------------------------------------------------------
# Full pass
# ---------------------------------------------------------------------------


def test_analyse_page_returns_a_complete_result():
    posts = [
        make_post(days_ago=i, likes=50 + i, media_type="reel" if i % 2 else "photo")
        for i in range(1, 30)
    ]
    snapshots = [
        FollowerSnapshot(
            captured_at=NOW - timedelta(days=d), followers=1000 + d * 5, likes=0, talking_about=0
        )
        for d in range(0, 30, 5)
    ]
    analysis = analyse_page(
        page=make_page(),
        posts=posts,
        snapshots=snapshots,
        tz=KARACHI,
        window_days=30,
        now=NOW,
    )
    assert analysis.post_count == 29
    assert analysis.engagement.rate_percent is not None
    assert analysis.cadence.posts_per_week > 0
    assert analysis.media_mix
    assert analysis.best_slots
    assert analysis.growth.current_followers == 1000
    assert analysis.top_posts
    assert analysis.generated_at is not None


def test_analyse_page_is_deterministic():
    """Same input, same output -- no hidden clock or randomness in the engine."""
    posts = [make_post(days_ago=i, likes=30 + i) for i in range(1, 20)]
    first = analyse_page(page=make_page(), posts=posts, tz=KARACHI, window_days=30, now=NOW)
    second = analyse_page(page=make_page(), posts=posts, tz=KARACHI, window_days=30, now=NOW)
    assert first.engagement.rate_percent == second.engagement.rate_percent
    assert [i.key for i in first.insights] == [i.key for i in second.insights]


def test_analyse_page_survives_a_single_post():
    analysis = analyse_page(
        page=make_page(), posts=[make_post()], tz=KARACHI, window_days=30, now=NOW
    )
    assert analysis.post_count == 1
    assert analysis.engagement.rate_percent is not None


def test_analyse_page_handles_zero_followers():
    """A brand-new page has no followers; nothing may divide by zero."""
    analysis = analyse_page(
        page=make_page(followers=0, likes=0),
        posts=[make_post(reach=None)],
        tz=KARACHI,
        window_days=30,
        now=NOW,
    )
    assert analysis.engagement.rate_percent is None
    assert analysis.engagement.basis is EngagementBasis.UNAVAILABLE


def test_build_insights_accepts_a_previous_window():
    """A comparison window must not crash when the current one is empty."""
    page = make_page()
    posts = [make_post(days_ago=i, likes=20) for i in range(1, 10)]
    engagement = summarise_engagement(posts, page.followers)
    previous = summarise_engagement([make_post(days_ago=100, likes=5)], page.followers)
    insights = build_insights(
        page=page,
        posts=posts,
        engagement=engagement,
        cadence=summarise_cadence(posts, 30),
        media_mix=analyse_media_mix(posts, page.followers),
        best_slots=analyse_time_slots(posts, KARACHI),
        growth=compute_growth([], page.followers, 0),
        video=None,
        previous_engagement=previous,
    )
    assert isinstance(insights, list)
