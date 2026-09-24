from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.md_quiz.app import create_app
from backend.md_quiz.storage.db import (
    conn_scope,
    count_job_descriptions,
    count_system_logs,
    create_candidate,
    create_quiz_paper,
    create_quiz_version,
    create_system_log,
    delete_candidate,
    save_quiz_definition,
    set_exam_public_invite,
    set_quiz_paper_handling,
    update_candidate_resume,
    update_quiz_paper_result,
    upsert_runtime_job,
)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MCP_ENABLED", "false")
    monkeypatch.setenv("APP_SECRET_KEY", "admin-redesign-test-secret")
    # 不进入 lifespan；这些只读接口不需要启动后台任务或同步仓库。
    client = TestClient(create_app())
    response = client.post(
        "/api/admin/session/login", json={"username": "admin", "password": "password"},
    )
    assert response.status_code == 200
    yield client
    client.close()


def _paper(candidate_id, token, finished_at, *, handled=False, status="finished"):
    paper_id = create_quiz_paper(
        candidate_id=candidate_id, phone="00000000000", quiz_key="dashboard-demo", token=token,
    )
    update_quiz_paper_result(token, status=status, score=92, finished_at=finished_at)
    if handled:
        set_quiz_paper_handling(token, handled=True, handled_by="演示管理员")
    return paper_id


def _job(job_id, finished_at, *, status="failed", kind="grade_attempt", attempts=1):
    now = datetime.now(timezone.utc).isoformat()
    upsert_runtime_job({
        "id": job_id, "kind": kind, "status": status, "attempts": attempts,
        "source": "test", "created_at": now, "updated_at": now,
        "finished_at": finished_at.isoformat() if finished_at else None,
        "payload": {"private_test_payload": "do-not-return"},
        "result": {"private_test_result": "do-not-return"},
        "error": "private_test_error-do-not-return",
    })


def _window(client, offset=480):
    response = client.get("/api/admin/dashboard", params={"tz_offset_minutes": offset})
    assert response.status_code == 200
    body = response.json()
    start = datetime.fromisoformat(body["window"]["start_at"])
    end = datetime.fromisoformat(body["window"]["end_at"])
    assert end - start == timedelta(days=7) - timedelta(microseconds=1)
    return body, start, end


def test_dashboard_empty_and_timezone_window(client):
    for offset in (-720, -300, 0, 480, 840):
        body, start, end = _window(client, offset)
        local_start = start + timedelta(minutes=offset)
        local_end = end + timedelta(minutes=offset)
        local_today = (datetime.now(timezone.utc) + timedelta(minutes=offset)).date()
        assert local_start.hour == local_start.minute == local_start.second == 0
        assert local_end.date() == local_today
        assert body["window"]["start_date"] == (local_today - timedelta(days=6)).isoformat()
        assert body["window"]["end_date"] == local_today.isoformat()
        assert datetime.fromisoformat(body["generated_at"]).tzinfo is not None
        for key in ("unhandled", "completed", "failed_jobs"):
            assert body[key] == {"total": 0, "items": []}


def test_dashboard_preserves_soft_deleted_history_and_uses_actual_finished_at(client):
    _, start, end = _window(client)
    candidate_id = create_candidate("演示候选人", "00000000001")
    _paper(candidate_id, "at-start", start)
    _paper(candidate_id, "at-end", end, handled=True)
    _paper(candidate_id, "before-start", start - timedelta(microseconds=1))
    _paper(candidate_id, "after-end", end + timedelta(microseconds=1))
    _paper(candidate_id, "missing-finish", None)
    _paper(candidate_id, "grading-only", start, status="grading")
    delete_candidate(candidate_id)
    body, _, _ = _window(client)
    assert body["completed"]["total"] == 2
    assert [row["token"] for row in body["completed"]["items"]] == ["at-end", "at-start"]
    assert body["unhandled"]["total"] == 4
    assert body["unhandled"]["items"][-1]["token"] == "missing-finish"
    assert body["unhandled"]["items"][-1]["finished_at"] == ""
    assert all(row["candidate_deleted"] for row in body["completed"]["items"])
    assert body["completed"]["items"][0]["href"] == "/admin/attempt/at-end"
    assert body["completed"]["items"][0]["quiz_title"] == "dashboard-demo"


def test_dashboard_counts_all_rows_but_returns_six_in_finish_order(client):
    _, start, _ = _window(client)
    candidate_id = create_candidate("演示排序", "00000000002")
    for index in range(9):
        _paper(candidate_id, f"paper-{index}", start + timedelta(hours=9 - index))
        _job(f"job-{index}", start + timedelta(hours=9 - index), attempts=index + 1)
    _job("no-time", None)
    _job("before-window", start - timedelta(microseconds=1))
    _job("running", start + timedelta(days=1), status="running")
    _job("done", start + timedelta(days=1), status="done")
    body, _, _ = _window(client)
    assert body["unhandled"]["total"] == body["completed"]["total"] == 9
    assert [row["token"] for row in body["completed"]["items"]] == [f"paper-{i}" for i in range(6)]
    assert len(body["unhandled"]["items"]) == 6
    assert body["failed_jobs"]["total"] == 9
    assert [row["id"] for row in body["failed_jobs"]["items"]] == [f"job-{i}" for i in range(6)]
    for row in body["failed_jobs"]["items"]:
        assert set(row) == {"id", "kind", "status", "attempts", "finished_at", "href"}
        assert row["href"] == "/admin/assignments"
    assert "private_test" not in str(body)
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM runtime_job")
            assert cur.fetchone()[0] == 13


