"""Phase 1: scheduled REMINDERS (plain text, no Okghumo data, no model call) + in-app inbox.

Every query is filtered by the vendor id from the verified JWT. Reports with data come in Phase 2
(SCHEDULING_ROADMAP.md).
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import current_user
from app.db import run

router = APIRouter()
MAX_TASKS = 10  # per vendor, any status
MIN_GAP_S = 3600  # no schedule may fire more than once an hour


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat(timespec="seconds")


def next_run(cron: str, tz: str, after: datetime) -> datetime:
    """Next fire time (UTC) strictly after `after`, evaluating the cron in the vendor's timezone."""
    return croniter(cron, after.astimezone(ZoneInfo(tz))).get_next(datetime).astimezone(timezone.utc)


def validate(cron: str, tz: str) -> None:
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "unknown timezone")
    if not croniter.is_valid(cron) or len(cron.split()) != 5:
        raise HTTPException(422, "cron must be 5 fields, e.g. '0 8 * * *'")
    t, prev = now(), None
    for _ in range(6):  # ponytail: samples 6 firings; a pathological cron could dodge it, upgrade = check all gaps in a month
        t = next_run(cron, tz, t)
        if prev and (t - prev).total_seconds() < MIN_GAP_S:
            raise HTTPException(422, "schedule may not fire more than once an hour")
        prev = t


class TaskIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)  # the reminder text delivered to the inbox
    cron: str = Field(max_length=100)
    tz: str = "Asia/Kolkata"


class TaskPatch(BaseModel):
    status: str = Field(pattern="^(active|paused)$")


def _task(vendor: str, task_id: int):
    rows = run("SELECT * FROM tasks WHERE id=? AND vendor_id=?", (task_id, vendor))
    if not rows:
        raise HTTPException(404, "not found")  # same answer for "missing" and "someone else's"
    return rows[0]


@router.post("/tasks", status_code=201)
def create_task(body: TaskIn, claims: dict = Depends(current_user)):
    v = claims["sub"]
    validate(body.cron, body.tz)
    if run("SELECT COUNT(*) AS n FROM tasks WHERE vendor_id=?", (v,))[0]["n"] >= MAX_TASKS:
        raise HTTPException(409, f"max {MAX_TASKS} tasks")
    n = now()
    run(
        "INSERT INTO tasks(vendor_id,name,message,cron,tz,next_run_at,created_at) VALUES(?,?,?,?,?,?,?)",
        (v, body.name, body.message, body.cron, body.tz, iso(next_run(body.cron, body.tz, n)), iso(n)),
    )
    return dict(run("SELECT * FROM tasks WHERE vendor_id=? ORDER BY id DESC LIMIT 1", (v,))[0])


@router.get("/tasks")
def list_tasks(claims: dict = Depends(current_user)):
    return [dict(r) for r in run("SELECT * FROM tasks WHERE vendor_id=? ORDER BY id", (claims["sub"],))]


@router.patch("/tasks/{task_id}")
def patch_task(task_id: int, body: TaskPatch, claims: dict = Depends(current_user)):
    t = _task(claims["sub"], task_id)
    # resuming recomputes next_run_at so a long pause doesn't fire stale
    run("UPDATE tasks SET status=?, next_run_at=? WHERE id=?",
        (body.status, iso(next_run(t["cron"], t["tz"], now())), task_id))
    return dict(_task(claims["sub"], task_id))


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, claims: dict = Depends(current_user)):
    _task(claims["sub"], task_id)
    run("DELETE FROM tasks WHERE id=?", (task_id,))


@router.get("/inbox")
def inbox(unread: bool = False, claims: dict = Depends(current_user)):
    sql = "SELECT * FROM inbox WHERE vendor_id=?" + (" AND read=0" if unread else "") + " ORDER BY id DESC LIMIT 100"
    return [dict(r) for r in run(sql, (claims["sub"],))]


@router.post("/inbox/{item_id}/read", status_code=204)
def mark_read(item_id: int, claims: dict = Depends(current_user)):
    run("UPDATE inbox SET read=1 WHERE id=? AND vendor_id=?", (item_id, claims["sub"]))
