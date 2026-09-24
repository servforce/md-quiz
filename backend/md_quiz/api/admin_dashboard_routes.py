from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Query, Request

from backend.md_quiz.storage.db import get_admin_dashboard_summary

from . import admin as shared

router = APIRouter()

_JOB_ENTRY_PATHS = {
    "git_sync_exams": "/admin/quizzes",
    "scan_exams": "/admin/quizzes",
    "admin_candidate_resume_upload": "/admin/candidates",
    "admin_candidate_resume_reparse": "/admin/candidates",
    "resume_parse": "/admin/candidates",
    "grade_attempt": "/admin/assignments",
    "archive_attempt": "/admin/assignments",
}


@router.get("/dashboard")
def get_dashboard(
    request: Request,
    tz_offset_minutes: int = Query(default=0, ge=-720, le=840),
):
    shared._require_admin(request)
    start_day, end_day, start_at, end_at, _ = shared._resolve_log_trend_window(
        days=7, tz_offset_minutes=tz_offset_minutes,
    )
    summary = get_admin_dashboard_summary(at_from=start_at, at_to=end_at)
    for key in ("unhandled", "completed"):
        for item in summary[key]["items"]:
            item["candidate_deleted"] = bool(item.pop("candidate_deleted_at"))
            item["finished_at"] = shared._iso_or_empty(item["finished_at"])
            item["handled_at"] = shared._iso_or_empty(item["handled_at"])
            item["href"] = f"/admin/attempt/{quote(item['token'], safe='')}"
    for item in summary["failed_jobs"]["items"]:
        item["finished_at"] = shared._iso_or_empty(item["finished_at"])
        item["href"] = _JOB_ENTRY_PATHS.get(item["kind"], "/admin/status")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": {
            "days": 7,
            "start_date": start_day.isoformat(),
            "end_date": end_day.isoformat(),
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "tz_offset_minutes": tz_offset_minutes,
        },
        **summary,
    }
