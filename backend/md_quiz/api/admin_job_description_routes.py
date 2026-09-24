from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, Field

from backend.md_quiz.services.markdown_rendering import render_markdown_html

from . import admin as shared

router = APIRouter()

_JOB_DESCRIPTION_STATUS_LABELS = {
    "draft": "草稿",
    "active": "启用",
    "archived": "归档",
}
_JOB_DESCRIPTION_SOURCE_LABELS = {
    "manual": "手动",
    "git": "仓库",
}


class JobDescriptionPayload(BaseModel):
    title: str = Field(default="", max_length=160)
    content_md: str = ""
    status: str = "draft"
    related_quizzes: list[str] = Field(default_factory=list)


class JobDescriptionPreviewPayload(BaseModel):
    content_md: str = ""


def _normalize_job_description_status(value: str, *, default: str = "draft") -> str:
    status_key = str(value or "").strip().lower() or default
    if status_key not in _JOB_DESCRIPTION_STATUS_LABELS:
        raise shared.HTTPException(status_code=400, detail="职位状态不正确")
    return status_key


def _job_description_excerpt(content_md: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", str(content_md or ""))
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[#>*_`~\\|-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= 120:
        return text
    return f"{text[:120].rstrip()}..."


def _normalize_related_quizzes(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _serialize_job_description(item: dict[str, Any], *, include_content: bool = False) -> dict[str, Any]:
    content_md = str(item.get("content_md") or "")
    status_key = str(item.get("status") or "").strip().lower() or "draft"
    out = {
        "id": int(item.get("id") or 0),
        "title": str(item.get("title") or "").strip(),
        "status": status_key,
        "status_label": _JOB_DESCRIPTION_STATUS_LABELS.get(status_key, status_key),
        "jd_key": str(item.get("jd_key") or "").strip(),
        "source_kind": str(item.get("source_kind") or "manual").strip().lower() or "manual",
        "source_path": str(item.get("source_path") or "").strip(),
        "git_repo_url": str(item.get("git_repo_url") or "").strip(),
        "last_synced_commit": str(item.get("last_synced_commit") or "").strip(),
        "last_sync_error": str(item.get("last_sync_error") or "").strip(),
        "last_sync_at": shared._iso_or_empty(item.get("last_sync_at")),
        "related_quizzes": _normalize_related_quizzes(item.get("related_quizzes")),
        "excerpt": _job_description_excerpt(content_md),
        "content_length": len(content_md),
        "created_at": shared._iso_or_empty(item.get("created_at")),
        "updated_at": shared._iso_or_empty(item.get("updated_at")),
    }
    out["source_label"] = _JOB_DESCRIPTION_SOURCE_LABELS.get(out["source_kind"], out["source_kind"])
    if include_content:
        out["content_md"] = content_md
        out["content_html"] = render_markdown_html(content_md)
    return out


def _serialize_job_description_option(item: dict[str, Any]) -> dict[str, Any]:
    status_key = str(item.get("status") or "").strip().lower() or "draft"
    source_kind = str(item.get("source_kind") or "manual").strip().lower() or "manual"
    return {
        "id": int(item.get("id") or 0),
        "title": str(item.get("title") or "").strip(),
        "status": status_key,
        "status_label": _JOB_DESCRIPTION_STATUS_LABELS.get(status_key, status_key),
        "jd_key": str(item.get("jd_key") or "").strip(),
        "source_kind": source_kind,
        "source_label": _JOB_DESCRIPTION_SOURCE_LABELS.get(source_kind, source_kind),
        "source_path": str(item.get("source_path") or "").strip(),
        "related_quizzes": _normalize_related_quizzes(item.get("related_quizzes")),
    }


@router.get("/job-descriptions")
def list_job_descriptions(
    request: Request,
    q: str = "",
    status_filter: str = Query(default="", alias="status"),
    page: int = 1,
):
    shared._require_admin(request)
    normalized_status = ""
    if str(status_filter or "").strip():
        normalized_status = _normalize_job_description_status(status_filter, default="")
    per_page = 20
    total = shared.deps.count_job_descriptions(query=q or None, status=normalized_status or None)
    total_pages = max(1, (total + per_page - 1) // per_page)
    current_page = max(1, min(int(page or 1), total_pages))
    offset = (current_page - 1) * per_page
    items = shared.deps.list_job_descriptions(
        limit=per_page,
        offset=offset,
        query=q or None,
        status=normalized_status or None,
    )
    return {
        "items": [_serialize_job_description(item) for item in items],
        "page": current_page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "filters": {
            "q": str(q or "").strip(),
            "status": normalized_status,
        },
        "status_options": [
            {"key": key, "label": label}
            for key, label in _JOB_DESCRIPTION_STATUS_LABELS.items()
        ],
    }


@router.get("/job-descriptions/options")
def list_job_description_options(request: Request):
    shared._require_admin(request)
    items = shared.deps.list_job_description_options()
    return {"items": [_serialize_job_description_option(item) for item in items]}


@router.post("/job-descriptions", status_code=status.HTTP_201_CREATED)
def create_job_description(payload: JobDescriptionPayload, request: Request):
    shared._require_admin(request)
    title = str(payload.title or "").strip()
    if not title:
        raise shared.HTTPException(status_code=400, detail="岗位名称不能为空")
    status_key = _normalize_job_description_status(payload.status)
    item = shared.deps.create_job_description(
        title=title,
        content_md=str(payload.content_md or ""),
        status=status_key,
        related_quizzes=_normalize_related_quizzes(payload.related_quizzes),
    )
    return _serialize_job_description(item, include_content=True)


@router.post("/job-descriptions/preview")
def preview_job_description(payload: JobDescriptionPreviewPayload, request: Request):
    shared._require_admin(request)
    return {"content_html": render_markdown_html(payload.content_md)}


@router.get("/job-descriptions/{job_description_id}")
def get_job_description(job_description_id: int, request: Request):
    shared._require_admin(request)
    item = shared.deps.get_job_description(int(job_description_id))
    if not item:
        raise shared.HTTPException(status_code=404, detail="职位不存在")
    return _serialize_job_description(item, include_content=True)


@router.put("/job-descriptions/{job_description_id}")
def update_job_description(job_description_id: int, payload: JobDescriptionPayload, request: Request):
    shared._require_admin(request)
    existing = shared.deps.get_job_description(int(job_description_id))
    if not existing:
        raise shared.HTTPException(status_code=404, detail="职位不存在")
    if str(existing.get("source_kind") or "").strip().lower() == "git":
        raise shared.HTTPException(status_code=409, detail="仓库来源职位请在 Git 仓库中修改")
    title = str(payload.title or "").strip()
    if not title:
        raise shared.HTTPException(status_code=400, detail="岗位名称不能为空")
    status_key = _normalize_job_description_status(payload.status)
    item = shared.deps.update_job_description(
        int(job_description_id),
        title=title,
        content_md=str(payload.content_md or ""),
        status=status_key,
        related_quizzes=_normalize_related_quizzes(payload.related_quizzes),
    )
    if not item:
        raise shared.HTTPException(status_code=404, detail="职位不存在")
    return _serialize_job_description(item, include_content=True)


@router.delete("/job-descriptions/{job_description_id}")
def delete_job_description(job_description_id: int, request: Request):
    shared._require_admin(request)
    existing = shared.deps.get_job_description(int(job_description_id))
    if not existing:
        raise shared.HTTPException(status_code=404, detail="职位不存在")
    if str(existing.get("source_kind") or "").strip().lower() == "git":
        raise shared.HTTPException(status_code=409, detail="仓库来源职位请在 Git 仓库中归档或移除")
    deleted = shared.deps.delete_job_description(int(job_description_id))
    if deleted <= 0:
        raise shared.HTTPException(status_code=404, detail="职位不存在")
    return {"ok": True, "deleted": deleted}
