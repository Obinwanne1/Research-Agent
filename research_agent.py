import json
import os
import re
import subprocess
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from ddgs import DDGS
import models
from config import Config
from utils import call_claude, call_claude_fast

# ── Domain filter ─────────────────────────────────────────────────────────────

BLOCKED_DOMAINS = (".cn", ".ru", ".jp", ".kr")
BLOCKED_KEYWORDS = ("baidu.", "zhidao.", "sina.", "weibo.")

def _is_blocked(url):
    url_lower = url.lower()
    return any(url_lower.endswith(d) or f"{d}/" in url_lower for d in BLOCKED_DOMAINS) or \
           any(kw in url_lower for kw in BLOCKED_KEYWORDS)


# ── Planning step — multi-step reasoning ──────────────────────────────────────

def plan_search_queries(topic):
    """Ask Claude to decompose the topic into 3 focused sub-queries."""
    prompt = (
        f'Topic: {topic}\n\n'
        'Generate exactly 3 focused web search queries to research this topic thoroughly.\n'
        'Each query must target a different angle: (1) broad overview, (2) specific evidence/data, (3) real-world examples.\n'
        'Return ONLY a JSON array of 3 strings. No explanation, no markdown.\n'
        'Example: ["query one", "query two", "query three"]'
    )
    try:
        raw = call_claude_fast(prompt)
        raw = re.sub(r"^```[a-z]*\n?", "", raw.strip())
        raw = re.sub(r"\n?```$", "", raw.strip())
        queries = json.loads(raw)
        if isinstance(queries, list):
            return [topic] + [str(q) for q in queries[:3]]
    except Exception:
        pass
    return [topic]  # fallback: original topic only


# ── Search ────────────────────────────────────────────────────────────────────

def _search_one(query, max_results=8):
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return [{"url": r["href"], "title": r["title"]} for r in results if "href" in r]
    except Exception:
        return []


def search_all_queries(queries):
    """Run all search queries in parallel, deduplicate by URL."""
    all_results = []
    seen_urls = set()
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(_search_one, q): q for q in queries}
        for fut in as_completed(futures):
            for item in fut.result():
                if item["url"] not in seen_urls and not _is_blocked(item["url"]):
                    seen_urls.add(item["url"])
                    all_results.append(item)
    return all_results


# ── Page fetcher — parallel ───────────────────────────────────────────────────

_FETCH_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ResearchAgent/1.0)"}


def _fetch_one(result):
    url = result["url"]
    try:
        resp = requests.get(url, timeout=10, headers=_FETCH_HEADERS)
        if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
            text = re.sub(r"<[^>]+>", " ", resp.text)
            text = re.sub(r"\s+", " ", text).strip()
            return {"url": url, "title": result["title"], "content": text[:8000]}
    except Exception:
        pass
    return None


def fetch_pages_parallel(search_results, max_pages=5):
    candidates = [r for r in search_results if not _is_blocked(r["url"])]
    fetched = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(_fetch_one, r) for r in candidates[:max_pages * 2]]
        for fut in as_completed(futures):
            result = fut.result()
            if result and len(fetched) < max_pages:
                fetched.append(result)
    return fetched


# ── Topic category + staleness ───────────────────────────────────────────────

_CATEGORY_MAP = [
    ("fast",   14,  ["ai", "artificial intelligence", "crypto", "bitcoin", "ethereum",
                     "market", "stock", "geopolit", "ukraine", "war", "election",
                     "breaking", "latest", "2024", "2025", "2026"]),
    ("medium", 30,  ["technology", "tech", "startup", "company", "business", "product",
                     "software", "regulation", "policy", "law", "drug", "clinical"]),
    ("slow",   60,  ["science", "research", "study", "health", "medicine", "biology",
                     "physics", "climate", "environment", "psychology"]),
    ("stable", 90,  ["history", "philosophy", "mathematics", "economics", "theory",
                     "fundamental", "how to", "guide", "introduction", "overview"]),
]

def detect_topic_category(topic):
    """Return (category_name, staleness_days) based on keyword match."""
    lower = topic.lower()
    for cat, days, keywords in _CATEGORY_MAP:
        if any(kw in lower for kw in keywords):
            return cat, days
    return "medium", 30


# ── Confidence grader ────────────────────────────────────────────────────────

