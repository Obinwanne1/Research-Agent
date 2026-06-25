---
name: deploy-research-agent
description: >
  Build the full Research Agent web app from scratch in any target directory.
  Flask + SQLite + DuckDuckGo + Claude CLI subprocess. No API key required.
  Includes auth (superadmin/admin/user), research pipeline, job search,
  prompt/skill generators, admin panel, REST API keys, scheduled research,
  article comparison, public sharing, search cache, rate limiting, Claude SDK
  fallback, full-text search, green/white brand, dark mode.
triggers:
  - "build research agent"
  - "deploy research agent"
  - "create research agent app"
  - "reproduce research agent"
  - "/deploy-research-agent"
---

# Deploy Research Agent

## What This Builds
Full-stack Flask web app:
- AI research summaries (DuckDuckGo → fetch → Claude → markdown)
- Job search (DuckDuckGo → Claude extracts structured listings)
- Prompt & skill generators (Claude-powered)
- User auth: superadmin / admin / user roles, CSRF, login rate limiting, session timeout
- REST API key system — automate from scripts, n8n, Zapier
- Scheduled research (daily/weekly recurring, daemon scheduler)
- Article comparison view (up to 4 side-by-side + Claude synthesis)
- Public article sharing via unique token link
- DuckDuckGo search result caching (SQLite, 6h TTL)
- Per-user API rate limiting (sliding window, 429 + Retry-After)
- Claude API SDK fallback (when CLI subprocess fails, uses anthropic SDK if key set)
- SQLite FTS5 full-text search on article library
- Admin panel: user management, job monitor, stats
- Green/white brand (#16a34a accent, #14532d sidebar), dark mode toggle
- Primary: `claude -p` subprocess (no API key). SDK fallback optional.

---

## Step 1 — Gather Target Directory
Ask user for target path if not provided. Default: `C:\Users\<username>\Desktop\ResearchAgent`
Create directory if it doesn't exist.

---

## Step 2 — Install Dependencies
```
pip install flask ddgs markdown requests werkzeug waitress anthropic python-dotenv
```

requirements.txt:
```
flask>=3.1.3
ddgs>=0.1.0
markdown>=3.10.2
requests>=2.33.1
werkzeug>=3.1.8
waitress>=3.0.0
anthropic>=0.93.0
python-dotenv>=1.0.0
```

---

## Step 3 — Create File Structure
Build these files in order (each depends on the previous):

### 3a. config.py
```python
import os
from dotenv import load_dotenv
load_dotenv()

class Config:
    SECRET_KEY              = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod-please")
    DATABASE_PATH           = os.environ.get("DATABASE_PATH", "research_agent.db")
    RESEARCH_BASE_DIR       = os.environ.get("RESEARCH_BASE_DIR", "research")
    MAX_CONCURRENT_JOBS     = int(os.environ.get("MAX_CONCURRENT_JOBS", "10"))
    CLAUDE_TIMEOUT          = int(os.environ.get("CLAUDE_TIMEOUT", "120"))
    CLAUDE_FAST_TIMEOUT     = int(os.environ.get("CLAUDE_FAST_TIMEOUT", "60"))
    PORT                    = int(os.environ.get("PORT", "5001"))
    DEBUG                   = os.environ.get("DEBUG", "true").lower() == "true"
    SESSION_LIFETIME_MINUTES = int(os.environ.get("SESSION_LIFETIME_MINUTES", "30"))
    API_RATE_LIMIT          = int(os.environ.get("API_RATE_LIMIT", "20"))   # calls/user/hour
    SEARCH_CACHE_TTL_HOURS  = int(os.environ.get("SEARCH_CACHE_TTL_HOURS", "6"))
```

---

### 3b. models.py — SQLite layer
All write operations wrapped in `threading.Lock()`.

**Tables to create in `init_db()`:**
```
users(id PK, email UNIQUE, password_hash, display_name,
      role DEFAULT 'user', is_active DEFAULT 1,
      created_at, last_login, must_change_password DEFAULT 0)

research_jobs(id PK, user_id FK, job_type DEFAULT 'research', topic,
              status DEFAULT 'pending', message, result_slug, result_data,
              created_at, completed_at, payload TEXT)

articles(id PK, user_id FK, job_id FK, title, slug, file_path,
         topic, word_count, created_at,
         is_public INTEGER DEFAULT 0, share_token TEXT,
         UNIQUE(user_id, slug))

articles_fts VIRTUAL TABLE USING fts5(title, topic, tokenize='unicode61')

api_keys(id PK, user_id FK, name DEFAULT 'My Key',
         key_hash UNIQUE, is_active DEFAULT 1,
         created_at, last_used_at)

password_reset_tokens(id PK, user_id FK, token UNIQUE,
                      expires_at, used DEFAULT 0, created_at)

search_cache(topic_key TEXT PRIMARY KEY, results_json, cached_at)

schedules(id PK, user_id FK, topic, frequency DEFAULT 'daily',
          is_active DEFAULT 1, next_run_at, last_run_at,
          last_job_id, created_at)
```

**Migrations (run via ALTER TABLE + except pass):**
- `must_change_password` on users
- `payload` on research_jobs
- `is_public`, `share_token` on articles

**On init: promote oldest admin to superadmin if no superadmin exists.**

**After FTS table creation: backfill any existing articles not yet indexed.**

**Helper functions (all read+write patterns):**
```python
# Users
count_users(), get_user_by_email(email), get_user_by_id(user_id)
create_user(email, password_hash, display_name, role="user")
update_user_password(user_id, password_hash)
update_last_login(user_id), set_user_role(user_id, role)
toggle_user_active(user_id), get_all_users(email_filter=None)
delete_user(user_id)

# Password reset
create_reset_token(user_id, token, expires_at)
get_reset_token(token), use_reset_token(token_id)

# Jobs
create_job(user_id, job_type, topic, payload=None) → job_id
reset_stuck_jobs()  # set running→pending on startup
get_pending_jobs(), get_job(job_id, user_id=None)
update_job(job_id, status=None, message=None, result_slug=None, result_data=None)
get_all_jobs(status_filter, type_filter, user_filter, limit=100)
get_jobs_for_user(user_id, limit=50)
delete_job(job_id), delete_jobs_by_status(status)

# Articles
create_article(user_id, job_id, title, slug, file_path, topic, word_count)
  → also inserts into articles_fts
get_article(slug, user_id), get_articles_for_user(user_id)
search_articles(user_id, query, limit=50)  # FTS5 MATCH query

# Public sharing
set_article_share(slug, user_id, make_public) → share_token
  # generates secrets.token_urlsafe(24) on first enable; reuses on re-enable
get_article_by_token(token)  # only if is_public=1

# API keys (hash with SHA-256, never store raw)
create_api_key(user_id, name, raw_key)
get_api_keys_for_user(user_id)
verify_api_key(raw_key) → (user_dict, key_id) or None
revoke_api_key(key_id, user_id), touch_api_key(key_id)

# Search cache
get_search_cache(topic_key, ttl_hours=6) → results or None
set_search_cache(topic_key, results)  # UPSERT ON CONFLICT

# Schedules
create_schedule(user_id, topic, frequency, next_run_at) → schedule_id
get_schedules_for_user(user_id)
get_due_schedules()  # active schedules WHERE next_run_at <= datetime('now')
update_schedule_after_run(schedule_id, next_run_at, job_id)
toggle_schedule(schedule_id, user_id)
delete_schedule(schedule_id, user_id)

# Admin
get_admin_stats() → {total_users, active_users, article_count, job_stats, recent_jobs}
```

---

### 3c. auth.py — decorators + rate limiting + API auth
```python
import secrets, threading
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import session, redirect, url_for, abort, request, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash
import models

# Password helpers
def hash_password(password): return generate_password_hash(password)
def verify_password(password, password_hash): return check_password_hash(password_hash, password)

# Password strength (min 8 chars, 1 number, 1 letter, 1 special)
_SPECIAL_CHARS = set("!@#$%^&*()_+-=[]{}|;:',.<>?/`~")
def validate_password_strength(password): → (ok: bool, error: str|None)

# CSRF
def generate_csrf_token(): # stores in session['csrf_token'], returns it
def validate_csrf(): # compares form csrf_token vs session token with secrets.compare_digest

# Login rate limit (5 attempts, 15 min lockout, in-memory)
_login_attempts = {}  # {identifier: [count, first_attempt, locked_until]}
_login_lock = threading.Lock()
def check_rate_limit(identifier) → (allowed: bool, retry_after_seconds: int)
def record_failed_attempt(identifier)
def clear_rate_limit(identifier)

# API rate limit (sliding window, in-memory per user_id)
_api_windows = {}  # {user_id: [timestamps]}
_api_lock = threading.Lock()
def check_api_rate_limit(user_id) → (allowed: bool, retry_after_seconds: int):
    # from config import Config; limit = Config.API_RATE_LIMIT; window = timedelta(hours=1)
    # filter timestamps > 1h ago; if len >= limit return False + retry_after; else append now

# Session helper
def set_session(user):
    session.permanent = True
    session["user_id"]     = user["id"]
    session["email"]       = user["email"]
    session["role"]        = user["role"]
    session["display_name"] = user["display_name"]
    session["last_activity"] = datetime.now(timezone.utc).isoformat()

# Decorators
@login_required   — checks session["user_id"], else redirect to /login
@admin_required   — checks role in ('admin','superadmin'), else 403
@superadmin_required — checks role == 'superadmin', else 403

# API auth decorator (checks X-API-Key or Authorization: Bearer header)
@api_auth_required:
    raw_key = request.headers.get("X-API-Key") or Bearer from Authorization
    result = models.verify_api_key(raw_key)
    if not result: return 401 JSON
    user, key_id = result
    models.touch_api_key(key_id)
    g.api_user = user; g.api_user_id = user["id"]
```

---

### 3d. background.py — bounded thread pool + scheduler
```python
import json, threading, time as _time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import models
from config import Config

_executor = ThreadPoolExecutor(max_workers=Config.MAX_CONCURRENT_JOBS,
                               thread_name_prefix="ra_worker")
_started = False
_start_lock = threading.Lock()

def _get_handlers():
    # lazy imports to avoid circular
    from research_agent import run_research_task
    from job_scraper import run_job_search_task
    from prompt_generator import run_prompt_gen_task, run_skill_gen_task
    return {"research": run_research_task, "job_search": run_job_search_task,
            "prompt_gen": run_prompt_gen_task, "skill_gen": run_skill_gen_task}

def enqueue(job_type, payload, user_id) → job_id:
    topic = payload.get("topic") or payload.get("query") or "unknown"
    job_id = models.create_job(user_id, job_type, topic, payload=payload)
    _executor.submit(_run_safe, handlers[job_type], payload, user_id, job_id)
    return job_id

def recover_pending_jobs():
    # Called once at startup. Lock-guarded.
    # models.reset_stuck_jobs() — set running→pending
    # for each pending job: restore payload from DB, resubmit to executor

def _run_safe(handler, payload, user_id, job_id):
    try: handler(payload, user_id, job_id)
    except Exception as e: models.update_job(job_id, status='error', message=str(e)[:200])

# Scheduler
_FREQ_DELTA = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1)}
_scheduler_started = False
_scheduler_lock = threading.Lock()

