from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from backend.md_quiz.config import load_runtime_defaults
from backend.md_quiz.services import answer_activity, exam_helpers, runtime_bootstrap, runtime_jobs, support_deps as deps
from backend.md_quiz.services import public_flow_service
from backend.md_quiz.services.request_url_helpers import external_base_url
from backend.md_quiz.services import validation_helpers

router = APIRouter(prefix="/api/public", tags=["public"])

_PUBLIC_SESSION_HEADER = "X-Public-Session-Id"
_MAX_PUBLIC_REENTRY_COUNT = 5


class SmsSendPayload(BaseModel):
    token: str
    name: str = ""
    phone: str = ""


class VerifyPayload(BaseModel):
    token: str
    name: str = ""
    phone: str = ""
    sms_code: str = ""


class UseExistingResumePayload(BaseModel):
    token: str


class ActivityEventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seq: int = Field(ge=1)
    elapsed_ms: int = Field(ge=0)
    kind: str
    added_chars: int = Field(ge=0, le=100_000)
    deleted_chars: int = Field(ge=0, le=100_000)
    length_after: int = Field(ge=0, le=100_000)


class ActivityBatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str = Field(min_length=1, max_length=100)
    session_id: str = Field(min_length=1, max_length=80)
    capture_id: str = Field(min_length=1, max_length=80)
    events: list[ActivityEventPayload] = Field(max_length=answer_activity.MAX_EVENTS_PER_BATCH)
    dropped_events: int = Field(default=0, ge=0)


class AnswerActionPayload(BaseModel):
    question_id: str = ""
    answer: Any = None
    advance: bool = False
    submit: bool = False
    session_id: str = ""
    force_timeout: bool = False
    activity: ActivityBatchPayload | None = None


def _public_base_url(request: Request) -> str:
    return external_base_url(request)


def _compute_exam_stats(spec: dict[str, Any]) -> dict[str, Any]:
    questions = list(spec.get("questions") or [])
    counts_by_type: dict[str, int] = {}
    total_points = 0
    for question in questions:
        qtype = str(question.get("type") or "").strip() or "unknown"
        counts_by_type[qtype] = int(counts_by_type.get(qtype, 0)) + 1
        try:
            total_points += int(question.get("max_points") or 0)
        except Exception:
            continue
    return {
        "total_questions": len(questions),
        "total_points": total_points,
        "counts_by_type": counts_by_type,
    }


def _invite_window_payload(start_date: Any, end_date: Any) -> dict[str, str]:
    def _stringify(value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value or "").strip()

    return {"start_date": _stringify(start_date), "end_date": _stringify(end_date)}


def _assignment_requires_phone_verification(assignment: dict[str, Any]) -> bool:
    return validation_helpers._require_phone_verification(assignment)


def _assignment_ignore_timing(assignment: dict[str, Any]) -> bool:
    return bool((assignment or {}).get("ignore_timing"))


def _normalize_public_session_id(raw: Any) -> str:
    return str(raw or "").strip()[:80]


def _session_id_from_request(request: Request | None) -> str:
    if request is None:
        return ""
    return _normalize_public_session_id(request.headers.get(_PUBLIC_SESSION_HEADER))


def _mask_phone(phone: str) -> str:
    normalized = validation_helpers._normalize_phone(phone)
    if len(normalized) != 11:
        return ""
    return f"{normalized[:3]}****{normalized[-4:]}"


def _verification_mode(assignment: dict[str, Any], *, candidate_id: int | None = None) -> str:
    if not _assignment_requires_phone_verification(assignment):
        return "none"
    if candidate_id is None:
        try:
            candidate_id = int(assignment.get("candidate_id") or 0)
        except Exception:
            candidate_id = 0
    if bool(assignment.get("public_invite")) or candidate_id <= 0:
        return "public_identity"
    return "direct_phone"


def _normalize_question_flow(assignment: dict[str, Any]) -> dict[str, Any]:
    current = assignment.get("question_flow") if isinstance(assignment.get("question_flow"), dict) else {}
    flow = {
        "current_index": max(0, int(current.get("current_index") or 0)),
        "current_started_at": str(current.get("current_started_at") or "").strip() or None,
        "reentry_count": max(0, int(current.get("reentry_count") or 0)),
        "active_session_id": str(current.get("active_session_id") or "").strip(),
        "last_session_seen_at": str(current.get("last_session_seen_at") or "").strip(),
    }
    assignment["question_flow"] = flow
    return flow


