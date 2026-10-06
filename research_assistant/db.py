"""Small SQLite store; PDF content, summaries and conversations survive restarts."""
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(path):
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize(path):
    with connect(path) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS papers (
                id TEXT PRIMARY KEY, external_id TEXT UNIQUE,
                title TEXT NOT NULL, authors TEXT NOT NULL DEFAULT '[]',
                year INTEGER, abstract TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '', pdf_url TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL, filename TEXT,
                pages TEXT NOT NULL DEFAULT '[]', content_hash TEXT,
                summary TEXT, summary_model TEXT, summary_language TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS papers_hash ON papers(content_hash);
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                question TEXT NOT NULL, answer TEXT NOT NULL,
                sources TEXT NOT NULL, model TEXT NOT NULL, created_at TEXT NOT NULL,
                language TEXT NOT NULL DEFAULT 'en'
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                kind TEXT NOT NULL, status TEXT NOT NULL,
                progress TEXT NOT NULL, result TEXT, error TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS search_cache (
                cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL,
                expires_at REAL NOT NULL
            );
        """)
        if "language" not in {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}:
            conn.execute("ALTER TABLE messages ADD COLUMN language TEXT NOT NULL DEFAULT 'en'")
            for row in conn.execute("SELECT id, answer FROM messages").fetchall():
                if re.search(r"[\u3400-\u9fff]", row["answer"]):
                    conn.execute("UPDATE messages SET language='zh' WHERE id=?", (row["id"],))
        conn.execute("""UPDATE jobs SET status='failed', error=?, progress='Interrupted'
                        WHERE status IN ('queued', 'running')""",
                     ("The server restarted. Please try again.",))


def serialize_paper(row, detail=False):
    paper = dict(row)
    paper["authors"] = json.loads(paper["authors"])
    pages = json.loads(paper.pop("pages"))
    paper["page_count"] = len(pages)
    paper["has_content"] = bool(pages)
    paper.pop("content_hash", None)
    paper.pop("filename", None)
    if detail:
        paper["pages"] = pages
    return paper
