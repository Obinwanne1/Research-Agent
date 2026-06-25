import os
import re
import json
import secrets
import markdown as md
from datetime import datetime, timedelta, timezone
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, jsonify, abort
)
from config import Config
import models
from auth import (
    login_required, admin_required, hash_password, verify_password,
    set_session, generate_csrf_token, validate_csrf, validate_password_strength,
    check_rate_limit, record_failed_attempt, clear_rate_limit
)
from admin import admin_bp
import background

app = Flask(__name__)
app.secret_key = Config.SECRET_KEY
app.permanent_session_lifetime = timedelta(minutes=Config.SESSION_LIFETIME_MINUTES)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = not Config.DEBUG  # HTTPS only in production
app.register_blueprint(admin_bp)

# Expose csrf_token() in all templates
app.jinja_env.globals['csrf_token'] = generate_csrf_token

# Init DB on startup
models.init_db()
os.makedirs(Config.RESEARCH_BASE_DIR, exist_ok=True)


# ── Security middleware ───────────────────────────────────────────────────────

@app.before_request
def session_timeout():
    if "user_id" in session:
        last = session.get("last_activity")
        if last:
            last_dt = datetime.fromisoformat(last)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            elapsed = datetime.now(timezone.utc) - last_dt
            if elapsed > timedelta(minutes=Config.SESSION_LIFETIME_MINUTES):
                session.clear()
                return redirect(url_for("login"))
        session["last_activity"] = datetime.now(timezone.utc).isoformat()
        session.permanent = True


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    if not Config.DEBUG:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# ── Public routes ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        # CSRF check
        if not validate_csrf():
            error = "Invalid request. Please try again."
            return render_template("auth/login.html", error=error)

        # Rate limit by IP
        ip = request.remote_addr or "unknown"
        allowed, retry_after = check_rate_limit(ip)
        if not allowed:
            mins = max(1, retry_after // 60)
            error = f"Too many failed attempts. Try again in {mins} minute(s)."
            return render_template("auth/login.html", error=error)

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = models.get_user_by_email(email)

        if not user or not verify_password(password, user["password_hash"]):
            record_failed_attempt(ip)
            error = "Invalid email or password."
        elif not user["is_active"]:
            error = "Your account has been suspended. Contact support."
        else:
            clear_rate_limit(ip)
            set_session(user)
            models.update_last_login(user["id"])
            if user.get("must_change_password"):
                return redirect(url_for("change_password"))
            return redirect(url_for("dashboard"))
    return render_template("auth/login.html", error=error)


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        if not validate_csrf():
            error = "Invalid request. Please try again."
            return render_template("auth/register.html", error=error)

        ip = request.remote_addr or "unknown"
        allowed, retry_after = check_rate_limit(f"reg:{ip}")
        if not allowed:
            mins = max(1, retry_after // 60)
            error = f"Too many attempts. Try again in {mins} minute(s)."
            return render_template("auth/register.html", error=error)

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        display_name = request.form.get("display_name", "").strip()

        if not email or not password:
            record_failed_attempt(f"reg:{ip}")
            error = "Email and password are required."
        else:
            ok, msg = validate_password_strength(password)
            if not ok:
                record_failed_attempt(f"reg:{ip}")
                error = msg
            elif models.get_user_by_email(email):
                record_failed_attempt(f"reg:{ip}")
                error = "An account with that email already exists."
            else:
                clear_rate_limit(f"reg:{ip}")
                role = "superadmin" if models.count_users() == 0 else "user"
                user = models.create_user(email, hash_password(password), display_name or None, role)
                set_session(user)
                models.update_last_login(user["id"])
                return redirect(url_for("change_password"))
    return render_template("auth/register.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    if not validate_csrf():
        return redirect(url_for("dashboard"))
    session.clear()
    return redirect(url_for("index"))


# ── Password change (forced on first login) ───────────────────────────────────

@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    error = None
    success = None
    if request.method == "POST":
        if not validate_csrf():
            error = "Invalid request. Please try again."
            return render_template("auth/change_password.html", error=error)

        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")

        user = models.get_user_by_id(session["user_id"])
        if not verify_password(current_pw, user["password_hash"]):
            error = "Current password is incorrect."
        elif new_pw != confirm_pw:
            error = "New passwords do not match."
        else:
            ok, msg = validate_password_strength(new_pw)
            if not ok:
                error = msg
            elif verify_password(new_pw, user["password_hash"]):
                error = "New password must be different from current password."
            else:
                models.update_user_password(session["user_id"], hash_password(new_pw))
                session["must_change_password"] = False
                success = "Password changed successfully."
    return render_template("auth/change_password.html", error=error, success=success)


# ── Password reset ────────────────────────────────────────────────────────────

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    token_link = None
    error = None
    if request.method == "POST":
        if not validate_csrf():
            error = "Invalid request. Please try again."
            return render_template("auth/forgot_password.html", error=error)

        ip = request.remote_addr or "unknown"
        allowed, retry_after = check_rate_limit(f"forgot:{ip}")
        if not allowed:
            mins = max(1, retry_after // 60)
            error = f"Too many attempts. Try again in {mins} minute(s)."
            return render_template("auth/forgot_password.html", error=error)

        email = request.form.get("email", "").strip().lower()
        user = models.get_user_by_email(email)
        if user and user["is_active"]:
            token = secrets.token_urlsafe(32)
            expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            models.create_reset_token(user["id"], token, expires_at)
            token_link = url_for("reset_password", token=token, _external=True)
        record_failed_attempt(f"forgot:{ip}")
        # Always show success message (don't reveal if email exists)
    return render_template("auth/forgot_password.html", token_link=token_link, error=error)


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    token_row = models.get_reset_token(token)
    error = None
    if not token_row:
        return render_template("auth/reset_password.html", invalid=True, error=None)

    # Check expiry
    expires_at = datetime.fromisoformat(token_row["expires_at"])
    if datetime.now(timezone.utc) > expires_at:
        return render_template("auth/reset_password.html", invalid=True, error="Reset link has expired.")

    if request.method == "POST":
        if not validate_csrf():
            error = "Invalid request. Please try again."
            return render_template("auth/reset_password.html", invalid=False, error=error, token=token)

        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")

        if new_pw != confirm_pw:
            error = "Passwords do not match."
        else:
            ok, msg = validate_password_strength(new_pw)
            if not ok:
                error = msg
            else:
                models.update_user_password(token_row["user_id"], hash_password(new_pw))
                models.use_reset_token(token_row["id"])
                flash("Password reset successful. Please sign in.", "success")
                return redirect(url_for("login"))

    return render_template("auth/reset_password.html", invalid=False, error=error, token=token)


# ── User routes ───────────────────────────────────────────────────────────────

@app.route("/dashboard")
@login_required
def dashboard():
    articles = models.get_articles_for_user(session["user_id"])
    jobs = models.get_jobs_for_user(session["user_id"], limit=10)
    return render_template("dashboard.html", articles=articles, jobs=jobs)


@app.route("/article/<slug>")
@login_required
def article(slug):
    art = models.get_article(slug, session["user_id"])
    if not art:
        abort(404)
    file_path = os.path.join(Config.RESEARCH_BASE_DIR, art["file_path"])
    real_base = os.path.realpath(Config.RESEARCH_BASE_DIR)
    real_path = os.path.realpath(file_path)
    if not real_path.startswith(real_base + os.sep):
        abort(403)
    if not os.path.exists(real_path):
        abort(404)
    with open(real_path, "r", encoding="utf-8") as f:
        raw = f.read()
    html_content = md.markdown(raw, extensions=["fenced_code", "tables"])
    html_content = re.sub(
        r'((?:^|(?<=[\s(>]))(https?://[^\s<>"\')\]]+))',
        r'<a href="\2" target="_blank" rel="noopener noreferrer">\2</a>',
        html_content
    )
    return render_template("article.html", article=art, content=html_content)


@app.route("/jobs/results/<int:job_id>")
@login_required
def job_results(job_id):
    job = models.get_job(job_id, session["user_id"])
    if not job:
        abort(404)
    jobs_data = []
    if job["result_data"]:
        try:
            jobs_data = json.loads(job["result_data"])
        except Exception:
            pass
    return render_template("jobs.html", job=job, jobs=jobs_data)


# ── API routes ────────────────────────────────────────────────────────────────

_MAX_INPUT = 500


@app.route("/api/research", methods=["POST"])
@login_required
def api_research():
    data = request.get_json(silent=True) or {}
    topic = (data.get("topic") or "").strip()
    if not topic:
        return jsonify({"error": "topic is required"}), 400
    if len(topic) > _MAX_INPUT:
        return jsonify({"error": f"topic must be {_MAX_INPUT} characters or fewer"}), 400
    job_id = background.enqueue("research", {"topic": topic}, session["user_id"])
    return jsonify({"job_id": job_id})


@app.route("/api/jobs/search", methods=["POST"])
@login_required
def api_job_search():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400
    if len(query) > _MAX_INPUT:
        return jsonify({"error": f"query must be {_MAX_INPUT} characters or fewer"}), 400
    job_id = background.enqueue("job_search", {"query": query, "topic": query}, session["user_id"])
    return jsonify({"job_id": job_id})


@app.route("/api/status/<int:job_id>")
@login_required
def api_status(job_id):
    job = models.get_job(job_id, session["user_id"])
    if not job:
        return jsonify({"error": "not found"}), 404
    result_data = None
    if job["result_data"]:
        try:
            result_data = json.loads(job["result_data"])
        except Exception:
            pass
    return jsonify({
        "status": job["status"],
        "message": job["message"],
        "slug": job["result_slug"],
        "job_type": job["job_type"],
        "result_data": result_data,
        "job_id": job_id,
    })


@app.route("/api/generate/prompt", methods=["POST"])
@login_required
def api_generate_prompt():
    data = request.get_json(silent=True) or {}
    description = (data.get("description") or "").strip()
    if not description:
        return jsonify({"error": "description is required"}), 400
    if len(description) > _MAX_INPUT:
        return jsonify({"error": f"description must be {_MAX_INPUT} characters or fewer"}), 400
    job_id = background.enqueue("prompt_gen", {"topic": description}, session["user_id"])
    return jsonify({"job_id": job_id})


@app.route("/api/generate/skill", methods=["POST"])
@login_required
def api_generate_skill():
    data = request.get_json(silent=True) or {}
    description = (data.get("description") or "").strip()
    if not description:
        return jsonify({"error": "description is required"}), 400
    if len(description) > _MAX_INPUT:
        return jsonify({"error": f"description must be {_MAX_INPUT} characters or fewer"}), 400
    job_id = background.enqueue("skill_gen", {"topic": description}, session["user_id"])
    return jsonify({"job_id": job_id})


@app.route("/api/articles")
@login_required
def api_articles():
    articles = models.get_articles_for_user(session["user_id"])
    return jsonify(articles)


# ── Error handlers ────────────────────────────────────────────────────────────

@app.errorhandler(403)
def forbidden(e):
    return render_template("errors/403.html"), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("errors/404.html"), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=Config.PORT, debug=Config.DEBUG, threaded=True, use_reloader=False)
