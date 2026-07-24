from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import urlsplit

import psycopg2
import psycopg2.extras
import psycopg2.pool

from backend.md_quiz.config import (
    DATABASE_URL,
    DB_POOL_MAXCONN,
    DB_POOL_MINCONN,
    DB_POOL_WAIT_TIMEOUT_SECONDS,
    logger,
)

_POOL_LOCK = threading.Lock()
_PG_POOL: psycopg2.pool.ThreadedConnectionPool | None = None
_PG_POOL_GATE: threading.BoundedSemaphore | None = None
_PG_POOL_MINCONN = DB_POOL_MINCONN
_PG_POOL_MAXCONN = DB_POOL_MAXCONN
_PG_POOL_WAIT_TIMEOUT_SECONDS = DB_POOL_WAIT_TIMEOUT_SECONDS

# 把一个数据库连接字符串 DATABASE_URL 解析成 psycopg2.connect() 需要的参数字典
def _parse_pg_dsn(database_url: str) -> dict[str, Any]:
    url = database_url.strip()  # 去掉前后两端的空白
    if url.startswith("postgresql+psycopg2://"):
        url = "postgresql://" + url[len("postgresql+psycopg2://") :]        # 更换地址前缀
    if not url.startswith("postgresql://"):
        raise RuntimeError(f"Unsupported DATABASE_URL scheme: {database_url!r}")        # 使用别的数据库就抛出异常

    u = urlsplit(url)   # 将url拆分出来协议，用户名，密码，主机，端口，路径
    if not u.hostname or not u.port or not u.path:
        raise RuntimeError(f"Invalid DATABASE_URL: {database_url!r}")       # 遇到异常则抛出异常
    return {
        "host": u.hostname,  # 127.0.0.1
        "port": u.port,     # 端口号
        "user": u.username,     # 用户名
        "password": u.password,     # 密码
        "dbname": u.path.lstrip("/"),       # 数据库名称
    }

# 链接数据库管理器，自动管理 PostgreSQL 连接的打开、提交、回滚、关闭
def _get_pg_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _PG_POOL
    with _POOL_LOCK:
        if _PG_POOL is None:
            dsn = _parse_pg_dsn(DATABASE_URL)
            _PG_POOL = psycopg2.pool.ThreadedConnectionPool(
                _PG_POOL_MINCONN,
                _PG_POOL_MAXCONN,
                **dsn,
            )
        return _PG_POOL


def _get_pg_pool_gate() -> threading.BoundedSemaphore:
    global _PG_POOL_GATE
    with _POOL_LOCK:
        if _PG_POOL_GATE is None:
            _PG_POOL_GATE = threading.BoundedSemaphore(_PG_POOL_MAXCONN)
        return _PG_POOL_GATE


@contextmanager
def conn_scope() -> Iterator[psycopg2.extensions.connection]:
    pool = _get_pg_pool()
    gate = _get_pg_pool_gate()
    acquired = gate.acquire(timeout=_PG_POOL_WAIT_TIMEOUT_SECONDS)
    if not acquired:
        logger.warning(
            "Database connection pool wait timeout: maxconn=%s timeout_seconds=%.3f",
            _PG_POOL_MAXCONN,
            _PG_POOL_WAIT_TIMEOUT_SECONDS,
        )
        raise psycopg2.pool.PoolError(
            f"connection pool exhausted after waiting {_PG_POOL_WAIT_TIMEOUT_SECONDS:.3f}s"
        )
    conn = None
    try:
        conn = pool.getconn()
        conn.autocommit = False
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            if conn is not None:
                pool.putconn(conn)
        except Exception:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
        finally:
            gate.release()


def _candidate_query_where_clause(query: str | None) -> tuple[str, list[Any]]:
    """
    Build a WHERE clause + params for candidate search.

    Behavior:
    - If the user enters a pure-number query, treat it as:
        phone prefix match (phone LIKE '{q}%') OR exact ID match (id = q).
      This matches the UI expectation that entering "13" shows phones starting with 13
      (and also candidate ID 13 if exists).
    - Supports optional ID prefixes like "#3" or "id:3".
    """
    qraw = str(query or "").strip()
    if not qraw:
        return "", []

    qid = qraw
    if qid.startswith("#"):
        qid = qid[1:].strip()
    if qid.lower().startswith("id:"):
        qid = qid[3:].strip()

    if qid.isdigit():
        params: list[Any] = [f"{qid}%"]
        try:
            qnum = int(qid)
        except Exception:
            qnum = None
        if qnum is None:
            return " WHERE (phone LIKE %s)", params
        params.append(qnum)
        return " WHERE (phone LIKE %s OR id = %s)", params

    q = f"%{qraw}%"
    return " WHERE (name ILIKE %s OR phone LIKE %s)", [q, q]


def _json_param(value: Any) -> psycopg2.extras.Json:
    return psycopg2.extras.Json(value, dumps=lambda x: json.dumps(x, ensure_ascii=False))


