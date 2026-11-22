"""On-demand PDF reports in chat: "give me a detailed report on last month" -> the assistant politely asks PDF or
simple figures -> if PDF, it calls `create_pdf_report`.

This tool only READS the vendor's own Okghumo data (same read-only toolset, the vendor's own token, no app tools in the
nested call) and writes one file into THIS app's own DB. It cannot schedule, cancel or send anything. A daily cap
(RATE_PDF_PER_DAY) bounds cost. Kill switch: PDF_REPORTS=0. Never log question text, report text or tokens.
"""
import logging
import os
import time
import uuid

from app import logs, pdfreport, ratelimit
from app.db import run
from app.rbac import mcp_toolset

log = logging.getLogger("reports")

ASK_SYSTEM = (
    " DETAILED REPORTS: when the vendor asks for a detailed report, a full analysis or a breakdown, first ask ONE "
    "warm, polite question: would they like a detailed PDF with analytics and charts, or just simple figures here in "
    "the chat? For example: 'Of course, happy to help! Would you like a detailed PDF with analytics and charts, or "
    "just the key figures here in the chat?' Do not ask if they already said which. If they choose the PDF, thank "
    "them and call create_pdf_report with a self-contained `question` (what to cover and the period, relative dates "
    "only); if they choose figures, answer as usual. When the PDF is created, reply with one or two short, polite "
    "lines saying it is ready (the system attaches it). If the tool returns an error, apologise briefly and offer the "
    "simple figures instead. Never say a PDF is ready unless the tool succeeded."
)

TOOL = {
    "name": "create_pdf_report",
    "description": "Create the detailed PDF report (analytics and charts) the vendor asked for, ONLY after they chose "
                   "the PDF. Reads their own data only. Takes up to a minute.",
    "input_schema": {"type": "object", "required": ["question"], "properties": {
        "question": {"type": "string", "description": "Self-contained question for the report: topic and period, "
                                                      "relative dates ('last 30 days'). Max 500 chars"}}},
}


class OnDemand:
    """Per-request tool handler (plugs into assistant.ask(extra=...)). `.pdf` = (title, bytes) once created;
    `.pdf_id` = inbox row id for web vendors (WhatsApp vendors get the file pushed, nothing is stored)."""
    tools = [TOOL]

    def __init__(self, client, vendor: str, role: str | None, token: str):
        self.client, self.vendor, self.role, self.token = client, vendor, role, token
        self.pdf, self.pdf_id = None, None

    def call(self, name: str, inp) -> str:
        try:
            if name != "create_pdf_report":
                return "error: unknown tool"
            q = str((inp or {}).get("question") or "").strip()[:500] if isinstance(inp, dict) else ""
            if not q:
                return "error: question is required"
            if self.pdf:
                return "error: the PDF for this message is already created"
            mcp_toolset(self.role)  # role gate (KeyError = not allowed)
            if ratelimit.hit(f"pdf:{self.vendor}", int(os.environ.get("RATE_PDF_PER_DAY", 10)), 86400):
                logs.event(log, "pdf_rate_limited", logging.WARNING, vendor=logs.mask_id(self.vendor))
                return "error: the daily limit of PDF reports is reached; offer the simple figures instead"
            t0 = time.time()
            spec, pdf = pdfreport.generate(self.client, self.role, self.token, q)
            text = pdfreport.text_of(spec)
            self.pdf = (spec["title"], pdf)
            if not self.vendor.startswith("wa:"):
                now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
                self.pdf_id = run(
                    "INSERT INTO inbox(vendor_id,task_id,scheduled_for,title,body,read,created_at,sent,pdf) "
                    "VALUES(?,0,?,?,?,1,?,1,?) RETURNING id",
                    (self.vendor, "adhoc-" + uuid.uuid4().hex, spec["title"], text, now, pdf))[0]["id"]
            logs.event(log, "pdf_created", vendor=logs.mask_id(self.vendor), bytes=len(pdf), ms=int((time.time() - t0) * 1000))
            return "PDF created and attached by the system. Contents summary: " + text[:600]
        except pdfreport.BadSpec:
            return "error: could not build the report this time"
        except Exception as e:  # never leak internals to the model
            logs.event(log, "pdf_failed", logging.WARNING, vendor=logs.mask_id(self.vendor), error=type(e).__name__)
            return "error: could not create the report"


class Combo:
    """Several per-request tool handlers behind the one `extra` slot of assistant.ask()."""
    def __init__(self, *handlers):
        self.handlers = [h for h in handlers if h]
        self.tools = [t for h in self.handlers for t in h.tools]

    def call(self, name: str, inp) -> str:
        for h in self.handlers:
            if any(t["name"] == name for t in h.tools):
                return h.call(name, inp)
        return "error: unknown tool"


def for_vendor(client, vendor: str, role: str | None, token: str) -> "OnDemand | None":
    return OnDemand(client, vendor, role, token) if os.environ.get("PDF_REPORTS", "1") == "1" else None  # kill switch
