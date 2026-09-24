from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.md_quiz.api import admin as admin_api
from backend.md_quiz.services import answer_activity, runtime_jobs
from backend.md_quiz.storage.db import create_assignment_record, create_candidate, get_assignment_record, get_quiz_archive_by_token
from test_fastapi_app import _admin_login, _build_client, _seed_exam_with_answer_time


def _assignment(token: str, quiz_key: str, version_id: int, candidate_id: int, *, ignore_timing: bool = False) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "token": token, "quiz_key": quiz_key, "quiz_version_id": version_id, "candidate_id": candidate_id,
        "created_at": now, "status": "verified", "status_updated_at": now,
        "invite_window": {"start_date": None, "end_date": None},
        "time_limit_seconds": 0 if ignore_timing else 150, "min_submit_seconds": 0,
        "ignore_timing": ignore_timing, "verify": {"attempts": 0, "locked": False},
        "sms_verify": {"verified": True},
        "question_flow": {"current_index": 0, "current_started_at": None, "active_session_id": ""},
        "timing": {"start_at": None, "end_at": None}, "answers": {}, "grading": None,
    }


def _event(seq: int, kind: str, added: int, *, elapsed_ms: int = 0) -> dict:
    return {"seq": seq, "elapsed_ms": elapsed_ms, "kind": kind,
            "added_chars": added, "deleted_chars": 0, "length_after": added}


def test_question_duration_input_batches_and_admin_archive(monkeypatch, tmp_path):
    client = _build_client(monkeypatch, tmp_path)
    key, token, sid = "activity-flow-demo", "activityflow01", "activity-session"
    version = _seed_exam_with_answer_time(key)
    candidate = create_candidate("输入记录测试", "13900000082")
    create_assignment_record(token, _assignment(token, key, version, candidate))

    entered = client.post(f"/api/public/attempt/{token}/enter", headers={"X-Public-Session-Id": sid})
    assert entered.status_code == 200
    assert "question_activity" not in entered.json()["assignment"]
    assert datetime.fromisoformat(entered.json()["quiz"]["server_now"])
    assert entered.json()["quiz"]["question_flow"]["current_started_at"]
    for qid, answer in (("Q1", "A"), ("Q2", ["A"])):
        response = client.post(f"/api/public/answers/{token}", json={
            "question_id": qid, "answer": answer, "advance": True, "session_id": sid,
        })
        assert response.status_code == 200

    batch = {"question_id": "Q3", "session_id": sid, "capture_id": "page-1",
             "events": [_event(1, "paste", 30), _event(2, "insert", 50)]}
    posted = client.post(f"/api/public/answers/{token}/activity", json=batch)
    assert posted.status_code == 200
    assert posted.json()["accepted"] == 2
    assert "Q3" not in get_assignment_record(token)["answers"]
    assert client.post(f"/api/public/answers/{token}/activity", json=batch).json()["accepted"] == 0
    assert client.post(f"/api/public/answers/{token}/activity", json={**batch, "session_id": "wrong"}).status_code == 409
    assert client.post(f"/api/public/answers/{token}/activity", json={
        **batch, "events": [{**_event(3, "insert", 1), "text": "不得保存"}],
    }).status_code == 422
    assert client.get(f"/api/public/attempt/{token}", headers={"X-Public-Session-Id": sid}).status_code == 200
    reloaded_batch = {**batch, "capture_id": "page-2", "events": [_event(1, "composition", 2)]}
    assert client.post(f"/api/public/answers/{token}/activity", json=reloaded_batch).json()["accepted"] == 1

    submitted = client.post(f"/api/public/answers/{token}", json={
        "question_id": "Q3", "answer": "最终答案", "submit": True, "session_id": sid,
        "activity": {**batch, "events": [_event(3, "delete", 0)]},
    })
    assert submitted.status_code == 200
    assignment = get_assignment_record(token)
    assert assignment is not None
    records = assignment["question_activity"]
    assert [records[qid]["end_reason"] for qid in ("Q1", "Q2", "Q3")] == ["advance", "advance", "submit"]
    assert all(records[qid]["duration_ms"] >= 0 for qid in ("Q1", "Q2", "Q3"))
    input_data = records["Q3"]["input"]
    assert input_data["summary"]["event_count"] == 4
    assert [item["kind"] for item in input_data["signals"]] == ["large_paste", "large_insert"]
    assert "不得保存" not in str(records)
    assert "question_activity" not in submitted.json()["assignment"]

    runtime_jobs._archive_candidate_attempt(assignment)
    archive_row = get_quiz_archive_by_token(token)
    assert archive_row["archive"]["questions"][2]["activity"]["duration_ms"] >= 0
    _admin_login(client)
    detail = client.get(f"/api/admin/attempts/{token}")
    assert detail.status_code == 200
    review = detail.json()["review"]["answers"]
    assert review[2]["activity"]["input"]["summary"]["event_count"] == 4
    assert "last_seq_by_capture" not in review[2]["activity"]["input"]