def _scheduler_loop():
    while True:
        _time.sleep(60)
        try:
            due = models.get_due_schedules()
            for sched in due:
                delta = _FREQ_DELTA.get(sched["frequency"], timedelta(days=1))
                next_run = (datetime.utcnow() + delta).isoformat()
                job_id = enqueue("research", {"topic": sched["topic"]}, sched["user_id"])
                models.update_schedule_after_run(sched["id"], next_run, job_id)
        except Exception: pass  # never crash scheduler thread

def start_scheduler():
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started: return
        _scheduler_started = True
    threading.Thread(target=_scheduler_loop, daemon=True, name="ra_scheduler").start()
```

---

### 3e. utils.py — Claude subprocess + SDK fallback
```python
import os, shutil, subprocess
from config import Config

_SDK_MODEL = "claude-sonnet-4-6"

def _resolve_claude():
    # Check ~/AppData/Roaming/npm first (Windows npm global bin)
    npm_bin = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "npm")
    augmented = npm_bin + os.pathsep + os.environ.get("PATH", "")
    found = shutil.which("claude", path=augmented)
    if found: return found
    cmd_path = os.path.join(npm_bin, "claude.cmd")
    if os.path.exists(cmd_path): return cmd_path
    return "claude"

CLAUDE_CMD = _resolve_claude()