def grade_confidence(topic, summary, source_count):
    """Ask Claude to score research confidence 1-10 and suggest alternative queries."""
    prompt = (
        f'Research topic: {topic}\n'
        f'Sources used: {source_count}\n\n'
        f'Summary excerpt (first 800 chars):\n{summary[:800]}\n\n'
        'Rate the research confidence on a scale of 1-10:\n'
        '10 = comprehensive, multiple high-quality sources, no significant gaps\n'
        '7-9 = good coverage, minor gaps\n'
        '5-6 = partial coverage, some key areas missing\n'
        '1-4 = poor coverage, limited or low-quality sources\n\n'
        'Also provide 2 alternative search queries that would fill gaps in coverage.\n'
        'Return ONLY a JSON object, no explanation:\n'
        '{"confidence": <1-10>, "reason": "<10-15 words>", "alternative_queries": ["query1", "query2"]}'
    )
    try:
        raw = call_claude_fast(prompt)
        raw = re.sub(r"^```[a-z]*\n?", "", raw.strip())
        raw = re.sub(r"\n?```$", "", raw.strip())
        data = json.loads(raw)
        return {
            "confidence": max(1, min(10, int(data.get("confidence", 5)))),
            "reason": str(data.get("reason", ""))[:120],
            "alternative_queries": [str(q) for q in data.get("alternative_queries", [])[:2]],
        }
    except Exception:
        return {"confidence": 5, "reason": "Could not grade", "alternative_queries": []}


# ── Slug generator ────────────────────────────────────────────────────────────

def make_slug(topic):
    date_str = datetime.now().strftime("%Y-%m-%d")
    slug_part = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:60]
    return f"{date_str}_{slug_part}"


# ── Main research pipeline ────────────────────────────────────────────────────

