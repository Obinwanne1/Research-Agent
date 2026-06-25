import json
import threading
import time as _time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
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


def _run_safe(handler, payload, user_id, job_id):
    try:
        handler(payload, user_id, job_id)
    except Exception as e:
        models.update_job(job_id, status="error", message=f"Unexpected error: {str(e)[:200]}")


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