def test_overdue_questions_and_ignore_timing_still_record_duration(monkeypatch, tmp_path):
    client = _build_client(monkeypatch, tmp_path)
    key = "activity-timeout-demo"
    version = _seed_exam_with_answer_time(key)
    candidate = create_candidate("超时记录测试", "13900000083")
    started = datetime.now(timezone.utc) - timedelta(seconds=170)
    token = "activitytimeout01"
    assignment = _assignment(token, key, version, candidate)
    assignment["timing"]["start_at"] = started.isoformat()
    assignment["question_flow"]["current_started_at"] = started.isoformat()
    assignment["status"] = "in_quiz"
    create_assignment_record(token, assignment)
    response = client.get(f"/api/public/attempt/{token}")
    assert response.status_code == 200
    records = get_assignment_record(token)["question_activity"]
    assert [records[qid]["duration_ms"] for qid in ("Q1", "Q2", "Q3")] == [45_000, 90_000, 15_000]
    assert records["Q3"]["end_reason"] == "exam_timeout"

    token = "activityignore01"
    create_assignment_record(token, _assignment(token, key, version, candidate, ignore_timing=True))
    entered = client.post(f"/api/public/attempt/{token}/enter", headers={"X-Public-Session-Id": "ignore-session"})
    assert entered.status_code == 200
    advanced = client.post(f"/api/public/answers/{token}", json={
        "question_id": "Q1", "answer": "A", "advance": True, "session_id": "ignore-session",
    })
    assert advanced.status_code == 200
    record = get_assignment_record(token)["question_activity"]["Q1"]
    assert record["duration_ms"] >= 0
    assert record["end_reason"] == "advance"


def test_single_question_timeout_and_activity_cap(monkeypatch, tmp_path):
    client = _build_client(monkeypatch, tmp_path)
    key = "activity-one-timeout-demo"
    version = _seed_exam_with_answer_time(key)
    candidate = create_candidate("单题超时测试", "13900000084")
    token = "activityonetimeout01"
    started = datetime.now(timezone.utc) - timedelta(seconds=50)
    assignment = _assignment(token, key, version, candidate)
    assignment["timing"]["start_at"] = started.isoformat()
    assignment["question_flow"]["current_started_at"] = started.isoformat()
    assignment["status"] = "in_quiz"
    create_assignment_record(token, assignment)
    response = client.get(f"/api/public/attempt/{token}")
    assert response.status_code == 200
    assert response.json()["quiz"]["question_flow"]["current_index"] == 1
    saved = get_assignment_record(token)["question_activity"]
    assert saved["Q1"]["duration_ms"] == 45_000
    assert saved["Q1"]["end_reason"] == "timeout"

    activity = {}
    monkeypatch.setattr(answer_activity, "MAX_STORED_EVENTS", 1)
    accepted = answer_activity.record_input_batch(activity, "Q3", started.isoformat(), {
        "capture_id": "one-page", "events": [_event(1, "insert", 1), _event(2, "paste", 30)],
    })
    assert accepted == 2
    input_data = activity["question_activity"]["Q3"]["input"]
    assert input_data["summary"]["event_count"] == 2
    assert len(input_data["events"]) == 1
    assert input_data["truncated"] is True
    assert input_data["signals"][0]["kind"] == "large_paste"
    assert admin_api._build_review_answer_item({"qid": "Q0", "type": "short", "answer": "旧答案"})["activity"] is None
