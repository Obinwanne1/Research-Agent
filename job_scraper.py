import json
import re
import subprocess
import models
from config import Config
from ddgs import DDGS
from utils import call_claude


# ATS platforms where companies post jobs directly (no aggregator middleman)
_COMPANY_DIRECT_SITES = (
    "site:greenhouse.io OR site:lever.co OR site:workday.com OR "
    "site:myworkdayjobs.com OR site:jobs.ashbyhq.com OR site:apply.workable.com OR "
    "site:bamboohr.com OR site:smartrecruiters.com OR site:icims.com"
)


def search_jobs_ddg(query, max_results=20):
    """Broad global search — job boards + LinkedIn + remote boards."""
    search_query = (
        f"{query} jobs hiring apply 2026 "
        "site:linkedin.com OR site:indeed.com OR site:glassdoor.com OR "
        "site:remote.co OR site:weworkremotely.com OR site:remoteok.com OR "
        "site:jobs.lever.co OR site:greenhouse.io"
    )
    with DDGS() as ddgs:
        results = list(ddgs.text(search_query, max_results=max_results))
    return results


def search_company_direct(query, max_results=10):
    """Search company career pages posted on ATS platforms directly."""
    search_query = f"{query} jobs {_COMPANY_DIRECT_SITES}"
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(search_query, max_results=max_results))
        return results
    except Exception:
        return []


def extract_jobs_with_claude(query, search_results):
    snippets = "\n\n".join(
        f"RESULT {i+1}:\nTitle: {r.get('title','')}\nURL: {r.get('href','')}\nSnippet: {r.get('body','')}"
        for i, r in enumerate(search_results)
    )

    prompt = f"""You are a global job search assistant. The user searched for: "{query}"

Below are web search results. Extract REAL job listings — including remote, international, and company-direct postings.

{snippets}

Return ONLY a valid JSON array. Each element must have these exact keys:
- "title": job title (string)
- "company": company name (string, or "" if unknown)
- "location": city and country, or "Remote" or "Remote (Worldwide)" (string)
- "salary": salary/range if mentioned, else "" (string)
- "url": direct apply or job listing URL (string)
- "tags": up to 4 skill/category tags (list of strings)
- "source": one of "LinkedIn", "Indeed", "Glassdoor", "Company Direct", "Remote Board", or "Other" (string)

Rules:
- Include jobs from ANY country — this is a global search
- Mark jobs from Greenhouse/Lever/Workday/Ashby/Bamboo as "Company Direct"
- If a result is a listing page with multiple jobs, create one entry per identifiable job
- Prefer direct company apply links over aggregator links where both exist
- If no real listings found, return []
- Output ONLY the JSON array, no markdown fences, no other text

Example:
[{{"title":"Software Engineer","company":"Stripe","location":"Remote (Worldwide)","salary":"$120k–$180k","url":"https://stripe.com/jobs/123","tags":["Python","AWS","fintech"],"source":"Company Direct"}}]"""

    output = call_claude(prompt)
    output = re.sub(r"^```[a-z]*\n?", "", output.strip())
    output = re.sub(r"\n?```$", "", output.strip())

    try:
        jobs = json.loads(output)
        if not isinstance(jobs, list):
            return []
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
                "source":   str(j.get("source", "Other")).strip(),
                "date":     "",
            })
        return [j for j in cleaned if j["title"] and j["url"]]
    except json.JSONDecodeError:
        return []


def run_job_search_task(payload, user_id, job_id):
    query = payload.get("query") or payload.get("topic", "")

    try:
        models.update_job(job_id, status="running", message="Searching global job boards...")
        broad_results = search_jobs_ddg(query, max_results=20)

        models.update_job(job_id, message="Searching company career pages...")
        direct_results = search_company_direct(query, max_results=10)

        all_results = broad_results + direct_results

        if not all_results:
            models.update_job(job_id, status="error", message="No results found. Try a different query.")
            return

        # Deduplicate by URL
        seen = set()
        unique = []
        for r in all_results:
            url = r.get("href", "")
            if url and url not in seen:
                seen.add(url)
                unique.append(r)

        models.update_job(job_id, message=f"Extracting listings from {len(unique)} results with Claude...")
        jobs = extract_jobs_with_claude(query, unique[:30])

        if not jobs:
            models.update_job(
                job_id, status="done",
                message="No specific listings found. Try a more specific search (e.g. 'Python developer remote USA').",
                result_data=[]
            )
            return

        # Sort: company direct first, then by source
        source_order = {"Company Direct": 0, "LinkedIn": 1, "Indeed": 2, "Glassdoor": 3, "Remote Board": 4, "Other": 5}
        jobs.sort(key=lambda j: source_order.get(j.get("source", "Other"), 5))

        models.update_job(
            job_id,
            status="done",
            message=f"Found {len(jobs)} job listings worldwide.",
            result_data=jobs,
        )

    except subprocess.TimeoutExpired:
        models.update_job(job_id, status="error", message="Claude CLI timed out. Try again.")
    except Exception as e:
        models.update_job(job_id, status="error", message=f"Error: {str(e)[:200]}")
