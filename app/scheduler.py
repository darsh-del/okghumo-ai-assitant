"""Scheduler process: `python -m app.scheduler`. Run EXACTLY ONE instance (see docker-compose).

Polls the tasks table; due reminders become inbox rows. Idempotent via UNIQUE(task_id, scheduled_for).
Missed runs (downtime) fire once, not once per missed slot. Set SCHEDULER_ENABLED=0 to stop firing.
"""
import os
import time
from contextlib import closing
from datetime import datetime

from app.db import connect
from app.tasks import iso, next_run, now


def tick(at: datetime | None = None) -> int:
    at = at or now()
    fired = 0
    with closing(connect()) as c:
        c.execute("BEGIN IMMEDIATE")  # one writer at a time
        for t in c.execute("SELECT * FROM tasks WHERE status='active' AND next_run_at<=?", (iso(at),)).fetchall():
            cur = c.execute(
                "INSERT OR IGNORE INTO inbox(vendor_id,task_id,scheduled_for,title,body,created_at) VALUES(?,?,?,?,?,?)",
                (t["vendor_id"], t["id"], t["next_run_at"], t["name"], t["message"], iso(at)),
            )
            fired += cur.rowcount
            c.execute("UPDATE tasks SET last_run_at=?, next_run_at=? WHERE id=?",
                      (iso(at), iso(next_run(t["cron"], t["tz"], at)), t["id"]))
        c.execute("COMMIT")
    return fired


def main() -> None:
    while True:
        if os.environ.get("SCHEDULER_ENABLED", "1") == "1":
            try:
                tick()
            except Exception as e:  # keep the loop alive; log type only (no task text)
                print("tick failed:", type(e).__name__, flush=True)
        time.sleep(30)


if __name__ == "__main__":
    main()
