"""Scheduler process: `python -m app.scheduler`. Run EXACTLY ONE instance (see docker-compose).

Polls the tasks table; due reminders become inbox rows. Due reports get a placeholder inbox row (idempotent via
UNIQUE(task_id, scheduled_for)) that a worker thread fills in via app.runner. Missed runs (downtime) fire once,
not once per missed slot. Set SCHEDULER_ENABLED=0 to stop firing.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime

from app import whatsapp
from app.db import connect
from app.runner import run_report
from app.tasks import iso, next_run, now

PENDING = "⏳ Preparing your report…"


def tick(at: datetime | None = None, submit=None) -> int:
    submit = submit or (lambda f, *a: f(*a))  # tests run reports inline; main() passes a thread pool
    at = at or now()
    fired, jobs = 0, []
    with closing(connect()) as c:
        c.execute("BEGIN IMMEDIATE")  # one writer at a time
        for t in c.execute("SELECT * FROM tasks WHERE status='active' AND next_run_at<=?", (iso(at),)).fetchall():
            report = t["kind"] == "report"
            cur = c.execute(
                "INSERT OR IGNORE INTO inbox(vendor_id,task_id,scheduled_for,title,body,created_at,sent) VALUES(?,?,?,?,?,?,?)",
                (t["vendor_id"], t["id"], t["next_run_at"], t["name"], PENDING if report else t["message"], iso(at),
                 0 if t["vendor_id"].startswith("wa:") else 1),
            )
            fired += cur.rowcount
            if cur.rowcount and report:
                jobs.append((run_report, cur.lastrowid, dict(t)))
            elif cur.rowcount and t["vendor_id"].startswith("wa:"):  # WhatsApp reminder: push it out
                jobs.append((whatsapp.deliver, cur.lastrowid, t["vendor_id"], t["name"], t["message"]))
            c.execute("UPDATE tasks SET last_run_at=?, next_run_at=? WHERE id=?",
                      (iso(at), iso(next_run(t["cron"], t["tz"], at)), t["id"]))
        c.execute("COMMIT")
    for job in jobs:  # after COMMIT: slow model calls never hold the DB write lock
        submit(*job)
    return fired


def main() -> None:
    # ponytail: a crash mid-report leaves the placeholder row; add a stale-placeholder sweep if it ever matters
    pool = ThreadPoolExecutor(max_workers=4)
    while True:
        if os.environ.get("SCHEDULER_ENABLED", "1") == "1":
            try:
                tick(submit=pool.submit)
            except Exception as e:  # keep the loop alive; log type only (no task text)
                print("tick failed:", type(e).__name__, flush=True)
        time.sleep(30)


if __name__ == "__main__":
    main()
