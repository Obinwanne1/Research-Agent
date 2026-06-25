"""
End-to-end tests for ResearchAgent.
Run: pytest tests/test_app.py -v
"""
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("DATABASE_PATH", ":memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest")
os.environ.setdefault("RESEARCH_BASE_DIR", "research_test")
os.environ.setdefault("DEBUG", "true")

import models
from app import app as flask_app


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("models.Config.DATABASE_PATH", db_path)
    monkeypatch.setattr("config.Config.DATABASE_PATH", db_path)
    monkeypatch.setattr("config.Config.RESEARCH_BASE_DIR", str(tmp_path / "research"))
    models.init_db()
    yield


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    flask_app.config["SESSION_COOKIE_SECURE"] = False
    with flask_app.test_client() as c:
        yield c


def _csrf(client):
    """Get a CSRF token by hitting the login page."""
    with client.session_transaction() as sess:
        import secrets
        token = secrets.token_hex(32)
        sess["csrf_token"] = token
    return token


def _register(client, email="test@example.com", password="Test@1234!", name="Tester"):
    token = _csrf(client)
    return client.post("/register", data={
        "email": email,
        "password": password,
        "display_name": name,
        "csrf_token": token,
    }, follow_redirects=True)


def _login(client, email="test@example.com", password="Test@1234!"):
    token = _csrf(client)
    return client.post("/login", data={
        "email": email,
        "password": password,
        "csrf_token": token,
    }, follow_redirects=True)


# ── Auth ──────────────────────────────────────────────────────────────────────

class TestRegister:
    def test_first_user_becomes_superadmin(self, client):
        _register(client)
        user = models.get_user_by_email("test@example.com")
        assert user["role"] == "superadmin"

    def test_second_user_is_user(self, client):
        _register(client, "a@example.com")
        with client.session_transaction() as sess:
            sess.clear()
        _register(client, "b@example.com")
        assert models.get_user_by_email("b@example.com")["role"] == "user"

    def test_duplicate_email_rejected(self, client):
        _register(client)
        with client.session_transaction() as sess:
            sess.clear()
        r = _register(client)
        assert b"already exists" in r.data

    def test_weak_password_rejected_no_special(self, client):
        token = _csrf(client)
        r = client.post("/register", data={
            "email": "x@x.com", "password": "Password1",
            "display_name": "", "csrf_token": token,
        })
        assert b"special character" in r.data

    def test_weak_password_rejected_too_short(self, client):
        token = _csrf(client)
        r = client.post("/register", data={
            "email": "x@x.com", "password": "Ab1!",
            "display_name": "", "csrf_token": token,
        })
        assert b"8 characters" in r.data

    def test_password_max_length_rejected(self, client):
        token = _csrf(client)
        r = client.post("/register", data={
            "email": "x@x.com", "password": "A1!" + "x" * 130,
            "display_name": "", "csrf_token": token,
        })
        assert b"128" in r.data

    def test_csrf_missing_blocks_register(self, client):
        r = client.post("/register", data={
            "email": "x@x.com", "password": "Test@1234!", "display_name": "",
        })
        assert b"Invalid request" in r.data


class TestLogin:
    def test_valid_login_redirects_to_change_password(self, client):
        _register(client)
        with client.session_transaction() as sess:
            sess.clear()
        r = _login(client)
        assert r.status_code == 200

    def test_wrong_password_rejected(self, client):
        _register(client)
        with client.session_transaction() as sess:
            sess.clear()
        token = _csrf(client)
        r = client.post("/login", data={
            "email": "test@example.com", "password": "WrongPass!9",
            "csrf_token": token,
        })
        assert b"Invalid email or password" in r.data

    def test_nonexistent_email_rejected(self, client):
        token = _csrf(client)
        r = client.post("/login", data={
            "email": "nobody@example.com", "password": "Test@1234!",
            "csrf_token": token,
        })
        assert b"Invalid email or password" in r.data

    def test_csrf_missing_blocks_login(self, client):
        r = client.post("/login", data={
            "email": "test@example.com", "password": "Test@1234!",
        })
        assert b"Invalid request" in r.data

    def test_suspended_user_blocked(self, client, monkeypatch):
        _register(client)
        user = models.get_user_by_email("test@example.com")
        models.toggle_user_active(user["id"])
        with client.session_transaction() as sess:
            sess.clear()
        token = _csrf(client)
        r = client.post("/login", data={
            "email": "test@example.com", "password": "Test@1234!",
            "csrf_token": token,
        })
        assert b"suspended" in r.data


