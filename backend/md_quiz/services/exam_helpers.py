from __future__ import annotations

import copy
import mimetypes
from typing import Callable

from backend.md_quiz.services.quiz_metadata import (
    QUIZ_SCHEMA_VERSION,
    apply_quiz_metadata,
    build_quiz_metadata,
    compute_answer_time_total_seconds,
)
from backend.md_quiz.services.support_deps import *
from backend.md_quiz.services.validation_helpers import *

def _invite_window_state(assignment: dict, *, now: datetime | None = None) -> tuple[str, date | None, date | None]:
    """
    Returns (state, start_date, end_date):
    - state: ok | not_started | expired
    - start/end are local dates (day granularity)

    Expiration is judged only if the candidate has not started the exam yet.
    """
    a = assignment or {}
    inv = a.get("invite_window") or {}
    if not isinstance(inv, dict):
        inv = {}
    sd = _parse_date_ymd(str(inv.get("start_date") or ""))
    ed = _parse_date_ymd(str(inv.get("end_date") or ""))

    if now is None:
        now = datetime.now().astimezone()
    else:
        now = now.astimezone()
    tz = now.tzinfo

    timing = a.get("timing") or {}
    if isinstance(timing, dict) and str(timing.get("start_at") or "").strip():
        return "ok", sd, ed

    if sd is not None:
        start_at = datetime.combine(sd, dt_time.min, tzinfo=tz)
        if now < start_at:
            return "not_started", sd, ed

    if ed is not None:
        end_at = datetime.combine(ed, dt_time.max, tzinfo=tz)
        if now > end_at:
            return "expired", sd, ed

    return "ok", sd, ed


_MD_IMAGE_RE = re.compile(r"!\[[^\]]*]\((?P<path>[^)]+)\)")
_HTML_IMG_SRC_RE = re.compile(
    r"""<img\b(?P<before>[^>]*?)\bsrc\s*=\s*(?P<quote>["']?)(?P<path>[^"'>\s]+)(?P=quote)(?P<after>[^>]*)>""",
    re.IGNORECASE,
)
_DISPLAY_ESCAPE_RE = re.compile(r"\\(?P<char>[_{}\[\]#*])")
_STANDALONE_MD_IMAGE_RE = re.compile(r"^\s*!\[[^\]]*]\((?P<path>[^)]+)\)\s*$")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
_FENCED_BLOCK_RE = re.compile(r"^\s*(```|~~~)")
_HORIZONTAL_RULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_TRAILING_HARD_BREAK_RE = re.compile(r"(?:\\|[ \t]{2,})\s*$")
_FILENAME_UNSAFE_RE = re.compile(r'[\\\\/:*?"<>|]+')
_PUBLIC_INVITE_GUARD = threading.Lock()
_MARKDOWN_EXTENSIONS = [
    "markdown.extensions.fenced_code",
    "markdown.extensions.footnotes",
    "markdown.extensions.attr_list",
    "markdown.extensions.def_list",
    "markdown.extensions.tables",
    "markdown.extensions.abbr",
    "markdown.extensions.md_in_html",
    "markdown.extensions.sane_lists",
]


def get_public_invite_config(quiz_key: str) -> dict[str, object]:
    exam = get_quiz_definition(str(quiz_key or "").strip()) or {}
    if not exam:
        return {"enabled": False, "token": ""}
    enabled = bool(exam.get("public_invite_enabled"))
    token = str(exam.get("public_invite_token") or "").strip()
    if str(exam.get("status") or "").strip() != "active" or int(exam.get("current_version_id") or 0) <= 0:
        enabled = False
    return {"enabled": enabled, "token": token}


def compute_quiz_time_limit_seconds(spec: dict[str, Any]) -> int:
    return int(compute_answer_time_total_seconds(list((spec or {}).get("questions") or [])))

