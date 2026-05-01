from functools import wraps
from flask import session, redirect, url_for, abort
from werkzeug.security import generate_password_hash, check_password_hash
import models


def hash_password(password):
    return generate_password_hash(password)


def verify_password(password, password_hash):
    return check_password_hash(password_hash, password)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        user = models.get_user_by_id(session["user_id"])
        if not user or not user["is_active"]:
            session.clear()
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            abort(403)
        user = models.get_user_by_id(session["user_id"])
        if not user or not user["is_active"]:
            session.clear()
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def set_session(user):
    session["user_id"] = user["id"]
    session["email"] = user["email"]
    session["role"] = user["role"]
    session["display_name"] = user.get("display_name") or user["email"].split("@")[0]
