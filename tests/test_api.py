"""API tests: auth, page lifecycle, analysis and error handling.

Each test gets its own SQLite file so nothing leaks between them.
"""

from __future__ import annotations

import itertools
import os
import tempfile

import pytest

# Point the app at a throwaway database before it is imported. The path is
# unique per run so a previous run's rows can never collide with this one's.
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="pagepulse-test-"), "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production-use-only"
os.environ["ENVIRONMENT"] = "test"
os.environ["RATE_LIMIT_ENABLED"] = "false"

from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app

Base.metadata.create_all(bind=engine)

client = TestClient(app)

CREDENTIALS = {"email": "tester@example.com", "password": "a-good-test-password"}


@pytest.fixture(scope="module")
def token() -> str:
    """A stable user for the auth tests."""
    client.post("/api/auth/register", json={**CREDENTIALS, "display_name": "Tester"})
    response = client.post("/api/auth/login", json=CREDENTIALS)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


_user_counter = itertools.count(1)


def _register_and_login(prefix: str = "user") -> str:
    """Register a unique user and return a bearer token."""
    email = f"{prefix}{next(_user_counter)}@example.com"
    password = "a-good-test-password"
    client.post("/api/auth/register", json={"email": email, "password": password})
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture
def auth() -> dict:
    """A brand-new user for each test.

    Page external_ids are unique per owner, so sharing one user across tests
    makes them collide on the second demo page of the same slug. A fresh user
    per test gives each one its own namespace.
    """
    return {"Authorization": f"Bearer {_register_and_login()}"}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health_reports_graph_configuration():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # The flag must be a real boolean so the UI can branch on it.
    assert isinstance(body["graph_configured"], bool)


def test_root_serves_the_dashboard():
    response = client.get("/")
    assert response.status_code == 200
    assert "Page Pulse" in response.text


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_register_returns_a_token_and_user():
    response = client.post(
        "/api/auth/register",
        json={"email": "new-user@example.com", "password": "another-good-password"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["access_token"]
    assert body["user"]["email"] == "new-user@example.com"


def test_register_rejects_a_duplicate_email():
    payload = {"email": "dupe@example.com", "password": "a-good-test-password"}
    assert client.post("/api/auth/register", json=payload).status_code == 201
    second = client.post("/api/auth/register", json=payload)
    assert second.status_code == 409


def test_register_rejects_a_short_password():
    response = client.post(
        "/api/auth/register", json={"email": "short@example.com", "password": "abc"}
    )
    assert response.status_code == 422


def test_register_rejects_an_invalid_email():
    response = client.post(
        "/api/auth/register", json={"email": "not-an-email", "password": "a-good-test-password"}
    )
    assert response.status_code == 422


def test_login_rejects_a_wrong_password():
    response = client.post(
        "/api/auth/login", json={"email": CREDENTIALS["email"], "password": "wrong-password"}
    )
    assert response.status_code == 401


def test_login_rejects_an_unknown_user():
    response = client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "whatever12345"}
    )
    assert response.status_code == 401


def test_me_requires_a_token():
    assert client.get("/api/auth/me").status_code == 401


def test_me_rejects_a_garbage_token():
    response = client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert response.status_code == 401


def test_me_returns_the_current_user(token):
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["email"] == CREDENTIALS["email"]


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def test_pages_require_authentication():
    assert client.get("/api/pages").status_code == 401


