from __future__ import annotations

import hmac
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request, status
from pydantic import BaseModel, Field

from backend.md_quiz.services import candidate_resume_admin_service
from backend.md_quiz.services import exam_helpers, runtime_jobs, support_deps as deps
from backend.md_quiz.services.request_url_helpers import external_base_url
from backend.md_quiz.services import system_status_helpers, validation_helpers
from backend.md_quiz.services.exam_repo_sync_service import ExamRepoSyncError

router = APIRouter(prefix="/api/admin", tags=["admin"])

_LOG_CATEGORY_KEYS = ("candidate", "quiz", "grading", "assignment", "system")
_QUIZ_ANALYTICS_WINDOWS = {
    "week": {"days": 7, "label": "近 7 天"},
    "month": {"days": 30, "label": "近 30 天"},
    "half_year": {"days": 180, "label": "近 180 天"},
    "year": {"days": 365, "label": "近 365 天"},
    "custom": {"days": 0, "label": "自定义时间"},
}
_QUIZ_ANALYTICS_VERSION_SCOPES = {
    "all": "全部版本",
    "current": "当前版本",
}


class AdminLoginPayload(BaseModel):
    username: str
    password: str


class RuntimeConfigPatch(BaseModel):
    token_daily_threshold: int | None = Field(default=None, ge=0)
    sms_daily_threshold: int | None = Field(default=None, ge=0)
    allow_public_assignments: bool | None = None
    min_submit_seconds: int | None = Field(default=None, ge=0)
    ui_theme_name: str | None = None


class EnqueueJobPayload(BaseModel):
    kind: str
    payload: dict = Field(default_factory=dict)


class SyncExamPayload(BaseModel):
    repo_url: str = ""


class RepoBindingPayload(BaseModel):
    repo_url: str = ""


class RepoRebindPayload(BaseModel):
    repo_url: str = ""
    confirmation_text: str = ""


class PublicInviteTogglePayload(BaseModel):
    enabled: bool


class CandidateCreatePayload(BaseModel):
    name: str
    phone: str
    job_description_id: int | None = None


class CandidateEvaluationPayload(BaseModel):
    evaluation: str


class CandidateResumeEvaluationPayload(BaseModel):
    evaluation: str = ""
    job_match_score: Any = None


class CandidateJobDescriptionAddPayload(BaseModel):
    job_description_ids: list[int] = Field(default_factory=list)


class CandidateJobDescriptionRemovePayload(BaseModel):
    job_description_id: int


class AssignmentCreatePayload(BaseModel):
    quiz_key: str = ""
    quiz_keys: list[str] = Field(default_factory=list)
    candidate_id: int
    time_limit_seconds: int | str | None = None
    invite_start_date: str
    invite_end_date: str
    min_submit_seconds: int | None = None
    require_phone_verification: bool = False
    ignore_timing: bool = False
    verify_max_attempts: int = 3


class AssignmentHandlingPayload(BaseModel):
    handled: bool


class AssignmentSuspectedAiQuestionPayload(BaseModel):
    qid: str
    suspected: bool