def test_dashboard_failed_job_boundaries_and_existing_entries(client):
    _, start, end = _window(client)
    _job("first", start, kind="git_sync_exams")
    _job("last", end, kind="admin_candidate_resume_upload")
    _job("future", end + timedelta(microseconds=1))
    _job("unknown", start + timedelta(days=1), kind="future_kind")
    body, _, _ = _window(client)
    rows = body["failed_jobs"]["items"]
    assert body["failed_jobs"]["total"] == 3
    assert [row["id"] for row in rows] == ["last", "unknown", "first"]
    assert [row["href"] for row in rows] == ["/admin/candidates", "/admin/status", "/admin/quizzes"]


def _quiz(key, *, enabled=False, status="active", version=True):
    spec = {"title": key, "description": "测试列表筛选", "questions": []}
    version_id = create_quiz_version(
        quiz_key=key, version_no=1, title=key, source_path="", git_repo_url="", git_commit="",
        content_hash=key, source_md="", spec=spec, public_spec=spec,
    ) if version else None
    save_quiz_definition(
        quiz_key=key, title=key, source_md="", spec=spec, public_spec=spec,
        status=status, current_version_id=version_id,
    )
    set_exam_public_invite(key, enabled=enabled, token=f"token-{key}")


@pytest.mark.parametrize("path", ["quizzes", "exams"])
def test_quiz_public_filter_precedes_pagination_and_matches_effective_state(client, path):
    for index in range(22):
        _quiz(f"enabled-{index:02}", enabled=True)
    _quiz("disabled")
    _quiz("no-version", enabled=True, version=False)
    _quiz("archived", enabled=True, status="archived")
    enabled = client.get(f"/api/admin/{path}?public_invite=enabled&page=2").json()
    assert enabled["total"] == 22
    assert enabled["total_pages"] == 2
    assert enabled["page"] == 2
    assert len(enabled["items"]) == 2
    assert all(row["public_invite_enabled"] for row in enabled["items"])
    assert enabled["filters"]["public_invite"] == "enabled"
    disabled = client.get(f"/api/admin/{path}?public_invite=disabled").json()
    assert disabled["total"] == 3
    assert not any(row["public_invite_enabled"] for row in disabled["items"])
    query = client.get(f"/api/admin/{path}?public_invite=disabled&q=no-version").json()
    assert query["total"] == 1
    assert query["items"][0]["quiz_key"] == "no-version"


def test_logs_filters_count_and_paginate_whole_dataset_without_changing_trend(client):
    candidate_id = create_candidate("跨页搜索演示", "00000000003")
    ids = [create_system_log(actor="actor-跨页搜索演示", event_type="candidate.update", candidate_id=candidate_id) for _ in range(25)]
    for event in ("exam.upload", "exam.grade", "exam.finish", "assignment.verify", "system.alert", "llm.usage"):
        create_system_log(actor="actor-演示筛选", event_type=event, candidate_id=candidate_id, meta={"note": "演示筛选"})
    baseline = client.get("/api/admin/logs").json()
    response = client.get("/api/admin/logs", params={"q": "跨页搜索演示", "category": "candidate", "page": 2})
    assert response.status_code == 200
    filtered = response.json()
    assert filtered["total"] == 25
    assert filtered["total_pages"] == 2
    assert len(filtered["items"]) == 5
    assert {row["id"] for row in filtered["items"]} == set(ids[:5])
    assert filtered["filters"] == {"q": "跨页搜索演示", "category": "candidate"}
    assert filtered["trend"] == baseline["trend"]
    assert filtered["counts"] == baseline["counts"]
    for category, total in (("quiz", 1), ("grading", 1), ("assignment", 2), ("system", 1)):
        result = client.get("/api/admin/logs", params={"q": "演示筛选", "category": category}).json()
        assert result["total"] == total
        assert all(row["type_key"] == category for row in result["items"])
    empty = client.get("/api/admin/logs?q=does-not-exist&page=999").json()
    assert empty["total"] == 0
    assert empty["page"] == 1
    assert empty["items"] == []
    log_id = create_system_log(actor="reader", event_type="exam.upload")
    by_id = client.get("/api/admin/logs", params={"q": str(log_id)}).json()
    assert by_id["total"] == 1
    assert by_id["items"][0]["id"] == log_id