def _hash_token_base64url(seed: str, *, length: int = 10) -> str:
    """
    Deterministic, URL-safe token: base64url(sha256(seed)) truncated to `length`.
    """
    raw = hashlib.sha256(seed.encode("utf-8", errors="ignore")).digest()
    b64 = base64.urlsafe_b64encode(raw).decode("ascii", errors="ignore").rstrip("=")
    return b64[:length]


def _compute_public_invite_token_for_exam(*, quiz_key: str, created_at: str, title: str, length: int = 10) -> str:
    """
    Generate a stable public invite token for an exam based on exam metadata.

    Seed includes: created_at, quiz_key (id), title. Token is base64url hash (10 chars).
    Collision-safe: appends a suffix and re-hashes if token already taken by another exam.
    """
    ek = str(quiz_key or "").strip()
    ca = str(created_at or "").strip()
    tt = str(title or "").strip()
    if not ek or not ca:
        raise ValueError("missing quiz_key/created_at")

    base_seed = f"{ek}\n{tt}\n{ca}"
    for n in range(0, 50):
        seed = base_seed if n == 0 else f"{base_seed}\n#{n}"
        t = _hash_token_base64url(seed, length=length)
        if not t:
            continue
        bound = get_quiz_key_by_public_invite_token(t)
        if not bound or bound == ek:
            return t
    raise RuntimeError("Failed to allocate a collision-free public invite token")


def set_public_invite_enabled(quiz_key: str, enabled: bool) -> dict[str, object]:
    ek = str(quiz_key or "").strip()
    if not ek:
        return {"enabled": False, "token": ""}
    exam = get_quiz_definition(ek)
    if not exam:
        return {"enabled": False, "token": ""}
    if enabled:
        if str(exam.get("status") or "").strip() != "active":
            return {"enabled": False, "token": ""}
        if int(exam.get("current_version_id") or 0) <= 0:
            return {"enabled": False, "token": ""}

    with _PUBLIC_INVITE_GUARD:
        token0 = str(exam.get("public_invite_token") or "").strip()
        created_at0 = str(exam.get("created_at") or "").strip()
        title0 = str(exam.get("title") or "").strip()
        if enabled:
            token = _compute_public_invite_token_for_exam(quiz_key=ek, created_at=created_at0, title=title0, length=10)
        else:
            token = token0 if token0 else None
        try:
            set_exam_public_invite(ek, enabled=bool(enabled), token=(token or None))
        except Exception:
            return {"enabled": False, "token": ""}
    return {"enabled": bool(enabled), "token": token}

def _resolve_public_invite_quiz_key(public_token: str) -> str:
    t = str(public_token or "").strip()
    if not t:
        return ""
    return get_quiz_key_by_public_invite_token(t)


def _protect_math_for_markdown(raw: str) -> tuple[str, list[tuple[str, str]]]:
    """
    Python-Markdown treats trailing backslashes as hard line breaks and can
    consume TeX row separators like `\\\\` at end-of-line (e.g. inside `cases`).
    To keep MathJax/TeX intact, temporarily replace math segments with tokens
    before markdown processing, then restore them in the generated HTML.
    """

    text = str(raw or "")
    replacements: list[tuple[str, str]] = []
    out: list[str] = []

    def is_escaped(pos: int) -> bool:
        # Consider a '$' escaped if preceded by an odd number of backslashes.
        bs = 0
        j = pos - 1
        while j >= 0 and text[j] == "\\":
            bs += 1
            j -= 1
        return (bs % 2) == 1

    i = 0
    while i < len(text):
        if text.startswith("$$", i) and not is_escaped(i):
            j = i + 2
            while True:
                k = text.find("$$", j)
                if k < 0:
                    break
                if not is_escaped(k):
                    seg = text[i : k + 2]
                    token = f"@@MATH{len(replacements)}@@"
                    replacements.append((token, html.escape(seg, quote=False)))
                    out.append(token)
                    i = k + 2
                    break
                j = k + 1
            else:
                # Unreachable; keep structure explicit.
                pass
            if i != j and out and out[-1].startswith("@@MATH"):
                continue

        if text.startswith("\\[", i):
            k = text.find("\\]", i + 2)
            if k >= 0:
                seg = text[i : k + 2]
                token = f"@@MATH{len(replacements)}@@"
                replacements.append((token, html.escape(seg, quote=False)))
                out.append(token)
                i = k + 2
                continue

        if text.startswith("\\(", i):
            k = text.find("\\)", i + 2)
            if k >= 0:
                seg = text[i : k + 2]
                token = f"@@MATH{len(replacements)}@@"
                replacements.append((token, html.escape(seg, quote=False)))
                out.append(token)
                i = k + 2
                continue

        if text[i] == "$" and not is_escaped(i) and not text.startswith("$$", i):
            j = i + 1
            while True:
                k = text.find("$", j)
                if k < 0:
                    break
                if not is_escaped(k):
                    seg = text[i : k + 1]
                    token = f"@@MATH{len(replacements)}@@"
                    replacements.append((token, html.escape(seg, quote=False)))
                    out.append(token)
                    i = k + 1
                    break
                j = k + 1
            if out and out[-1].startswith("@@MATH"):
                continue

        out.append(text[i])
        i += 1

    return "".join(out), replacements


