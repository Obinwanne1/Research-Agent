import os
import shutil
from flask import Blueprint, render_template, redirect, url_for, request, flash
import models
from auth import admin_required
from config import Config

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/")
@admin_required
def index():
    stats = models.get_admin_stats()
    return render_template("admin/index.html", stats=stats)


@admin_bp.route("/users")
@admin_required
def users():
    all_users = models.get_all_users()
    return render_template("admin/users.html", users=all_users)


@admin_bp.route("/users/<int:user_id>")
@admin_required
def user_detail(user_id):
    user = models.get_user_by_id(user_id)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("admin.users"))
    articles = models.get_articles_for_user(user_id)
    jobs = models.get_jobs_for_user(user_id)

    # Disk usage: count files in research/<user_id>/
    user_dir = os.path.join(Config.RESEARCH_BASE_DIR, str(user_id))
    file_count = len(os.listdir(user_dir)) if os.path.exists(user_dir) else 0

    return render_template(
        "admin/user_detail.html",
        user=user,
        articles=articles,
        jobs=jobs,
        file_count=file_count
    )


@admin_bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@admin_required
def toggle_user(user_id):
    models.toggle_user_active(user_id)
    flash("User status updated.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    user = models.get_user_by_id(user_id)
    if user:
        # Delete files
        user_dir = os.path.join(Config.RESEARCH_BASE_DIR, str(user_id))
        if os.path.exists(user_dir):
            shutil.rmtree(user_dir)
        models.delete_user(user_id)
        flash(f"User {user['email']} deleted.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/jobs")
@admin_required
def jobs():
    status_filter = request.args.get("status", "")
    type_filter = request.args.get("type", "")
    all_jobs = models.get_all_jobs(
        status_filter=status_filter or None,
        type_filter=type_filter or None
    )
    return render_template(
        "admin/jobs.html",
        jobs=all_jobs,
        status_filter=status_filter,
        type_filter=type_filter
    )