def _run_cli(prompt, timeout):
    result = subprocess.run([CLAUDE_CMD, "-p"], input=prompt,
                            capture_output=True, text=True,
                            encoding="utf-8", timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI error: {result.stderr[:300]}")
    output = result.stdout.strip()
    try: output = output.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError): pass
    return output

def _run_sdk(prompt):
    import anthropic
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    msg = client.messages.create(model=_SDK_MODEL, max_tokens=4096,
                                  messages=[{"role": "user", "content": prompt}])
    return msg.content[0].text.strip()

def _run_claude(prompt, timeout):
    try: return _run_cli(prompt, timeout)
    except Exception as cli_err:
        if os.environ.get("ANTHROPIC_API_KEY"):
            return _run_sdk(prompt)
        raise cli_err

def call_claude(prompt):       return _run_claude(prompt, Config.CLAUDE_TIMEOUT)
def call_claude_fast(prompt):  return _run_claude(prompt, Config.CLAUDE_FAST_TIMEOUT)
```

---

### 3f. research_agent.py — core pipeline
`from utils import call_claude`

```python
BLOCKED_DOMAINS = {".cn",".ru",".jp",".kr",".de",".fr"}
BLOCKED_PATTERNS = {"baidu","zhidao","sina","weibo","sogou"}

