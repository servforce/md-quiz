from __future__ import annotations

from datetime import timedelta

from backend.md_quiz.services.exam_helpers import *
from backend.md_quiz.services.support_deps import *
from backend.md_quiz.services.validation_helpers import *
from backend.md_quiz.services import answer_activity

def _timing_ignored(assignment: dict) -> bool:
    return bool((assignment or {}).get("ignore_timing"))


def _remaining_seconds(assignment: dict) -> int:
    if _timing_ignored(assignment):
        return 0
    limit = int(assignment.get("time_limit_seconds") or 0)
    start = (assignment.get("timing") or {}).get("start_at")
    if not start or limit <= 0:
        return 0
    try:
        started = datetime.fromisoformat(start.replace("Z", "+00:00"))
    except Exception:
        return 0
    used = int((datetime.now(timezone.utc) - started).total_seconds())
    return max(0, limit - used)


def _is_time_up(assignment: dict, *, now: datetime | None = None) -> bool:
    if _timing_ignored(assignment):
        return False
    limit = int(assignment.get("time_limit_seconds") or 0)
    if limit <= 0:
        return False
    started_at = _parse_iso_dt((assignment.get("timing") or {}).get("start_at"))
    if not started_at:
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    elapsed = max(0, int((now - started_at).total_seconds()))
    return elapsed >= limit


def _finalize_if_time_up(token: str, assignment: dict, *, now: datetime | None = None) -> bool:
    if assignment.get("grading"):
        return True
    if now is None:
        now = datetime.now(timezone.utc)
    if not _is_time_up(assignment, now=now):
        return False
    started_at = _parse_iso_dt((assignment.get("timing") or {}).get("start_at"))
    deadline = started_at + timedelta(seconds=int(assignment.get("time_limit_seconds") or 0)) if started_at else now
    flow = assignment.get("question_flow") or {}
    snapshot = get_exam_snapshot_for_assignment(assignment) or {}
    questions = ((snapshot.get("public_spec") or {}).get("questions") or [])
    index = max(0, int(flow.get("current_index") or 0))
    while index < len(questions) - 1:
        question_start = _parse_iso_dt(flow.get("current_started_at"))
        seconds = max(0, int((questions[index] or {}).get("answer_time_seconds") or 0))
        if not question_start or not seconds or question_start + timedelta(seconds=seconds) > deadline:
            break
        question_end = question_start + timedelta(seconds=seconds)
        answer_activity.close_question(assignment, str((questions[index] or {}).get("qid") or ""), question_start, question_end, "timeout")
        index += 1
        flow["current_index"] = index
        flow["current_started_at"] = question_end.isoformat()
    _finalize_public_submission(token, assignment, now=deadline, reason="exam_timeout")
    return True


def _duration_seconds(assignment: dict) -> int | None:
    timing = assignment.get("timing") or {}
    start = timing.get("start_at")
    end = timing.get("end_at")
    if not start or not end:
        return None
    try:
        started = datetime.fromisoformat(start.replace("Z", "+00:00"))
        ended = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return max(0, int((ended - started).total_seconds()))
    except Exception:
        return None