def _json_load(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return None


def _normalize_quiz_key_list(value: Any) -> list[str]:
    raw = _json_load(value) if isinstance(value, str) else value
    if raw is None:
        return []
    if not isinstance(raw, list):
        raw = [raw]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _decode_job_description_row(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["related_quizzes"] = _normalize_quiz_key_list(out.get("related_quizzes"))
    return out


def _iso_or_none(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.isoformat()
    text = str(raw or "").strip()
    return text or None


def _log_time_or_none(raw: Any) -> datetime | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except Exception:
        return None


def _log_duration_seconds_or_none(
    duration_seconds: int | None,
    started_at: datetime | None,
    finished_at: datetime | None,
) -> int | None:
    if duration_seconds is not None:
        return max(0, int(duration_seconds))
    if not started_at or not finished_at:
        return None
    try:
        return max(0, int((finished_at - started_at).total_seconds()))
    except Exception:
        return None


def init_db() -> None:
    """
    Create/upgrade required DB objects.

    Notes:
    - candidate no longer stores exam status/entered/submitted timestamps.
    - Per-token exam attempts are stored in quiz_paper so one candidate can take the same paper multiple times
      with different tokens.
    """
    enum_ddl = """
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'candidate_status') THEN
    CREATE TYPE candidate_status AS ENUM ('created', 'distributed', 'verified', 'finished');
  ELSE
    -- Ensure required values exist (compatible with older PostgreSQL)
    IF EXISTS (
      SELECT 1
      FROM pg_enum e
      JOIN pg_type t ON t.oid = e.enumtypid
      WHERE t.typname = 'candidate_status' AND e.enumlabel = 'send'
    ) AND NOT EXISTS (
      SELECT 1
      FROM pg_enum e
      JOIN pg_type t ON t.oid = e.enumtypid
      WHERE t.typname = 'candidate_status' AND e.enumlabel = 'distributed'
    ) THEN
      BEGIN
        ALTER TYPE candidate_status RENAME VALUE 'send' TO 'distributed';
      EXCEPTION
        WHEN OTHERS THEN
          NULL;
      END;
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM pg_enum e
      JOIN pg_type t ON t.oid = e.enumtypid
      WHERE t.typname = 'candidate_status' AND e.enumlabel = 'created'
    ) THEN
      BEGIN
        ALTER TYPE candidate_status ADD VALUE 'created';
      EXCEPTION
        WHEN OTHERS THEN
          NULL;
      END;
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM pg_enum e
      JOIN pg_type t ON t.oid = e.enumtypid
      WHERE t.typname = 'candidate_status' AND e.enumlabel = 'distributed'
    ) THEN
      BEGIN
        ALTER TYPE candidate_status ADD VALUE 'distributed';
      EXCEPTION
        WHEN OTHERS THEN
          NULL;
      END;
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM pg_enum e
      JOIN pg_type t ON t.oid = e.enumtypid
      WHERE t.typname = 'candidate_status' AND e.enumlabel = 'verified'
    ) THEN
      BEGIN
        ALTER TYPE candidate_status ADD VALUE 'verified';
      EXCEPTION
        WHEN OTHERS THEN
          NULL;
      END;
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM pg_enum e
      JOIN pg_type t ON t.oid = e.enumtypid
      WHERE t.typname = 'candidate_status' AND e.enumlabel = 'in_quiz'
    ) THEN
      BEGIN
        ALTER TYPE candidate_status ADD VALUE 'in_quiz';
      EXCEPTION
        WHEN OTHERS THEN
          NULL;
      END;
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM pg_enum e
      JOIN pg_type t ON t.oid = e.enumtypid
      WHERE t.typname = 'candidate_status' AND e.enumlabel = 'grading'
    ) THEN
      BEGIN
        ALTER TYPE candidate_status ADD VALUE 'grading';
      EXCEPTION
        WHEN OTHERS THEN
          NULL;
      END;
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM pg_enum e
      JOIN pg_type t ON t.oid = e.enumtypid
      WHERE t.typname = 'candidate_status' AND e.enumlabel = 'finished'
    ) THEN
      BEGIN
        ALTER TYPE candidate_status ADD VALUE 'finished';
      EXCEPTION
        WHEN OTHERS THEN
          NULL;
      END;
    END IF;
  END IF;
END$$;
"""

    quiz_paper_enum_ddl = """
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'exam_paper_status')
     AND NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'quiz_paper_status') THEN
    BEGIN
      ALTER TYPE exam_paper_status RENAME TO quiz_paper_status;
    EXCEPTION WHEN OTHERS THEN NULL;
    END;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'quiz_paper_status') THEN
    CREATE TYPE quiz_paper_status AS ENUM ('invited', 'verified', 'in_quiz', 'grading', 'finished');
  ELSE
    IF EXISTS (
      SELECT 1
      FROM pg_enum e
      JOIN pg_type t ON t.oid = e.enumtypid
      WHERE t.typname = 'quiz_paper_status' AND e.enumlabel = 'in_exam'
    ) AND NOT EXISTS (
      SELECT 1
      FROM pg_enum e
      JOIN pg_type t ON t.oid = e.enumtypid
      WHERE t.typname = 'quiz_paper_status' AND e.enumlabel = 'in_quiz'
    ) THEN
      BEGIN
        ALTER TYPE quiz_paper_status RENAME VALUE 'in_exam' TO 'in_quiz';
      EXCEPTION WHEN OTHERS THEN NULL;
      END;
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM pg_enum e
      JOIN pg_type t ON t.oid = e.enumtypid
      WHERE t.typname = 'quiz_paper_status' AND e.enumlabel = 'invited'
    ) THEN
      BEGIN
        ALTER TYPE quiz_paper_status ADD VALUE 'invited';
      EXCEPTION WHEN OTHERS THEN NULL;
      END;
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM pg_enum e
      JOIN pg_type t ON t.oid = e.enumtypid
      WHERE t.typname = 'quiz_paper_status' AND e.enumlabel = 'verified'
    ) THEN
      BEGIN
        ALTER TYPE quiz_paper_status ADD VALUE 'verified';
      EXCEPTION WHEN OTHERS THEN NULL;
      END;
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM pg_enum e
      JOIN pg_type t ON t.oid = e.enumtypid
      WHERE t.typname = 'quiz_paper_status' AND e.enumlabel = 'in_quiz'
    ) THEN
      BEGIN
        ALTER TYPE quiz_paper_status ADD VALUE 'in_quiz';
      EXCEPTION WHEN OTHERS THEN NULL;
      END;
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM pg_enum e
      JOIN pg_type t ON t.oid = e.enumtypid
      WHERE t.typname = 'quiz_paper_status' AND e.enumlabel = 'grading'
    ) THEN
      BEGIN
        ALTER TYPE quiz_paper_status ADD VALUE 'grading';
      EXCEPTION WHEN OTHERS THEN NULL;
      END;
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM pg_enum e
      JOIN pg_type t ON t.oid = e.enumtypid
      WHERE t.typname = 'quiz_paper_status' AND e.enumlabel = 'finished'
    ) THEN
      BEGIN
        ALTER TYPE quiz_paper_status ADD VALUE 'finished';
      EXCEPTION WHEN OTHERS THEN NULL;
      END;
    END IF;
  END IF;
END$$;
"""

    schema_ddl = """
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'exam_paper')
     AND NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'quiz_paper') THEN
    EXECUTE 'ALTER TABLE exam_paper RENAME TO quiz_paper';
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'exam_asset')
     AND NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'quiz_asset') THEN
    EXECUTE 'ALTER TABLE exam_asset RENAME TO quiz_asset';
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'exam_version')
     AND NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'quiz_version') THEN
    EXECUTE 'ALTER TABLE exam_version RENAME TO quiz_version';
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'exam_version_asset')
     AND NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'quiz_version_asset') THEN
    EXECUTE 'ALTER TABLE exam_version_asset RENAME TO quiz_version_asset';
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'exam_definition')
     AND NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'quiz_definition') THEN
    EXECUTE 'ALTER TABLE exam_definition RENAME TO quiz_definition';
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'exam_archive')
     AND NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'quiz_archive') THEN
    EXECUTE 'ALTER TABLE exam_archive RENAME TO quiz_archive';
  END IF;
END$$;

 CREATE TABLE IF NOT EXISTS candidate (
   id               BIGSERIAL PRIMARY KEY,
    name             TEXT NOT NULL,
    phone            TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at       TIMESTAMPTZ NULL,
   resume_bytes     BYTEA NULL,
   resume_filename  TEXT NULL,
   resume_mime      TEXT NULL,
   resume_size      INT NULL,
   resume_parsed    JSONB NULL,
  resume_parsed_at TIMESTAMPTZ NULL
 );

 DO $$
 BEGIN
   IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'candidate_phone_key') THEN
     BEGIN
       ALTER TABLE candidate ADD CONSTRAINT candidate_phone_key UNIQUE (phone);
    EXCEPTION
      WHEN unique_violation THEN
        -- Existing duplicates; keep running without enforcing uniqueness to avoid startup failure.
        RAISE NOTICE 'Skip adding UNIQUE(phone) because duplicates exist.';
    END;
  END IF;
END$$;

ALTER TABLE candidate ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;
UPDATE candidate SET created_at = NOW() WHERE created_at IS NULL;
ALTER TABLE candidate ALTER COLUMN created_at SET DEFAULT NOW();
ALTER TABLE candidate ALTER COLUMN created_at SET NOT NULL;

ALTER TABLE candidate ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ NULL;

ALTER TABLE candidate DROP COLUMN IF EXISTS updated_at;

-- Drop deprecated columns (status/exam fields moved to quiz_paper).
ALTER TABLE candidate DROP COLUMN IF EXISTS status;
ALTER TABLE candidate DROP COLUMN IF EXISTS quiz_key;
ALTER TABLE candidate DROP COLUMN IF EXISTS score;
ALTER TABLE candidate DROP COLUMN IF EXISTS exam_started_at;
ALTER TABLE candidate DROP COLUMN IF EXISTS exam_submitted_at;
ALTER TABLE candidate DROP COLUMN IF EXISTS duration_seconds;
 ALTER TABLE candidate ADD COLUMN IF NOT EXISTS resume_bytes BYTEA NULL;
 ALTER TABLE candidate ADD COLUMN IF NOT EXISTS resume_filename TEXT NULL;
 ALTER TABLE candidate ADD COLUMN IF NOT EXISTS resume_mime TEXT NULL;
 ALTER TABLE candidate ADD COLUMN IF NOT EXISTS resume_size INT NULL;
 ALTER TABLE candidate ADD COLUMN IF NOT EXISTS resume_parsed JSONB NULL;
 ALTER TABLE candidate ADD COLUMN IF NOT EXISTS resume_parsed_at TIMESTAMPTZ NULL;

  -- Drop deprecated columns (we no longer use them).
  ALTER TABLE candidate DROP COLUMN IF EXISTS interview;
  ALTER TABLE candidate DROP COLUMN IF EXISTS remark;

 CREATE INDEX IF NOT EXISTS idx_candidate_phone ON candidate(phone);
 CREATE INDEX IF NOT EXISTS idx_candidate_created_at ON candidate(created_at);
 CREATE INDEX IF NOT EXISTS idx_candidate_deleted_at ON candidate(deleted_at);

  CREATE TABLE IF NOT EXISTS job_description (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    content_md TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ NULL
  );
  ALTER TABLE job_description ADD COLUMN IF NOT EXISTS content_md TEXT NOT NULL DEFAULT '';
  ALTER TABLE job_description ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'draft';
  ALTER TABLE job_description ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
  ALTER TABLE job_description ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
  ALTER TABLE job_description ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ NULL;
  ALTER TABLE job_description ADD COLUMN IF NOT EXISTS jd_key TEXT NULL;
  ALTER TABLE job_description ADD COLUMN IF NOT EXISTS source_kind TEXT NOT NULL DEFAULT 'manual';
  ALTER TABLE job_description ADD COLUMN IF NOT EXISTS source_path TEXT NULL;
  ALTER TABLE job_description ADD COLUMN IF NOT EXISTS git_repo_url TEXT NULL;
  ALTER TABLE job_description ADD COLUMN IF NOT EXISTS last_synced_commit TEXT NULL;
  ALTER TABLE job_description ADD COLUMN IF NOT EXISTS last_sync_error TEXT NOT NULL DEFAULT '';
  ALTER TABLE job_description ADD COLUMN IF NOT EXISTS last_sync_at TIMESTAMPTZ NULL;
  ALTER TABLE job_description ADD COLUMN IF NOT EXISTS content_hash TEXT NULL;
  ALTER TABLE job_description ADD COLUMN IF NOT EXISTS related_quizzes JSONB NOT NULL DEFAULT '[]'::jsonb;
  UPDATE job_description
     SET source_kind = 'manual'
   WHERE COALESCE(NULLIF(BTRIM(source_kind), ''), 'manual') NOT IN ('manual', 'git');
  UPDATE job_description
     SET status = 'draft'
   WHERE COALESCE(NULLIF(BTRIM(status), ''), 'draft') NOT IN ('draft', 'active', 'archived');
  DO $$
  BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'job_description_status_check') THEN
      BEGIN
        ALTER TABLE job_description
          ADD CONSTRAINT job_description_status_check CHECK (status IN ('draft', 'active', 'archived'));
      EXCEPTION WHEN OTHERS THEN NULL;
      END;
    END IF;
  END$$;
  DO $$
  BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'job_description_source_kind_check') THEN
      BEGIN
        ALTER TABLE job_description
          ADD CONSTRAINT job_description_source_kind_check CHECK (source_kind IN ('manual', 'git'));
      EXCEPTION WHEN OTHERS THEN NULL;
      END;
    END IF;
  END$$;
  CREATE INDEX IF NOT EXISTS idx_job_description_status ON job_description(status);
  CREATE INDEX IF NOT EXISTS idx_job_description_created_at ON job_description(created_at);
  CREATE INDEX IF NOT EXISTS idx_job_description_updated_at ON job_description(updated_at);
  CREATE INDEX IF NOT EXISTS idx_job_description_deleted_at ON job_description(deleted_at);
  CREATE UNIQUE INDEX IF NOT EXISTS idx_job_description_jd_key_unique ON job_description(jd_key) WHERE jd_key IS NOT NULL;
  CREATE INDEX IF NOT EXISTS idx_job_description_source_kind ON job_description(source_kind);
  CREATE INDEX IF NOT EXISTS idx_job_description_source_path ON job_description(source_path);

  CREATE TABLE IF NOT EXISTS candidate_job_description (
    candidate_id BIGINT NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
    job_description_id BIGINT NOT NULL REFERENCES job_description(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (candidate_id, job_description_id)
  );
  CREATE INDEX IF NOT EXISTS idx_candidate_job_description_candidate_id ON candidate_job_description(candidate_id);
  CREATE INDEX IF NOT EXISTS idx_candidate_job_description_job_description_id ON candidate_job_description(job_description_id);
  CREATE INDEX IF NOT EXISTS idx_candidate_job_description_created_at ON candidate_job_description(created_at);

  CREATE TABLE IF NOT EXISTS quiz_paper (
    id BIGSERIAL PRIMARY KEY,
    candidate_id BIGINT NOT NULL REFERENCES candidate(id),
    phone TEXT NOT NULL,
    quiz_key TEXT NOT NULL,
    quiz_version_id BIGINT NULL,
    token TEXT NOT NULL,
    source_kind TEXT NOT NULL DEFAULT 'direct',
    invite_start_date DATE NULL,
    invite_end_date DATE NULL,
    status quiz_paper_status NOT NULL DEFAULT 'invited',
    entered_at TIMESTAMPTZ NULL,
    finished_at TIMESTAMPTZ NULL,
    handled_at TIMESTAMPTZ NULL,
    handled_by TEXT NULL,
    suspected_ai_question_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    score INT NULL CHECK (score IS NULL OR score BETWEEN 0 AND 100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
   updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
 );

 ALTER TABLE quiz_paper DROP COLUMN IF EXISTS duration_seconds;
 DO $$
 BEGIN
   IF EXISTS (
     SELECT 1 FROM information_schema.columns
     WHERE table_name = 'quiz_paper' AND column_name = 'exam_key'
   ) AND NOT EXISTS (
     SELECT 1 FROM information_schema.columns
     WHERE table_name = 'quiz_paper' AND column_name = 'quiz_key'
   ) THEN
     EXECUTE 'ALTER TABLE quiz_paper RENAME COLUMN exam_key TO quiz_key';
   END IF;
   IF EXISTS (
     SELECT 1 FROM information_schema.columns
     WHERE table_name = 'quiz_paper' AND column_name = 'exam_version_id'
   ) AND NOT EXISTS (
     SELECT 1 FROM information_schema.columns
     WHERE table_name = 'quiz_paper' AND column_name = 'quiz_version_id'
   ) THEN
     EXECUTE 'ALTER TABLE quiz_paper RENAME COLUMN exam_version_id TO quiz_version_id';
   END IF;
   IF EXISTS (
     SELECT 1 FROM information_schema.columns
     WHERE table_name = 'quiz_paper' AND column_name = 'status' AND udt_name = 'exam_paper_status'
   ) THEN
     EXECUTE 'ALTER TABLE quiz_paper ALTER COLUMN status DROP DEFAULT';
     EXECUTE 'ALTER TABLE quiz_paper ALTER COLUMN status TYPE quiz_paper_status USING status::text::quiz_paper_status';
     EXECUTE 'ALTER TABLE quiz_paper ALTER COLUMN status SET DEFAULT ''invited''::quiz_paper_status';
   END IF;
 END$$;
 ALTER TABLE quiz_paper ADD COLUMN IF NOT EXISTS quiz_version_id BIGINT NULL;
 ALTER TABLE quiz_paper ADD COLUMN IF NOT EXISTS source_kind TEXT NOT NULL DEFAULT 'direct';
 ALTER TABLE quiz_paper ADD COLUMN IF NOT EXISTS handled_at TIMESTAMPTZ NULL;
 ALTER TABLE quiz_paper ADD COLUMN IF NOT EXISTS handled_by TEXT NULL;
 ALTER TABLE quiz_paper ADD COLUMN IF NOT EXISTS suspected_ai_question_ids JSONB NOT NULL DEFAULT '[]'::jsonb;

  CREATE TABLE IF NOT EXISTS assignment_record (
    token TEXT PRIMARY KEY,
    quiz_key TEXT NOT NULL,
    quiz_version_id BIGINT NULL,
    candidate_id BIGINT NULL REFERENCES candidate(id) ON DELETE SET NULL,
    status TEXT NOT NULL,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  );
  DO $$
  BEGIN
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'assignment_record' AND column_name = 'exam_key'
    ) AND NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'assignment_record' AND column_name = 'quiz_key'
    ) THEN
      EXECUTE 'ALTER TABLE assignment_record RENAME COLUMN exam_key TO quiz_key';
    END IF;
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'assignment_record' AND column_name = 'exam_version_id'
    ) AND NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'assignment_record' AND column_name = 'quiz_version_id'
    ) THEN
      EXECUTE 'ALTER TABLE assignment_record RENAME COLUMN exam_version_id TO quiz_version_id';
    END IF;
  END$$;
  ALTER TABLE assignment_record ADD COLUMN IF NOT EXISTS quiz_version_id BIGINT NULL;
  CREATE INDEX IF NOT EXISTS idx_assignment_record_quiz_key ON assignment_record(quiz_key);
  CREATE INDEX IF NOT EXISTS idx_assignment_record_quiz_version_id ON assignment_record(quiz_version_id);
  CREATE INDEX IF NOT EXISTS idx_assignment_record_candidate_id ON assignment_record(candidate_id);
  CREATE INDEX IF NOT EXISTS idx_assignment_record_status ON assignment_record(status);
  CREATE INDEX IF NOT EXISTS idx_assignment_record_created_at ON assignment_record(created_at);

 UPDATE quiz_paper ep
    SET source_kind='public'
   FROM assignment_record ar
  WHERE ar.token = ep.token
    AND COALESCE(ar.data->'public_invite', 'null'::jsonb) <> 'null'::jsonb;
 UPDATE quiz_paper
    SET source_kind='direct'
  WHERE COALESCE(NULLIF(BTRIM(source_kind), ''), 'direct') NOT IN ('direct', 'public');

 DO $$
 BEGIN
   IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'quiz_paper_token_key') THEN
     BEGIN
       ALTER TABLE quiz_paper ADD CONSTRAINT quiz_paper_token_key UNIQUE (token);
     EXCEPTION WHEN OTHERS THEN NULL;
     END;
   END IF;
 END$$;

  CREATE INDEX IF NOT EXISTS idx_quiz_paper_candidate_id ON quiz_paper(candidate_id);
 CREATE INDEX IF NOT EXISTS idx_quiz_paper_phone ON quiz_paper(phone);
  CREATE INDEX IF NOT EXISTS idx_quiz_paper_quiz_key ON quiz_paper(quiz_key);
  CREATE INDEX IF NOT EXISTS idx_quiz_paper_quiz_version_id ON quiz_paper(quiz_version_id);
  CREATE INDEX IF NOT EXISTS idx_quiz_paper_source_kind ON quiz_paper(source_kind);
  CREATE INDEX IF NOT EXISTS idx_quiz_paper_status ON quiz_paper(status);
  CREATE INDEX IF NOT EXISTS idx_quiz_paper_handled_at ON quiz_paper(handled_at);
  CREATE INDEX IF NOT EXISTS idx_quiz_paper_created_at ON quiz_paper(created_at);
  ALTER TABLE quiz_paper ADD COLUMN IF NOT EXISTS invite_start_date DATE NULL;
  ALTER TABLE quiz_paper ADD COLUMN IF NOT EXISTS invite_end_date DATE NULL;
  CREATE INDEX IF NOT EXISTS idx_quiz_paper_invite_start_date ON quiz_paper(invite_start_date);

  CREATE TABLE IF NOT EXISTS quiz_asset (
    id BIGSERIAL PRIMARY KEY,
    quiz_key TEXT NOT NULL,
    relpath TEXT NOT NULL,
    content BYTEA NOT NULL,
    mime TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  );
  DO $$
  BEGIN
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'quiz_asset' AND column_name = 'exam_key'
    ) AND NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'quiz_asset' AND column_name = 'quiz_key'
    ) THEN
      EXECUTE 'ALTER TABLE quiz_asset RENAME COLUMN exam_key TO quiz_key';
    END IF;
  END$$;
  DO $$
  BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'quiz_asset_quiz_key_relpath_key') THEN
      BEGIN
        ALTER TABLE quiz_asset ADD CONSTRAINT quiz_asset_quiz_key_relpath_key UNIQUE (quiz_key, relpath);
      EXCEPTION WHEN OTHERS THEN NULL;
      END;
    END IF;
  END$$;
  CREATE INDEX IF NOT EXISTS idx_quiz_asset_quiz_key ON quiz_asset(quiz_key);

  CREATE TABLE IF NOT EXISTS quiz_version (
    id BIGSERIAL PRIMARY KEY,
    quiz_key TEXT NOT NULL,
    version_no INT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    source_path TEXT NULL,
    git_repo_url TEXT NULL,
    git_commit TEXT NULL,
    content_hash TEXT NOT NULL,
    source_md TEXT NOT NULL,
    spec JSONB NOT NULL,
    public_spec JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  );
  DO $$
  BEGIN
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'quiz_version' AND column_name = 'exam_key'
    ) AND NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'quiz_version' AND column_name = 'quiz_key'
    ) THEN
      EXECUTE 'ALTER TABLE quiz_version RENAME COLUMN exam_key TO quiz_key';
    END IF;
  END$$;
  DO $$
  BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'quiz_version_quiz_key_version_no_key') THEN
      BEGIN
        ALTER TABLE quiz_version ADD CONSTRAINT quiz_version_quiz_key_version_no_key UNIQUE (quiz_key, version_no);
      EXCEPTION WHEN OTHERS THEN NULL;
      END;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'quiz_version_quiz_key_content_hash_key') THEN
      BEGIN
        ALTER TABLE quiz_version ADD CONSTRAINT quiz_version_quiz_key_content_hash_key UNIQUE (quiz_key, content_hash);
      EXCEPTION WHEN OTHERS THEN NULL;
      END;
    END IF;
  END$$;
  CREATE INDEX IF NOT EXISTS idx_quiz_version_quiz_key ON quiz_version(quiz_key);
  CREATE INDEX IF NOT EXISTS idx_quiz_version_created_at ON quiz_version(created_at);

  CREATE TABLE IF NOT EXISTS quiz_version_asset (
    id BIGSERIAL PRIMARY KEY,
    quiz_version_id BIGINT NOT NULL REFERENCES quiz_version(id) ON DELETE CASCADE,
    relpath TEXT NOT NULL,
    content BYTEA NOT NULL,
    mime TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  );
  DO $$
  BEGIN
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'quiz_version_asset' AND column_name = 'exam_version_id'
    ) AND NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'quiz_version_asset' AND column_name = 'quiz_version_id'
    ) THEN
      EXECUTE 'ALTER TABLE quiz_version_asset RENAME COLUMN exam_version_id TO quiz_version_id';
    END IF;
  END$$;
  DO $$
  BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'quiz_version_asset_version_relpath_key') THEN
      BEGIN
        ALTER TABLE quiz_version_asset ADD CONSTRAINT quiz_version_asset_version_relpath_key UNIQUE (quiz_version_id, relpath);
      EXCEPTION WHEN OTHERS THEN NULL;
      END;
    END IF;
  END$$;
  CREATE INDEX IF NOT EXISTS idx_quiz_version_asset_version_id ON quiz_version_asset(quiz_version_id);

  CREATE TABLE IF NOT EXISTS quiz_definition (
    quiz_key TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    source_md TEXT NOT NULL,
    spec JSONB NOT NULL,
    public_spec JSONB NOT NULL,
    public_invite_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    public_invite_token TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  );
  DO $$
  BEGIN
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'quiz_definition' AND column_name = 'exam_key'
    ) AND NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'quiz_definition' AND column_name = 'quiz_key'
    ) THEN
      EXECUTE 'ALTER TABLE quiz_definition RENAME COLUMN exam_key TO quiz_key';
    END IF;
  END$$;
  ALTER TABLE quiz_definition ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';
  ALTER TABLE quiz_definition ADD COLUMN IF NOT EXISTS source_path TEXT NULL;
  ALTER TABLE quiz_definition ADD COLUMN IF NOT EXISTS git_repo_url TEXT NULL;
  ALTER TABLE quiz_definition ADD COLUMN IF NOT EXISTS current_version_id BIGINT NULL;
  ALTER TABLE quiz_definition ADD COLUMN IF NOT EXISTS current_version_no INT NOT NULL DEFAULT 0;
  ALTER TABLE quiz_definition ADD COLUMN IF NOT EXISTS last_synced_commit TEXT NULL;
  ALTER TABLE quiz_definition ADD COLUMN IF NOT EXISTS last_sync_error TEXT NULL;
  ALTER TABLE quiz_definition ADD COLUMN IF NOT EXISTS last_sync_at TIMESTAMPTZ NULL;
  CREATE UNIQUE INDEX IF NOT EXISTS idx_quiz_definition_public_invite_token
    ON quiz_definition(public_invite_token)
    WHERE public_invite_token IS NOT NULL;
  CREATE INDEX IF NOT EXISTS idx_quiz_definition_created_at ON quiz_definition(created_at);
  CREATE INDEX IF NOT EXISTS idx_quiz_definition_status ON quiz_definition(status);

  CREATE TABLE IF NOT EXISTS quiz_archive (
    archive_name TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    candidate_id BIGINT NULL REFERENCES candidate(id) ON DELETE SET NULL,
    quiz_key TEXT NOT NULL,
    quiz_version_id BIGINT NULL,
    phone TEXT NOT NULL,
    archive JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  );
  DO $$
  BEGIN
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'quiz_archive' AND column_name = 'exam_key'
    ) AND NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'quiz_archive' AND column_name = 'quiz_key'
    ) THEN
      EXECUTE 'ALTER TABLE quiz_archive RENAME COLUMN exam_key TO quiz_key';
    END IF;
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'quiz_archive' AND column_name = 'exam_version_id'
    ) AND NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'quiz_archive' AND column_name = 'quiz_version_id'
    ) THEN
      EXECUTE 'ALTER TABLE quiz_archive RENAME COLUMN exam_version_id TO quiz_version_id';
    END IF;
  END$$;
  ALTER TABLE quiz_archive ADD COLUMN IF NOT EXISTS quiz_version_id BIGINT NULL;
  CREATE UNIQUE INDEX IF NOT EXISTS idx_quiz_archive_token ON quiz_archive(token);
  CREATE INDEX IF NOT EXISTS idx_quiz_archive_phone ON quiz_archive(phone);
  CREATE INDEX IF NOT EXISTS idx_quiz_archive_quiz_key ON quiz_archive(quiz_key);
  CREATE INDEX IF NOT EXISTS idx_quiz_archive_quiz_version_id ON quiz_archive(quiz_version_id);

  CREATE TABLE IF NOT EXISTS runtime_kv (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  );

  CREATE TABLE IF NOT EXISTS runtime_daily_metric (
    day DATE NOT NULL,
    key TEXT NOT NULL,
    value_int BIGINT NULL,
    value_json JSONB NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(day, key)
  );
  CREATE INDEX IF NOT EXISTS idx_runtime_daily_metric_day ON runtime_daily_metric(day);

  CREATE TABLE IF NOT EXISTS runtime_job (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    status TEXT NOT NULL DEFAULT 'pending',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    dedupe_key TEXT NULL,
    attempts INT NOT NULL DEFAULT 0,
    error TEXT NULL,
    result JSONB NULL,
    worker_name TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    finished_at TIMESTAMPTZ NULL
  );
  ALTER TABLE runtime_job ADD COLUMN IF NOT EXISTS dedupe_key TEXT NULL;
  ALTER TABLE runtime_job ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ NULL;
  CREATE INDEX IF NOT EXISTS idx_runtime_job_status_created_at ON runtime_job(status, created_at);
  CREATE UNIQUE INDEX IF NOT EXISTS uq_runtime_job_active_dedupe_key
    ON runtime_job(dedupe_key)
    WHERE dedupe_key IS NOT NULL AND status IN ('pending', 'running');
  CREATE INDEX IF NOT EXISTS idx_runtime_job_running_lease
    ON runtime_job(status, lease_expires_at)
    WHERE status = 'running';

  CREATE TABLE IF NOT EXISTS process_heartbeat (
    name TEXT PRIMARY KEY,
    process TEXT NOT NULL,
    pid INT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  );
  CREATE INDEX IF NOT EXISTS idx_process_heartbeat_updated_at ON process_heartbeat(updated_at);

  CREATE TABLE IF NOT EXISTS system_log (
    id BIGSERIAL PRIMARY KEY,
    at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor TEXT NOT NULL,
    event_type TEXT NOT NULL,
    candidate_id BIGINT NULL,
    quiz_key TEXT NULL,
    token TEXT NULL,
    llm_prompt_tokens INT NULL,
    llm_completion_tokens INT NULL,
    llm_total_tokens INT NULL,
    started_at TIMESTAMPTZ NULL,
    finished_at TIMESTAMPTZ NULL,
    duration_seconds INT NULL,
    ip TEXT NULL,
    user_agent TEXT NULL,
    meta JSONB NULL
  );
  DO $$
  BEGIN
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'system_log' AND column_name = 'exam_key'
    ) AND NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'system_log' AND column_name = 'quiz_key'
    ) THEN
      EXECUTE 'ALTER TABLE system_log RENAME COLUMN exam_key TO quiz_key';
    END IF;
  END$$;
  ALTER TABLE system_log ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ NULL;
  ALTER TABLE system_log ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ NULL;
  CREATE INDEX IF NOT EXISTS idx_system_log_at ON system_log(at);
  CREATE INDEX IF NOT EXISTS idx_system_log_event_type ON system_log(event_type);
  CREATE INDEX IF NOT EXISTS idx_system_log_candidate_id ON system_log(candidate_id);
  CREATE INDEX IF NOT EXISTS idx_system_log_quiz_key ON system_log(quiz_key);
  CREATE INDEX IF NOT EXISTS idx_system_log_token ON system_log(token);
  """
    try:
        # PostgreSQL requires committing enum value changes before using them in
        # defaults/updates within the same session. Execute enum DDL separately.
        with conn_scope() as conn:
            with conn.cursor() as cur:
                cur.execute(enum_ddl)
        with conn_scope() as conn:
            with conn.cursor() as cur:
                cur.execute(quiz_paper_enum_ddl)
        with conn_scope() as conn:
            with conn.cursor() as cur:
                cur.execute(schema_ddl)
        try:
            n = backfill_system_log_llm_totals_from_meta()
            if n > 0:
                logger.info("Backfilled system_log llm_total_tokens from meta: %s rows", n)
            n2 = backfill_system_log_llm_totals_zero_for_ai_generate_missing()
            if n2 > 0:
                logger.info(
                    "Backfilled system_log llm_total_tokens=0 for historical ai.generate rows: %s rows",
                    n2,
                )
        except Exception:
            logger.exception("Failed to backfill system_log llm_total_tokens from meta")
        logger.info("DB ready")
    except Exception as e:
        raise RuntimeError(
            f"Database connection failed. Please check DATABASE_URL and PostgreSQL status. Details: {type(e).__name__}({e})"
        ) from e


# 从 PostgreSQL 的 candidate 表里查询候选人（考生）列表，按创建时间倒序排列
def list_candidates(
    limit: int | None = None,
    offset: int = 0,
    *,
    query: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> list[dict[str, Any]]:
    # 查询的sql语句
    sql = """
WITH filtered_candidates AS (
 SELECT
   id,
   name,
   phone,
   created_at,
    (resume_bytes IS NOT NULL) AS has_resume
  FROM candidate
 WHERE deleted_at IS NULL
   """
    params: list[Any] = []      # 最多返回多少条
    where_sql, where_params = _candidate_query_where_clause(query)
    if where_sql:
        # _candidate_query_where_clause returns a " WHERE ..." fragment; we already have a base WHERE.
        sql += " AND " + where_sql.strip().removeprefix("WHERE").removeprefix("where").strip()
        params.extend(where_params)

    if created_from is not None:
        if " WHERE " in sql:
            sql += " AND created_at >= %s"
        else:
            sql += " WHERE created_at >= %s"
        params.append(created_from)
    if created_to is not None:
        if " WHERE " in sql:
            sql += " AND created_at <= %s"
        else:
            sql += " WHERE created_at <= %s"
        params.append(created_to)

    sql += "\nORDER BY created_at DESC, id DESC\n"
    # 将限制的limit的数量传到数据库中
    if limit is not None:
        sql += " LIMIT %s"
        params.append(int(limit))   # 确保传给数据库的一定是整数 
    if offset:
        sql += " OFFSET %s"
        params.append(int(offset))
    sql += """
)
SELECT
  c.id,
  c.name,
  c.phone,
  c.created_at,
  c.has_resume,
  COALESCE(attempts.attempt_summaries, '[]'::jsonb) AS attempt_summaries
FROM filtered_candidates c
LEFT JOIN LATERAL (
  SELECT jsonb_agg(
    jsonb_build_object(
      'attempt_id', recent.attempt_id,
      'quiz_key', recent.quiz_key,
      'quiz_version_id', recent.quiz_version_id,
      'quiz_title', recent.quiz_title,
      'token', recent.token,
      'status', recent.status,
      'score', recent.score,
      'score_max', recent.score_max,
      'result_mode', recent.result_mode,
      'finished_at', recent.finished_at,
      'created_at', recent.created_at
    )
    ORDER BY recent.sort_at DESC, recent.attempt_id DESC
  ) AS attempt_summaries
  FROM (
    SELECT
      ep.id AS attempt_id,
      ep.quiz_key,
      ep.quiz_version_id,
      COALESCE(NULLIF(qv.title, ''), NULLIF(qd.title, ''), ep.quiz_key, '') AS quiz_title,
      ep.token,
      ep.status,
      ep.score,
      ar.data->'grading'->>'total_max' AS score_max,
      ar.data->'grading'->>'result_mode' AS result_mode,
      ep.finished_at,
      ep.created_at,
      COALESCE(ep.finished_at, ep.entered_at, ep.created_at) AS sort_at
    FROM quiz_paper ep
    LEFT JOIN assignment_record ar ON ar.token = ep.token
    LEFT JOIN quiz_version qv ON qv.id = ep.quiz_version_id
    LEFT JOIN quiz_definition qd ON qd.quiz_key = ep.quiz_key
    WHERE ep.candidate_id = c.id
    ORDER BY COALESCE(ep.finished_at, ep.entered_at, ep.created_at) DESC, ep.id DESC
    LIMIT 2
  ) recent
) attempts ON TRUE
ORDER BY c.id DESC
"""
    # 连接数据库并将数据库中的内容都展示出来
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))     # 将参数列表换成元组传进去
            return [dict(r) for r in cur.fetchall()]


# 创建候选人身份信息
def count_candidates(
    *,
    query: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> int:
    sql = "SELECT COUNT(*) FROM candidate WHERE deleted_at IS NULL"
    params: list[Any] = []
    where_sql, where_params = _candidate_query_where_clause(query)
    if where_sql:
        sql += " AND " + where_sql.strip().removeprefix("WHERE").removeprefix("where").strip()
        params.extend(where_params)
    if created_from is not None:
        sql += " AND created_at >= %s"
        params.append(created_from)
    if created_to is not None:
        sql += " AND created_at <= %s"
        params.append(created_to)
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return int(cur.fetchone()[0])


def _job_description_where_clause(
    *,
    query: str | None = None,
    status: str | None = None,
    table_alias: str = "",
) -> tuple[str, list[Any]]:
    alias = str(table_alias or "").strip()
    if alias and not alias.endswith("."):
        alias = f"{alias}."
    where = [f"{alias}deleted_at IS NULL"]
    params: list[Any] = []

    current_status = str(status or "").strip().lower()
    if current_status:
        where.append(f"{alias}status = %s")
        params.append(current_status)

    q = str(query or "").strip()
    if q:
        like = f"%{q}%"
        where.append(
            f"({alias}title ILIKE %s OR {alias}content_md ILIKE %s OR {alias}jd_key ILIKE %s "
            f"OR {alias}source_path ILIKE %s OR CAST({alias}id AS TEXT) = %s)"
        )
        params.extend([like, like, like, like, q])

    return " WHERE " + " AND ".join(where), params


def count_job_descriptions(*, query: str | None = None, status: str | None = None) -> int:
    sql = "SELECT COUNT(*) FROM job_description"
    where_sql, params = _job_description_where_clause(query=query, status=status)
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql + where_sql, tuple(params))
            return int(cur.fetchone()[0])


def list_job_descriptions(
    *,
    limit: int | None = None,
    offset: int = 0,
    query: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    sql = """
 SELECT id, title, content_md, status, created_at, updated_at,
        jd_key, source_kind, source_path, git_repo_url,
        last_synced_commit, last_sync_error, last_sync_at, content_hash,
        related_quizzes::text AS related_quizzes
 FROM job_description jd
 """
    where_sql, params = _job_description_where_clause(query=query, status=status, table_alias="jd")
    sql += where_sql
    sql += "\n ORDER BY jd.updated_at DESC, jd.id DESC\n"
    if limit is not None:
        sql += " LIMIT %s"
        params.append(int(limit))
    if offset:
        sql += " OFFSET %s"
        params.append(int(offset))
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            return [_decode_job_description_row(row) for row in cur.fetchall()]


def list_job_description_options(*, limit: int = 500) -> list[dict[str, Any]]:
    sql = """
 SELECT id, title, status, created_at, updated_at,
        jd_key, source_kind, source_path, git_repo_url,
        last_synced_commit, last_sync_error, last_sync_at,
        related_quizzes::text AS related_quizzes
 FROM job_description
 WHERE deleted_at IS NULL
   AND status = 'active'
 ORDER BY
   updated_at DESC,
   id DESC
 LIMIT %s
 """
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (int(limit),))
            return [_decode_job_description_row(row) for row in cur.fetchall()]


def get_job_description(job_description_id: int) -> dict[str, Any] | None:
    sql = """
 SELECT id, title, content_md, status, created_at, updated_at,
        jd_key, source_kind, source_path, git_repo_url,
        last_synced_commit, last_sync_error, last_sync_at, content_hash,
        related_quizzes::text AS related_quizzes
 FROM job_description
 WHERE id = %s
   AND deleted_at IS NULL
 """
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (int(job_description_id),))
            row = cur.fetchone()
            return _decode_job_description_row(row) if row else None


def get_job_description_by_key(jd_key: str) -> dict[str, Any] | None:
    key = str(jd_key or "").strip()
    if not key:
        return None
    sql = """
 SELECT id, title, content_md, status, created_at, updated_at,
        jd_key, source_kind, source_path, git_repo_url,
        last_synced_commit, last_sync_error, last_sync_at, content_hash,
        related_quizzes::text AS related_quizzes
 FROM job_description
 WHERE jd_key = %s
   AND deleted_at IS NULL
 LIMIT 1
 """
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (key,))
            row = cur.fetchone()
            return _decode_job_description_row(row) if row else None


