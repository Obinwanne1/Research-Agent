# Research Agent — Reproduction Prompt

> Paste this entire file as your first message. Claude builds the full app with no follow-up needed.

---

Build a Flask web app: AI research summaries, job search, prompt/skill generators, user auth, admin panel. Green/white brand. No Anthropic API key — uses `claude -p` subprocess via existing Claude Code CLI session.

## Stack
Python 3.10+ | Flask | SQLite | ddgs | requests | markdown | werkzeug | waitress | Vanilla JS+CSS

```
pip install flask ddgs markdown requests werkzeug waitress
```

## File Tree
```
app.py  auth.py  admin.py  background.py  config.py  models.py  utils.py
research_agent.py  job_scraper.py  prompt_generator.py  requirements.txt
templates/base.html  index.html  dashboard.html  article.html  jobs.html
templates/auth/login.html  register.html
templates/admin/index.html  users.html  user_detail.html  jobs.html
templates/errors/403.html  404.html
static/css/main.css  static/js/main.js
research/   (auto-created, stores per-user markdown output)
```

## config.py
```python
import os
class Config:
    SECRET_KEY   = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod-please")
    DATABASE_PATH= os.environ.get("DATABASE_PATH", "research_agent.db")
    RESEARCH_BASE_DIR = os.environ.get("RESEARCH_BASE_DIR", "research")
    MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "10"))
    CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "120"))
    PORT  = int(os.environ.get("PORT", "5000"))
    DEBUG = os.environ.get("DEBUG", "true").lower() == "true"
```

## DB Schema (SQLite, auto-create on init_db())
```
users(id PK, email UNIQUE NOT NULL, password_hash NOT NULL, display_name,
      role DEFAULT 'user', is_active DEFAULT 1, created_at, last_login)

research_jobs(id PK, user_id FK, job_type DEFAULT 'research', topic NOT NULL,
              status DEFAULT 'pending', message, result_slug, result_data,
              created_at, completed_at)

articles(id PK, user_id FK, job_id FK, title NOT NULL, slug NOT NULL,
         file_path NOT NULL, topic, word_count, created_at)
UNIQUE(user_id, slug)
```
init_db() also promotes the oldest user (by created_at) with role='admin' to role='superadmin'.

## Roles & Auth
- superadmin: first user ever registered (auto-set by init_db)
- admin: set by superadmin
- user: default
- Session dict: {user_id, email, role, display_name}
- Passwords: werkzeug generate_password_hash / check_password_hash (PBKDF2)
- Decorators: @login_required  @admin_required  @superadmin_required

## Routes
| Method   | Path                          | Guard        | Response        |
|----------|-------------------------------|--------------|-----------------|
| GET      | /                             | —            | landing HTML    |
| GET POST | /login                        | —            | HTML            |
| GET POST | /register                     | —            | HTML            |
| GET      | /logout                       | —            | redirect        |
| GET      | /dashboard                    | login        | HTML            |
| GET      | /article/<slug>               | login        | HTML            |
| GET      | /jobs/results/<job_id>        | login        | HTML            |
| POST     | /api/research                 | login        | {job_id}        |
| POST     | /api/jobs/search              | login        | {job_id}        |
| POST     | /api/generate/prompt          | login        | {job_id}        |
| POST     | /api/generate/skill           | login        | {job_id}        |
| GET      | /api/status/<job_id>          | login        | {status,message,slug,job_type,result_data} |
| GET      | /api/articles                 | login        | JSON array      |
| GET      | /admin/                       | admin        | stats HTML      |
| GET      | /admin/users                  | admin        | user list HTML  |
| POST     | /admin/users/<id>/set-role    | superadmin   | JSON            |
| GET      | /admin/users/<id>             | admin        | user detail HTML|
| POST     | /admin/users/<id>/toggle      | admin        | JSON            |
| POST     | /admin/users/<id>/delete      | superadmin   | JSON (deletes files+DB) |
| GET      | /admin/jobs                   | admin        | jobs HTML       |
| POST     | /admin/jobs/<id>/delete       | superadmin   | JSON            |
| POST     | /admin/jobs/delete-errors     | superadmin   | JSON            |
Errors: @app.errorhandler(403) → errors/403.html | @app.errorhandler(404) → errors/404.html

