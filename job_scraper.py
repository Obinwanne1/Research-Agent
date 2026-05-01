import json
import re
import subprocess
import models
from config import Config
from ddgs import DDGS


def search_jobs_ddg(query, max_results=15):
    """Search DuckDuckGo for job listings and return raw results."""
    search_query = f"{query} jobs hiring apply"
    with DDGS() as ddgs:
        results = list(ddgs.text(search_query, max_results=max_results))
    return results


def call_claude(prompt):
    result = subprocess.run(
        ["claude", "-p"],
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=Config.CLAUDE_TIMEOUT
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI error: {result.stderr[:300]}")
    output = result.stdout.strip()
    try:
        output = output.encode('cp1252').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return output


def extract_jobs_with_claude(query, search_results):
    """
    Give Claude the raw DuckDuckGo snippets and ask it to extract
    real job listings as a JSON array.
    """
    snippets = "\n\n".join(
        f"RESULT {i+1}:\nTitle: {r.get('title','')}\nURL: {r.get('href','')}\nSnippet: {r.get('body','')}"
        for i, r in enumerate(search_results)
    )

    prompt = f"""You are a job search assistant. The user searched for: "{query}"

Below are web search results from DuckDuckGo. Extract REAL job listings from these results.

{snippets}

Return ONLY a valid JSON array. Each element must have these exact keys:
- "title": job title (string)
- "company": company name (string, or "" if unknown)
- "location": city/country or "Remote" (string)
- "salary": salary if mentioned, else "" (string)
- "url": the apply/job URL (string)
- "tags": list of up to 4 relevant skill/category tags (list of strings)

Rules:
- Only include actual job listings, not generic job board homepages
- If a result is a job board listing page with multiple jobs, create one entry per job you can identify
- If you cannot identify any real job listings, return an empty array: []
- Output ONLY the JSON array, no other text, no markdown code fences

Example output format:
[{{"title":"Hairdresser","company":"Salon XYZ","location":"Dortmund, Germany","salary":"€2,200/month","url":"https://example.com/job/123","tags":["hairdressing","beauty","salon"]}}]"""

    output = call_claude(prompt)

    # Strip markdown fences if Claude added them anyway
    output = re.sub(r"^```[a-z]*\n?", "", output.strip())
    output = re.sub(r"\n?```$", "", output.strip())

    try:
        jobs = json.loads(output)
        if not isinstance(jobs, list):
            return []
        # Normalise fields
        cleaned = []
        for j in jobs:
            if not isinstance(j, dict):
                continue
            cleaned.append({
                "title":    str(j.get("title", "")).strip(),
                "company":  str(j.get("company", "")).strip(),
                "location": str(j.get("location", "")).strip() or "See listing",
                "salary":   str(j.get("salary", "")).strip(),
                "url":      str(j.get("url", "")).strip(),
                "tags":     [str(t) for t in j.get("tags", [])[:4] if t],
                "date":     "",
            })
        return [j for j in cleaned if j["title"] and j["url"]]
    except json.JSONDecodeError:
        return []


def run_job_search_task(payload, user_id, job_id):
    query = payload.get("query") or payload.get("topic", "")

    try:
        models.update_job(job_id, status="running", message="Searching the web for jobs...")
        search_results = search_jobs_ddg(query)

        if not search_results:
            models.update_job(job_id, status="error", message="No search results found. Try a different query.")
            return

        models.update_job(job_id, message="Extracting job listings with Claude...")
        jobs = extract_jobs_with_claude(query, search_results)

        if not jobs:
            models.update_job(
                job_id, status="done",
                message="No specific job listings found. Try a more specific search (e.g. 'hairdresser Dortmund 2024').",
                result_data=[]
            )
            return

        models.update_job(
            job_id,
            status="done",
            message=f"Found {len(jobs)} job listings.",
            result_data=jobs
        )

    except subprocess.TimeoutExpired:
        models.update_job(job_id, status="error", message="Claude CLI timed out. Try again.")
    except Exception as e:
        models.update_job(job_id, status="error", message=f"Error: {str(e)[:200]}")