def _finalize_public_submission(token: str, assignment: dict, *, now: datetime, reason: str = "submit") -> None:
    """
    Finalize a candidate submission by marking it submitted and enqueueing background grading.

    Caller is responsible for locking (assignment_locked) if needed.
    """
    if assignment.get("grading"):
        return

    flow = assignment.get("question_flow") or {}
    started_at = flow.get("current_started_at")
    if started_at:
        snapshot = get_exam_snapshot_for_assignment(assignment) or {}
        public_spec = snapshot.get("public_spec") or {}
        questions = public_spec.get("questions") or []
        index = max(0, int(flow.get("current_index") or 0))
        if index < len(questions):
            qid = str((questions[index] or {}).get("qid") or "").strip()
            answer_activity.close_question(assignment, qid, started_at, now, reason)

    assignment.setdefault("timing", {})["end_at"] = now.isoformat()
    assignment["status"] = "grading"
    assignment["status_updated_at"] = now.isoformat()
    assignment["grading_started_at"] = assignment.get("grading_started_at") or now.isoformat()
    assignment["graded_at"] = None
    assignment["grading_error"] = None
    assignment["grading"] = {"status": "pending", "queued_at": now.isoformat()}
    save_assignment(token, assignment)

    duration_seconds = _duration_seconds(assignment)
    timing = assignment.get("timing") or {}
    started_at = _parse_iso_dt(timing.get("start_at"))
    submitted_at = _parse_iso_dt(timing.get("end_at"))
    try:
        update_quiz_paper_result(
            token,
            status="grading",
            score=None,
            entered_at=started_at,
            finished_at=submitted_at,
        )
    except Exception:
        logger.exception("Update quiz_paper grading state failed (token=%s)", token)

    try:
        cid2 = int(assignment.get("candidate_id") or 0)
    except Exception:
        cid2 = 0
    try:
        ek2 = str(assignment.get("quiz_key") or "").strip()
    except Exception:
        ek2 = ""
    name2 = ""
    phone2 = ""
    if cid2 > 0:
        try:
            c2 = get_candidate(cid2) or {}
            name2 = str(c2.get("name") or "").strip()
            phone2 = str(c2.get("phone") or "").strip()
        except Exception:
            name2 = ""
            phone2 = ""
    if not phone2:
        try:
            sms2 = assignment.get("sms_verify") or {}
            if isinstance(sms2, dict):
                phone2 = str(sms2.get("phone") or "").strip()
        except Exception:
            pass
    try:
        log_event(
            "exam.finish",
            actor="candidate",
            candidate_id=(cid2 if cid2 > 0 else None),
            quiz_key=(ek2 or None),
            token=(str(token or "").strip() or None),
            started_at=started_at,
            finished_at=submitted_at,
            duration_seconds=duration_seconds,
            meta={
                "name": name2,
                "phone": phone2,
                "public_invite": bool(assignment.get("public_invite")),
            },
        )
    except Exception:
        pass

    try:
        ensure_grade_attempt_job(token, source="public_submission", assignment=assignment)
    except Exception:
        logger.exception("Enqueue grade attempt failed (token=%s)", token)


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def ensure_grade_attempt_job(token: str, *, source: str = "runtime_jobs", assignment: dict[str, Any] | None = None):
    t = str(token or "").strip()
    if not t:
        return None

    current = assignment
    if current is None:
        with assignment_locked(t):
            current = load_assignment(t)

    grading = current.get("grading") or {}
    grading_status = str((grading.get("status") if isinstance(grading, dict) else "") or "").strip()
    if grading_status == "done" and _find_archive_by_token(t, assignment=current):
        return None
    if not grading and str(current.get("status") or "").strip() not in {"grading", "graded"}:
        return None

    from backend.md_quiz.services.job_service import JobService
    from backend.md_quiz.storage import JobStore

    return JobService(JobStore()).ensure_grade_attempt(t, source=source)


def _start_background_grading(token: str) -> None:
    # 兼容旧调用点：不再起线程，只做幂等投递。
    ensure_grade_attempt_job(token)


def _sync_grade_side_effects(
    token: str,
    assignment: dict[str, Any],
    *,
    spec: dict[str, Any] | None,
    grading: dict[str, Any],
    grading_llm_total_tokens: int = 0,
    emit_grade_log: bool,
) -> None:
    try:
        timing = assignment.get("timing") or {}
        started_at = _parse_iso_dt(timing.get("start_at"))
        submitted_at = _parse_iso_dt(timing.get("end_at"))
        update_quiz_paper_result(
            token,
            status="finished",
            score=int((grading or {}).get("total") or 0),
            entered_at=started_at,
            finished_at=submitted_at,
        )
    except Exception:
        logger.exception("Update quiz_paper graded result failed (token=%s)", token)

    if emit_grade_log:
        try:
            cid2 = int((assignment or {}).get("candidate_id") or 0)
        except Exception:
            cid2 = 0
        try:
            ek2 = str((assignment or {}).get("quiz_key") or "").strip()
        except Exception:
            ek2 = ""
        try:
            score2 = int((grading or {}).get("total") or 0)
        except Exception:
            score2 = 0
        grading_started_at = None
        grading_finished_at = None
        if isinstance(grading, dict):
            grading_started_at = _parse_iso_dt(str(grading.get("started_at") or "").strip())
            grading_finished_at = _parse_iso_dt(str(grading.get("finished_at") or "").strip())
        try:
            log_event(
                "exam.grade",
                actor="system",
                candidate_id=(cid2 if cid2 > 0 else None),
                quiz_key=(ek2 or None),
                token=(str(token or "").strip() or None),
                llm_total_tokens=(int(grading_llm_total_tokens or 0) or None),
                started_at=grading_started_at,
                finished_at=grading_finished_at,
                meta={"score": score2, "public_invite": bool((assignment or {}).get("public_invite"))},
            )
        except Exception:
            pass

    _archive_candidate_attempt(assignment, spec=spec)


