from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from . import admin as shared
from .deps import get_container

router = APIRouter()


@router.post("/session/login")
def login(payload: shared.AdminLoginPayload, request: Request, container=Depends(get_container)):
    settings = container.settings
    if payload.username != settings.admin_username or payload.password != settings.admin_password:
        raise shared.HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误")
    request.session["admin_logged_in"] = True
    request.session["admin_username"] = payload.username
    return {"ok": True, "username": payload.username}


@router.post("/session/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/session")
def session(request: Request):
    return {
        "authenticated": bool(request.session.get("admin_logged_in")),
        "username": request.session.get("admin_username"),
    }


@router.get("/bootstrap")
def bootstrap(request: Request, container=Depends(get_container)):
    shared._require_admin(request)
    runtime_config = container.runtime_service.get_runtime_config().model_dump()
    return {
        "brand": {"name": "MD Quiz", "theme": runtime_config.get("ui_theme_name") or "blue-green"},
        "navigation": [
            {"key": "quizzes", "label": "测验", "href": "/admin/quizzes"},
            {"key": "candidates", "label": "候选人", "href": "/admin/candidates"},
            {"key": "job-descriptions", "label": "职位管理", "href": "/admin/job-descriptions"},
            {"key": "assignments", "label": "邀约与答题", "href": "/admin/assignments"},
            {"key": "logs", "label": "系统日志", "href": "/admin/logs"},
            {"key": "status", "label": "系统状态", "href": "/admin/status"},
        ],
        "runtime_config": runtime_config,
        "cards": [
            {"label": "后端架构", "value": "FastAPI + Worker + Scheduler"},
            {"label": "后台入口", "value": "/admin"},
            {"label": "兼容跳转", "value": "/legacy/* -> /*"},
        ],
    }


@router.get("/config")
def get_runtime_config(request: Request, container=Depends(get_container)):
    shared._require_admin(request)
    return container.runtime_service.get_runtime_config().model_dump()


@router.put("/config")
def update_runtime_config(payload: shared.RuntimeConfigPatch, request: Request, container=Depends(get_container)):
    shared._require_admin(request)
    updates = payload.model_dump(exclude_none=True)
    return container.runtime_service.update_runtime_config(updates).model_dump()


@router.get("/jobs")
def list_jobs(request: Request, container=Depends(get_container)):
    shared._require_admin(request)
    return {"items": [item.model_dump() for item in container.job_service.list_jobs()]}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request, container=Depends(get_container)):
    shared._require_admin(request)
    job = container.job_service.get_job(job_id)
    if job is None:
        raise shared.HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return job.model_dump()


@router.post("/jobs", status_code=status.HTTP_201_CREATED)
def enqueue_job(payload: shared.EnqueueJobPayload, request: Request, container=Depends(get_container)):
    shared._require_admin(request)
    job = container.job_service.enqueue(payload.kind, payload=payload.payload, source="admin-api")
    return job.model_dump()
