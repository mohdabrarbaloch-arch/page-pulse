# Architecture

## What this is

A Facebook page analytics tool. You give it a page; it gives you engagement,
posting cadence, format performance, best posting times, follower growth, and a
list of findings that each cite the numbers behind them.

The design constraint that shapes everything: **a number without its
denominator is a lie waiting to happen.** So every metric carries the sample it
was computed over, and the UI shows that sample next to the figure.

## System diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│  Browser                                                             │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  index.html + styles.css + app.js   (no build step)            │  │
│  │  · auth gate · page switcher · metric cards · insights         │  │
│  │  · Chart.js (CDN) for the two time-series charts               │  │
│  └───────────────────────────┬────────────────────────────────────┘  │
└──────────────────────────────┼───────────────────────────────────────┘
                               │  fetch() + Bearer JWT
┌──────────────────────────────▼───────────────────────────────────────┐
│  FastAPI                                                             │
│                                                                      │
│  middleware:  security headers · CORS · rate limit · request id      │
│                                                                      │
│  routers/                                                            │
│    auth.py    register · login · me                                  │
│    pages.py   list · demo · live · import · analysis · delete        │
│                                                                      │
│  services/                                                           │
│    analytics.py   ← pure functions, no I/O, no DB, no clock          │
│    pages.py       ← ORM ↔ dataclass translation, persistence         │
│    demo_data.py   ← deterministic synthetic pages                    │
│    graph_api.py   ← Facebook Graph API adapter                       │
└──────────────────────────────┬───────────────────────────────────────┘
                               │  SQLAlchemy 2.0
┌──────────────────────────────▼───────────────────────────────────────┐
│  Database                                                            │
│    users · pages · posts · metric_snapshots · import_records         │
│    SQLite by default · PostgreSQL via docker-compose                 │
└──────────────────────────────────────────────────────────────────────┘
```

## The layering rule

`analytics.py` imports nothing from `app.models`, `app.database` or FastAPI. It
takes frozen dataclasses (`PostRecord`, `PageRecord`, `FollowerSnapshot`) and
returns frozen dataclasses. That is why the whole engine is testable in
milliseconds with no fixtures, no database and no network — and why the 40-odd
analytics tests run in about two seconds.

`services/pages.py` is the only module that knows both worlds. It reads ORM
rows, converts them to the dataclasses, calls the engine, and maps the result
back into response schemas.

## Data flow: one analysis request

```
GET /api/pages/7/analysis?days=180
  │
  ├─ deps.get_current_user      → 401 if the JWT is missing or invalid
  ├─ get_page_for_user          → 404 if the page is not this user's
  │
  ├─ analyse_stored_page
  │    ├─ load posts in window  → PostRecord[]
  │    ├─ load snapshots        → FollowerSnapshot[]
  │    ├─ normalise datetimes   → SQLite returns naive; attach UTC
  │    └─ analytics.analyse_page
  │         ├─ summarise_engagement   (rate + its own sample size)
  │         ├─ summarise_cadence      (posts/week, gaps, consistency)
  │         ├─ analyse_media_mix      (share of output vs share of engagement)
  │         ├─ analyse_time_slots     (4-hour bands, confidence per band)
  │         ├─ compute_growth         (absolute + %, None if baseline is 0)
  │         ├─ rank_posts             (top and weakest)
  │         ├─ summarise_video        (watch time per view, not total)
  │         └─ build_insights         (rules that must cite a number)
  │
  └─ build daily timeline → AnalysisOut
```

## Metric definitions, and why each is defined that way

| Metric | Definition | Why |
|---|---|---|
| Engagement rate | `engagement / reach`, falling back to `engagement / followers` | Reach is the platform's own denominator. The fallback is labelled, because the two are not comparable. |
| Rate sample | Posts that reported reach, out of all posts in the window | A page whose reels report reach and whose photos do not would otherwise show a reel-only rate as if it described the page. |
| Typical engagement | Median, with the mean shown alongside | One viral post moves the mean by an order of magnitude and the median by nothing. |
| Cadence | Posts per week, median gap, longest gap | "Posts per week" alone hides a page that posts 8 times in one day and then vanishes for a month. |
| Format performance | Share of engagement ÷ share of output | A format that is 25% of output and 60% of engagement is worth more of. Raw totals just reward whichever format you post most. |
| Best time | 4-hour weekday bands, each with a confidence level | A 48-post page has at most two posts per exact hour, so an hour-level "best time" is really the best single post. |
| Watch time | `watch_seconds / views` | `watch_seconds` is a total across all views. Reporting it raw gave a median of 600 seconds against sub-minute clips. |
| Growth % | `None` when the baseline is zero | Dividing by zero is not an infinite growth rate, it is an undefined one. |

## Insights

`build_insights` is a list of rules. Each rule fires only when it can cite a
number, and each returns an `Insight` with a severity, a title, a body that
interpolates its own figures, and an `evidence` dict.

A rule that cannot cite a number raises nothing. "Post more consistently" is
advice; "1.7 posts/week against a 6-day median gap" is analysis.

Severity ordering is enforced: `critical` → `warning` → `opportunity` →
`positive`. A test pins this, because a critical finding sitting below an
opportunity is a UI bug that no one notices until it matters.

## Data sources

Three, and the API always says which one produced the response:

| Source | How | Banner |
|---|---|---|
| `demo` | Deterministic generator, seeded per slug | Amber: "sample data" |
| `live` | Facebook Graph API with a page access token | Green: "live" |
| `import` | `POST /api/pages/{id}/import` with a JSON post list | Purple: "imported" |

The demo generator is deterministic — same slug, same output — so screenshots
and tests are reproducible. It is also *labelled* everywhere it appears,
including in the API response and on every screen of the dashboard.

## Security

- **Passwords**: bcrypt, cost 12. Never logged, never returned.
- **Sessions**: JWT, HS256, 12-hour expiry, `sub` = user id.
- **Ownership**: every page query is scoped by `owner_id`. A page belonging to
  another user returns 404, not 403 — the existence of someone else's page is
  not information this API gives away.
- **Rate limiting**: sliding window, per client IP. Tighter on auth endpoints
  (10/min) than the API (60/min).
- **Headers**: CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy`, `Permissions-Policy`.
- **Secrets**: environment only. The app refuses to start in production with
  the default `SECRET_KEY`.
- **Input**: Pydantic models with bounds on every numeric field; the analysis
  window is clamped to 7–730 days.

## Scaling notes

What is fine now and what breaks first:

1. **Rate limiter is per-process.** N workers = N× the budget. Redis is the
   answer; the interface is already a single `check(key)` call.
2. **Analysis is computed per request.** For a page with a few hundred posts
   that is microseconds. At tens of thousands, cache the result keyed by
   `(page_id, window_days, last_post_at)`.
3. **`create_all()` instead of migrations.** Fine for a fresh install, wrong for
   a schema that will change. Alembic is the next step.
4. **No background sync.** Live pages are fetched on demand. A scheduled job
   that refreshes every page nightly would make the dashboard instant and let
   the growth chart fill in properly.
5. **SQLite writes serialise.** Fine for a single user; PostgreSQL is already
   wired up in `docker-compose.yml` for anything more.

## What this deliberately does not do

- It does not scrape Facebook. Scraping violates the terms and breaks weekly.
  Live data comes from the Graph API with a token you own.
- It does not invent reach or impressions when the API does not return them. It
  falls back to followers and says so.
- It does not present a single post as a pattern. Every ranked bucket carries a
  confidence level, and thin buckets are marked `insufficient`.
