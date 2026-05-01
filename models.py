import sqlite3
import threading
import json
from datetime import datetime
from config import Config

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
            "INSERT INTO users (email, password_hash, display_name, role) VALUES (?, ?, ?, ?)",
            (email, password_hash, display_name, role)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()
        return dict(row)


def update_last_login(user_id):
    with _db_lock:
        conn = get_conn()
        conn.execute(
            "UPDATE users SET last_login = datetime('now') WHERE id = ?", (user_id,)
        )
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


def get_all_users():
    conn = get_conn()
    rows = conn.execute("""
        SELECT u.id, u.email, u.display_name, u.role, u.is_active,
               u.last_login, u.created_at,
               COUNT(DISTINCT a.id) as article_count,
               COUNT(DISTINCT rj.id) as job_count
        FROM users u
        LEFT JOIN articles a ON a.user_id = u.id
        LEFT JOIN research_jobs rj ON rj.user_id = u.id
        GROUP BY u.id
        ORDER BY u.created_at DESC
    """).fetchall()
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

def create_job(user_id, job_type, topic):
    with _db_lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO research_jobs (user_id, job_type, topic, status, message) VALUES (?, ?, ?, 'pending', 'Starting...')",
            (user_id, job_type, topic)
        )
        conn.commit()
        row = conn.execute("SELECT last_insert_rowid()").fetchone()
        job_id = row[0]
        conn.close()
        return job_id


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


def get_all_jobs(status_filter=None, type_filter=None, limit=100):
    conn = get_conn()
    conditions = []
    params = []
    if status_filter:
        conditions.append("rj.status = ?")
        params.append(status_filter)
    if type_filter:
        conditions.append("rj.job_type = ?")
        params.append(type_filter)
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