def _strip_display_escapes(raw: str) -> str:
    return _DISPLAY_ESCAPE_RE.sub(lambda match: str(match.group("char") or ""), str(raw or ""))


def _split_display_blocks(raw: str) -> list[list[str]]:
    text = str(raw or "").strip()
    if not text:
        return []

    blocks: list[list[str]] = []
    current: list[str] = []
    in_fenced_block = False
    fence_marker = ""

    def flush_current() -> None:
        nonlocal current
        if current:
            blocks.append(current)
            current = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if in_fenced_block:
            current.append(line)
            if stripped.startswith(fence_marker):
                in_fenced_block = False
                fence_marker = ""
            continue

        if not stripped:
            flush_current()
            continue

        if _FENCED_BLOCK_RE.match(stripped):
            flush_current()
            current.append(line)
            in_fenced_block = True
            fence_marker = stripped[:3]
            continue

        is_list_item = bool(_LIST_ITEM_RE.match(stripped))
        prev_stripped = current[-1].strip() if current else ""
        if is_list_item and prev_stripped and not _LIST_ITEM_RE.match(prev_stripped):
            flush_current()

        current.append(line)

    flush_current()
    return blocks


def _is_simple_display_block(block: list[str]) -> bool:
    if not block:
        return False

    for line in block:
        stripped = line.strip()
        if not stripped:
            return False
        if stripped.startswith(("$$", "\\[", "```", "~~~", "#", ">", "|")):
            return False
        if _HORIZONTAL_RULE_RE.fullmatch(stripped):
            return False
        if _LIST_ITEM_RE.match(stripped):
            return False
        if _STANDALONE_MD_IMAGE_RE.fullmatch(stripped):
            return False
        if stripped.startswith("<") and stripped.endswith(">"):
            return False
        if line.startswith(("    ", "\t")):
            return False
    return True


def _is_inline_math_only_block(block: str) -> bool:
    text = str(block or "").strip()
    if not text:
        return False
    if text.startswith("$$") and text.endswith("$$"):
        return False
    if text.startswith("$") and text.endswith("$") and text.count("$") >= 2:
        return True
    if text.startswith("\\(") and text.endswith("\\)"):
        return True
    return False


def _normalize_simple_display_block(block: list[str]) -> str:
    parts: list[str] = []
    for line in block:
        compact = _TRAILING_HARD_BREAK_RE.sub("", line).strip()
        if compact:
            parts.append(compact)
    return " ".join(parts)


