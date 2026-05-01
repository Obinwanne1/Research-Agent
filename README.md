# Research Agent

A personal AI-powered research and job search web app — built with Python, Flask, and Claude Code CLI. No Anthropic API key needed. Brand colors: **Green and White**.

---

## What It Does

- You type a research topic (e.g. "quantum computing")
- The agent searches the web, fetches the top pages, and asks Claude to write a 600–1000 word summary
- The summary is saved as a Markdown file and shown in your browser
- You can also search for remote jobs by role/location
- Everything lives behind a login — your articles are private to your account

---

## Requirements

| Tool | Why you need it |
|------|----------------|
| Python 3.10+ | Runs the web server and all scripts |
| Claude Code CLI (`claude`) | Generates the AI summaries — uses your existing Pro/Max subscription |
| pip | Installs Python packages |
| A modern browser | Chrome, Firefox, Edge all work |

> **No Anthropic API key required.** The app calls Claude through the `claude` command-line tool, which you log into once with your Claude account.

---

## Step-by-Step Setup (Beginner Guide)

### Step 1 — Install Python

1. Go to [python.org/downloads](https://python.org/downloads)
2. Download the latest Python 3.x installer for Windows
3. Run the installer — **tick the box that says "Add Python to PATH"**
4. Open a terminal (search `cmd` or `PowerShell` in the Start menu) and verify:
   ```
   python --version
   ```
   You should see something like `Python 3.12.0`

---

### Step 2 — Install Claude Code CLI

1. Make sure you have [Node.js](https://nodejs.org) installed (download the LTS version)
2. In your terminal, run:
   ```
   npm install -g @anthropic-ai/claude-code
   ```
3. Log in with your Claude account:
   ```
   claude
   ```
   Follow the browser prompt to authenticate. You only do this once.

---

### Step 3 — Download the Project

If you received this as a folder, copy it to your Desktop so the path is:
```
C:\Users\YOUR_NAME\Desktop\ResearchAgent\
```

If you're cloning from Git:
```
git clone <repo-url> C:\Users\YOUR_NAME\Desktop\ResearchAgent
```

---

### Step 4 — Install Python Packages

Open a terminal, navigate to the project folder, then install dependencies:

```powershell
cd C:\Users\YOUR_NAME\Desktop\ResearchAgent
pip install -r requirements.txt
```

This installs:
- `flask` — the web server framework
- `ddgs` — DuckDuckGo search (no API key)
- `markdown` — converts `.md` files to HTML
- `requests` — fetches web pages
- `werkzeug` — password hashing
- `waitress` — production-ready server

---

### Step 5 — Start the App

In the same terminal:

```powershell
python app.py
```

You should see output like:
```
 * Running on http://0.0.0.0:5000
```

---

### Step 6 — Open in Browser

Go to: **http://localhost:5000**

You'll see the home page. Click **Register** to create your first account.

> The first account registered automatically becomes the **admin**.

---

### Step 7 — Run Your First Research

1. Log in and go to the Dashboard
2. Type a topic in the research box (e.g. `history of the internet`)
3. Click **Research**
4. A green spinner appears while the agent works (30–90 seconds)
5. When done, your article appears in the dashboard — click to read it

---

### Step 8 — Search for Jobs

1. On the Dashboard, find the job search box
2. Type a role and location (e.g. `Python developer remote`)
3. Click **Search Jobs**
4. Results appear as a table with apply links

---

## How It Was Built — The Architecture

Here is what each file does and how the pieces connect:

```
ResearchAgent/
│
├── app.py              ← The web server. Handles all URL routes (pages + API)
├── auth.py             ← Login, register, logout, session management
├── admin.py            ← Admin panel (manage users, view all articles)
├── background.py       ← Runs research jobs in background threads
├── config.py           ← App settings (port, secret key, file paths)
├── models.py           ← Database layer (SQLite — users, jobs, articles)
├── research_agent.py   ← The core research pipeline (see below)
├── job_scraper.py      ← Fetches jobs from the RemoteOK API
│
├── static/
│   ├── css/main.css    ← All styles (green/white brand, dark mode)
│   └── js/main.js      ← Theme toggle, spinner, status polling
│
└── templates/          ← HTML pages (Jinja2 templates)
    ├── base.html       ← Shared layout with sidebar
    ├── index.html      ← Landing page
    ├── dashboard.html  ← Main user page
    ├── article.html    ← Single article view
    ├── jobs.html       ← Job results
    ├── auth/           ← Login + register pages
    └── errors/         ← 403, 404 pages
```

---

## The Research Pipeline (How AI Summaries Work)

Every time you submit a research topic, this 4-step process runs in the background:

```
Step 1: SEARCH
  → Uses DuckDuckGo (no API key) to find top 10 web results for your topic

Step 2: FETCH
  → Downloads the content of the top 2 English-language pages
  → Skips blocked domains (.cn, .ru, .jp, baidu, etc.)
  → Strips HTML tags, keeps plain text (first 8000 characters per page)

Step 3: SUMMARIZE
  → Builds a prompt combining your topic + the fetched page content
  → Sends it to Claude via the CLI command: claude -p
  → Claude writes a 600–1000 word summary with Overview, Key Findings, Sources

Step 4: SAVE
  → Saves the summary as a .md file: research/<user_id>/YYYY-MM-DD_topic.md
  → Stores the article record in SQLite database
  → Frontend gets notified via polling → spinner disappears → article appears
```

**Why `claude -p` instead of the API?**
The app uses the Claude Code CLI as a subprocess. This means it reuses your existing Claude login — no API key or extra billing. The Python code looks like this:

```python
result = subprocess.run(
    ["claude", "-p"],
    input=prompt,        # sends the prompt via standard input
    capture_output=True,
    text=True,
    timeout=120
)
summary = result.stdout.strip()
```

---

## The Database

The app uses **SQLite** — a simple file-based database (`research_agent.db`). No setup needed, it creates itself on first run.

Three tables:
- `users` — email, hashed password, role (admin/user), created date
- `jobs` — tracks each research/job-search request (status: pending → running → done/error)
- `articles` — links to saved markdown files, stores title, topic, word count

---

## Authentication Flow

1. User registers with email + password
2. Password is hashed with `werkzeug` (never stored as plain text)
3. First user becomes admin automatically
4. Login creates a Flask session (stored in a browser cookie)
5. All dashboard/article routes are protected — redirect to login if not authenticated

---

## Dark Mode

Click the **☾ Dark** button in the sidebar. The choice is saved to `localStorage` so it persists across sessions. CSS variables switch between the green/white light theme and a dark green theme.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `claude: command not found` | Re-run `npm install -g @anthropic-ai/claude-code` and make sure Node is on your PATH |
| Port 5000 already in use | Run `netstat -ano \| findstr :5000` to find the PID, then `taskkill /PID <pid> /F` |
| Research spins forever | Claude CLI may have timed out — check your internet connection and try again |
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` again |
| Can't log in | Make sure you registered first — there's no default account |

---

## Customization

- **Brand colors**: Edit `static/css/main.css` — change `--accent` and `--bg-sidebar` variables at the top
- **Summary length**: Edit the prompt in `research_agent.py` around line 114
- **Max pages fetched**: Change `max_pages=2` in `research_agent.py`
- **Blocked domains**: Add to `BLOCKED_DOMAINS` list in `research_agent.py`

---

## Security Notes

- Never commit `research_agent.db` to a public repo (contains user data)
- Change `Config.SECRET_KEY` in `config.py` before deploying publicly
- The app is designed for personal/local use — add HTTPS if exposing to the internet

---

*Built with Python, Flask, Claude Code CLI, DuckDuckGo Search, and SQLite.*
