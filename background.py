import json
import threading
import time as _time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import requests as _requests
import models
from config import Config

_executor = ThreadPoolExecutor(max_workers=Config.MAX_CONCURRENT_JOBS, thread_name_prefix="ra_worker")
_started = False
_start_lock = threading.Lock()


def _get_handlers():
    from research_agent import run_research_task
    from job_scraper import run_job_search_task
    from prompt_generator import run_prompt_gen_task, run_skill_gen_task
    return {
        "research":   run_research_task,
        "job_search": run_job_search_task,
        "prompt_gen": run_prompt_gen_task,
        "skill_gen":  run_skill_gen_task,
    }


def enqueue(job_type, payload, user_id):
    """Create a DB job record, submit to bounded thread pool. Returns job_id."""
    topic = payload.get("topic") or payload.get("query") or "unknown"
    job_id = models.create_job(user_id, job_type, topic, payload=payload)

    handlers = _get_handlers()
    if job_type not in handlers:
        models.update_job(job_id, status="error", message=f"Unknown job type: {job_type}")
        return job_id

    _executor.submit(_run_safe, handlers[job_type], payload, user_id, job_id)
    return job_id


def recover_pending_jobs():
    """
    Call once at startup. Resets jobs that were mid-flight when the server
    last died, then re-submits all pending jobs to the executor.
    """
    global _started
    with _start_lock:
        if _started:
            return
        _started = True

    models.reset_stuck_jobs()
    pending = models.get_pending_jobs()
    if not pending:
        return

    handlers = _get_handlers()
    for job in pending:
        job_type = job["job_type"]
        if job_type not in handlers:
            models.update_job(job["id"], status="error", message=f"Unknown job type: {job_type}")
            continue
        # Restore payload from DB if available, else reconstruct from topic
        raw = job.get("payload")
        if raw:
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"topic": job["topic"]}
        else:
            payload = {"topic": job["topic"]}
        _executor.submit(_run_safe, handlers[job_type], payload, job["user_id"], job["id"])


def _build_webhook_payload(hook_type, job, user, base_url=""):
    topic = job.get("topic", "")
    slug = job.get("result_slug", "")
    article_url = f"{base_url}/article/{slug}" if slug else ""
    ts = datetime.utcnow().isoformat()

    if hook_type == "slack":
        link = f"<{article_url}|Read Article>" if article_url else ""
        return {"text": f":memo: Research complete: *{topic}*\n{link}".strip()}

    if hook_type == "teams":
        body = f"**Research complete:** {topic}"
        if article_url:
            body += f"\n\n[Read Article]({article_url})"
        return {"type": "message", "text": body}

    return {
        "event": "research.complete",
        "job_id": job["id"],
        "job_type": job.get("job_type", "research"),
        "topic": topic,
        "slug": slug,
        "article_url": article_url,
        "user_email": user.get("email", ""),
        "timestamp": ts,
    }


def _fire_webhooks(job_id, user_id):
    try:
        job = models.get_job(job_id)
        if not job or job["status"] != "done":
            return
        if job.get("job_type") not in ("research", "prompt_gen", "skill_gen"):
            return
        user = models.get_user_by_id(user_id) or {}
        webhooks = models.get_active_webhooks_for_user(user_id)
        for wh in webhooks:
            try:
                data = _build_webhook_payload(wh["type"], job, user)
                _requests.post(wh["url"], json=data, timeout=8)
                models.touch_webhook(wh["id"])
            except Exception:
                pass  # webhook failure must never affect the job

        # In-app notification
        topic = job.get("topic", "")
        slug = job.get("result_slug", "")
        job_type = job.get("job_type", "research")
        if job_type == "research" and slug:
            msg = f"Research complete: {topic}"
            models.create_notification(user_id, msg, link=f"/article/{slug}")
        elif job_type in ("prompt_gen", "skill_gen") and slug:
            label = "Prompt" if job_type == "prompt_gen" else "Skill"
            models.create_notification(user_id, f"{label} generated: {topic}", link=f"/article/{slug}")
    except Exception:
        pass  # never crash a worker thread


def _run_safe(handler, payload, user_id, job_id):
    try:
        handler(payload, user_id, job_id)
    except Exception as e:
        models.update_job(job_id, status="error", message=f"Unexpected error: {str(e)[:200]}")
    finally:
        _fire_webhooks(job_id, user_id)


# ── Scheduler ──────────────────────────────────────────────────────────────────

_FREQ_DELTA = {
    "daily":  timedelta(days=1),
    "weekly": timedelta(weeks=1),
}

_scheduler_started = False
_scheduler_lock = threading.Lock()


def _scheduler_loop():
    while True:
        _time.sleep(60)
        try:
            due = models.get_due_schedules()
            if not due:
                continue
            handlers = _get_handlers()
            for sched in due:
                freq = sched.get("frequency", "daily")
                delta = _FREQ_DELTA.get(freq, timedelta(days=1))
                next_run = (datetime.utcnow() + delta).isoformat()
                job_id = enqueue("research", {"topic": sched["topic"]}, sched["user_id"])
                models.update_schedule_after_run(sched["id"], next_run, job_id)
        except Exception:
            pass  # never crash the scheduler thread


def start_scheduler():
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True
    t = threading.Thread(target=_scheduler_loop, daemon=True, name="ra_scheduler")
    t.start()