def process_grade_attempt_job(token: str) -> dict[str, Any]:
    t = str(token or "").strip()
    if not t:
        raise ValueError("缺少 token")

    archive_only = False
    finalized_assignment: dict[str, Any] | None = None
    snapshot: dict[str, Any] | None = None

    with assignment_locked(t):
        assignment = load_assignment(t)
        grading0 = assignment.get("grading") or {}
        grading_status = str((grading0.get("status") if isinstance(grading0, dict) else "") or "").strip()

        if grading_status == "done":
            if _find_archive_by_token(t, assignment=assignment):
                return {"message": "判卷任务跳过，结果已存在", "status": "noop", "token": t}
            archive_only = True
            finalized_assignment = dict(assignment)
        else:
            if not grading0 and str(assignment.get("status") or "").strip() not in {"grading", "graded"}:
                raise RuntimeError("assignment 未进入判卷状态")

            now = datetime.now(timezone.utc)
            queued_at = (
                str((grading0.get("queued_at") if isinstance(grading0, dict) else "") or "").strip()
                or str(assignment.get("grading_started_at") or "").strip()
                or now.isoformat()
            )
            assignment["grading"] = {
                "status": "running",
                "queued_at": queued_at,
                "started_at": now.isoformat(),
            }
            assignment["status"] = "grading"
            assignment["status_updated_at"] = now.isoformat()
            assignment["grading_started_at"] = assignment.get("grading_started_at") or now.isoformat()
            assignment["grading_error"] = None
            save_assignment(t, assignment)
            snapshot = dict(assignment)

    if archive_only:
        grading = finalized_assignment.get("grading") or {}
        if not isinstance(grading, dict) or not grading:
            raise RuntimeError("assignment 缺少判卷结果，无法补齐归档")
        _sync_grade_side_effects(
            t,
            finalized_assignment,
            spec=None,
            grading=grading,
            grading_llm_total_tokens=0,
            emit_grade_log=False,
        )
        return {"message": "判卷任务已补齐归档", "status": "archived", "token": t}

    grading_llm_total_tokens = 0
    try:
        exam = get_exam_snapshot_for_assignment(snapshot) or {}
        spec = exam.get("spec") if isinstance(exam.get("spec"), dict) else {}
        if not spec:
            raise FileNotFoundError(str(snapshot.get("quiz_key") or ""))
        with audit_context(meta={}):
            grading = grade_attempt(spec, snapshot)
            if isinstance(grading, dict):
                grading["status"] = "done"
            remark = generate_candidate_remark(spec, snapshot, grading) if grading else ""
            try:
                ctx = get_audit_context()
                meta = ctx.get("meta")
                if isinstance(meta, dict):
                    grading_llm_total_tokens = int(meta.get("llm_total_tokens_sum") or 0)
            except Exception:
                grading_llm_total_tokens = 0
    except Exception as exc:
        logger.exception("Grade attempt failed (token=%s)", t)
        with assignment_locked(t):
            assignment = load_assignment(t)
            current = assignment.get("grading") or {}
            failed_at = datetime.now(timezone.utc).isoformat()
            failed = {"status": "failed", "failed_at": failed_at}
            if isinstance(current, dict):
                queued_at = str(current.get("queued_at") or "").strip()
                started_at = str(current.get("started_at") or "").strip()
                if queued_at:
                    failed["queued_at"] = queued_at
                if started_at:
                    failed["started_at"] = started_at
            assignment["grading"] = failed
            assignment["grading_error"] = f"{type(exc).__name__}: {exc}"
            assignment["status"] = "grading"
            assignment["status_updated_at"] = failed_at
            save_assignment(t, assignment)
        raise

    with assignment_locked(t):
        assignment = load_assignment(t)
        current = assignment.get("grading") or {}
        current_status = str((current.get("status") if isinstance(current, dict) else "") or "").strip()
        if current_status == "done" and _find_archive_by_token(t, assignment=assignment):
            return {"message": "判卷任务跳过，结果已存在", "status": "noop", "token": t}

        finished_at = datetime.now(timezone.utc).isoformat()
        if isinstance(grading, dict):
            queued_at = str((current.get("queued_at") if isinstance(current, dict) else "") or "").strip()
            started_at = str((current.get("started_at") if isinstance(current, dict) else "") or "").strip()
            if queued_at:
                grading.setdefault("queued_at", queued_at)
            if started_at:
                grading.setdefault("started_at", started_at)
            grading["finished_at"] = finished_at
            grading["status"] = "done"

        assignment["grading"] = grading
        assignment["candidate_remark"] = remark
        assignment["graded_at"] = finished_at
        assignment["status"] = "graded"
        assignment["status_updated_at"] = finished_at
        assignment["grading_error"] = None
        save_assignment(t, assignment)
        finalized_assignment = dict(assignment)

    _sync_grade_side_effects(
        t,
        finalized_assignment,
        spec=spec,
        grading=(finalized_assignment.get("grading") or grading),
        grading_llm_total_tokens=grading_llm_total_tokens,
        emit_grade_log=True,
    )

    return {
        "message": "判卷任务已完成",
        "status": "done",
        "token": t,
        "candidate_id": int(finalized_assignment.get("candidate_id") or 0),
        "quiz_key": str(finalized_assignment.get("quiz_key") or "").strip(),
    }


