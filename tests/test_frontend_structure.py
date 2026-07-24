from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _fragments_from_mapping(source: str, prefix: str) -> list[str]:
    pattern = re.compile(rf'"/static/{re.escape(prefix)}/([^"\n]+\.html)"')
    return pattern.findall(source)


def test_admin_route_fragments_exist() -> None:
    source = (ROOT / "static" / "admin" / "modules" / "router.js").read_text(encoding="utf-8")
    names = _fragments_from_mapping(source, "admin/pages")

    assert names
    for name in names:
        assert (ROOT / "static" / "admin" / "pages" / name).exists(), name


def test_public_view_fragments_exist() -> None:
    source = (ROOT / "static" / "public" / "modules" / "view-loader.js").read_text(encoding="utf-8")
    names = _fragments_from_mapping(source, "public/views")

    assert names
    for name in names:
        assert (ROOT / "static" / "public" / "views" / name).exists(), name


def test_css_build_script_produces_bundles() -> None:
    subprocess.run(
        ["node", "static/scripts/build-admin-css.cjs"],
        cwd=ROOT,
        check=True,
    )

    assert (ROOT / "static" / "admin.css").exists()
    assert (ROOT / "static" / "public.css").exists()


def test_admin_candidates_page_uses_resume_job_polling() -> None:
    source = (ROOT / "static" / "admin" / "modules" / "pages" / "candidates.js").read_text(encoding="utf-8")

    assert "/api/admin/candidates/resume/upload-job" in source
    assert "/api/admin/job-descriptions/options" in source
    assert 'form.append("job_description_id"' in source
    assert "/resume/reparse-job" in source
    assert "/api/admin/jobs/" in source
    assert "scheduleCandidateResumeUploadPolling" in source
    assert "scheduleCandidateResumeReparsePolling" in source


def test_admin_assignments_page_exposes_pagination_controls() -> None:
    source = (ROOT / "static" / "admin" / "pages" / "assignments.html").read_text(encoding="utf-8")

    assert "首页" in source
    assert "上一页" in source
    assert "下一页" in source
    assert "末页" in source
    assert "filters.assignments.status" in source
    assert "filters.assignments.quiz_key" in source
    assert "quizOptionItems()" in source
    assert source.index("请选择候选人") < source.index("选择候选人后自动带出")
    assert "assignmentQuizSelected(item)" in source
    assert "toggleAssignmentQuiz(item)" in source
    assert "assignment-selected-quiz-" in source
    assert 'aria-label="移除测验"' in source
    assert "setAssignmentHandledFilter" in source
    assert "resetAssignmentFilters" in source


def test_admin_assignments_module_uses_page_query_param() -> None:
    source = (ROOT / "static" / "admin" / "modules" / "pages" / "assignments.js").read_text(encoding="utf-8")

    assert 'query.set("page"' in source
    assert 'query.set("status"' in source
    assert 'query.set("handled"' in source
    assert 'query.set("quiz_key"' in source
    assert "scheduleAssignmentsReloadFromFirstPage" in source
    assert "default_quiz_key" in source
    assert "default_quiz_keys" in source
    assert "selectedAssignmentQuizzes()" in source
    assert "quiz_keys: quizKeys" in source
    assert "loadAssignmentCandidateDefaultQuizKeys" in source
    assert "/api/admin/candidates/${candidateId}" in source
    assert "target === \"quiz\" && (this.assignmentForm.quiz_key" not in source