class TestLogout:
    def test_logout_requires_post(self, client):
        r = client.get("/logout", follow_redirects=False)
        assert r.status_code == 405

    def test_logout_clears_session(self, client):
        _register(client)
        token = _csrf(client)
        client.post("/logout", data={"csrf_token": token})
        r = client.get("/dashboard", follow_redirects=False)
        assert r.status_code == 302


# ── Access control ────────────────────────────────────────────────────────────

class TestAccessControl:
    def test_dashboard_requires_login(self, client):
        r = client.get("/dashboard", follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_admin_blocked_for_regular_user(self, client):
        _register(client, "super@x.com")
        with client.session_transaction() as sess:
            sess.clear()
        _register(client, "user@x.com")
        with client.session_transaction() as sess:
            sess["must_change_password"] = False
        r = client.get("/admin/", follow_redirects=False)
        assert r.status_code == 403

    def test_api_requires_login(self, client):
        r = client.post("/api/research", json={"topic": "test"})
        assert r.status_code == 302


# ── API input validation ──────────────────────────────────────────────────────

class TestAPIValidation:
    def _logged_in_client(self, client):
        _register(client)
        with client.session_transaction() as sess:
            sess["must_change_password"] = False
        return client

    def test_research_empty_topic_rejected(self, client):
        self._logged_in_client(client)
        r = client.post("/api/research", json={"topic": ""})
        assert r.status_code == 400
        assert b"required" in r.data

    def test_research_topic_too_long_rejected(self, client):
        self._logged_in_client(client)
        r = client.post("/api/research", json={"topic": "x" * 501})
        assert r.status_code == 400
        assert b"500" in r.data

    def test_job_search_empty_query_rejected(self, client):
        self._logged_in_client(client)
        r = client.post("/api/jobs/search", json={"query": ""})
        assert r.status_code == 400

    def test_job_search_query_too_long_rejected(self, client):
        self._logged_in_client(client)
        r = client.post("/api/jobs/search", json={"query": "q" * 501})
        assert r.status_code == 400

    def test_generate_prompt_too_long_rejected(self, client):
        self._logged_in_client(client)
        r = client.post("/api/generate/prompt", json={"description": "d" * 501})
        assert r.status_code == 400

    def test_generate_skill_too_long_rejected(self, client):
        self._logged_in_client(client)
        r = client.post("/api/generate/skill", json={"description": "d" * 501})
        assert r.status_code == 400


# ── Security headers ──────────────────────────────────────────────────────────

class TestSecurityHeaders:
    def test_csp_header_present(self, client):
        r = client.get("/login")
        assert "Content-Security-Policy" in r.headers
        assert "default-src" in r.headers["Content-Security-Policy"]

    def test_x_frame_options_deny(self, client):
        r = client.get("/login")
        assert r.headers.get("X-Frame-Options") == "DENY"

    def test_x_content_type_nosniff(self, client):
        r = client.get("/login")
        assert r.headers.get("X-Content-Type-Options") == "nosniff"

    def test_referrer_policy(self, client):
        r = client.get("/login")
        assert "Referrer-Policy" in r.headers


# ── Path traversal ────────────────────────────────────────────────────────────

class TestPathTraversal:
    def test_traversal_slug_blocked(self, client, monkeypatch, tmp_path):
        _register(client)
        with client.session_transaction() as sess:
            sess["must_change_password"] = False
        user = models.get_user_by_email("test@example.com")
        # Insert article with traversal path
        models.create_article(
            user_id=user["id"], job_id=None,
            title="Evil", slug="evil-slug",
            file_path="../../etc/passwd",
            topic="evil", word_count=0
        )
        r = client.get("/article/evil-slug")
        assert r.status_code in (403, 404)


# ── CSRF protection ───────────────────────────────────────────────────────────

class TestCSRF:
    def test_empty_csrf_token_blocked(self, client):
        with client.session_transaction() as sess:
            sess["csrf_token"] = ""
        r = client.post("/login", data={
            "email": "x@x.com", "password": "Test@1234!", "csrf_token": "",
        })
        assert b"Invalid request" in r.data

    def test_mismatched_csrf_blocked(self, client):
        with client.session_transaction() as sess:
            sess["csrf_token"] = "correct-token"
        r = client.post("/login", data={
            "email": "x@x.com", "password": "Test@1234!", "csrf_token": "wrong-token",
        })
        assert b"Invalid request" in r.data
