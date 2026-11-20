# API reference

Base URL: `http://127.0.0.1:8000`

All endpoints except `/health` and `/` require a bearer token:

```
Authorization: Bearer <access_token>
```

Interactive docs are served at `/docs` (Swagger) and `/redoc` while the app is
running.

---

## Errors

Every error returns a JSON body with a `detail` field.

```json
{ "detail": "You have already added 'ROBO .PK'. Delete it first to re-import." }
```

Validation errors (422) return a structured list:

```json
{
  "detail": "The request body failed validation.",
  "errors": [{ "field": "body", "message": "Input should be a valid list", "type": "list_type" }]
}
```

| Status | Meaning |
|---|---|
| 200 | OK |
| 201 | Created |
| 204 | Deleted, no body |
| 401 | Missing or invalid token |
| 403 | Authenticated but not permitted |
| 404 | Not found, or not yours |
| 409 | Already exists |
| 422 | Validation failed |
| 429 | Rate limited — `Retry-After` header is set |
| 502 | Upstream (Facebook) returned an error |
| 503 | Feature unavailable (no Graph token configured) |

---

## Health

### `GET /health`

No authentication.

```json
{
  "status": "ok",
  "version": "1.0.0",
  "environment": "development",
  "graph_configured": false
}
```

`graph_configured` tells the UI whether to offer the "connect a real page"
field. It is a real boolean, not a string.

---

## Auth

### `POST /api/auth/register`

```json
{ "email": "you@example.com", "password": "at-least-8-chars", "display_name": "Your Name" }
```

**201** →

```json
{
  "access_token": "eyJhbGci...",
  "token_type": "bearer",
  "user": { "id": 1, "email": "you@example.com", "display_name": "Your Name" }
}
```

| Status | When |
|---|---|
| 201 | Created |
| 409 | Email already registered |
| 422 | Invalid email, or password under 8 characters |

### `POST /api/auth/login`

Same body shape (email + password). **200** with the same response shape.
**401** for a wrong password or unknown user — deliberately the same response
for both, so the endpoint does not confirm which emails exist.

Rate limited to 10 attempts per minute per IP.

### `GET /api/auth/me`

**200** → `{ "id": 1, "email": "...", "display_name": "..." }`

---

## Pages

### `GET /api/pages`

**200** →

```json
{
  "pages": [
    {
      "id": 1,
      "external_id": "demo-robo-pk",
      "name": "ROBO .PK",
      "category": "Home & Garden",
      "followers": 4180,
      "likes": 4025,
      "talking_about": 268,
      "is_verified": false,
      "source": "demo",
      "post_count": 75,
      "last_synced_at": "2026-11-20T13:30:00Z"
    }
  ],
  "total": 1
}
```

### `GET /api/pages/demo`

The catalogue of sample pages. **200** →

```json
[
  { "slug": "robo-pk", "name": "ROBO .PK", "category": "Home & Garden", "followers": 4180 },
  { "slug": "chai-khana", "name": "Chai Khana Karachi", "category": "Restaurant", "followers": 12480 },
  { "slug": "thread-studio", "name": "Thread Studio", "category": "Clothing Store", "followers": 8640 }
]
```

### `POST /api/pages/demo`

```json
{ "slug": "robo-pk", "days": 180 }
```

`days` is optional (default 180, range 7–730) and controls how much history the
generator produces.

**201** → a page object. **404** for an unknown slug. **409** if you already
added it.

### `POST /api/pages/live`

```json
{ "external_id": "61552661620882", "days": 180 }
```

**201** → a page object. **503** if no `FB_GRAPH_TOKEN` is configured. **403** if
the token lacks permission for that page. **502** if Facebook returns an error.

### `GET /api/pages/{page_id}`

**200** → a page object. **404** if it does not exist or is not yours.

### `DELETE /api/pages/{page_id}`

**204**, no body. Cascades to posts, snapshots and import records.

---

## Analysis

### `GET /api/pages/{page_id}/analysis?days=180`

The main endpoint. `days` defaults to 180, range 7–730.

