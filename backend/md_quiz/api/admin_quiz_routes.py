from __future__ import annotations

from fastapi import APIRouter, Request, status

from . import admin as shared

router = APIRouter()


def _normalize_quiz_list_sort(sort_by: str, sort_order: str) -> tuple[str, bool]:
    field = str(sort_by or "").strip().lower()
    if field not in {"updated_at", "respondent_count"}:
        field = "updated_at"
    descending = str(sort_order or "").strip().lower() != "asc"
    return field, descending


@router.get("/quizzes")
@router.get("/exams")
def list_exams(
    request: Request,
    q: str = "",
    page: int = 1,
    sort_by: str = "updated_at",
    sort_order: str = "desc",
):
    shared._require_admin(request)
    exams = shared.exam_helpers._list_exams()
    respondent_counts = shared.deps.count_quiz_respondents_by_key()
    for exam in exams:
        quiz_key = str(exam.get("quiz_key") or "").strip()
        exam["respondent_count"] = int(respondent_counts.get(quiz_key) or 0)
    query = str(q or "").strip().lower()
    if query:
        exams = [
            item
            for item in exams
            if query in str(item.get("quiz_key") or "").lower()
            or query in str(item.get("title") or "").lower()
            or query in str(item.get("id") or "")
            or any(query in str(tag or "").lower() for tag in (item.get("tags") or []))
        ]
    sort_field, descending = _normalize_quiz_list_sort(sort_by, sort_order)
    exams.sort(key=lambda item: str(item.get("quiz_key") or "").lower())
    if sort_field == "respondent_count":
        exams.sort(key=lambda item: int(item.get("respondent_count") or 0), reverse=descending)
    else:
        exams.sort(key=lambda item: float(item.get("_mtime") or 0), reverse=descending)
    per_page = 20
    total = len(exams)
    total_pages = max(1, (total + per_page - 1) // per_page)
    current_page = max(1, min(int(page or 1), total_pages))
    start = (current_page - 1) * per_page
    items = [shared._serialize_exam_summary(item, request) for item in exams[start : start + per_page]]
    return {
        "items": items,
        "page": current_page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "repo_binding": shared._serialize_repo_binding(shared.deps.read_exam_repo_binding()),
        "sync_state": shared.deps.read_exam_repo_sync_state(),
        "filters": {
            "q": str(q or "").strip(),
            "sort_by": sort_field,
            "sort_order": "desc" if descending else "asc",
        },
    }


@router.get("/quizzes/options")
@router.get("/exams/options")
def list_exam_options(request: Request, q: str = ""):
    shared._require_admin(request)
    exams = shared.exam_helpers._list_exams()
    query = str(q or "").strip().lower()
    if query:
        exams = [
            item
            for item in exams
            if query in str(item.get("quiz_key") or "").lower()
            or query in str(item.get("title") or "").lower()
            or query in str(item.get("id") or "")
            or any(query in str(tag or "").lower() for tag in (item.get("tags") or []))
        ]
    exams.sort(key=lambda item: float(item.get("_mtime") or 0), reverse=True)
    return {
        "items": [shared._serialize_exam_summary(item, request) for item in exams],
    }


@router.post("/quizzes/binding", status_code=status.HTTP_201_CREATED)
def bind_exam_repo(payload: shared.RepoBindingPayload, request: Request):
    shared._require_admin(request)
    try:
        result = shared.deps.bind_exam_repo(str(payload.repo_url or "").strip())
    except shared.ExamRepoSyncError as exc:
        raise shared.HTTPException(status_code=shared._repo_sync_http_status(exc), detail=str(exc)) from exc
    except Exception as exc:
        raise shared.HTTPException(status_code=500, detail=f"绑定仓库失败：{exc}") from exc
    try:
        shared.deps.log_event(
            "exam.repo.bind",
            actor="admin",
            meta={
                "repo_url": str((result.get("binding") or {}).get("repo_url") or ""),
                "job_id": str((result.get("sync") or {}).get("job_id") or ""),
                "sync_created": bool((result.get("sync") or {}).get("created")),
                "sync_error": str((result.get("sync") or {}).get("error") or ""),
            },
        )
    except Exception:
        pass
    return result


@router.post("/quizzes/binding/rebind")
def rebind_exam_repo(payload: shared.RepoRebindPayload, request: Request):
    shared._require_admin(request)
    if str(payload.confirmation_text or "").strip() != "重新绑定":
        raise shared.HTTPException(status_code=400, detail="确认词不正确")
    try:
        result = shared.deps.rebind_exam_repo(str(payload.repo_url or "").strip())
    except shared.ExamRepoSyncError as exc:
        raise shared.HTTPException(status_code=shared._repo_sync_http_status(exc), detail=str(exc)) from exc
    except Exception as exc:
        raise shared.HTTPException(status_code=500, detail=f"重新绑定失败：{exc}") from exc
    try:
        shared.deps.log_event(
            "exam.repo.rebind",
            actor="admin",
            meta={
                "previous_repo_url": str(result.get("previous_repo_url") or ""),
                "repo_url": str((result.get("binding") or {}).get("repo_url") or ""),
                "cleanup": result.get("cleanup") if isinstance(result.get("cleanup"), dict) else {},
                "job_id": str((result.get("sync") or {}).get("job_id") or ""),
                "sync_created": bool((result.get("sync") or {}).get("created")),
                "sync_error": str((result.get("sync") or {}).get("error") or ""),
            },
        )
    except Exception:
        pass
    return result


@router.post("/quizzes/sync")
def sync_exams(payload: shared.SyncExamPayload, request: Request):
    shared._require_admin(request)
    _ = payload
    binding = shared._serialize_repo_binding(shared.deps.read_exam_repo_binding())
    try:
        result = shared.deps.enqueue_exam_repo_sync()
    except shared.ExamRepoSyncError as exc:
        raise shared.HTTPException(status_code=shared._repo_sync_http_status(exc), detail=str(exc)) from exc
    except Exception as exc:
        raise shared.HTTPException(status_code=500, detail=f"同步任务创建失败：{exc}") from exc
    try:
        shared.deps.log_event(
            "exam.sync.enqueue",
            actor="admin",
            meta={
                "repo_url": str(binding.get("repo_url") or ""),
                "job_id": str(result.get("job_id") or ""),
                "created": bool(result.get("created")),
            },
        )
    except Exception:
        pass
    return result


@router.get("/quizzes/{quiz_key}")
@router.get("/exams/{quiz_key}")
def get_exam_detail(quiz_key: str, request: Request):
    shared._require_admin(request)
    exam = shared.deps.get_quiz_definition(str(quiz_key or "").strip())
    if not exam:
        raise shared.HTTPException(status_code=404, detail="测验不存在")
    selected_version = None
    current_version_id = int(exam.get("current_version_id") or 0)
    if current_version_id > 0:
        selected_version = shared.exam_helpers.get_quiz_version_snapshot(current_version_id)
    return shared._serialize_exam_detail(exam, request=request, selected_version=selected_version)


@router.get("/quiz-versions/{version_id}")
@router.get("/exam-versions/{version_id}")
def get_quiz_version_detail(version_id: int, request: Request):
    shared._require_admin(request)
    version = shared.exam_helpers.get_quiz_version_snapshot(version_id)
    if not version:
        raise shared.HTTPException(status_code=404, detail="版本不存在")
    exam = shared.deps.get_quiz_definition(str(version.get("quiz_key") or "").strip())
    if not exam:
        raise shared.HTTPException(status_code=404, detail="测验不存在")
    return shared._serialize_exam_detail(exam, request=request, selected_version=version)


@router.post("/quizzes/{quiz_key}/public-invite")
def toggle_exam_public_invite(quiz_key: str, payload: shared.PublicInviteTogglePayload, request: Request):
    shared._require_admin(request)
    exam = shared.deps.get_quiz_definition(str(quiz_key or "").strip())
    if not exam:
        raise shared.HTTPException(status_code=404, detail="测验不存在")
    cfg = shared.exam_helpers.set_public_invite_enabled(str(quiz_key or "").strip(), payload.enabled)
    public_token = str(cfg.get("token") or "").strip()
    try:
        shared.deps.log_event(
            "exam.public_invite.enable" if payload.enabled else "exam.public_invite.disable",
            actor="admin",
            quiz_key=str(quiz_key or "").strip(),
            meta={"public_token": public_token},
        )
    except Exception:
        pass
    return {
        "ok": True,
        "enabled": bool(cfg.get("enabled")),
        "token": public_token,
        "public_url": (
            f"{shared._admin_base_url(request)}/p/{public_token}"
            if bool(cfg.get("enabled")) and public_token
            else ""
        ),
        "qr_url": (
            f"/api/public/invites/{public_token}/qr.png"
            if bool(cfg.get("enabled")) and public_token
            else ""
        ),
    }
