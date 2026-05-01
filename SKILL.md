---
name: research-agent
description: >
  Deploy a full Research Agent web app at C:\Users\[USERNAME]\Desktop\ResearchAgent.
  Flask app with green/white brand, user auth, AI research summaries, and job search.
  Uses Claude Code CLI (claude -p) — NO Anthropic API key required.
---

## Brand Colors
- Primary: Green `#16a34a`
- Background: White `#ffffff`
- Sidebar: Dark Green `#14532d`

## Folder Structure
```
ResearchAgent/
├── CLAUDE.md          ← Claude behavior config
├── SKILL.md           ← This file (agent blueprint)
├── README.md          ← Beginner setup guide
├── app.py             ← Flask routes
├── auth.py            ← Login/register/session logic
├── admin.py           ← Admin blueprint
├── background.py      ← Job queue (threading)
├── config.py          ← App settings
├── models.py          ← SQLite DB layer
├── research_agent.py  ← Research pipeline (search → fetch → Claude → save)
├── job_scraper.py     ← RemoteOK job search
├── requirements.txt   ← Python dependencies
├── research/          ← Saved markdown articles (per user)
├── static/
│   ├── css/main.css   ← Green/white brand styles
│   └── js/main.js     ← Theme toggle, spinner, polling
└── templates/
    ├── base.html
    ├── index.html
    ├── dashboard.html
    ├── article.html
    ├── jobs.html
    ├── auth/
    │   ├── login.html
    │   └── register.html
    ├── admin/
    └── errors/
```

## Install Dependencies
```
pip install flask markdown duckduckgo-search requests
```

## Key Architecture (DO NOT deviate)

### Research Pipeline (research_agent.py)
1. **Search** — DuckDuckGo via `ddgs` library
2. **Fetch** — Top 2 English pages (skip `.cn .ru .jp .kr`, baidu, sina, weibo)
3. **Summarize** — Claude CLI via subprocess STDIN, NOT the Anthropic SDK:
   ```python
   result = subprocess.run(
       ["claude", "-p"],
       input=prompt,
       capture_output=True, text=True, timeout=120
   )
   ```
4. **Save** — Markdown to `research/<user_id>/YYYY-MM-DD_slug.md`

### Job Search Pipeline (job_scraper.py)
- Fetches from RemoteOK API (free, no key needed)
- Returns JSON list of job objects

### Web Server (app.py)
- Flask on port 5000
- Background threading for long-running research jobs
- Frontend polls `/api/status/<job_id>` every 1.5s
- Spinner overlay shown during research

### Auth (auth.py)
- Sessions via Flask `session`
- First registered user auto-becomes admin
- Passwords hashed with `werkzeug.security`

### Database (models.py)
- SQLite (`research_agent.db`)
- Tables: `users`, `jobs`, `articles`

## DO NOT
- Use `anthropic.Anthropic()` anywhere
- Require an API key — Claude CLI handles auth via the user's Pro/Max session
- Store plaintext passwords

## Start Server
```powershell
# Kill existing process on port 5000 first
$pid = (netstat -ano | Select-String ":5000").ToString().Trim().Split()[-1]
Stop-Process -Id $pid -Force

# Start
python app.py
```

## Health Check
```
curl http://localhost:5000
```
Expect HTTP 200.
