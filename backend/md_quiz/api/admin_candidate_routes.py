from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, Response, UploadFile, status

from . import admin as shared

router = APIRouter()


def _validate_candidate_job_description_id(raw_id: Any) -> int:
    try:
        job_description_id = int(raw_id)
    except Exception as exc:
        raise shared.HTTPException(status_code=400, detail="请选择职位") from exc
    if job_description_id <= 0:
        raise shared.HTTPException(status_code=400, detail="请选择职位")
    item = shared.deps.get_job_description(job_description_id)
    if not item or str(item.get("status") or "").strip().lower() != "active":
        raise shared.HTTPException(status_code=400, detail="请选择有效职位")
    return job_description_id


def _validate_candidate_job_description_ids(raw_ids: list[int]) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for raw_id in raw_ids:
        job_description_id = _validate_candidate_job_description_id(raw_id)
        if job_description_id in seen:
            continue
        seen.add(job_description_id)
        normalized.append(job_description_id)
    if not normalized:
        raise shared.HTTPException(status_code=400, detail="请选择职位")
    return normalized


def _candidate_attempt_summary(item: dict[str, Any]) -> dict[str, Any]:
    token = str(item.get("token") or item.get("attempt_token") or "").strip()
    quiz_key = str(item.get("quiz_key") or item.get("attempt_quiz_key") or "").strip()
    if not token and not quiz_key:
        return {}
    status_key = shared.validation_helpers._normalize_exam_status(
        str(item.get("status") or item.get("attempt_status") or "").strip()
    )
    completed = status_key == "finished"
    score = item.get("score")
    if score is None:
        score = item.get("attempt_score")
    score_display = shared._score_display(
        score,
        item.get("score_max") if item.get("score_max") is not None else item.get("attempt_score_max"),
        result_mode=str(item.get("result_mode") or item.get("attempt_result_mode") or "").strip(),
    ) if score is not None or completed else ""
    return {
        "attempt_id": int(item.get("attempt_id") or 0),
        "quiz_key": quiz_key,
        "quiz_version_id": int(item.get("quiz_version_id") or item.get("attempt_quiz_version_id") or 0),
        "quiz_name": str(item.get("quiz_title") or item.get("attempt_quiz_title") or "").strip() or quiz_key or "未知试卷",
        "token": token,
        "status": status_key,
        "status_label": shared._status_label(status_key),
        "completed": completed,
        "completion_label": "已完成" if completed else "未完成",
        "score": score,
        "score_display": score_display,
        "finished_at": shared._iso_or_empty(item.get("finished_at") or item.get("attempt_finished_at")),
        "created_at": shared._iso_or_empty(item.get("created_at") or item.get("attempt_created_at")),
    }


