"""Phase 1: scheduled REMINDERS (plain text, no Okghumo data, no model call) + in-app inbox.

Every query is filtered by the vendor id from the verified JWT. Reports with data come in Phase 2
(SCHEDULING_ROADMAP.md).
"""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import logs
from app.auth import current_user
from app.db import run

router = APIRouter()
log = logging.getLogger("tasks")
MAX_TASKS = 10  # per vendor, any status
MIN_GAP_S = 3600  # no reminder may fire more than once an hour
REPORT_GAP_S = 20 * 3600  # reports cost a model call: at most daily (20h tolerates DST shifts)
MAX_REPORTS = 5  # per vendor


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat(timespec="seconds")


def next_run(cron: str, tz: str, after: datetime) -> datetime:
    """Next fire time (UTC) strictly after `after`, evaluating the cron in the vendor's timezone."""
    return croniter(cron, after.astimezone(ZoneInfo(tz))).get_next(datetime).astimezone(timezone.utc)


def validate(cron: str, tz: str, min_gap: int = MIN_GAP_S) -> None:
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, OSError):  # OSError: e.g. "America" is a directory
        raise HTTPException(422, "unknown timezone")
    if not croniter.is_valid(cron) or len(cron.split()) != 5:
        raise HTTPException(422, "cron must be 5 fields, e.g. '0 8 * * *'")
    t, prev = now(), None
    for _ in range(6):  # ponytail: samples 6 firings; a pathological cron could dodge it, upgrade = check all gaps in a month
        t = next_run(cron, tz, t)
        if prev and (t - prev).total_seconds() < min_gap:
            raise HTTPException(422, "schedule fires too often")
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


def check_task(v: str, message: str, cron: str, tz: str, kind: str) -> None:
    """Every rule a new task must satisfy. HTTPException on violation (also used to pre-check chat proposals)."""
    report = kind == "report"
    validate(cron, tz, REPORT_GAP_S if report else MIN_GAP_S)
    mine = run("SELECT kind, cron, message FROM tasks WHERE vendor_id=?", (v,))
    if len(mine) >= MAX_TASKS:
        raise HTTPException(409, f"max {MAX_TASKS} tasks")
    if report and sum(r["kind"] == "report" for r in mine) >= MAX_REPORTS:
        raise HTTPException(409, f"max {MAX_REPORTS} scheduled reports")
    if any(r["cron"] == cron and r["message"] == message and r["kind"] == kind for r in mine):
        raise HTTPException(409, "already scheduled")


def add_task(v: str, name: str, message: str, cron: str, tz: str, kind: str = "reminder", role: str | None = None) -> dict:
    """The one place a task row is created (API and chat-confirmed)."""
    check_task(v, message, cron, tz, kind)
    n = now()
    t = dict(run(
        "INSERT INTO tasks(vendor_id,name,message,cron,tz,kind,role,next_run_at,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?) RETURNING *",
        (v, name, message, cron, tz, kind, role, iso(next_run(cron, tz, n)), iso(n)),
    )[0])
    logs.event(log, "task_created", vendor=logs.mask_id(v), task=t["id"], kind=kind, cron=cron, tz=tz)
    return t


@router.post("/tasks", status_code=201)
def create_task(body: TaskIn, claims: dict = Depends(current_user)):
    return add_task(claims["sub"], body.name, body.message, body.cron, body.tz)  # API makes reminders only


@router.get("/tasks")
def list_tasks(claims: dict = Depends(current_user)):
    return [dict(r) for r in run("SELECT * FROM tasks WHERE vendor_id=? ORDER BY id", (claims["sub"],))]


@router.patch("/tasks/{task_id}")
def patch_task(task_id: int, body: TaskPatch, claims: dict = Depends(current_user)):
    t = _task(claims["sub"], task_id)
    # resuming recomputes next_run_at so a long pause doesn't fire stale
    run("UPDATE tasks SET status=?, fails=0, next_run_at=? WHERE id=?",
        (body.status, iso(next_run(t["cron"], t["tz"], now())), task_id))
    logs.event(log, "task_status", vendor=logs.mask_id(claims["sub"]), task=task_id, status=body.status)
    return dict(_task(claims["sub"], task_id))


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, claims: dict = Depends(current_user)):
    _task(claims["sub"], task_id)
    run("DELETE FROM tasks WHERE id=?", (task_id,))
    logs.event(log, "task_deleted", vendor=logs.mask_id(claims["sub"]), task=task_id)


@router.get("/inbox")
def inbox(unread: bool = False, claims: dict = Depends(current_user)):
    sql = "SELECT * FROM inbox WHERE vendor_id=?" + (" AND read=0" if unread else "") + " ORDER BY id DESC LIMIT 100"
    return [dict(r) for r in run(sql, (claims["sub"],))]


@router.post("/inbox/{item_id}/read", status_code=204)
def mark_read(item_id: int, claims: dict = Depends(current_user)):
    run("UPDATE inbox SET read=1 WHERE id=? AND vendor_id=?", (item_id, claims["sub"]))