def upsert_job_description_from_repo(
    *,
    jd_key: str,
    title: str,
    content_md: str,
    status: str,
    source_path: str,
    git_repo_url: str,
    last_synced_commit: str,
    content_hash: str,
    last_sync_at,
    related_quizzes: list[str] | None = None,
) -> dict[str, Any]:
    key = str(jd_key or "").strip()
    if not key:
        raise ValueError("missing jd_key")
    status_key = str(status or "").strip().lower() or "draft"
    if status_key not in {"draft", "active", "archived"}:
        status_key = "draft"
    normalized_related_quizzes = _normalize_quiz_key_list(related_quizzes)
    select_sql = """
 SELECT id
 FROM job_description
 WHERE jd_key = %s
 LIMIT 1
 """
    returning_sql = """
 RETURNING id, title, content_md, status, created_at, updated_at,
           jd_key, source_kind, source_path, git_repo_url,
           last_synced_commit, last_sync_error, last_sync_at, content_hash,
           related_quizzes::text AS related_quizzes
 """
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(select_sql, (key,))
            existing = cur.fetchone()
            if existing:
                cur.execute(
                    """
 UPDATE job_description
 SET title = %s,
     content_md = %s,
     status = %s,
     related_quizzes = %s,
     source_kind = 'git',
     source_path = %s,
     git_repo_url = %s,
     last_synced_commit = %s,
     last_sync_error = '',
     last_sync_at = %s,
     content_hash = %s,
     deleted_at = NULL,
     updated_at = NOW()
 WHERE id = %s
"""
                    + returning_sql,
                    (
                        str(title or "").strip(),
                        str(content_md or ""),
                        status_key,
                        _json_param(normalized_related_quizzes),
                        str(source_path or "").strip(),
                        str(git_repo_url or "").strip(),
                        str(last_synced_commit or "").strip(),
                        last_sync_at,
                        str(content_hash or "").strip(),
                        int(existing["id"]),
                    ),
                )
            else:
                cur.execute(
                    """
 INSERT INTO job_description(
   title,
   content_md,
   status,
   related_quizzes,
   jd_key,
   source_kind,
   source_path,
   git_repo_url,
   last_synced_commit,
   last_sync_error,
   last_sync_at,
   content_hash
 )
 VALUES (%s, %s, %s, %s, %s, 'git', %s, %s, %s, '', %s, %s)
"""
                    + returning_sql,
                    (
                        str(title or "").strip(),
                        str(content_md or ""),
                        status_key,
                        _json_param(normalized_related_quizzes),
                        key,
                        str(source_path or "").strip(),
                        str(git_repo_url or "").strip(),
                        str(last_synced_commit or "").strip(),
                        last_sync_at,
                        str(content_hash or "").strip(),
                    ),
                )
            return _decode_job_description_row(cur.fetchone())


def mark_job_description_sync_error(
    *,
    jd_key: str = "",
    source_path: str,
    git_repo_url: str,
    last_synced_commit: str,
    message: str,
    last_sync_at,
) -> int:
    key = str(jd_key or "").strip()
    path = str(source_path or "").strip()
    repo_url = str(git_repo_url or "").strip()
    if not key and not (path and repo_url):
        return 0
    sql = """
 UPDATE job_description
 SET last_synced_commit = %s,
     last_sync_error = %s,
     last_sync_at = %s,
     updated_at = NOW()
 WHERE source_kind = 'git'
   AND deleted_at IS NULL
   AND (
     (%s <> '' AND jd_key = %s)
     OR (%s <> '' AND %s <> '' AND source_path = %s AND git_repo_url = %s)
   )
 """
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    str(last_synced_commit or "").strip(),
                    str(message or ""),
                    last_sync_at,
                    key,
                    key,
                    path,
                    repo_url,
                    path,
                    repo_url,
                ),
            )
            return int(cur.rowcount or 0)


def archive_missing_repo_job_descriptions(
    *,
    git_repo_url: str,
    active_source_paths: list[str],
    last_synced_commit: str,
    last_sync_at,
) -> int:
    repo_url = str(git_repo_url or "").strip()
    normalized_paths = sorted({str(path or "").strip() for path in active_source_paths if str(path or "").strip()})
    if not repo_url:
        return 0
    params: list[Any] = [str(last_synced_commit or "").strip(), last_sync_at, repo_url]
    where_extra = ""
    if normalized_paths:
        where_extra = " AND NOT (source_path = ANY(%s::text[]))"
        params.append(normalized_paths)
    sql = f"""
 UPDATE job_description
 SET status = 'archived',
     last_synced_commit = %s,
     last_sync_error = '',
     last_sync_at = %s,
     updated_at = NOW()
 WHERE source_kind = 'git'
   AND git_repo_url = %s
   AND deleted_at IS NULL
   AND status <> 'archived'
{where_extra}
 """
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return int(cur.rowcount or 0)


def list_candidate_job_descriptions(candidate_id: int) -> list[dict[str, Any]]:
    sql = """
 SELECT
   jd.id,
   jd.title,
   jd.status,
   jd.jd_key,
   jd.source_kind,
   jd.source_path,
   jd.git_repo_url,
   jd.related_quizzes::text AS related_quizzes,
   cjd.created_at AS linked_at,
   jd.created_at,
   jd.updated_at
 FROM candidate_job_description cjd
 JOIN job_description jd ON jd.id = cjd.job_description_id
 WHERE cjd.candidate_id = %s
   AND jd.deleted_at IS NULL
 ORDER BY cjd.created_at DESC, jd.id DESC
 """
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (int(candidate_id),))
            return [_decode_job_description_row(row) for row in cur.fetchall()]


def add_candidate_job_descriptions(candidate_id: int, job_description_ids: list[int]) -> list[dict[str, Any]]:
    normalized: list[int] = []
    seen: set[int] = set()
    for raw_id in job_description_ids:
        try:
            job_description_id = int(raw_id)
        except Exception:
            continue
        if job_description_id <= 0 or job_description_id in seen:
            continue
        seen.add(job_description_id)
        normalized.append(job_description_id)
    if not normalized:
        return list_candidate_job_descriptions(candidate_id)

    sql = """
 INSERT INTO candidate_job_description(candidate_id, job_description_id)
 SELECT %s, jd.id
 FROM job_description jd
 WHERE jd.id = ANY(%s::bigint[])
   AND jd.deleted_at IS NULL
   AND jd.status = 'active'
 ON CONFLICT (candidate_id, job_description_id) DO NOTHING
 """
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (int(candidate_id), normalized))
    return list_candidate_job_descriptions(candidate_id)


def remove_candidate_job_description(candidate_id: int, job_description_id: int) -> int:
    sql = """
 DELETE FROM candidate_job_description
 WHERE candidate_id = %s
   AND job_description_id = %s
 """
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (int(candidate_id), int(job_description_id)))
            return int(cur.rowcount or 0)


def create_job_description(
    *,
    title: str,
    content_md: str = "",
    status: str = "draft",
    related_quizzes: list[str] | None = None,
) -> dict[str, Any]:
    sql = """
 INSERT INTO job_description(title, content_md, status, related_quizzes)
 VALUES (%s, %s, %s, %s)
 RETURNING id, title, content_md, status, created_at, updated_at,
           jd_key, source_kind, source_path, git_repo_url,
           last_synced_commit, last_sync_error, last_sync_at, content_hash,
           related_quizzes::text AS related_quizzes
 """
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                sql,
                (
                    str(title or "").strip(),
                    str(content_md or ""),
                    str(status or "draft").strip(),
                    _json_param(_normalize_quiz_key_list(related_quizzes)),
                ),
            )
            return _decode_job_description_row(cur.fetchone())


def update_job_description(
    job_description_id: int,
    *,
    title: str,
    content_md: str,
    status: str,
    related_quizzes: list[str] | None = None,
) -> dict[str, Any] | None:
    sql = """
 UPDATE job_description
 SET title = %s,
     content_md = %s,
     status = %s,
     related_quizzes = %s,
     updated_at = NOW()
 WHERE id = %s
   AND deleted_at IS NULL
 RETURNING id, title, content_md, status, created_at, updated_at,
          jd_key, source_kind, source_path, git_repo_url,
          last_synced_commit, last_sync_error, last_sync_at, content_hash,
          related_quizzes::text AS related_quizzes
 """
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                sql,
                (
                    str(title or "").strip(),
                    str(content_md or ""),
                    str(status or "draft").strip(),
                    _json_param(_normalize_quiz_key_list(related_quizzes)),
                    int(job_description_id),
                ),
            )
            row = cur.fetchone()
            return _decode_job_description_row(row) if row else None


def delete_job_description(job_description_id: int) -> int:
    sql = """
 UPDATE job_description
 SET deleted_at = NOW(),
     updated_at = NOW(),
     status = 'archived'
 WHERE id = %s
   AND deleted_at IS NULL
 """
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (int(job_description_id),))
            return int(cur.rowcount or 0)


