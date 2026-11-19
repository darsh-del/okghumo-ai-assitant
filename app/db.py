"""This app's OWN sqlite DB (never Okghumo's): vendor credentials, reminder tasks, inbox.

ponytail: sqlite file shared by the API and the scheduler container on ONE host. Move to Postgres
(+ SELECT ... FOR UPDATE SKIP LOCKED in scheduler.tick) before running more than one host.
"""
import os
import sqlite3
from contextlib import closing

SCHEMA = """
CREATE TABLE IF NOT EXISTS credentials(
  vendor_id TEXT PRIMARY KEY, token_enc BLOB NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, vendor_id TEXT NOT NULL, name TEXT NOT NULL,
  message TEXT NOT NULL, cron TEXT NOT NULL, tz TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active', next_run_at TEXT NOT NULL, last_run_at TEXT,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS tasks_due ON tasks(status, next_run_at);
CREATE TABLE IF NOT EXISTS inbox(
  id INTEGER PRIMARY KEY AUTOINCREMENT, vendor_id TEXT NOT NULL, task_id INTEGER NOT NULL,
  scheduled_for TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
  read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
  UNIQUE(task_id, scheduled_for));  -- idempotency: a restart can never fire the same run twice
"""


def connect() -> sqlite3.Connection:
    path = os.environ.get("DB_PATH", "data/app.db")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    c = sqlite3.connect(path, timeout=10, isolation_level=None)  # autocommit; explicit BEGIN where needed
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    return c


def run(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    with closing(connect()) as c:
        return c.execute(sql, args).fetchall()
