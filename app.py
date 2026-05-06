import os
import re
import json
import markdown as md
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, jsonify, abort
)
from config import Config
import models
from auth import login_required, admin_required, hash_password, verify_password, set_session
from admin import admin_bp
import background

app = Flask(__name__)
app.secret_key = Config.SECRET_KEY
app.register_blueprint(admin_bp)

# Init DB on startup
models.init_db()
os.makedirs(Config.RESEARCH_BASE_DIR, exist_ok=True)


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
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = models.get_user_by_email(email)
        if not user or not verify_password(password, user["password_hash"]):
            error = "Invalid email or password."
        elif not user["is_active"]:
            error = "Your account has been suspended. Contact support."
        else:
            set_session(user)
            models.update_last_login(user["id"])
            return redirect(url_for("dashboard"))
    return render_template("auth/login.html", error=error)


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        display_name = request.form.get("display_name", "").strip()

        if not email or not password:
            error = "Email and password are required."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif models.get_user_by_email(email):
            error = "An account with that email already exists."
        else:
            # First registered user becomes superadmin
            role = "superadmin" if models.count_users() == 0 else "user"
            user = models.create_user(email, hash_password(password), display_name or None, role)
            set_session(user)
            models.update_last_login(user["id"])
            flash(f"Welcome! {'You have been granted admin access.' if role == 'admin' else ''}", "success")
            return redirect(url_for("dashboard"))
    return render_template("auth/register.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


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
    if not os.path.exists(file_path):
        abort(404)
    with open(file_path, "r", encoding="utf-8") as f:
        raw = f.read()
    html_content = md.markdown(raw, extensions=["fenced_code", "tables"])
    # Convert bare URLs in text nodes to clickable links (covers old articles where Claude wrote plain URLs)
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

@app.route("/api/research", methods=["POST"])
@login_required
def api_research():
    data = request.get_json(silent=True) or {}
    topic = (data.get("topic") or "").strip()
    if not topic:
        return jsonify({"error": "topic is required"}), 400
    job_id = background.enqueue("research", {"topic": topic}, session["user_id"])
    return jsonify({"job_id": job_id})


@app.route("/api/jobs/search", methods=["POST"])
@login_required
def api_job_search():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400
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