def get_candidate_by_phone(phone: str) -> dict[str, Any] | None:
    sql = """
 SELECT id, name, phone, created_at
  FROM candidate
 WHERE phone = %s
   AND deleted_at IS NULL
 ORDER BY id DESC
 LIMIT 1
 """
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (str(phone or ""),))
            row = cur.fetchone()
            return dict(row) if row else None


def create_candidate(name: str, phone: str, job_description_id: int | None = None) -> int:
    sql = """
 INSERT INTO candidate(name, phone)
 VALUES (%s, %s)
 RETURNING id
 """
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (name, phone))
            candidate_id = int(cur.fetchone()[0])
            if job_description_id is not None:
                cur.execute(
                    """
 INSERT INTO candidate_job_description(candidate_id, job_description_id)
 SELECT %s, jd.id
 FROM job_description jd
 WHERE jd.id = %s
   AND jd.deleted_at IS NULL
   AND jd.status = 'active'
 """,
                    (candidate_id, int(job_description_id)),
                )
                if int(cur.rowcount or 0) <= 0:
                    raise ValueError("job_description_not_found")
            return candidate_id


# 候选人（考生）的 id查找到候选者的身份信息
def get_candidate(candidate_id: int) -> dict[str, Any] | None:
    sql = """
 SELECT id, name, phone, created_at, deleted_at,
        resume_filename, resume_mime, resume_size, resume_parsed, resume_parsed_at
  FROM candidate
  WHERE id = %s
   """
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (int(candidate_id),))
            row = cur.fetchone()
            return dict(row) if row else None


def get_candidate_resume(candidate_id: int) -> dict[str, Any] | None:
    sql = """
 SELECT resume_bytes, resume_filename, resume_mime, resume_size
 FROM candidate
 WHERE id=%s
 """
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (int(candidate_id),))
            row = cur.fetchone()
            if not row:
                return None
            d = dict(row)
            b = d.get("resume_bytes")
            if isinstance(b, memoryview):
                d["resume_bytes"] = b.tobytes()
            return d


def update_candidate_resume(
    candidate_id: int,
    *,
    resume_bytes: bytes,
    resume_filename: str | None = None,
    resume_mime: str | None = None,
    resume_size: int | None = None,
    resume_parsed: dict[str, Any] | None = None,
) -> None:
    sql = """
 UPDATE candidate
 SET
   resume_bytes=%s,
   resume_filename=%s,
   resume_mime=%s,
   resume_size=%s,
   resume_parsed=%s,
   resume_parsed_at=NOW()
 WHERE id=%s
 """
    parsed_param = None
    if resume_parsed is not None:
        parsed_param = psycopg2.extras.Json(
            resume_parsed, dumps=lambda x: json.dumps(x, ensure_ascii=False)
        )
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    psycopg2.Binary(resume_bytes),
                    (resume_filename or None),
                    (resume_mime or None),
                    (int(resume_size) if resume_size is not None else None),
                    parsed_param,
                    int(candidate_id),
                ),
            )


def update_candidate_resume_parsed(
    candidate_id: int,
    *,
    resume_parsed: dict[str, Any] | None,
    touch_resume_parsed_at: bool = True,
) -> None:
    if touch_resume_parsed_at:
        sql = """
  UPDATE candidate
  SET
    resume_parsed=%s,
    resume_parsed_at=NOW()
  WHERE id=%s
  """
    else:
        sql = """
  UPDATE candidate
  SET
    resume_parsed=%s
  WHERE id=%s
  """
    parsed_param = None
    if resume_parsed is not None:
        parsed_param = psycopg2.extras.Json(resume_parsed, dumps=lambda x: json.dumps(x, ensure_ascii=False))
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (parsed_param, int(candidate_id)))


def mark_exam_deleted(quiz_key: str, *, marker: str = "已删除") -> int:
    """
    Count impacted history rows when an exam is deleted.
    We keep original quiz_key in quiz_paper to preserve attempt display data.
    """
    sql = "SELECT COUNT(*) FROM quiz_paper WHERE quiz_key=%s"
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(quiz_key or ""),))
            row = cur.fetchone()
            return int(row[0] or 0) if row else 0


def rename_quiz_key(old_quiz_key: str, new_quiz_key: str) -> int:
    """
    Rename quiz_key references in quiz_paper table.
    Returns number of affected rows.
    """
    sql = "UPDATE quiz_paper SET quiz_key=%s WHERE quiz_key=%s"
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(new_quiz_key or ""), str(old_quiz_key or "")))
            return int(cur.rowcount or 0)


def save_assignment_record(token: str, assignment: dict[str, Any]) -> None:
    token_str = str(token or "").strip()
    if not token_str:
        raise ValueError("missing token")
    assignment_obj = dict(assignment or {})
    quiz_key = str(assignment_obj.get("quiz_key") or "").strip()
    if not quiz_key:
        raise ValueError("missing quiz_key")
    try:
        candidate_id = int(assignment_obj.get("candidate_id") or 0)
    except Exception:
        candidate_id = 0
    candidate_id_param = int(candidate_id) if candidate_id > 0 else None
    try:
        quiz_version_id = int(assignment_obj.get("quiz_version_id") or 0)
    except Exception:
        quiz_version_id = 0
    quiz_version_id_param = int(quiz_version_id) if quiz_version_id > 0 else None
    status = str(assignment_obj.get("status") or "").strip() or "invited"
    created_at_raw = str(assignment_obj.get("created_at") or "").strip()
    created_at_param = None
    if created_at_raw:
        try:
            created_at_param = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
        except Exception:
            created_at_param = None
    payload = psycopg2.extras.Json(assignment_obj, dumps=lambda x: json.dumps(x, ensure_ascii=False))
    sql = """
INSERT INTO assignment_record(token, quiz_key, quiz_version_id, candidate_id, status, data, created_at, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, NOW()), NOW())
ON CONFLICT (token) DO UPDATE
SET
  quiz_key = EXCLUDED.quiz_key,
  quiz_version_id = EXCLUDED.quiz_version_id,
  candidate_id = EXCLUDED.candidate_id,
  status = EXCLUDED.status,
  data = EXCLUDED.data,
  updated_at = NOW()
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    token_str,
                    quiz_key,
                    quiz_version_id_param,
                    candidate_id_param,
                    status,
                    payload,
                    created_at_param,
                ),
            )


def create_assignment_record(token: str, assignment: dict[str, Any]) -> bool:
    token_str = str(token or "").strip()
    if not token_str:
        raise ValueError("missing token")
    assignment_obj = dict(assignment or {})
    quiz_key = str(assignment_obj.get("quiz_key") or "").strip()
    if not quiz_key:
        raise ValueError("missing quiz_key")
    try:
        candidate_id = int(assignment_obj.get("candidate_id") or 0)
    except Exception:
        candidate_id = 0
    candidate_id_param = int(candidate_id) if candidate_id > 0 else None
    try:
        quiz_version_id = int(assignment_obj.get("quiz_version_id") or 0)
    except Exception:
        quiz_version_id = 0
    quiz_version_id_param = int(quiz_version_id) if quiz_version_id > 0 else None
    status = str(assignment_obj.get("status") or "").strip() or "invited"
    created_at_raw = str(assignment_obj.get("created_at") or "").strip()
    created_at_param = None
    if created_at_raw:
        try:
            created_at_param = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
        except Exception:
            created_at_param = None
    payload = psycopg2.extras.Json(assignment_obj, dumps=lambda x: json.dumps(x, ensure_ascii=False))
    sql = """
INSERT INTO assignment_record(token, quiz_key, quiz_version_id, candidate_id, status, data, created_at, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, NOW()), NOW())
ON CONFLICT (token) DO NOTHING
RETURNING token
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    token_str,
                    quiz_key,
                    quiz_version_id_param,
                    candidate_id_param,
                    status,
                    payload,
                    created_at_param,
                ),
            )
            return cur.fetchone() is not None


def get_assignment_record(token: str) -> dict[str, Any] | None:
    sql = "SELECT data::text FROM assignment_record WHERE token=%s"
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(token or "").strip(),))
            row = cur.fetchone()
    if not row or not row[0]:
        return None
    try:
        obj = json.loads(str(row[0]))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def delete_assignment_record(token: str) -> int:
    sql = "DELETE FROM assignment_record WHERE token=%s"
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(token or "").strip(),))
            return int(cur.rowcount or 0)


def list_assignment_tokens() -> list[str]:
    sql = "SELECT token FROM assignment_record ORDER BY created_at DESC"
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
    return [str(row[0] or "").strip() for row in rows if str(row[0] or "").strip()]


def rename_assignment_quiz_key(old_quiz_key: str, new_quiz_key: str) -> int:
    sql = """
UPDATE assignment_record
SET
  quiz_key=%s,
  data=jsonb_set(COALESCE(data, '{}'::jsonb), '{quiz_key}', to_jsonb(%s::text), true),
  updated_at=NOW()
WHERE quiz_key=%s
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(new_quiz_key or ""), str(new_quiz_key or ""), str(old_quiz_key or "")))
            return int(cur.rowcount or 0)


def backfill_assignment_quiz_version_id(quiz_key: str, quiz_version_id: int) -> int:
    sql = """
UPDATE assignment_record
SET
  quiz_version_id = %s,
  data = jsonb_set(COALESCE(data, '{}'::jsonb), '{quiz_version_id}', to_jsonb(%s::bigint), true),
  updated_at = NOW()
WHERE quiz_key=%s AND quiz_version_id IS NULL
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (int(quiz_version_id), int(quiz_version_id), str(quiz_key or "").strip()))
            return int(cur.rowcount or 0)


def replace_quiz_assets(quiz_key: str, assets: dict[str, tuple[bytes, str]]) -> None:
    quiz_key_str = str(quiz_key or "").strip()
    if not quiz_key_str:
        raise ValueError("missing quiz_key")
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM quiz_asset WHERE quiz_key=%s", (quiz_key_str,))
            for relpath, payload in dict(assets or {}).items():
                rel = str(relpath or "").strip()
                if not rel:
                    continue
                content, mime = payload
                cur.execute(
                    """
INSERT INTO quiz_asset(quiz_key, relpath, content, mime, updated_at)
VALUES (%s, %s, %s, %s, NOW())
""",
                    (quiz_key_str, rel, psycopg2.Binary(bytes(content or b"")), str(mime or "application/octet-stream")),
                )


def get_quiz_asset(quiz_key: str, relpath: str) -> tuple[bytes, str] | None:
    sql = "SELECT content, mime FROM quiz_asset WHERE quiz_key=%s AND relpath=%s"
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(quiz_key or "").strip(), str(relpath or "").strip()))
            row = cur.fetchone()
    if not row:
        return None
    content = bytes(row[0] or b"")
    mime = str(row[1] or "application/octet-stream").strip() or "application/octet-stream"
    return content, mime


def list_quiz_assets(quiz_key: str) -> list[dict[str, Any]]:
    sql = "SELECT relpath, content, mime FROM quiz_asset WHERE quiz_key=%s ORDER BY relpath ASC"
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (str(quiz_key or "").strip(),))
            rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for row in rows or []:
        item = dict(row)
        item["content"] = bytes(item.get("content") or b"")
        item["mime"] = str(item.get("mime") or "application/octet-stream").strip() or "application/octet-stream"
        out.append(item)
    return out


def rename_quiz_assets(old_quiz_key: str, new_quiz_key: str) -> int:
    sql = "UPDATE quiz_asset SET quiz_key=%s, updated_at=NOW() WHERE quiz_key=%s"
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(new_quiz_key or ""), str(old_quiz_key or "")))
            return int(cur.rowcount or 0)


def delete_quiz_assets(quiz_key: str) -> int:
    sql = "DELETE FROM quiz_asset WHERE quiz_key=%s"
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(quiz_key or "").strip(),))
            return int(cur.rowcount or 0)


def save_quiz_definition(
    *,
    quiz_key: str,
    title: str,
    source_md: str,
    spec: dict[str, Any],
    public_spec: dict[str, Any],
    status: str | None = None,
    source_path: str | None = None,
    git_repo_url: str | None = None,
    current_version_id: int | None = None,
    current_version_no: int | None = None,
    last_synced_commit: str | None = None,
    last_sync_error: str | None = None,
    last_sync_at=None,
) -> None:
    sql = """
INSERT INTO quiz_definition(
  quiz_key,
  title,
  source_md,
  spec,
  public_spec,
  status,
  source_path,
  git_repo_url,
  current_version_id,
  current_version_no,
  last_synced_commit,
  last_sync_error,
  last_sync_at,
  created_at,
  updated_at
)
VALUES (
  %s,
  %s,
  %s,
  %s,
  %s,
  COALESCE(%s, 'active'),
  %s,
  %s,
  %s,
  COALESCE(%s, 0),
  %s,
  %s,
  %s,
  NOW(),
  NOW()
)
ON CONFLICT (quiz_key) DO UPDATE
SET
  title = EXCLUDED.title,
  source_md = EXCLUDED.source_md,
  spec = EXCLUDED.spec,
  public_spec = EXCLUDED.public_spec,
  status = COALESCE(EXCLUDED.status, quiz_definition.status),
  source_path = COALESCE(EXCLUDED.source_path, quiz_definition.source_path),
  git_repo_url = COALESCE(EXCLUDED.git_repo_url, quiz_definition.git_repo_url),
  current_version_id = COALESCE(EXCLUDED.current_version_id, quiz_definition.current_version_id),
  current_version_no = COALESCE(NULLIF(EXCLUDED.current_version_no, 0), quiz_definition.current_version_no),
  last_synced_commit = COALESCE(EXCLUDED.last_synced_commit, quiz_definition.last_synced_commit),
  last_sync_error = EXCLUDED.last_sync_error,
  last_sync_at = COALESCE(EXCLUDED.last_sync_at, quiz_definition.last_sync_at),
  updated_at = NOW()
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    str(quiz_key or "").strip(),
                    str(title or "").strip(),
                    str(source_md or ""),
                    _json_param(spec or {}),
                    _json_param(public_spec or {}),
                    (str(status).strip() if status is not None else None),
                    (str(source_path).strip() if source_path is not None else None),
                    (str(git_repo_url).strip() if git_repo_url is not None else None),
                    (int(current_version_id) if current_version_id else None),
                    (int(current_version_no) if current_version_no else None),
                    (str(last_synced_commit).strip() if last_synced_commit is not None else None),
                    (str(last_sync_error) if last_sync_error is not None else None),
                    last_sync_at,
                ),
            )


def get_quiz_definition(quiz_key: str) -> dict[str, Any] | None:
    sql = """
SELECT
  quiz_key,
  title,
  source_md,
  spec::text,
  public_spec::text,
  status,
  source_path,
  git_repo_url,
  current_version_id,
  current_version_no,
  public_invite_enabled,
  public_invite_token,
  last_synced_commit,
  last_sync_error,
  last_sync_at,
  created_at,
  updated_at
FROM quiz_definition
WHERE quiz_key=%s
"""
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (str(quiz_key or "").strip(),))
            row = cur.fetchone()
    if not row:
        return None
    out = dict(row)
    out["spec"] = _json_load(out.get("spec")) or {}
    out["public_spec"] = _json_load(out.get("public_spec")) or {}
    return out


def list_quiz_definitions() -> list[dict[str, Any]]:
    sql = """
SELECT
  quiz_key,
  title,
  spec::text,
  status,
  source_path,
  git_repo_url,
  current_version_id,
  current_version_no,
  public_invite_enabled,
  public_invite_token,
  last_synced_commit,
  last_sync_error,
  last_sync_at,
  created_at,
  updated_at
FROM quiz_definition
ORDER BY created_at ASC, quiz_key ASC
"""
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for row in rows or []:
        item = dict(row)
        item["spec"] = _json_load(item.get("spec")) or {}
        out.append(item)
    return out


def rename_quiz_definition(old_quiz_key: str, new_quiz_key: str) -> int:
    sql = "UPDATE quiz_definition SET quiz_key=%s, updated_at=NOW() WHERE quiz_key=%s"
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(new_quiz_key or "").strip(), str(old_quiz_key or "").strip()))
            return int(cur.rowcount or 0)


def delete_quiz_definition(quiz_key: str) -> int:
    sql = "DELETE FROM quiz_definition WHERE quiz_key=%s"
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(quiz_key or "").strip(),))
            return int(cur.rowcount or 0)


def get_exam_public_invite(quiz_key: str) -> dict[str, Any] | None:
    sql = """
SELECT public_invite_enabled, public_invite_token, created_at, title
FROM quiz_definition
WHERE quiz_key=%s
"""
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (str(quiz_key or "").strip(),))
            row = cur.fetchone()
    return dict(row) if row else None


def set_exam_public_invite(quiz_key: str, *, enabled: bool, token: str | None) -> int:
    sql = """
UPDATE quiz_definition
SET
  public_invite_enabled=%s,
  public_invite_token=%s,
  updated_at=NOW()
WHERE quiz_key=%s
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    bool(enabled),
                    (str(token or "").strip() or None),
                    str(quiz_key or "").strip(),
                ),
            )
            return int(cur.rowcount or 0)


def get_quiz_key_by_public_invite_token(token: str) -> str:
    sql = """
SELECT quiz_key
FROM quiz_definition
WHERE public_invite_enabled = TRUE AND public_invite_token = %s
LIMIT 1
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(token or "").strip(),))
            row = cur.fetchone()
    return str((row[0] if row else "") or "").strip()


def create_quiz_version(
    *,
    quiz_key: str,
    version_no: int,
    title: str,
    source_path: str | None,
    git_repo_url: str | None,
    git_commit: str | None,
    content_hash: str,
    source_md: str,
    spec: dict[str, Any],
    public_spec: dict[str, Any],
) -> int:
    sql = """
INSERT INTO quiz_version(
  quiz_key,
  version_no,
  title,
  source_path,
  git_repo_url,
  git_commit,
  content_hash,
  source_md,
  spec,
  public_spec,
  created_at,
  updated_at
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
RETURNING id
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    str(quiz_key or "").strip(),
                    int(version_no),
                    str(title or "").strip(),
                    (str(source_path).strip() if source_path is not None else None),
                    (str(git_repo_url).strip() if git_repo_url is not None else None),
                    (str(git_commit).strip() if git_commit is not None else None),
                    str(content_hash or "").strip(),
                    str(source_md or ""),
                    _json_param(spec or {}),
                    _json_param(public_spec or {}),
                ),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0


def update_quiz_version_metadata(
    version_id: int,
    *,
    title: str | None = None,
    source_path: str | None = None,
    git_repo_url: str | None = None,
    git_commit: str | None = None,
) -> int:
    sql = """
UPDATE quiz_version
SET
  title = COALESCE(%s, title),
  source_path = COALESCE(%s, source_path),
  git_repo_url = COALESCE(%s, git_repo_url),
  git_commit = COALESCE(%s, git_commit),
  updated_at = NOW()
WHERE id=%s
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    (str(title).strip() if title is not None else None),
                    (str(source_path).strip() if source_path is not None else None),
                    (str(git_repo_url).strip() if git_repo_url is not None else None),
                    (str(git_commit).strip() if git_commit is not None else None),
                    int(version_id),
                ),
            )
            return int(cur.rowcount or 0)


def update_quiz_version_payload(
    version_id: int,
    *,
    title: str,
    source_md: str,
    spec: dict[str, Any],
    public_spec: dict[str, Any],
) -> int:
    sql = """
UPDATE quiz_version
SET
  title=%s,
  source_md=%s,
  spec=%s,
  public_spec=%s,
  updated_at=NOW()
WHERE id=%s
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    str(title or "").strip(),
                    str(source_md or ""),
                    _json_param(spec or {}),
                    _json_param(public_spec or {}),
                    int(version_id),
                ),
            )
            return int(cur.rowcount or 0)


def get_quiz_version(version_id: int) -> dict[str, Any] | None:
    sql = """
SELECT
  id,
  quiz_key,
  version_no,
  title,
  source_path,
  git_repo_url,
  git_commit,
  content_hash,
  source_md,
  spec::text,
  public_spec::text,
  created_at,
  updated_at
FROM quiz_version
WHERE id=%s
LIMIT 1
"""
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (int(version_id),))
            row = cur.fetchone()
    if not row:
        return None
    out = dict(row)
    out["spec"] = _json_load(out.get("spec")) or {}
    out["public_spec"] = _json_load(out.get("public_spec")) or {}
    return out


def get_current_quiz_version(quiz_key: str) -> dict[str, Any] | None:
    sql = """
SELECT ev.id
FROM quiz_definition ed
JOIN quiz_version ev ON ev.id = ed.current_version_id
WHERE ed.quiz_key=%s
LIMIT 1
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(quiz_key or "").strip(),))
            row = cur.fetchone()
    if not row:
        return None
    return get_quiz_version(int(row[0]))


def find_quiz_version_by_hash(quiz_key: str, content_hash: str) -> dict[str, Any] | None:
    sql = """
SELECT
  id,
  quiz_key,
  version_no,
  title,
  source_path,
  git_repo_url,
  git_commit,
  content_hash,
  source_md,
  spec::text,
  public_spec::text,
  created_at,
  updated_at
FROM quiz_version
WHERE quiz_key=%s AND content_hash=%s
LIMIT 1
"""
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute((sql), (str(quiz_key or "").strip(), str(content_hash or "").strip()))
            row = cur.fetchone()
    if not row:
        return None
    out = dict(row)
    out["spec"] = _json_load(out.get("spec")) or {}
    out["public_spec"] = _json_load(out.get("public_spec")) or {}
    return out


def list_quiz_versions(quiz_key: str) -> list[dict[str, Any]]:
    sql = """
SELECT
  id,
  quiz_key,
  version_no,
  title,
  source_path,
  git_repo_url,
  git_commit,
  content_hash,
  source_md,
  spec::text,
  public_spec::text,
  created_at,
  updated_at
FROM quiz_version
WHERE quiz_key=%s
ORDER BY version_no DESC, id DESC
"""
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (str(quiz_key or "").strip(),))
            rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for row in rows or []:
        item = dict(row)
        item["spec"] = _json_load(item.get("spec")) or {}
        item["public_spec"] = _json_load(item.get("public_spec")) or {}
        out.append(item)
    return out


def replace_quiz_version_assets(version_id: int, assets: dict[str, tuple[bytes, str]]) -> None:
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM quiz_version_asset WHERE quiz_version_id=%s", (int(version_id),))
            for relpath, payload in dict(assets or {}).items():
                rel = str(relpath or "").strip()
                if not rel:
                    continue
                content, mime = payload
                cur.execute(
                    """
INSERT INTO quiz_version_asset(quiz_version_id, relpath, content, mime, updated_at)
VALUES (%s, %s, %s, %s, NOW())
""",
                    (int(version_id), rel, psycopg2.Binary(bytes(content or b"")), str(mime or "application/octet-stream")),
                )


def get_quiz_version_asset(version_id: int, relpath: str) -> tuple[bytes, str] | None:
    sql = "SELECT content, mime FROM quiz_version_asset WHERE quiz_version_id=%s AND relpath=%s"
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (int(version_id), str(relpath or "").strip()))
            row = cur.fetchone()
    if not row:
        return None
    return bytes(row[0] or b""), str(row[1] or "application/octet-stream").strip() or "application/octet-stream"


def save_quiz_archive(
    *,
    archive_name: str,
    token: str,
    candidate_id: int | None,
    quiz_key: str,
    quiz_version_id: int | None = None,
    phone: str,
    archive: dict[str, Any],
) -> None:
    sql = """
INSERT INTO quiz_archive(archive_name, token, candidate_id, quiz_key, quiz_version_id, phone, archive, created_at, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
ON CONFLICT (archive_name) DO UPDATE
SET
  token = EXCLUDED.token,
  candidate_id = EXCLUDED.candidate_id,
  quiz_key = EXCLUDED.quiz_key,
  quiz_version_id = EXCLUDED.quiz_version_id,
  phone = EXCLUDED.phone,
  archive = EXCLUDED.archive,
  updated_at = NOW()
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    str(archive_name or "").strip(),
                    str(token or "").strip(),
                    (int(candidate_id) if candidate_id else None),
                    str(quiz_key or "").strip(),
                    (int(quiz_version_id) if quiz_version_id else None),
                    str(phone or "").strip(),
                    _json_param(archive or {}),
                ),
            )


def get_quiz_archive_by_name(archive_name: str) -> dict[str, Any] | None:
    sql = """
SELECT archive_name, token, candidate_id, quiz_key, quiz_version_id, phone, archive::text, created_at, updated_at
FROM quiz_archive
WHERE archive_name=%s
"""
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (str(archive_name or "").strip(),))
            row = cur.fetchone()
    if not row:
        return None
    out = dict(row)
    out["archive"] = _json_load(out.get("archive")) or {}
    return out


def get_quiz_archive_by_token(token: str) -> dict[str, Any] | None:
    sql = """
SELECT archive_name, token, candidate_id, quiz_key, quiz_version_id, phone, archive::text, created_at, updated_at
FROM quiz_archive
WHERE token=%s
"""
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (str(token or "").strip(),))
            row = cur.fetchone()
    if not row:
        return None
    out = dict(row)
    out["archive"] = _json_load(out.get("archive")) or {}
    return out


def delete_quiz_archive_by_token(token: str) -> int:
    sql = "DELETE FROM quiz_archive WHERE token=%s"
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(token or "").strip(),))
            return int(cur.rowcount or 0)


def list_quiz_archives_for_phone(phone: str) -> list[dict[str, Any]]:
    sql = """
SELECT archive_name, token, candidate_id, quiz_key, quiz_version_id, phone, archive::text, created_at, updated_at
FROM quiz_archive
WHERE phone=%s
ORDER BY updated_at DESC, archive_name DESC
"""
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (str(phone or "").strip(),))
            rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for row in rows or []:
        item = dict(row)
        item["archive"] = _json_load(item.get("archive")) or {}
        out.append(item)
    return out


def list_quiz_archives_by_quiz_key(quiz_key: str) -> list[dict[str, Any]]:
    sql = """
SELECT archive_name, token, candidate_id, quiz_key, quiz_version_id, phone, archive::text, created_at, updated_at
FROM quiz_archive
WHERE quiz_key=%s
ORDER BY updated_at DESC, archive_name DESC
"""
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (str(quiz_key or "").strip(),))
            rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for row in rows or []:
        item = dict(row)
        item["archive"] = _json_load(item.get("archive")) or {}
        out.append(item)
    return out


def rename_quiz_archives_quiz_key(old_quiz_key: str, new_quiz_key: str) -> int:
    sql = """
UPDATE quiz_archive
SET
  quiz_key=%s,
  archive=jsonb_set(COALESCE(archive, '{}'::jsonb), '{exam,quiz_key}', to_jsonb(%s::text), true),
  updated_at=NOW()
WHERE quiz_key=%s
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(new_quiz_key or ""), str(new_quiz_key or ""), str(old_quiz_key or "")))
            return int(cur.rowcount or 0)


def backfill_quiz_archive_version_id(quiz_key: str, quiz_version_id: int) -> int:
    sql = """
UPDATE quiz_archive
SET
  quiz_version_id = %s,
  archive = jsonb_set(COALESCE(archive, '{}'::jsonb), '{exam,quiz_version_id}', to_jsonb(%s::bigint), true),
  updated_at = NOW()
WHERE quiz_key=%s AND quiz_version_id IS NULL
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (int(quiz_version_id), int(quiz_version_id), str(quiz_key or "").strip()))
            return int(cur.rowcount or 0)


def get_runtime_kv(key: str) -> dict[str, Any] | None:
    sql = "SELECT value::text FROM runtime_kv WHERE key=%s"
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(key or "").strip(),))
            row = cur.fetchone()
    value = _json_load(row[0] if row else None)
    return value if isinstance(value, dict) else None


def set_runtime_kv(key: str, value: dict[str, Any]) -> None:
    sql = """
INSERT INTO runtime_kv(key, value, updated_at)
VALUES (%s, %s, NOW())
ON CONFLICT (key) DO UPDATE
SET value=EXCLUDED.value, updated_at=NOW()
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(key or "").strip(), _json_param(value or {})))


def _delete_exam_domain_rows(cur) -> dict[str, int]:
    counts: dict[str, int] = {}
    cur.execute("SELECT COUNT(*) FROM quiz_version_asset")
    row = cur.fetchone()
    counts["quiz_version_asset"] = int((row[0] if row else 0) or 0)
    for table in ("quiz_archive", "quiz_paper", "assignment_record", "quiz_asset", "quiz_version", "quiz_definition"):
        cur.execute(f"DELETE FROM {table}")
        counts[table] = int(cur.rowcount or 0)
    return counts


def clear_exam_domain_data() -> dict[str, int]:
    with conn_scope() as conn:
        with conn.cursor() as cur:
            return _delete_exam_domain_rows(cur)


def delete_exam_domain_data_by_quiz_key(quiz_key: str) -> dict[str, int]:
    quiz_key_str = str(quiz_key or "").strip()
    if not quiz_key_str:
        raise ValueError("missing quiz_key")
    counts: dict[str, int] = {}
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM quiz_version_asset WHERE quiz_version_id IN (SELECT id FROM quiz_version WHERE quiz_key=%s)", (quiz_key_str,))
            row = cur.fetchone()
            counts["quiz_version_asset"] = int((row[0] if row else 0) or 0)
            cur.execute("DELETE FROM quiz_version_asset WHERE quiz_version_id IN (SELECT id FROM quiz_version WHERE quiz_key=%s)", (quiz_key_str,))
            for table in ("quiz_archive", "quiz_paper", "assignment_record", "quiz_asset", "quiz_version", "quiz_definition"):
                cur.execute(f"DELETE FROM {table} WHERE quiz_key=%s", (quiz_key_str,))
                counts[table] = int(cur.rowcount or 0)
    return counts


def clear_exam_domain_data_and_set_repo_binding(
    *,
    binding_key: str,
    binding_value: dict[str, Any],
    sync_state_key: str,
    sync_state_value: dict[str, Any],
) -> dict[str, int]:
    upsert_sql = """
INSERT INTO runtime_kv(key, value, updated_at)
VALUES (%s, %s, NOW())
ON CONFLICT (key) DO UPDATE
SET value=EXCLUDED.value, updated_at=NOW()
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            counts = _delete_exam_domain_rows(cur)
            cur.execute(upsert_sql, (str(binding_key or "").strip(), _json_param(binding_value or {})))
            cur.execute(upsert_sql, (str(sync_state_key or "").strip(), _json_param(sync_state_value or {})))
            return counts


def get_runtime_daily_metric_int(*, day: str, key: str) -> int:
    sql = "SELECT value_int FROM runtime_daily_metric WHERE day=%s::date AND key=%s"
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(day or "").strip()[:10], str(key or "").strip()))
            row = cur.fetchone()
    try:
        return int((row[0] if row else 0) or 0)
    except Exception:
        return 0


def incr_runtime_daily_metric_int(*, day: str, key: str, delta: int) -> int:
    sql = """
INSERT INTO runtime_daily_metric(day, key, value_int, updated_at)
VALUES (%s::date, %s, %s, NOW())
ON CONFLICT (day, key) DO UPDATE
SET
  value_int = GREATEST(0, COALESCE(runtime_daily_metric.value_int, 0) + EXCLUDED.value_int),
  updated_at = NOW()
RETURNING value_int
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    str(day or "").strip()[:10],
                    str(key or "").strip(),
                    int(delta or 0),
                ),
            )
            row = cur.fetchone()
    try:
        return int((row[0] if row else 0) or 0)
    except Exception:
        return 0


def set_runtime_daily_metric_json(*, day: str, key: str, value: dict[str, Any]) -> None:
    sql = """
INSERT INTO runtime_daily_metric(day, key, value_json, updated_at)
VALUES (%s::date, %s, %s, NOW())
ON CONFLICT (day, key) DO UPDATE
SET value_json=EXCLUDED.value_json, updated_at=NOW()
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    str(day or "").strip()[:10],
                    str(key or "").strip(),
                    _json_param(value or {}),
                ),
            )


def get_runtime_daily_metric_json(*, day: str, key: str) -> dict[str, Any] | None:
    sql = "SELECT value_json::text FROM runtime_daily_metric WHERE day=%s::date AND key=%s"
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(day or "").strip()[:10], str(key or "").strip()))
            row = cur.fetchone()
    value = _json_load(row[0] if row else None)
    return value if isinstance(value, dict) else None


def _runtime_job_row_to_dict(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    out = dict(row)
    out["payload"] = _json_load(out.get("payload")) or {}
    dedupe_key = str(out.get("dedupe_key") or "").strip()
    out["dedupe_key"] = dedupe_key or None
    result = _json_load(out.get("result"))
    out["result"] = result if isinstance(result, dict) else None
    for key in ("created_at", "updated_at", "started_at", "lease_expires_at", "finished_at"):
        out[key] = _iso_or_none(out.get(key))
    try:
        out["attempts"] = int(out.get("attempts") or 0)
    except Exception:
        out["attempts"] = 0
    return out


def list_runtime_jobs() -> list[dict[str, Any]]:
    sql = """
SELECT
  id,
  kind,
  source,
  status,
  payload,
  dedupe_key,
  attempts,
  error,
  result,
  worker_name,
  created_at,
  updated_at,
  started_at,
  lease_expires_at,
  finished_at
FROM runtime_job
ORDER BY created_at DESC, id DESC
"""
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            return [_runtime_job_row_to_dict(dict(row)) or {} for row in cur.fetchall()]


def get_runtime_job(job_id: str) -> dict[str, Any] | None:
    sql = """
SELECT
  id,
  kind,
  source,
  status,
  payload,
  dedupe_key,
  attempts,
  error,
  result,
  worker_name,
  created_at,
  updated_at,
  started_at,
  lease_expires_at,
  finished_at
FROM runtime_job
WHERE id = %s
LIMIT 1
"""
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (str(job_id or "").strip(),))
            row = cur.fetchone()
    return _runtime_job_row_to_dict(dict(row)) if row else None


def get_active_runtime_job_by_dedupe_key(dedupe_key: str) -> dict[str, Any] | None:
    sql = """
SELECT
  id,
  kind,
  source,
  status,
  payload,
  dedupe_key,
  attempts,
  error,
  result,
  worker_name,
  created_at,
  updated_at,
  started_at,
  lease_expires_at,
  finished_at
FROM runtime_job
WHERE dedupe_key = %s
  AND status IN ('pending', 'running')
ORDER BY created_at DESC, id DESC
LIMIT 1
"""
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (str(dedupe_key or "").strip(),))
            row = cur.fetchone()
    return _runtime_job_row_to_dict(dict(row)) if row else None


def create_runtime_job(record: dict[str, Any]) -> dict[str, Any]:
    sql = """
INSERT INTO runtime_job(
  id,
  kind,
  source,
  status,
  payload,
  dedupe_key,
  attempts,
  error,
  result,
  worker_name,
  created_at,
  updated_at,
  started_at,
  lease_expires_at,
  finished_at
)
VALUES (
  %s,
  %s,
  %s,
  %s,
  %s,
  %s,
  %s,
  %s,
  %s,
  %s,
  %s::timestamptz,
  %s::timestamptz,
  %s::timestamptz,
  %s::timestamptz,
  %s::timestamptz
)
RETURNING
  id,
  kind,
  source,
  status,
  payload,
  dedupe_key,
  attempts,
  error,
  result,
  worker_name,
  created_at,
  updated_at,
  started_at,
  lease_expires_at,
  finished_at
"""
    payload = dict(record or {})
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                sql,
                (
                    str(payload.get("id") or "").strip(),
                    str(payload.get("kind") or "").strip(),
                    str(payload.get("source") or "manual").strip() or "manual",
                    str(payload.get("status") or "pending").strip() or "pending",
                    _json_param(payload.get("payload") or {}),
                    str(payload.get("dedupe_key") or "").strip() or None,
                    int(payload.get("attempts") or 0),
                    payload.get("error"),
                    _json_param(payload.get("result")) if isinstance(payload.get("result"), dict) else None,
                    payload.get("worker_name"),
                    payload.get("created_at"),
                    payload.get("updated_at"),
                    payload.get("started_at"),
                    payload.get("lease_expires_at"),
                    payload.get("finished_at"),
                ),
            )
            return _runtime_job_row_to_dict(dict(cur.fetchone())) or {}


def upsert_runtime_job(record: dict[str, Any]) -> dict[str, Any]:
    sql = """
INSERT INTO runtime_job(
  id,
  kind,
  source,
  status,
  payload,
  dedupe_key,
  attempts,
  error,
  result,
  worker_name,
  created_at,
  updated_at,
  started_at,
  lease_expires_at,
  finished_at
)
VALUES (
  %s,
  %s,
  %s,
  %s,
  %s,
  %s,
  %s,
  %s,
  %s,
  %s,
  %s::timestamptz,
  %s::timestamptz,
  %s::timestamptz,
  %s::timestamptz,
  %s::timestamptz
)
ON CONFLICT (id) DO UPDATE
SET
  kind = EXCLUDED.kind,
  source = EXCLUDED.source,
  status = EXCLUDED.status,
  payload = EXCLUDED.payload,
  dedupe_key = EXCLUDED.dedupe_key,
  attempts = EXCLUDED.attempts,
  error = EXCLUDED.error,
  result = EXCLUDED.result,
  worker_name = EXCLUDED.worker_name,
  created_at = EXCLUDED.created_at,
  updated_at = EXCLUDED.updated_at,
  started_at = EXCLUDED.started_at,
  lease_expires_at = EXCLUDED.lease_expires_at,
  finished_at = EXCLUDED.finished_at
RETURNING
  id,
  kind,
  source,
  status,
  payload,
  dedupe_key,
  attempts,
  error,
  result,
  worker_name,
  created_at,
  updated_at,
  started_at,
  lease_expires_at,
  finished_at
"""
    payload = dict(record or {})
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                sql,
                (
                    str(payload.get("id") or "").strip(),
                    str(payload.get("kind") or "").strip(),
                    str(payload.get("source") or "manual").strip() or "manual",
                    str(payload.get("status") or "pending").strip() or "pending",
                    _json_param(payload.get("payload") or {}),
                    str(payload.get("dedupe_key") or "").strip() or None,
                    int(payload.get("attempts") or 0),
                    payload.get("error"),
                    _json_param(payload.get("result")) if isinstance(payload.get("result"), dict) else None,
                    payload.get("worker_name"),
                    payload.get("created_at"),
                    payload.get("updated_at"),
                    payload.get("started_at"),
                    payload.get("lease_expires_at"),
                    payload.get("finished_at"),
                ),
            )
            return _runtime_job_row_to_dict(dict(cur.fetchone())) or {}


def claim_next_runtime_job(worker_name: str, *, started_at: str) -> dict[str, Any] | None:
    sql = """
WITH next_job AS (
  SELECT id
  FROM runtime_job
  WHERE status = 'pending'
     OR (
       status = 'running'
       AND lease_expires_at IS NOT NULL
       AND lease_expires_at <= %s::timestamptz
     )
  ORDER BY
    CASE WHEN status = 'pending' THEN 0 ELSE 1 END ASC,
    CASE
      WHEN status = 'pending' THEN created_at
      ELSE lease_expires_at
    END ASC,
    created_at ASC,
    id ASC
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
UPDATE runtime_job job
SET
  status = 'running',
  worker_name = %s,
  started_at = %s::timestamptz,
  updated_at = %s::timestamptz,
  lease_expires_at = CASE
    WHEN job.kind = 'grade_attempt' THEN %s::timestamptz + INTERVAL '1800 seconds'
    WHEN job.kind IN ('resume_parse', 'git_sync_exams') THEN %s::timestamptz + INTERVAL '600 seconds'
    ELSE %s::timestamptz + INTERVAL '300 seconds'
  END,
  attempts = COALESCE(job.attempts, 0) + 1,
  error = NULL,
  result = NULL,
  finished_at = NULL
FROM next_job
WHERE job.id = next_job.id
RETURNING
  job.id,
  job.kind,
  job.source,
  job.status,
  job.payload,
  job.dedupe_key,
  job.attempts,
  job.error,
  job.result,
  job.worker_name,
  job.created_at,
  job.updated_at,
  job.started_at,
  job.lease_expires_at,
  job.finished_at
"""
    ts = str(started_at or "").strip()
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (ts, str(worker_name or "").strip(), ts, ts, ts, ts, ts))
            row = cur.fetchone()
    return _runtime_job_row_to_dict(dict(row)) if row else None


def update_runtime_job(
    job_id: str,
    *,
    status: str,
    updated_at: str,
    error: str | None = None,
    result: dict[str, Any] | None = None,
    lease_expires_at: str | None = None,
    finished_at: str | None = None,
) -> dict[str, Any] | None:
    sql = """
UPDATE runtime_job
SET
  status = %s,
  error = %s,
  result = %s,
  lease_expires_at = %s::timestamptz,
  finished_at = %s::timestamptz,
  updated_at = %s::timestamptz
WHERE id = %s
RETURNING
  id,
  kind,
  source,
  status,
  payload,
  dedupe_key,
  attempts,
  error,
  result,
  worker_name,
  created_at,
  updated_at,
  started_at,
  lease_expires_at,
  finished_at
"""
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                sql,
                (
                    str(status or "").strip(),
                    error,
                    _json_param(result) if isinstance(result, dict) else None,
                    lease_expires_at,
                    finished_at,
                    str(updated_at or "").strip(),
                    str(job_id or "").strip(),
                ),
            )
            row = cur.fetchone()
    return _runtime_job_row_to_dict(dict(row)) if row else None


def _process_heartbeat_row_to_dict(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    out = dict(row)
    out["updated_at"] = _iso_or_none(out.get("updated_at"))
    try:
        out["pid"] = int(out.get("pid") or 0)
    except Exception:
        out["pid"] = 0
    return out


def upsert_process_heartbeat(record: dict[str, Any]) -> dict[str, Any]:
    sql = """
INSERT INTO process_heartbeat(name, process, pid, status, message, updated_at)
VALUES (%s, %s, %s, %s, %s, %s::timestamptz)
ON CONFLICT (name) DO UPDATE
SET
  process = EXCLUDED.process,
  pid = EXCLUDED.pid,
  status = EXCLUDED.status,
  message = EXCLUDED.message,
  updated_at = EXCLUDED.updated_at
RETURNING name, process, pid, status, message, updated_at
"""
    payload = dict(record or {})
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                sql,
                (
                    str(payload.get("name") or "").strip(),
                    str(payload.get("process") or "").strip(),
                    int(payload.get("pid") or 0),
                    str(payload.get("status") or "running").strip() or "running",
                    str(payload.get("message") or "").strip(),
                    payload.get("updated_at"),
                ),
            )
            return _process_heartbeat_row_to_dict(dict(cur.fetchone())) or {}


def list_process_heartbeats() -> list[dict[str, Any]]:
    sql = """
SELECT name, process, pid, status, message, updated_at
FROM process_heartbeat
ORDER BY name ASC
"""
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            return [_process_heartbeat_row_to_dict(dict(row)) or {} for row in cur.fetchall()]

# 根据id修改候选者姓名和手机号
def update_candidate(candidate_id: int, *, name: str, phone: str, created_at=None) -> None:
    if created_at is None:
        sql = "UPDATE candidate SET name=%s, phone=%s WHERE id=%s"
        params = (name, phone, int(candidate_id))
    else:
        sql = "UPDATE candidate SET name=%s, phone=%s, created_at=%s WHERE id=%s"
        params = (name, phone, created_at, int(candidate_id))
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


# 根据id号删除候选者id
def delete_candidate(candidate_id: int) -> None:
    # Preserve exam history: if quiz_paper exists, perform a "soft delete" by anonymizing
    # the candidate record while keeping its id for FK references.
    sql_check = "SELECT 1 FROM quiz_paper WHERE candidate_id=%s LIMIT 1"
    sql_get_name = "SELECT name FROM candidate WHERE id=%s LIMIT 1"
    sql_hard = "DELETE FROM candidate WHERE id=%s"
    sql_soft = """
 UPDATE candidate
   SET
     deleted_at=NOW(),
     name=%s,
     phone=%s,
     resume_bytes=NULL,
     resume_filename=NULL,
     resume_mime=NULL,
     resume_size=NULL,
     resume_parsed=NULL,
     resume_parsed_at=NULL
 WHERE id=%s
 """
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_check, (int(candidate_id),))
            if cur.fetchone() is not None:
                cur.execute(sql_get_name, (int(candidate_id),))
                row = cur.fetchone()
                keep_name = str((row[0] if row else "") or "").strip()
                if not keep_name:
                    keep_name = f"候选人#{int(candidate_id)}"
                # Use a unique phone placeholder to free the original phone for future registrations.
                placeholder_phone = f"DELETED_{int(candidate_id)}_{int(time.time())}"
                cur.execute(sql_soft, (keep_name, placeholder_phone, int(candidate_id)))
                return
            cur.execute(sql_hard, (int(candidate_id),))


# 通过姓名和电话验证候选者
def verify_candidate(candidate_id: int, *, name: str, phone: str) -> bool:
    sql = "SELECT 1 FROM candidate WHERE id=%s AND name=%s AND phone=%s"
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (int(candidate_id), name, phone))
            return cur.fetchone() is not None


def create_quiz_paper(
    *,
    candidate_id: int,
    phone: str,
    quiz_key: str,
    quiz_version_id: int | None = None,
    token: str,
    source_kind: str = "direct",
    invite_start_date: str | None = None,
    invite_end_date: str | None = None,
    status: str = "invited",
) -> int:
    sql = """
 INSERT INTO quiz_paper(candidate_id, phone, quiz_key, quiz_version_id, token, source_kind, invite_start_date, invite_end_date, status)
 VALUES (%s, %s, %s, %s, %s, %s, %s::date, %s::date, %s::quiz_paper_status)
 RETURNING id
 """
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    int(candidate_id),
                    str(phone or ""),
                    str(quiz_key or ""),
                    (int(quiz_version_id) if quiz_version_id else None),
                    str(token or ""),
                    ("public" if str(source_kind or "").strip().lower() == "public" else "direct"),
                    (str(invite_start_date).strip() if invite_start_date else None),
                    (str(invite_end_date).strip() if invite_end_date else None),
                    str(status or "invited"),
                ),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0


def get_quiz_paper_by_token(token: str) -> dict[str, Any] | None:
    sql = """
 SELECT
    id,
    candidate_id,
    phone,
    quiz_key,
    quiz_version_id,
    token,
    source_kind,
    invite_start_date,
    invite_end_date,
    status,
    entered_at,
    finished_at,
    handled_at,
    handled_by,
    suspected_ai_question_ids,
    score,
    created_at,
    updated_at
 FROM quiz_paper
 WHERE token=%s
 LIMIT 1
 """
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (str(token or ""),))
            row = cur.fetchone()
            return dict(row) if row else None


def delete_quiz_paper_by_token(token: str) -> int:
    sql = "DELETE FROM quiz_paper WHERE token=%s"
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(token or "").strip(),))
            return int(cur.rowcount or 0)


def get_quiz_paper_admin_detail_by_token(token: str) -> dict[str, Any] | None:
    sql = """
 SELECT
    ep.id AS attempt_id,
    ep.candidate_id,
    c.name,
    c.deleted_at AS candidate_deleted_at,
    ep.phone,
    ep.quiz_key,
    ep.quiz_version_id,
    ep.token,
    ep.source_kind,
    ep.invite_start_date,
    ep.invite_end_date,
    ep.status,
    ep.entered_at,
    ep.finished_at,
    ep.handled_at,
    ep.handled_by,
    ep.suspected_ai_question_ids,
    ep.score,
    ep.created_at
 FROM quiz_paper ep
 JOIN candidate c ON c.id = ep.candidate_id
 WHERE ep.token=%s
 LIMIT 1
 """
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (str(token or ""),))
            row = cur.fetchone()
            return dict(row) if row else None


def set_quiz_paper_status(token: str, status: str) -> None:
    sql = "UPDATE quiz_paper SET status=%s::quiz_paper_status, updated_at=NOW() WHERE token=%s"
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(status or ""), str(token or "")))


def set_quiz_paper_handling(token: str, *, handled: bool, handled_by: str = "") -> None:
    sql = """
 UPDATE quiz_paper
 SET
   handled_at = CASE WHEN %s THEN NOW() ELSE NULL END,
   handled_by = CASE WHEN %s THEN %s ELSE NULL END,
   updated_at = NOW()
 WHERE token=%s
 """
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (bool(handled), bool(handled), str(handled_by or "").strip(), str(token or "")))


def set_quiz_paper_suspected_ai_question_ids(token: str, question_ids: list[str]) -> None:
    normalized_ids: list[str] = []
    seen: set[str] = set()
    for value in question_ids:
        qid = str(value or "").strip()
        if not qid or qid in seen:
            continue
        seen.add(qid)
        normalized_ids.append(qid)
    sql = """
 UPDATE quiz_paper
 SET suspected_ai_question_ids=%s::jsonb,
     updated_at=NOW()
 WHERE token=%s
 """
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    psycopg2.extras.Json(normalized_ids, dumps=lambda value: json.dumps(value, ensure_ascii=False)),
                    str(token or "").strip(),
                ),
            )


def set_quiz_paper_entered_at(token: str, entered_at) -> None:
    sql = """
 UPDATE quiz_paper
 SET entered_at=COALESCE(entered_at, %s),
     updated_at=NOW()
 WHERE token=%s
 """
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (entered_at, str(token or "")))


def set_quiz_paper_finished_at(token: str, finished_at) -> None:
    sql = """
 UPDATE quiz_paper
 SET finished_at=COALESCE(finished_at, %s),
     updated_at=NOW()
 WHERE token=%s
 """
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (finished_at, str(token or "")))


def set_quiz_paper_invite_window_if_missing(
    token: str,
    *,
    invite_start_date: str | None = None,
    invite_end_date: str | None = None,
) -> None:
    """
    Backfill invite window dates onto quiz_paper without overwriting existing values.
    """
    sql = """
 UPDATE quiz_paper
 SET
   invite_start_date = COALESCE(invite_start_date, %s::date),
   invite_end_date = COALESCE(invite_end_date, %s::date),
   updated_at = NOW()
 WHERE token=%s
 """
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    (str(invite_start_date).strip() if invite_start_date else None),
                    (str(invite_end_date).strip() if invite_end_date else None),
                    str(token or ""),
                ),
            )


def update_quiz_paper_result(
    token: str,
    *,
    status: str,
    score: int | None,
    entered_at=None,
    finished_at=None,
) -> None:
    sql = """
 UPDATE quiz_paper
 SET
   status=%s::quiz_paper_status,
   score=%s,
   entered_at=COALESCE(entered_at, %s),
   finished_at=%s,
   updated_at=NOW()
 WHERE token=%s
 """
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    str(status or ""),
                    (int(score) if score is not None else None),
                    entered_at,
                    finished_at,
                    str(token or ""),
                ),
            )


def backfill_quiz_paper_version_id(quiz_key: str, quiz_version_id: int) -> int:
    sql = """
UPDATE quiz_paper
SET
  quiz_version_id=%s,
  updated_at=NOW()
WHERE quiz_key=%s AND quiz_version_id IS NULL
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (int(quiz_version_id), str(quiz_key or "").strip()))
            return int(cur.rowcount or 0)


def _append_quiz_paper_filters(
    where: list[str],
    params: list[Any],
    *,
    query: str | None = None,
    quiz_key: str | None = None,
    status_filter: str | None = None,
    handled_filter: str | None = None,
    invite_start_from: str | None = None,
    invite_start_to: str | None = None,
    invite_end_from: str | None = None,
    invite_end_to: str | None = None,
) -> None:
    q = str(query or "").strip()
    if q:
        ql = f"%{q}%"
        where.append("(c.name ILIKE %s OR ep.phone LIKE %s OR ep.quiz_key ILIKE %s OR ep.token ILIKE %s)")
        params.extend([ql, ql, ql, ql])
    quiz = str(quiz_key or "").strip()
    if quiz:
        where.append("ep.quiz_key = %s")
        params.append(quiz)
    status_key = str(status_filter or "").strip().lower()
    if status_key == "expired":
        where.append(
            "ep.status IN ('invited'::quiz_paper_status, 'verified'::quiz_paper_status)"
            " AND ep.entered_at IS NULL"
            " AND ep.invite_end_date < CURRENT_DATE"
        )
    elif status_key in {"invited", "verified", "in_quiz", "grading", "finished"}:
        where.append("ep.status = %s::quiz_paper_status")
        params.append(status_key)
        if status_key in {"invited", "verified"}:
            where.append("NOT (ep.entered_at IS NULL AND ep.invite_end_date < CURRENT_DATE)")
    handled_key = str(handled_filter or "").strip().lower()
    if handled_key == "unhandled":
        where.append("ep.status = 'finished'::quiz_paper_status")
        where.append("ep.handled_at IS NULL")
    elif handled_key == "handled":
        where.append("ep.status = 'finished'::quiz_paper_status")
        where.append("ep.handled_at IS NOT NULL")
    if invite_start_from:
        where.append("ep.invite_start_date >= %s::date")
        params.append(str(invite_start_from).strip())
    if invite_start_to:
        where.append("ep.invite_start_date <= %s::date")
        params.append(str(invite_start_to).strip())
    if invite_end_from:
        where.append("ep.invite_end_date >= %s::date")
        params.append(str(invite_end_from).strip())
    if invite_end_to:
        where.append("ep.invite_end_date <= %s::date")
        params.append(str(invite_end_to).strip())


def list_quiz_papers(
    *,
    query: str | None = None,
    quiz_key: str | None = None,
    status_filter: str | None = None,
    handled_filter: str | None = None,
    invite_start_from: str | None = None,
    invite_start_to: str | None = None,
    invite_end_from: str | None = None,
    invite_end_to: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    sql = """
  SELECT
     ep.id AS attempt_id,
     ep.candidate_id,
     c.name,
     c.deleted_at AS candidate_deleted_at,
     ep.phone,
     ep.quiz_key,
     ep.quiz_version_id,
     ep.token,
     ep.source_kind,
     ep.invite_start_date,
     ep.invite_end_date,
     ep.status,
     ep.entered_at,
     ep.finished_at,
     ep.handled_at,
     ep.handled_by,
     ep.suspected_ai_question_ids,
     ep.score,
     ep.created_at
  FROM quiz_paper ep
  JOIN candidate c ON c.id = ep.candidate_id
  """
    params: list[Any] = []
    where: list[str] = []
    _append_quiz_paper_filters(
        where,
        params,
        query=query,
        quiz_key=quiz_key,
        status_filter=status_filter,
        handled_filter=handled_filter,
        invite_start_from=invite_start_from,
        invite_start_to=invite_start_to,
        invite_end_from=invite_end_from,
        invite_end_to=invite_end_to,
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += "\n ORDER BY ep.id DESC\n"
    if limit is not None:
        sql += " LIMIT %s"
        params.append(int(limit))
    if offset:
        sql += " OFFSET %s"
        params.append(int(offset))
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            return [dict(r) for r in cur.fetchall()]


def get_candidate_name_from_logs(candidate_id: int) -> str:
    """
    Best-effort recovery of original candidate name from historical logs.
    Prefer the latest non-empty meta.name for this candidate.
    """
    cid = int(candidate_id or 0)
    if cid <= 0:
        return ""
    sql = """
SELECT COALESCE(sl.meta->>'name','') AS name
FROM system_log sl
WHERE sl.candidate_id=%s
  AND sl.meta IS NOT NULL
  AND COALESCE(sl.meta->>'name','') <> ''
ORDER BY sl.id DESC
LIMIT 50
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (cid,))
            rows = cur.fetchall()
    for row in rows:
        try:
            nm = str(row[0] or "").strip()
        except Exception:
            nm = ""
        if not nm:
            continue
        low = nm.lower()
        if nm in {"已删除", "候选人"}:
            continue
        if low.startswith("deleted_") or low.startswith("deletion_"):
            continue
        return nm
    return ""


def create_system_log(
    *,
    actor: str,
    event_type: str,
    candidate_id: int | None = None,
    quiz_key: str | None = None,
    token: str | None = None,
    llm_prompt_tokens: int | None = None,
    llm_completion_tokens: int | None = None,
    llm_total_tokens: int | None = None,
    started_at: datetime | str | None = None,
    finished_at: datetime | str | None = None,
    duration_seconds: int | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    meta: dict[str, Any] | None = None,
) -> int:
    started_dt = _log_time_or_none(started_at)
    finished_dt = _log_time_or_none(finished_at)
    duration_value = _log_duration_seconds_or_none(duration_seconds, started_dt, finished_dt)
    sql = """
 INSERT INTO system_log(
   actor, event_type, candidate_id, quiz_key, token,
   llm_prompt_tokens, llm_completion_tokens, llm_total_tokens,
   started_at, finished_at, duration_seconds,
   ip, user_agent, meta
 )
 VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
 RETURNING id
 """
    meta_param = None
    if meta is not None:
        meta_param = psycopg2.extras.Json(meta, dumps=lambda x: json.dumps(x, ensure_ascii=False))
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    str(actor or ""),
                    str(event_type or ""),
                    (int(candidate_id) if candidate_id is not None else None),
                    (str(quiz_key).strip() if quiz_key else None),
                    (str(token).strip() if token else None),
                    (int(llm_prompt_tokens) if llm_prompt_tokens is not None else None),
                    (int(llm_completion_tokens) if llm_completion_tokens is not None else None),
                    (int(llm_total_tokens) if llm_total_tokens is not None else None),
                    started_dt,
                    finished_dt,
                    duration_value,
                    (str(ip).strip() if ip else None),
                    (str(user_agent).strip() if user_agent else None),
                    meta_param,
                ),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0


def backfill_system_log_llm_totals_from_meta() -> int:
    """
    Best-effort backfill:

    For historical rows written before we started persisting llm_total_tokens explicitly,
    copy meta.llm_total_tokens_sum into the dedicated llm_total_tokens column.

    This enables consistent UI display without changing existing meta payloads.
    """
    sql = """
 UPDATE system_log
 SET llm_total_tokens = (meta->>'llm_total_tokens_sum')::int
 WHERE (llm_total_tokens IS NULL OR llm_total_tokens <= 0)
   AND meta IS NOT NULL
   AND (meta ? 'llm_total_tokens_sum')
   AND (meta->>'llm_total_tokens_sum') ~ '^[0-9]+$'
   AND (meta->>'llm_total_tokens_sum')::int > 0
    """
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return int(cur.rowcount or 0)


def backfill_system_log_llm_totals_zero_for_ai_generate_missing() -> int:
    """
    Historical fallback:

    Some old ai-generated exam logs never persisted token usage in either
    llm_total_tokens or meta.llm_total_tokens_sum. For those rows, backfill
    llm_total_tokens to 0 and annotate meta for traceability.
    """
    sql = """
UPDATE system_log
SET
  llm_total_tokens = 0,
  meta = jsonb_set(
    COALESCE(meta, '{}'::jsonb),
    '{llm_total_tokens_backfill}',
    '"missing_history_default_0"'::jsonb,
    true
  )
WHERE event_type = 'exam.upload'
  AND COALESCE(meta->>'source', '') = 'ai.generate'
  AND (llm_total_tokens IS NULL OR llm_total_tokens < 0)
  AND (
    meta IS NULL
    OR NOT (meta ? 'llm_total_tokens_sum')
    OR COALESCE(NULLIF(meta->>'llm_total_tokens_sum', ''), '0') !~ '^[0-9]+$'
    OR (meta->>'llm_total_tokens_sum')::int <= 0
  )
    """
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return int(cur.rowcount or 0)


def _system_log_where_clause(
    *,
    query: str | None = None,
    event_type: str | None = None,
    at_from: datetime | None = None,
    at_to: datetime | None = None,
    table_alias: str = "",
    business_only: bool = False,
) -> tuple[str, list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    a = str(table_alias or "").strip()
    if a and not a.endswith("."):
        a = a + "."

    if business_only:
        # Keep only business-relevant logs:
        # - candidate.* ops
        # - exam CRUD ops
        # - assignment/invite + answering timeline
        # - llm.usage only when it can be linked to candidate/quiz/token context
        llm_linked = (
            f"({a}token IS NOT NULL AND {a}token <> '') OR "
            f"{a}candidate_id IS NOT NULL OR "
            f"({a}quiz_key IS NOT NULL AND {a}quiz_key <> '')"
        )
        where.append(
            "("
            f"{a}event_type LIKE 'candidate.%%' OR "
            f"{a}event_type IN ('exam.upload','exam.update','exam.delete','exam.read',"
            f"'assignment.create','assignment.verify','exam.enter','exam.finish') OR "
            f"({a}event_type='llm.usage' AND ({llm_linked}))"
            ")"
        )

    t = str(event_type or "").strip()
    if t:
        where.append(f"{a}event_type=%s")
        params.append(t)

    if at_from is not None:
        where.append(f"{a}at >= %s")
        params.append(at_from)
    if at_to is not None:
        where.append(f"{a}at <= %s")
        params.append(at_to)

    q = str(query or "").strip()
    if q:
        ql = f"%{q}%"
        where.append(
            "("
            f"{a}actor ILIKE %s OR {a}event_type ILIKE %s OR {a}quiz_key ILIKE %s OR {a}token ILIKE %s OR "
            f"CAST({a}candidate_id AS TEXT) ILIKE %s OR CAST({a}meta AS TEXT) ILIKE %s"
            ")"
        )
        params.extend([ql, ql, ql, ql, ql, ql])

    if not where:
        return "", []
    return " WHERE " + " AND ".join(where), params


def count_system_logs(
    *,
    query: str | None = None,
    event_type: str | None = None,
    at_from: datetime | None = None,
    at_to: datetime | None = None,
    business_only: bool = False,
) -> int:
    sql = "SELECT COUNT(*) FROM system_log"
    where_sql, params = _system_log_where_clause(
        query=query,
        event_type=event_type,
        at_from=at_from,
        at_to=at_to,
        business_only=business_only,
    )
    if where_sql:
        sql += where_sql
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return int(cur.fetchone()[0])


def list_system_logs(
    *,
    query: str | None = None,
    event_type: str | None = None,
    at_from: datetime | None = None,
    at_to: datetime | None = None,
    limit: int | None = None,
    offset: int = 0,
    business_only: bool = False,
) -> list[dict[str, Any]]:
    sql = """
 SELECT
   sl.id,
   sl.at,
   sl.actor,
   sl.event_type,
   sl.candidate_id,
   c.name AS candidate_name,
   c.phone AS candidate_phone,
   sl.quiz_key,
   sl.token,
   sl.llm_prompt_tokens,
   sl.llm_completion_tokens,
   sl.llm_total_tokens,
   sl.started_at,
   sl.finished_at,
   sl.duration_seconds,
   sl.ip,
   sl.user_agent,
   sl.meta
 FROM system_log sl
 LEFT JOIN candidate c ON c.id = sl.candidate_id
 """
    where_sql, params = _system_log_where_clause(
        query=query,
        event_type=event_type,
        at_from=at_from,
        at_to=at_to,
        table_alias="sl",
        business_only=business_only,
    )
    if where_sql:
        sql += where_sql
    sql += "\n ORDER BY id DESC\n"
    if limit is not None:
        sql += " LIMIT %s"
        params.append(int(limit))
    if offset:
        sql += " OFFSET %s"
        params.append(int(offset))
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            return [dict(r) for r in cur.fetchall()]


def count_operation_logs() -> int:
    """
    Count business operation logs (exclude llm.usage rows).

    Operations shown in UI:
      - candidate.* (CRUD and related admin actions)
      - exam.* (CRUD + public invite toggle)
      - assignment timeline: assignment.create / assignment.verify / exam.enter / exam.finish
      - system: system.alert
    """
    sql = """
 SELECT COUNT(*)
 FROM system_log sl
 WHERE (
     sl.event_type LIKE 'candidate.%%' OR
     sl.event_type LIKE 'exam.%%' OR
     sl.event_type = 'system.alert' OR
     sl.event_type IN ('assignment.create','assignment.verify','exam.enter','exam.finish')
   ) AND sl.event_type <> 'llm.usage'
   """
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return int(cur.fetchone()[0])


def list_operation_logs(*, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """
    List business operation logs (exclude llm.usage rows), newest first.
    """
    sql = """
 SELECT
    sl.id,
    sl.at,
    sl.actor,
    sl.event_type,
    sl.candidate_id,
    c.name AS candidate_name,
    c.phone AS candidate_phone,
    sl.quiz_key,
    sl.token,
    sl.llm_prompt_tokens,
    sl.llm_completion_tokens,
    sl.llm_total_tokens,
    sl.started_at,
    sl.finished_at,
    sl.duration_seconds,
    sl.meta
  FROM system_log sl
  LEFT JOIN candidate c ON c.id = sl.candidate_id
  WHERE (
    sl.event_type LIKE 'candidate.%%' OR
    sl.event_type LIKE 'exam.%%' OR
    sl.event_type = 'system.alert' OR
    sl.event_type IN ('assignment.create','assignment.verify','exam.enter','exam.finish')
  ) AND sl.event_type <> 'llm.usage'
  ORDER BY sl.at DESC, sl.id DESC
  LIMIT %s
  OFFSET %s
  """
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (int(limit), int(offset)))
            return [dict(r) for r in cur.fetchall()]


def list_operation_logs_after_id(*, after_id: int, limit: int = 50) -> list[dict[str, Any]]:
    """
    List business operation logs with id > after_id (exclude llm.usage rows), newest first.

    Used by the admin dashboard to poll incremental updates without a full page reload.
    """
    try:
        aid = int(after_id or 0)
    except Exception:
        aid = 0
    aid = max(0, aid)
    lim = max(1, min(100, int(limit or 50)))
    sql = """
 SELECT
    sl.id,
    sl.at,
    sl.actor,
    sl.event_type,
    sl.candidate_id,
    c.name AS candidate_name,
    c.phone AS candidate_phone,
    sl.quiz_key,
    sl.token,
    sl.llm_prompt_tokens,
    sl.llm_completion_tokens,
    sl.llm_total_tokens,
    sl.started_at,
    sl.finished_at,
    sl.duration_seconds,
    sl.meta
  FROM system_log sl
  LEFT JOIN candidate c ON c.id = sl.candidate_id
  WHERE sl.id > %s AND (
    sl.event_type LIKE 'candidate.%%' OR
    sl.event_type LIKE 'exam.%%' OR
    sl.event_type = 'system.alert' OR
    sl.event_type IN ('assignment.create','assignment.verify','exam.enter','exam.finish')
  ) AND sl.event_type <> 'llm.usage'
  ORDER BY sl.at DESC, sl.id DESC
  LIMIT %s
 """
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (aid, lim))
            return [dict(r) for r in cur.fetchall()]


def list_operation_daily_counts(
    *,
    tz_offset_seconds: int,
    at_from: datetime | None = None,
    at_to: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Aggregate operation log density by day (local day buckets using a numeric UTC offset in seconds).

    Notes:
    - We intentionally use seconds instead of PostgreSQL's numeric time zone strings (e.g. '+08:00'),
      because PostgreSQL interprets numeric zones using POSIX sign conventions (reversed vs the common ISO form).
    - Keep event scope aligned with legend categories, including sms.* as part of "system".
    """
    sql = """
 SELECT (((sl.at AT TIME ZONE 'UTC') + (%s * INTERVAL '1 second'))::date) AS day, COUNT(*) AS cnt
 FROM system_log sl
  WHERE (
    sl.event_type LIKE 'candidate.%%' OR
    sl.event_type LIKE 'exam.%%' OR
    sl.event_type LIKE 'sms.%%' OR
    sl.event_type = 'system.alert' OR
    sl.event_type IN ('assignment.create','assignment.verify','exam.enter','exam.finish')
   ) AND sl.event_type <> 'llm.usage'
  """
    params: list[Any] = [int(tz_offset_seconds or 0)]
    if at_from is not None:
        sql += "\n AND sl.at >= %s"
        params.append(at_from)
    if at_to is not None:
        sql += "\n AND sl.at <= %s"
        params.append(at_to)
    sql += "\n GROUP BY day\n ORDER BY day ASC\n"
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            return [dict(r) for r in cur.fetchall()]


def list_operation_daily_counts_by_category(
    *,
    tz_offset_seconds: int,
    at_from: datetime | None = None,
    at_to: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Aggregate operation logs by local day and UI category.

    Categories align with the admin logs page legend:
      - candidate
      - exam
      - grading
      - assignment
      - system
    """
    sql = """
 SELECT
   (((sl.at AT TIME ZONE 'UTC') + (%s * INTERVAL '1 second'))::date) AS day,
   SUM(CASE WHEN sl.event_type LIKE 'candidate.%%' THEN 1 ELSE 0 END) AS candidate_cnt,
   SUM(CASE WHEN sl.event_type LIKE 'exam.%%' AND sl.event_type NOT IN ('exam.grade','exam.enter','exam.finish') THEN 1 ELSE 0 END) AS exam_cnt,
   SUM(CASE WHEN sl.event_type = 'exam.grade' THEN 1 ELSE 0 END) AS grading_cnt,
   SUM(CASE WHEN sl.event_type IN ('assignment.create','assignment.verify','exam.enter','exam.finish') THEN 1 ELSE 0 END) AS assignment_cnt,
   SUM(CASE WHEN sl.event_type = 'system.alert' OR sl.event_type LIKE 'sms.%%' THEN 1 ELSE 0 END) AS system_cnt
 FROM system_log sl
 WHERE (
   sl.event_type LIKE 'candidate.%%' OR
   sl.event_type LIKE 'exam.%%' OR
   sl.event_type LIKE 'sms.%%' OR
   sl.event_type = 'system.alert' OR
   sl.event_type IN ('assignment.create','assignment.verify','exam.enter','exam.finish')
 ) AND sl.event_type <> 'llm.usage'
 """
    params: list[Any] = [int(tz_offset_seconds or 0)]
    if at_from is not None:
        sql += "\n AND sl.at >= %s"
        params.append(at_from)
    if at_to is not None:
        sql += "\n AND sl.at <= %s"
        params.append(at_to)
    sql += "\n GROUP BY day\n ORDER BY day ASC\n"
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            return [dict(r) for r in cur.fetchall()]


def list_system_status_daily_metrics(
    *,
    tz_offset_seconds: int,
    at_from: datetime | None = None,
    at_to: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Aggregate "system status" metrics by day (local day buckets using a numeric UTC offset in seconds).

    Metrics:
      - exams_new: exam.upload count
      - invites_new: assignment.create count
      - candidates_new: candidate.create count
      - llm_tokens: SUM(llm_total_tokens) across rows (best-effort)
      - sms_calls: (tracked outside system_log)
    """
    sql = """
 SELECT
   (((sl.at AT TIME ZONE 'UTC') + (%s * INTERVAL '1 second'))::date) AS day,
   SUM(CASE WHEN sl.event_type = 'exam.upload' THEN 1 ELSE 0 END) AS exams_new,
   SUM(CASE WHEN sl.event_type = 'assignment.create' THEN 1 ELSE 0 END) AS invites_new,
   SUM(CASE WHEN sl.event_type = 'candidate.create' THEN 1 ELSE 0 END) AS candidates_new,
   SUM(COALESCE(sl.llm_total_tokens, 0)) AS llm_tokens,
   0 AS sms_calls
 FROM system_log sl
 WHERE sl.event_type <> 'llm.usage'
"""
    params: list[Any] = [int(tz_offset_seconds or 0)]
    if at_from is not None:
        sql += "\n AND sl.at >= %s"
        params.append(at_from)
    if at_to is not None:
        sql += "\n AND sl.at <= %s"
        params.append(at_to)
    sql += "\n GROUP BY day\n ORDER BY day ASC\n"
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            return [dict(r) for r in cur.fetchall()]


def has_system_alert(*, day: str, kind: str, level: str) -> bool:
    """
    Best-effort dedupe guard for system status alerts.
    Checks existing system_log rows where event_type='system.alert' and meta contains day/kind/level.
    """
    d = str(day or "").strip()
    k = str(kind or "").strip()
    lv = str(level or "").strip()
    if not d or not k or not lv:
        return False
    sql = """
 SELECT 1
 FROM system_log sl
 WHERE sl.event_type = 'system.alert'
   AND COALESCE(sl.meta->>'day','') = %s
   AND COALESCE(sl.meta->>'kind','') = %s
   AND COALESCE(sl.meta->>'level','') = %s
 LIMIT 1
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (d, k, lv))
            return bool(cur.fetchone())


def touch_system_alert(*, day: str, kind: str, level: str, used: int, limit: int, ratio: float) -> None:
    """
    Backward-compatible helper to record a system.alert snapshot.
    Always appends a new row (never updates existing rows), so repeated warnings
    on the same day are preserved chronologically.
    """
    d = str(day or "").strip()
    k = str(kind or "").strip()
    lv = str(level or "").strip()
    if not d or not k or not lv:
        return
    try:
        u = int(used or 0)
    except Exception:
        u = 0
    try:
        l = int(limit or 0)
    except Exception:
        l = 0
    try:
        r = float(ratio or 0.0)
    except Exception:
        r = 0.0

    create_system_log(
        actor="system",
        event_type="system.alert",
        meta={
            "day": d,
            "kind": k,
            "level": lv,
            "used": u,
            "limit": l,
            "ratio": r,
        },
    )


def cleanup_duplicate_system_alert_logs(*, day: str | None = None, kind: str | None = None) -> int:
    """
    Delete duplicated system.alert rows and keep only the earliest row in each group.

    Group key:
      - meta.day
      - meta.kind
      - meta.limit
      - meta.level

    This matches the current alert policy:
      - one alert when threshold is exceeded
      - threshold value changes can produce a new alert row
    """
    d = str(day or "").strip()[:10]
    k = str(kind or "").strip()
    params: list[Any] = []
    filters: list[str] = [
        "sl.event_type = 'system.alert'",
        "COALESCE(sl.meta->>'day','') <> ''",
        "COALESCE(sl.meta->>'kind','') <> ''",
    ]
    if d:
        filters.append("COALESCE(sl.meta->>'day','') = %s")
        params.append(d)
    if k:
        filters.append("COALESCE(sl.meta->>'kind','') = %s")
        params.append(k)
    where_sql = " AND ".join(filters)
    sql = f"""
WITH ranked AS (
  SELECT
    sl.id,
    ROW_NUMBER() OVER (
      PARTITION BY
        COALESCE(sl.meta->>'day',''),
        COALESCE(sl.meta->>'kind',''),
        COALESCE(sl.meta->>'limit',''),
        COALESCE(sl.meta->>'level','')
      ORDER BY sl.at ASC, sl.id ASC
    ) AS rn
  FROM system_log sl
  WHERE {where_sql}
)
DELETE FROM system_log sl
USING ranked r
WHERE sl.id = r.id
  AND r.rn > 1
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return int(cur.rowcount or 0)


def list_system_alert_limits(*, day: str, kind: str) -> list[int]:
    d = str(day or "").strip()[:10]
    k = str(kind or "").strip()
    if not d or not k:
        return []
    sql = """
SELECT DISTINCT COALESCE(NULLIF(sl.meta->>'limit',''),'0')::int AS lim
FROM system_log sl
WHERE sl.event_type = 'system.alert'
  AND COALESCE(sl.meta->>'day','') = %s
  AND COALESCE(sl.meta->>'kind','') = %s
  AND COALESCE(NULLIF(sl.meta->>'limit',''),'0')::int > 0
ORDER BY lim ASC
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (d, k))
            out: list[int] = []
            for row in cur.fetchall():
                try:
                    out.append(int(row[0]))
                except Exception:
                    continue
            return out


