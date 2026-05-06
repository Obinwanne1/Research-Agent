import os
import re
import subprocess
import requests
from datetime import datetime
from ddgs import DDGS
import models
from config import Config

# ── Domain filter ─────────────────────────────────────────────────────────────
BLOCKED_DOMAINS = (".cn", ".ru", ".jp", ".kr")
BLOCKED_KEYWORDS = ("baidu.", "zhidao.", "sina.", "weibo.")

def _is_blocked(url):
    url_lower = url.lower()
    return any(url_lower.endswith(d) or f"{d}/" in url_lower for d in BLOCKED_DOMAINS) or \
           any(kw in url_lower for kw in BLOCKED_KEYWORDS)


# ── Search sources registry ───────────────────────────────────────────────────
# To add a new source: write a function matching (topic: str) -> list[dict]
# where each dict has "url" and "title" keys, then add it here.

def search_duckduckgo(topic):
    with DDGS() as ddgs:
        results = list(ddgs.text(topic, max_results=10))
    return [{"url": r["href"], "title": r["title"]} for r in results if "href" in r]

SEARCH_SOURCES = {
    "duckduckgo": search_duckduckgo,
}


# ── Page fetcher ──────────────────────────────────────────────────────────────

def fetch_pages(search_results, max_pages=2):
    fetched = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ResearchAgent/1.0)"}
    for result in search_results:
        if len(fetched) >= max_pages:
            break
        url = result["url"]
        if _is_blocked(url):
            continue
        try:
            resp = requests.get(url, timeout=10, headers=headers)
            if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
                # Strip HTML tags simply
                text = re.sub(r"<[^>]+>", " ", resp.text)
                text = re.sub(r"\s+", " ", text).strip()
                fetched.append({"url": url, "title": result["title"], "content": text[:8000]})
        except Exception:
            continue
    return fetched


# ── Claude CLI wrapper ────────────────────────────────────────────────────────

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
    # Sanitize: if Windows decoded UTF-8 bytes as CP1252, reverse it
    try:
        output = output.encode('cp1252').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass  # already clean UTF-8, leave as-is
    return output


# ── Slug generator ────────────────────────────────────────────────────────────

def make_slug(topic):
    date_str = datetime.now().strftime("%Y-%m-%d")
    slug_part = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:60]
    return f"{date_str}_{slug_part}"


# ── Main research pipeline ────────────────────────────────────────────────────

def run_research_task(payload, user_id, job_id):
    topic = payload["topic"]
    source = payload.get("source", "duckduckgo")

    try:
        # Step 1: Search
        models.update_job(job_id, status="running", message="Searching the web...")
        results = SEARCH_SOURCES[source](topic)
        if not results:
            models.update_job(job_id, status="error", message="No search results found.")
            return

        # Detect if topic requests a specific number of items (e.g. "20 top AI", "top 10 tools")
        quantity_match = re.search(r'\b(\d+)\b', topic)
        requested_count = int(quantity_match.group(1)) if quantity_match else None
        max_pages = 5 if requested_count and requested_count >= 10 else 3

        # Step 2: Fetch pages
        models.update_job(job_id, message="Fetching pages...")
        pages = fetch_pages(results, max_pages=max_pages)
        if not pages:
            models.update_job(job_id, status="error", message="Could not fetch any pages.")
            return

        # Step 3: Summarize with Claude CLI
        models.update_job(job_id, message="Summarizing with Claude...")
        sources_text = "\n\n".join(
            f"SOURCE: {p['url']}\nTITLE: {p['title']}\nCONTENT:\n{p['content']}"
            for p in pages
        )

        if requested_count:
            count_instruction = (
                f"\nIMPORTANT: The topic explicitly requests {requested_count} items. "
                f"You MUST list ALL {requested_count} of them — no more, no less. "
                f"Number each item using ### and its number (e.g. ### 1. Item Name). "
                f"Do not summarize or group items together to reach a shorter count."
            )
            word_guide = f"{requested_count * 80}–{requested_count * 120}"
        else:
            count_instruction = ""
            word_guide = "600–1000"

        prompt = f"""Research topic: {topic}

Here is content fetched from the web:

{sources_text}

Write a detailed research summary in plain English ({word_guide} words).{count_instruction}
Structure:
## Overview
## Key Findings
(use ### for each individual item heading)

Do NOT include a Sources section — it will be added automatically.
Output ONLY the markdown content — no preamble."""

        summary = call_claude(prompt)

        # Strip any Sources section Claude may have added anyway
        summary = re.sub(r'\n## Sources.*', '', summary, flags=re.DOTALL).strip()

        # Build verified sources from pages we actually fetched (all returned HTTP 200)
        # Use raw HTML so links are clickable with target="_blank" and title chars can't break markdown
        source_items = "\n".join(
            f'<li><a href="{p["url"]}" target="_blank" rel="noopener noreferrer">{p["title"] or p["url"]}</a></li>'
            for p in pages
        )
        sources_md = f"\n\n## Sources\n<ul>\n{source_items}\n</ul>"

        # Step 4: Save to file
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
        title = topic.title()

        models.create_article(
            user_id=user_id,
            job_id=job_id,
            title=title,
            slug=slug,
            file_path=os.path.join(str(user_id), f"{slug}.md"),
            topic=topic,
            word_count=word_count
        )
        models.update_job(job_id, status="done", message="Research complete!", result_slug=slug)

    except subprocess.TimeoutExpired:
        models.update_job(job_id, status="error", message="Claude CLI timed out. Try again.")
    except Exception as e:
        models.update_job(job_id, status="error", message=f"Error: {str(e)[:200]}")