def test_admin_assignment_suspected_ai_controls_are_present() -> None:
    attempt_source = (ROOT / "static" / "admin" / "pages" / "attempt-detail.html").read_text(encoding="utf-8")
    assignments_source = (ROOT / "static" / "admin" / "pages" / "assignments.html").read_text(encoding="utf-8")
    module_source = (ROOT / "static" / "admin" / "modules" / "pages" / "assignments.js").read_text(encoding="utf-8")

    assert "疑似AI-" in attempt_source
    assert "toggleAttemptSuspectedAi(question)" in attempt_source
    assert "attemptQuestionIsSuspectedAi(question)" in attempt_source
    assert "attemptDetail.quiz_paper?.suspected_ai_question_count || 0" in attempt_source
    assert "疑似AI-" in assignments_source
    assert "assignmentSuspectedAiQuestionCount(item)" in assignments_source
    assert 'x-show="assignmentHasCompletedGrading(item) && assignmentSuspectedAiQuestionCount(item) > 0"' in assignments_source
    assert "sm:flex-row sm:flex-wrap" in assignments_source
    assert "whitespace-nowrap text-lg" in assignments_source
    assert 'class="truncate whitespace-nowrap text-lg' not in assignments_source
    assert "sm:flex-none sm:shrink-0" in assignments_source
    assert "sm:ml-auto sm:shrink-0 sm:flex-nowrap" in assignments_source
    assert "<div\n                        x-show=\"assignmentHasCompletedGrading(item) && assignmentSuspectedAiQuestionCount(item) > 0\"\n                        class=\"mt-3\"\n                      >\n                        <span class=\"assignment-badge border-rose-200" in assignments_source
    assert "/suspected-ai-question" in module_source
    assert "attemptSuspectedAiButtonClass(question)" in module_source
    assert "attempt-suspected-ai-toggle" in module_source
    assert "assignmentHasCompletedGrading(item)" in module_source
    assert 'return this.assignmentStatusValue(item) === "finished";' in module_source
    assert "syncAttemptSuspectedAiQuestionState(updatedItem)" in module_source
    assert "this.applyAssignmentItemUpdate(updatedItem, { updateAttemptDetail: false });" in module_source
    assert "quizPaper.suspected_ai_question_ids = questionIds;" in module_source
    assert "quizPaper.suspected_ai_question_count = count;" in module_source
    sync_start = module_source.index("syncAttemptSuspectedAiQuestionState(updatedItem) {")
    sync_end = module_source.index("    syncStatus()", sync_start)
    assert "this.attemptDetail = {" not in module_source[sync_start:sync_end]
    assert "const detailChanged = JSON.stringify(this.attemptDetail) !== JSON.stringify(nextDetail);" in module_source
    assert "if (detailChanged) {\n        this.attemptDetail = nextDetail;" in module_source


def test_admin_logs_page_supports_time_range_display() -> None:
    source = (ROOT / "static" / "admin" / "pages" / "logs.html").read_text(encoding="utf-8")

    assert "item.has_time_range" in source
    assert "item.started_at_display" in source
    assert "item.finished_at_display" in source
    assert "item.duration_display" in source
    assert "item.at_display" in source


def test_admin_candidates_page_exposes_pagination_controls() -> None:
    source = (ROOT / "static" / "admin" / "pages" / "candidates.html").read_text(encoding="utf-8")
    index_source = (ROOT / "static" / "admin" / "index.html").read_text(encoding="utf-8")
    css_source = (ROOT / "static" / "assets" / "css" / "admin" / "pages.css").read_text(encoding="utf-8")

    assert "admin-body--candidates" in index_source
    assert "admin-candidates-page" in source
    assert "admin-candidate-list" in source
    assert "admin-candidate-items" in source
    assert "admin-candidate-create" in source
    assert "candidatesHavePagination" in source
    assert "candidateForm.job_description_id" in source
    assert "candidateResumeUploadForm.job_description_id" in source
    assert 'type="date"' in source
    assert "filters.candidates.created_from" in source
    assert "filters.candidates.created_to" in source
    assert 'style="flex: 1 1 420px"' in source
    assert 'style="flex: 0 0 256px; min-width: 256px"' in source
    assert source.count('style="min-width: 0"') == 2
    assert "rounded-2xl border border-blue-100 bg-slate-50/70 p-2" not in source
    assert 'aria-label="搜索姓名或手机号"' in source
    assert 'role="group" aria-label="创建时间"' in source
    assert 'aria-label="重置筛选"' not in source
    assert "首页" in source
    assert "上一页" in source
    assert "下一页" in source
    assert "末页" in source
    assert "body.admin-body--candidates .admin-page-mount" in css_source
    assert "body.admin-body--candidates {\n      overflow: auto;" in css_source
    assert "body.admin-body--candidates .admin-page-mount {\n      min-height: 0;\n      overflow: visible;" in css_source
    assert ".admin-candidate-create.admin-right-pane" in css_source
    assert ".admin-candidate-create.admin-right-pane {\n      position: sticky;" in css_source
    assert ".admin-candidate-create.admin-right-pane {\n      position: sticky;\n      top: 1.5rem;\n      display: flex;\n      height: calc(100vh - 3rem);" in css_source