def run_research_task(payload, user_id, job_id):
    topic = payload.get("topic", "")
    models.update_job(job_id, status="running", message="Searching...")

    # 1. Cache check (skip DDGS if cached within TTL)
    cache_key = topic.lower().strip()
    cached = models.get_search_cache(cache_key, ttl_hours=Config.SEARCH_CACHE_TTL_HOURS)
    if cached:
        results = cached
        models.update_job(job_id, message="Using cached search results...")
    else:
        queries = build_queries(topic)  # 1-3 DDGS queries
        results = search_all_queries(queries)
        if results:
            models.set_search_cache(cache_key, results)

    # 2. Filter: skip blocked domains/patterns
    # 3. Fetch up to 5 pages: GET 10s timeout, text/html only, strip tags, keep 8000 chars
    # 4. Build prompt → call_claude() → 600-1000 word summary
    # 5. Append ## Sources from confirmed HTTP-200 URLs (no hallucinated links)
    # 6. Write research/<user_id>/YYYY-MM-DD_<slug>.md (encoding='utf-8')
    # 7. models.create_article(...) + models.update_job(status='done', result_slug=slug)

def search_all_queries(queries):
    # DDGS().text(q, max_results=10) for each query, dedupe by URL, time.sleep(1) between
    pass

def build_queries(topic):
    # Returns [topic, topic+" overview", topic+" latest"] (3 variations)
    pass
```

---

### 3g. job_scraper.py
`from utils import call_claude`

```python
def run_job_search_task(payload, user_id, job_id):
    query = payload.get("query") or payload.get("topic", "")
    models.update_job(job_id, status="running", message="Searching jobs...")
    # 1. DDGS().text(f"{query} job opening", max_results=15)
    # 2. Fetch page content (optional for richer data)
    # 3. Claude: "Extract jobs as JSON array [{title,company,location,salary,url,tags:[]}]. JSON only."
    # 4. json.loads(output) — strip markdown fences if present
    # 5. models.update_job(status='done', result_data=json.dumps(jobs))
```

---

### 3h. prompt_generator.py
`from utils import call_claude_fast`

```python
PROMPT_META = """You are an expert prompt engineer. Write a Claude/GPT prompt based on the user request.
Output ONLY the prompt text, then a table comparing key parameters with GPT-4."""

SKILL_META = """You are an expert at writing Claude Code skill .md files.
Output a skill file with: triggers, numbered steps, constraints, edge cases.
Format as valid YAML frontmatter + markdown body. Under 500 tokens."""

def run_prompt_gen_task(payload, user_id, job_id):
    # call_claude_fast(PROMPT_META + "\n\nUser request: " + description)
    # save result as article (type=prompt)

def run_skill_gen_task(payload, user_id, job_id):
    # call_claude_fast(SKILL_META + "\n\nUser request: " + description)
    # save result as article (type=skill)