```json
{
  "page": { "id": 1, "name": "ROBO .PK", "followers": 4180, "...": "..." },
  "window_days": 180,
  "post_count": 75,
  "data_source": "demo",
  "data_source_note": "This page is running on generated sample data...",
  "reporting_timezone": "Asia/Karachi (UTC+5)",
  "generated_at": "2026-11-20T13:31:02Z",

  "engagement": {
    "rate_percent": 2.4,
    "basis": "reach",
    "median_engagement": 96.0,
    "mean_engagement": 118.4,
    "post_count": 75,
    "posts_with_basis_data": 38,
    "total_posts_considered": 75,
    "basis_share_percent": 50.7,
    "explained": "2.40% median engagement per reach, computed over 38 of 75 posts (51% of the window) — the ones that reported reach, not the whole page."
  },

  "cadence": {
    "post_count": 75,
    "posts_per_week": 2.9,
    "median_gap_days": 2.4,
    "longest_gap_days": 9.1,
    "consistency": "steady"
  },

  "media_mix": [
    {
      "media_type": "reel",
      "post_count": 34,
      "share_of_posts_percent": 45.3,
      "share_of_engagement_percent": 61.2,
      "median_engagement": 142.0,
      "outperforms_share": true,
      "confidence": "high"
    }
  ],

  "best_slots": [
    {
      "weekday": 6,
      "weekday_label": "Sunday",
      "hour_start": 16,
      "hour_end": 20,
      "label": "Sunday, 16:00-20:00",
      "post_count": 7,
      "median_engagement": 168.0,
      "confidence": "high"
    }
  ],

  "growth": {
    "start_followers": 3393,
    "current_followers": 4180,
    "absolute_change": 787,
    "percent_change": 23.19,
    "window_days": 180,
    "per_week": 30.6,
    "note": "787 followers added over 180 days."
  },

  "video": {
    "videos_analysed": 34,
    "median_views": 8000.5,
    "median_watch_seconds_per_view": 7.4,
    "total_views": 191894
  },

  "top_posts": [
    {
      "external_id": "demo-robo-pk-42",
      "excerpt": "Dry run test on the bench: listen for the cutout.",
      "media_type": "reel",
      "posted_at": "2026-10-14T18:20:00Z",
      "likes": 312,
      "comments": 18,
      "shares": 41,
      "engagement": 371,
      "engagement_rate_percent": 4.1,
      "permalink": null
    }
  ],

  "weakest_posts": [],

  "insights": [
    {
      "key": "reel_dominance",
      "severity": "opportunity",
      "title": "Reel posts earn the most engagement",
      "body": "Reels are 45% of your output but 61% of your engagement...",
      "evidence": { "share_of_posts_percent": 45.3, "share_of_engagement_percent": 61.2 }
    }
  ],

  "timeline": [
    { "date": "2026-05-24", "posts": 0, "engagement": 0, "followers": 3393 }
  ]
}
```

**Reading the response:**

- `engagement.basis` is `reach`, `followers` or `unavailable`. Never compare a
  `followers`-based rate against a `reach`-based benchmark.
- `engagement.basis_share_percent` is how much of the window the rate actually
  covers. Below 100, the rate describes a subset.
- `confidence` on a slot or format is `high`, `medium`, `low` or `insufficient`.
  `insufficient` means do not act on it.
- `growth.percent_change` is `null` when the baseline is zero.
- `timeline[].followers` is `null` on days with no recorded snapshot. The chart
  does not interpolate.

---

## Import

### `POST /api/pages/{page_id}/import`

Body is a **JSON array** of posts (not an object wrapping one).

```json
[
  {
    "external_id": "post-1",
    "message": "Our new controller is in stock",
    "media_type": "photo",
    "posted_at": "2026-11-01T10:00:00Z",
    "likes": 42,
    "comments": 3,
    "shares": 1,
    "reach": 900,
    "video_views": null,
    "watch_seconds": null,
    "permalink": null
  }
]
```

| Field | Required | Notes |
|---|---|---|
| `external_id` | yes | Upsert key. Re-importing updates. |
| `posted_at` | yes | ISO 8601. Naive values are treated as UTC. |
| `media_type` | no | `photo`, `reel`, `video`, `link`, `status`. Default `status`. |
| `likes` / `comments` / `shares` | no | Default 0. |
| `reach` | no | Enables reach-based rates for this post. |
| `video_views` / `watch_seconds` | no | `watch_seconds` is a **total** across views. |

**200** →

```json
{ "imported": 1, "skipped": 0, "warnings": [] }
```

200 rather than 201 because the endpoint upserts — a repeat import is not a new
resource. **422** for an empty array or a malformed post.

### `GET /api/pages/{page_id}/imports`

**200** → import history, newest first.

```json
[
  {
    "id": 3,
    "source": "demo",
    "posts_imported": 75,
    "posts_skipped": 0,
    "detail": { "slug": "robo-pk", "days": 180 },
    "created_at": "2026-11-20T13:30:00Z"
  }
]
```

---

## Rate limits

| Scope | Limit |
|---|---|
| Auth endpoints | 10 requests / minute / IP |
| Everything else | 60 requests / minute / IP |

Exceeding either returns **429** with a `Retry-After` header in seconds.

Set `RATE_LIMIT_ENABLED=false` only when a proxy in front of the app already
limits requests.