def test_demo_catalogue_is_listed(auth):
    response = client.get("/api/pages/demo", headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert len(body) >= 3
    assert {"slug", "name", "category", "followers"} <= set(body[0])


def test_creating_a_demo_page_returns_201(auth):
    response = client.post("/api/pages/demo", json={"slug": "robo-pk", "days": 90}, headers=auth)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"]
    assert body["source"] == "demo"
    assert body["post_count"] > 0


def test_creating_an_unknown_demo_page_returns_404(auth):
    response = client.post("/api/pages/demo", json={"slug": "does-not-exist"}, headers=auth)
    assert response.status_code == 404


def test_creating_a_demo_page_twice_returns_409(auth):
    client.post("/api/pages/demo", json={"slug": "chai-khana"}, headers=auth)
    second = client.post("/api/pages/demo", json={"slug": "chai-khana"}, headers=auth)
    assert second.status_code == 409


def test_live_import_without_a_token_is_refused_honestly(auth):
    """No FB_GRAPH_TOKEN means no live data -- and the API must say so."""
    response = client.post("/api/pages/live", json={"external_id": "1234567890"}, headers=auth)
    assert response.status_code == 503
    assert "token" in response.json()["detail"].lower()


def test_analysis_returns_every_section(auth):
    page_id = client.post(
        "/api/pages/demo", json={"slug": "thread-studio", "days": 180}, headers=auth
    ).json()["id"]

    response = client.get(f"/api/pages/{page_id}/analysis?days=180", headers=auth)
    assert response.status_code == 200, response.text
    body = response.json()

    for key in (
        "page",
        "engagement",
        "cadence",
        "media_mix",
        "best_slots",
        "growth",
        "top_posts",
        "weakest_posts",
        "insights",
        "timeline",
        "data_source",
        "data_source_note",
        "reporting_timezone",
    ):
        assert key in body, f"missing section: {key}"

    assert body["post_count"] > 0
    assert body["data_source"] == "demo"
    # The demo banner must be present so nobody mistakes it for real data.
    assert body["data_source_note"]


def test_analysis_rejects_an_unknown_page(auth):
    assert client.get("/api/pages/999999/analysis", headers=auth).status_code == 404


def test_analysis_rejects_an_out_of_range_window(auth):
    page_id = client.post(
        "/api/pages/demo", json={"slug": "robo-pk", "days": 30}, headers=auth
    ).json()["id"]
    assert client.get(f"/api/pages/{page_id}/analysis?days=0", headers=auth).status_code == 422
    assert client.get(f"/api/pages/{page_id}/analysis?days=9999", headers=auth).status_code == 422


def test_analysis_window_changes_the_post_count(auth):
    page_id = client.post(
        "/api/pages/demo", json={"slug": "chai-khana", "days": 180}, headers=auth
    ).json()["id"]
    wide = client.get(f"/api/pages/{page_id}/analysis?days=180", headers=auth).json()
    narrow = client.get(f"/api/pages/{page_id}/analysis?days=30", headers=auth).json()
    assert narrow["post_count"] <= wide["post_count"]


def test_import_endpoint_accepts_a_post_list(auth):
    page_id = client.post(
        "/api/pages/demo", json={"slug": "robo-pk", "days": 30}, headers=auth
    ).json()["id"]

    payload = [
        {
            "external_id": "imported-1",
            "message": "An imported post",
            "media_type": "photo",
            "posted_at": "2026-11-01T10:00:00Z",
            "likes": 42,
            "comments": 3,
            "shares": 1,
        }
    ]
    response = client.post(f"/api/pages/{page_id}/import", json=payload, headers=auth)
    # 200, not 201: the endpoint upserts, so a repeat import is not a new resource.
    assert response.status_code == 200, response.text
    assert response.json()["imported"] == 1

    listing = client.get(f"/api/pages/{page_id}/imports", headers=auth)
    assert listing.status_code == 200
    assert len(listing.json()) >= 1


def test_import_rejects_an_empty_post_list(auth):
    page_id = client.post(
        "/api/pages/demo", json={"slug": "thread-studio", "days": 30}, headers=auth
    ).json()["id"]
    response = client.post(f"/api/pages/{page_id}/import", json=[], headers=auth)
    assert response.status_code == 422


def test_delete_removes_the_page(auth):
    page_id = client.post(
        "/api/pages/demo", json={"slug": "robo-pk", "days": 30}, headers=auth
    ).json()["id"]
    assert client.delete(f"/api/pages/{page_id}", headers=auth).status_code == 204
    assert client.get(f"/api/pages/{page_id}", headers=auth).status_code == 404


def test_one_user_cannot_read_another_users_page():
    """Ownership must be enforced, not just authentication."""
    owner = _register_and_login("owner")
    intruder = _register_and_login("intruder")

    page_id = client.post(
        "/api/pages/demo",
        json={"slug": "robo-pk", "days": 30},
        headers={"Authorization": f"Bearer {owner}"},
    ).json()["id"]

    response = client.get(
        f"/api/pages/{page_id}/analysis", headers={"Authorization": f"Bearer {intruder}"}
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


def test_security_headers_are_present():
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" in response.headers