## background.py
```python
import threading, models
_lock = threading.Lock()  # wrap ALL SQLite writes

def enqueue(job_type, payload, user_id):
    job_id = models.create_job(user_id, job_type, payload['topic'])
    t = threading.Thread(target=_run_safe, args=(handler, payload, user_id, job_id), daemon=True)
    t.start()
    return job_id

def _run_safe(handler, payload, user_id, job_id):
    try:
        handler(payload, user_id, job_id)
    except Exception as e:
        models.update_job(job_id, status='error', message=str(e))
```
Dispatch: research→research_agent.run_research_task | job_search→job_scraper.run_job_search_task | prompt_gen/skill_gen→prompt_generator.run_*_task
Use lazy imports inside _run_safe to avoid circular imports.

## utils.py — shared Claude subprocess (import from here in ALL pipeline files)
```python
import subprocess

def call_claude(prompt, timeout=120):
    try:
        r = subprocess.run(
            ["claude", "-p"],
            input=prompt, capture_output=True,
            text=True, timeout=timeout, encoding='utf-8'
        )
        return r.stdout.strip()
    except UnicodeDecodeError:
        r = subprocess.run(
            ["claude", "-p"],
            input=prompt.encode('utf-8'), capture_output=True,
            timeout=timeout
        )
        return r.stdout.decode('utf-8', errors='replace').strip()

# Import in research_agent.py, job_scraper.py, prompt_generator.py:
# from utils import call_claude
# NEVER use anthropic SDK. NEVER ask for API key.
```

## Research Pipeline (research_agent.py)
```
1. DDGS().text(topic, max_results=10) → [{href, title, body}]
2. Filter: skip domains ending .cn .ru .jp .kr | skip if baidu/zhidao/sina/weibo in href
3. For each result (stop after 5 valid pages fetched):
   - requests.get(url, timeout=10, headers={"User-Agent":"Mozilla/5.0 (compatible; ResearchAgent/1.0)"})
   - Skip if Content-Type not text/html or status != 200
   - Strip HTML (re.sub('<[^>]+>', ' ', html)), collapse whitespace, keep first 8000 chars
4. Build prompt:
   "Summarize this research on '{topic}'. Write 600-1000 words in plain English.
    Structure: ## Overview\n## Key Findings\n
    Content:\n{fetched_text}\n
    Output ONLY the markdown — no preamble."
5. call_claude(prompt) → summary_md
6. Build ## Sources from verified fetched URLs (NOT from Claude output)
7. slug = f"{date.today()}_{ re.sub(r'[^a-z0-9]+','-', topic.lower())[:60] }"
8. Write research/<user_id>/<slug>.md (encoding='utf-8')
9. models.create_article(...); models.update_job(job_id, status='done', result_slug=slug)
```

## Job Search Pipeline (job_scraper.py)
```
1. DDGS().text(f"{query} job opening", max_results=15) → [{href,title,body}]
2. Combine titles+snippets into text block
3. Claude prompt:
   "Extract job listings from this text as JSON array.
    Schema per item: {title, company, location, salary, url, tags:[]}
    Return valid JSON array only, no other text."
4. Parse JSON from call_claude(prompt)
5. models.update_job(job_id, status='done', result_data=json.dumps(jobs))
```

## Prompt & Skill Generator (prompt_generator.py)
```python
PROMPT_META = """You are a prompt engineer. Generate a token-efficient Claude prompt from the user's description.
Rules: ≤200 tokens, action-first, zero filler, imperative verbs.
Output format:
## Prompt
<the prompt>
## Token Estimate
~N tokens
## vs GPT-4
<2-row comparison table>
## Edge Cases
<bullet list>"""

SKILL_META = """Generate a production Claude Code skill .md file.
Rules: ≤500 tokens, include trigger phrases, numbered steps, constraints, edge cases.
Output the skill file content only (no wrapper)."""

# run_prompt_gen_task: call_claude(PROMPT_META + "\n\nUser request: " + description)
# run_skill_gen_task:  call_claude(SKILL_META  + "\n\nUser request: " + description)
# Both save result as article
```

