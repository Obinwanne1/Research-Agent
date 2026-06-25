import sqlite3
import threading
import re
import json
import hashlib
from datetime import datetime
from config import Config
import secrets

_db_lock = threading.Lock()


def get_conn():
    conn = sqlite3.connect(Config.DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _db_lock:
        conn = get_conn()
        c = conn.cursor()
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT    NOT NULL UNIQUE,
                password_hash TEXT    NOT NULL,
                display_name  TEXT,
                role          TEXT    NOT NULL DEFAULT 'user',
                is_active     INTEGER NOT NULL DEFAULT 1,
                created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
                last_login    TEXT
            );

            CREATE TABLE IF NOT EXISTS research_jobs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                job_type     TEXT    NOT NULL DEFAULT 'research',
                topic        TEXT    NOT NULL,
                status       TEXT    NOT NULL DEFAULT 'pending',
                message      TEXT,
                result_slug  TEXT,
                result_data  TEXT,
                created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
                completed_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS articles (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                job_id     INTEGER,
                title      TEXT    NOT NULL,
                slug       TEXT    NOT NULL,
                file_path  TEXT    NOT NULL,
                topic      TEXT,
                word_count INTEGER,
                created_at TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, slug),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (job_id)  REFERENCES research_jobs(id)
            );
        """)
        conn.commit()

        # Migrate: add must_change_password column if missing
        try:
            c.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        except Exception:
            pass  # Column already exists

        # Migrate: add payload column for full job recovery on restart
        try:
            c.execute("ALTER TABLE research_jobs ADD COLUMN payload TEXT")
            conn.commit()
        except Exception:
            pass  # Column already exists

        # Migrate: add public sharing columns to articles
        for col_sql in [
            "ALTER TABLE articles ADD COLUMN is_public INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE articles ADD COLUMN share_token TEXT",
        ]:
            try:
                c.execute(col_sql)
                conn.commit()
            except Exception:
                pass

        # FTS5 full-text search index on articles (title + topic)
        c.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
                title, topic,
                tokenize='unicode61'
            )
        """)
        conn.commit()

        # Password reset tokens table
        c.execute("""
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                token      TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                used       INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        conn.commit()

        # API keys table
        c.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                name         TEXT    NOT NULL DEFAULT 'My Key',
                key_hash     TEXT    NOT NULL UNIQUE,
                is_active    INTEGER NOT NULL DEFAULT 1,
                created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
                last_used_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        conn.commit()

        # Populate FTS for any existing articles not yet indexed
        indexed_ids = {r[0] for r in conn.execute("SELECT rowid FROM articles_fts").fetchall()}
        for art in conn.execute("SELECT id, title, topic FROM articles").fetchall():
            if art[0] not in indexed_ids:
                conn.execute(
                    "INSERT INTO articles_fts(rowid, title, topic) VALUES (?, ?, ?)",
                    (art[0], art[1] or '', art[2] or '')
                )
        conn.commit()

        # Search result cache (DuckDuckGo, 6h TTL)
        c.execute("""
            CREATE TABLE IF NOT EXISTS search_cache (
                topic_key   TEXT PRIMARY KEY,
                results_json TEXT NOT NULL,
                cached_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.commit()

        # Schedules table for recurring research
        c.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                topic       TEXT    NOT NULL,
                frequency   TEXT    NOT NULL DEFAULT 'daily',
                is_active   INTEGER NOT NULL DEFAULT 1,
                next_run_at TEXT    NOT NULL,
                last_run_at TEXT,
                last_job_id INTEGER,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        conn.commit()

        # Migrate: promote oldest admin to superadmin if no superadmin exists
        has_superadmin = conn.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'superadmin'"
        ).fetchone()[0]
        if not has_superadmin:
            conn.execute("""
                UPDATE users SET role = 'superadmin'
                WHERE id = (SELECT id FROM users WHERE role = 'admin' ORDER BY created_at ASC LIMIT 1)
            """)
            conn.commit()

        conn.close()


# ── User helpers ──────────────────────────────────────────────────────────────

def count_users():
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
    conn.close()
    return row[0]


def get_user_by_email(email):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_user(email, password_hash, display_name, role="user"):
    with _db_lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO users (email, password_hash, display_name, role, must_change_password) VALUES (?, ?, ?, ?, 1)",
            (email, password_hash, display_name, role)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()
        return dict(row)


def update_user_password(user_id, password_hash):
    with _db_lock:
        conn = get_conn()
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
            (password_hash, user_id)
        )
        conn.commit()
        conn.close()


