import assert from "node:assert/strict";
import test from "node:test";
import { setTimeout as sleep } from "node:timers/promises";
import { createPublicQuizModule, summarizeTextChange } from "../../static/public/modules/quiz.js";

test("输入、删除和替换只返回字数及行为", () => {
  assert.deepEqual(summarizeTextChange("", "你好", "insertText"), {
    kind: "insert", added_chars: 2, deleted_chars: 0, length_after: 2,
  });
  assert.deepEqual(summarizeTextChange("你好", "你", "deleteContentBackward"), {
    kind: "delete", added_chars: 0, deleted_chars: 1, length_after: 1,
  });
  assert.deepEqual(summarizeTextChange("你好", "你们", "insertReplacementText"), {
    kind: "replace", added_chars: 1, deleted_chars: 1, length_after: 2,
  });
  assert.equal(summarizeTextChange("你好", "你好"), null);
});

test("粘贴、输入法提交和 Unicode 字符计数", () => {
  assert.deepEqual(summarizeTextChange("", "一段粘贴", "insertFromPaste"), {
    kind: "paste", added_chars: 4, deleted_chars: 0, length_after: 4,
  });
  assert.deepEqual(summarizeTextChange("", "中文", "", false, "composition"), {
    kind: "composition", added_chars: 2, deleted_chars: 0, length_after: 2,
  });
  assert.deepEqual(summarizeTextChange("", "🙂", "insertText"), {
    kind: "insert", added_chars: 1, deleted_chars: 0, length_after: 1,
  });
});

test("浏览器时钟落后服务端时，题目倒计时仍递减", async () => {
  const serverNow = new Date(Date.now() + 60_000).toISOString();
  const app = Object.assign({
    viewCard: "question",
    textDraft: "",
    activityPending: [],
    state: {
      assignment: { ignore_timing: false },
      quiz: {
        server_now: serverNow,
        question_flow: { current_index: 0, current_started_at: serverNow },
        spec: { questions: [{ qid: "Q1", type: "single", answer_time_seconds: 120 }] },
      },
    },
  }, createPublicQuizModule());
  app.syncQuestionClock(app.currentQuestion());
  const before = app.computeCurrentRemainingMs();
  await sleep(1100);
  const after = app.computeCurrentRemainingMs();
  assert.ok(after < before - 900, `倒计时应递减，实际 ${before} -> ${after}`);
});