def _auto_collect_loop(*, interval_seconds: int = 15) -> None:
    """
    Background loop: once countdown starts (timing.start_at is set), auto-submit when time is up,
    even if the candidate is not actively on the page.
    """
    while True:
        try:
            for token in list_assignment_tokens():
                try:
                    with assignment_locked(token):
                        assignment = load_assignment(token)
                        g = assignment.get("grading")
                        if isinstance(g, dict) and str(g.get("status") or "").strip() in {"pending", "running"}:
                            # Ensure grading proceeds even after process restarts.
                            try:
                                ensure_grade_attempt_job(token, source="auto_collect", assignment=assignment)
                            except Exception:
                                pass
                            continue
                        if g:
                            continue
                        _finalize_if_time_up(token, assignment)
                except Exception:
                    logger.exception("Auto-collect scan failed (token=%s)", token)
        except Exception:
            logger.exception("Auto-collect loop failed")
        time_module.sleep(interval_seconds)


def _parse_duration_seconds(value: str | None) -> int:
    v = (value or "").strip()
    if not v:
        return 0
    if ":" not in v:
        try:
            return max(0, int(v))
        except Exception:
            return 0

    parts = [p.strip() for p in v.split(":")]
    if len(parts) not in (2, 3):
        return 0
    try:
        nums = [int(p) for p in parts]
    except Exception:
        return 0
    if any(n < 0 for n in nums):
        return 0

    if len(nums) == 3:
        h, m, s = nums
    else:
        h = 0
        m, s = nums
    if m >= 60 or s >= 60:
        return 0
    return h * 3600 + m * 60 + s


def _stable_archive_filename(phone: str, token: str, quiz_key: str) -> str:
    phone_part = _sanitize_archive_part(str(phone or ""))
    token_part = _sanitize_archive_part(str(token or ""))
    exam_part = _sanitize_archive_part(str(quiz_key or ""))

    # Stable per token: one attempt -> one archive file (overwrite on re-save).
    # Must keep suffix compatible with existing glob patterns: *_{phone}_*_{quiz_key}.json
    suffix_parts = [p for p in (phone_part, token_part, exam_part) if p]
    suffix = "_".join(suffix_parts) or "attempt"
    raw = f"latest_{suffix}".strip("._")
    if len(raw) > 160:
        raw = raw[:160].rstrip("._")
    return f"{raw}.json"
