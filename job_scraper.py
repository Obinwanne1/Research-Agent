import requests
import json
import models


REMOTEOK_URL = "https://remoteok.com/api"
REMOTEOK_HEADERS = {"User-Agent": "ResearchAgent/1.0 (job search tool)"}


def fetch_remote_jobs(query, limit=20):
    try:
        resp = requests.get(REMOTEOK_URL, headers=REMOTEOK_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        # First element is metadata, skip it
        jobs = [j for j in data if isinstance(j, dict) and j.get("position")]
    except Exception as e:
        return [], str(e)

    query_lower = query.lower()
    matched = []
    for job in jobs:
        text = " ".join([
            job.get("position", ""),
            job.get("company", ""),
            " ".join(job.get("tags", []))
        ]).lower()
        if any(word in text for word in query_lower.split()):
            matched.append({
                "title": job.get("position", ""),
                "company": job.get("company", ""),
                "location": job.get("location") or "Remote",
                "salary": job.get("salary") or "",
                "tags": job.get("tags", [])[:5],
                "url": job.get("url") or f"https://remoteok.com/remote-jobs/{job.get('id', '')}",
                "date": job.get("date", "")[:10] if job.get("date") else "",
            })
        if len(matched) >= limit:
            break

    return matched, None


def run_job_search_task(payload, user_id, job_id):
    query = payload.get("query") or payload.get("topic", "")

    try:
        models.update_job(job_id, status="running", message="Searching RemoteOK...")
        jobs, error = fetch_remote_jobs(query)

        if error:
            models.update_job(job_id, status="error", message=f"RemoteOK error: {error}")
            return

        if not jobs:
            models.update_job(
                job_id, status="done",
                message="No matching jobs found.",
                result_data=[]
            )
            return

        models.update_job(
            job_id,
            status="done",
            message=f"Found {len(jobs)} jobs.",
            result_data=jobs
        )

    except Exception as e:
        models.update_job(job_id, status="error", message=f"Error: {str(e)[:200]}")