def test_admin_candidates_module_uses_page_query_param() -> None:
    source = (ROOT / "static" / "admin" / "modules" / "pages" / "candidates.js").read_text(encoding="utf-8")

    assert 'query.set("page"' in source
    assert 'query.set("created_from"' in source
    assert 'query.set("created_to"' in source
    assert "scheduleCandidatesReloadFromFirstPage" in source


def test_admin_candidates_page_exposes_attempt_summary() -> None:
    html_source = (ROOT / "static" / "admin" / "pages" / "candidates.html").read_text(encoding="utf-8")
    js_source = (ROOT / "static" / "admin" / "modules" / "pages" / "candidates.js").read_text(encoding="utf-8")

    assert "candidateAttemptTitle" in html_source
    assert "candidateAttemptCompletionLabel" in html_source
    assert "candidateAttemptShowScore(summary)" in html_source
    assert "candidateAttemptScoreLabel" in html_source
    assert "candidateAttemptDisplayRows" in html_source
    assert "最近试卷" not in html_source
    assert "attempt_summary" in js_source
    assert "attempt_summaries" in js_source
    assert 'score !== "-"' in js_source
    assert 'summary.completed || Boolean(score && score !== "-")' not in js_source
    assert "正在答题" in js_source
    assert "正在判卷" in js_source


def test_admin_candidate_detail_exposes_job_description_management() -> None:
    html_source = (ROOT / "static" / "admin" / "pages" / "candidate-detail.html").read_text(encoding="utf-8")
    js_source = (ROOT / "static" / "admin" / "modules" / "pages" / "candidates.js").read_text(encoding="utf-8")

    assert "candidateJobDescriptions()" in html_source
    assert "candidateAvailableJobDescriptionOptions()" in html_source
    assert "addCandidateJobDescriptions" in html_source
    assert "removeCandidateJobDescription(item)" in html_source
    assert "×" in html_source
    assert "removeCandidateJobDescription" in js_source
    assert "/job-descriptions/remove" in js_source
    assert "/job-descriptions" in js_source
    assert "job_description_ids" in js_source


def test_admin_candidate_detail_places_resume_summary_in_action_column() -> None:
    html_source = (ROOT / "static" / "admin" / "pages" / "candidate-detail.html").read_text(encoding="utf-8")
    js_source = (ROOT / "static" / "admin" / "modules" / "pages" / "candidates.js").read_text(encoding="utf-8")

    actions_index = html_source.index('adminCompactPanelVisible(\'candidate-detail\', \'actions\')')
    resume_summary_index = html_source.index("saveCandidateResumeEvaluation")

    assert actions_index < resume_summary_index
    assert "candidateResumeMainHasStructuredContent()" in html_source
    assert "candidateResumeMainHasStructuredContent()" in js_source


