export function summarizeTextChange(beforeValue, afterValue, inputType = "", wasPaste = false, forcedKind = "") {
  const before = Array.from(String(beforeValue || ""));
  const after = Array.from(String(afterValue || ""));
  let prefix = 0;
  while (prefix < before.length && prefix < after.length && before[prefix] === after[prefix]) prefix += 1;
  let suffix = 0;
  while (suffix < before.length - prefix && suffix < after.length - prefix
    && before[before.length - 1 - suffix] === after[after.length - 1 - suffix]) suffix += 1;
  const added = after.length - prefix - suffix;
  const deleted = before.length - prefix - suffix;
  if (!added && !deleted) return null;
  const kind = forcedKind || (wasPaste || inputType === "insertFromPaste" ? "paste"
    : inputType.startsWith("delete") ? "delete" : added && deleted ? "replace" : added ? "insert" : "delete");
  return { kind, added_chars: added, deleted_chars: deleted, length_after: after.length };
}

export function createPublicQuizModule() {
  return {
          clearAutosaveTimer() {
            if (this.autosaveTimer) {
              window.clearTimeout(this.autosaveTimer);
              this.autosaveTimer = null;
            }
          },

          resetActivityCapture(question) {
            if (this.activityFlushTimer) window.clearTimeout(this.activityFlushTimer);
            this.activityFlushTimer = null;
            this.activityQuestionId = question?.type === "short" && this.viewCard === "question" ? String(question.qid || "") : "";
            this.activityCaptureId = this.activityQuestionId
              ? (window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`) : "";
            this.activityBaseline = this.activityQuestionId ? String(this.textDraft || "") : "";
            this.activityNextSeq = 1;
            this.activityPending = [];
            this.activityPastePending = false;
            this.activityIsComposing = false;
          },

          syncQuestionClock(question) {
            const serverNow = Date.parse(this.state.quiz?.server_now || "");
            const questionStart = Date.parse(this.state.quiz?.question_flow?.current_started_at || "");
            const remainingSeconds = Number(this.state.quiz?.question_flow?.current_question_remaining_seconds);
            const durationSeconds = Number(question?.answer_time_seconds || this.state.quiz?.question_flow?.current_question_seconds || 0);
            this.questionElapsedAtSyncMs = Number.isFinite(serverNow) && Number.isFinite(questionStart)
              ? Math.max(0, serverNow - questionStart)
              : Number.isFinite(remainingSeconds) && Number.isFinite(durationSeconds)
                ? Math.max(0, (durationSeconds - remainingSeconds) * 1000) : 0;
            this.questionClockSyncedAtMs = performance.now();
          },

          scheduleActivityFlush() {
            if (this.activityFlushTimer || !this.activityPending.length) return;
            this.activityFlushTimer = window.setTimeout(() => {
              this.activityFlushTimer = null;
              this.flushActivity();
            }, 2000);
          },

          recordTextChange(event, forcedKind = "") {
            if (!this.activityQuestionId || this.activityQuestionId !== this.currentQuestion()?.qid) return;
            if (event?.isComposing || this.activityIsComposing) return;
            const afterValue = String(event?.target?.value ?? this.textDraft ?? "");
            const wasPaste = this.activityPastePending && (!event?.inputType || event.inputType === "insertFromPaste");
            this.activityPastePending = false;
            const inputType = String(event?.inputType || "");
            const change = summarizeTextChange(this.activityBaseline, afterValue, inputType, wasPaste, forcedKind);
            this.activityBaseline = afterValue;
            if (!change) return;
            this.activityPending.push({
              seq: this.activityNextSeq++,
              elapsed_ms: Math.max(0, Math.round(this.questionElapsedAtSyncMs + performance.now() - this.questionClockSyncedAtMs)),
              ...change,
            });
            if (this.activityPending.length >= 20) this.flushActivity();
            else this.scheduleActivityFlush();
          },

          onTextCompositionEnd(event) {
            this.activityIsComposing = false;
            this.recordTextChange(event, "composition");
          },

          async flushActivity() {
            if (this.activityFlushPromise) return this.activityFlushPromise;
            if (!this.activityQuestionId || !this.activityPending.length || !this.route.token) return true;
            const captureId = this.activityCaptureId;
            const questionId = this.activityQuestionId;
            this.activityFlushPromise = (async () => {
              while (this.activityPending.length && this.activityCaptureId === captureId) {
                const events = this.activityPending.slice(0, 100);
                try {
                  await this.api(`/api/public/answers/${encodeURIComponent(this.route.token)}/activity`, {
                    method: "POST",
                    body: JSON.stringify({ question_id: questionId, session_id: this.sessionId, capture_id: captureId, events }),
                    headers: { "Content-Type": "application/json" },
                    manageState: false,
                  });
                } catch (_) {
                  return false;
                }
                if (this.activityCaptureId !== captureId) return false;
                const lastSeq = events[events.length - 1].seq;
                this.activityPending = this.activityPending.filter((item) => item.seq > lastSeq);
              }
              return true;
            })();
            try {
              return await this.activityFlushPromise;
            } finally {
              this.activityFlushPromise = null;
              this.scheduleActivityFlush();
            }
          },

          stopQuestionTimer() {
            if (this.questionTimer) {
              window.clearInterval(this.questionTimer);
              this.questionTimer = null;
            }
          },

          timingIgnored() {
            return Boolean(this.state.assignment?.ignore_timing);
          },

          syncQuestionTimer() {
            if (this.viewCard !== "question" || this.timingIgnored()) {
              this.questionRemainingSeconds = 0;
              this.questionRemainingMs = 0;
              return;
            }
            this.questionRemainingMs = this.computeCurrentRemainingMs();
            this.questionRemainingSeconds = Math.ceil(this.questionRemainingMs / 1000);
            this.questionTimer = window.setInterval(() => {
              if (this.viewCard !== "question") return;
              this.questionRemainingMs = this.computeCurrentRemainingMs();
              this.questionRemainingSeconds = Math.ceil(this.questionRemainingMs / 1000);
              if (this.questionRemainingMs > 0 || this.timeoutSubmitting || this.actionBusy) return;
              this.timeoutSubmitting = true;
              const isLast = this.isLastQuestion();
              this.performAnswerAction({ advance: !isLast, submit: isLast, forceTimeout: true })
                .catch(() => {})
                .finally(() => {
                  this.timeoutSubmitting = false;
                });
            }, 80);
          },

          computeCurrentRemainingMs() {
            if (this.timingIgnored()) return 0;
            const question = this.currentQuestion();
            const startedAt = this.state.quiz?.question_flow?.current_started_at || "";
            if (!question) return 0;
            const durationSeconds = Number(question.answer_time_seconds || this.state.quiz?.question_flow?.current_question_seconds || 0);
            const durationMs = Math.max(0, durationSeconds * 1000);
            if (!startedAt || durationMs <= 0) return durationMs;
            if (this.questionClockSyncedAtMs == null) return durationMs;
            const elapsedMs = Math.max(0, this.questionElapsedAtSyncMs + performance.now() - this.questionClockSyncedAtMs);
            return Math.max(0, durationMs - elapsedMs);
          },

          cardBackgroundStyle() {
            let image = "";
            let position = "center center";
            if (this.viewCard === "done") {
              image = this.state.quiz?.spec?.end_image || "";
            } else {
              image = this.state.quiz?.spec?.welcome_image || "";
              position = "center top";
            }
            return image
              ? `background-image:url('${image}');background-position:${position};background-size:cover;`
              : "";
          },

          quizTitle() {
            return this.state.quiz?.title || this.state.assignment?.quiz_key || "在线测验";
          },

          quizQuestionCount() {
            return Number(this.state.quiz?.question_count || this.state.quiz?.stats?.total_questions || 0);
          },

          quizDurationSeconds() {
            return Number(this.state.quiz?.answer_time_total_seconds || this.state.quiz?.time_limit_seconds || 0);
          },

          quizDurationText() {
            if (this.timingIgnored()) return "不限时";
            const totalSeconds = this.quizDurationSeconds();
            if (totalSeconds <= 0) return "-";
            const totalMinutes = Math.ceil(totalSeconds / 60);
            return `${totalMinutes} 分钟`;
          },

          quizDurationLabel() {
            return this.timingIgnored() ? "答题时长" : "预计时长";
          },

          doneMessage() {
            if (this.state.result?.status === "done") {
              return "答案已经提交并完成判卷，这个链接不会再重新开放答题。";
            }
            return "答案已经提交，系统正在判卷或整理结果。这个链接再次访问也只会显示当前结束状态。";
          },

          currentQuestionIndex() {
            return Number(this.state.quiz?.question_flow?.current_index || 0);
          },

          questionNumber() {
            return this.currentQuestionIndex() + 1;
          },

          currentQuestion() {
            const questions = this.state.quiz?.spec?.questions || [];
            if (!questions.length) return null;
            const index = Math.max(0, Math.min(this.currentQuestionIndex(), questions.length - 1));
            return questions[index];
          },

          isLastQuestion() {
            return this.questionNumber() >= this.quizQuestionCount();
          },

          currentAnswer() {
            const qid = this.currentQuestion()?.qid;
            if (!qid) return null;
            return this.state.assignment?.answers?.[qid] ?? null;
          },

          currentAnswerPayload() {
            const question = this.currentQuestion();
            if (!question) return null;
            if (question.type === "short") return this.textDraft;
            if (question.type === "multiple") return [...this.selectedMultiple];
            return this.currentAnswer();
          },

          isSingleSelected(optionKey) {
            return String(this.currentAnswer() || "") === String(optionKey || "");
          },

          isMultipleSelected(optionKey) {
            return this.selectedMultiple.includes(String(optionKey || ""));
          },

          optionBadgeClasses(optionKey, selected) {
            const key = String(optionKey || "").trim().toUpperCase();
            const palette = {
              A: selected
                ? "border-teal-300/55 bg-teal-300/18 text-teal-100"
                : "border-teal-400/35 bg-teal-400/10 text-teal-200",
              B: selected
                ? "border-sky-300/55 bg-sky-300/18 text-sky-100"
                : "border-sky-400/35 bg-sky-400/10 text-sky-200",
              C: selected
                ? "border-amber-300/55 bg-amber-300/18 text-amber-100"
                : "border-amber-400/35 bg-amber-400/10 text-amber-200",
              D: selected
                ? "border-rose-300/55 bg-rose-300/18 text-rose-100"
                : "border-rose-400/35 bg-rose-400/10 text-rose-200",
              E: selected
                ? "border-lime-300/55 bg-lime-300/18 text-lime-100"
                : "border-lime-400/35 bg-lime-400/10 text-lime-200",
            };
            return palette[key] || (selected
              ? "border-emerald-300/55 bg-emerald-300/18 text-emerald-100"
              : "border-white/15 bg-white/5 text-slate-200");
          },

          canAdvanceCurrent() {
            const question = this.currentQuestion();
            if (!question) return false;
            if (question.type === "single") return Boolean(String(this.currentAnswer() || "").trim());
            if (question.type === "multiple") return this.selectedMultiple.length > 0;
            return Boolean(String(this.textDraft || "").trim());
          },

          deferredSaveText(question = this.currentQuestion()) {
            if (!question) return "";
            if (question.type === "short") {
              return this.isLastQuestion() ? "简答题在点击“完成并提交”后保存" : "简答题在点击“完成本题”后保存";
            }
            if (question.type === "multiple") {
              return this.isLastQuestion() ? "多选题在点击“提交测验”后保存" : "多选题在点击“下一题”后保存";
            }
            return "";
          },

          questionProgressPercent() {
            if (this.timingIgnored()) return 0;
            const question = this.currentQuestion();
            if (!question) return 0;
            const totalMs = Math.max(0, Number(question.answer_time_seconds || 0) * 1000);
            if (totalMs <= 0) return 0;
            return Math.max(0, Math.min(100, (this.questionRemainingMs / totalMs) * 100));
          },

          formatDuration(totalSeconds) {
            const seconds = Math.max(0, Number(totalSeconds || 0));
            const h = Math.floor(seconds / 3600);
            const m = Math.floor((seconds % 3600) / 60);
            const s = seconds % 60;
            return [h, m, s].map((value) => String(value).padStart(2, "0")).join(":");
          },

          autosaveText() {
            return this.autosaveMessage || "";
          },

          async performAnswerAction(action, options = {}) {
            const question = this.currentQuestion();
            if (!question || !this.route.token) return;
            this.actionBusy = true;
            try {
              if (question.type === "short") await this.flushActivity();
              const pending = question.type === "short" ? this.activityPending.slice(-100) : [];
              const data = await this.api(`/api/public/answers/${encodeURIComponent(this.route.token)}`, {
                method: "POST",
                body: JSON.stringify({
                  question_id: question.qid,
                  answer: this.currentAnswerPayload(),
                  advance: Boolean(action.advance),
                  submit: Boolean(action.submit),
                  session_id: this.sessionId,
                  force_timeout: Boolean(action.forceTimeout),
                  activity: pending.length ? {
                    question_id: question.qid,
                    session_id: this.sessionId,
                    capture_id: this.activityCaptureId,
                    events: pending,
                    dropped_events: Math.max(0, this.activityPending.length - pending.length),
                  } : null,
                }),
                headers: { "Content-Type": "application/json" },
              });
              this.state = data;
              await this.syncFromState();
            } catch (error) {
              const detail = String(error?.message || "");
              if (["question_locked", "already_submitted", "not_last_question"].includes(detail) && this.route.token) {
                await this.loadAttempt(this.route.token);
              }
              throw error;
            } finally {
              this.actionBusy = false;
            }
          },

          async enterQuiz() {
            const data = await this.api(`/api/public/attempt/${encodeURIComponent(this.route.token)}/enter`, {
              method: "POST",
            });
            this.state = data;
            await this.syncFromState();
          },

          async selectSingleOption(optionKey) {
            if (this.actionBusy) return;
            const qid = this.currentQuestion()?.qid;
            if (!qid) return;
            if (!this.state.assignment.answers) this.state.assignment.answers = {};
            this.state.assignment.answers[qid] = String(optionKey || "");
            await this.performAnswerAction({ advance: true, submit: this.isLastQuestion(), forceTimeout: false });
          },

          toggleMultipleOption(optionKey) {
            const value = String(optionKey || "");
            if (!value) return;
            if (this.selectedMultiple.includes(value)) {
              this.selectedMultiple = this.selectedMultiple.filter((item) => item !== value);
            } else {
              this.selectedMultiple = [...this.selectedMultiple, value];
            }
            this.clearAutosaveTimer();
            this.autosaveMessage = this.deferredSaveText();
          },

          onTextInput(event) {
            this.clearAutosaveTimer();
            this.autosaveMessage = this.deferredSaveText();
            this.recordTextChange(event);
          },

          async goNext() {
            if (!this.canAdvanceCurrent() || this.actionBusy) return;
            await this.performAnswerAction({ advance: true, submit: false, forceTimeout: false });
          },

          async submitLastQuestion() {
            if (!this.canAdvanceCurrent() || this.actionBusy) return;
            await this.performAnswerAction({ advance: false, submit: true, forceTimeout: false });
          },

          onQuestionTouchStart(event) {
            const touch = event.changedTouches?.[0];
            if (!touch) return;
            this.touchStart = { x: touch.clientX, y: touch.clientY };
          },

          onQuestionTouchEnd(event) {
            if (!this.touchStart || !this.canAdvanceCurrent() || this.actionBusy) {
              this.touchStart = null;
              return;
            }
            const touch = event.changedTouches?.[0];
            if (!touch) {
              this.touchStart = null;
              return;
            }
            const dx = touch.clientX - this.touchStart.x;
            const dy = touch.clientY - this.touchStart.y;
            this.touchStart = null;
            if (!(dx < -60 || dy < -60)) return;
            if (this.isLastQuestion()) {
              this.submitLastQuestion();
            } else {
              this.goNext();
            }
          },
  };
}
