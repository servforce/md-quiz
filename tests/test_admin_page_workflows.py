from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def _run_page_modules(tmp_path: Path, checks: str) -> None:
    """直接执行真实前端模块；使用临时 ESM 扩展名适配测试机 Node。"""
    modules = ROOT / "static" / "admin" / "modules"
    (tmp_path / "constants.mjs").write_text(
        (modules / "constants.js").read_text(encoding="utf-8"), encoding="utf-8"
    )
    for name in ("quizzes", "assignments", "quiz-analytics"):
        source = (modules / "pages" / f"{name}.js").read_text(encoding="utf-8")
        (tmp_path / f"{name}.mjs").write_text(
            source.replace('"../constants.js"', '"./constants.mjs"'), encoding="utf-8"
        )
    imports = "\n".join(
        [
            'import assert from "node:assert/strict";',
            f'import {{ createAdminQuizzesModule }} from {json.dumps((tmp_path / "quizzes.mjs").as_uri())};',
            f'import {{ createAdminAssignmentsModule }} from {json.dumps((tmp_path / "assignments.mjs").as_uri())};',
            f'import {{ createAdminQuizAnalyticsModule }} from {json.dumps((tmp_path / "quiz-analytics.mjs").as_uri())};',
        ]
    )
    subprocess.run(
        ["node", "--input-type=module", "-e", imports + "\n" + checks],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def test_sync_summary_distinguishes_partial_failure_and_pending(tmp_path: Path) -> None:
    _run_page_modules(
        tmp_path,
        """
const app = Object.assign({ repoBinding: { repo_url: 'https://example.invalid/demo.git' }, syncState: {} }, createAdminQuizzesModule(), createAdminAssignmentsModule());
for (const status of ['queued', 'pending', 'running']) {
  app.syncState = { status };
  assert.equal(app.isSyncBusy(), true);
}
app.syncState = { status: 'done', last_result: { error_count: 2, job_description_error_count: 1, deleted_exams: 3, retired_exams: 3, archived_job_descriptions: 4 } };
assert.equal(app.isSyncBusy(), false);
assert.equal(app.quizSyncStatusLabel(), '同步完成，部分条目失败');
const groups = app.quizSyncCountGroups();
assert.equal(groups[0].counts.find(item => item.label === '删除').value, 3);
assert.equal(groups[1].counts.find(item => item.label === '归档').value, 4);
assert.equal(app.quizSyncResult().error_count, 2);
app.syncState.last_result.error_count = 0;
assert.equal(app.quizSyncStatusLabel(), '同步完成');
app.syncState.status = 'failed';
assert.equal(app.quizSyncStatusLabel(), '同步失败');
assert.deepEqual(app.quizSyncCountGroups(), []);
app.syncState.status = 'future-status';
assert.equal(app.quizSyncStatusLabel(), 'future-status');
""",
    )


def test_detail_tabs_and_share_state_do_not_change_business_records(tmp_path: Path) -> None:
    _run_page_modules(
        tmp_path,
        """
const app = Object.assign({ route: { query: {} }, assignmentShareToken: '' }, createAdminQuizzesModule(), createAdminAssignmentsModule());
assert.equal(app.quizDetailTab(), 'content');
for (const tab of ['content', 'analytics', 'history']) {
  app.route.query.tab = tab;
  assert.equal(app.quizDetailTab(), tab);
  assert.equal(app.quizDetailPanelVisible(tab), true);
}
app.route.query.tab = 'invalid';
assert.equal(app.quizDetailTab(), 'content');
const first = Object.freeze({ token: 'demo-first', status: 'finished', needs_attention: true, suspected_ai_question_count: 2 });
const second = Object.freeze({ token: 'demo-second', status: 'grading' });
app.toggleAssignmentShare(first);
assert.equal(app.assignmentShareIsOpen(first), true);
app.toggleAssignmentShare(second);
assert.equal(app.assignmentShareIsOpen(first), false);
assert.equal(app.assignmentShareIsOpen(second), true);
app.closeAssignmentShare({ restoreFocus: false });
assert.equal(app.assignmentShareToken, '');
assert.equal(first.needs_attention, true);
assert.equal(first.suspected_ai_question_count, 2);
assert.equal(app.canToggleAssignmentHandling(first), true);
assert.equal(app.canToggleAssignmentHandling(second), false);
""",
    )


def test_result_views_keep_traits_and_scored_results_distinct(tmp_path: Path) -> None:
    _run_page_modules(
        tmp_path,
        """
const app = Object.assign({ attemptDetail: { review: { evaluation: {} } } }, createAdminAssignmentsModule(), createAdminQuizAnalyticsModule());
app.attemptDetail.review.evaluation = { result_mode: 'traits', has_score: false, score_display: '', primary_dimensions: ['E', 'N'], paired_dimensions: [{ left: 'E', right: 'I', left_score: 3, right_score: 1 }] };
assert.equal(app.attemptEvaluationShowScore(), false);
assert.deepEqual(app.attemptEvaluationPrimaryDimensions(), ['E', 'N']);
assert.equal(app.attemptEvaluationTraitPairs()[0].left_score, 3);
assert.equal(app.quizAnalyticsResultDisplay({ result_mode: 'traits', trait_summary: 'EN' }), 'EN');
app.attemptDetail.review.evaluation = { result_mode: 'scored', has_score: true, score_display: '0 / 100' };
assert.equal(app.attemptEvaluationShowScore(), true);
assert.equal(app.quizAnalyticsResultDisplay({ result_mode: 'scored', score_display: '0 / 100' }), '0 / 100');
assert.equal(app.quizAnalyticsItemMatchesScoreFilter({ score: 90, score_max: 100 }, { start: 80, end: 100, scoreMax: 100 }), true);
assert.equal(app.quizAnalyticsItemMatchesScoreFilter({ score: 90, score_max: 120 }, { start: 80, end: 100, scoreMax: 100 }), false);
""",
    )


def test_attempt_input_review_mark_only_includes_short_answers_with_signals(tmp_path: Path) -> None:
    _run_page_modules(
        tmp_path,
        """
const app = Object.assign({ attemptDetail: { review: { answers: [
  { qid: 'Q1', type: 'short', activity: { input: { signals: [{ kind: 'large_paste' }] } } },
  { qid: 'Q2', type: 'short', activity: { input: { signals: [] } } },
  { qid: 'Q3', type: 'short' },
  { qid: 'Q4', type: 'single', activity: { input: { signals: [{ kind: 'large_insert' }] } } },
] } } }, createAdminAssignmentsModule());
assert.equal(app.attemptQuestionHasInputSignals(app.attemptReviewAnswers()[0]), true);
assert.equal(app.attemptQuestionHasInputSignals(app.attemptReviewAnswers()[1]), false);
assert.equal(app.attemptQuestionHasInputSignals(app.attemptReviewAnswers()[2]), false);
assert.equal(app.attemptQuestionHasInputSignals(app.attemptReviewAnswers()[3]), false);
""",
    )
