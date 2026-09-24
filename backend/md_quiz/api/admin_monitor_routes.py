from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Query, Request

from backend.md_quiz.mcp import build_mcp_admin_payload

from . import admin as shared

router = APIRouter()


@router.get("/logs")
def get_logs(
    request: Request,
    page: int = 1,
    limit: int = 20,
    days: int = Query(default=30, ge=7, le=120),
    tz_offset_minutes: int = Query(default=0, ge=-720, le=840),
    q: str = "",
    category: Literal["", "candidate", "quiz", "grading", "assignment", "system"] = "",
):
    shared._require_admin(request)
    page_size = max(1, min(100, int(limit or 20)))
    query = str(q or "").strip()
    total = int(shared.deps.count_operation_logs(query=query or None, category=category or None) or 0)
    total_pages = max(1, (total + page_size - 1) // page_size)
    current_page = max(1, min(int(page or 1), total_pages))
    offset = (current_page - 1) * page_size
    rows = shared.deps.list_operation_logs(
        limit=page_size, offset=offset, query=query or None, category=category or None,
    )
    start_day, end_day, start_at, end_at, tz_offset_seconds = shared._resolve_log_trend_window(
        days=int(days or 30),
        tz_offset_minutes=int(tz_offset_minutes or 0),
    )
    trend_rows = shared.deps.list_operation_daily_counts_by_category(
        tz_offset_seconds=tz_offset_seconds,
        at_from=start_at,
        at_to=end_at,
    )
    return {
        "items": [shared._serialize_log_row(row) for row in rows],
        "page": current_page,
        "per_page": page_size,
        "total": total,
        "total_pages": total_pages,
        "filters": {"q": query, "category": category},
        "counts": shared._normalize_log_category_counts(shared.deps.count_operation_logs_by_category()),
        "trend": shared._serialize_log_trend(trend_rows, start_day=start_day, end_day=end_day),
    }


@router.get("/logs/updates")
def get_log_updates(request: Request, after_id: int = 0, limit: int = 20):
    shared._require_admin(request)
    page_size = max(1, min(50, int(limit or 20)))
    rows = shared.deps.list_operation_logs_after_id(after_id=int(after_id or 0), limit=page_size)
    items = [shared._serialize_log_row(row) for row in rows]
    items.sort(key=lambda item: int(item.get("id") or 0))
    return {"ok": True, "items": items}


@router.get("/system-status/summary")
def get_system_status_summary(request: Request):
    shared._require_admin(request)
    return shared.system_status_helpers._get_cached_system_status_summary()


@router.get("/mcp/summary")
def get_mcp_summary(request: Request):
    shared._require_admin(request)
    return build_mcp_admin_payload(request.app.state.container.settings)


@router.get("/system-status")
def get_system_status_range(
    request: Request,
    start: str = "",
    end: str = "",
):
    shared._require_admin(request)
    today = datetime.now().astimezone().date()
    start_day = shared._parse_date_ymd(start) or (today - timedelta(days=29))
    end_day = shared._parse_date_ymd(end) or today
    data = shared.system_status_helpers._compute_system_status_range(start_day=start_day, end_day=end_day)
    return {
        "ok": True,
        "config": shared.system_status_helpers._load_system_status_cfg(),
        "data": data,
        "summary": shared.system_status_helpers._get_cached_system_status_summary(),
    }


@router.put("/system-status/config")
def update_system_status_config(payload: dict[str, Any], request: Request):
    shared._require_admin(request)
    config = shared.system_status_helpers._save_system_status_cfg(payload if isinstance(payload, dict) else {})
    try:
        shared.deps.emit_alerts_for_current_snapshot()
    except Exception:
        pass
    return {
        "ok": True,
        "config": config,
        "summary": shared.system_status_helpers._get_cached_system_status_summary(force=True),
    }


@router.post("/system-status/alerts/cleanup")
def cleanup_system_status_alerts(payload: dict[str, Any], request: Request):
    shared._require_admin(request)
    body = payload if isinstance(payload, dict) else {}
    day = str(body.get("day") or "").strip()
    kind = str(body.get("kind") or "").strip()
    try:
        deleted = int(shared.deps.cleanup_duplicate_system_alert_logs(day=(day or None), kind=(kind or None)))
    except Exception as exc:
        raise shared.HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True, "deleted": deleted, "day": day, "kind": kind}


@router.post("/system-status/alerts/backfill")
def backfill_system_status_alerts(payload: dict[str, Any], request: Request):
    shared._require_admin(request)
    body = payload if isinstance(payload, dict) else {}
    day = str(body.get("day") or "").strip()
    kind = str(body.get("kind") or "").strip()
    if not day or kind not in {"llm_tokens", "sms_calls"}:
        raise shared.HTTPException(status_code=400, detail="invalid day/kind")
    try:
        inserted = int(shared.deps.backfill_missing_system_alert_levels(day=day, kind=kind))
    except Exception as exc:
        raise shared.HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True, "inserted": inserted, "day": day, "kind": kind}
