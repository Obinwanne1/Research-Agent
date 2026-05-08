import secrets
import threading
from datetime import datetime, timedelta
from functools import wraps
from flask import session, redirect, url_for, abort, request
from werkzeug.security import generate_password_hash, check_password_hash
import models

# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(password):
    return generate_password_hash(password)


def verify_password(password, password_hash):
    return check_password_hash(password_hash, password)


def validate_password_strength(password):
    """Returns (ok, error_message)."""
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one number."
    if not any(c.isalpha() for c in password):
        return False, "Password must contain at least one letter."
    return True, None


# ── CSRF ──────────────────────────────────────────────────────────────────────

def generate_csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']


def validate_csrf():
    token = request.form.get('csrf_token', '')
    return secrets.compare_digest(token, session.get('csrf_token', ''))


# ── Rate limiter ──────────────────────────────────────────────────────────────

_login_attempts = {}   # {identifier: [count, first_attempt, locked_until]}
_login_lock = threading.Lock()
MAX_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def check_rate_limit(identifier):
    """Returns (allowed, retry_after_seconds)."""
    with _login_lock:
        now = datetime.utcnow()
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
        now = datetime.utcnow()
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


# ── Session ───────────────────────────────────────────────────────────────────

def set_session(user):
    session["user_id"] = user["id"]
    session["email"] = user["email"]
    session["role"] = user["role"]
    session["display_name"] = user.get("display_name") or user["email"].split("@")[0]
    session["must_change_password"] = bool(user.get("must_change_password", 0))
    session["last_activity"] = datetime.utcnow().isoformat()


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
        if session.get("role") not in ("admin", "superadmin"):
            abort(403)
        user = models.get_user_by_id(session["user_id"])
        if not user or not user["is_active"]:
            session.clear()
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def superadmin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        if session.get("role") != "superadmin":
            abort(403)
        user = models.get_user_by_id(session["user_id"])
        if not user or not user["is_active"]:
            session.clear()
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated
