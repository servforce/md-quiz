from __future__ import annotations

import base64
import hashlib
import html
import hmac
import os
import re
import secrets
import shutil
import threading
import time as time_module
import zipfile
from datetime import date, time as dt_time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import markdown as mdlib
from markupsafe import Markup

from backend.md_quiz.config import ADMIN_PASSWORD, ADMIN_USERNAME, BASE_DIR, SECRET_KEY, logger
from backend.md_quiz.parsers.qml import QmlParseError, parse_qml_markdown
from backend.md_quiz.storage.db import (
    add_candidate_job_descriptions,
    backfill_assignment_quiz_version_id,
    backfill_quiz_archive_version_id,
    backfill_quiz_paper_version_id,
    cleanup_duplicate_system_alert_logs,
    create_candidate,
    create_job_description,
    create_quiz_paper,
    create_quiz_version,
    delete_assignment_record,
    delete_job_description,
    delete_quiz_archive_by_token,
    delete_quiz_assets,
    delete_quiz_definition,
    count_candidates,
    count_job_descriptions,
    count_quiz_respondents_by_key,
    count_quiz_papers,
    count_unhandled_finished_quiz_papers,
    count_operation_logs,
    count_operation_logs_by_category,
    count_quiz_paper_analytics_rows,
    get_assignment_record,
    get_quiz_archive_by_name,
    get_quiz_archive_by_token,
    get_quiz_asset,
    get_quiz_definition,
    get_quiz_key_by_public_invite_token,
    get_exam_public_invite,
    get_current_quiz_version,
    get_job_description,
    get_job_description_by_key,
    get_quiz_version,
    get_quiz_version_asset,
    delete_candidate,
    delete_quiz_paper_by_token,
    get_quiz_paper_by_token,
    get_quiz_paper_admin_detail_by_token,
    get_candidate,
    get_candidate_name_from_logs,
    get_candidate_by_phone,
    get_candidate_resume,
    init_db,
    list_candidate_job_descriptions,
    list_candidates,
    list_job_description_options,
    list_job_descriptions,
    list_assignment_tokens,
    list_quiz_archives_by_quiz_key,
    list_quiz_archives_for_phone,
    list_quiz_definitions,
    list_quiz_paper_analytics_rows,
    list_quiz_papers,
    list_quiz_versions,
    list_quiz_assets,
    list_estimated_sms_calls_daily_counts,
    list_operation_daily_counts,
    list_operation_daily_counts_by_category,
    list_operation_logs,
    list_operation_logs_after_id,
    list_system_status_daily_metrics,
    mark_exam_deleted,
    get_runtime_daily_metric_int,
    get_runtime_daily_metric_json,
    get_runtime_kv,
    rename_assignment_quiz_key,
    rename_quiz_archives_quiz_key,
    rename_quiz_assets,
    rename_quiz_definition,
    rename_quiz_key,
    remove_candidate_job_description,
    archive_missing_repo_job_descriptions,
    mark_job_description_sync_error,
    replace_quiz_assets,
    replace_quiz_version_assets,
    save_assignment_record,
    save_quiz_archive,
    save_quiz_definition,
    find_quiz_version_by_hash,
    set_exam_public_invite,
    set_runtime_daily_metric_json,
    set_runtime_kv,
    incr_runtime_daily_metric_int,
    set_quiz_paper_entered_at,
    set_quiz_paper_finished_at,
    set_quiz_paper_handling,
    set_quiz_paper_suspected_ai_question_ids,
    set_quiz_paper_invite_window_if_missing,
    set_quiz_paper_status,
    update_candidate,
    update_candidate_resume,
    update_candidate_resume_parsed,
    update_job_description,
    upsert_job_description_from_repo,
    update_quiz_paper_result,
    update_quiz_version_metadata,
    update_quiz_version_payload,
    verify_candidate,
)
from backend.md_quiz.services.aliyun_dypns import check_sms_verify_code, send_sms_verify_code
from backend.md_quiz.services.assignment_service import (
    assignment_locked,
    compute_min_submit_seconds,
    create_assignment,
    load_assignment,
    save_assignment,
)
from backend.md_quiz.services.audit_context import audit_context, get_audit_context
from backend.md_quiz.services.exam_generation_service import (
    check_exam_prompt_completeness,
    generate_exam_from_prompt,
)
from backend.md_quiz.services.exam_repo_sync_service import (
    EXAM_SYNC_JOB_KIND,
    bind_exam_repo,
    enqueue_exam_repo_sync,
    migrate_legacy_exam_data,
    perform_exam_repo_sync,
    read_exam_repo_binding,
    read_exam_repo_sync_state,
    rebind_exam_repo,
)
from backend.md_quiz.services.grading_service import generate_candidate_remark, grade_attempt
from backend.md_quiz.services.resume_service import (
    clean_projects_raw_for_display,
    extract_resume_text,
    extract_resume_section,
    extract_experience_raw,
    parse_resume_all_llm,
    parse_resume_details_llm,
    parse_resume_identity_fast,
    parse_resume_identity_llm,
    parse_resume_name_llm,
    split_projects_raw_into_blocks,
)
from backend.md_quiz.services.system_log import log_event
from backend.md_quiz.services.system_metrics import (
    backfill_missing_system_alert_levels,
    emit_alerts_for_current_snapshot,
    get_daily_metric,
    incr_sms_calls_and_alert,
)
from backend.md_quiz.services.university_tags import classify_university