def _load_public_quiz_bundle(assignment: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    exam_snapshot = exam_helpers.get_exam_snapshot_for_assignment(assignment) or {}
    public_spec = exam_snapshot.get("public_spec") if isinstance(exam_snapshot.get("public_spec"), dict) else {}
    if not public_spec:
        raise HTTPException(status_code=404, detail="测验不存在")
    public_spec = exam_helpers.build_render_ready_public_spec(public_spec)
    quiz_metadata = exam_helpers.build_quiz_metadata(public_spec)
    return public_spec, quiz_metadata


def _sync_assignment_time_limit_fields(assignment: dict[str, Any], public_spec: dict[str, Any]) -> bool:
    changed = False
    ignore_timing = _assignment_ignore_timing(assignment)
    total_seconds = 0 if ignore_timing else exam_helpers.compute_quiz_time_limit_seconds(public_spec)
    if int(assignment.get("time_limit_seconds") or 0) != int(total_seconds):
        assignment["time_limit_seconds"] = int(total_seconds)
        changed = True
    if int(assignment.get("min_submit_seconds") or 0) != 0:
        assignment["min_submit_seconds"] = 0
        changed = True
    if "question_flow" not in assignment or not isinstance(assignment.get("question_flow"), dict):
        _normalize_question_flow(assignment)
        changed = True
    return changed


def _current_question(questions: list[dict[str, Any]], flow: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
    if not questions:
        return 0, None
    index = max(0, min(int(flow.get("current_index") or 0), len(questions) - 1))
    flow["current_index"] = index
    return index, questions[index]


def _current_question_remaining_seconds(
    question: dict[str, Any] | None,
    flow: dict[str, Any],
    *,
    now: datetime | None = None,
    ignore_timing: bool = False,
) -> int:
    if ignore_timing:
        return 0
    if not question:
        return 0
    started_at = runtime_jobs._parse_iso_dt(flow.get("current_started_at"))
    if not started_at:
        return int(question.get("answer_time_seconds") or 0)
    if now is None:
        now = datetime.now(timezone.utc)
    duration = max(0, int(question.get("answer_time_seconds") or 0))
    elapsed = max(0, int((now - started_at).total_seconds()))
    return max(0, duration - elapsed)


def _sync_question_timeouts(token: str, assignment: dict[str, Any], questions: list[dict[str, Any]], *, now: datetime) -> bool:
    if assignment.get("grading") or not questions:
        return False
    if _assignment_ignore_timing(assignment):
        return False
    timing = assignment.get("timing") if isinstance(assignment.get("timing"), dict) else {}
    if not str((timing or {}).get("start_at") or "").strip():
        return False
    flow = _normalize_question_flow(assignment)
    if not flow.get("current_started_at"):
        flow["current_started_at"] = str(timing.get("start_at") or now.isoformat())
    changed = False
    while True:
        index, question = _current_question(questions, flow)
        if not question:
            break
        started_at = runtime_jobs._parse_iso_dt(flow.get("current_started_at"))
        if not started_at:
            flow["current_started_at"] = now.isoformat()
            changed = True
            break
        limit = max(0, int(question.get("answer_time_seconds") or 0))
        if limit <= 0 or now < started_at + timedelta(seconds=limit):
            qid = str(question.get("qid") or "").strip()
            if qid and qid not in (assignment.get("question_activity") or {}):
                answer_activity.question_record(assignment, qid, flow["current_started_at"])
                changed = True
            break
        next_started_at = (started_at + timedelta(seconds=limit)).isoformat()
        answer_activity.close_question(assignment, str(question.get("qid") or "").strip(), flow["current_started_at"], started_at + timedelta(seconds=limit), "timeout")
        if index >= len(questions) - 1:
            runtime_jobs._finalize_public_submission(token, assignment, now=now, reason="timeout")
            return True
        flow["current_index"] = index + 1
        flow["current_started_at"] = next_started_at
        changed = True
    if changed:
        deps.save_assignment(token, assignment)
    return False


def _register_public_session(token: str, assignment: dict[str, Any], session_id: str, *, now: datetime) -> bool:
    sid = _normalize_public_session_id(session_id)
    if not sid or assignment.get("grading"):
        return False
    timing = assignment.get("timing") if isinstance(assignment.get("timing"), dict) else {}
    if not str((timing or {}).get("start_at") or "").strip():
        return False
    flow = _normalize_question_flow(assignment)
    current = str(flow.get("active_session_id") or "").strip()
    flow["last_session_seen_at"] = now.isoformat()
    if not current:
        flow["active_session_id"] = sid
        deps.save_assignment(token, assignment)
        return False
    if current == sid:
        deps.save_assignment(token, assignment)
        return False
    flow["reentry_count"] = int(flow.get("reentry_count") or 0) + 1
    flow["active_session_id"] = sid
    if int(flow.get("reentry_count") or 0) > _MAX_PUBLIC_REENTRY_COUNT:
        runtime_jobs._finalize_public_submission(token, assignment, now=now)
        return True
    deps.save_assignment(token, assignment)
    return False


def _serialize_assignment_payload(assignment: dict[str, Any]) -> dict[str, Any]:
    current = dict(assignment or {})
    current.pop("question_activity", None)
    current["quiz_key"] = str(current.get("quiz_key") or "").strip()
    status_text = validation_helpers._normalize_exam_status(str(current.get("status") or "").strip())
    current["status"] = status_text
    current["require_phone_verification"] = _assignment_requires_phone_verification(current)
    current["ignore_timing"] = _assignment_ignore_timing(current)
    current["verification_mode"] = _verification_mode(current)
    current["question_flow"] = _normalize_question_flow(current)
    return current


def _build_verify_payload(assignment: dict[str, Any], *, verify: dict[str, Any], sms: dict[str, Any], pending_profile: dict[str, Any], candidate_id: int) -> dict[str, Any]:
    mode = _verification_mode(assignment, candidate_id=candidate_id)
    masked_phone = ""
    if mode == "direct_phone" and candidate_id > 0:
        candidate = deps.get_candidate(candidate_id) or {}
        masked_phone = _mask_phone(str(candidate.get("phone") or ""))
    return {
        "locked": bool(verify.get("locked")),
        "attempts": int(verify.get("attempts") or 0),
        "max_attempts": int(assignment.get("verify_max_attempts") or 3),
        "sms_verified": bool(sms.get("verified")),
        "mode": mode,
        "masked_phone": masked_phone,
        "name": str(pending_profile.get("name") or ""),
        "phone": str(sms.get("phone") or pending_profile.get("phone") or ""),
    }


def _build_resume_payload(assignment: dict[str, Any], *, pending_profile: dict[str, Any], sms: dict[str, Any]) -> dict[str, Any]:
    pending_existing = assignment.get("pending_existing_candidate") or {}
    if not isinstance(pending_existing, dict):
        pending_existing = {}
    try:
        existing_candidate_id = int(pending_existing.get("candidate_id") or 0)
    except Exception:
        existing_candidate_id = 0
    existing_resume = {
        "filename": str(pending_existing.get("resume_filename") or "").strip(),
        "parsed_at": str(pending_existing.get("resume_parsed_at") or "").strip(),
        "size": max(0, int(pending_existing.get("resume_size") or 0)),
    }
    has_existing_resume = bool(
        existing_candidate_id > 0
        and (
            existing_resume["filename"]
            or int(existing_resume["size"] or 0) > 0
            or existing_resume["parsed_at"]
        )
    )
    return {
        "name": str(pending_profile.get("name") or "候选人").strip() or "候选人",
        "phone": validation_helpers._normalize_phone(
            str(pending_profile.get("phone") or sms.get("phone") or "").strip()
        ),
        "mode": ("reuse_or_replace" if has_existing_resume else "upload_required"),
        "existing_resume": existing_resume,
    }


def _build_quiz_payload(assignment: dict[str, Any], public_spec: dict[str, Any], quiz_metadata: dict[str, Any]) -> dict[str, Any]:
    flow = _normalize_question_flow(assignment)
    questions = list(public_spec.get("questions") or [])
    current_index, current_question = _current_question(questions, flow)
    ignore_timing = _assignment_ignore_timing(assignment)
    return {
        "server_now": datetime.now(timezone.utc).isoformat(),
        "quiz_key": str(assignment.get("quiz_key") or "").strip(),
        "title": str(public_spec.get("title") or "").strip(),
        "description": str(public_spec.get("description") or "").strip(),
        "tags": list(quiz_metadata["tags"]),
        "schema_version": quiz_metadata["schema_version"],
        "format": str(quiz_metadata["format"] or "").strip(),
        "question_count": int(quiz_metadata["question_count"]),
        "question_counts": dict(quiz_metadata["question_counts"]),
        "estimated_duration_minutes": int(quiz_metadata["estimated_duration_minutes"]),
        "answer_time_total_seconds": int(quiz_metadata.get("answer_time_total_seconds") or 0),
        "trait": dict(quiz_metadata["trait"]),
        "spec": public_spec,
        "stats": _compute_exam_stats(public_spec),
        "remaining_seconds": 0 if ignore_timing else runtime_jobs._remaining_seconds(assignment),
        "time_limit_seconds": 0 if ignore_timing else int(assignment.get("time_limit_seconds") or 0),
        "min_submit_seconds": 0,
        "answers": assignment.get("answers") or {},
        "entered_at": str(((assignment.get("timing") or {}).get("start_at") or "")).strip(),
        "question_flow": {
            "current_index": int(flow.get("current_index") or 0),
            "current_started_at": str(flow.get("current_started_at") or ""),
            "current_question_id": str((current_question or {}).get("qid") or ""),
            "current_question_seconds": 0 if ignore_timing else int((current_question or {}).get("answer_time_seconds") or 0),
            "current_question_remaining_seconds": _current_question_remaining_seconds(
                current_question,
                flow,
                ignore_timing=ignore_timing,
            ),
            "reentry_count": int(flow.get("reentry_count") or 0),
            "reentry_limit": _MAX_PUBLIC_REENTRY_COUNT,
        },
    }


def _build_quiz_preview(assignment: dict[str, Any]) -> dict[str, Any]:
    try:
        public_spec, quiz_metadata = _load_public_quiz_bundle(assignment)
    except Exception:
        return {
            "title": str(assignment.get("quiz_key") or "测验").strip(),
            "description": "",
            "question_count": 0,
            "estimated_duration_minutes": 0,
            "answer_time_total_seconds": int(assignment.get("time_limit_seconds") or 0),
            "spec": {"welcome_image": "", "end_image": "", "questions": []},
        }
    return {
        "title": str(public_spec.get("title") or "").strip(),
        "description": str(public_spec.get("description") or "").strip(),
        "question_count": int(quiz_metadata.get("question_count") or 0),
        "estimated_duration_minutes": int(quiz_metadata.get("estimated_duration_minutes") or 0),
        "answer_time_total_seconds": int(quiz_metadata.get("answer_time_total_seconds") or 0),
        "spec": public_spec,
    }


def _build_done_payload(token: str, assignment: dict[str, Any]) -> dict[str, Any]:
    quiz: dict[str, Any] = {}
    try:
        public_spec, quiz_metadata = _load_public_quiz_bundle(assignment)
        quiz = {
            "title": str(public_spec.get("title") or "").strip(),
            "description": str(public_spec.get("description") or "").strip(),
            "spec": public_spec,
            "estimated_duration_minutes": int(quiz_metadata["estimated_duration_minutes"]),
            "answer_time_total_seconds": int(quiz_metadata.get("answer_time_total_seconds") or 0),
        }
    except Exception:
        quiz = {
            "title": str(assignment.get("quiz_key") or "测验").strip(),
            "description": "",
            "spec": {"welcome_image": "", "end_image": "", "questions": []},
            "estimated_duration_minutes": 0,
            "answer_time_total_seconds": int(assignment.get("time_limit_seconds") or 0),
        }
    grading = assignment.get("grading") or {}
    runtime_jobs._sync_quiz_paper_finished_from_assignment(assignment)
    return {
        "token": token,
        "step": "done",
        "assignment": _serialize_assignment_payload(assignment),
        "quiz": quiz,
        "result": {
            "grading": grading,
            "candidate_remark": assignment.get("candidate_remark"),
            "final_analysis": (grading or {}).get("final_analysis") or (grading or {}).get("analysis"),
            "traits": (grading or {}).get("traits") or (grading or {}).get("trait_result") or {},
            "graded_at": assignment.get("graded_at"),
            "status": str((grading or {}).get("status") or "").strip(),
            "total_score": (grading or {}).get("total"),
            "score_max": (grading or {}).get("total_max"),
            "result_mode": str((grading or {}).get("result_mode") or "").strip(),
        },
    }


def _normalize_answer_for_question(question: dict[str, Any], raw: Any) -> Any:
    qtype = str(question.get("type") or "").strip().lower()
    if qtype == "single":
        option_keys = {str(item.get("key") or "").strip() for item in (question.get("options") or [])}
        value = str(raw or "").strip()
        return value if value and value in option_keys else ""
    if qtype == "multiple":
        values = raw if isinstance(raw, list) else ([raw] if raw not in {None, ""} else [])
        option_keys = {str(item.get("key") or "").strip() for item in (question.get("options") or [])}
        out: list[str] = []
        for item in values:
            value = str(item or "").strip()
            if value and value in option_keys and value not in out:
                out.append(value)
        return out
    return str(raw or "")


def _answer_is_ready(question: dict[str, Any], answer: Any) -> bool:
    qtype = str(question.get("type") or "").strip().lower()
    if qtype == "single":
        return bool(str(answer or "").strip())
    if qtype == "multiple":
        return bool(isinstance(answer, list) and len(answer) > 0)
    return bool(str(answer or "").strip())


def _record_activity_batch(
    assignment: dict[str, Any], question: dict[str, Any], flow: dict[str, Any],
    batch: ActivityBatchPayload, *, now: datetime,
) -> int:
    qid = str(question.get("qid") or "").strip()
    if str(question.get("type") or "") != "short" or batch.question_id != qid:
        raise HTTPException(status_code=409, detail="question_locked")
    if batch.session_id != str(flow.get("active_session_id") or ""):
        raise HTTPException(status_code=409, detail="session_changed")
    started_at = runtime_jobs._parse_iso_dt(flow.get("current_started_at"))
    if not started_at:
        raise HTTPException(status_code=400, detail="请先开始答题")
    latest_ms = max(0, round((now - started_at).total_seconds() * 1000)) + 5000
    events = [event.model_dump() for event in batch.events]
    if any(event["kind"] not in answer_activity.EVENT_KINDS or event["elapsed_ms"] > latest_ms for event in events):
        raise HTTPException(status_code=422, detail="invalid_activity")
    return answer_activity.record_input_batch(
        assignment, qid, flow["current_started_at"],
        {"capture_id": batch.capture_id, "events": events, "dropped_events": batch.dropped_events},
    )


def _apply_answer_action(token: str, action: AnswerActionPayload, *, session_id: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    should_reload = False
    with deps.assignment_locked(token):
        assignment = deps.load_assignment(token)
        invite_state, _, _ = exam_helpers._invite_window_state(assignment)
        if invite_state in {"not_started", "expired"}:
            raise HTTPException(status_code=403, detail="invite_window_invalid")
        if (assignment.get("verify") or {}).get("locked"):
            raise HTTPException(status_code=410, detail="当前链接已失效")
        if assignment.get("grading") or runtime_jobs._finalize_if_time_up(token, assignment):
            raise HTTPException(status_code=409, detail="already_submitted")

        runtime_bootstrap._ensure_quiz_paper_for_token(token, assignment)
        try:
            candidate_id = int(assignment.get("candidate_id") or 0)
        except Exception:
            candidate_id = 0
        if candidate_id <= 0:
            raise HTTPException(status_code=400, detail="请先完成身份验证")

        public_spec, _quiz_metadata = _load_public_quiz_bundle(assignment)
        questions = list(public_spec.get("questions") or [])
        if not questions:
            raise HTTPException(status_code=400, detail="测验题目为空")
        _sync_assignment_time_limit_fields(assignment, public_spec)

        timing = assignment.get("timing") if isinstance(assignment.get("timing"), dict) else {}
        if not str((timing or {}).get("start_at") or "").strip():
            raise HTTPException(status_code=400, detail="请先开始答题")

        if _sync_question_timeouts(token, assignment, questions, now=now):
            should_reload = True
        else:
            assignment = deps.load_assignment(token)
            if _register_public_session(token, assignment, session_id, now=now):
                should_reload = True
            else:
                assignment = deps.load_assignment(token)
                if assignment.get("grading"):
                    should_reload = True

        if should_reload:
            pass
        else:
            flow = _normalize_question_flow(assignment)
            current_index, question = _current_question(questions, flow)
            if not question:
                raise HTTPException(status_code=400, detail="题目不存在")
            current_qid = str(question.get("qid") or "").strip()
            if action.question_id and str(action.question_id or "").strip() != current_qid:
                raise HTTPException(status_code=409, detail="question_locked")

            if action.activity:
                _record_activity_batch(assignment, question, flow, action.activity, now=now)

            answers = assignment.setdefault("answers", {})
            if action.force_timeout:
                answers.pop(current_qid, None)
            else:
                normalized_answer = _normalize_answer_for_question(question, action.answer)
                if normalized_answer is None or normalized_answer == "" or normalized_answer == []:
                    answers.pop(current_qid, None)
                else:
                    answers[current_qid] = normalized_answer

            should_advance = bool(action.advance or action.submit or action.force_timeout)
            if not should_advance:
                deps.save_assignment(token, assignment)
            else:
                if action.submit and current_index < len(questions) - 1:
                    raise HTTPException(status_code=409, detail="not_last_question")
                if not action.force_timeout and not _answer_is_ready(question, answers.get(current_qid)):
                    raise HTTPException(status_code=400, detail="请先完成本题")

                if current_index >= len(questions) - 1 or action.submit:
                    runtime_jobs._finalize_public_submission(
                        token, assignment, now=now,
                        reason="timeout" if action.force_timeout else "submit",
                    )
                else:
                    answer_activity.close_question(
                        assignment, current_qid, flow.get("current_started_at"), now,
                        "timeout" if action.force_timeout else "advance",
                    )
                    flow["current_index"] = current_index + 1
                    flow["current_started_at"] = now.isoformat()
                    next_qid = str(questions[current_index + 1].get("qid") or "").strip()
                    answer_activity.question_record(assignment, next_qid, flow["current_started_at"])
                    assignment["status"] = "in_quiz"
                    assignment["status_updated_at"] = now.isoformat()
                    deps.save_assignment(token, assignment)
    return _bootstrap_attempt(token, session_id=session_id)


def _bootstrap_attempt(token: str, *, session_id: str = "") -> dict[str, Any]:
    token = str(token or "").strip()
    if not token:
        raise HTTPException(status_code=404, detail="token 不存在")

    with deps.assignment_locked(token):
        assignment = deps.load_assignment(token)
        runtime_jobs._finalize_if_time_up(token, assignment)
        assignment = deps.load_assignment(token)
        runtime_bootstrap._ensure_quiz_paper_for_token(token, assignment)

    invite_state, start_date, end_date = exam_helpers._invite_window_state(assignment)
    verify = assignment.get("verify") or {}
    if not isinstance(verify, dict):
        verify = {}
    sms = assignment.get("sms_verify") or {}
    if not isinstance(sms, dict):
        sms = {}
    pending_profile = assignment.get("pending_profile") or {}
    if not isinstance(pending_profile, dict):
        pending_profile = {}
    status_text = str(assignment.get("status") or "").strip()
    require_phone_verification = _assignment_requires_phone_verification(assignment)

    try:
        candidate_id = int(assignment.get("candidate_id") or 0)
    except Exception:
        candidate_id = 0

    if invite_state == "not_started":
        return {
            "token": token,
            "step": "unavailable",
            "assignment": _serialize_assignment_payload(assignment),
            "invite_window": _invite_window_payload(start_date, end_date),
            "unavailable": {
                "title": "未到答题时间",
                "message": "当前未到答题时间，请在有效时间范围内进入答题。",
            },
        }

    if status_text == "expired" or (
        invite_state == "expired"
        and not str(((assignment.get("timing") or {}).get("start_at") or "")).strip()
    ):
        return {
            "token": token,
            "step": "unavailable",
            "assignment": _serialize_assignment_payload(assignment),
            "invite_window": _invite_window_payload(start_date, end_date),
            "unavailable": {
                "title": "邀约已失效",
                "message": "当前答题链接已失效，请联系管理员重新生成新的邀请链接。",
            },
        }

    if require_phone_verification and verify.get("locked"):
        return {
            "token": token,
            "step": "verify",
            "assignment": _serialize_assignment_payload(assignment),
            "invite_window": _invite_window_payload(start_date, end_date),
            "quiz": _build_quiz_preview(assignment),
            "verify": _build_verify_payload(assignment, verify=verify, sms=sms, pending_profile=pending_profile, candidate_id=candidate_id),
        }

    grading = assignment.get("grading") or {}
    if grading or status_text in {"grading", "graded"}:
        if isinstance(grading, dict) and str(grading.get("status") or "") in {"pending", "running"}:
            try:
                runtime_jobs.ensure_grade_attempt_job(token, source="public_bootstrap")
            except Exception:
                pass
        payload = _build_done_payload(token, assignment)
        payload["invite_window"] = _invite_window_payload(start_date, end_date)
        return payload

    if require_phone_verification and not bool(sms.get("verified")):
        return {
            "token": token,
            "step": "verify",
            "assignment": _serialize_assignment_payload(assignment),
            "invite_window": _invite_window_payload(start_date, end_date),
            "quiz": _build_quiz_preview(assignment),
            "verify": _build_verify_payload(assignment, verify=verify, sms=sms, pending_profile=pending_profile, candidate_id=candidate_id),
        }

    if status_text == "resume_pending" or candidate_id <= 0:
        return {
            "token": token,
            "step": "resume",
            "assignment": _serialize_assignment_payload(assignment),
            "invite_window": _invite_window_payload(start_date, end_date),
            "quiz": _build_quiz_preview(assignment),
            "resume": _build_resume_payload(assignment, pending_profile=pending_profile, sms=sms),
        }

    public_spec, quiz_metadata = _load_public_quiz_bundle(assignment)
    questions = list(public_spec.get("questions") or [])
    with deps.assignment_locked(token):
        assignment = deps.load_assignment(token)
        changed = _sync_assignment_time_limit_fields(assignment, public_spec)
        if _sync_question_timeouts(token, assignment, questions, now=datetime.now(timezone.utc)):
            assignment = deps.load_assignment(token)
        elif _register_public_session(token, assignment, session_id, now=datetime.now(timezone.utc)):
            assignment = deps.load_assignment(token)
        elif changed:
            deps.save_assignment(token, assignment)
            assignment = deps.load_assignment(token)

    if assignment.get("grading") or str(assignment.get("status") or "").strip() in {"grading", "graded"}:
        payload = _build_done_payload(token, assignment)
        payload["invite_window"] = _invite_window_payload(start_date, end_date)
        return payload

    return {
        "token": token,
        "step": "quiz",
        "assignment": _serialize_assignment_payload(assignment),
        "invite_window": _invite_window_payload(start_date, end_date),
        "quiz": _build_quiz_payload(assignment, public_spec, quiz_metadata),
    }


def _public_runtime_config() -> dict[str, Any]:
    defaults = load_runtime_defaults()
    payload = {
        "token_daily_threshold": int(defaults.token_daily_threshold),
        "sms_daily_threshold": int(defaults.sms_daily_threshold),
        "allow_public_assignments": bool(defaults.allow_public_assignments),
        "min_submit_seconds": int(defaults.min_submit_seconds),
        "ui_theme_name": str(defaults.ui_theme_name or "blue-green"),
    }
    current = deps.get_runtime_kv("runtime_config") or {}
    if isinstance(current, dict):
        payload.update(current)
    return payload


@router.get("/bootstrap")
def bootstrap():
    config = _public_runtime_config()
    return {
        "brand": {"name": "MD Quiz", "theme": str(config.get("ui_theme_name") or "blue-green")},
        "features": {
            "allow_public_assignments": bool(config.get("allow_public_assignments", True)),
            "min_submit_seconds": int(config.get("min_submit_seconds") or 60),
        },
    }


@router.post("/invites/{public_token}/ensure")
def ensure_public_invite(public_token: str, request: Request):
    return public_flow_service.ensure_public_invite(public_token, request)


@router.get("/invites/{public_token}/qr.png")
def public_invite_qr(public_token: str, request: Request):
    token_value = str(public_token or "").strip()
    if not token_value:
        raise HTTPException(status_code=404, detail="公开邀约不存在")
    quiz_key = exam_helpers._resolve_public_invite_quiz_key(token_value)
    if not quiz_key:
        raise HTTPException(status_code=404, detail="公开邀约不存在")
    cfg = exam_helpers.get_public_invite_config(quiz_key)
    if not bool(cfg.get("enabled")) or str(cfg.get("token") or "").strip() != token_value:
        raise HTTPException(status_code=404, detail="公开邀约不存在")
    try:
        import qrcode  # type: ignore
    except Exception as exc:
        raise HTTPException(status_code=500, detail="二维码依赖不可用") from exc
    public_url = f"{_public_base_url(request)}/p/{token_value}"
    qr = qrcode.QRCode(border=2)
    qr.add_data(public_url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    try:
        image.save(buffer, format="PNG")
    except TypeError:
        image.save(buffer)
    headers = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}
    return Response(content=buffer.getvalue(), media_type="image/png", headers=headers)


@router.get("/attempt/{token}")
def get_attempt_bootstrap(token: str, request: Request):
    return _bootstrap_attempt(token, session_id=_session_id_from_request(request))


@router.post("/attempt/{token}/enter")
def enter_quiz(token: str, request: Request):
    session_id = _session_id_from_request(request)
    with deps.assignment_locked(token):
        assignment = deps.load_assignment(token)
        if str(assignment.get("status") or "").strip() == "expired":
            raise HTTPException(status_code=410, detail="当前答题链接已失效")
        invite_state, _, _ = exam_helpers._invite_window_state(assignment)
        if invite_state in {"not_started", "expired"}:
            raise HTTPException(status_code=400, detail="当前不在可答题时间范围内")
        if (assignment.get("verify") or {}).get("locked"):
            raise HTTPException(status_code=410, detail="当前链接已失效")

        try:
            candidate_id = int(assignment.get("candidate_id") or 0)
        except Exception:
            candidate_id = 0
        if candidate_id <= 0:
            raise HTTPException(status_code=400, detail="请先完成身份验证")

        runtime_bootstrap._ensure_quiz_paper_for_token(token, assignment)
        public_spec, _quiz_metadata = _load_public_quiz_bundle(assignment)
        _sync_assignment_time_limit_fields(assignment, public_spec)
        timing = assignment.setdefault("timing", {})
        if not timing.get("start_at"):
            now = datetime.now(timezone.utc)
            timing["start_at"] = now.isoformat()
            flow = _normalize_question_flow(assignment)
            flow["current_index"] = max(0, int(flow.get("current_index") or 0))
            flow["current_started_at"] = now.isoformat()
            questions = list(public_spec.get("questions") or [])
            if questions:
                _, current_question = _current_question(questions, flow)
                answer_activity.question_record(assignment, str((current_question or {}).get("qid") or ""), flow["current_started_at"])
            if session_id:
                flow["active_session_id"] = session_id
                flow["last_session_seen_at"] = now.isoformat()
            try:
                deps.set_quiz_paper_entered_at(token, now)
            except Exception:
                pass
            try:
                candidate = deps.get_candidate(candidate_id) or {}
                deps.log_event(
                    "exam.enter",
                    actor="candidate",
                    candidate_id=int(candidate_id),
                    quiz_key=(str(assignment.get("quiz_key") or "").strip() or None),
                    token=(token or None),
                    meta={
                        "name": str(candidate.get("name") or "").strip(),
                        "phone": str(candidate.get("phone") or "").strip(),
                        "public_invite": bool(assignment.get("public_invite")),
                    },
                )
            except Exception:
                pass
        try:
            deps.set_quiz_paper_status(token, "in_quiz")
        except Exception:
            pass
        if str(assignment.get("status") or "").strip() not in {"in_quiz", "grading", "graded"}:
            assignment["status"] = "in_quiz"
            assignment["status_updated_at"] = datetime.now(timezone.utc).isoformat()
        deps.save_assignment(token, assignment)
    return _bootstrap_attempt(token, session_id=session_id)


@router.post("/sms/send")
def public_send_sms_code(payload: SmsSendPayload):
    return public_flow_service.send_sms_code(
        token=payload.token,
        name=payload.name,
        phone=payload.phone,
    )


@router.post("/verify")
def public_verify(payload: VerifyPayload):
    return public_flow_service.verify_assignment(
        token=payload.token,
        name=payload.name,
        phone=payload.phone,
        sms_code=payload.sms_code,
    )


@router.post("/resume/upload")
def public_resume_upload(request: Request, token: str = "", file: UploadFile = File(...)):
    _ = request
    return public_flow_service.upload_public_resume(token=token, file=file)


@router.post("/resume/use-existing")
def public_resume_use_existing(payload: UseExistingResumePayload):
    return public_flow_service.use_existing_public_resume(token=payload.token)


@router.post("/answers/{token}")
async def public_save_answer(token: str, request: Request):
    content_type = str(request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        body = await request.json()
        payload = AnswerActionPayload.model_validate(body if isinstance(body, dict) else {})
    else:
        form = await request.form()
        payload = AnswerActionPayload(
            question_id=str(form.get("question_id") or "").strip(),
            answer=(form.getlist("answer[]") or [form.get("answer")])[0] if not form.getlist("answer[]") else form.getlist("answer[]"),
            advance=str(form.get("advance") or "").strip().lower() in {"1", "true", "yes", "on"},
            submit=str(form.get("submit") or "").strip().lower() in {"1", "true", "yes", "on"},
            session_id=str(form.get("session_id") or "").strip(),
            force_timeout=str(form.get("force_timeout") or "").strip().lower() in {"1", "true", "yes", "on"},
        )
    session_id = _normalize_public_session_id(payload.session_id or _session_id_from_request(request))
    return _apply_answer_action(token, payload, session_id=session_id)


@router.post("/answers/{token}/activity")
def public_save_answer_activity(token: str, payload: ActivityBatchPayload):
    now = datetime.now(timezone.utc)
    with deps.assignment_locked(token):
        assignment = deps.load_assignment(token)
        if assignment.get("grading") or runtime_jobs._finalize_if_time_up(token, assignment, now=now):
            raise HTTPException(status_code=409, detail="already_submitted")
        if int(assignment.get("candidate_id") or 0) <= 0:
            raise HTTPException(status_code=400, detail="请先完成身份验证")
        public_spec, _ = _load_public_quiz_bundle(assignment)
        questions = list(public_spec.get("questions") or [])
        if _sync_question_timeouts(token, assignment, questions, now=now):
            raise HTTPException(status_code=409, detail="already_submitted")
        flow = _normalize_question_flow(assignment)
        _, question = _current_question(questions, flow)
        if not question:
            raise HTTPException(status_code=400, detail="题目不存在")
        accepted = _record_activity_batch(assignment, question, flow, payload, now=now)
        if accepted or payload.dropped_events:
            deps.save_assignment(token, assignment)
    return {"accepted": accepted}


@router.post("/answers_bulk/{token}")
async def public_save_answers_bulk(token: str, request: Request):
    body = await request.json()
    answers = body.get("answers") if isinstance(body, dict) else None
    if not isinstance(answers, dict):
        raise HTTPException(status_code=400, detail="invalid_payload")
    items = [(str(key or "").strip(), value) for key, value in answers.items() if str(key or "").strip()]
    if len(items) != 1:
        raise HTTPException(status_code=400, detail="当前仅支持逐题自动保存")
    question_id, value = items[0]
    payload = AnswerActionPayload(
        question_id=question_id,
        answer=value,
        session_id=_normalize_public_session_id(body.get("session_id") if isinstance(body, dict) else ""),
    )
    session_id = _normalize_public_session_id(payload.session_id or _session_id_from_request(request))
    return _apply_answer_action(token, payload, session_id=session_id)


@router.post("/submit/{token}")
def public_submit(token: str):
    with deps.assignment_locked(token):
        assignment = deps.load_assignment(token)
        invite_state, _, _ = exam_helpers._invite_window_state(assignment)
        if invite_state in {"not_started", "expired"}:
            raise HTTPException(status_code=400, detail="当前不在可答题时间范围内")
        runtime_bootstrap._ensure_quiz_paper_for_token(token, assignment)
        if (assignment.get("verify") or {}).get("locked"):
            raise HTTPException(status_code=410, detail="当前链接已失效")
        if assignment.get("grading"):
            return {"ok": True, "redirect": f"/done/{token}"}
        now = datetime.now(timezone.utc)
        public_spec, _quiz_metadata = _load_public_quiz_bundle(assignment)
        questions = list(public_spec.get("questions") or [])
        _sync_assignment_time_limit_fields(assignment, public_spec)
        if _sync_question_timeouts(token, assignment, questions, now=now):
            return {"ok": True, "redirect": f"/done/{token}"}
        assignment = deps.load_assignment(token)
        flow = _normalize_question_flow(assignment)
        if int(flow.get("current_index") or 0) < max(0, len(questions) - 1):
            raise HTTPException(status_code=409, detail="not_last_question")
        runtime_jobs._finalize_public_submission(token, assignment, now=now)
    return {"ok": True, "redirect": f"/done/{token}"}