def test_admin_log_search_uses_exact_numeric_id_and_literal_actor_only(client):
    first = create_system_log(actor="admin", event_type="exam.upload")
    second = create_system_log(actor="admin138", event_type="exam.upload")
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE system_log SET id=38 WHERE id=%s", (first,))
            cur.execute("UPDATE system_log SET id=138 WHERE id=%s", (second,))
    for query in ("38", "0038"):
        body = client.get("/api/admin/logs", params={"q": query}).json()
        assert body["total"] == 1
        assert [row["id"] for row in body["items"]] == [38]
    literal_id = create_system_log(actor=r"review%_team\audit", event_type="exam.upload")
    create_system_log(actor="reviewXYteam-audit", event_type="exam.upload")
    for query in ("%_", r"\audit"):
        body = client.get("/api/admin/logs", params={"q": query}).json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == literal_id
    actor = client.get("/api/admin/logs", params={"q": "DMIN"}).json()
    assert actor["total"] == 2
    assert {row["id"] for row in actor["items"]} == {38, 138}
    assert client.get("/api/admin/logs", params={"q": "9" * 100}).json()["total"] == 0

    candidate_id = create_candidate("name-only", "00000000007")
    create_system_log(actor="operator", event_type="candidate.update", candidate_id=candidate_id,
        quiz_key="quiz-only", token="token-only", meta={"note": "meta-only"})
    for query in ("name-only", "meta-only", "token-only", "quiz-only", "candidate.update"):
        body = client.get("/api/admin/logs", params={"q": query}).json()
        assert body["total"] == 0
        assert body["items"] == []
    # 通用日志查询仍保留既有的广义检索，不受管理页专用筛选影响。
    assert count_system_logs(query="meta-only") == 1


def test_job_description_preview_uses_save_renderer_without_writing(client):
    content = "## 演示职责\n\n- 设计接口\n\n<script>alert(1)</script>\n\n[坏链接](javascript:alert(1))"
    before = count_job_descriptions()
    preview = client.post("/api/admin/job-descriptions/preview", json={"content_md": content})
    assert preview.status_code == 200
    html = preview.json()["content_html"]
    assert "<h2>演示职责</h2>" in html
    assert "<script>" not in html
    assert "javascript:" not in html
    assert count_job_descriptions() == before
    created = client.post("/api/admin/job-descriptions", json={"title": "测试职位", "content_md": content}).json()
    assert created["content_html"] == html
    detail = client.get(f"/api/admin/job-descriptions/{created['id']}").json()
    assert detail["content_html"] == html


def _resume(candidate_id, data, filename="演示简历.png", mime="application/octet-stream"):
    update_candidate_resume(
        candidate_id, resume_bytes=data, resume_filename=filename,
        resume_mime=mime, resume_size=len(data), resume_parsed={},
    )


@pytest.mark.parametrize("image_format,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("BMP", "image/bmp"), ("WEBP", "image/webp")])
def test_resume_preview_validates_bytes_and_keeps_default_download(client, image_format, mime):
    candidate_id = create_candidate("图片预览演示", "00000000004")
    buffer = BytesIO()
    Image.new("RGB", (12, 8), color="white").save(buffer, format=image_format)
    data = buffer.getvalue()
    filename = "演示简历.png"
    _resume(candidate_id, data, filename)
    path = f"/api/admin/candidates/{candidate_id}/resume"
    preview = client.get(path, params={"preview": "true"})
    assert preview.status_code == 200
    assert preview.content == data
    assert preview.headers["content-type"] == mime
    assert preview.headers["content-disposition"].startswith("inline;")
    assert f"filename*=UTF-8''{quote(filename, safe='')}" in preview.headers["content-disposition"]
    assert preview.headers["cache-control"] == "no-store"
    assert preview.headers["x-content-type-options"] == "nosniff"
    download = client.get(path)
    assert download.content == data
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.headers["content-disposition"].startswith("attachment;")


@pytest.mark.parametrize("data", [b"not an image", b"%PDF-1.4 test", b'<svg xmlns="http://www.w3.org/2000/svg"/>', b"\x89PNG\r\n\x1a\n"])
def test_resume_preview_rejects_non_images_even_with_image_filename_and_mime(client, data):
    candidate_id = create_candidate("无效预览演示", "00000000005")
    _resume(candidate_id, data, mime="image/png")
    path = f"/api/admin/candidates/{candidate_id}/resume"
    assert client.get(path, params={"preview": "true"}).status_code == 415
    download = client.get(path)
    assert download.status_code == 200
    assert download.content == data


def test_redesign_endpoints_require_admin_and_validate_query_parameters(client):
    for path in ("/api/admin/dashboard?tz_offset_minutes=841", "/api/admin/dashboard?tz_offset_minutes=-721",
                 "/api/admin/quizzes?public_invite=invalid", "/api/admin/exams?public_invite=invalid",
                 "/api/admin/logs?category=invalid"):
        assert client.get(path).status_code == 422
    client.cookies.clear()
    for path in ("/api/admin/dashboard", "/api/admin/candidates/1/resume?preview=true"):
        assert client.get(path).status_code == 401
    assert client.post("/api/admin/job-descriptions/preview", json={"content_md": "# 演示"}).status_code == 401


def test_resume_preview_missing_candidate_and_file_return_404(client):
    assert client.get("/api/admin/candidates/999/resume?preview=true").status_code == 404
    candidate_id = create_candidate("尚无文件", "00000000006")
    assert client.get(f"/api/admin/candidates/{candidate_id}/resume?preview=true").status_code == 404