def _normalize_display_markdown(raw: str) -> str:
    blocks = _split_display_blocks(raw)
    if not blocks:
        return ""

    out: list[str] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        if not _is_simple_display_block(block):
            out.append("\n".join(block).strip())
            i += 1
            continue

        run: list[str] = []
        has_inline_math_only = False
        j = i
        while j < len(blocks) and _is_simple_display_block(blocks[j]):
            normalized = _normalize_simple_display_block(blocks[j])
            if normalized:
                run.append(normalized)
                has_inline_math_only = has_inline_math_only or _is_inline_math_only_block(normalized)
            j += 1

        if has_inline_math_only:
            out.append(" ".join(run))
        else:
            out.extend(run)
        i = j

    return "\n\n".join(out)


def _prepare_display_markdown(markdown_text: str) -> tuple[str, list[tuple[str, str]]]:
    normalized = _normalize_display_markdown(markdown_text)
    protected, math_repls = _protect_math_for_markdown(normalized)
    return _strip_display_escapes(protected), math_repls


def _safe_relpath(raw: str) -> str:
    p = (raw or "").strip().strip('"').strip("'")
    p = p.split("#", 1)[0].strip()
    p = p.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def _is_local_asset_path(p: str) -> bool:
    if not p:
        return False
    lower = p.lower()
    if lower.startswith(("http://", "https://", "data:", "mailto:")):
        return False
    return True


def _collect_md_assets(markdown_text: str) -> set[str]:
    assets: set[str] = set()
    for m in _MD_IMAGE_RE.finditer(markdown_text or ""):
        p = _safe_relpath(m.group("path"))
        if _is_local_asset_path(p):
            assets.add(p)
    for m in _HTML_IMG_SRC_RE.finditer(markdown_text or ""):
        p = _safe_relpath(m.group("path"))
        if _is_local_asset_path(p):
            assets.add(p)
    return assets


def _render_markdown_html(markdown_text: str) -> str:
    text = str(markdown_text or "").strip()
    if not text:
        return ""

    protected, math_repls = _prepare_display_markdown(text)
    rendered = mdlib.markdown(
        protected,
        extensions=_MARKDOWN_EXTENSIONS,
        output_format="html5",
    )
    for token, math_html in math_repls:
        rendered = rendered.replace(token, math_html)
    return rendered


def build_render_ready_question(question: dict[str, Any], *, include_rubric_html: bool = False) -> dict[str, Any]:
    q2 = copy.deepcopy(question or {})
    q2["stem_html"] = _render_markdown_html(str(q2.get("stem_md") or ""))
    options_out = []
    for option in q2.get("options") or []:
        if not isinstance(option, dict):
            options_out.append(option)
            continue
        opt = dict(option)
        opt["text_html"] = _render_markdown_html(str(opt.get("text") or ""))
        options_out.append(opt)
    if isinstance(q2.get("options"), list):
        q2["options"] = options_out
    if include_rubric_html:
        q2["rubric_html"] = _render_markdown_html(str(q2.get("rubric") or ""))
    return q2


def build_render_ready_public_spec(
    public_spec: dict[str, Any],
    *,
    include_rubric_html: bool = False,
) -> dict[str, Any]:
    out = copy.deepcopy(public_spec or {})
    questions_out = []
    for q in out.get("questions") or []:
        if not isinstance(q, dict):
            continue
        questions_out.append(
            build_render_ready_question(q, include_rubric_html=include_rubric_html)
        )
    out["questions"] = questions_out
    return out


def _rewrite_text_assets_with_builder(text: str, make_url: Callable[[str], str]) -> str:
    out = str(text or "")
    for p in _collect_md_assets(out):
        out = out.replace(f"({p})", f"({make_url(p)})")

    def _replace_html_img(match: re.Match[str]) -> str:
        rel = _safe_relpath(match.group("path"))
        if not rel or not _is_local_asset_path(rel):
            return match.group(0)
        before = match.group("before") or ""
        quote = match.group("quote") or '"'
        after = match.group("after") or ""
        return f'<img{before}src={quote}{make_url(rel)}{quote}{after}>'

    return _HTML_IMG_SRC_RE.sub(_replace_html_img, out)


