import secrets
import threading
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import session, redirect, url_for, abort, request, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash
import models

# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(password):
    return generate_password_hash(password)


def verify_password(password, password_hash):
    return check_password_hash(password_hash, password)


_SPECIAL_CHARS = set("!@#$%^&*()_+-=[]{}|;:',.<>?/`~")

def validate_password_strength(password):
    """Returns (ok, error_message)."""
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    if len(password) > 128:
        return False, "Password must be 128 characters or fewer."
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one number."
    if not any(c.isalpha() for c in password):
        return False, "Password must contain at least one letter."
    if not any(c in _SPECIAL_CHARS for c in password):
        return False, "Password must contain at least one special character (!@#$%^&* etc)."
    return True, None


# ── CSRF ──────────────────────────────────────────────────────────────────────

def generate_csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']


def validate_csrf():
    token = request.form.get('csrf_token', '')
    session_token = session.get('csrf_token', '')
    if not token or not session_token:
        return False
    return secrets.compare_digest(token, session_token)


# ── Rate limiter (login) ──────────────────────────────────────────────────────

_login_attempts = {}   # {identifier: [count, first_attempt, locked_until]}
_login_lock = threading.Lock()
MAX_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def check_rate_limit(identifier):
    """Returns (allowed, retry_after_seconds)."""
    with _login_lock:
        now = datetime.now(timezone.utc)
        if identifier not in _login_attempts:
            return True, 0
        count, first_attempt, locked_until = _login_attempts[identifier]
        if locked_until and now < locked_until:
            return False, int((locked_until - now).total_seconds())
        if now - first_attempt > timedelta(minutes=LOCKOUT_MINUTES):
            del _login_attempts[identifier]
            return True, 0
        return True, 0


def record_failed_attempt(identifier):
    with _login_lock:
        now = datetime.now(timezone.utc)
        if identifier not in _login_attempts:
            _login_attempts[identifier] = [1, now, None]
        else:
            count, first_attempt, _ = _login_attempts[identifier]
            count += 1
            locked_until = (now + timedelta(minutes=LOCKOUT_MINUTES)) if count >= MAX_ATTEMPTS else None
            _login_attempts[identifier] = [count, first_attempt, locked_until]


def clear_rate_limit(identifier):
    with _login_lock:
        _login_attempts.pop(identifier, None)


# ── Rate limiter (API — per user, sliding window) ─────────────────────────────

_api_windows = {}   # {user_id: [timestamp, ...]}
_api_lock = threading.Lock()


def check_api_rate_limit(user_id):
    """Sliding window: allow Config.API_RATE_LIMIT calls per user per hour.
    Returns (allowed, retry_after_seconds)."""
    from config import Config
    limit = Config.API_RATE_LIMIT
    window = timedelta(hours=1)
    now = datetime.now(timezone.utc)
    with _api_lock:
        timestamps = _api_windows.get(user_id, [])
        # Drop timestamps older than 1 hour
        timestamps = [t for t in timestamps if now - t < window]
        if len(timestamps) >= limit:
            oldest = min(timestamps)
            retry_after = int((oldest + window - now).total_seconds()) + 1
            _api_windows[user_id] = timestamps
            return False, retry_after
        timestamps.append(now)
        _api_windows[user_id] = timestamps
        return True, 0


# ── Session ───────────────────────────────────────────────────────────────────

def set_session(user):
    session["user_id"] = user["id"]
    session["email"] = user["email"]
    session["role"] = user["role"]
    session["display_name"] = user.get("display_name") or user["email"].split("@")[0]
    session["must_change_password"] = bool(user.get("must_change_password", 0))
    session["last_activity"] = datetime.now(timezone.utc).isoformat()


# ── Access decorators ─────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        user = models.get_user_by_id(session["user_id"])
        if not user or not user["is_active"]:
            session.clear()
            return redirect(url_for("login"))
        # Force password change before accessing anything else
        if session.get("must_change_password") and f.__name__ != "change_password":
            return redirect(url_for("change_password"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        user = models.get_user_by_id(session["user_id"])
        if not user or not user["is_active"]:
            session.clear()
            return redirect(url_for("login"))
        if user["role"] not in ("admin", "superadmin"):
            abort(403)
        session["role"] = user["role"]  # keep session in sync with DB
        return f(*args, **kwargs)
    return decorated


def _extract_api_key():
    h = request.headers.get("X-API-Key", "").strip()
    if h:
        return h
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def api_auth_required(f):
    """Accepts session auth OR X-API-Key / Authorization: Bearer header."""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Session auth (browser)
        if "user_id" in session:
            user = models.get_user_by_id(session["user_id"])
            if not user or not user["is_active"]:
                session.clear()
                return jsonify({"error": "Authentication required"}), 401
            g.user_id = user["id"]
            return f(*args, **kwargs)
        # API key auth
        raw_key = _extract_api_key()
        if raw_key:
            result = models.verify_api_key(raw_key)
            if result:
                user, key_id = result
                if user["is_active"]:
                    g.user_id = user["id"]
                    models.touch_api_key(key_id)
                    return f(*args, **kwargs)
        return jsonify({"error": "Authentication required"}), 401
    return decorated


def superadmin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        user = models.get_user_by_id(session["user_id"])
        if not user or not user["is_active"]:
            session.clear()
            return redirect(url_for("login"))
        if user["role"] != "superadmin":
            abort(403)
        session["role"] = user["role"]
        return f(*args, **kwargs)
    return decorated