```

---

### 3i. admin.py — Flask Blueprint (url_prefix='/admin')
Decorators: `@admin_bp.before_request` → `@admin_required`

Routes:
```
GET  /admin/            → stats dashboard
GET  /admin/users       → searchable user list (email filter)
GET  /admin/users/<id>  → user detail: profile + articles + jobs + file count
POST /admin/users/<id>/set-role   → form: role=[user|admin|superadmin]
POST /admin/users/<id>/toggle     → flip is_active
POST /admin/users/<id>/delete     → shutil.rmtree(research/<id>/) + delete DB records
GET  /admin/jobs        → filter by status/type/user
POST /admin/jobs/<id>/delete → delete single job
POST /admin/jobs/delete-errors   → bulk delete error jobs
```

---

### 3j. app.py — entry point + all routes

**Startup sequence:**
```python
models.init_db()
os.makedirs(Config.RESEARCH_BASE_DIR, exist_ok=True)
background.recover_pending_jobs()
background.start_scheduler()
```

**Security middleware:**
```python
@app.before_request → session_timeout():
    # check last_activity; if elapsed > SESSION_LIFETIME_MINUTES: session.clear(), redirect /login
    # update last_activity to now (UTC ISO string)

@app.after_request → security_headers(response):
    # X-Content-Type-Options: nosniff
    # X-Frame-Options: DENY
    # X-XSS-Protection: 1; mode=block
    # Referrer-Policy: strict-origin-when-cross-origin
    # Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; ...
    # Strict-Transport-Security (production only)
```

**Route list:**
```
GET  /                     → index (redirect to /dashboard if logged in)
GET|POST /login            → CSRF + rate limit by IP
GET|POST /register         → CSRF + rate limit; first user → superadmin
POST /logout               → session.clear()
GET|POST /change-password  → @login_required; validate_password_strength
GET|POST /forgot-password  → email lookup → create token (24h) → show reset link
GET|POST /reset-password/<token> → validate token, set new password, use_reset_token

GET  /dashboard            → @login_required; articles + recent jobs + search query
GET  /article/<slug>       → @login_required; render markdown → HTML
POST /article/<slug>/delete → @login_required; delete file + DB record
GET  /article/<slug>/download → @login_required; send .md file
POST /article/<slug>/share → @login_required; toggle is_public; return JSON {token,is_public}
GET  /share/<token>        → NO AUTH; render share.html with article content

GET  /compare              → @login_required; ?slugs=a,b,c,d (up to 4)
                            → read article files, render markdown, call Claude synthesis

GET  /jobs/results/<job_id> → @login_required; render jobs.html with result_data

GET|POST /schedules        → @login_required; list + create form
POST /schedules/<id>/toggle → @login_required
POST /schedules/<id>/delete → @login_required

GET  /settings/api-keys    → @login_required; list keys + reveal new_key from flash
POST /settings/api-keys    → create key (ra_ prefix + secrets.token_urlsafe(32))
POST /settings/api-keys/<id>/revoke → revoke key

# API endpoints (accept X-API-Key or Bearer token)
POST /api/research         → @api_auth_required + check_api_rate_limit → background.enqueue
POST /api/jobs/search      → @api_auth_required + check_api_rate_limit → background.enqueue
GET  /api/status/<job_id>  → @api_auth_required → get_job; return {status,message,result_slug,...}

# SSE live status stream (optional alternative to polling)
GET  /api/stream/<job_id>  → @login_required; Response(stream_with_context(generator))
```

**Error handlers:**
```python
@app.errorhandler(403) → render errors/403.html
@app.errorhandler(404) → render errors/404.html
```

**Entry:**
```python
if __name__ == "__main__":
    from waitress import serve
    serve(app, host="0.0.0.0", port=Config.PORT)
```

---

## Step 4 — Templates (Jinja2, inherit from base.html)

### base.html — shared layout
- Fixed sidebar (`background: #14532d`)
- Sidebar sections: **Research** (Dashboard, Schedules) | **Account** (API Keys, Admin) | Logout
- Between sections: `<hr class="sidebar-divider">` (1px rgba white border, margin 6px 12px)
- Nav items: `border-radius: 8px`, hover `rgba(255,255,255,0.08)`, active = green bg + drop shadow
- User email + dark mode toggle in sidebar footer
- Spinner overlay (`#spinner-overlay`) with message + `#spinner-msg`
- Flash messages list (`.flash-item.flash-{category}`)
- CSRF token available via `{{ csrf_token() }}` (registered as jinja global)