def _rewrite_quiz_asset_paths_for_version(version_id: int, spec: dict, public_spec: dict) -> None:
    def make_url(relpath: str) -> str:
        return _version_asset_url(version_id, relpath)

    for doc in (spec, public_spec):
        for key in ("welcome_image", "end_image"):
            raw = str(doc.get(key) or "").strip()
            if raw and _is_local_asset_path(raw):
                doc[key] = make_url(raw)
        for q in (doc.get("questions") or []):
            q["stem_md"] = _rewrite_text_assets_with_builder(str(q.get("stem_md") or ""), make_url)
            media = str(q.get("media") or "").strip()
            if media and _is_local_asset_path(media):
                q["media"] = make_url(media)
            if q.get("rubric") is not None:
                q["rubric"] = _rewrite_text_assets_with_builder(str(q.get("rubric") or ""), make_url)


def _quiz_payload_has_blank_option_text(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    for question in (payload.get("questions") or []):
        if not isinstance(question, dict):
            continue
        qtype = str(question.get("type") or "").strip().lower()
        if qtype not in {"single", "multiple"}:
            continue
        for option in (question.get("options") or []):
            if not isinstance(option, dict):
                continue
            key = str(option.get("key") or "").strip()
            text = str(option.get("text") or "").strip()
            if key and not text:
                return True
    return False


def _repair_quiz_snapshot_payloads(snapshot: dict[str, Any]) -> dict[str, Any]:
    raw = dict(snapshot or {})
    spec = raw.get("spec") if isinstance(raw.get("spec"), dict) else {}
    public_spec = raw.get("public_spec") if isinstance(raw.get("public_spec"), dict) else {}
    if not (_quiz_payload_has_blank_option_text(spec) or _quiz_payload_has_blank_option_text(public_spec)):
        return raw

    source_md = str(raw.get("source_md") or "")
    if not source_md.strip():
        return raw

    try:
        repaired_spec, repaired_public = parse_qml_markdown(source_md)
        repaired_spec = apply_quiz_metadata(repaired_spec, default_schema_version=QUIZ_SCHEMA_VERSION)
        repaired_public = apply_quiz_metadata(repaired_public, default_schema_version=QUIZ_SCHEMA_VERSION)
    except Exception:
        return raw

    version_id = int(raw.get("quiz_version_id") or raw.get("id") or raw.get("current_version_id") or 0)
    quiz_key = str(raw.get("quiz_key") or "").strip()
    if version_id > 0:
        _rewrite_quiz_asset_paths_for_version(version_id, repaired_spec, repaired_public)
    elif quiz_key:
        _rewrite_quiz_asset_paths(quiz_key, repaired_spec, repaired_public)

    raw["spec"] = repaired_spec
    raw["public_spec"] = repaired_public
    return raw


def _asset_url(quiz_key: str, relpath: str) -> str:
    return f"/quizzes/{quiz_key}/assets/{_safe_relpath(relpath)}"


def _version_asset_url(version_id: int, relpath: str) -> str:
    return f"/quizzes/versions/{int(version_id)}/assets/{_safe_relpath(relpath)}"


def get_quiz_version_snapshot(version_id: int) -> dict[str, Any] | None:
    try:
        version = get_quiz_version(int(version_id))
    except Exception:
        version = None
    if not version:
        return None
    out = dict(version)
    out["quiz_version_id"] = int(version.get("id") or 0)
    out["quiz_key"] = str(version.get("quiz_key") or "").strip()
    return _repair_quiz_snapshot_payloads(out)


def resolve_quiz_version_id_for_new_assignment(quiz_key: str) -> int | None:
    exam = get_quiz_definition(str(quiz_key or "").strip()) or {}
    if not exam:
        return None
    if str(exam.get("status") or "").strip() != "active":
        return None
    try:
        version_id = int(exam.get("current_version_id") or 0)
    except Exception:
        version_id = 0
    return version_id or None


def get_exam_snapshot_for_assignment(assignment: dict[str, Any]) -> dict[str, Any] | None:
    a = assignment or {}
    try:
        version_id = int(a.get("quiz_version_id") or 0)
    except Exception:
        version_id = 0
    if version_id > 0:
        snap = get_quiz_version_snapshot(version_id)
        if snap:
            return snap
    quiz_key = str(a.get("quiz_key") or "").strip()
    if not quiz_key:
        return None
    exam = get_quiz_definition(quiz_key) or {}
    if not exam:
        return None
    current_version_id = int(exam.get("current_version_id") or 0)
    if current_version_id > 0:
        snap = get_quiz_version_snapshot(current_version_id)
        if snap:
            return snap
    return _repair_quiz_snapshot_payloads(exam)


def _resolve_quiz_asset_payload_by_version(version_id: int, relpath: str) -> tuple[bytes, str] | None:
    rp = _safe_relpath(relpath)
    if not rp:
        return None
    if any(part == ".." for part in Path(rp).parts):
        return None
    try:
        return get_quiz_version_asset(int(version_id), rp)
    except Exception:
        return None


def _resolve_quiz_asset_payload(quiz_key: str, relpath: str) -> tuple[bytes, str] | None:
    rp = _safe_relpath(relpath)
    if not rp:
        return None
    if any(part == ".." for part in Path(rp).parts):
        return None
    exam = get_quiz_definition(str(quiz_key or "").strip()) or {}
    try:
        version_id = int(exam.get("current_version_id") or 0)
    except Exception:
        version_id = 0
    if version_id > 0:
        payload = _resolve_quiz_asset_payload_by_version(version_id, rp)
        if payload:
            return payload
    try:
        return get_quiz_asset(str(quiz_key or "").strip(), rp)
    except Exception:
        return None


# 测验资源处理：将 Markdown 中的本地资源路径统一重写为受控访问 URL。
def _rewrite_quiz_asset_paths(quiz_key: str, spec: dict, public_spec: dict) -> None:
    def _rewrite_text_assets(text: str) -> str:
        out = str(text or "")
        for p in _collect_md_assets(out):
            asset_url = _asset_url(quiz_key, p)
            out = out.replace(f"({p})", f"({asset_url})")

        def _replace_html_img(match: re.Match[str]) -> str:
            rel = _safe_relpath(match.group("path"))
            if not rel or not _is_local_asset_path(rel):
                return match.group(0)
            before = match.group("before") or ""
            quote = match.group("quote") or '"'
            after = match.group("after") or ""
            return f'<img{before}src={quote}{_asset_url(quiz_key, rel)}{quote}{after}>'

        return _HTML_IMG_SRC_RE.sub(_replace_html_img, out)

    for k in ("welcome_image", "end_image"):
        v = str(spec.get(k) or "").strip()
        if v:
            spec[k] = _asset_url(quiz_key, v) if _is_local_asset_path(v) else v
        v2 = str(public_spec.get(k) or "").strip()
        if v2:
            public_spec[k] = _asset_url(quiz_key, v2) if _is_local_asset_path(v2) else v2

    for q in (spec.get("questions") or []):
        q["stem_md"] = _rewrite_text_assets(str(q.get("stem_md") or ""))
        if q.get("rubric") is not None:
            q["rubric"] = _rewrite_text_assets(str(q.get("rubric") or ""))
    for q in (public_spec.get("questions") or []):
        q["stem_md"] = _rewrite_text_assets(str(q.get("stem_md") or ""))
        if q.get("rubric") is not None:
            q["rubric"] = _rewrite_text_assets(str(q.get("rubric") or ""))


# 首次写入测验：解析 Markdown -> 落盘 source/spec/public -> 同步资源文件。
def _write_exam_to_storage(
    exam_text: str,
    *,
    assets: dict[str, bytes] | None = None,
    ensure_unique_key: bool = False,
) -> str:
    raise RuntimeError("测验上传/生成入口已下线，请改用 Git 仓库同步")


# 覆写已有测验目录（用于编辑保存）。
def _rewrite_exam_in_dir(quiz_key: str, exam_text: str) -> None:
    raise RuntimeError("测验在线编辑入口已下线，请改用外部 Git 仓库")


def _migrate_assignment_quiz_key(old_quiz_key: str, new_quiz_key: str) -> int:
    """quiz_key 变更时，迁移 assignment 记录中的关联键。"""
    try:
        return int(rename_assignment_quiz_key(old_quiz_key, new_quiz_key) or 0)
    except Exception:
        logger.exception("Failed to migrate assignment quiz_key: %s -> %s", old_quiz_key, new_quiz_key)
        return 0


# quiz_key 变更时，迁移历史归档文件名与归档内部 quiz_key。
def _migrate_archives_quiz_key(old_quiz_key: str, new_quiz_key: str) -> int:
    try:
        return int(rename_quiz_archives_quiz_key(old_quiz_key, new_quiz_key) or 0)
    except Exception:
        logger.exception("Failed to migrate archives quiz_key: %s -> %s", old_quiz_key, new_quiz_key)
        return 0


# 管理端更新测验：必要时先改目录/关联键，再重写 spec/public。
def _admin_update_exam_from_source(old_quiz_key: str, new_source_md: str) -> str:
    raise RuntimeError("测验在线编辑入口已下线，请改用外部 Git 仓库")


def _list_exams():
    out = []
    for row in list_quiz_definitions():
        spec = row.get("spec") or {}
        metadata = build_quiz_metadata(spec)
        updated_at = row.get("last_sync_at") or row.get("updated_at") or row.get("created_at")
        mtime = 0.0
        try:
            if updated_at:
                mtime = float(updated_at.timestamp())
        except Exception:
            mtime = 0.0
        out.append(
            {
                "quiz_key": str(row.get("quiz_key") or "").strip(),
                "title": spec.get("title", ""),
                "description": str(spec.get("description") or "").strip(),
                "count": int(metadata["question_count"]),
                "question_count": int(metadata["question_count"]),
                "question_counts": dict(metadata["question_counts"]),
                "estimated_duration_minutes": int(metadata["estimated_duration_minutes"]),
                "tags": list(metadata["tags"]),
                "schema_version": metadata["schema_version"],
                "format": str(metadata["format"] or "").strip(),
                "trait": dict(metadata["trait"]),
                "status": str(row.get("status") or "").strip() or "active",
                "current_version_id": int(row.get("current_version_id") or 0),
                "current_version_no": int(row.get("current_version_no") or 0),
                "source_path": str(row.get("source_path") or "").strip(),
                "last_sync_error": str(row.get("last_sync_error") or ""),
                "updated_at": updated_at,
                "_mtime": mtime,
            }
        )
    # Assign an incremental id by upload/parse order (oldest -> newest),
    # then sort by id desc (newest first).
    out.sort(key=lambda x: x.get("_mtime", 0))
    for idx, item in enumerate(out, start=1):
        item["id"] = idx
    out.sort(key=lambda x: x.get("id", 0), reverse=True)
    return out


def _quiz_key_from_sort_id(exam_id: int) -> str | None:
    try:
        exam_id = int(exam_id)
    except Exception:
        return None
    if exam_id <= 0:
        return None
    for e in _list_exams():
        try:
            if int(e.get("id") or 0) == exam_id:
                v = str(e.get("quiz_key") or "").strip()
                return v or None
        except Exception:
            continue
    return None


def _sort_id_from_quiz_key(quiz_key: str) -> int | None:
    k = str(quiz_key or "").strip()
    if not k:
        return None
    for e in _list_exams():
        if str(e.get("quiz_key") or "") == k:
            try:
                v = int(e.get("id") or 0)
            except Exception:
                v = 0
            return v or None
    return None


__all__ = [name for name in globals() if not name.startswith("__")]
