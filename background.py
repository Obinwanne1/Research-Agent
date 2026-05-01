import threading
import models

# Import task handlers (imported lazily to avoid circular deps at module load)
def _get_handlers():
    from research_agent import run_research_task
    from job_scraper import run_job_search_task
    return {
        "research":   run_research_task,
        "job_search": run_job_search_task,
        # Add new task types here — one function, one dict entry
    }


def enqueue(job_type, payload, user_id):
    """
    Create a DB job record and spawn a daemon thread for it.
    Returns job_id for polling.

    To add a new task type:
      1. Write a handler: def run_X_task(payload, user_id, job_id): ...
      2. Add it to _get_handlers() above.
      3. Add a Flask route that calls enqueue("X", {...}, user_id).
    """
    topic = payload.get("topic") or payload.get("query") or "unknown"
    job_id = models.create_job(user_id, job_type, topic)

    handlers = _get_handlers()
    if job_type not in handlers:
        models.update_job(job_id, status="error", message=f"Unknown job type: {job_type}")
        return job_id

    thread = threading.Thread(
        target=_run_safe,
        args=(handlers[job_type], payload, user_id, job_id),
        daemon=True
    )
    thread.start()
    return job_id


def _run_safe(handler, payload, user_id, job_id):
    try:
        handler(payload, user_id, job_id)
    except Exception as e:
        models.update_job(job_id, status="error", message=f"Unexpected error: {str(e)[:200]}")