### dashboard.html
- Research form: topic input → POST /api/research (AJAX) → start polling /api/status/<job_id>
- Job search form: query input → POST /api/jobs/search → polling
- Prompt generator + Skill generator forms (similar pattern)
- Article library: card grid with title, topic, word count, date, tags
- Article comparison checkboxes: JS tracks selected slugs (max 4), shows floating compare bar
- Compare bar: "Compare X Articles" button → navigate to /compare?slugs=a,b,c,d
- FTS search box: GET /dashboard?q=query → filters article cards

### article.html
- Render article markdown content with `python-markdown` (fenced_code + tables extensions)
- Share toggle button: POST /article/<slug>/share → update button state + copy-link button
- Download .md button → /article/<slug>/download
- Delete button with CSRF confirm

### compare.html
- CSS grid columns (1 per article, repeat(N, 1fr))
- Column headers: topic, title, date, word count, "Open Article" button
- Column content: `.compare-col.card` with `max-height:70vh; overflow-y:auto`
- h1 override: `font-size: 0.95rem` (avoid huge headings in narrow columns)
- h2 override: `font-size: 1rem` with bottom border
- Claude synthesis panel below columns (border-top: 3px solid accent)
- Fallback message if synthesis unavailable

### share.html — standalone public view (no sidebar, no auth)
- Read CSS vars from main.css
- Article title, topic badge, date/word count pills
- Full markdown content rendered
- Branded footer: "Generated by Research Agent"

### schedules/index.html
- Create form: topic input + frequency select (daily/weekly) → POST /schedules
- Schedules table: topic, frequency, next_run_at, last_run_at, status badge, pause/resume/delete
- "How it works" info box

### settings/api_keys.html
- New key reveal card (shown once after creation)
- Create form: name input
- Keys table: name, created, last_used, active/revoked badge, revoke button
- Usage docs: curl example with X-API-Key header

| Template | Must include |
|----------|--------------|
| index.html | Landing: hero + feature list + Register/Login links |
| jobs.html | Job cards: title/company/location/salary/tags/Apply button |
| auth/login.html | CSRF token, email+password, forgot password link |
| auth/register.html | CSRF token, email+password+display_name |
| auth/change_password.html | CSRF token, new+confirm password |
| auth/forgot_password.html | Email input → shows reset link in flash |
| auth/reset_password.html | Token in URL, new+confirm password |
| admin/index.html | Stat cards (users, active, articles, jobs) + recent 10 jobs |
| admin/users.html | Searchable by email; per-row: toggle/set-role/delete |
| admin/user_detail.html | Profile + articles + jobs + file count |
| admin/jobs.html | Filter by status/type/user; delete individual; bulk delete errors |
| errors/403.html | Forbidden + link to dashboard |
| errors/404.html | Not found + link to dashboard |

---

## Step 5 — CSS & JS

### CSS (static/css/main.css) — key rules
```css
:root {
  --bg: #f0fdf4;
  --accent: #16a34a;
  --accent-dark: #14532d;
  --accent-light: #dcfce7;
  --text: #1a1a1a;
  --text-muted: #6b7280;
  --border: #d1fae5;
  --bg-card: #fff;
  --radius: 8px;
  --font: 'Segoe UI', system-ui, sans-serif;
}
body.dark-mode {
  --bg: #0a1a0f;
  --text: #e2e8f0;
  --text-muted: #94a3b8;
  --border: #1e3a2a;
  --bg-card: #0f2a18;
  --accent-light: #14532d;
}
.sidebar {
  background: #14532d;
  color: #fff;
  width: 220px;
  min-height: 100vh;
  display: flex; flex-direction: column;
}
.sidebar-nav a, .logout-nav-btn {
  display: flex; align-items: center; gap: 10px;
  padding: 9px 14px;
  border-radius: 8px;
  color: rgba(255,255,255,0.8);
  text-decoration: none;
  transition: background 0.15s;
}
.sidebar-nav a:hover, .logout-nav-btn:hover {
  background: rgba(255,255,255,0.08);
  color: #fff;
}
.sidebar-nav a.active {
  background: #16a34a;
  color: #fff;
  box-shadow: 0 2px 8px rgba(0,0,0,0.25);
}
.sidebar-divider {
  border: none;
  border-top: 1px solid rgba(255,255,255,0.08);
  margin: 6px 12px;
}
```