def list_system_alert_levels(*, day: str, kind: str, limit: int) -> set[str]:
    d = str(day or "").strip()[:10]
    k = str(kind or "").strip()
    try:
        l = int(limit or 0)
    except Exception:
        l = 0
    if not d or not k or l <= 0:
        return set()
    sql = """
SELECT DISTINCT COALESCE(sl.meta->>'level','') AS lv
FROM system_log sl
WHERE sl.event_type = 'system.alert'
  AND COALESCE(sl.meta->>'day','') = %s
  AND COALESCE(sl.meta->>'kind','') = %s
  AND COALESCE(NULLIF(sl.meta->>'limit',''),'0')::int = %s
"""
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (d, k, l))
            out: set[str] = set()
            for row in cur.fetchall():
                try:
                    lv = str(row[0] or "").strip().lower()
                except Exception:
                    lv = ""
                if lv:
                    out.add(lv)
            return out


def estimate_sms_calls_for_day(*, day: str, tz_offset_seconds: int) -> int:
    """
    Best-effort estimation of "sms verification usage" for a given local day bucket.

    We no longer persist per-call sms.send logs to the DB. For historical backfill and UX consistency,
    we derive usage from:
      1) legacy system_log rows where event_type='sms.send' (count)
      2) assignment.verify meta.sms_send_count (sum; only recorded when verification succeeds)

    To avoid double counting for old data sets that may have both, we take the maximum of the two.
    """
    d = str(day or "").strip()[:10]
    try:
        off = int(tz_offset_seconds or 0)
    except Exception:
        off = 0
    if not d:
        return 0
    sql = """
 SELECT
   GREATEST(
     COALESCE(SUM(CASE WHEN sl.event_type = 'sms.send' THEN 1 ELSE 0 END), 0),
     COALESCE(SUM(CASE WHEN sl.event_type = 'assignment.verify' THEN COALESCE(NULLIF(sl.meta->>'sms_send_count','')::int, 0) ELSE 0 END), 0)
   ) AS sms_calls
 FROM system_log sl
 WHERE (((sl.at AT TIME ZONE 'UTC') + (%s * INTERVAL '1 second'))::date) = (%s::date)
   AND sl.event_type <> 'llm.usage'
"""
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (off, d))
            row = cur.fetchone() or {}
            try:
                return max(0, int(row.get("sms_calls") or 0))
            except Exception:
                return 0


def list_estimated_sms_calls_daily_counts(
    *,
    tz_offset_seconds: int,
    at_from: datetime | None = None,
    at_to: datetime | None = None,
) -> dict[str, int]:
    """
    Best-effort estimation of sms verification usage grouped by local day bucket.

    See estimate_sms_calls_for_day() for rationale. This is the batched version used for
    system status range queries to avoid N-per-day DB round trips.
    """
    sql = """
 SELECT
   (((sl.at AT TIME ZONE 'UTC') + (%s * INTERVAL '1 second'))::date) AS day,
   GREATEST(
     COALESCE(SUM(CASE WHEN sl.event_type = 'sms.send' THEN 1 ELSE 0 END), 0),
     COALESCE(SUM(CASE WHEN sl.event_type = 'assignment.verify' THEN COALESCE(NULLIF(sl.meta->>'sms_send_count','')::int, 0) ELSE 0 END), 0)
   ) AS sms_calls
 FROM system_log sl
 WHERE sl.event_type IN ('sms.send','assignment.verify')
   AND sl.event_type <> 'llm.usage'
"""
    params: list[Any] = [int(tz_offset_seconds or 0)]
    if at_from is not None:
        sql += "\n AND sl.at >= %s"
        params.append(at_from)
    if at_to is not None:
        sql += "\n AND sl.at <= %s"
        params.append(at_to)
    sql += "\n GROUP BY day\n ORDER BY day ASC\n"

    out: dict[str, int] = {}
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            for r in cur.fetchall() or []:
                day = str(r.get("day") or "")[:10]
                if not day:
                    continue
                try:
                    out[day] = max(0, int(r.get("sms_calls") or 0))
                except Exception:
                    out[day] = 0
    return out


def count_operation_logs_by_category() -> dict[str, int]:
    """
    Count operation logs grouped into UI categories across the whole DB.

     Categories:
       - candidate: candidate.* ops
       - exam: exam.* ops (excluding grading and assignment timeline)
       - grading: exam.grade
       - assignment: assignment.create / assignment.verify / exam.enter / exam.finish
       - system: system.alert + sms.* (ops not covered above)
    """
    sql = """
 SELECT
    SUM(CASE WHEN sl.event_type LIKE 'candidate.%%' THEN 1 ELSE 0 END) AS candidate_cnt,
    SUM(CASE WHEN sl.event_type LIKE 'exam.%%' AND sl.event_type NOT IN ('exam.grade','exam.enter','exam.finish') THEN 1 ELSE 0 END) AS exam_cnt,
    SUM(CASE WHEN sl.event_type = 'exam.grade' THEN 1 ELSE 0 END) AS grading_cnt,
    SUM(CASE WHEN sl.event_type IN ('assignment.create','assignment.verify','exam.enter','exam.finish') THEN 1 ELSE 0 END) AS assignment_cnt,
    SUM(CASE WHEN sl.event_type = 'system.alert' OR sl.event_type LIKE 'sms.%%' THEN 1 ELSE 0 END) AS system_cnt
  FROM system_log sl
  WHERE (
    sl.event_type LIKE 'candidate.%%' OR
    sl.event_type LIKE 'exam.%%' OR
    sl.event_type LIKE 'sms.%%' OR
    sl.event_type = 'system.alert' OR
    sl.event_type IN ('assignment.create','assignment.verify','exam.enter','exam.finish')
  ) AND sl.event_type <> 'llm.usage'
   """
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            row = cur.fetchone() or {}
            out: dict[str, int] = {"candidate": 0, "exam": 0, "grading": 0, "assignment": 0, "system": 0}
            try:
                out["candidate"] = int(row.get("candidate_cnt") or 0)
            except Exception:
                out["candidate"] = 0
            try:
                out["exam"] = int(row.get("exam_cnt") or 0)
            except Exception:
                out["exam"] = 0
            try:
                out["grading"] = int(row.get("grading_cnt") or 0)
            except Exception:
                out["grading"] = 0
            try:
                out["assignment"] = int(row.get("assignment_cnt") or 0)
            except Exception:
                out["assignment"] = 0
            try:
                out["system"] = int(row.get("system_cnt") or 0)
            except Exception:
                out["system"] = 0
            return out



def list_system_log_type_counts(
    *,
    query: str | None = None,
    event_type: str | None = None,
    at_from: datetime | None = None,
    at_to: datetime | None = None,
    business_only: bool = False,
) -> list[dict[str, Any]]:
    sql = """
 SELECT event_type, COUNT(*) AS cnt
 FROM system_log
 """
    where_sql, params = _system_log_where_clause(
        query=query,
        event_type=event_type,
        at_from=at_from,
        at_to=at_to,
        business_only=business_only,
    )
    if where_sql:
        sql += where_sql
    sql += "\n GROUP BY event_type\n ORDER BY cnt DESC, event_type ASC\n"
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            return [dict(r) for r in cur.fetchall()]


def list_system_log_category_counts(
    *,
    query: str | None = None,
    event_type: str | None = None,
    at_from: datetime | None = None,
    at_to: datetime | None = None,
    business_only: bool = False,
) -> list[dict[str, Any]]:
    """
    Aggregate logs into higher-level categories for UI legend.

    Categories:
      - candidate: candidate.* (and llm.usage tied to candidate)
      - exam: exam CRUD (and llm.usage tied to exam)
      - assignment: invitations + answering timeline (and llm.usage tied to token)
      - ui: page views
      - system: fallback/unknown
    """
    sql = """
 SELECT
   CASE
     WHEN sl.event_type LIKE 'candidate.%%' THEN 'candidate'
     WHEN sl.event_type IN ('exam.upload','exam.update','exam.delete','exam.read') THEN 'exam'
     WHEN sl.event_type IN ('assignment.create','assignment.verify','exam.enter','exam.finish') THEN 'assignment'
     WHEN sl.event_type = 'llm.usage' THEN
       CASE
         WHEN sl.token IS NOT NULL AND sl.token <> '' THEN 'assignment'
         WHEN sl.candidate_id IS NOT NULL THEN 'candidate'
         WHEN sl.quiz_key IS NOT NULL AND sl.quiz_key <> '' THEN 'exam'
         ELSE 'system'
       END
     WHEN sl.event_type IN ('ui.view','admin.view') THEN 'ui'
     ELSE 'system'
   END AS category,
   COUNT(*) AS cnt
 FROM system_log sl
 """
    where_sql, params = _system_log_where_clause(
        query=query,
        event_type=event_type,
        at_from=at_from,
        at_to=at_to,
        table_alias="sl",
        business_only=business_only,
    )
    if where_sql:
        sql += where_sql
    sql += "\n GROUP BY category\n ORDER BY cnt DESC, category ASC\n"
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            return [dict(r) for r in cur.fetchall()]


def list_system_log_daily_counts(
    *,
    query: str | None = None,
    event_type: str | None = None,
    at_from: datetime | None = None,
    at_to: datetime | None = None,
    business_only: bool = False,
) -> list[dict[str, Any]]:
    """
    Aggregate log density by day (UTC day buckets).
    """
    sql = """
 SELECT (DATE_TRUNC('day', at))::date AS day, COUNT(*) AS cnt
 FROM system_log
 """
    where_sql, params = _system_log_where_clause(
        query=query,
        event_type=event_type,
        at_from=at_from,
        at_to=at_to,
        business_only=business_only,
    )
    if where_sql:
        sql += where_sql
    sql += "\n GROUP BY day\n ORDER BY day ASC\n"
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            return [dict(r) for r in cur.fetchall()]


def count_quiz_papers(
    *,
    query: str | None = None,
    quiz_key: str | None = None,
    status_filter: str | None = None,
    handled_filter: str | None = None,
    invite_start_from: str | None = None,
    invite_start_to: str | None = None,
    invite_end_from: str | None = None,
    invite_end_to: str | None = None,
) -> int:
    sql = """
 SELECT COUNT(*)
 FROM quiz_paper ep
 JOIN candidate c ON c.id = ep.candidate_id
 """
    params: list[Any] = []
    where: list[str] = []
    _append_quiz_paper_filters(
        where,
        params,
        query=query,
        quiz_key=quiz_key,
        status_filter=status_filter,
        handled_filter=handled_filter,
        invite_start_from=invite_start_from,
        invite_start_to=invite_start_to,
        invite_end_from=invite_end_from,
        invite_end_to=invite_end_to,
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return int(cur.fetchone()[0])


def count_unhandled_finished_quiz_papers(
    *,
    query: str | None = None,
    quiz_key: str | None = None,
    status_filter: str | None = None,
    handled_filter: str | None = None,
    invite_start_from: str | None = None,
    invite_start_to: str | None = None,
    invite_end_from: str | None = None,
    invite_end_to: str | None = None,
) -> int:
    sql = """
 SELECT COUNT(*)
 FROM quiz_paper ep
 JOIN candidate c ON c.id = ep.candidate_id
 """
    params: list[Any] = []
    where: list[str] = ["ep.status = 'finished'::quiz_paper_status", "ep.handled_at IS NULL"]
    _append_quiz_paper_filters(
        where,
        params,
        query=query,
        quiz_key=quiz_key,
        status_filter=status_filter,
        handled_filter=handled_filter,
        invite_start_from=invite_start_from,
        invite_start_to=invite_start_to,
        invite_end_from=invite_end_from,
        invite_end_to=invite_end_to,
    )
    sql += " WHERE " + " AND ".join(where)
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return int(cur.fetchone()[0])


def count_quiz_respondents_by_key() -> dict[str, int]:
    """按测验聚合已实际开始或已完成答题的去重候选人数。"""
    sql = """
SELECT quiz_key, COUNT(DISTINCT candidate_id)::int AS respondent_count
FROM quiz_paper
WHERE entered_at IS NOT NULL OR finished_at IS NOT NULL
GROUP BY quiz_key
"""
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            rows = cur.fetchall()
    return {
        str(row.get("quiz_key") or "").strip(): int(row.get("respondent_count") or 0)
        for row in rows or []
        if str(row.get("quiz_key") or "").strip()
    }


def _quiz_analytics_where_clause(
    *,
    quiz_key: str,
    current_version_id: int | None = None,
    selected_version_id: int | None = None,
    version_scope: str = "all",
    start_at=None,
    end_at=None,
) -> tuple[str, list[Any]]:
    where: list[str] = ["ep.quiz_key = %s"]
    params: list[Any] = [str(quiz_key or "").strip()]

    scope = str(version_scope or "all").strip().lower()
    if scope == "current":
        resolved_version_id = int(selected_version_id or 0) or int(current_version_id or 0)
        if resolved_version_id > 0:
            where.append("ep.quiz_version_id = %s")
            params.append(resolved_version_id)
        else:
            where.append("1 = 0")

    if start_at is not None and end_at is not None:
        where.append(
            "("
            "(ep.status = 'finished'::quiz_paper_status AND ep.finished_at IS NOT NULL AND ep.finished_at >= %s AND ep.finished_at <= %s)"
            " OR "
            "(ep.status IN ('in_quiz'::quiz_paper_status, 'grading'::quiz_paper_status) AND ep.entered_at IS NOT NULL AND ep.entered_at >= %s AND ep.entered_at <= %s)"
            ")"
        )
        params.extend([start_at, end_at, start_at, end_at])

    return " WHERE " + " AND ".join(where), params


def count_quiz_paper_analytics_rows(
    *,
    quiz_key: str,
    current_version_id: int | None = None,
    selected_version_id: int | None = None,
    version_scope: str = "all",
    start_at=None,
    end_at=None,
) -> int:
    sql = """
 SELECT COUNT(*)
 FROM quiz_paper ep
 """
    where_sql, params = _quiz_analytics_where_clause(
        quiz_key=quiz_key,
        current_version_id=current_version_id,
        selected_version_id=selected_version_id,
        version_scope=version_scope,
        start_at=start_at,
        end_at=end_at,
    )
    sql += where_sql
    with conn_scope() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return int(cur.fetchone()[0])


def list_quiz_paper_analytics_rows(
    *,
    quiz_key: str,
    current_version_id: int | None = None,
    selected_version_id: int | None = None,
    version_scope: str = "all",
    start_at=None,
    end_at=None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    sql = """
 SELECT
    ep.id AS attempt_id,
    ep.candidate_id,
    c.name,
    c.deleted_at AS candidate_deleted_at,
    ep.quiz_key,
    ep.quiz_version_id,
    ep.token,
    ep.source_kind,
    ep.status,
    ep.entered_at,
    ep.finished_at,
    ep.score,
    ep.created_at,
    qa.archive::text AS archive
 FROM quiz_paper ep
 JOIN candidate c ON c.id = ep.candidate_id
 LEFT JOIN quiz_archive qa ON qa.token = ep.token
 """
    where_sql, params = _quiz_analytics_where_clause(
        quiz_key=quiz_key,
        current_version_id=current_version_id,
        selected_version_id=selected_version_id,
        version_scope=version_scope,
        start_at=start_at,
        end_at=end_at,
    )
    sql += where_sql
    sql += """
 ORDER BY
   COALESCE(
     CASE
       WHEN ep.status = 'finished'::quiz_paper_status THEN ep.finished_at
       ELSE ep.entered_at
     END,
     ep.created_at
   ) DESC,
   ep.id DESC
 """
    if limit is not None:
        sql += " LIMIT %s"
        params.append(int(limit))
    if offset:
        sql += " OFFSET %s"
        params.append(int(offset))
    with conn_scope() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for row in rows or []:
        item = dict(row)
        item["archive"] = _json_load(item.get("archive")) or {}
        out.append(item)
    return out
