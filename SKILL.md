---
name: deploy-research-agent
description: >
  Build the full Research Agent web app from scratch in any target directory.
  Flask + SQLite + DuckDuckGo + Claude CLI subprocess. No API key required.
  Includes auth (superadmin/admin/user), research pipeline, job search,
  prompt/skill generators, admin panel, green/white brand, dark mode.
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
- User auth: superadmin / admin / user roles
- Admin panel: user management, job monitor, stats
- Green/white brand (#16a34a accent, #14532d sidebar), dark mode toggle
- No Anthropic API key — uses `claude -p` subprocess via existing Claude Code session

---

## Step 1 — Gather Target Directory
Ask user for target path if not provided. Default: `C:\Users\<username>\Desktop\ResearchAgent`
Create directory if it doesn't exist.

---

## Step 2 — Install Dependencies
```
pip install flask ddgs markdown requests werkzeug waitress
```
requirements.txt:
```
flask>=3.1.0
ddgs>=0.1.0
markdown>=3.10.0
requests>=2.33.0
werkzeug>=3.1.0
waitress>=3.0.0
```

---

## Step 3 — Create File Structure
Build these files in order (each depends on the previous):

### 3a. config.py
```python
import os
class Config:
    SECRET_KEY        = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod-please")
    DATABASE_PATH     = os.environ.get("DATABASE_PATH", "research_agent.db")
    RESEARCH_BASE_DIR = os.environ.get("RESEARCH_BASE_DIR", "research")
    MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "10"))
    CLAUDE_TIMEOUT    = int(os.environ.get("CLAUDE_TIMEOUT", "120"))
    PORT  = int(os.environ.get("PORT", "5000"))
    DEBUG = os.environ.get("DEBUG", "true").lower() == "true"
```

### 3b. models.py — SQLite layer
Tables:
```
users(id PK, email UNIQUE, password_hash, display_name,
      role DEFAULT 'user', is_active DEFAULT 1, created_at, last_login)
research_jobs(id PK, user_id FK, job_type DEFAULT 'research', topic,
              status DEFAULT 'pending', message, result_slug, result_data,
              created_at, completed_at)
articles(id PK, user_id FK, job_id FK, title, slug, file_path,
         topic, word_count, created_at)
UNIQUE(user_id, slug)
```
init_db(): create tables + promote oldest admin to superadmin
Thread-safety: threading.Lock() wraps all write operations
Helper functions: create/get/update/delete for users, jobs, articles + get_admin_stats()

### 3c. auth.py — decorators + routes
Decorators: @login_required  @admin_required  @superadmin_required
Session: {user_id, email, role, display_name}
Routes: GET|POST /login  /register  GET /logout
First registered user → role='superadmin'
Passwords: werkzeug generate_password_hash / check_password_hash

### 3d. background.py — async job queue
```python
def enqueue(job_type, payload, user_id):
    job_id = models.create_job(...)
    threading.Thread(target=_run_safe, args=(...), daemon=True).start()
    return job_id

def _run_safe(handler, payload, user_id, job_id):
    try: handler(payload, user_id, job_id)
    except Exception as e: models.update_job(job_id, status='error', message=str(e))
```
Dispatch: research→research_agent | job_search→job_scraper | prompt_gen/skill_gen→prompt_generator
Use lazy imports inside dispatch to avoid circular imports.

### 3e. utils.py — shared Claude subprocess (create FIRST, import everywhere)
```python
import subprocess

def call_claude(prompt, timeout=120):
    try:
        r = subprocess.run(["claude", "-p"], input=prompt,
                           capture_output=True, text=True,
                           timeout=timeout, encoding='utf-8')
        return r.stdout.strip()
    except UnicodeDecodeError:
        r = subprocess.run(["claude", "-p"],
                           input=prompt.encode('utf-8'),
                           capture_output=True, timeout=timeout)
        return r.stdout.decode('utf-8', errors='replace').strip()
# All pipeline files: from utils import call_claude
# NEVER import anthropic. NEVER ask for API key.
```

### 3f. research_agent.py — core pipeline
`from utils import call_claude`
Pipeline:
1. DDGS().text(topic, max_results=10)
2. Filter: skip .cn .ru .jp .kr domains; skip baidu/zhidao/sina/weibo in URL
3. Fetch up to 5 pages: GET 10s timeout, text/html only, strip tags, keep 8000 chars
4. Claude prompt → "## Overview\n## Key Findings" (600-1000 words, no preamble)
5. Append ## Sources from verified fetched URLs (not Claude-generated)
6. Write research/<user_id>/YYYY-MM-DD_<slug>.md (encoding='utf-8')
7. models.create_article + models.update_job(status='done', result_slug=slug)

### 3g. job_scraper.py
`from utils import call_claude`
1. DDGS().text(f"{query} job opening", max_results=15)
2. Claude: "Extract as JSON array [{title,company,location,salary,url,tags:[]}]. JSON only."
3. models.update_job(status='done', result_data=json.dumps(jobs))

### 3h. prompt_generator.py
`from utils import call_claude`
PROMPT_META: ≤200-token action-first prompt + token estimate + GPT-4 comparison table + edge cases
SKILL_META: ≤500-token Claude Code skill .md with triggers, numbered steps, constraints, edge cases
Both: call_claude(meta + "\n\nUser request: " + description) → save as article

### 3i. admin.py — Flask Blueprint (url_prefix='/admin')
Routes: GET /  GET /users  POST /users/<id>/set-role  GET /users/<id>
        POST /users/<id>/toggle  POST /users/<id>/delete
        GET /jobs  POST /jobs/<id>/delete  POST /jobs/delete-errors
Delete user: shutil.rmtree(research/<user_id>/) + delete all DB records
Stats: COUNT users, active; COUNT articles; GROUP BY job status; 10 recent jobs

### 3j. app.py — entry point
Register admin blueprint. Define all user/API routes.
Startup: init_db() + os.makedirs(RESEARCH_BASE_DIR) + app.run(0.0.0.0:5000, use_reloader=False)
Error handlers: 403 → errors/403.html | 404 → errors/404.html

---

## Step 4 — Templates (Jinja2 inheritance from base.html)

| Template | Must include |
|----------|--------------|
| base.html | Fixed sidebar (#14532d), theme toggle (☾/☀ btn), spinner overlay, flash messages |
| dashboard.html | Research form + job search form + generators form + article cards grid + recent jobs table |
| article.html | Python `markdown` lib render (fenced_code + tables extensions); linkify bare URLs |
| jobs.html | Job cards: title/company/location/salary/tags/Apply button |
| admin/index.html | Stat cards (users, active, articles, jobs by status) + recent 10 jobs |
| admin/users.html | Searchable by email; per row: toggle/set-role/delete |
| admin/user_detail.html | Profile + articles list + jobs list + file count |
| admin/jobs.html | Filter by status/type/user; delete individual; bulk delete errors |
| errors/403.html | Forbidden + link to dashboard |
| errors/404.html | Not found + link to dashboard |

---

## Step 5 — CSS & JS

### CSS (static/css/main.css) — key rules
```css
:root {
  --bg:#f0fdf4; --accent:#16a34a; --accent-dark:#14532d;
  --text:#1a1a1a; --border:#d1fae5; --bg-card:#fff; --radius:8px;
}
body.dark-mode { --bg:#0a1a0f; --text:#e2e8f0; --border:#1e3a2a; --bg-card:#0f2a18; }
.sidebar { background:#14532d !important; color:#fff !important; }
```
Cards: grid `minmax(280px,1fr)`, hover `translateY(-2px)` + shadow
Badges: pending=grey | running=blue | done=green | error=red

### JS (static/js/main.js)
```javascript
// Theme: toggle .dark-mode on body, persist to localStorage
// Spinner: show on submit, update message each poll tick, hide on done/error
// Poll every 1500ms → research/generators done: /article/<slug>
//                   → job search done: /jobs/results/<job_id>
//                   → error: show flash, hide spinner
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
```

---

## Step 7 — Verify (run each check)
```
1. python app.py → "Running on http://0.0.0.0:5000"
2. GET http://localhost:5000 → HTTP 200
3. Register → first user auto-superadmin → /dashboard loads
4. Submit research topic → spinner → polls to done → /article/<slug> with ## Overview / ## Key Findings / ## Sources
5. GET /admin/ → stats page loads, no 403
6. Submit job search → job cards with Apply links
7. Submit prompt description → article with generated prompt
```

---

## Critical Constraints
- NEVER import anthropic or use Anthropic SDK
- NEVER ask for API key
- NEVER store plaintext passwords
- ALWAYS encoding='utf-8' on all file I/O
- NEVER use `&&` in shell — use `;` or separate calls
- threading.Lock() on ALL SQLite writes
- UNIQUE(user_id, slug) — INSERT OR IGNORE on duplicate

## Common Errors & Fixes
| Error | Fix |
|-------|-----|
| UnicodeDecodeError on Claude output | CP1252 fallback (see call_claude above) |
| Port 5000 in use | `netstat -ano \| findstr :5000` → `taskkill /PID <pid> /F` |
| DDGS rate limit | `time.sleep(1)` between searches |
| Circular import | Lazy imports inside dispatch function |
| SQLite locked | threading.Lock() on all write cursors |
| Slug collision | UNIQUE constraint + INSERT OR IGNORE |