Cards: grid `minmax(280px,1fr)`, hover `translateY(-2px)` + shadow
Badges: `.badge-pending` gray | `.badge-running` blue | `.badge-done` green | `.badge-error` red
Spinner overlay: full-screen, backdrop blur, centered box with animated ring + message

### JS (static/js/main.js)
```javascript
// Theme: toggle .dark-mode on body, persist to localStorage
// Spinner: show on submit; poll /api/status/<job_id> every 1500ms
//   → research/generators done: redirect to /article/<slug>
//   → job search done: redirect to /jobs/results/<job_id>
//   → error: show flash, hide spinner
// Article comparison checkboxes:
//   track selected = [] (max 4), show/hide #compare-bar
//   "Compare" button → /compare?slugs=a,b,c,d
// Share button: POST /article/<slug>/share → update button text + show copy-link
// Copy link: navigator.clipboard.writeText(shareUrl)
```

---

## Step 6 — git init + .gitignore
```
*.db
research/
__pycache__/
*.pyc
.env
.server.log
.server.err
*.html.bak
```

---

## Step 7 — Verify (run each check)
```
1. python app.py → "Running on http://0.0.0.0:5001"
2. GET http://localhost:5001 → HTTP 200 (landing page)
3. Register → first user auto-superadmin → /dashboard loads with sidebar
4. Submit research topic → spinner → polls /api/status/<job_id> → done → /article/<slug>
   Article has ## Overview / ## Key Findings / ## Sources with real URLs
5. GET /admin/ → stats page, no 403
6. Submit job search → job cards with Apply links
7. Submit prompt → article with generated prompt + comparison table
8. API Keys: generate key → copy → curl /api/research with X-API-Key header → 202 + job_id
9. Schedules: create daily schedule → check schedules page shows next_run_at
10. Compare: check 2+ articles → floating bar appears → /compare → side-by-side columns
11. Share: open article → toggle Public → copy link → visit /share/<token> without login
12. Dark mode: click ☾ Dark → page switches theme → reload persists
```

---

## Critical Constraints
- **NEVER** store plaintext passwords — always werkzeug hash
- **NEVER** use `&&` in PowerShell — use `;` or separate calls
- **ALWAYS** `encoding='utf-8'` on all file I/O
- `threading.Lock()` on ALL SQLite writes
- `UNIQUE(user_id, slug)` — INSERT OR IGNORE on duplicate
- API key stored as SHA-256 hash only — raw key shown ONCE at creation
- Share token generated with `secrets.token_urlsafe(24)` — reused on re-enable (stable links)
- Scheduler loop: never crash (broad except+pass), sleep 60s between checks
- Job executor: bounded by MAX_CONCURRENT_JOBS — rejects or queues excess
- `_run_sdk()` only called if `ANTHROPIC_API_KEY` env var is set; never prompt for key
- CSRF token on every state-changing form POST
- Session timeout middleware must handle naive datetimes (add UTC tzinfo if missing)

## Common Errors & Fixes
| Error | Fix |
|-------|-----|
| UnicodeDecodeError on Claude output | CP1252 fallback already in _run_cli |
| Port 5001 in use | `netstat -ano \| findstr :5001` → `taskkill /PID <pid> /F` |
| DDGS rate limit | `time.sleep(1)` between searches already in place |
| Circular import | Lazy imports inside `_get_handlers()` in background.py |
| SQLite locked | threading.Lock() on all write cursors |
| Slug collision | UNIQUE constraint + INSERT OR IGNORE (except IntegrityError: pass) |
| claude.cmd not found | _resolve_claude() checks npm bin path; fall back to "claude" |
| Session datetime comparison error | Ensure last_activity stored as UTC ISO; add tzinfo if naive |
| FTS5 not available | SQLite must be compiled with FTS5 (default on Windows Python) |
| Search cache stale | get_search_cache compares datetime.utcnow() vs cached_at; returns None if expired |