def _require_admin(request: Request) -> None:
    if not request.session.get("admin_logged_in"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要先登录后台")


def _status_label(status_key: str) -> str:
    mapping = {
        "verified": "验证通过",
        "invited": "已邀约",
        "in_quiz": "正在答题",
        "grading": "正在判卷",
        "finished": "判卷结束",
        "expired": "失效",
    }
    return mapping.get(str(status_key or "").strip(), "未知")


def _source_label(source_kind: str) -> str:
    return "公开邀约" if str(source_kind or "").strip() == "public" else "主动邀约"


def _parse_date_ymd(value: str) -> date | None:
    return validation_helpers._parse_date_ymd(value)


def _iso_or_empty(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value or "").strip()


def _iso_to_local_display(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except Exception:
        return raw


def _parse_log_datetime(value: Any) -> datetime | None:
    raw = _iso_or_empty(value)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _log_duration_display(seconds: Any) -> str:
    try:
        total = max(0, int(seconds))
    except Exception:
        return ""
    if total < 60:
        return f"{total}秒"
    minutes, second = divmod(total, 60)
    if minutes < 60:
        if second:
            return f"{minutes}分{second:02d}秒"
        return f"{minutes}分"
    hours, minute = divmod(minutes, 60)
    return f"{hours}小时{minute:02d}分"


def _score_display(score: Any, score_max: Any, *, result_mode: str = "") -> str:
    mode = str(result_mode or "").strip().lower()
    if mode == "traits":
        return "-"
    try:
        scored = int(score)
    except Exception:
        return "-"
    try:
        max_value = int(score_max)
    except Exception:
        max_value = 0
    if max_value > 0:
        return f"{scored} / {max_value}"
    return str(scored)


def _coerce_int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _normalize_answer_keys(value: Any) -> list[str]:
    if isinstance(value, list):
        items = value
    elif value is None:
        items = []
    else:
        items = [value]
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = str(item or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    return normalized


def _has_trait_options(options: list[dict[str, Any]]) -> bool:
    return any(isinstance((option or {}).get("traits"), dict) and (option or {}).get("traits") for option in options)


def _normalize_review_options(raw_options: Any, *, spec_question: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    current_options = [dict(option) for option in (raw_options or []) if isinstance(option, dict)]
    current_by_key = {str(option.get("key") or "").strip(): option for option in current_options if str(option.get("key") or "").strip()}
    spec_options = [dict(option) for option in ((spec_question or {}).get("options") or []) if isinstance(option, dict)]
    if spec_options:
        merged: list[dict[str, Any]] = []
        for spec_option in spec_options:
            key = str(spec_option.get("key") or "").strip()
            current_option = current_by_key.get(key) or {}
            item: dict[str, Any] = {
                "key": current_option.get("key") or spec_option.get("key"),
                "text": current_option.get("text") or spec_option.get("text") or "",
            }
            if "correct" in current_option or "correct" in spec_option:
                item["correct"] = bool(current_option.get("correct")) or bool(spec_option.get("correct"))
            traits = current_option.get("traits")
            if not isinstance(traits, dict) or not traits:
                traits = spec_option.get("traits")
            if isinstance(traits, dict) and traits:
                item["traits"] = dict(traits)
            merged.append(item)
        return merged

    normalized: list[dict[str, Any]] = []
    for option in current_options:
        item = {
            "key": option.get("key"),
            "text": option.get("text") or "",
        }
        if "correct" in option:
            item["correct"] = bool(option.get("correct"))
        traits = option.get("traits")
        if isinstance(traits, dict) and traits:
            item["traits"] = dict(traits)
        normalized.append(item)
    return normalized


def _review_question_kind(question_type: str, options: list[dict[str, Any]]) -> str:
    qtype = str(question_type or "").strip().lower()
    if qtype == "short":
        return "short"
    if qtype in {"single", "multiple"} and _has_trait_options(options):
        return "traits"
    if qtype in {"single", "multiple"}:
        return "objective"
    return "unknown"


def _build_review_answer_item(
    raw_question: dict[str, Any],
    *,
    spec_question: dict[str, Any] | None = None,
    public_question: dict[str, Any] | None = None,
) -> dict[str, Any]:
    qid = str(raw_question.get("qid") or (spec_question or {}).get("qid") or "").strip()
    qtype = str(raw_question.get("type") or (spec_question or {}).get("type") or (public_question or {}).get("type") or "").strip()
    options = _normalize_review_options(raw_question.get("options"), spec_question=spec_question)
    review_kind = _review_question_kind(qtype, options)
    score = _coerce_int_or_none(raw_question.get("score"))
    max_points = _coerce_int_or_none(
        raw_question.get("max_points")
        or (spec_question or {}).get("max_points")
        or (spec_question or {}).get("points")
        or (public_question or {}).get("max_points")
        or (public_question or {}).get("points")
    )
    score_max = _coerce_int_or_none(raw_question.get("score_max"))
    if score_max is None:
        score_max = max_points
    rubric = str(raw_question.get("rubric") or (spec_question or {}).get("rubric") or "").strip()
    stem_md = str(
        raw_question.get("stem_md")
        or (public_question or {}).get("stem_md")
        or (spec_question or {}).get("stem_md")
        or ""
    )
    display_question = exam_helpers.build_render_ready_question(
        {
            "stem_md": stem_md,
            "options": options,
            "rubric": rubric,
        },
        include_rubric_html=True,
    )
    stem_html = str(raw_question.get("stem_html") or "").strip() or str(display_question.get("stem_html") or "")
    options = display_question.get("options") if isinstance(display_question.get("options"), list) else options
    rubric_html = str(raw_question.get("rubric_html") or "").strip() or str(display_question.get("rubric_html") or "")
    answer = raw_question.get("answer")
    selected_options = _normalize_answer_keys(answer) if qtype in {"single", "multiple"} else []
    correct_options = [
        str(option.get("key") or "").strip()
        for option in options
        if bool(option.get("correct")) and str(option.get("key") or "").strip()
    ]
    has_answer = bool(str(answer or "").strip()) if qtype == "short" else bool(selected_options)
    is_correct: bool | None = None
    is_partial = False
    if review_kind == "objective" and has_answer:
        is_correct = set(selected_options) == set(correct_options)
        is_partial = not bool(is_correct) and score is not None and int(score or 0) > 0 and int(score or 0) < int(score_max or 0)
    score_display = _score_display(score, score_max, result_mode="scored") if review_kind != "traits" and score is not None else ""
    return {
        "qid": qid,
        "label": raw_question.get("label") or (spec_question or {}).get("label") or (public_question or {}).get("label") or qid,
        "type": qtype,
        "review_kind": review_kind,
        "is_trait_question": review_kind == "traits",
        "max_points": max_points,
        "score": score,
        "score_max": score_max,
        "has_score": review_kind != "traits" and score is not None,
        "score_display": score_display,
        "stem_md": stem_md,
        "stem_html": stem_html,
        "answer": answer,
        "has_answer": has_answer,
        "options": options,
        "correct_options": correct_options,
        "selected_options": selected_options,
        "is_correct": is_correct,
        "is_partial": is_partial,
        "reason": str(raw_question.get("reason") or "").strip(),
        "rubric": rubric,
        "rubric_html": rubric_html,
    }


def _build_grading_details_by_qid(grading: Any) -> dict[str, dict[str, Any]]:
    details: dict[str, dict[str, Any]] = {}
    current = grading if isinstance(grading, dict) else {}
    for item in (current.get("objective") or []):
        qid = str((item or {}).get("qid") or "").strip()
        if qid:
            details[qid] = dict(item)
    for item in (current.get("subjective") or []):
        qid = str((item or {}).get("qid") or "").strip()
        if qid:
            details[qid] = dict(item)
    return details


def _resolve_attempt_snapshot(
    assignment: dict[str, Any] | None,
    archive: dict[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(assignment, dict) and assignment:
        snapshot = exam_helpers.get_exam_snapshot_for_assignment(assignment)
        if isinstance(snapshot, dict) and snapshot:
            return snapshot
    exam = (archive or {}).get("exam") or {}
    try:
        quiz_version_id = int(exam.get("quiz_version_id") or 0)
    except Exception:
        quiz_version_id = 0
    if quiz_version_id > 0:
        snapshot = exam_helpers.get_quiz_version_snapshot(quiz_version_id)
        if isinstance(snapshot, dict) and snapshot:
            return snapshot
    quiz_key = str(exam.get("quiz_key") or "").strip()
    if quiz_key:
        snapshot = deps.get_quiz_definition(quiz_key) or {}
        if isinstance(snapshot, dict):
            return snapshot
    return {}


def _build_review_answers(
    *,
    archive: dict[str, Any] | None,
    assignment: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    snapshot = _resolve_attempt_snapshot(assignment, archive)
    spec_questions = [dict(question) for question in ((snapshot.get("spec") or {}).get("questions") or []) if isinstance(question, dict)]
    spec_by_qid = {str(question.get("qid") or "").strip(): question for question in spec_questions if str(question.get("qid") or "").strip()}
    public_by_qid = {
        str(question.get("qid") or "").strip(): question
        for question in (((snapshot.get("public_spec") or {}).get("questions") or []))
        if isinstance(question, dict) and str(question.get("qid") or "").strip()
    }
    if isinstance(archive, dict) and isinstance(archive.get("questions"), list) and archive.get("questions"):
        answers: list[dict[str, Any]] = []
        for raw_question in archive.get("questions") or []:
            if not isinstance(raw_question, dict):
                continue
            qid = str(raw_question.get("qid") or "").strip()
            answers.append(
                _build_review_answer_item(
                    dict(raw_question),
                    spec_question=spec_by_qid.get(qid),
                    public_question=public_by_qid.get(qid),
                )
            )
        return answers

    if not isinstance(assignment, dict) or not spec_questions:
        return []

    grading = assignment.get("grading") or {}
    scored_by_qid = _build_grading_details_by_qid(grading)
    assignment_answers = assignment.get("answers") or {}
    answers: list[dict[str, Any]] = []
    for spec_question in spec_questions:
        qid = str(spec_question.get("qid") or "").strip()
        score_detail = scored_by_qid.get(qid) or {}
        raw_question = {
            "qid": qid,
            "label": spec_question.get("label") or qid,
            "type": spec_question.get("type"),
            "max_points": spec_question.get("max_points") or spec_question.get("points"),
            "stem_md": (public_by_qid.get(qid) or {}).get("stem_md") or spec_question.get("stem_md"),
            "options": spec_question.get("options"),
            "rubric": spec_question.get("rubric"),
            "answer": assignment_answers.get(qid),
            "score": score_detail.get("score"),
            "score_max": score_detail.get("max") or spec_question.get("max_points") or spec_question.get("points"),
            "reason": score_detail.get("reason"),
        }
        answers.append(
            _build_review_answer_item(
                raw_question,
                spec_question=spec_question,
                public_question=public_by_qid.get(qid),
            )
        )
    return answers


def _result_mode_label(result_mode: str) -> str:
    mapping = {
        "scored": "计分题",
        "traits": "量表题",
        "mixed": "计分 + 量表",
    }
    key = str(result_mode or "").strip().lower()
    return mapping.get(key, "未定义")


def _build_review_evaluation(
    *,
    archive: dict[str, Any] | None,
    assignment: dict[str, Any] | None,
) -> dict[str, Any]:
    archive_data = archive if isinstance(archive, dict) else {}
    assignment_data = assignment if isinstance(assignment, dict) else {}
    grading = archive_data.get("grading") or assignment_data.get("grading") or {}
    raw_total = _coerce_int_or_none((grading or {}).get("raw_total")) if isinstance(grading, dict) else None
    result_mode = str(
        archive_data.get("result_mode")
        or (grading.get("result_mode") if isinstance(grading, dict) else "")
        or ("traits" if isinstance(grading, dict) and raw_total == 0 and grading else "")
    ).strip().lower()
    total_score = _coerce_int_or_none(archive_data.get("total_score"))
    if total_score is None and isinstance(grading, dict):
        total_score = _coerce_int_or_none(grading.get("total"))
    score_max = _coerce_int_or_none(archive_data.get("score_max"))
    if score_max is None and isinstance(grading, dict):
        score_max = _coerce_int_or_none(grading.get("total_max"))
    traits = archive_data.get("traits")
    if not isinstance(traits, dict) or not traits:
        traits = (grading.get("traits") or grading.get("trait_result") or {}) if isinstance(grading, dict) else {}
    traits = dict(traits or {})
    has_score = result_mode != "traits" and total_score is not None
    return {
        "result_mode": result_mode,
        "result_mode_label": _result_mode_label(result_mode),
        "total_score": total_score,
        "score_max": score_max,
        "has_score": has_score,
        "score_display": _score_display(total_score, score_max, result_mode=result_mode) if has_score else "",
        "final_analysis": str(
            archive_data.get("final_analysis")
            or (grading.get("final_analysis") if isinstance(grading, dict) else "")
            or (grading.get("analysis") if isinstance(grading, dict) else "")
            or ""
        ).strip(),
        "candidate_remark": str(archive_data.get("candidate_remark") or assignment_data.get("candidate_remark") or "").strip(),
        "traits": traits,
        "primary_dimensions": list(traits.get("primary_dimensions") or []),
        "paired_dimensions": list(traits.get("paired_dimensions") or []),
        "dimension_list": list(traits.get("dimension_list") or []),
    }


def _build_attempt_review(
    *,
    archive: dict[str, Any] | None,
    assignment: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "answers": _build_review_answers(archive=archive, assignment=assignment),
        "evaluation": _build_review_evaluation(archive=archive, assignment=assignment),
    }


def _looks_deleted_marker(value: str) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    if text in {"已删除", "deleted", "????", "???", "null", "none", "历史测验"}:
        return True
    if "?" in text and len(text) <= 12:
        return True
    return "删除" in text and len(text) <= 8


def _compute_exam_stats(spec: dict[str, Any]) -> dict[str, Any]:
    questions = list(spec.get("questions") or [])
    counts_by_type: dict[str, int] = {}
    points_by_type: dict[str, int] = {}
    total_points = 0
    for question in questions:
        qtype = str(question.get("type") or "").strip() or "unknown"
        counts_by_type[qtype] = int(counts_by_type.get(qtype, 0)) + 1
        try:
            points = int(question.get("max_points") or 0)
        except Exception:
            points = 0
        points_by_type[qtype] = int(points_by_type.get(qtype, 0)) + points
        total_points += points
    return {
        "total_questions": len(questions),
        "total_points": int(total_points),
        "counts_by_type": counts_by_type,
        "points_by_type": points_by_type,
    }


def _admin_base_url(request: Request) -> str:
    return external_base_url(request)


def _repo_sync_http_status(exc: Exception) -> int:
    message = str(exc or "")
    if any(text in message for text in ("已绑定仓库", "尚未绑定仓库", "同步任务在执行")):
        return status.HTTP_409_CONFLICT
    return status.HTTP_400_BAD_REQUEST


def _serialize_repo_binding(binding: dict[str, Any] | None) -> dict[str, Any]:
    current = binding if isinstance(binding, dict) else {}
    repo_url = str(current.get("repo_url") or "").strip()
    if not repo_url:
        return {}
    return {
        "repo_url": repo_url,
        "bound_at": str(current.get("bound_at") or "").strip(),
        "updated_at": str(current.get("updated_at") or "").strip(),
    }


def _serialize_exam_summary(exam: dict[str, Any], request: Request) -> dict[str, Any]:
    quiz_key = str(exam.get("quiz_key") or "").strip()
    cfg = exam_helpers.get_public_invite_config(quiz_key)
    public_token = str(cfg.get("token") or "").strip()
    question_count = int(exam.get("question_count") or exam.get("count") or 0)
    question_counts = exam.get("question_counts") if isinstance(exam.get("question_counts"), dict) else {}
    tags = exam.get("tags") if isinstance(exam.get("tags"), list) else []
    trait = exam.get("trait") if isinstance(exam.get("trait"), dict) else {}
    return {
        "id": int(exam.get("id") or 0),
        "quiz_key": quiz_key,
        "title": str(exam.get("title") or "").strip(),
        "description": str(exam.get("description") or "").strip(),
        "status": str(exam.get("status") or "").strip() or "active",
        "question_count": question_count,
        "question_counts": question_counts,
        "estimated_duration_minutes": int(exam.get("estimated_duration_minutes") or 0),
        "tags": tags,
        "schema_version": exam.get("schema_version"),
        "format": str(exam.get("format") or "").strip(),
        "trait": trait,
        "current_quiz_version_id": int(exam.get("current_version_id") or 0),
        "current_version_no": int(exam.get("current_version_no") or 0),
        "source_path": str(exam.get("source_path") or "").strip(),
        "last_sync_error": str(exam.get("last_sync_error") or "").strip(),
        "updated_at": _iso_or_empty(exam.get("updated_at")),
        "respondent_count": int(exam.get("respondent_count") or 0),
        "public_invite_enabled": bool(cfg.get("enabled")),
        "public_invite_token": public_token,
        "public_invite_url": (
            f"{_admin_base_url(request)}/p/{public_token}"
            if bool(cfg.get("enabled")) and public_token
            else ""
        ),
        "public_invite_qr_url": (
            f"/api/public/invites/{public_token}/qr.png"
            if bool(cfg.get("enabled")) and public_token
            else ""
        ),
    }


def _serialize_exam_detail(
    exam: dict[str, Any],
    *,
    request: Request,
    selected_version: dict[str, Any] | None = None,
) -> dict[str, Any]:
    quiz_key = str(exam.get("quiz_key") or "").strip()
    current_version_id = int(exam.get("current_version_id") or 0)
    selected = selected_version or {}
    selected_version_id = int(selected.get("id") or 0)
    raw_spec = selected.get("spec") if isinstance(selected.get("spec"), dict) else exam.get("spec") or {}
    spec = exam_helpers.build_render_ready_public_spec(
        raw_spec if isinstance(raw_spec, dict) else {},
        include_rubric_html=True,
    )
    quiz_metadata = exam_helpers.build_quiz_metadata(spec)
    stats = _compute_exam_stats(spec if isinstance(spec, dict) else {})
    cfg = exam_helpers.get_public_invite_config(quiz_key)
    public_token = str(cfg.get("token") or "").strip()
    versions: list[dict[str, Any]] = []
    for item in deps.list_quiz_versions(quiz_key):
        version_id = int(item.get("id") or 0)
        versions.append(
            {
                "id": version_id,
                "version_no": int(item.get("version_no") or 0),
                "git_commit": str(item.get("git_commit") or "").strip(),
                "source_path": str(item.get("source_path") or "").strip(),
                "created_at": _iso_or_empty(item.get("created_at")),
                "is_current": bool(current_version_id and current_version_id == version_id),
                "is_selected": bool(selected_version_id and selected_version_id == version_id),
            }
        )
    return {
        "quiz": {
            "id": int(exam_helpers._sort_id_from_quiz_key(quiz_key) or 0),
            "quiz_key": quiz_key,
            "title": str((spec or {}).get("title") or exam.get("title") or "").strip(),
            "description": str((spec or {}).get("description") or "").strip(),
            "status": str(exam.get("status") or "").strip() or "active",
            "tags": list(quiz_metadata["tags"]),
            "schema_version": quiz_metadata["schema_version"],
            "format": str(quiz_metadata["format"] or "").strip(),
            "question_count": int(quiz_metadata["question_count"]),
            "question_counts": dict(quiz_metadata["question_counts"]),
            "estimated_duration_minutes": int(quiz_metadata["estimated_duration_minutes"]),
            "trait": dict(quiz_metadata["trait"]),
            "current_quiz_version_id": current_version_id,
            "current_version_no": int(exam.get("current_version_no") or 0),
            "source_path": str(exam.get("source_path") or "").strip(),
            "last_synced_commit": str(exam.get("last_synced_commit") or "").strip(),
            "last_sync_error": str(exam.get("last_sync_error") or "").strip(),
            "public_invite_enabled": bool(cfg.get("enabled")),
            "public_invite_token": public_token,
            "public_invite_url": (
                f"{_admin_base_url(request)}/p/{public_token}"
                if bool(cfg.get("enabled")) and public_token
                else ""
            ),
            "public_invite_qr_url": (
                f"/api/public/invites/{public_token}/qr.png"
                if bool(cfg.get("enabled")) and public_token
                else ""
            ),
        },
        "selected_quiz_version": {
            "id": selected_version_id,
            "version_no": int(selected.get("version_no") or 0),
            "git_commit": str(selected.get("git_commit") or "").strip(),
            "source_path": str(selected.get("source_path") or "").strip(),
            "tags": list(quiz_metadata["tags"]),
            "schema_version": quiz_metadata["schema_version"],
            "format": str(quiz_metadata["format"] or "").strip(),
            "question_count": int(quiz_metadata["question_count"]),
            "question_counts": dict(quiz_metadata["question_counts"]),
            "estimated_duration_minutes": int(quiz_metadata["estimated_duration_minutes"]),
            "trait": dict(quiz_metadata["trait"]),
            "spec": spec if isinstance(spec, dict) else {},
        },
        "quiz_version_history": versions,
        "stats": stats,
        "sync_state": deps.read_exam_repo_sync_state(),
    }


def _parse_candidate_query_dates(raw: str, *, end_of_day: bool = False) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if text.count("-") == 2 and "T" not in text and " " not in text:
        if end_of_day:
            return parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
        return parsed.replace(hour=0, minute=0, second=0, microsecond=0)
    return parsed


def _candidate_attempt_results(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    phone = str(candidate.get("phone") or "").strip()
    if not phone:
        return []
    best_by_key: dict[str, dict[str, Any]] = {}
    rows = deps.list_quiz_archives_for_phone(phone)
    for row in rows:
        archive = row.get("archive") if isinstance(row, dict) else None
        if not isinstance(archive, dict):
            continue
        token = str(archive.get("token") or "").strip()
        timing = archive.get("timing") or {}
        if not isinstance(timing, dict):
            timing = {}
        start_at = str(timing.get("start_at") or "").strip()
        end_at = str(timing.get("end_at") or "").strip()
        score = archive.get("total_score")
        score_max = archive.get("score_max")
        grading = archive.get("grading") if isinstance(archive.get("grading"), dict) else {}
        result_mode = str(
            archive.get("result_mode")
            or grading.get("result_mode")
            or ("traits" if int(archive.get("raw_total") or 0) <= 0 else "scored")
        ).strip()
        if not end_at or (score is None and result_mode != "traits"):
            continue
        exam = archive.get("exam") or {}
        if not isinstance(exam, dict):
            exam = {}
        quiz_key = str(exam.get("quiz_key") or "").strip()
        quiz_name = str(exam.get("title") or "").strip() or quiz_key or "未知测验"
        sort_key = 0.0
        try:
            sort_key = datetime.fromisoformat(end_at.replace("Z", "+00:00")).timestamp()
        except Exception:
            updated_at = row.get("updated_at")
            try:
                sort_key = float(updated_at.timestamp()) if updated_at else 0.0
            except Exception:
                sort_key = 0.0
        dedupe_key = f"{token}::{quiz_key}" if token else str(row.get("archive_name") or "")
        current = best_by_key.get(dedupe_key)
        if current is None or float(sort_key) >= float(current.get("_sort_key") or 0.0):
            best_by_key[dedupe_key] = {
                "token": token,
                "quiz_name": quiz_name,
                "score": score,
                "score_max": score_max,
                "score_display": _score_display(score, score_max, result_mode=result_mode),
                "result_mode": result_mode,
                "start_at": start_at,
                "end_at": end_at,
                "_sort_key": sort_key,
                "_archive_name": str(row.get("archive_name") or ""),
            }
    items = list(best_by_key.values())
    items.sort(key=lambda item: float(item.get("_sort_key") or 0.0))
    out: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        out.append(
            {
                "no": index,
                "token": str(item.get("token") or "").strip(),
                "quiz_name": str(item.get("quiz_name") or "").strip(),
                "score": item.get("score"),
                "score_display": str(item.get("score_display") or "").strip(),
                "start_at": _iso_to_local_display(str(item.get("start_at") or "")),
                "end_at": _iso_to_local_display(str(item.get("end_at") or "")),
                "archive_name": str(item.get("_archive_name") or "").strip(),
            }
        )
    return out


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


def _candidate_default_quiz_from_job_rows(rows: list[dict[str, Any]]) -> str:
    keys = _candidate_default_quizzes_from_job_rows(rows)
    return keys[0] if keys else ""


def _candidate_default_quizzes_from_job_rows(rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if str(row.get("status") or "").strip().lower() != "active":
            continue
        for quiz_key in _normalize_related_quizzes(row.get("related_quizzes")):
            if quiz_key in seen:
                continue
            seen.add(quiz_key)
            out.append(quiz_key)
    return out


def _serialize_candidate_detail(candidate_id: int, candidate: dict[str, Any]) -> dict[str, Any]:
    parsed = candidate.get("resume_parsed") or {}
    if not isinstance(parsed, dict):
        parsed = {}
    details = parsed.get("details") or {}
    if not isinstance(details, dict):
        details = {}
    details_data = details.get("data") or {}
    if not isinstance(details_data, dict):
        details_data = {}

    def _degree_rank(value: str) -> int:
        return {"高中": 1, "大专": 2, "本科": 3, "硕士": 4, "博士": 5}.get(
            str(value or "").strip(),
            0,
        )

    educations = details_data.get("educations") or []
    if not isinstance(educations, list):
        educations = []
    education_rows = [item for item in educations if isinstance(item, dict)]
    highest = ""
    highest_rank = 0
    for item in education_rows:
        degree = str(item.get("degree") or "").strip()
        rank = _degree_rank(degree)
        if rank > highest_rank:
            highest_rank = rank
            highest = degree
    if not highest:
        highest = str(details_data.get("highest_education") or "").strip()
        highest_rank = _degree_rank(highest)
    education_view: list[dict[str, Any]] = []
    for item in education_rows:
        degree = str(item.get("degree") or "").strip()
        rank = _degree_rank(degree)
        if highest_rank <= 0 or (rank > 0 and rank <= highest_rank):
            education_view.append(dict(item))
    if not education_view:
        education_view = [dict(item) for item in education_rows]
    education_view.sort(
        key=lambda item: (
            _degree_rank(str(item.get("degree") or "")),
            str(item.get("start") or ""),
            str(item.get("end") or ""),
        )
    )
    for item in education_view:
        try:
            tag, label = deps.classify_university(str(item.get("school") or ""))
        except Exception:
            tag, label = "", ""
        item["school_tag"] = tag
        item["school_tag_label"] = label

    projects = details_data.get("projects") or []
    if not isinstance(projects, list):
        projects = []
    project_rows = [item for item in projects if isinstance(item, dict)]
    projects_raw = str(details_data.get("projects_raw") or "").strip()
    project_blocks = validation_helpers._split_projects_raw(projects_raw) if projects_raw else []
    llm_blocks = details_data.get("experience_blocks") or []
    if isinstance(llm_blocks, list) and any(isinstance(item, dict) for item in llm_blocks):
        experience_blocks = [item for item in llm_blocks if isinstance(item, dict)]
    else:
        experience_blocks = project_blocks
    uniq_blocks: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for block in experience_blocks:
        title = str(block.get("title") or "").strip()
        period = str(block.get("period") or "").strip()
        body = str(block.get("body") or "").strip()
        signature = (title, period, body[:120])
        if not title and not body:
            continue
        if signature in seen:
            continue
        seen.add(signature)
        uniq_blocks.append(block)

    evaluation_llm = str(details_data.get("evaluation") or "").strip() or str(
        details_data.get("summary") or ""
    ).strip()
    evaluation_source = str(details_data.get("evaluation_source") or "").strip().lower()
    if evaluation_source != "manual":
        evaluation_source = "system" if evaluation_llm else ""
    evaluation_source_label = {"manual": "手工修改", "system": "系统生成"}.get(evaluation_source, "")
    evaluation_updated_at = _iso_or_empty(details_data.get("evaluation_updated_at"))
    job_match_score = _coerce_int_or_none(details_data.get("job_match_score"))
    if job_match_score is not None:
        job_match_score = max(0, min(100, job_match_score))
    raw_admin_evaluations = details_data.get("admin_evaluations")
    admin_evaluations: list[dict[str, Any]] = []
    if isinstance(raw_admin_evaluations, list):
        for item in raw_admin_evaluations:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            at = str(item.get("at") or "").strip()
            if not text:
                continue
            at_display = _iso_to_local_display(at) if at else ""
            admin_evaluations.append({"text": text, "at": at, "at_display": at_display})
    else:
        blob = str(details_data.get("admin_evaluation") or "").strip()
        if blob:
            for block in [item.strip() for item in re.split(r"\n\s*\n", blob) if item.strip()]:
                lines = [line.rstrip() for line in block.splitlines()]
                at = ""
                if lines and re.match(r"^\[(.+?)\]\s*$", lines[0].strip()):
                    match = re.match(r"^\[(.+?)\]\s*$", lines[0].strip())
                    at = str(match.group(1) if match else "").strip()
                    lines = lines[1:]
                text = "\n".join(lines).strip()
                if not text:
                    continue
                admin_evaluations.append({"text": text, "at": at, "at_display": at})

    email = ""
    emails = details_data.get("emails") or []
    if isinstance(emails, list) and emails:
        email = str(emails[0] or "").strip()

    job_status_labels = {
        "draft": "草稿",
        "active": "启用",
        "archived": "归档",
    }
    try:
        job_description_rows = deps.list_candidate_job_descriptions(int(candidate_id))
    except Exception:
        job_description_rows = []
    job_descriptions = [
        {
            "id": int(item.get("id") or 0),
            "title": str(item.get("title") or "").strip(),
            "status": str(item.get("status") or "").strip().lower(),
            "status_label": job_status_labels.get(str(item.get("status") or "").strip().lower(), str(item.get("status") or "").strip()),
            "related_quizzes": _normalize_related_quizzes(item.get("related_quizzes")),
            "linked_at": _iso_or_empty(item.get("linked_at")),
        }
        for item in job_description_rows
        if int(item.get("id") or 0) > 0
    ]
    default_quiz_keys = _candidate_default_quizzes_from_job_rows(job_description_rows)
    default_quiz_key = default_quiz_keys[0] if default_quiz_keys else ""

    return {
        "candidate": {
            "id": int(candidate.get("id") or candidate_id),
            "name": str(candidate.get("name") or "").strip(),
            "phone": str(candidate.get("phone") or "").strip(),
            "default_quiz_key": default_quiz_key,
            "default_quiz_keys": default_quiz_keys,
            "created_at": _iso_or_empty(candidate.get("created_at")),
            "deleted_at": _iso_or_empty(candidate.get("deleted_at")),
            "resume_filename": str(candidate.get("resume_filename") or "").strip(),
            "resume_mime": str(candidate.get("resume_mime") or "").strip(),
            "resume_size": candidate.get("resume_size"),
            "resume_parsed_at": _iso_or_empty(candidate.get("resume_parsed_at")),
        },
        "profile": {
            "gender": str(details_data.get("gender") or "").strip(),
            "email": email,
            "highest_education": str(details_data.get("highest_education") or "").strip(),
            "educations": education_view,
            "english": details_data.get("english") or {},
            "experience_blocks": uniq_blocks,
            "projects": project_rows,
            "projects_raw": projects_raw,
            "projects_raw_blocks": project_blocks,
            "evaluation_llm": evaluation_llm,
            "evaluation_source": evaluation_source,
            "evaluation_source_label": evaluation_source_label,
            "evaluation_updated_at": evaluation_updated_at,
            "evaluation_updated_at_display": _iso_to_local_display(evaluation_updated_at) if evaluation_updated_at else "",
            "job_match_score": job_match_score,
            "admin_evaluations": admin_evaluations,
            "details_status": str(details.get("status") or "").strip(),
            "details_error": str(details.get("error") or "").strip(),
            "attempt_results": _candidate_attempt_results(candidate),
        },
        "job_descriptions": job_descriptions,
        "resume_parsed": parsed,
    }


def _serialize_assignment_row(row: dict[str, Any], *, request: Request) -> dict[str, Any]:
    token = str(row.get("token") or "").strip()
    status_key = validation_helpers._normalize_exam_status(str(row.get("status") or "").strip())
    invite_end_date = _iso_or_empty(row.get("invite_end_date"))
    if status_key in {"invited", "verified"} and not row.get("entered_at"):
        end_date = _parse_date_ymd(invite_end_date)
        if end_date is not None and datetime.now().astimezone().date() > end_date:
            status_key = "expired"
    candidate_id = int(row.get("candidate_id") or 0)
    candidate_name = str(row.get("name") or "").strip()
    candidate_deleted = bool(row.get("candidate_deleted_at"))
    if _looks_deleted_marker(candidate_name) or not candidate_name:
        candidate_name = f"候选人#{candidate_id}" if candidate_id > 0 else "候选人"
    if candidate_id > 0 and (not candidate_deleted) and candidate_name.startswith("候选人#"):
        try:
            recovered = str(deps.get_candidate_name_from_logs(candidate_id) or "").strip()
        except Exception:
            recovered = ""
        if recovered:
            candidate_name = recovered
    quiz_key = str(row.get("quiz_key") or "").strip()
    if _looks_deleted_marker(quiz_key):
        quiz_key = "历史测验"
    source_kind = "public" if str(row.get("source_kind") or "").strip() == "public" else "direct"
    require_phone_verification = bool(source_kind == "public")
    ignore_timing = False
    score = row.get("score")
    score_max = None
    result_mode = ""
    if token:
        try:
            assignment = deps.load_assignment(token)
        except Exception:
            assignment = None
        grading = (assignment or {}).get("grading") if isinstance((assignment or {}).get("grading"), dict) else {}
        score_max = grading.get("total_max")
        result_mode = str(
            grading.get("result_mode")
            or ("traits" if int(grading.get("raw_total") or 0) <= 0 and grading else "")
        ).strip()
        require_phone_verification = validation_helpers._require_phone_verification(assignment or row)
        ignore_timing = bool((assignment or row).get("ignore_timing"))
    handled_at = _iso_or_empty(row.get("handled_at"))
    handled_by = str(row.get("handled_by") or "").strip()
    raw_suspected_ai_question_ids = row.get("suspected_ai_question_ids")
    suspected_ai_question_ids: list[str] = []
    seen_suspected_ai_question_ids: set[str] = set()
    if isinstance(raw_suspected_ai_question_ids, list):
        for value in raw_suspected_ai_question_ids:
            qid = str(value or "").strip()
            if not qid or qid in seen_suspected_ai_question_ids:
                continue
            seen_suspected_ai_question_ids.add(qid)
            suspected_ai_question_ids.append(qid)
    needs_attention = bool(status_key == "finished" and not handled_at)
    return {
        "attempt_id": int(row.get("attempt_id") or 0),
        "candidate_id": candidate_id,
        "candidate_name": candidate_name,
        "candidate_deleted": candidate_deleted,
        "phone": str(row.get("phone") or "").strip(),
        "quiz_key": quiz_key,
        "quiz_version_id": int(row.get("quiz_version_id") or 0),
        "token": token,
        "source_kind": source_kind,
        "source_label": _source_label(source_kind),
        "invite_start_date": _iso_or_empty(row.get("invite_start_date")),
        "invite_end_date": invite_end_date,
        "require_phone_verification": require_phone_verification,
        "ignore_timing": ignore_timing,
        "status": status_key,
        "status_label": _status_label(status_key),
        "entered_at": _iso_or_empty(row.get("entered_at")),
        "finished_at": _iso_or_empty(row.get("finished_at")),
        "handled_at": handled_at,
        "handled_by": handled_by,
        "needs_attention": needs_attention,
        "suspected_ai_question_ids": suspected_ai_question_ids,
        "suspected_ai_question_count": len(suspected_ai_question_ids),
        "score": score,
        "score_max": score_max,
        "score_display": _score_display(score, score_max, result_mode=result_mode),
        "result_mode": result_mode,
        "created_at": _iso_or_empty(row.get("created_at")),
        "url": f"{_admin_base_url(request)}/t/{token}" if token else "",
        "qr_url": f"/api/admin/assignments/{token}/qr.png" if token else "",
    }


def _serialize_attempt_detail(token: str, *, request: Request) -> dict[str, Any]:
    quiz_paper_row = deps.get_quiz_paper_admin_detail_by_token(token)
    try:
        assignment = deps.load_assignment(token)
    except FileNotFoundError:
        assignment = {}
    if not assignment and not quiz_paper_row:
        raise FileNotFoundError(token)
    row = runtime_jobs._find_archive_by_token(token, assignment=assignment)
    archive = row.get("archive") if isinstance(row, dict) else None
    if isinstance(archive, dict):
        try:
            archive = runtime_jobs._augment_archive_with_spec(dict(archive))
        except Exception:
            archive = dict(archive)
    else:
        archive = None
    if isinstance(assignment, dict):
        assignment = dict(assignment)
        assignment["require_phone_verification"] = validation_helpers._require_phone_verification(assignment)
        assignment["ignore_timing"] = bool(assignment.get("ignore_timing"))
    return {
        "assignment": assignment,
        "quiz_paper": _serialize_assignment_row(quiz_paper_row, request=request) if quiz_paper_row else None,
        "archive": archive,
        "review": _build_attempt_review(archive=archive, assignment=assignment),
    }


def _serialize_log_row(row: dict[str, Any]) -> dict[str, Any]:
    event_type = str(row.get("event_type") or "").strip()
    type_key, type_label = system_status_helpers._oplog_type_label_v2(event_type)
    if type_key == "exam":
        type_key = "quiz"
    detail_text = system_status_helpers._oplog_detail_text_v2(dict(row))
    started_at = _iso_or_empty(row.get("started_at"))
    finished_at = _iso_or_empty(row.get("finished_at"))
    started_dt = _parse_log_datetime(started_at)
    finished_dt = _parse_log_datetime(finished_at)
    has_time_range = bool(started_dt and finished_dt)
    duration_seconds = _coerce_int_or_none(row.get("duration_seconds"))
    if duration_seconds is None and has_time_range:
        try:
            duration_seconds = max(0, int((finished_dt - started_dt).total_seconds()))
        except Exception:
            duration_seconds = None
    return {
        "id": int(row.get("id") or 0),
        "at": _iso_or_empty(row.get("at")),
        "at_display": _iso_to_local_display(_iso_or_empty(row.get("at"))),
        "started_at": started_at if started_dt else "",
        "started_at_display": _iso_to_local_display(started_at) if started_dt else "",
        "finished_at": finished_at if finished_dt else "",
        "finished_at_display": _iso_to_local_display(finished_at) if finished_dt else "",
        "actor": str(row.get("actor") or "").strip(),
        "event_type": event_type,
        "candidate_id": row.get("candidate_id"),
        "candidate_name": str(row.get("candidate_name") or "").strip(),
        "candidate_phone": system_status_helpers._oplog_mask_phone(str(row.get("candidate_phone") or "").strip()),
        "quiz_key": str(row.get("quiz_key") or "").strip(),
        "token": str(row.get("token") or "").strip(),
        "llm_total_tokens": row.get("llm_total_tokens"),
        "duration_seconds": duration_seconds,
        "duration_display": _log_duration_display(duration_seconds) if has_time_range else "",
        "has_time_range": has_time_range,
        "meta": row.get("meta") or {},
        "type_key": type_key,
        "type_label": type_label,
        "detail_text": detail_text,
    }


def _resolve_log_trend_window(
    *,
    days: int,
    tz_offset_minutes: int,
) -> tuple[date, date, datetime, datetime, int]:
    clamped_days = max(7, min(120, int(days or 30)))
    tz_offset_seconds = int(tz_offset_minutes or 0) * 60
    local_now = datetime.now(timezone.utc) + timedelta(seconds=tz_offset_seconds)
    end_day = local_now.date()
    start_day = end_day - timedelta(days=clamped_days - 1)
    start_at = datetime.combine(start_day, datetime.min.time(), tzinfo=timezone.utc) - timedelta(
        seconds=tz_offset_seconds
    )
    end_at = (
        datetime.combine(end_day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        - timedelta(seconds=tz_offset_seconds)
        - timedelta(microseconds=1)
    )
    return start_day, end_day, start_at, end_at, tz_offset_seconds


def _serialize_log_trend(
    rows: list[dict[str, Any]],
    *,
    start_day: date,
    end_day: date,
) -> dict[str, Any]:
    by_day: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        day_text = str(row.get("day") or "")[:10]
        if day_text:
            by_day[day_text] = dict(row)
    days: list[str] = []
    series: dict[str, list[dict[str, Any]]] = {key: [] for key in _LOG_CATEGORY_KEYS}
    cursor = start_day
    while cursor <= end_day:
        day_text = cursor.isoformat()
        row = by_day.get(day_text) or {}
        days.append(day_text)
        for key in _LOG_CATEGORY_KEYS:
            try:
                raw_key = "exam_cnt" if key == "quiz" else f"{key}_cnt"
                count = int(row.get(raw_key) or 0)
            except Exception:
                count = 0
            series[key].append({"day": day_text, "count": max(0, count)})
        cursor += timedelta(days=1)
    return {
        "start_day": start_day.isoformat(),
        "end_day": end_day.isoformat(),
        "days": days,
        "series": series,
    }


def _normalize_log_category_counts(counts: dict[str, Any]) -> dict[str, Any]:
    current = dict(counts or {})
    return {
        "candidate": int(current.get("candidate") or 0),
        "quiz": int(current.get("quiz", current.get("exam") or 0) or 0),
        "grading": int(current.get("grading") or 0),
        "assignment": int(current.get("assignment") or 0),
        "system": int(current.get("system") or 0),
    }


def _parse_assignment_duration(raw: int | str) -> int:
    if isinstance(raw, int):
        return max(0, raw)
    seconds = runtime_jobs._parse_duration_seconds(str(raw or ""))
    if seconds <= 0:
        raise HTTPException(status_code=400, detail="time_limit_seconds 无效")
    return seconds


def _normalize_quiz_analytics_window(value: str) -> str:
    key = str(value or "").strip().lower()
    return key if key in _QUIZ_ANALYTICS_WINDOWS else "month"


def _normalize_quiz_analytics_version_scope(value: str) -> str:
    key = str(value or "").strip().lower()
    return key if key in _QUIZ_ANALYTICS_VERSION_SCOPES else "all"


def _parse_quiz_analytics_date(value: str, *, field_name: str) -> date:
    text = str(value or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail=f"{field_name} 不能为空")
    try:
        return date.fromisoformat(text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} 格式无效，应为 YYYY-MM-DD") from exc


def _resolve_quiz_analytics_window(
    window: str,
    *,
    start_date: str = "",
    end_date: str = "",
) -> tuple[str, dict[str, Any], datetime, datetime]:
    start_date_text = str(start_date or "").strip()
    end_date_text = str(end_date or "").strip()
    if start_date_text or end_date_text:
        if not start_date_text or not end_date_text:
            raise HTTPException(status_code=400, detail="自定义时间范围需要同时提供开始日期和结束日期")
        start_day = _parse_quiz_analytics_date(start_date_text, field_name="start_date")
        end_day = _parse_quiz_analytics_date(end_date_text, field_name="end_date")
        if start_day > end_day:
            raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
        start_at = datetime(start_day.year, start_day.month, start_day.day, tzinfo=timezone.utc)
        end_at = datetime(end_day.year, end_day.month, end_day.day, 23, 59, 59, 999999, tzinfo=timezone.utc)
        days = max(1, (end_day - start_day).days + 1)
        return (
            "custom",
            {
                "days": days,
                "label": f"{start_day.isoformat()} 至 {end_day.isoformat()}",
                "start_date": start_day.isoformat(),
                "end_date": end_day.isoformat(),
                "is_custom": True,
            },
            start_at,
            end_at,
        )
    key = _normalize_quiz_analytics_window(window)
    meta = dict(_QUIZ_ANALYTICS_WINDOWS.get(key) or _QUIZ_ANALYTICS_WINDOWS["month"])
    end_at = datetime.now(timezone.utc)
    start_at = end_at - timedelta(days=int(meta.get("days") or 30))
    return key, meta, start_at, end_at


def _quiz_analytics_result_mode(archive: dict[str, Any]) -> str:
    current = dict(archive or {})
    grading = current.get("grading") if isinstance(current.get("grading"), dict) else {}
    raw_total = current.get("raw_total")
    if raw_total is None and isinstance(grading, dict):
        raw_total = grading.get("raw_total")
    fallback = ""
    try:
        fallback = "traits" if int(raw_total or 0) <= 0 and current else "scored"
    except Exception:
        fallback = ""
    return str(current.get("result_mode") or grading.get("result_mode") or fallback).strip().lower()


def _quiz_analytics_score_meta(row: dict[str, Any], archive: dict[str, Any]) -> tuple[int | None, int | None, str]:
    current_archive = dict(archive or {})
    grading = current_archive.get("grading") if isinstance(current_archive.get("grading"), dict) else {}
    result_mode = _quiz_analytics_result_mode(current_archive)
    total_score = _coerce_int_or_none(current_archive.get("total_score"))
    if total_score is None and isinstance(grading, dict):
        total_score = _coerce_int_or_none(grading.get("total"))
    if total_score is None:
        total_score = _coerce_int_or_none(row.get("score"))
    score_max = _coerce_int_or_none(current_archive.get("score_max"))
    if score_max is None and isinstance(grading, dict):
        score_max = _coerce_int_or_none(grading.get("total_max"))
    return total_score, score_max, result_mode


def _quiz_analytics_traits(archive: dict[str, Any]) -> dict[str, Any]:
    current = dict(archive or {})
    traits = current.get("traits")
    if not isinstance(traits, dict) or not traits:
        grading = current.get("grading") if isinstance(current.get("grading"), dict) else {}
        traits = grading.get("traits") or grading.get("trait_result")
    return dict(traits or {}) if isinstance(traits, dict) else {}


def _quiz_analytics_trait_dimensions(traits: dict[str, Any]) -> list[str]:
    primary_dimensions = traits.get("primary_dimensions") if isinstance(traits, dict) else []
    out = [
        str(item or "").strip()
        for item in (primary_dimensions if isinstance(primary_dimensions, list) else [])
        if str(item or "").strip()
    ]
    if out:
        return out
    paired_dimensions = traits.get("paired_dimensions") if isinstance(traits, dict) else []
    if not isinstance(paired_dimensions, list):
        return []
    return [
        str((item or {}).get("winner") or "").strip()
        for item in paired_dimensions
        if isinstance(item, dict) and str((item or {}).get("winner") or "").strip()
    ]


def _quiz_analytics_trait_combination(traits: dict[str, Any]) -> str:
    return "".join(_quiz_analytics_trait_dimensions(traits))


def _quiz_analytics_trait_summary(traits: dict[str, Any]) -> str:
    combination = _quiz_analytics_trait_combination(traits)
    return f"量表：{combination}" if combination else "量表"


def _quiz_analytics_attempt_time(row: dict[str, Any]) -> str:
    status_key = validation_helpers._normalize_exam_status(str(row.get("status") or "").strip())
    if status_key == "finished":
        return _iso_or_empty(row.get("finished_at"))
    return _iso_or_empty(row.get("entered_at"))


def _serialize_quiz_analytics_item(
    row: dict[str, Any],
    *,
    version_no_by_id: dict[int, int],
) -> dict[str, Any]:
    archive = row.get("archive") if isinstance(row.get("archive"), dict) else {}
    status_key = validation_helpers._normalize_exam_status(str(row.get("status") or "").strip())
    candidate_id = int(row.get("candidate_id") or 0)
    candidate_name = str(row.get("name") or "").strip()
    if _looks_deleted_marker(candidate_name) or not candidate_name:
        candidate_name = f"候选人#{candidate_id}" if candidate_id > 0 else "候选人"
    total_score, score_max, result_mode = _quiz_analytics_score_meta(row, archive)
    traits = _quiz_analytics_traits(archive)
    quiz_version_id = int(row.get("quiz_version_id") or 0)
    return {
        "attempt_id": int(row.get("attempt_id") or 0),
        "candidate_id": candidate_id,
        "candidate_name": candidate_name,
        "token": str(row.get("token") or "").strip(),
        "status": status_key,
        "status_label": _status_label(status_key),
        "source_kind": str(row.get("source_kind") or "").strip() or "direct",
        "source_label": _source_label(str(row.get("source_kind") or "").strip()),
        "quiz_version_id": quiz_version_id,
        "version_no": int(version_no_by_id.get(quiz_version_id) or 0),
        "entered_at": _iso_or_empty(row.get("entered_at")),
        "entered_at_display": _iso_to_local_display(_iso_or_empty(row.get("entered_at"))),
        "finished_at": _iso_or_empty(row.get("finished_at")),
        "finished_at_display": _iso_to_local_display(_iso_or_empty(row.get("finished_at"))),
        "attempt_at": _quiz_analytics_attempt_time(row),
        "attempt_at_display": _iso_to_local_display(_quiz_analytics_attempt_time(row)),
        "score": total_score,
        "score_max": score_max,
        "score_display": _score_display(total_score, score_max, result_mode=result_mode),
        "result_mode": result_mode,
        "trait_combination": _quiz_analytics_trait_combination(traits),
        "trait_summary": _quiz_analytics_trait_summary(traits) if traits else "",
        "detail_url": f"/admin/attempt/{str(row.get('token') or '').strip()}",
    }


def _build_quiz_analytics_distribution_groups(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, dict[str, Any]] = {}
    for item in items:
        if str(item.get("status") or "").strip() != "finished":
            continue
        if str(item.get("result_mode") or "").strip() == "traits":
            continue
        score = _coerce_int_or_none(item.get("score"))
        score_max = _coerce_int_or_none(item.get("score_max"))
        if score is None or score_max is None or score_max <= 0:
            continue
        bucket_group = grouped.setdefault(
            score_max,
            {
                "score_max": score_max,
                "score_max_label": f"满分 {score_max}",
                "total_count": 0,
                "buckets": {value: 0 for value in range(max(0, score_max) + 1)},
            },
        )
        clamped_score = max(0, min(score_max, score))
        bucket_group["buckets"][clamped_score] = int(bucket_group["buckets"].get(clamped_score) or 0) + 1
        bucket_group["total_count"] = int(bucket_group.get("total_count") or 0) + 1

    out: list[dict[str, Any]] = []
    for score_max in sorted(grouped):
        current = grouped[score_max]
        buckets = current.pop("buckets")
        current["buckets"] = [
            {
                "score": int(score),
                "label": str(score),
                "count": int(count or 0),
            }
            for score, count in sorted(buckets.items(), key=lambda item: int(item[0]))
        ]
        out.append(current)
    return out


def _quiz_analytics_percent(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((int(count or 0) / total) * 100, 1)


def _quiz_analytics_count_rows(counts: dict[str, int], total: int, *, key_name: str = "label") -> list[dict[str, Any]]:
    rows = [
        {
            key_name: key,
            "count": int(count or 0),
            "percent": _quiz_analytics_percent(int(count or 0), total),
        }
        for key, count in counts.items()
        if str(key or "").strip() and int(count or 0) > 0
    ]
    return sorted(rows, key=lambda item: (-int(item.get("count") or 0), str(item.get(key_name) or "")))


def _build_quiz_analytics_trait_distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_count = 0
    combination_counts: dict[str, int] = {}
    combination_dimensions: dict[str, list[str]] = {}
    primary_dimension_counts: dict[str, int] = {}
    pair_counts: dict[str, dict[str, Any]] = {}

    for row in rows:
        status_key = validation_helpers._normalize_exam_status(str(row.get("status") or "").strip())
        if status_key != "finished":
            continue
        archive = row.get("archive") if isinstance(row.get("archive"), dict) else {}
        traits = _quiz_analytics_traits(archive)
        if not traits:
            continue
        result_mode = _quiz_analytics_result_mode(archive)
        if result_mode not in {"traits", "mixed"}:
            continue

        dimensions = _quiz_analytics_trait_dimensions(traits)
        paired_dimensions = traits.get("paired_dimensions") if isinstance(traits.get("paired_dimensions"), list) else []
        if not dimensions and not paired_dimensions:
            continue

        total_count += 1
        combination = "".join(dimensions)
        if combination:
            combination_counts[combination] = int(combination_counts.get(combination) or 0) + 1
            combination_dimensions.setdefault(combination, list(dimensions))
            for dimension in dimensions:
                primary_dimension_counts[dimension] = int(primary_dimension_counts.get(dimension) or 0) + 1

        for pair in paired_dimensions:
            if not isinstance(pair, dict):
                continue
            left = str(pair.get("left") or "").strip()
            right = str(pair.get("right") or "").strip()
            winner = str(pair.get("winner") or "").strip()
            if not left or not right or not winner:
                continue
            key = f"{left}/{right}"
            current = pair_counts.setdefault(
                key,
                {
                    "key": key,
                    "label": f"{left} / {right}",
                    "left": left,
                    "right": right,
                    "description": str(pair.get("description") or "").strip(),
                    "total_count": 0,
                    "counts": {},
                },
            )
            current["total_count"] = int(current.get("total_count") or 0) + 1
            counts = current["counts"] if isinstance(current.get("counts"), dict) else {}
            counts[winner] = int(counts.get(winner) or 0) + 1
            current["counts"] = counts

    combination_rows = []
    for combination, count in combination_counts.items():
        combination_rows.append(
            {
                "combination": combination,
                "dimensions": list(combination_dimensions.get(combination) or []),
                "count": int(count or 0),
                "percent": _quiz_analytics_percent(int(count or 0), total_count),
            }
        )
    combination_rows.sort(key=lambda item: (-int(item.get("count") or 0), str(item.get("combination") or "")))

    pair_rows = []
    for pair in pair_counts.values():
        counts = pair.get("counts") if isinstance(pair.get("counts"), dict) else {}
        total = int(pair.get("total_count") or 0)
        left = str(pair.get("left") or "").strip()
        right = str(pair.get("right") or "").strip()
        pair_rows.append(
            {
                "key": str(pair.get("key") or "").strip(),
                "label": str(pair.get("label") or "").strip(),
                "left": left,
                "right": right,
                "description": str(pair.get("description") or "").strip(),
                "left_count": int(counts.get(left) or 0),
                "right_count": int(counts.get(right) or 0),
                "total_count": total,
                "winner_counts": _quiz_analytics_count_rows(counts, total, key_name="dimension"),
            }
        )

    return {
        "total_count": total_count,
        "appeared_combination_count": len(combination_rows),
        "combination_counts": combination_rows,
        "primary_dimension_counts": _quiz_analytics_count_rows(primary_dimension_counts, total_count, key_name="dimension"),
        "pair_counts": pair_rows,
    }


def _serialize_quiz_analytics_detail(
    exam: dict[str, Any],
    *,
    request: Request,
    window: str,
    version_scope: str,
    version_id: int | None = None,
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    quiz_key = str(exam.get("quiz_key") or "").strip()
    current_version_id = int(exam.get("current_version_id") or 0)
    window_key, window_meta, start_at, end_at = _resolve_quiz_analytics_window(
        window,
        start_date=start_date,
        end_date=end_date,
    )
    scope_key = _normalize_quiz_analytics_version_scope(version_scope)
    versions = deps.list_quiz_versions(quiz_key)
    version_no_by_id = {int(item.get("id") or 0): int(item.get("version_no") or 0) for item in versions}
    available_versions = [
        {
            "id": int(item.get("id") or 0),
            "version_no": int(item.get("version_no") or 0),
            "is_current": int(item.get("id") or 0) == current_version_id,
        }
        for item in versions
        if int(item.get("id") or 0) > 0
    ]
    selected_version_id = 0
    if scope_key == "current":
        requested_version_id = int(version_id or 0)
        valid_version_ids = {int(item.get("id") or 0) for item in available_versions}
        if requested_version_id > 0 and requested_version_id in valid_version_ids:
            selected_version_id = requested_version_id
        else:
            selected_version_id = current_version_id if current_version_id in valid_version_ids else 0
    rows = deps.list_quiz_paper_analytics_rows(
        quiz_key=quiz_key,
        current_version_id=current_version_id,
        selected_version_id=selected_version_id,
        version_scope=scope_key,
        start_at=start_at,
        end_at=end_at,
    )
    items = [
        _serialize_quiz_analytics_item(
            row,
            version_no_by_id=version_no_by_id,
        )
        for row in rows
    ]
    summary = {
        "total_attempt_count": len(items),
        "finished_count": sum(1 for item in items if str(item.get("status") or "") == "finished"),
        "in_progress_count": sum(1 for item in items if str(item.get("status") or "") in {"in_quiz", "grading"}),
        "scored_finished_count": sum(
            1
            for item in items
            if str(item.get("status") or "") == "finished"
            and str(item.get("result_mode") or "") != "traits"
            and _coerce_int_or_none(item.get("score")) is not None
        ),
        "traits_only_finished_count": sum(
            1
            for item in items
            if str(item.get("status") or "") == "finished" and str(item.get("result_mode") or "") == "traits"
        ),
    }
    return {
        "quiz": {
            "quiz_key": quiz_key,
            "title": str(exam.get("title") or "").strip(),
            "tags": list(exam.get("tags") if isinstance(exam.get("tags"), list) else []),
            "current_version_id": current_version_id,
            "current_version_no": int(exam.get("current_version_no") or 0),
            "supports_version_scope_switch": current_version_id > 0,
            "available_versions": available_versions,
            "url": f"{_admin_base_url(request)}/admin/quizzes/{quiz_key}",
        },
        "filters": {
            "window": window_key,
            "window_label": str(window_meta.get("label") or ""),
            "window_days": int(window_meta.get("days") or 30),
            "is_custom_window": bool(window_meta.get("is_custom")),
            "version_scope": scope_key,
            "version_scope_label": _QUIZ_ANALYTICS_VERSION_SCOPES.get(scope_key) or _QUIZ_ANALYTICS_VERSION_SCOPES["all"],
            "version_id": selected_version_id,
            "start_date": str(window_meta.get("start_date") or start_at.date().isoformat()),
            "end_date": str(window_meta.get("end_date") or end_at.date().isoformat()),
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
        },
        "summary": summary,
        "distribution_groups": _build_quiz_analytics_distribution_groups(items),
        "trait_distribution": _build_quiz_analytics_trait_distribution(rows),
        "items": items,
    }

from .admin_assignment_routes import router as admin_assignment_router
from .admin_candidate_routes import router as admin_candidate_router
from .admin_core_routes import router as admin_core_router
from .admin_dashboard_routes import router as admin_dashboard_router
from .admin_job_description_routes import router as admin_job_description_router
from .admin_monitor_routes import router as admin_monitor_router
from .admin_quiz_analytics_routes import router as admin_quiz_analytics_router
from .admin_quiz_routes import router as admin_quiz_router

router.include_router(admin_core_router)
router.include_router(admin_dashboard_router)
router.include_router(admin_quiz_router)
router.include_router(admin_quiz_analytics_router)
router.include_router(admin_candidate_router)
router.include_router(admin_job_description_router)
router.include_router(admin_assignment_router)
router.include_router(admin_monitor_router)