def _sanitize_archive_part(value: str) -> str:
    raw = (value or "").strip()
    raw = _FILENAME_UNSAFE_RE.sub("_", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    raw = raw.replace(" ", "_")
    raw = raw.strip("._")
    return raw


def _try_load_public_spec(quiz_key: str) -> dict | None:
    exam = get_quiz_definition(str(quiz_key or "").strip()) or {}
    public_spec = exam.get("public_spec")
    return public_spec if isinstance(public_spec, dict) else None


def _try_load_public_spec_for_assignment(assignment: dict) -> dict | None:
    exam = get_exam_snapshot_for_assignment(assignment) or {}
    public_spec = exam.get("public_spec")
    return public_spec if isinstance(public_spec, dict) else None


def _redact_spec_for_archive(spec: dict) -> dict:
    # 当公开测验对象缺失时，回退到完整测验并去掉标准答案。
    out = dict(spec or {})
    questions = []
    for q in (spec or {}).get("questions", []) or []:
        q2 = dict(q)
        if q2.get("options"):
            opts = []
            for o in q2.get("options") or []:
                oo = dict(o)
                oo.pop("correct", None)
                opts.append(oo)
            q2["options"] = opts
        questions.append(q2)
    out["questions"] = questions
    return out


def _archive_candidate_attempt(assignment: dict, *, spec: dict | None = None) -> None:
    try:
        candidate_id = int(assignment.get("candidate_id") or 0)
    except Exception:
        return
    if candidate_id <= 0:
        return

    c = get_candidate(candidate_id)
    if not c:
        return
    quiz_key = str(assignment.get("quiz_key") or "")
    if not quiz_key:
        return
    try:
        quiz_version_id = int(assignment.get("quiz_version_id") or 0)
    except Exception:
        quiz_version_id = 0

    grading = assignment.get("grading") or {}
    answers = assignment.get("answers") or {}

    # Use full spec when available to persist correct answers for admin review.
    if spec is None:
        exam = get_exam_snapshot_for_assignment(assignment) or {}
        spec = exam.get("spec") if isinstance(exam.get("spec"), dict) else {}
    public_spec = _try_load_public_spec_for_assignment(assignment)
    if public_spec is None:
        public_spec = _redact_spec_for_archive(spec or {})

    scored_by_qid: dict[str, dict] = {}
    for d in (grading.get("objective") or []):
        scored_by_qid[str(d.get("qid"))] = dict(d)
    for d in (grading.get("subjective") or []):
        scored_by_qid[str(d.get("qid"))] = dict(d)

    spec_by_qid = {str(q.get("qid") or ""): q for q in ((spec or {}).get("questions") or [])}
    public_by_qid = {str(q.get("qid") or ""): q for q in (public_spec.get("questions") or [])}

    questions_out = []
    for qid, full_q in spec_by_qid.items():
        pub_q = public_by_qid.get(qid) or {}
        qtype = full_q.get("type") or pub_q.get("type")
        sd = scored_by_qid.get(qid) or {}
        ans = answers.get(qid)
        options_out = None
        if qtype in {"single", "multiple"}:
            options_out = []
            for o in (full_q.get("options") or []):
                options_out.append(
                    {
                        "key": o.get("key"),
                        "text": o.get("text"),
                        "correct": bool(o.get("correct")),
                    }
                )
        item = {
            "qid": qid,
            "label": full_q.get("label") or pub_q.get("label") or qid,
            "type": qtype,
            "max_points": full_q.get("max_points") or full_q.get("points") or pub_q.get("max_points") or pub_q.get("points"),
            "stem_md": pub_q.get("stem_md") or full_q.get("stem_md"),
            "options": options_out or pub_q.get("options"),
            "rubric": full_q.get("rubric"),
            "answer": ans,
            "score": sd.get("score"),
            "score_max": sd.get("max") or (full_q.get("max_points") or full_q.get("points") or pub_q.get("max_points") or pub_q.get("points")),
            "reason": sd.get("reason"),
            "activity": (assignment.get("question_activity") or {}).get(qid),
        }
        questions_out.append(item)

    # Keep ordering consistent with spec.
    def _qid_key(x: dict) -> int:
        v = str(x.get("qid") or "")
        m = re.match(r"^Q(\\d+)$", v)
        return int(m.group(1)) if m else 10**9

    questions_out.sort(key=_qid_key)

    now_iso = datetime.now(timezone.utc).isoformat()
    token = str(assignment.get("token") or "").strip()
    archive = {
        "saved_at": now_iso,
        "token": token,
        "candidate": {"id": c.get("id"), "name": c.get("name"), "phone": c.get("phone")},
        "exam": {
            "quiz_key": quiz_key,
            "quiz_version_id": (quiz_version_id or None),
            "title": public_spec.get("title"),
            "description": public_spec.get("description"),
        },
        "timing": assignment.get("timing") or {},
        "time_limit_seconds": int(assignment.get("time_limit_seconds") or 0),
        "min_submit_seconds": int(assignment.get("min_submit_seconds") or 0),
        "total_score": grading.get("total"),
        "score_max": grading.get("total_max"),
        "result_mode": grading.get("result_mode"),
        "traits": grading.get("traits") or grading.get("trait_result") or {},
        "final_analysis": grading.get("final_analysis") or grading.get("analysis") or "",
        "raw_scored": grading.get("raw_scored"),
        "raw_total": grading.get("raw_total"),
        "grading": grading,
        "candidate_remark": assignment.get("candidate_remark"),
        "answers": answers,
        "questions": questions_out,
    }

    filename = _stable_archive_filename(str(c.get("phone") or ""), token, quiz_key)
    save_quiz_archive(
        archive_name=filename,
        token=token,
        candidate_id=int(candidate_id),
        quiz_key=quiz_key,
        quiz_version_id=(quiz_version_id or None),
        phone=str(c.get("phone") or ""),
        archive=archive,
    )


def _find_latest_archive(candidate: dict) -> dict[str, Any] | None:
    try:
        phone = str(candidate.get("phone") or "").strip()
    except Exception:
        return None
    if not phone:
        return None
    rows = list_quiz_archives_for_phone(phone)
    return rows[0] if rows else None


def _find_archive_by_token(token: str, *, assignment: dict | None = None) -> dict[str, Any] | None:
    row = get_quiz_archive_by_token(str(token or "").strip())
    if row:
        return row
    if not assignment:
        return None
    try:
        candidate_id = int(assignment.get("candidate_id") or 0)
    except Exception:
        candidate_id = 0
    if candidate_id <= 0:
        return None
    c = get_candidate(candidate_id) or {}
    phone = str(c.get("phone") or "").strip()
    if not phone:
        return None
    rows = list_quiz_archives_for_phone(phone)
    return rows[0] if rows else None


def _augment_archive_with_spec(archive: dict) -> dict:
    """
    尽力用当前测验定义补全归档里的标签、rubric 和标准答案信息。
    """
    try:
        quiz_key = str(((archive.get("exam") or {}).get("quiz_key")) or "").strip()
    except Exception:
        quiz_key = ""
    try:
        quiz_version_id = int(((archive.get("exam") or {}).get("quiz_version_id")) or 0)
    except Exception:
        quiz_version_id = 0
    if not quiz_key:
        return archive
    if quiz_version_id > 0:
        exam = get_quiz_version_snapshot(quiz_version_id) or {}
    else:
        exam = get_quiz_definition(quiz_key) or {}
    spec = exam.get("spec")
    if not isinstance(spec, dict):
        return archive

    spec_by_qid = {str(q.get("qid") or ""): q for q in (spec.get("questions") or [])}
    for q in (archive.get("questions") or []):
        qid = str(q.get("qid") or "")
        full_q = spec_by_qid.get(qid) or {}
        if not q.get("label") and (full_q.get("label") or full_q.get("qid")):
            q["label"] = full_q.get("label") or full_q.get("qid")
        if not q.get("rubric") and full_q.get("rubric"):
            q["rubric"] = full_q.get("rubric")
        if q.get("type") in {"single", "multiple"}:
            # If options don't carry correctness, fill them from spec.
            opts = q.get("options") or []
            has_correct = any(isinstance(o, dict) and ("correct" in o) for o in opts)
            if not has_correct and full_q.get("options"):
                q["options"] = [
                    {"key": o.get("key"), "text": o.get("text"), "correct": bool(o.get("correct"))}
                    for o in (full_q.get("options") or [])
                ]
        if not q.get("stem_md") and full_q.get("stem_md"):
            q["stem_md"] = full_q.get("stem_md")
    return archive


def _sync_quiz_paper_finished_from_assignment(assignment: dict) -> None:
    token = str(assignment.get("token") or "").strip()
    if not token:
        return
    grading = assignment.get("grading") or {}
    if not grading:
        return
    if isinstance(grading, dict) and str(grading.get("status") or "").strip() in {"pending", "running"}:
        return
    if not isinstance(grading, dict) or ("total" not in grading):
        return

    timing = assignment.get("timing") or {}
    started_at = _parse_iso_dt(timing.get("start_at"))
    submitted_at = _parse_iso_dt(timing.get("end_at"))
    duration_seconds = _duration_seconds(assignment)
    try:
        try:
            _archive_candidate_attempt(assignment)
        except Exception:
            logger.exception("Archive candidate attempt failed (token=%s)", token)
        update_quiz_paper_result(
            token,
            status="finished",
            score=int(grading.get("total") or 0),
            entered_at=started_at,
            finished_at=submitted_at,
        )
    except Exception:
        logger.exception("Sync quiz_paper finished failed (token=%s)", token)
        return

__all__ = [name for name in globals() if not name.startswith("__")]