def test_admin_candidate_detail_allows_resume_evaluation_editing() -> None:
    html_source = (ROOT / "static" / "admin" / "pages" / "candidate-detail.html").read_text(encoding="utf-8")
    js_source = (ROOT / "static" / "admin" / "modules" / "pages" / "candidates.js").read_text(encoding="utf-8")
    state_source = (ROOT / "static" / "admin" / "modules" / "state.js").read_text(encoding="utf-8")

    assert "candidateResumeEvaluationForm.evaluation" in html_source
    assert "candidateResumeEvaluationForm.job_match_score" in html_source
    assert "candidateResumeEvaluationSourceLabel" in html_source
    assert "!candidateResumeEvaluationForm.editing" in html_source
    assert "beginCandidateResumeEvaluationEdit" in html_source
    assert "cancelCandidateResumeEvaluationEdit" in html_source
    assert "beginCandidateResumeScoreEdit" in html_source
    assert "saveCandidateResumeScore" in html_source
    assert "saveCandidateResumeEvaluation" in html_source
    assert "/resume/evaluation" in js_source
    assert "syncCandidateResumeEvaluationForm" in js_source
    assert "beginCandidateResumeEvaluationEdit" in js_source
    assert "cancelCandidateResumeEvaluationEdit" in js_source
    assert "candidateResumeEvaluationSourceClass" in js_source
    assert "normalizeCandidateResumeJobMatchScoreInput" in js_source
    assert "editing: false" in js_source
    assert "candidateResumeEvaluationForm" in state_source


def test_admin_quiz_analytics_is_embedded_in_quiz_detail() -> None:
    router_source = (ROOT / "static" / "admin" / "modules" / "router.js").read_text(encoding="utf-8")
    state_source = (ROOT / "static" / "admin" / "modules" / "state.js").read_text(encoding="utf-8")
    constants_source = (ROOT / "static" / "admin" / "modules" / "constants.js").read_text(encoding="utf-8")
    quiz_source = (ROOT / "static" / "admin" / "modules" / "pages" / "quizzes.js").read_text(encoding="utf-8")
    detail_source = (ROOT / "static" / "admin" / "pages" / "quiz-detail.html").read_text(encoding="utf-8")

    assert '"/static/admin/pages/quiz-analytics.html"' not in router_source
    assert 'href: "/admin/quiz-analytics"' not in state_source
    assert 'pathname === "/admin/quiz-analytics"' in router_source
    assert 'defaultTab: "analytics"' in constants_source
    assert '{ id: "analytics", label: "测验分析" }' in constants_source
    assert 'quizDetailSideTab: "analytics"' in state_source
    assert 'this.loadQuizAnalyticsDetail(quizKey, { quiet: true, syncRoute: false })' in quiz_source
    assert "quizDetailPanelVisible('analytics')" in detail_source
    assert "quizDetailPanelVisible('history')" in detail_source


def test_admin_quiz_detail_keeps_its_primary_content_panel_surface() -> None:
    source = (ROOT / "static" / "admin" / "pages" / "quiz-detail.html").read_text(encoding="utf-8")

    primary_panel = re.search(
        r"x-show=\"adminCompactPanelVisible\('quiz-detail', 'content'\)\"\s+class=\"([^\"]+)\"",
        source,
    )

    assert primary_panel is not None
    classes = set(primary_panel.group(1).split())
    assert {
        "admin-surface",
        "rounded-3xl",
        "border",
        "bg-white/82",
        "p-5",
        "shadow-[var(--admin-shadow)]",
        "backdrop-blur-xl",
    } <= classes


def test_admin_quiz_detail_keeps_its_score_summary_card() -> None:
    source = (ROOT / "static" / "admin" / "pages" / "quiz-detail.html").read_text(encoding="utf-8")

    assert "admin-quiz-overview-card overflow-hidden rounded-2xl border border-blue-100 bg-blue-50/70" in source
    assert 'class="admin-quiz-summary-split"' in source
    assert 'class="admin-quiz-summary-score px-4 py-4 md:px-5"' in source


