# Setup

## Requirements

- Python 3.11 or newer
- pip
- Optional: Docker, if you would rather not install Python locally

No Node, no build step, no external services. The dashboard is plain HTML, CSS
and JavaScript served by the same process as the API.

---

## Local install

From the project folder:

```bash
cd facebook-page-analyzer
```

### 1. Virtual environment

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

### 2. Dependencies

```bash
pip install -r requirements.txt
```

### 3. Environment

```bash
cp .env.example .env
```

Generate a signing key and append it:

```bash
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))" >> .env
```

Everything else in `.env` has a working default. You do not need a Facebook
token to run the app.

### 4. Seed the demo data

```bash
python -m scripts.seed
```

This creates one account and three sample pages. It is safe to run twice —
existing records are reported and skipped.

### 5. Run

```bash
uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000**.

```
email     abraz@example.com
password  pagepulse-demo-2026
```

---

## Docker

```bash
cp .env.example .env
# edit .env and set a real SECRET_KEY — compose refuses to start without one
docker compose up --build
```

App on **http://127.0.0.1:8000**, PostgreSQL in a named volume.

To seed inside the container:

```bash
docker compose exec web python -m scripts.seed
```

---

## Configuration

Every setting is an environment variable. See `.env.example` for the annotated
list.

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | — | JWT signing key. **Required.** The app refuses to start in production with the default. |
| `DATABASE_URL` | `sqlite:///./pagepulse.db` | Any SQLAlchemy URL. |
| `ENVIRONMENT` | `development` | `development`, `production` or `test`. |
| `FB_GRAPH_TOKEN` | empty | Page access token. Empty means demo mode. |
| `FB_GRAPH_VERSION` | `v21.0` | Graph API version. |
| `CORS_ORIGINS` | empty | Comma-separated. Empty means same-origin only. |
| `RATE_LIMIT_ENABLED` | `true` | Turn off only behind a proxy that already limits. |
| `RATE_LIMIT_PER_MINUTE` | `60` | General API limit. |
| `AUTH_RATE_LIMIT_PER_MINUTE` | `10` | Login/register limit. |
| `SEED_DEMO_ON_STARTUP` | `false` | Seed sample pages on first boot. |

---

## Switching to PostgreSQL

```env
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/pagepulse
```

Install the driver:

```bash
pip install "psycopg[binary]"
```

Tables are created on startup. There are no migrations yet — see the
limitations section in the README.

---

## Running the tests

```bash
pytest
```

57 tests, about 10 seconds. The API tests create their own throwaway SQLite
database in a temp directory, so they never touch your development data.

```bash
ruff check app tests scripts
ruff format --check app tests scripts
```

---

## Optional: browser verification

`scripts/verify_ui.js` drives a real Chromium through the dashboard — sign in,
check every section rendered, switch the window, add a page, then repeat at
390px — and fails on any console error or failed request.

It needs Playwright, which is not a project dependency:

```bash
npm install playwright
npx playwright install chromium
node scripts/verify_ui.js
```

Screenshots land in `docs/screenshots/`.

---

## Troubleshooting

**`SECRET_KEY` error on startup**
You are running with `ENVIRONMENT=production` and the default key. Generate one
as shown above.

**`disk I/O error` from SQLite**
Usually a stale `pagepulse.db-wal` left by a killed process. Stop the server,
delete `pagepulse.db*`, and re-run the seed script.

**429 on login**
The auth limiter allows 10 attempts per minute. Wait, or set
`AUTH_RATE_LIMIT_PER_MINUTE` higher for local work.

**"Connect a real page" field is missing**
`FB_GRAPH_TOKEN` is not set. That is expected in demo mode — the dialog says so
rather than showing a field that would fail.

**Charts are blank**
Chart.js loads from a CDN. Offline, the charts do not render but every number
above them still does, and the chart subtitle says why.
