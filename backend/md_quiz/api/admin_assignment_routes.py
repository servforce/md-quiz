from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from fastapi import APIRouter, Query, Request, Response, status

from . import admin as shared

router = APIRouter()


def _is_subjective_question(quiz_paper: dict[str, Any], qid: str) -> bool:
    """仅从该次答题绑定的题库快照校验简答题，避免构建完整答题回放。"""
    try:
        version_id = int(quiz_paper.get("quiz_version_id") or 0)
    except (TypeError, ValueError):
        version_id = 0
    snapshot = shared.deps.get_quiz_version(version_id) if version_id > 0 else None
    if not isinstance(snapshot, dict) or not snapshot:
        quiz_key = str(quiz_paper.get("quiz_key") or "").strip()
        snapshot = shared.deps.get_quiz_definition(quiz_key) if quiz_key else None
    if not isinstance(snapshot, dict):
        return False

    for section in ("spec", "public_spec"):
        questions = snapshot.get(section) or {}
        if not isinstance(questions, dict):
            continue
        for question in questions.get("questions") or []:
            if not isinstance(question, dict):
                continue
            if str(question.get("qid") or "").strip() != qid:
                continue
            return str(question.get("type") or "").strip().lower() == "short"
    return False


def _normalize_assignment_quiz_keys(payload: shared.AssignmentCreatePayload, candidate_id: int) -> list[str]:
    raw_keys: list[str] = []
    if isinstance(payload.quiz_keys, list):
        raw_keys.extend(str(item or "").strip() for item in payload.quiz_keys)
    quiz_key = str(payload.quiz_key or "").strip()
    if quiz_key:
        raw_keys.append(quiz_key)
    if not any(raw_keys):
        try:
            job_description_rows = shared.deps.list_candidate_job_descriptions(int(candidate_id))
        except Exception:
            job_description_rows = []
        raw_keys.extend(shared._candidate_default_quizzes_from_job_rows(job_description_rows))
    out: list[str] = []
    seen: set[str] = set()
    for item in raw_keys:
        key = str(item or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


@router.get("/assignments")
def get_assignments(
    request: Request,
    q: str = "",
    assignment_status: str = Query("", alias="status"),
    handled: str = "",
    quiz_key: str = "",
    start_from: str = "",
    start_to: str = "",
    end_from: str = "",
    end_to: str = "",
    page: int = 1,
):
    shared._require_admin(request)
    per_page = 20
    invite_start_from = str(start_from or "").strip() or None
    invite_start_to = str(start_to or "").strip() or None
    invite_end_from = str(end_from or "").strip() or None
    invite_end_to = str(end_to or "").strip() or None
    status_filter = shared.validation_helpers._normalize_exam_status(assignment_status)
    if status_filter not in {"invited", "verified", "in_quiz", "grading", "finished", "expired"}:
        status_filter = ""
    handled_filter = str(handled or "").strip().lower()
    if handled_filter not in {"handled", "unhandled"}:
        handled_filter = ""
    quiz_key_filter = str(quiz_key or "").strip() or None
    total = shared.deps.count_quiz_papers(
        query=q or None,
        quiz_key=quiz_key_filter,
        status_filter=status_filter or None,
        handled_filter=handled_filter or None,
        invite_start_from=invite_start_from,
        invite_start_to=invite_start_to,
        invite_end_from=invite_end_from,
        invite_end_to=invite_end_to,
    )
    unhandled_finished_count = shared.deps.count_unhandled_finished_quiz_papers(
        query=q or None,
        quiz_key=quiz_key_filter,
        status_filter=status_filter or None,
        handled_filter=handled_filter or None,
        invite_start_from=invite_start_from,
        invite_start_to=invite_start_to,
        invite_end_from=invite_end_from,
        invite_end_to=invite_end_to,
    )
    total_pages = max(1, (total + per_page - 1) // per_page)
    current_page = max(1, min(int(page or 1), total_pages))
    offset = (current_page - 1) * per_page
    rows = shared.deps.list_quiz_papers(
        query=q or None,
        quiz_key=quiz_key_filter,
        status_filter=status_filter or None,
        handled_filter=handled_filter or None,
        invite_start_from=invite_start_from,
        invite_start_to=invite_start_to,
        invite_end_from=invite_end_from,
        invite_end_to=invite_end_to,
        limit=per_page,
        offset=offset,
    )
    return {
        "items": [shared._serialize_assignment_row(row, request=request) for row in rows],
        "summary": {
            "unhandled_finished_count": int(unhandled_finished_count or 0),
        },
        "page": current_page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "filters": {
            "q": str(q or "").strip(),
            "status": status_filter,
            "handled": handled_filter,
            "quiz_key": str(quiz_key_filter or ""),
            "start_from": start_from,
            "start_to": start_to,
            "end_from": end_from,
            "end_to": end_to,
        },
    }


@router.post("/assignments", status_code=status.HTTP_201_CREATED)
def create_assignment(payload: shared.AssignmentCreatePayload, request: Request):
    shared._require_admin(request)
    candidate = shared.deps.get_candidate(int(payload.candidate_id))
    if not candidate:
        raise shared.HTTPException(status_code=404, detail="候选人不存在")
    quiz_keys = _normalize_assignment_quiz_keys(payload, int(payload.candidate_id))
    if not quiz_keys:
        raise shared.HTTPException(status_code=400, detail="缺少 quiz_key")
    start_date = shared._parse_date_ymd(payload.invite_start_date)
    end_date = shared._parse_date_ymd(payload.invite_end_date)
    if start_date is None or end_date is None:
        raise shared.HTTPException(status_code=400, detail="答题日期无效")
    if end_date < start_date:
        raise shared.HTTPException(status_code=400, detail="答题结束日期不能早于开始日期")
    ignore_timing = bool(payload.ignore_timing)

    quiz_specs: list[dict[str, Any]] = []
    for quiz_key in quiz_keys:
        exam = shared.deps.get_quiz_definition(quiz_key)
        quiz_version_id = shared.exam_helpers.resolve_quiz_version_id_for_new_assignment(quiz_key)
        if not exam or not quiz_version_id:
            raise shared.HTTPException(status_code=400, detail=f"测验不可用：{quiz_key}")
        public_spec = exam.get("public_spec") if isinstance(exam.get("public_spec"), dict) else {}
        time_limit_seconds = 0 if ignore_timing else shared.exam_helpers.compute_quiz_time_limit_seconds(public_spec)
        if not ignore_timing and time_limit_seconds <= 0:
            raise shared.HTTPException(status_code=400, detail=f"测验缺少有效的题目答题时长配置：{quiz_key}")
        quiz_specs.append(
            {
                "quiz_key": quiz_key,
                "quiz_version_id": int(quiz_version_id),
                "time_limit_seconds": int(time_limit_seconds),
            }
        )

    items: list[dict[str, Any]] = []
    for spec in quiz_specs:
        quiz_key = str(spec["quiz_key"])
        quiz_version_id = int(spec["quiz_version_id"])
        time_limit_seconds = int(spec["time_limit_seconds"])
        result = shared.deps.create_assignment(
            quiz_key=quiz_key,
            candidate_id=int(payload.candidate_id),
            quiz_version_id=quiz_version_id,
            base_url=shared._admin_base_url(request),
            phone=str(candidate.get("phone") or ""),
            invite_start_date=start_date.isoformat(),
            invite_end_date=end_date.isoformat(),
            time_limit_seconds=time_limit_seconds,
            min_submit_seconds=0,
            require_phone_verification=bool(payload.require_phone_verification),
            ignore_timing=ignore_timing,
            verify_max_attempts=int(payload.verify_max_attempts or 3),
        )
        token = str(result.get("token") or "").strip()
        try:
            shared.deps.create_quiz_paper(
                candidate_id=int(payload.candidate_id),
                phone=str(candidate.get("phone") or ""),
                quiz_key=quiz_key,
                quiz_version_id=quiz_version_id,
                token=token,
                source_kind="direct",
                invite_start_date=start_date.isoformat(),
                invite_end_date=end_date.isoformat(),
                status="invited",
            )
        except Exception:
            shared.deps.logger.exception("Create quiz_paper failed (candidate_id=%s, quiz_key=%s)", payload.candidate_id, quiz_key)
        try:
            shared.deps.log_event(
                "assignment.create",
                actor="admin",
                candidate_id=int(payload.candidate_id),
                quiz_key=quiz_key,
                token=token or None,
                meta={
                    "invite_start_date": start_date.isoformat(),
                    "invite_end_date": end_date.isoformat(),
                    "require_phone_verification": bool(payload.require_phone_verification),
                    "ignore_timing": ignore_timing,
                },
            )
        except Exception:
            pass
        items.append(
            {
                "quiz_key": quiz_key,
                "token": token,
                "url": result.get("url"),
                "qr_url": f"/api/admin/assignments/{token}/qr.png" if token else "",
            }
        )

    first = items[0] if items else {}
    return {
        "token": first.get("token", ""),
        "url": first.get("url", ""),
        "qr_url": first.get("qr_url", ""),
        "items": items,
        "created_count": len(items),
    }


@router.get("/assignments/{token}")
def get_assignment_detail(token: str, request: Request):
    shared._require_admin(request)
    try:
        return shared._serialize_attempt_detail(token, request=request)
    except FileNotFoundError as exc:
        raise shared.HTTPException(status_code=404, detail="答题记录不存在") from exc


@router.get("/attempts/{token}")
def get_attempt_detail(token: str, request: Request):
    return get_assignment_detail(token, request)


@router.get("/results/{token}")
def get_result_detail(token: str, request: Request):
    return get_assignment_detail(token, request)


@router.delete("/assignments/{token}")
def delete_assignment(token: str, request: Request):
    shared._require_admin(request)
    token_str = str(token or "").strip()
    if not token_str:
        raise shared.HTTPException(status_code=400, detail="缺少 token")
    quiz_paper = shared.deps.get_quiz_paper_by_token(token_str)
    try:
        assignment = shared.deps.load_assignment(token_str)
    except FileNotFoundError:
        assignment = {}
    if not quiz_paper and not assignment:
        raise shared.HTTPException(status_code=404, detail="邀约不存在")

    status_key = shared.validation_helpers._normalize_exam_status(
        str((quiz_paper or {}).get("status") or (assignment or {}).get("status") or "").strip()
    )

    deleted_quiz_archive = int(shared.deps.delete_quiz_archive_by_token(token_str) or 0)
    deleted_quiz_paper = int(shared.deps.delete_quiz_paper_by_token(token_str) or 0)
    deleted_assignment_record = int(shared.deps.delete_assignment_record(token_str) or 0)
    if deleted_quiz_archive <= 0 and deleted_quiz_paper <= 0 and deleted_assignment_record <= 0:
        raise shared.HTTPException(status_code=404, detail="邀约不存在")

    candidate_id = int((quiz_paper or {}).get("candidate_id") or (assignment or {}).get("candidate_id") or 0)
    quiz_key = str((quiz_paper or {}).get("quiz_key") or (assignment or {}).get("quiz_key") or "").strip()
    source_kind = str(
        (quiz_paper or {}).get("source_kind") or ("public" if (assignment or {}).get("public_invite") else "direct")
    ).strip() or "direct"
    try:
        shared.deps.log_event(
            "assignment.delete",
            actor="admin",
            candidate_id=(candidate_id or None),
            quiz_key=(quiz_key or None),
            token=token_str,
            meta={
                "source_kind": source_kind,
                "status": status_key,
                "deleted_quiz_archive": deleted_quiz_archive,
                "deleted_assignment_record": deleted_assignment_record,
                "deleted_quiz_paper": deleted_quiz_paper,
            },
        )
    except Exception:
        pass
    return {
        "ok": True,
        "deleted": {
            "quiz_archive": deleted_quiz_archive,
            "assignment_record": deleted_assignment_record,
            "quiz_paper": deleted_quiz_paper,
        },
    }


@router.post("/assignments/{token}/handling")
def set_assignment_handling(token: str, payload: shared.AssignmentHandlingPayload, request: Request):
    shared._require_admin(request)
    quiz_paper = shared.deps.get_quiz_paper_by_token(token)
    if not quiz_paper:
        raise shared.HTTPException(status_code=404, detail="答题记录不存在")
    status_key = shared.validation_helpers._normalize_exam_status(str(quiz_paper.get("status") or "").strip())
    if status_key != "finished":
        raise shared.HTTPException(status_code=409, detail="只有已判卷完成的记录才能标记处理状态")
    admin_username = str(request.session.get("admin_username") or "").strip() or "admin"
    shared.deps.set_quiz_paper_handling(
        token,
        handled=bool(payload.handled),
        handled_by=admin_username,
    )
    row = shared.deps.get_quiz_paper_admin_detail_by_token(token)
    if not row:
        raise shared.HTTPException(status_code=404, detail="答题记录不存在")
    return {
        "item": shared._serialize_assignment_row(row, request=request),
    }


@router.post("/assignments/{token}/suspected-ai-question")
def set_assignment_suspected_ai_question(
    token: str,
    payload: shared.AssignmentSuspectedAiQuestionPayload,
    request: Request,
):
    shared._require_admin(request)
    token_str = str(token or "").strip()
    qid = str(payload.qid or "").strip()
    quiz_paper = shared.deps.get_quiz_paper_by_token(token_str)
    if not quiz_paper:
        raise shared.HTTPException(status_code=404, detail="答题记录不存在")
    if not qid:
        raise shared.HTTPException(status_code=422, detail="缺少题目编号")

    if not _is_subjective_question(quiz_paper, qid):
        raise shared.HTTPException(status_code=422, detail="只能标记当前答题中的主观题")

    current_ids: list[str] = []
    seen_ids: set[str] = set()
    for value in quiz_paper.get("suspected_ai_question_ids") or []:
        current_qid = str(value or "").strip()
        if not current_qid or current_qid in seen_ids:
            continue
        seen_ids.add(current_qid)
        current_ids.append(current_qid)
    next_ids = [current_qid for current_qid in current_ids if current_qid != qid]
    if payload.suspected:
        next_ids.append(qid)
    shared.deps.set_quiz_paper_suspected_ai_question_ids(token_str, next_ids)
    return {
        "item": {
            "token": token_str,
            "suspected_ai_question_ids": next_ids,
            "suspected_ai_question_count": len(next_ids),
            "needs_attention": bool(
                str(quiz_paper.get("status") or "").strip().lower() == "finished"
                and not quiz_paper.get("handled_at")
            ),
        },
    }


@router.get("/assignments/{token}/qr.png")
def get_assignment_qr(token: str, request: Request):
    shared._require_admin(request)
    try:
        shared.deps.load_assignment(token)
    except Exception as exc:
        raise shared.HTTPException(status_code=404, detail="答题记录不存在") from exc
    try:
        import qrcode  # type: ignore
    except Exception as exc:
        raise shared.HTTPException(status_code=500, detail="二维码依赖不可用") from exc
    url = f"{shared._admin_base_url(request)}/t/{str(token or '').strip()}"
    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    try:
        image.save(buffer, format="PNG")
    except TypeError:
        image.save(buffer)
    return Response(content=buffer.getvalue(), media_type="image/png")


@router.get("/attempt-status")
def get_attempt_status(request: Request, tokens: str = ""):
    shared._require_admin(request)
    values = [item.strip() for item in str(tokens or "").split(",") if item.strip()][:50]
    items: list[dict[str, Any]] = []
    today_local = datetime.now().astimezone().date()
    for token in values:
        quiz_paper = shared.deps.get_quiz_paper_by_token(token) or {}
        if not quiz_paper:
            continue
        status_key = shared.validation_helpers._normalize_exam_status(quiz_paper.get("status"))
        invite_end_date = shared._iso_or_empty(quiz_paper.get("invite_end_date"))
        if status_key in {"invited", "verified"} and not quiz_paper.get("entered_at"):
            end_date = shared._parse_date_ymd(invite_end_date)
            if end_date is not None and today_local > end_date:
                status_key = "expired"
        items.append(
            {
                "token": token,
                "status": status_key,
                "status_label": shared._status_label(status_key),
                "score": quiz_paper.get("score"),
            }
        )
    return {"items": items}
