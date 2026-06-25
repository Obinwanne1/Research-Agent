import os
import re
import json
import secrets
import markdown as md
from datetime import datetime, timedelta, timezone
import time
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, jsonify, abort, send_file, g, Response, stream_with_context
)
from config import Config
import models
from auth import (
    login_required, admin_required, api_auth_required, hash_password, verify_password,
    set_session, generate_csrf_token, validate_csrf, validate_password_strength,
    check_rate_limit, record_failed_attempt, clear_rate_limit, check_api_rate_limit
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
background.recover_pending_jobs()
background.start_scheduler()


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


@app.route("/article/<slug>/download.md")
@login_required
def article_download_md(slug):
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
    filename = os.path.basename(real_path)
    return send_file(real_path, as_attachment=True, download_name=filename, mimetype="text/markdown; charset=utf-8")


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
@api_auth_required
def api_research():
    allowed, retry_after = check_api_rate_limit(g.user_id)
    if not allowed:
        resp = jsonify({"error": f"Rate limit exceeded. Try again in {retry_after}s."})
        resp.headers["Retry-After"] = str(retry_after)
        return resp, 429
    data = request.get_json(silent=True) or {}
    topic = (data.get("topic") or "").strip()
    if not topic:
        return jsonify({"error": "topic is required"}), 400
    if len(topic) > _MAX_INPUT:
        return jsonify({"error": f"topic must be {_MAX_INPUT} characters or fewer"}), 400
    job_id = background.enqueue("research", {"topic": topic}, g.user_id)
    return jsonify({"job_id": job_id})


@app.route("/api/jobs/search", methods=["POST"])
@api_auth_required
def api_job_search():
    allowed, retry_after = check_api_rate_limit(g.user_id)
    if not allowed:
        resp = jsonify({"error": f"Rate limit exceeded. Try again in {retry_after}s."})
        resp.headers["Retry-After"] = str(retry_after)
        return resp, 429
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    company = (data.get("company") or "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400
    if len(query) > _MAX_INPUT:
        return jsonify({"error": f"query must be {_MAX_INPUT} characters or fewer"}), 400
    if len(company) > 100:
        return jsonify({"error": "company name must be 100 characters or fewer"}), 400
    job_id = background.enqueue("job_search", {"query": query, "topic": query, "company": company}, g.user_id)
    return jsonify({"job_id": job_id})


@app.route("/api/status/<int:job_id>")
@api_auth_required
def api_status(job_id):
    job = models.get_job(job_id, g.user_id)
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


@app.route("/api/stream/<int:job_id>")
@login_required
def api_stream(job_id):
    user_id = session["user_id"]

    def generate():
        while True:
            try:
                job = models.get_job(job_id, user_id)
                if not job:
                    yield f"data: {json.dumps({'error': 'not found'})}\n\n"
                    break
                result_data = None
                if job["result_data"]:
                    try:
                        result_data = json.loads(job["result_data"])
                    except Exception:
                        pass
                payload = json.dumps({
                    "status":      job["status"],
                    "message":     job["message"],
                    "slug":        job["result_slug"],
                    "job_type":    job["job_type"],
                    "result_data": result_data,
                    "job_id":      job_id,
                })
                yield f"data: {payload}\n\n"
                if job["status"] in ("done", "error"):
                    break
                time.sleep(1)
            except GeneratorExit:
                break

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/generate/prompt", methods=["POST"])
@api_auth_required
def api_generate_prompt():
    data = request.get_json(silent=True) or {}
    description = (data.get("description") or "").strip()
    if not description:
        return jsonify({"error": "description is required"}), 400
    if len(description) > _MAX_INPUT:
        return jsonify({"error": f"description must be {_MAX_INPUT} characters or fewer"}), 400
    job_id = background.enqueue("prompt_gen", {"topic": description}, g.user_id)
    return jsonify({"job_id": job_id})


@app.route("/api/generate/skill", methods=["POST"])
@api_auth_required
def api_generate_skill():
    data = request.get_json(silent=True) or {}
    description = (data.get("description") or "").strip()
    if not description:
        return jsonify({"error": "description is required"}), 400
    if len(description) > _MAX_INPUT:
        return jsonify({"error": f"description must be {_MAX_INPUT} characters or fewer"}), 400
    job_id = background.enqueue("skill_gen", {"topic": description}, g.user_id)
    return jsonify({"job_id": job_id})


@app.route("/api/articles")
@api_auth_required
def api_articles():
    return jsonify(models.get_articles_for_user(g.user_id))


@app.route("/api/search/articles")
@api_auth_required
def api_search_articles():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify(models.get_articles_for_user(g.user_id))
    try:
        results = models.search_articles(g.user_id, q)
    except Exception:
        results = []
    return jsonify(results)


# ── API key settings ──────────────────────────────────────────────────────────

@app.route("/settings/api-keys")
@login_required
def settings_api_keys():
    keys = models.get_api_keys_for_user(session["user_id"])
    new_key = session.pop("_pending_api_key", None)
    return render_template("settings/api_keys.html", keys=keys, new_key=new_key)


@app.route("/settings/api-keys", methods=["POST"])
@login_required
def settings_api_keys_create():
    if not validate_csrf():
        flash("Invalid request.", "error")
        return redirect(url_for("settings_api_keys"))
    name = (request.form.get("name") or "").strip()[:80] or "My Key"
    raw_key = "ra_" + secrets.token_hex(32)
    models.create_api_key(session["user_id"], name, raw_key)
    session["_pending_api_key"] = raw_key
    flash("API key created. Copy it now — it won't be shown again.", "success")
    return redirect(url_for("settings_api_keys"))


@app.route("/settings/api-keys/<int:key_id>/revoke", methods=["POST"])
@login_required
def settings_api_keys_revoke(key_id):
    if not validate_csrf():
        flash("Invalid request.", "error")
        return redirect(url_for("settings_api_keys"))
    models.revoke_api_key(key_id, session["user_id"])
    flash("API key revoked.", "success")
    return redirect(url_for("settings_api_keys"))


# ── Article comparison ────────────────────────────────────────────────────────

@app.route("/compare")
@login_required
def compare():
    raw = request.args.get("slugs", "")
    slugs = [s.strip() for s in raw.split(",") if s.strip()][:4]
    if len(slugs) < 2:
        flash("Select at least 2 articles to compare.", "error")
        return redirect(url_for("dashboard"))

    articles_data = []
    for slug in slugs:
        art = models.get_article(slug, session["user_id"])
        if not art:
            continue
        file_path = os.path.join(Config.RESEARCH_BASE_DIR, art["file_path"])
        real_base = os.path.realpath(Config.RESEARCH_BASE_DIR)
        real_path = os.path.realpath(file_path)
        if not real_path.startswith(real_base + os.sep) or not os.path.exists(real_path):
            continue
        with open(real_path, "r", encoding="utf-8") as f:
            raw_md = f.read()
        html = md.markdown(raw_md, extensions=["fenced_code", "tables"])
        articles_data.append({"meta": art, "html": html, "raw": raw_md})

    if len(articles_data) < 2:
        flash("Could not load articles for comparison.", "error")
        return redirect(url_for("dashboard"))

    # Claude synthesis — compare all articles
    excerpts = "\n\n---\n\n".join(
        f"Article {i+1}: {a['meta']['title']}\n\n{a['raw'][:3000]}"
        for i, a in enumerate(articles_data)
    )
    synthesis_html = None
    try:
        from utils import call_claude
        prompt = (
            f"You are comparing {len(articles_data)} research articles.\n\n"
            f"{excerpts}\n\n"
            "Write a concise comparison (300–500 words) structured as:\n"
            "## What They Share\n"
            "## Key Differences\n"
            "## Synthesis\n"
            "Plain English only. Output ONLY the markdown — no preamble."
        )
        synthesis_md = call_claude(prompt)
        synthesis_html = md.markdown(synthesis_md, extensions=["fenced_code", "tables"])
    except Exception:
        pass  # synthesis is optional — page still works without it

    return render_template("compare.html", articles=articles_data, synthesis=synthesis_html)


# ── Public sharing ────────────────────────────────────────────────────────────

@app.route("/article/<slug>/share", methods=["POST"])
@login_required
def article_share_toggle(slug):
    if not validate_csrf():
        flash("Invalid request.", "error")
        return redirect(url_for("article", slug=slug))
    art = models.get_article(slug, session["user_id"])
    if not art:
        abort(404)
    make_public = not bool(art.get("is_public"))
    models.set_article_share(slug, session["user_id"], make_public)
    if make_public:
        flash("Article is now public. Anyone with the link can view it.", "success")
    else:
        flash("Article is now private.", "success")
    return redirect(url_for("article", slug=slug))


@app.route("/share/<token>")
def article_public(token):
    art = models.get_article_by_token(token)
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
    return render_template("share.html", article=art, content=html_content)


# ── Schedule routes ───────────────────────────────────────────────────────────

_VALID_FREQUENCIES = {"daily", "weekly"}


@app.route("/schedules")
@login_required
def schedules():
    user_schedules = models.get_schedules_for_user(session["user_id"])
    return render_template("schedules/index.html", schedules=user_schedules)


@app.route("/schedules", methods=["POST"])
@login_required
def schedules_create():
    if not validate_csrf():
        flash("Invalid request.", "error")
        return redirect(url_for("schedules"))
    topic = (request.form.get("topic") or "").strip()
    frequency = (request.form.get("frequency") or "daily").strip()
    if not topic:
        flash("Topic is required.", "error")
        return redirect(url_for("schedules"))
    if len(topic) > _MAX_INPUT:
        flash(f"Topic must be {_MAX_INPUT} characters or fewer.", "error")
        return redirect(url_for("schedules"))
    if frequency not in _VALID_FREQUENCIES:
        frequency = "daily"
    next_run_at = (datetime.now(timezone.utc) + timedelta(
        days=1 if frequency == "daily" else 7
    )).isoformat()
    models.create_schedule(session["user_id"], topic, frequency, next_run_at)
    flash(f'Schedule created. First run in {"24 hours" if frequency == "daily" else "7 days"}.', "success")
    return redirect(url_for("schedules"))


@app.route("/schedules/<int:schedule_id>/toggle", methods=["POST"])
@login_required
def schedules_toggle(schedule_id):
    if not validate_csrf():
        flash("Invalid request.", "error")
        return redirect(url_for("schedules"))
    models.toggle_schedule(schedule_id, session["user_id"])
    return redirect(url_for("schedules"))


@app.route("/schedules/<int:schedule_id>/delete", methods=["POST"])
@login_required
def schedules_delete(schedule_id):
    if not validate_csrf():
        flash("Invalid request.", "error")
        return redirect(url_for("schedules"))
    models.delete_schedule(schedule_id, session["user_id"])
    flash("Schedule deleted.", "success")
    return redirect(url_for("schedules"))


# ── Error handlers ────────────────────────────────────────────────────────────

@app.errorhandler(403)
def forbidden(e):
    return render_template("errors/403.html"), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("errors/404.html"), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=Config.PORT, debug=Config.DEBUG, threaded=True, use_reloader=False)