## CSS (static/css/main.css)
```css
:root {
  --bg:#f0fdf4; --accent:#16a34a; --accent-dark:#14532d;
  --text:#1a1a1a; --border:#d1fae5; --bg-card:#ffffff; --radius:8px;
}
body.dark-mode {
  --bg:#0a1a0f; --text:#e2e8f0; --border:#1e3a2a; --bg-card:#0f2a18;
}
/* Sidebar always dark regardless of theme */
.sidebar { background:#14532d; color:#ffffff; }
/* Accent unchanged in both modes */
/* System font stack */
body { font-family: system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
/* Article cards: grid minmax(280px,1fr), hover: translateY(-2px) + shadow */
/* Status badges: pending=grey, running=blue, done=green, error=red */
/* Forms: green border glow on focus */
```

## JS (static/js/main.js)
```javascript
// Theme toggle
document.getElementById('themeToggle').addEventListener('click', () => {
  document.body.classList.toggle('dark-mode');
  localStorage.setItem('theme', document.body.classList.contains('dark-mode') ? 'dark' : 'light');
});
if (localStorage.getItem('theme') === 'dark') document.body.classList.add('dark-mode');

// Job polling (called after form submit returns job_id)
function pollJob(jobId, onDone) {
  const iv = setInterval(async () => {
    const r = await fetch(`/api/status/${jobId}`);
    const d = await r.json();
    updateSpinnerMsg(d.message);
    if (d.status === 'done' || d.status === 'error') {
      clearInterval(iv);
      onDone(d);
    }
  }, 1500);
}
// Show spinner on submit, hide on poll done
// Research done → window.location = `/article/${d.slug}`
// Job search done → window.location = `/jobs/results/${jobId}`
// Generator done → window.location = `/article/${d.slug}`
```

## Template Guide
| Template | Key content |
|----------|-------------|
| base.html | Fixed sidebar, theme toggle btn, spinner overlay, flash messages, {% block content %} |
| index.html | Landing: Login + Register buttons |
| dashboard.html | Research form, job search form, generators form, article grid cards, recent jobs mini-table |
| article.html | Render markdown→HTML (Python `markdown` lib, extensions: fenced_code, tables); linkify bare URLs with regex |
| jobs.html | Job cards: title, company, location, salary, tags, Apply link |
| admin/index.html | Stat cards: total users, active users, articles, jobs by status, recent 10 jobs |
| admin/users.html | Searchable table + toggle/set-role/delete per row |
| admin/user_detail.html | User profile + their articles list + their jobs list + file count |
| admin/jobs.html | Table filterable by status/type/user_id; delete individual or bulk delete errors |
| errors/403.html | Forbidden — link back to dashboard |
| errors/404.html | Not found — link back to dashboard |

## Admin Logic
- Delete user: shutil.rmtree(research/<user_id>/) then delete all DB records for that user
- Toggle active: set is_active=0/1; if 0, @login_required redirects to login
- set-role: superadmin only; cannot modify another superadmin's role
- Stats query: COUNT users, COUNT active, COUNT articles, GROUP BY job status, SELECT 10 recent jobs

## Startup sequence in app.py
```python
from models import init_db
from config import Config
import os

init_db()
os.makedirs(Config.RESEARCH_BASE_DIR, exist_ok=True)
# register admin blueprint: app.register_blueprint(admin_bp, url_prefix='/admin')
app.run(host='0.0.0.0', port=Config.PORT, debug=Config.DEBUG, use_reloader=False)
```

## Windows-specific
- All file reads/writes: `encoding='utf-8'`
- Kill port before start: `netstat -ano | findstr :5000` → `taskkill /PID <pid> /F`
- Never use `&&` in shell commands; use `;` or separate calls

## Verification after build
```
1. python app.py → expect "Running on http://0.0.0.0:5000"
2. GET http://localhost:5000 → landing page (HTTP 200)
3. Register first user → auto-promoted to superadmin
4. Submit a research topic → polls to done → article renders
5. GET /admin/ → stats page visible
```
