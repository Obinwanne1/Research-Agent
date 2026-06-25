import json
import re
import subprocess
import requests
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import models
from config import Config
from ddgs import DDGS
from utils import call_claude


_ATS_SITES = (
    "site:greenhouse.io OR site:lever.co OR site:workday.com OR "
    "site:myworkdayjobs.com OR site:jobs.ashbyhq.com OR site:apply.workable.com OR "
    "site:bamboohr.com OR site:smartrecruiters.com OR site:icims.com"
)


def _ddg_search(query, max_results=20):
    try:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except Exception:
        return []


def search_jobs_broad(query, max_results=20):
    q = (
        f"{query} jobs hiring apply 2026 "
        "site:linkedin.com OR site:indeed.com OR site:glassdoor.com OR "
        "site:remote.co OR site:weworkremotely.com OR site:remoteok.com OR "
        f"site:jobs.lever.co OR site:greenhouse.io"
    )
    return _ddg_search(q, max_results)


def search_jobs_ats(query, max_results=10):
    return _ddg_search(f"{query} jobs {_ATS_SITES}", max_results)


def search_company_jobs(query, company, max_results=12):
    domain = company.lower().replace(" ", "") + ".com"
    q = (
        f'"{company}" {query} jobs '
        f'site:{domain}/careers OR site:{domain}/jobs OR '
        f'"{company}" site:greenhouse.io OR "{company}" site:lever.co OR '
        f'"{company}" site:myworkdayjobs.com'
    )
    return _ddg_search(q, max_results)


# ── Direct parser (no Claude) ─────────────────────────────────────────────────

def _infer_source(url):
    u = url.lower()
    if "linkedin.com" in u:
        return "LinkedIn"
    if "indeed.com" in u:
        return "Indeed"
    if "glassdoor.com" in u:
        return "Glassdoor"
    if any(x in u for x in ["greenhouse.io", "lever.co", "myworkdayjobs.com",
                              "ashbyhq.com", "workable.com", "bamboohr.com",
                              "smartrecruiters.com", "icims.com"]):
        return "Company Direct"
    if any(x in u for x in ["remote.co", "weworkremotely.com", "remoteok.com"]):
        return "Remote Board"
    if "/careers" in u or "/jobs/" in u:
        return "Company Direct"
    return "Other"


def _infer_confidence(url, source):
    u = url.lower()
    if source == "Company Direct" and re.search(r'/\d{4,}|/job/[a-z0-9-]+|/jobs/[a-z0-9-]+$', u):
        return 3
    if source == "LinkedIn" and re.search(r'/view/\d+', u):
        return 3
    if source == "Indeed" and "viewjob" in u:
        return 3
    if source in ("LinkedIn", "Indeed", "Glassdoor", "Company Direct", "Remote Board"):
        return 2
    return 1


def _extract_salary(text):
    m = re.search(
        r'\$[\d,]+(?:\.\d+)?[kK]?(?:\s*[-–]\s*\$[\d,]+(?:\.\d+)?[kK]?)?'
        r'(?:\s*/\s*(?:yr|year|mo|month|hr|hour|week))?'
        r'|£[\d,]+[kK]?(?:\s*[-–]\s*£[\d,]+[kK]?)?'
        r'|\b\d{2,3}[kK]\s*[-–]\s*\d{2,3}[kK]\b',
        text, re.IGNORECASE
    )
    return m.group(0).strip() if m else ""


def _clean_title(title):
    return re.sub(
        r'\s*[|\-–]\s*(LinkedIn|Indeed|Glassdoor|Greenhouse|Lever|Workday|'
        r'Workable|BambooHR|SmartRecruiters|iCIMS|Jobs|Careers|Apply).*$',
        '', title, flags=re.IGNORECASE
    ).strip()


def _extract_company(title, body, url):
    # "Title at Company Name"
    m = re.search(r'\bat\s+([A-Z][A-Za-z0-9\s&.,\'-]{1,50}?)(?:\s*[|·\-]|\s*$)', title)
    if m:
        return m.group(1).strip()
    # "Company Name - Title"
    m = re.search(r'^([A-Z][A-Za-z0-9\s&.,\'-]{1,40}?)\s*[-|·]\s*[A-Z]', title)
    if m:
        candidate = m.group(1).strip()
        if len(candidate) <= 50:
            return candidate
    # greenhouse.io/[company]/jobs or lever.co/[company]
    m = re.search(r'greenhouse\.io/([a-z0-9_-]+)/', url.lower())
    if not m:
        m = re.search(r'lever\.co/([a-z0-9_-]+)', url.lower())
    if m:
        return m.group(1).replace('-', ' ').replace('_', ' ').title()
    return ""


def _extract_location(body):
    m = re.search(
        r'\b(Remote(?:\s*(?:Worldwide|Global|\([^)]{1,30}\)))?'
        r'|Hybrid(?:\s*[-–]\s*[A-Z][a-z]+)?'
        r'|[A-Z][a-z]{2,}\s*,\s*[A-Z]{2,3}'
        r'|[A-Z][a-z]{2,}\s*,\s*[A-Z][a-z]+)',
        body
    )
    if m:
        loc = m.group(1).strip()
        if loc not in ("The", "This", "Our", "Apply", "Job", "You"):
            return loc
    return "See listing"