def _candidate_attempt_summaries(item: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = item.get("attempt_summaries")
    if not isinstance(raw_items, list):
        raw_items = []
    summaries: list[dict[str, Any]] = []
    for raw in raw_items[:2]:
        if not isinstance(raw, dict):
            continue
        summary = _candidate_attempt_summary(raw)
        if summary:
            summaries.append(summary)
    return summaries


def _resume_download_content_disposition(raw_filename: Any, candidate_id: int) -> str:
    filename = os.path.basename(str(raw_filename or "").replace("\\", "/").strip())
    filename = "".join(ch for ch in filename if ch >= " " and ch != "\x7f")
    if not filename:
        filename = f"candidate_{candidate_id}_resume.bin"

    fallback = "".join(ch if 32 <= ord(ch) <= 126 and ch not in {'"', "\\", ";"} else "_" for ch in filename)
    fallback = fallback.strip(" .") or f"candidate_{candidate_id}_resume.bin"
    encoded = quote(filename, safe="")
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{encoded}'


def _serialize_candidate_list_item(item: dict[str, Any]) -> dict[str, Any]:
    attempt_summaries = _candidate_attempt_summaries(item)
    job_description_rows: list[dict[str, Any]] = []
    candidate_id = int(item.get("id") or 0)
    if candidate_id > 0:
        try:
            job_description_rows = shared.deps.list_candidate_job_descriptions(candidate_id)
        except Exception:
            job_description_rows = []
    default_quiz_keys = shared._candidate_default_quizzes_from_job_rows(job_description_rows)
    return {
        "id": candidate_id,
        "name": str(item.get("name") or "").strip(),
        "phone": str(item.get("phone") or "").strip(),
        "created_at": shared._iso_or_empty(item.get("created_at")),
        "has_resume": bool(item.get("has_resume")),
        "default_quiz_key": default_quiz_keys[0] if default_quiz_keys else "",
        "default_quiz_keys": default_quiz_keys,
        "attempt_summary": attempt_summaries[0] if attempt_summaries else {},
        "attempt_summaries": attempt_summaries,
    }


@router.get("/candidates")
def get_candidates(
    request: Request,
    q: str = "",
    created_from: str = "",
    created_to: str = "",
    page: int = 1,
):
    shared._require_admin(request)
    created_from_raw = str(created_from or "").strip()
    created_to_raw = str(created_to or "").strip()
    parsed_from = shared._parse_candidate_query_dates(created_from_raw, end_of_day=False)
    parsed_to = shared._parse_candidate_query_dates(created_to_raw, end_of_day=True)
    per_page = 20
    total = shared.deps.count_candidates(query=q or None, created_from=parsed_from, created_to=parsed_to)
    total_pages = max(1, (total + per_page - 1) // per_page)
    current_page = max(1, min(int(page or 1), total_pages))
    offset = (current_page - 1) * per_page
    items = shared.deps.list_candidates(
        limit=per_page,
        offset=offset,
        query=q or None,
        created_from=parsed_from,
        created_to=parsed_to,
    )
    return {
        "items": [_serialize_candidate_list_item(item) for item in items],
        "page": current_page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "filters": {
            "q": str(q or "").strip(),
            "created_from": created_from_raw,
            "created_to": created_to_raw,
        },
    }


@router.post("/candidates", status_code=status.HTTP_201_CREATED)
def create_candidate(payload: shared.CandidateCreatePayload, request: Request):
    shared._require_admin(request)
    name = str(payload.name or "").strip()
    phone = shared.validation_helpers._normalize_phone(payload.phone)
    job_description_id = _validate_candidate_job_description_id(payload.job_description_id)
    if not shared.validation_helpers._is_valid_name(name):
        raise shared.HTTPException(status_code=400, detail="姓名格式不正确")
    if not shared.validation_helpers._is_valid_phone(phone):
        raise shared.HTTPException(status_code=400, detail="手机号格式不正确")
    if shared.deps.get_candidate_by_phone(phone):
        raise shared.HTTPException(status_code=409, detail="候选人已存在")
    try:
        candidate_id = int(shared.deps.create_candidate(name=name, phone=phone, job_description_id=job_description_id))
        shared.deps.log_event(
            "candidate.create",
            actor="admin",
            candidate_id=candidate_id,
            meta={"name": name, "phone": phone},
        )
    except shared.HTTPException:
        raise
    except Exception as exc:
        raise shared.HTTPException(status_code=500, detail="创建候选人失败") from exc
    return {
        "id": candidate_id,
        "name": name,
        "phone": phone,
        "job_descriptions": shared.deps.list_candidate_job_descriptions(candidate_id),
    }


@router.post("/candidates/resume/upload")
def upload_candidate_resume(
    request: Request,
    file: UploadFile = File(...),
    job_description_id: int = Form(...),
):
    shared._require_admin(request)
    result = shared.candidate_resume_admin_service.upload_candidate_resume(
        file,
        job_description_id=_validate_candidate_job_description_id(job_description_id),
    )
    candidate_id = int(result.get("candidate_id") or 0)
    candidate = result.get("candidate") if isinstance(result.get("candidate"), dict) else {}
    return {
        "created": bool(result.get("created")),
        **shared._serialize_candidate_detail(candidate_id, candidate),
    }


@router.post("/candidates/resume/upload-job", status_code=status.HTTP_202_ACCEPTED)
def enqueue_candidate_resume_upload(
    request: Request,
    file: UploadFile = File(...),
    job_description_id: int = Form(...),
):
    shared._require_admin(request)
    return shared.candidate_resume_admin_service.enqueue_candidate_resume_upload(
        file,
        job_description_id=_validate_candidate_job_description_id(job_description_id),
    )


@router.get("/candidates/{candidate_id}")
def get_candidate_detail(candidate_id: int, request: Request):
    shared._require_admin(request)
    candidate = shared.deps.get_candidate(candidate_id)
    if not candidate:
        raise shared.HTTPException(status_code=404, detail="候选人不存在")
    try:
        shared.deps.log_event(
            "candidate.read",
            actor="admin",
            candidate_id=int(candidate_id),
            meta={
                "name": str(candidate.get("name") or "").strip(),
                "phone": str(candidate.get("phone") or "").strip(),
            },
        )
    except Exception:
        pass
    return shared._serialize_candidate_detail(candidate_id, candidate)


@router.post("/candidates/{candidate_id}/job-descriptions")
def add_candidate_job_descriptions(
    candidate_id: int,
    payload: shared.CandidateJobDescriptionAddPayload,
    request: Request,
):
    shared._require_admin(request)
    candidate = shared.deps.get_candidate(candidate_id)
    if not candidate:
        raise shared.HTTPException(status_code=404, detail="候选人不存在")
    job_description_ids = _validate_candidate_job_description_ids(payload.job_description_ids)
    try:
        shared.deps.add_candidate_job_descriptions(int(candidate_id), job_description_ids)
        shared.deps.log_event(
            "candidate.job_description.add",
            actor="admin",
            candidate_id=int(candidate_id),
            meta={"job_description_ids": job_description_ids},
        )
    except shared.HTTPException:
        raise
    except Exception as exc:
        raise shared.HTTPException(status_code=500, detail="增加职位失败") from exc
    return shared._serialize_candidate_detail(candidate_id, shared.deps.get_candidate(candidate_id) or candidate)


@router.delete("/candidates/{candidate_id}/job-descriptions/{job_description_id}")
def remove_candidate_job_description(candidate_id: int, job_description_id: int, request: Request):
    return _remove_candidate_job_description(candidate_id, job_description_id, request)


@router.post("/candidates/{candidate_id}/job-descriptions/remove")
def remove_candidate_job_description_by_payload(
    candidate_id: int,
    payload: shared.CandidateJobDescriptionRemovePayload,
    request: Request,
):
    return _remove_candidate_job_description(candidate_id, payload.job_description_id, request)


def _remove_candidate_job_description(candidate_id: int, job_description_id: int, request: Request):
    shared._require_admin(request)
    candidate = shared.deps.get_candidate(candidate_id)
    if not candidate:
        raise shared.HTTPException(status_code=404, detail="候选人不存在")
    try:
        removed = shared.deps.remove_candidate_job_description(int(candidate_id), int(job_description_id))
        shared.deps.log_event(
            "candidate.job_description.remove",
            actor="admin",
            candidate_id=int(candidate_id),
            meta={"job_description_id": int(job_description_id), "removed": removed},
        )
    except Exception as exc:
        raise shared.HTTPException(status_code=500, detail="取消职位关联失败") from exc
    return shared._serialize_candidate_detail(candidate_id, shared.deps.get_candidate(candidate_id) or candidate)


@router.delete("/candidates/{candidate_id}")
def remove_candidate(candidate_id: int, request: Request):
    shared._require_admin(request)
    candidate = shared.deps.get_candidate(candidate_id)
    if not candidate:
        raise shared.HTTPException(status_code=404, detail="候选人不存在")
    try:
        shared.deps.delete_candidate(candidate_id)
        shared.deps.log_event(
            "candidate.delete",
            actor="admin",
            candidate_id=int(candidate_id),
            meta={
                "name": str(candidate.get("name") or "").strip(),
                "phone": str(candidate.get("phone") or "").strip(),
            },
        )
    except Exception as exc:
        raise shared.HTTPException(status_code=500, detail="删除失败") from exc
    return {"ok": True}


@router.post("/candidates/{candidate_id}/evaluation")
def update_candidate_evaluation(candidate_id: int, payload: shared.CandidateEvaluationPayload, request: Request):
    shared._require_admin(request)
    candidate = shared.deps.get_candidate(candidate_id)
    if not candidate:
        raise shared.HTTPException(status_code=404, detail="候选人不存在")
    evaluation = str(payload.evaluation or "").strip()
    if not evaluation:
        raise shared.HTTPException(status_code=400, detail="评价不能为空")
    parsed = candidate.get("resume_parsed") or {}
    if not isinstance(parsed, dict):
        parsed = {}
    details = parsed.get("details") or {}
    if not isinstance(details, dict):
        details = {}
    details_data = details.get("data") or {}
    if not isinstance(details_data, dict):
        details_data = {}
    existing = details_data.get("admin_evaluations")
    items: list[dict[str, str]] = []
    if isinstance(existing, list):
        for item in existing:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            at = str(item.get("at") or "").strip()
            if text:
                items.append({"text": text, "at": at})
    now_iso = datetime.now(timezone.utc).isoformat()
    items.append({"text": evaluation, "at": now_iso})
    details_data["admin_evaluations"] = items
    details_data["admin_evaluation"] = ""
    details["data"] = details_data
    parsed["details"] = details
    shared.deps.update_candidate_resume_parsed(
        candidate_id,
        resume_parsed=parsed,
        touch_resume_parsed_at=False,
    )
    return shared._serialize_candidate_detail(candidate_id, shared.deps.get_candidate(candidate_id) or candidate)


def _normalize_resume_job_match_score(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, bool):
        raise shared.HTTPException(status_code=400, detail="匹配度必须是 0-100 的整数")
    if isinstance(value, float) and not value.is_integer():
        raise shared.HTTPException(status_code=400, detail="匹配度必须是 0-100 的整数")
    try:
        score = int(value)
    except Exception as exc:
        raise shared.HTTPException(status_code=400, detail="匹配度必须是 0-100 的整数") from exc
    if score < 0 or score > 100:
        raise shared.HTTPException(status_code=400, detail="匹配度必须是 0-100 的整数")
    return score


@router.post("/candidates/{candidate_id}/resume/evaluation")
def update_candidate_resume_evaluation(
    candidate_id: int,
    payload: shared.CandidateResumeEvaluationPayload,
    request: Request,
):
    shared._require_admin(request)
    candidate = shared.deps.get_candidate(candidate_id)
    if not candidate:
        raise shared.HTTPException(status_code=404, detail="候选人不存在")

    parsed = candidate.get("resume_parsed") or {}
    if not isinstance(parsed, dict):
        parsed = {}
    details = parsed.get("details") or {}
    if not isinstance(details, dict):
        details = {}
    details_data = details.get("data") or {}
    if not isinstance(details_data, dict):
        details_data = {}

    details_data["evaluation"] = str(payload.evaluation or "").strip()
    details_data["job_match_score"] = _normalize_resume_job_match_score(payload.job_match_score)
    details_data["evaluation_source"] = "manual"
    details_data["evaluation_updated_at"] = datetime.now(timezone.utc).isoformat()
    details["data"] = details_data
    parsed["details"] = details

    shared.deps.update_candidate_resume_parsed(
        candidate_id,
        resume_parsed=parsed,
        touch_resume_parsed_at=False,
    )
    return shared._serialize_candidate_detail(candidate_id, shared.deps.get_candidate(candidate_id) or candidate)


@router.get("/candidates/{candidate_id}/resume")
def download_candidate_resume(candidate_id: int, request: Request):
    shared._require_admin(request)
    candidate = shared.deps.get_candidate(candidate_id)
    if not candidate:
        raise shared.HTTPException(status_code=404, detail="候选人不存在")
    resume = shared.deps.get_candidate_resume(candidate_id)
    if not resume:
        raise shared.HTTPException(status_code=404, detail="简历不存在")
    data = resume.get("resume_bytes") or b""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise shared.HTTPException(status_code=404, detail="简历不存在")
    mime = str(resume.get("resume_mime") or "").strip() or "application/octet-stream"
    headers = {
        "Content-Disposition": _resume_download_content_disposition(
            resume.get("resume_filename"),
            candidate_id,
        )
    }
    return Response(content=bytes(data), media_type=mime, headers=headers)


@router.post("/candidates/{candidate_id}/resume/reparse")
def reparse_candidate_resume(candidate_id: int, request: Request, file: UploadFile = File(...)):
    shared._require_admin(request)
    result = shared.candidate_resume_admin_service.reparse_candidate_resume(candidate_id, file)
    candidate = result.get("candidate") if isinstance(result.get("candidate"), dict) else {}
    return shared._serialize_candidate_detail(candidate_id, candidate)


@router.post("/candidates/{candidate_id}/resume/reparse-job", status_code=status.HTTP_202_ACCEPTED)
def enqueue_candidate_resume_reparse(candidate_id: int, request: Request, file: UploadFile = File(...)):
    shared._require_admin(request)
    return shared.candidate_resume_admin_service.enqueue_candidate_resume_reparse(candidate_id, file)
