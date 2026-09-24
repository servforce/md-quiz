"""逐题耗时与简答题文本变动统计。只保存数字和行为类型。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

MAX_EVENTS_PER_BATCH = 100
MAX_STORED_EVENTS = 20_000
MAX_SIGNALS = 30
LARGE_PASTE_CHARS = 30
LARGE_INSERT_CHARS = 50
EVENT_KINDS = {"insert", "delete", "replace", "paste", "composition"}


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def question_record(assignment: dict[str, Any], qid: str, started_at: Any) -> dict[str, Any]:
    records = assignment.setdefault("question_activity", {})
    start_text = started_at.isoformat() if isinstance(started_at, datetime) else str(started_at or "")
    record = records.setdefault(qid, {"started_at": start_text, "ended_at": None, "duration_ms": None, "end_reason": None})
    if not record.get("started_at") and started_at:
        record["started_at"] = start_text
    return record


def close_question(assignment: dict[str, Any], qid: str, started_at: Any, ended_at: datetime, reason: str) -> None:
    if not qid:
        return
    record = question_record(assignment, qid, started_at)
    if record.get("ended_at"):
        return
    start = _parse_time(record.get("started_at")) or ended_at
    end = max(start, ended_at)
    record["ended_at"] = end.isoformat()
    record["duration_ms"] = max(0, round((end - start).total_seconds() * 1000))
    record["end_reason"] = reason


def record_input_batch(assignment: dict[str, Any], qid: str, started_at: Any, batch: dict[str, Any]) -> int:
    record = question_record(assignment, qid, started_at)
    input_data = record.setdefault("input", {
        "events": [], "summary": {"event_count": 0, "inserted_chars": 0, "deleted_chars": 0, "paste_count": 0,
                                 "first_event_ms": None, "last_event_ms": None, "max_pause_ms": 0},
        "signals": [], "truncated": False, "incomplete": False, "last_seq_by_capture": {},
    })
    if int(batch.get("dropped_events") or 0) > 0:
        input_data["incomplete"] = True
    capture_id = batch["capture_id"]
    last_by_capture = input_data["last_seq_by_capture"]
    last_seq = int(last_by_capture.get(capture_id) or 0)
    accepted = 0
    for event in batch["events"]:
        seq = int(event["seq"])
        if seq <= last_seq:
            continue
        elapsed_ms = int(event["elapsed_ms"])
        added = int(event["added_chars"])
        deleted = int(event["deleted_chars"])
        kind = str(event["kind"])
        summary = input_data["summary"]
        previous_ms = summary["last_event_ms"]
        if previous_ms is not None and elapsed_ms >= previous_ms:
            summary["max_pause_ms"] = max(summary["max_pause_ms"], elapsed_ms - previous_ms)
        if summary["first_event_ms"] is None:
            summary["first_event_ms"] = elapsed_ms
        summary["last_event_ms"] = max(elapsed_ms, previous_ms or 0)
        summary["event_count"] += 1
        summary["inserted_chars"] += added
        summary["deleted_chars"] += deleted
        summary["paste_count"] += int(kind == "paste")
        clean_event = {"seq": seq, "elapsed_ms": elapsed_ms, "kind": kind,
                       "added_chars": added, "deleted_chars": deleted, "length_after": int(event["length_after"])}
        if len(input_data["events"]) < MAX_STORED_EVENTS:
            input_data["events"].append(clean_event)
        else:
            input_data["truncated"] = True
        signal_kind = "large_paste" if kind == "paste" and added >= LARGE_PASTE_CHARS else (
            "large_insert" if kind != "paste" and added >= LARGE_INSERT_CHARS else ""
        )
        if signal_kind and len(input_data["signals"]) < MAX_SIGNALS:
            input_data["signals"].append({"kind": signal_kind, "elapsed_ms": elapsed_ms, "added_chars": added})
        last_seq = seq
        accepted += 1
    last_by_capture[capture_id] = last_seq
    return accepted