def _extract_tags(title, body):
    text = (title + " " + body).lower()
    tags = []

    # Work arrangement (mutually exclusive, first match wins)
    if re.search(r'\bremote\b', text):
        tags.append("Remote")
    elif re.search(r'\bhybrid\b', text):
        tags.append("Hybrid")
    elif re.search(r'\bon.?site\b|\bin.?person\b|\bin.?office\b', text):
        tags.append("On-site")

    # Employment type
    if re.search(r'\bcontract\b|\bfreelance\b|\bcontractor\b', text):
        tags.append("Contract")
    elif re.search(r'\binternship\b|\bintern\b|\bco.?op\b', text):
        tags.append("Internship")
    elif re.search(r'\bpart.?time\b', text):
        tags.append("Part-time")
    elif re.search(r'\bfull.?time\b', text):
        tags.append("Full-time")

    # Seniority from title only
    t = title.lower()
    if re.search(r'\bprincipal\b|\bstaff\b|\barchitect\b|\bvp\b|\bvice president\b|\bdirector\b|\bhead of\b', t):
        tags.append("Senior+")
    elif re.search(r'\bsenior\b|\bsr\b|\blead\b|\bmanager\b', t):
        tags.append("Senior")
    elif re.search(r'\bjunior\b|\bjr\b|\bentry.?level\b|\bassociate\b', t):
        tags.append("Junior")

    return tags[:4]


def _extract_date(body):
    m = re.search(r'(\d+)\s+days?\s+ago', body, re.IGNORECASE)
    if m:
        return (date.today() - timedelta(days=int(m.group(1)))).isoformat()

    if re.search(r'\d+\s+hours?\s+ago|\btoday\b|\bjust posted\b', body, re.IGNORECASE):
        return date.today().isoformat()

    if re.search(r'\byesterday\b', body, re.IGNORECASE):
        return (date.today() - timedelta(days=1)).isoformat()

    m = re.search(r'(\d{4}-\d{2}-\d{2})', body)
    if m:
        return m.group(1)

    m = re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}),?\s+(\d{4})', body, re.IGNORECASE)
    if m:
        try:
            from datetime import datetime as _dt
            return _dt.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %Y").date().isoformat()
        except Exception:
            pass

    return ""


def parse_jobs_direct(results):
    """Parse DDG results directly — no Claude, instant."""
    jobs = []
    seen_urls = set()
    for r in results:
        title = (r.get("title") or "").strip()
        url = (r.get("href") or "").strip()
        body = (r.get("body") or "").strip()

        if not title or not url or url in seen_urls:
            continue
        seen_urls.add(url)

        source = _infer_source(url)
        confidence = _infer_confidence(url, source)
        clean = _clean_title(title)
        if not clean:
            continue

        jobs.append({
            "title":      clean[:120],
            "company":    _extract_company(title, body, url),
            "location":   _extract_location(body),
            "salary":     _extract_salary(body + " " + title),
            "url":        url,
            "tags":       _extract_tags(clean, body),
            "source":     source,
            "confidence": confidence,
            "url_valid":  True,
            "date":       _extract_date(body),
        })

    return jobs


def validate_job_urls(jobs):
    """Parallel HEAD check on all job URLs."""
    def check(job):
        try:
            r = requests.head(job["url"], timeout=3, allow_redirects=True,
                              headers={"User-Agent": "Mozilla/5.0"})
            job["url_valid"] = r.status_code < 400
        except Exception:
            job["url_valid"] = False
        return job

    with ThreadPoolExecutor(max_workers=10) as ex:
        return list(ex.map(check, jobs))


def run_job_search_task(payload, user_id, job_id):
    query   = payload.get("query") or payload.get("topic", "")
    company = payload.get("company", "").strip()

    try:
        # Parallel DDG searches
        models.update_job(job_id, status="running", message="Searching job boards in parallel...")

        with ThreadPoolExecutor(max_workers=3) as ex:
            f_broad   = ex.submit(search_jobs_broad, query, 20)
            f_ats     = ex.submit(search_jobs_ats, query, 10)
            f_company = ex.submit(search_company_jobs, query, company, 12) if company else None
            broad     = f_broad.result()
            ats       = f_ats.result()
            company_results = f_company.result() if f_company else []

        all_raw = company_results + ats + broad

        # Deduplicate by URL
        seen, unique = set(), []
        for r in all_raw:
            url = r.get("href", "")
            if url and url not in seen:
                seen.add(url)
                unique.append(r)

        if not unique:
            models.update_job(job_id, status="error", message="No results found. Try a different query.")
            return

        # Direct parse — instant, no Claude
        models.update_job(job_id, message=f"Parsing {len(unique)} results...")
        jobs = parse_jobs_direct(unique)

        if not jobs:
            models.update_job(
                job_id, status="done",
                message="No listings extracted. Try a more specific search.",
                result_data=[]
            )
            return

        models.update_job(job_id, message=f"Validating {len(jobs)} URLs...")
        jobs = validate_job_urls(jobs)

        source_order = {"Company Direct": 0, "LinkedIn": 1, "Indeed": 2,
                        "Glassdoor": 3, "Remote Board": 4, "Other": 5}
        jobs.sort(key=lambda j: (
            source_order.get(j.get("source", "Other"), 5),
            -j.get("confidence", 1)
        ))

        label = f"at {company}" if company else "worldwide"
        models.update_job(
            job_id, status="done",
            message=f"Found {len(jobs)} job listings {label}.",
            result_data=jobs,
        )

    except Exception as e:
        models.update_job(job_id, status="error", message=f"Error: {str(e)[:200]}")