def test_admin_job_descriptions_route_nav_and_page_exist() -> None:
    router_source = (ROOT / "static" / "admin" / "modules" / "router.js").read_text(encoding="utf-8")
    state_source = (ROOT / "static" / "admin" / "modules" / "state.js").read_text(encoding="utf-8")
    app_source = (ROOT / "static" / "admin" / "app.js").read_text(encoding="utf-8")
    index_source = (ROOT / "static" / "admin" / "index.html").read_text(encoding="utf-8")
    page_source = (ROOT / "static" / "admin" / "pages" / "job-descriptions.html").read_text(encoding="utf-8")
    module_source = (ROOT / "static" / "admin" / "modules" / "pages" / "job-descriptions.js").read_text(encoding="utf-8")
    css_source = (ROOT / "static" / "assets" / "css" / "admin" / "pages.css").read_text(encoding="utf-8")

    assert '"/static/admin/pages/job-descriptions.html"' in router_source
    assert 'path === "/admin/job-descriptions"' in router_source
    assert 'name: "job-descriptions"' in router_source
    assert 'href: "/admin/job-descriptions"' in state_source
    assert 'label: "职位管理"' in state_source
    assert 'jobDescriptionContentTab: "preview"' in state_source
    assert "admin-body--job-descriptions" in index_source
    assert "admin-page-mount" in index_source
    assert "createAdminJobDescriptionsModule" in app_source
    assert "jobDescriptionForm.content_md" in page_source
    assert "关联试题" in page_source
    assert "jobDescriptionRelatedQuizOptions()" in page_source
    assert "jobDescriptionRelatedQuizItems()" in page_source
    assert "jobDescriptionRelatedQuizSearchResults(query)" in page_source
    assert "addJobDescriptionRelatedQuiz(quiz.quiz_key)" in page_source
    assert "removeJobDescriptionRelatedQuiz(quiz.quiz_key)" in page_source
    assert 'x-show="!adding"' in page_source
    assert 'x-show="adding"' in page_source
    assert '@keydown.escape.prevent="adding = false; query = \'\'"' in page_source
    assert '@blur="adding = false; query = \'\'"' in page_source
    assert "@mousedown.prevent" in page_source
    assert "border-slate-200 bg-slate-50 text-slate-400" in page_source
    assert 'type="checkbox"' not in page_source
    assert "admin-job-description-page" in page_source
    assert "admin-job-description-items" in page_source
    assert "admin-job-description-editor" in page_source
    assert 'aria-label="职位内容视图"' in page_source
    assert "jobDescriptionContentTabs()" in page_source
    assert "(jobDescriptionContentTab || 'preview') === 'preview'" in page_source
    assert "overflow-y-auto overscroll-contain" not in page_source
    assert 'data-fixed-panel="true"' not in page_source
    assert "textarea.dataset?.fixedPanel" not in module_source
    assert "body.admin-body--job-descriptions .admin-page-mount" in css_source
    assert "overflow: visible;" in css_source
    assert ".admin-job-description-list" in css_source
    assert "position: sticky;" in css_source
    assert ".admin-job-description-items" in css_source
    job_description_items_css = re.search(
        r"\.admin-job-description-items\s*\{(?P<rules>.*?)\n\s*\}",
        css_source,
        re.DOTALL,
    )
    assert job_description_items_css is not None
    assert "scrollbar-gutter" not in job_description_items_css.group("rules")
    assert "jobDescriptionContentTabs()" in module_source
    assert "normalizeJobDescriptionRelatedQuizzes" in module_source
    assert "jobDescriptionRelatedQuizItems()" in module_source
    assert "jobDescriptionRelatedQuizSearchResults(query = \"\")" in module_source
    assert "addJobDescriptionRelatedQuiz(quizKey)" in module_source
    assert "removeJobDescriptionRelatedQuiz(quizKey)" in module_source
    assert "related_quizzes" in module_source
    assert 'this.jobDescriptionContentTab = "preview"' in module_source
    assert 'label: "编辑"' in module_source
    assert 'label: "预览"' in module_source
    assert "x-html=\"jobDescriptionPreviewHtml()\"" in page_source
    assert "jobDescriptionSourceBadgeClass" in page_source
    assert "jobDescriptionReadOnly()" in page_source
    assert "/api/admin/job-descriptions" in module_source
    assert "仓库来源职位请在 Git 仓库中修改" in module_source
