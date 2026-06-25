# Research Agent

An AI-powered research and automation web app — built with Python, Flask, and Claude Code CLI. No Anthropic API key needed for core operation. Brand colors: **Green (#16a34a) and White**.

---

## What It Does

- Type a research topic → the agent searches the web, fetches top pages, asks Claude to write a 600–1000 word summary
- Summaries saved as Markdown files, shown in your browser as formatted articles
- Search for remote jobs by role/location — structured cards with Apply links
- Generate Claude/GPT prompts and Claude Code skill files on demand
- Schedule research to run daily or weekly automatically
- Compare up to 4 articles side-by-side with optional Claude synthesis
- Share any article publicly via a unique link (no login required for readers)
- Automate everything via REST API keys — works with n8n, Zapier, scripts

Everything lives behind a login — your articles are private to your account unless you choose to share them.

---

## Requirements

| Tool | Why you need it |
|------|----------------|
| Python 3.10+ | Runs the web server and all scripts |
| Claude Code CLI (`claude`) | Generates AI summaries — uses your existing Pro/Max subscription |
| pip | Installs Python packages |
| A modern browser | Chrome, Firefox, Edge all work |

> **No Anthropic API key required.** The app calls Claude through the `claude` command-line tool, which you log into once with your Claude account. Optionally, set `ANTHROPIC_API_KEY` in `.env` as a fallback when the CLI is unavailable.

---

## Step-by-Step Setup

### Step 1 — Install Python

1. Go to [python.org/downloads](https://python.org/downloads)
2. Download the latest Python 3.x installer for Windows
3. Run the installer — **tick "Add Python to PATH"**
4. Verify in a terminal:
   ```
   python --version
   ```

### Step 2 — Install Claude Code CLI

1. Install [Node.js](https://nodejs.org) (LTS version)
2. In your terminal:
   ```
   npm install -g @anthropic-ai/claude-code
   ```
3. Log in:
   ```
   claude
   ```
   Follow the browser prompt. You only do this once.

### Step 3 — Download the Project

Copy the project folder to your Desktop so the path is:
```
C:\Users\YOUR_NAME\Desktop\ResearchAgent\
```

Or clone from Git:
```
git clone <repo-url> C:\Users\YOUR_NAME\Desktop\ResearchAgent
```

### Step 4 — Install Python Packages

```powershell
cd C:\Users\YOUR_NAME\Desktop\ResearchAgent
pip install -r requirements.txt
```

Installs: `flask`, `ddgs`, `markdown`, `requests`, `werkzeug`, `waitress`, `anthropic`, `python-dotenv`

### Step 5 — Start the App

```powershell
python app.py
```

Output: `Serving on http://0.0.0.0:5001`

### Step 6 — Open in Browser

Go to: **http://localhost:5001**

Click **Register** to create your first account.

> The first account registered automatically becomes the **superadmin**.

---

## Core Features

### Research
1. Log in → Dashboard
2. Type a topic (e.g. `quantum computing breakthroughs 2025`)
3. Click **Research**
4. Spinner appears while the agent works (30–90 seconds)
5. Article appears in your library — click to read

### Job Search
1. Dashboard → Job Search box
2. Type role + location (e.g. `Python developer remote`)
3. Click **Search Jobs**
4. Styled cards appear with Apply buttons

### Prompt & Skill Generator
- **Prompt Generator** → creates an optimised AI prompt with token estimate + GPT-4 comparison table
- **Skill Generator** → creates a Claude Code `.md` skill file with triggers, steps, constraints

---

## Scheduled Research

Set research topics to run automatically on a daily or weekly schedule.

1. Sidebar → **Schedules**
2. Enter a topic + select frequency
3. Click **Create Schedule**

The scheduler checks every 60 seconds for due tasks and fires them automatically. Your article library fills up without you lifting a finger.

---

## Article Comparison

Compare up to 4 articles side by side.

1. Dashboard → tick the checkboxes on article cards (up to 4)
2. Floating bar appears at the bottom → click **Compare**
3. Columns render side-by-side, each independently scrollable
4. Claude synthesises a combined summary below the columns (if CLI is reachable)

---

## Public Article Sharing

Share any research article with anyone — no login required for the reader.

1. Open an article
2. Click **Make Public**
3. A unique link appears — click **Copy Link**
4. Share the `/share/<token>` URL — it works without login forever (until you make it private again)

---

## REST API

Automate research from scripts, n8n workflows, or any HTTP client.

### Get an API Key
1. Sidebar → **API Keys**
2. Enter a name → **Generate Key**
3. Copy the key immediately — it's shown only once

### Use the API
```bash
# Start a research job
curl -X POST http://localhost:5001/api/research \
  -H "X-API-Key: ra_..." \
  -H "Content-Type: application/json" \
  -d '{"topic": "latest AI regulation news"}'

# Poll for status
curl -H "X-API-Key: ra_..." http://localhost:5001/api/status/<job_id>
# → {"status":"done","result_slug":"2026-06-25_ai-regulation"}

# Search jobs
curl -X POST http://localhost:5001/api/jobs/search \
  -H "X-API-Key: ra_..." \
  -H "Content-Type: application/json" \
  -d '{"query": "Python developer remote"}'
```

Also accepts `Authorization: Bearer ra_...` header.

**Rate limit:** 20 calls/user/hour (configurable via `API_RATE_LIMIT` in `.env`). Returns `429` + `Retry-After` header when exceeded.

---

## How It Was Built — Architecture

```
ResearchAgent/
│
├── app.py              ← Web server. All URL routes (pages + API)
├── auth.py             ← Auth decorators, CSRF, rate limiting, session
├── admin.py            ← Admin blueprint (/admin/*)
├── background.py       ← ThreadPoolExecutor job queue + scheduler daemon
├── config.py           ← Settings class (reads from .env)
├── models.py           ← SQLite layer (all tables + helper functions)
├── research_agent.py   ← Research pipeline (search → fetch → Claude → save)
├── job_scraper.py      ← Job search pipeline
├── prompt_generator.py ← Prompt & skill generators
├── utils.py            ← Claude CLI subprocess + SDK fallback
│
├── static/
│   ├── css/main.css    ← All styles (green/white brand, dark mode)
│   └── js/main.js      ← Theme toggle, spinner, polling, compare checkboxes
│
└── templates/
    ├── base.html           ← Sidebar layout, dark mode toggle
    ├── index.html          ← Landing page
    ├── dashboard.html      ← Main user page + article grid + compare
    ├── article.html        ← Single article view + share + download
    ├── compare.html        ← Side-by-side comparison + synthesis
    ├── share.html          ← Public article view (no auth required)
    ├── jobs.html           ← Job results
    ├── auth/               ← login, register, change_password, forgot, reset
    ├── settings/           ← api_keys.html
    ├── schedules/          ← index.html
    ├── admin/              ← index, users, user_detail, jobs
    └── errors/             ← 403, 404
```

---

## The Research Pipeline

```
Step 1: SEARCH
  → DuckDuckGo search (no API key, no account)
  → Check search cache first — if same topic searched in last 6h, skip DDGS entirely

Step 2: FETCH
  → Download top 5 English-language pages
  → Skip blocked domains (.cn, .ru, .jp, baidu, etc.)
  → Strip HTML, keep first 8000 characters of plain text

Step 3: SUMMARISE
  → Build prompt: topic + fetched content
  → Call Claude via CLI: claude -p
  → Claude writes 600–1000 word summary (## Overview, ## Key Findings)
  → If CLI fails: retry via Anthropic SDK (if ANTHROPIC_API_KEY is set in .env)

Step 4: SAVE
  → Append ## Sources from real HTTP-200 URLs (no hallucinated links)
  → Save as research/<user_id>/YYYY-MM-DD_topic.md
  → Store article in SQLite (indexed in FTS5 for search)
  → Frontend polling detects completion → spinner disappears → article appears
```

---

## Database Schema

SQLite file: `research_agent.db` (auto-created on first run)

| Table | Purpose |
|-------|---------|
| `users` | Accounts: email, hashed password, role, active flag |
| `research_jobs` | Every task: status (pending→running→done/error), payload |
| `articles` | Article index: slug, file path, is_public, share_token |
| `articles_fts` | FTS5 full-text index on title + topic |
| `api_keys` | API key hashes (SHA-256), per-user |
| `password_reset_tokens` | One-time reset tokens (24h expiry) |
| `search_cache` | DuckDuckGo result cache (6h TTL) |
| `schedules` | Recurring research: topic, frequency, next_run_at |

---

## Authentication & Security

- Passwords hashed with `werkzeug` (never stored as plain text)
- CSRF token on every state-changing form
- Login rate limited: 5 failed attempts → 15-minute lockout
- Session timeout: 30 minutes of inactivity (configurable)
- Security headers on every response: CSP, X-Frame-Options, X-Content-Type-Options
- API keys stored as SHA-256 hashes — raw key shown once at creation
- First registered user becomes superadmin automatically
- Admin-created accounts require password change on first login

---

## User Roles

| Role | Access |
|------|--------|
| `user` | Own articles, jobs, schedules, API keys |
| `admin` | + Admin panel: view all users + jobs |
| `superadmin` | + Promote/demote users, delete users |

---

## Dark Mode

Click **☾ Dark** in the sidebar footer. Choice persists in `localStorage` across sessions.

---

## Configuration (.env file)

Create a `.env` file in the project root to override defaults:

```env
SECRET_KEY=your-secret-key-here
PORT=5001
DEBUG=false
MAX_CONCURRENT_JOBS=10
CLAUDE_TIMEOUT=120
SESSION_LIFETIME_MINUTES=30
API_RATE_LIMIT=20
SEARCH_CACHE_TTL_HOURS=6
ANTHROPIC_API_KEY=sk-ant-...   # optional — enables SDK fallback
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `claude: command not found` | Re-run `npm install -g @anthropic-ai/claude-code`; ensure Node is on PATH |
| Port 5001 already in use | `netstat -ano \| findstr :5001` → `taskkill /PID <pid> /F` |
| Research spins forever | Claude CLI timed out — check internet connection, try again |
| `ModuleNotFoundError` | `pip install -r requirements.txt` |
| Can't log in | Register first — no default account exists |
| Schedules not firing | Check server is running; scheduler wakes every 60 seconds |
| Share link returns 404 | Article may have been made private again; re-enable sharing |
| API returns 429 | Rate limit hit; wait for `Retry-After` seconds (default: 1 hour window) |

---

## Customisation

- **Brand colours**: Edit `--accent` and `--accent-dark` in `static/css/main.css`
- **Summary length**: Edit the prompt in `research_agent.py`
- **Max pages fetched**: Change `max_pages` in `research_agent.py`
- **Rate limit**: `API_RATE_LIMIT=50` in `.env`
- **Cache TTL**: `SEARCH_CACHE_TTL_HOURS=12` in `.env`

---

## Security Notes

- Never commit `research_agent.db` to a public repo (contains user data)
- Change `SECRET_KEY` in `.env` before any public deployment
- Designed for personal/team use — add HTTPS reverse proxy (nginx/caddy) before exposing to the internet
- Set `DEBUG=false` in production

---

*Built with Python, Flask, Claude Code CLI, DuckDuckGo Search, and SQLite.*