def run_research_task(payload, user_id, job_id):
    topic = payload["topic"]

    try:
        # Step 1: Plan — decompose topic into sub-queries
        models.update_job(job_id, status="running", message="Planning search strategy...")
        queries = plan_search_queries(topic)
        models.update_job(job_id, message=f"Searching {len(queries)} angles in parallel...")

        # Step 2: Search (cache-first, then live DDGS)
        cache_key = topic.lower().strip()
        cached = models.get_search_cache(cache_key, ttl_hours=Config.SEARCH_CACHE_TTL_HOURS)
        if cached:
            results = cached
            models.update_job(job_id, message="Using cached search results...")
        else:
            results = search_all_queries(queries)
            if results:
                models.set_search_cache(cache_key, results)
        if not results:
            models.update_job(job_id, status="error", message="No search results found.")
            return

        # Detect explicit list requests (top 10 tools, 20 best apps, etc.)
        quantity_match = re.search(
            r'\b(\d+)\s+(?:top|best|types?|examples?|ways?|tips?|tools?|steps?|ideas?|apps?|reasons?|methods?)\b'
            r'|(?:top|best)\s+(\d+)\b',
            topic, re.IGNORECASE
        )
        requested_count = int(quantity_match.group(1) or quantity_match.group(2)) if quantity_match else None
        max_pages = 6 if requested_count and requested_count >= 10 else 5

        # Step 3: Fetch pages in parallel
        models.update_job(job_id, message="Fetching pages in parallel...")
        pages = fetch_pages_parallel(results, max_pages=max_pages)
        if not pages:
            models.update_job(job_id, status="error", message="Could not fetch any pages.")
            return

        # Step 4: Summarize with Claude (strict grounding + citations)
        doc_context = payload.get("doc_context", "")
        has_docs = bool(doc_context and doc_context.strip())
        msg = f"Synthesising {len(pages)} web sources" + (" + internal documents" if has_docs else "") + " with Claude..."
        models.update_job(job_id, message=msg)

        numbered_sources = "\n\n".join(
            f"[Source {i+1}] URL: {p['url']}\nTITLE: {p['title']}\nCONTENT:\n{p['content']}"
            for i, p in enumerate(pages)
        )

        if requested_count:
            count_instruction = (
                f"\nIMPORTANT: The topic requests exactly {requested_count} items. "
                f"List ALL {requested_count} using ### headings numbered 1 to {requested_count}. "
                f"Do not group or abbreviate."
            )
            word_guide = f"{requested_count * 60}–{requested_count * 100}"
        else:
            count_instruction = ""
            word_guide = "600–1000"

        internal_section = ""
        if has_docs:
            internal_section = (
                "\n\nINTERNAL DOCUMENTS (provided by the user — treat as primary authoritative sources):\n"
                f"{doc_context}\n"
                "\nWhen internal documents are relevant, prioritise them and cite as [Internal Document].\n"
            )

        prompt = f"""Research topic: {topic}

GROUNDING RULE: Use ONLY facts explicitly stated in the source content below.
Do NOT add external knowledge, statistics, dates, names, or claims not present in these sources.
If a source does not support a claim, omit it entirely.
For each key fact or statistic, add an inline citation: [Source 1], [Source 2], etc.
Sources are numbered in the order they appear below.
{internal_section}
{numbered_sources}

Write a detailed research summary in plain English ({word_guide} words).{count_instruction}
Structure:
## Overview
## Key Findings
(use ### for each individual item or finding)

Do NOT include a Sources section — it will be appended automatically.
Output ONLY the markdown — no preamble, no closing remarks."""

        summary = call_claude(prompt)
        summary = re.sub(r'\n## Sources.*', '', summary, flags=re.DOTALL).strip()

        # Step 5: Grade confidence — re-run if < 7
        models.update_job(job_id, message="Grading source quality...")
        grade = grade_confidence(topic, summary, len(pages))
        confidence = grade["confidence"]
        iteration_count = 1

        if confidence < 7 and grade["alternative_queries"]:
            models.update_job(job_id, message=f"Confidence {confidence}/10 — fetching additional sources...")
            extra_results = search_all_queries(grade["alternative_queries"])
            existing_urls = {p["url"] for p in pages}
            extra_results = [r for r in extra_results if r["url"] not in existing_urls]
            extra_pages = fetch_pages_parallel(extra_results, max_pages=4)
            if extra_pages:
                pages = pages + extra_pages
                iteration_count = 2
                models.update_job(job_id, message=f"Re-synthesising with {len(pages)} total sources...")

                numbered_sources = "\n\n".join(
                    f"[Source {i+1}] URL: {p['url']}\nTITLE: {p['title']}\nCONTENT:\n{p['content']}"
                    for i, p in enumerate(pages)
                )
                prompt2 = f"""Research topic: {topic}

GROUNDING RULE: Use ONLY facts explicitly stated in the source content below.
Do NOT add external knowledge, statistics, dates, names, or claims not present in these sources.
For each key fact or statistic, add an inline citation: [Source 1], [Source 2], etc.
{internal_section}
{numbered_sources}

Write a detailed research summary in plain English ({word_guide} words).{count_instruction}
Structure:
## Overview
## Key Findings
(use ### for each individual item or finding)

Do NOT include a Sources section — it will be appended automatically.
Output ONLY the markdown — no preamble, no closing remarks."""

                summary = call_claude(prompt2)
                summary = re.sub(r'\n## Sources.*', '', summary, flags=re.DOTALL).strip()

                # Re-grade after second pass (cap — never loop again)
                grade2 = grade_confidence(topic, summary, len(pages))
                confidence = grade2["confidence"]

        # Verified sources (all returned HTTP 200)
        source_items = "\n".join(
            f'<li><a href="{p["url"]}" target="_blank" rel="noopener noreferrer">{p["title"] or p["url"]}</a></li>'
            for p in pages
        )
        sources_md = f"\n\n## Sources\n<ul>\n{source_items}\n</ul>"

        # Step 6: Save
        models.update_job(job_id, message="Saving article...")
        slug = make_slug(topic)
        user_dir = os.path.join(Config.RESEARCH_BASE_DIR, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        file_path = os.path.join(user_dir, f"{slug}.md")

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"# {topic}\n\n")
            f.write(summary)
            f.write(sources_md)

        word_count = len(summary.split())
        topic_category, staleness_days = detect_topic_category(topic)
        workspace = models.get_workspace_for_user(user_id)
        parent_id = payload.get("parent_article_id")
        models.create_article(
            user_id=user_id, job_id=job_id,
            title=topic.title(), slug=slug,
            file_path=os.path.join(str(user_id), f"{slug}.md"),
            topic=topic, word_count=word_count,
            workspace_id=workspace["id"] if workspace else None,
            confidence_score=confidence,
            source_count=len(pages),
            iteration_count=iteration_count,
            topic_category=topic_category,
            staleness_days=staleness_days,
            parent_article_id=parent_id,
        )
        models.update_job(job_id, status="done", message="Research complete!", result_slug=slug)

        # Embed article for semantic search (Feature 6) — non-blocking best-effort
        try:
            import embeddings as _emb
            art = models.get_article(slug, user_id)
            if art:
                _emb.embed_and_store(art["id"], f"{topic} {summary[:600]}")
        except Exception:
            pass  # embedding is non-critical

    except subprocess.TimeoutExpired:
        models.update_job(job_id, status="error", message="Claude CLI timed out. Try again.")
    except Exception as e:
        models.update_job(job_id, status="error", message=f"Error: {str(e)[:200]}")