def create_reset_token(user_id, token, expires_at):
    with _db_lock:
        conn = get_conn()
        conn.execute(
            "UPDATE password_reset_tokens SET used = 1 WHERE user_id = ? AND used = 0",
            (user_id,)
        )
        conn.execute(
            "INSERT INTO password_reset_tokens (user_id, token, expires_at) VALUES (?, ?, ?)",
            (user_id, token, expires_at)
        )
        conn.commit()
        conn.close()


def get_reset_token(token):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM password_reset_tokens WHERE token = ? AND used = 0",
        (token,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def use_reset_token(token_id):
    with _db_lock:
        conn = get_conn()
        conn.execute("UPDATE password_reset_tokens SET used = 1 WHERE id = ?", (token_id,))
        conn.commit()
        conn.close()


def update_last_login(user_id):
    with _db_lock:
        conn = get_conn()
        conn.execute(
            "UPDATE users SET last_login = datetime('now') WHERE id = ?", (user_id,)
        )
        conn.commit()
        conn.close()


def set_user_role(user_id, role):
    with _db_lock:
        conn = get_conn()
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        conn.commit()
        conn.close()


def toggle_user_active(user_id):
    with _db_lock:
        conn = get_conn()
        conn.execute(
            "UPDATE users SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END WHERE id = ?",
            (user_id,)
        )
        conn.commit()
        conn.close()


def get_all_users(email_filter=None):
    conn = get_conn()
    where = "WHERE u.email LIKE ?" if email_filter else ""
    params = [f"%{email_filter}%"] if email_filter else []
    rows = conn.execute(f"""
        SELECT u.id, u.email, u.display_name, u.role, u.is_active,
               u.last_login, u.created_at,
               COUNT(DISTINCT a.id) as article_count,
               COUNT(DISTINCT rj.id) as job_count
        FROM users u
        LEFT JOIN articles a ON a.user_id = u.id
        LEFT JOIN research_jobs rj ON rj.user_id = u.id
        {where}
        GROUP BY u.id
        ORDER BY u.created_at DESC
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_user(user_id):
    with _db_lock:
        conn = get_conn()
        conn.execute("DELETE FROM articles WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM research_jobs WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()


# ── Job helpers ───────────────────────────────────────────────────────────────

def create_job(user_id, job_type, topic, payload=None):
    with _db_lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO research_jobs (user_id, job_type, topic, status, message, payload) VALUES (?, ?, ?, 'pending', 'Starting...', ?)",
            (user_id, job_type, topic, json.dumps(payload) if payload else None)
        )
        conn.commit()
        row = conn.execute("SELECT last_insert_rowid()").fetchone()
        job_id = row[0]
        conn.close()
        return job_id


def reset_stuck_jobs():
    """On restart: running jobs were killed mid-flight — reset to pending."""
    with _db_lock:
        conn = get_conn()
        conn.execute(
            "UPDATE research_jobs SET status = 'pending', message = 'Requeued after restart' WHERE status = 'running'"
        )
        conn.commit()
        conn.close()


def get_pending_jobs():
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM research_jobs WHERE status = 'pending' ORDER BY created_at ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_job(job_id, status=None, message=None, result_slug=None, result_data=None):
    with _db_lock:
        conn = get_conn()
        updates = []
        params = []
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if message is not None:
            updates.append("message = ?")
            params.append(message)
        if result_slug is not None:
            updates.append("result_slug = ?")
            params.append(result_slug)
        if result_data is not None:
            updates.append("result_data = ?")
            params.append(json.dumps(result_data) if not isinstance(result_data, str) else result_data)
        if status in ("done", "error"):
            updates.append("completed_at = datetime('now')")
        if updates:
            sql = f"UPDATE research_jobs SET {', '.join(updates)} WHERE id = ?"
            params.append(job_id)
            conn.execute(sql, params)
            conn.commit()
        conn.close()


def get_job(job_id, user_id=None):
    conn = get_conn()
    if user_id is not None:
        row = conn.execute(
            "SELECT * FROM research_jobs WHERE id = ? AND user_id = ?", (job_id, user_id)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM research_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_jobs(status_filter=None, type_filter=None, user_filter=None, limit=100):
    conn = get_conn()
    conditions = []
    params = []
    if status_filter:
        conditions.append("rj.status = ?")
        params.append(status_filter)
    if type_filter:
        conditions.append("rj.job_type = ?")
        params.append(type_filter)
    if user_filter:
        conditions.append("u.email LIKE ?")
        params.append(f"%{user_filter}%")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = conn.execute(f"""
        SELECT rj.*, u.email
        FROM research_jobs rj
        JOIN users u ON rj.user_id = u.id
        {where}
        ORDER BY rj.created_at DESC
        LIMIT ?
    """, params + [limit]).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_job(job_id):
    with _db_lock:
        conn = get_conn()
        conn.execute("DELETE FROM research_jobs WHERE id = ? AND status != 'done'", (job_id,))
        conn.commit()
        conn.close()


def delete_jobs_by_status(status):
    with _db_lock:
        conn = get_conn()
        conn.execute("DELETE FROM research_jobs WHERE status = ?", (status,))
        conn.commit()
        conn.close()


def get_jobs_for_user(user_id, limit=50):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM research_jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Article helpers ───────────────────────────────────────────────────────────

def create_article(user_id, job_id, title, slug, file_path, topic, word_count):
    with _db_lock:
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO articles (user_id, job_id, title, slug, file_path, topic, word_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, job_id, title, slug, file_path, topic, word_count)
            )
            conn.commit()
            article_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO articles_fts(rowid, title, topic) VALUES (?, ?, ?)",
                (article_id, title or '', topic or '')
            )
            conn.commit()
        except sqlite3.IntegrityError:
            pass  # slug collision — article already exists
        conn.close()


def get_article(slug, user_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM articles WHERE slug = ? AND user_id = ?", (slug, user_id)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_articles_for_user(user_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM articles WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_articles_for_user_detail(user_id):
    return get_articles_for_user(user_id)


# ── API key helpers ───────────────────────────────────────────────────────────

def _hash_key(raw_key):
    return hashlib.sha256(raw_key.encode()).hexdigest()


def create_api_key(user_id, name, raw_key):
    with _db_lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO api_keys (user_id, name, key_hash) VALUES (?, ?, ?)",
            (user_id, name or "My Key", _hash_key(raw_key))
        )
        conn.commit()
        conn.close()


def get_api_keys_for_user(user_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name, is_active, created_at, last_used_at FROM api_keys WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def verify_api_key(raw_key):
    """Returns (user_dict, key_id) or None."""
    key_hash = _hash_key(raw_key)
    conn = get_conn()
    row = conn.execute("""
        SELECT k.id as key_id,
               u.id, u.email, u.role, u.is_active, u.display_name, u.must_change_password
        FROM api_keys k
        JOIN users u ON u.id = k.user_id
        WHERE k.key_hash = ? AND k.is_active = 1
    """, (key_hash,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    key_id = d.pop("key_id")
    return d, key_id


def revoke_api_key(key_id, user_id):
    with _db_lock:
        conn = get_conn()
        conn.execute(
            "UPDATE api_keys SET is_active = 0 WHERE id = ? AND user_id = ?",
            (key_id, user_id)
        )
        conn.commit()
        conn.close()


def touch_api_key(key_id):
    with _db_lock:
        conn = get_conn()
        conn.execute(
            "UPDATE api_keys SET last_used_at = datetime('now') WHERE id = ?",
            (key_id,)
        )
        conn.commit()
        conn.close()


def set_article_share(slug, user_id, make_public):
    """Enable or disable public sharing. Generates a token on first enable."""
    with _db_lock:
        conn = get_conn()
        if make_public:
            existing = conn.execute(
                "SELECT share_token FROM articles WHERE slug = ? AND user_id = ?", (slug, user_id)
            ).fetchone()
            token = existing["share_token"] if (existing and existing["share_token"]) else secrets.token_urlsafe(24)
            conn.execute(
                "UPDATE articles SET is_public = 1, share_token = ? WHERE slug = ? AND user_id = ?",
                (token, slug, user_id)
            )
        else:
            conn.execute(
                "UPDATE articles SET is_public = 0 WHERE slug = ? AND user_id = ?",
                (slug, user_id)
            )
        conn.commit()
        row = conn.execute(
            "SELECT share_token FROM articles WHERE slug = ? AND user_id = ?", (slug, user_id)
        ).fetchone()
        conn.close()
        return row["share_token"] if row else None


def get_article_by_token(token):
    """Return article dict if token matches and article is public."""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM articles WHERE share_token = ? AND is_public = 1", (token,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def search_articles(user_id, query, limit=50):
    words = re.sub(r'[^\w\s]', ' ', query, flags=re.UNICODE).split()
    if not words:
        return get_articles_for_user(user_id)
    fts_query = ' '.join(words)
    conn = get_conn()
    rows = conn.execute("""
        SELECT a.*
        FROM articles_fts f
        JOIN articles a ON a.id = f.rowid
        WHERE articles_fts MATCH ?
          AND a.user_id = ?
        ORDER BY f.rank
        LIMIT ?
    """, (fts_query, user_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Search cache helpers ──────────────────────────────────────────────────────

def get_search_cache(topic_key, ttl_hours=6):
    """Return cached results list or None if missing/expired."""
    conn = get_conn()
    row = conn.execute(
        "SELECT results_json, cached_at FROM search_cache WHERE topic_key = ?",
        (topic_key,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    cached_at = datetime.fromisoformat(row["cached_at"])
    if (datetime.utcnow() - cached_at).total_seconds() > ttl_hours * 3600:
        return None
    try:
        return json.loads(row["results_json"])
    except Exception:
        return None


def set_search_cache(topic_key, results):
    with _db_lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO search_cache (topic_key, results_json, cached_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(topic_key) DO UPDATE SET results_json=excluded.results_json, cached_at=excluded.cached_at",
            (topic_key, json.dumps(results))
        )
        conn.commit()
        conn.close()


# ── Schedule helpers ─────────────────────────────────────────────────────────

def create_schedule(user_id, topic, frequency, next_run_at):
    with _db_lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO schedules (user_id, topic, frequency, next_run_at) VALUES (?, ?, ?, ?)",
            (user_id, topic, frequency, next_run_at)
        )
        conn.commit()
        row = conn.execute("SELECT last_insert_rowid()").fetchone()
        schedule_id = row[0]
        conn.close()
        return schedule_id


def get_schedules_for_user(user_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM schedules WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_due_schedules():
    """Return all active schedules whose next_run_at <= now (UTC)."""
    conn = get_conn()
    now = datetime.utcnow().isoformat()
    rows = conn.execute(
        "SELECT * FROM schedules WHERE is_active = 1 AND next_run_at <= ?",
        (now,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_schedule_after_run(schedule_id, next_run_at, job_id):
    with _db_lock:
        conn = get_conn()
        conn.execute(
            "UPDATE schedules SET last_run_at = datetime('now'), next_run_at = ?, last_job_id = ? WHERE id = ?",
            (next_run_at, job_id, schedule_id)
        )
        conn.commit()
        conn.close()


def toggle_schedule(schedule_id, user_id):
    with _db_lock:
        conn = get_conn()
        conn.execute(
            "UPDATE schedules SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END WHERE id = ? AND user_id = ?",
            (schedule_id, user_id)
        )
        conn.commit()
        conn.close()


def delete_schedule(schedule_id, user_id):
    with _db_lock:
        conn = get_conn()
        conn.execute(
            "DELETE FROM schedules WHERE id = ? AND user_id = ?",
            (schedule_id, user_id)
        )
        conn.commit()
        conn.close()


# ── Admin stats ───────────────────────────────────────────────────────────────

def get_admin_stats():
    conn = get_conn()
    user_row = conn.execute(
        "SELECT COUNT(*) as total, SUM(is_active) as active FROM users"
    ).fetchone()
    job_rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM research_jobs GROUP BY status"
    ).fetchall()
    article_count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    recent_jobs = conn.execute("""
        SELECT rj.id, u.email, rj.topic, rj.status, rj.created_at, rj.job_type
        FROM research_jobs rj JOIN users u ON rj.user_id = u.id
        ORDER BY rj.created_at DESC LIMIT 10
    """).fetchall()
    conn.close()

    job_stats = {r["status"]: r["cnt"] for r in job_rows}
    return {
        "total_users": user_row["total"],
        "active_users": user_row["active"] or 0,
        "article_count": article_count,
        "job_stats": job_stats,
        "recent_jobs": [dict(r) for r in recent_jobs],
    }
