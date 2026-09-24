export function createAdminAssignmentsModule() {
  let listRequestId = 0;
  let detailRequestId = 0;
  return {
    assignmentShareButtonId(item) {
      return `assignment-share-button-${encodeURIComponent(String(item?.token || ""))}`;
    },

    assignmentSharePanelId(item) {
      return `assignment-share-panel-${encodeURIComponent(String(item?.token || ""))}`;
    },

    assignmentShareIsOpen(item) {
      return Boolean(item?.token) && this.assignmentShareToken === String(item.token);
    },

    toggleAssignmentShare(item) {
      this.assignmentShareToken = this.assignmentShareIsOpen(item) ? "" : String(item?.token || "");
    },

    closeAssignmentShare({ restoreFocus = true } = {}) {
      const token = this.assignmentShareToken;
      this.assignmentShareToken = "";
      if (restoreFocus && token) document.getElementById(this.assignmentShareButtonId({ token }))?.focus();
    },

    normalizeAssignmentQuizKeys(value = null) {
      const raw = Array.isArray(value) ? value : [];
      const out = [];
      const seen = new Set();
      raw.forEach((item) => {
        const key = String(item || "").trim();
        if (!key || seen.has(key)) return;
        seen.add(key);
        out.push(key);
      });
      return out;
    },

    selectedAssignmentQuizKeys() {
      const keys = this.normalizeAssignmentQuizKeys(this.assignmentForm?.quiz_keys);
      const legacyKey = String(this.assignmentForm?.quiz_key || "").trim();
      if (legacyKey && !keys.includes(legacyKey)) {
        keys.unshift(legacyKey);
      }
      return keys;
    },

    selectedAssignmentQuizzes() {
      return this.selectedAssignmentQuizKeys()
        .map((quizKey) => this.quizOptionByKey(quizKey) || { quiz_key: quizKey, title: quizKey })
        .filter((item) => String(item?.quiz_key || "").trim());
    },

    selectedAssignmentQuiz() {
      return this.selectedAssignmentQuizzes()[0] || null;
    },

    selectedAssignmentCandidate() {
      const candidateId = String(this.assignmentForm?.candidate_id || "").trim();
      if (!candidateId) return null;
      return (this.candidates?.items || []).find((item) => String(item?.id || "").trim() === candidateId) || null;
    },

    filteredAssignmentQuizzes() {
      const query = String(this.assignmentSelect?.quiz?.query || "").trim().toLowerCase();
      const items = this.quizOptionItems();
      if (!query) return items;
      return items.filter((item) => {
        const haystacks = [
          String(item?.title || "").trim(),
          String(item?.quiz_key || "").trim(),
          ...(Array.isArray(item?.tags) ? item.tags.map((tag) => String(tag || "").trim()) : []),
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        return haystacks.includes(query);
      });
    },

    filteredAssignmentCandidates() {
      const query = String(this.assignmentSelect?.candidate?.query || "").trim().toLowerCase();
      const items = Array.isArray(this.candidates?.items) ? this.candidates.items : [];
      if (!query) return items;
      return items.filter((item) => {
        const defaultQuizKeys = this.assignmentCandidateDefaultQuizKeys(item);
        const haystacks = [
          String(item?.name || "").trim(),
          String(item?.phone || "").trim(),
          String(item?.default_quiz_key || "").trim(),
          ...defaultQuizKeys,
          this.quizOptionLabel(item?.default_quiz_key),
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        return haystacks.includes(query);
      });
    },

    assignmentSelectDisplayValue(kind) {
      const target = kind === "candidate" ? "candidate" : "quiz";
      if (this.assignmentSelect?.[target]?.open) {
        return String(this.assignmentSelect[target].query || "");
      }
      if (target === "quiz") {
        const selected = this.selectedAssignmentQuizzes();
        return selected.map((item) => String(item.title || item.quiz_key || "").trim()).filter(Boolean).join("、");
      }
      const selected = this.selectedAssignmentCandidate();
      return selected ? String(selected.name || "") : "";
    },

    openAssignmentSelect(kind) {
      const target = kind === "candidate" ? "candidate" : "quiz";
      this.assignmentSelect.quiz.open = false;
      this.assignmentSelect.candidate.open = false;
      this.assignmentSelect[target].open = true;
      this.assignmentSelect[target].query = "";
    },

    handleAssignmentSelectInput(kind, event) {
      const target = kind === "candidate" ? "candidate" : "quiz";
      const value = String(event?.target?.value || "");
      this.assignmentSelect.quiz.open = false;
      this.assignmentSelect.candidate.open = false;
      this.assignmentSelect[target].open = true;
      this.assignmentSelect[target].query = value;
      // 测验是多选：输入框只用于筛选候选项，不能清空已选的 quiz_keys。
      if (target === "candidate" && this.assignmentForm.candidate_id) {
        this.assignmentForm.candidate_id = "";
        this.assignmentForm.quiz_key = "";
        this.assignmentForm.quiz_keys = [];
      }
    },

    toggleAssignmentSelect(kind) {
      const target = kind === "candidate" ? "candidate" : "quiz";
      const nextOpen = !Boolean(this.assignmentSelect?.[target]?.open);
      this.assignmentSelect.quiz.open = false;
      this.assignmentSelect.candidate.open = false;
      this.assignmentSelect[target].open = nextOpen;
      if (!nextOpen) {
        this.assignmentSelect[target].query = "";
      }
    },

    closeAssignmentSelect(kind) {
      const target = kind === "candidate" ? "candidate" : "quiz";
      if (!this.assignmentSelect?.[target]) return;
      this.assignmentSelect[target].open = false;
      this.assignmentSelect[target].query = "";
    },

    assignmentQuizSelected(item) {
      const quizKey = String(item?.quiz_key || "").trim();
      return Boolean(quizKey) && this.selectedAssignmentQuizKeys().includes(quizKey);
    },

    toggleAssignmentQuiz(item) {
      const quizKey = String(item?.quiz_key || "").trim();
      if (!quizKey) return;
      const current = this.selectedAssignmentQuizKeys();
      const next = current.includes(quizKey)
        ? current.filter((itemKey) => itemKey !== quizKey)
        : [...current, quizKey];
      this.assignmentForm.quiz_keys = next;
      this.assignmentForm.quiz_key = next[0] || "";
    },

    selectAssignmentQuiz(item) {
      this.toggleAssignmentQuiz(item);
    },

    clearAssignmentQuiz() {
      this.assignmentForm.quiz_key = "";
      this.assignmentForm.quiz_keys = [];
      this.closeAssignmentSelect("quiz");
    },

    assignmentCandidateDefaultQuizKeys(item) {
      const directKeys = this.normalizeAssignmentQuizKeys(
        Array.isArray(item?.default_quiz_keys) && item.default_quiz_keys.length
          ? item.default_quiz_keys
          : [item?.default_quiz_key],
      );
      const jobRows = Array.isArray(item?.job_descriptions) ? item.job_descriptions : [];
      const jobKeys = this.normalizeAssignmentQuizKeys(
        jobRows.flatMap((job) => (Array.isArray(job?.related_quizzes) ? job.related_quizzes : [])),
      );
      return this.normalizeAssignmentQuizKeys([...directKeys, ...jobKeys]);
    },

    async loadAssignmentCandidateDefaultQuizKeys(item) {
      const fallbackKeys = this.assignmentCandidateDefaultQuizKeys(item);
      const candidateId = Number(item?.id || 0);
      if (!candidateId) return fallbackKeys;
      if (Array.isArray(item?.default_quiz_keys) && item.default_quiz_keys.length) {
        return fallbackKeys;
      }
      const detail = await this.api(`/api/admin/candidates/${candidateId}`, { quiet: true });
      const candidateKeys = this.normalizeAssignmentQuizKeys(detail?.candidate?.default_quiz_keys);
      const jobRows = Array.isArray(detail?.job_descriptions) ? detail.job_descriptions : [];
      const jobKeys = this.normalizeAssignmentQuizKeys(
        jobRows.flatMap((job) => (Array.isArray(job?.related_quizzes) ? job.related_quizzes : [])),
      );
      return this.normalizeAssignmentQuizKeys([...candidateKeys, ...jobKeys, ...fallbackKeys]);
    },

    async selectAssignmentCandidate(item) {
      this.assignmentForm.candidate_id = String(item?.id || "").trim();
      const quizKeys = await this.loadAssignmentCandidateDefaultQuizKeys(item);
      this.assignmentForm.quiz_keys = quizKeys;
      this.assignmentForm.quiz_key = quizKeys[0] || "";
      this.closeAssignmentSelect("candidate");
    },

    clearAssignmentCandidate() {
      this.assignmentForm.candidate_id = "";
      this.assignmentForm.quiz_key = "";
      this.assignmentForm.quiz_keys = [];
      this.closeAssignmentSelect("candidate");
    },

    resetAssignmentCandidateSelection() {
      this.assignmentForm.candidate_id = "";
      this.assignmentForm.quiz_key = "";
      this.assignmentForm.quiz_keys = [];
      this.assignmentSelect.candidate.open = false;
      this.assignmentSelect.candidate.query = "";
    },

    assignmentCandidateDefaultQuizLabel(item) {
      const quizKeys = this.assignmentCandidateDefaultQuizKeys(item);
      if (!quizKeys.length) return "";
      return quizKeys.map((quizKey) => this.quizOptionLabel(quizKey) || quizKey).join("、");
    },

    attemptReviewAnswers() {
      const answers = this.attemptDetail?.review?.answers;
      return Array.isArray(answers) ? answers : [];
    },

    attemptReviewEvaluation() {
      const evaluation = this.attemptDetail?.review?.evaluation;
      return evaluation && typeof evaluation === "object" ? evaluation : {};
    },

    attemptReviewQuestionKind(question) {
      const explicit = String(question?.review_kind || "").trim().toLowerCase();
      if (explicit) {
        return explicit;
      }
      const type = String(question?.type || "").trim().toLowerCase();
      if (type === "short") {
        return "short";
      }
      if (type === "single" || type === "multiple") {
        const options = Array.isArray(question?.options) ? question.options : [];
        const hasTraits = options.some((option) => this.optionTraits(option).length > 0);
        return hasTraits ? "traits" : "objective";
      }
      return "unknown";
    },

    attemptReviewIsShortQuestion(question) {
      return this.attemptReviewQuestionKind(question) === "short";
    },

    assignmentSuspectedAiQuestionIds(item) {
      const rawIds = item?.suspected_ai_question_ids;
      if (!Array.isArray(rawIds)) return [];
      const ids = [];
      const seen = new Set();
      rawIds.forEach((value) => {
        const qid = String(value || "").trim();
        if (!qid || seen.has(qid)) return;
        seen.add(qid);
        ids.push(qid);
      });
      return ids;
    },

    assignmentSuspectedAiQuestionCount(item) {
      const count = Number(item?.suspected_ai_question_count);
      if (Number.isFinite(count) && count >= 0) {
        return Math.floor(count);
      }
      return this.assignmentSuspectedAiQuestionIds(item).length;
    },

    assignmentHasCompletedGrading(item) {
      return this.assignmentStatusValue(item) === "finished";
    },

    attemptSuspectedAiQuestionIds() {
      return this.assignmentSuspectedAiQuestionIds(this.attemptDetail?.quiz_paper);
    },

    attemptSuspectedAiQuestionCount() {
      return this.assignmentSuspectedAiQuestionCount(this.attemptDetail?.quiz_paper);
    },

    attemptQuestionIsSuspectedAi(question) {
      const qid = String(question?.qid || "").trim();
      return Boolean(qid) && this.attemptSuspectedAiQuestionIds().includes(qid);
    },

    attemptSuspectedAiButtonClass(question) {
      const classes = ["attempt-suspected-ai-toggle"];
      if (this.attemptQuestionIsSuspectedAi(question)) {
        classes.push("attempt-suspected-ai-toggle--active");
      }
      return classes.join(" ");
    },

    async toggleAttemptSuspectedAi(question) {
      if (!this.attemptReviewIsShortQuestion(question)) return;
      const token = String(this.attemptDetail?.quiz_paper?.token || this.attemptDetail?.assignment?.token || "").trim();
      const qid = String(question?.qid || "").trim();
      if (!token || !qid) return;
      const suspected = !this.attemptQuestionIsSuspectedAi(question);
      const result = await this.api(`/api/admin/assignments/${encodeURIComponent(token)}/suspected-ai-question`, {
        method: "POST",
        body: JSON.stringify({ qid, suspected }),
        headers: { "Content-Type": "application/json" },
      });
      const updatedItem = result?.item;
      if (updatedItem && typeof updatedItem === "object") {
        this.applyAssignmentItemUpdate(updatedItem, { updateAttemptDetail: false });
        this.syncAttemptSuspectedAiQuestionState(updatedItem);
      } else {
        await this.loadAttemptDetail(token, { quiet: true });
      }
      this.showNotice(suspected ? "已标记为疑似 AI" : "已取消疑似 AI 标记");
    },

    attemptReviewIsTraitQuestion(question) {
      return this.attemptReviewQuestionKind(question) === "traits";
    },

    attemptReviewIsObjectiveQuestion(question) {
      return this.attemptReviewQuestionKind(question) === "objective";
    },

    attemptReviewHasOptions(question) {
      return Array.isArray(question?.options) && question.options.length > 0;
    },

    attemptReviewSelectedOptions(question) {
      const options = question?.selected_options;
      return Array.isArray(options)
        ? options.map((item) => String(item || "").trim()).filter(Boolean)
        : [];
    },

    attemptReviewCorrectOptions(question) {
      const options = question?.correct_options;
      return Array.isArray(options)
        ? options.map((item) => String(item || "").trim()).filter(Boolean)
        : [];
    },

    attemptReviewHasAnswer(question) {
      if (typeof question?.has_answer === "boolean") {
        return question.has_answer;
      }
      if (this.attemptReviewIsShortQuestion(question)) {
        return Boolean(String(question?.answer || "").trim());
      }
      return this.attemptReviewSelectedOptions(question).length > 0;
    },

    attemptReviewQuestionStatusLabel(question) {
      const hasAnswer = this.attemptReviewHasAnswer(question);
      if (this.attemptReviewIsTraitQuestion(question)) {
        return hasAnswer ? "已作答" : "未作答";
      }
      if (this.attemptReviewIsShortQuestion(question)) {
        if (!hasAnswer) {
          return "未作答";
        }
        return question?.has_score ? "已评分" : "待评分";
      }
      if (!hasAnswer) {
        return "未作答";
      }
      if (question?.is_correct) {
        return "回答正确";
      }
      if (question?.is_partial) {
        return "部分得分";
      }
      return "回答错误";
    },

    attemptReviewQuestionStatusClass(question) {
      const label = this.attemptReviewQuestionStatusLabel(question);
      const classes = [
        "inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-semibold",
      ];
      if (label === "回答正确") {
        classes.push("border-emerald-200 bg-emerald-50 text-emerald-700");
      } else if (label === "部分得分") {
        classes.push("border-amber-200 bg-amber-50 text-amber-700");
      } else if (label === "回答错误") {
        classes.push("border-rose-200 bg-rose-50 text-rose-700");
      } else if (label === "已评分") {
        classes.push("border-blue-200 bg-blue-50 text-blue-700");
      } else if (label === "待评分") {
        classes.push("border-amber-200 bg-amber-50 text-amber-700");
      } else if (label === "已作答") {
        classes.push("border-sky-200 bg-sky-50 text-sky-700");
      } else {
        classes.push("border-slate-200 bg-slate-50 text-slate-600");
      }
      return classes.join(" ");
    },

    attemptReviewOptionIsSelected(question, option) {
      const key = String(option?.key || "").trim();
      return Boolean(key) && this.attemptReviewSelectedOptions(question).includes(key);
    },

    attemptReviewOptionIsCorrect(question, option) {
      const key = String(option?.key || "").trim();
      return Boolean(key) && this.attemptReviewCorrectOptions(question).includes(key);
    },

    attemptReviewOptionRowClass(question, option) {
      const classes = [];
      const selected = this.attemptReviewOptionIsSelected(question, option);
      const correct = this.attemptReviewOptionIsCorrect(question, option);
      if (this.attemptReviewIsTraitQuestion(question)) {
        classes.push(selected ? "bg-sky-50/85" : "bg-slate-50/80");
      } else if (selected && correct) {
        classes.push("bg-emerald-50/85");
      } else if (selected) {
        classes.push("bg-rose-50/80");
      } else if (correct) {
        classes.push("bg-emerald-50/60");
      } else {
        classes.push("bg-slate-50/80");
      }
      return classes.join(" ");
    },

    attemptReviewOptionSelectionBadgeClass(question, option) {
      const classes = ["shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-semibold"];
      if (this.attemptReviewIsTraitQuestion(question)) {
        classes.push("border-sky-200 bg-white text-sky-700");
      } else if (this.attemptReviewOptionIsCorrect(question, option)) {
        classes.push("border-emerald-200 bg-white text-emerald-700");
      } else {
        classes.push("border-rose-200 bg-white text-rose-700");
      }
      return classes.join(" ");
    },

    attemptReviewShortAnswerText(question) {
      const answer = question?.answer;
      if (typeof answer === "string") {
        return answer.trim() || "未作答";
      }
      if (Array.isArray(answer)) {
        return answer.length ? answer.join("、") : "未作答";
      }
      if (answer === null || answer === undefined || answer === "") {
        return "未作答";
      }
      try {
        return JSON.stringify(answer, null, 2);
      } catch (_error) {
        return String(answer);
      }
    },

    attemptEvaluationResultModeLabel() {
      const evaluation = this.attemptReviewEvaluation();
      if (String(evaluation?.result_mode_label || "").trim()) {
        return String(evaluation.result_mode_label).trim();
      }
      const mapping = {
        scored: "计分题",
        traits: "量表题",
        mixed: "计分 + 量表",
      };
      const key = String(evaluation?.result_mode || "").trim().toLowerCase();
      return mapping[key] || "未定义";
    },

    attemptEvaluationPrimaryDimensions() {
      const evaluation = this.attemptReviewEvaluation();
      const items = evaluation?.primary_dimensions || evaluation?.traits?.primary_dimensions;
      return Array.isArray(items) ? items.map((item) => String(item || "").trim()).filter(Boolean) : [];
    },

    attemptEvaluationTraitPairs() {
      const evaluation = this.attemptReviewEvaluation();
      const items = evaluation?.paired_dimensions || evaluation?.traits?.paired_dimensions;
      return Array.isArray(items) ? items : [];
    },

    attemptEvaluationDimensionList() {
      const evaluation = this.attemptReviewEvaluation();
      const items = evaluation?.dimension_list || evaluation?.traits?.dimension_list;
      return Array.isArray(items) ? items : [];
    },

    attemptEvaluationShowScore() {
      const evaluation = this.attemptReviewEvaluation();
      if (typeof evaluation?.has_score === "boolean") {
        return evaluation.has_score;
      }
      return Boolean(String(evaluation?.score_display || "").trim()) && String(evaluation?.result_mode || "").trim() !== "traits";
    },

    attemptEvaluationHasContent() {
      const evaluation = this.attemptReviewEvaluation();
      return Boolean(
        String(evaluation?.final_analysis || "").trim()
        || String(evaluation?.candidate_remark || "").trim()
        || String(evaluation?.score_display || "").trim()
        || String(evaluation?.result_mode || "").trim()
        || this.attemptEvaluationPrimaryDimensions().length
        || this.attemptEvaluationTraitPairs().length
        || this.attemptEvaluationDimensionList().length
      );
    },

    unhandledAssignmentCount() {
      return Math.max(0, Number(this.assignments?.summary?.unhandled_finished_count || 0));
    },

    assignmentSurfaceStateClass(item) {
      switch (this.assignmentStatusValue(item)) {
        case "in_quiz":
          return "assignment-surface--in-quiz";
        case "grading":
          return "assignment-surface--grading";
        case "finished":
          return "assignment-surface--finished";
        case "expired":
          return "assignment-surface--expired";
        case "invited":
        case "verified":
          return "assignment-surface--waiting";
        default:
          return "";
      }
    },

    assignmentCardClass(item) {
      const classes = [];
      const stateClass = this.assignmentSurfaceStateClass(item);
      if (stateClass) {
        classes.push(stateClass);
      }
      if (item?.needs_attention) {
        classes.push("assignment-card--attention");
      }
      return classes.join(" ");
    },

    assignmentDetailSectionClass(item) {
      const classes = [];
      const stateClass = this.assignmentSurfaceStateClass(item);
      if (stateClass) {
        classes.push(stateClass);
      }
      if (item?.needs_attention) {
        classes.push("assignment-detail-panel--attention");
      }
      return classes.join(" ");
    },

    assignmentSourceBadgeClass() {
      return "assignment-badge assignment-badge--source";
    },

    assignmentStatusBadgeClass(item) {
      const classes = ["assignment-badge"];
      switch (this.assignmentStatusValue(item)) {
        case "in_quiz":
          classes.push("assignment-badge--status-active");
          break;
        case "grading":
          classes.push("assignment-badge--status-grading");
          break;
        case "finished":
          classes.push("assignment-badge--status-finished");
          break;
        case "expired":
          classes.push("assignment-badge--status-expired");
          break;
        case "invited":
        case "verified":
          classes.push("assignment-badge--status-waiting");
          break;
        default:
          classes.push("assignment-badge--status-neutral");
          break;
      }
      return classes.join(" ");
    },

    assignmentHandlingBadgeClass(item) {
      return item?.needs_attention
        ? "assignment-badge assignment-badge--attention"
        : "assignment-badge assignment-badge--handled";
    },

    assignmentIgnoresTiming(item) {
      return Boolean(item?.ignore_timing);
    },

    canToggleAssignmentHandling(item) {
      return String(item?.status || "").trim() === "finished";
    },

    assignmentHandlingLabel(item) {
      if (!this.canToggleAssignmentHandling(item)) return "";
      return item?.needs_attention ? "未处理" : "已处理";
    },

    assignmentHandlingActionLabel(item) {
      return item?.needs_attention ? "标记已处理" : "取消已处理";
    },

    assignmentActionButtonClass(kind, item) {
      const classes = ["assignment-action"];
      if (kind === "detail") {
        classes.push("assignment-action--primary");
      } else if (kind === "danger") {
        classes.push("assignment-action--danger");
      } else if (kind === "handling" && item?.needs_attention) {
        classes.push("assignment-action--warning");
      } else {
        classes.push("assignment-action--secondary");
      }
      return classes.join(" ");
    },

    assignmentHandledMeta(item) {
      const handledAt = String(item?.handled_at || "").trim();
      if (!handledAt) return "";
      const parts = [];
      const handledBy = String(item?.handled_by || "").trim();
      if (handledBy) {
        parts.push(handledBy);
      }
      const displayTime = this.formatDateTime(handledAt);
      if (displayTime) {
        parts.push(displayTime);
      }
      return parts.join(" · ");
    },

    assignmentQuizKey(item) {
      return String(item?.quiz_key || "").trim();
    },

    assignmentQuizTitle(item) {
      const quizKey = this.assignmentQuizKey(item);
      const directTitle = String(item?.quiz_title || item?.quiz_name || "").trim();
      if (directTitle) return directTitle;
      const matched = this.quizOptionByKey(quizKey);
      return String(matched?.title || "").trim() || quizKey || "未知测验";
    },

    assignmentQuizKeyTitle(item) {
      const quizKey = this.assignmentQuizKey(item);
      return quizKey || "暂无编号";
    },

    formatAnswerTime(seconds) {
      const value = Number(seconds || 0);
      if (!Number.isFinite(value) || value <= 0) {
        return "";
      }
      const totalSeconds = Math.max(0, Math.round(value));
      const hours = Math.floor(totalSeconds / 3600);
      const minutes = Math.floor((totalSeconds % 3600) / 60);
      const remainderSeconds = totalSeconds % 60;
      const parts = [];
      if (hours) {
        parts.push(`${hours}小时`);
      }
      if (minutes) {
        parts.push(`${minutes}分`);
      }
      if (remainderSeconds || !parts.length) {
        parts.push(`${remainderSeconds}秒`);
      }
      return parts.join("");
    },

    canDeleteAssignment(item) {
      return Boolean(String(item?.token || "").trim());
    },

    async copyAssignmentUrl(item) {
      await this.copyText(item?.url, "邀约链接已复制");
    },

    async copyAssignmentQuizKey(item) {
      const quizKey = this.assignmentQuizKey(item);
      if (!quizKey) return;
      await this.copyText(quizKey, "编号已复制");
    },

    async copyAssignmentQr(item) {
      const qrPath = String(item?.qr_url || "").trim();
      if (!qrPath) return;
      const qrUrl = new URL(qrPath, window.location.origin).toString();
      try {
        if (!navigator.clipboard?.write || typeof window.ClipboardItem === "undefined") {
          throw new Error("image clipboard unsupported");
        }
        const response = await fetch(qrUrl, { credentials: "same-origin" });
        if (!response.ok) {
          throw new Error("qr fetch failed");
        }
        const blob = await response.blob();
        await navigator.clipboard.write([
          new window.ClipboardItem({
            [blob.type || "image/png"]: blob,
          }),
        ]);
        this.showNotice("二维码图片已复制");
      } catch (_error) {
        await this.copyText(qrUrl, "当前环境不支持复制图片，已复制二维码地址");
      }
    },

    async toggleAssignmentHandling(item) {
      if (!this.canToggleAssignmentHandling(item)) return;
      const token = String(item?.token || "").trim();
      if (!token) return;
      const handled = Boolean(item?.needs_attention);
      const result = await this.api(`/api/admin/assignments/${encodeURIComponent(token)}/handling`, {
        method: "POST",
        body: JSON.stringify({ handled }),
        headers: { "Content-Type": "application/json" },
      });
      const updatedItem = result?.item;
      if (updatedItem && typeof updatedItem === "object") {
        this.applyAssignmentItemUpdate(updatedItem);
        if (this.route.name === "assignments" && this.filters.assignments.handled) {
          await this.loadAssignments({ quiet: true, page: this.currentAssignmentsPage() });
        }
      } else {
        await this.loadAssignments({ quiet: true });
        if (String(this.attemptDetail?.quiz_paper?.token || "").trim() === token) {
          await this.loadAttemptDetail(token, { quiet: true });
        }
      }
      this.showNotice(handled ? "已标记为已处理" : "已取消已处理");
    },

    async deleteAssignment(item) {
      if (!this.canDeleteAssignment(item)) return;
      const token = String(item?.token || "").trim();
      if (!token) return;
      const name = String(item?.candidate_name || "").trim() || "该邀约";
      if (!window.confirm(`确定删除 ${name} 的邀约吗？这会同时删除该次答题归档，不影响候选人和简历。`)) return;
      await this.api(`/api/admin/assignments/${encodeURIComponent(token)}`, { method: "DELETE" });
      this.showNotice("邀约已删除");
      const currentToken = String(this.attemptDetail?.quiz_paper?.token || this.attemptDetail?.assignment?.token || "").trim();
      if (currentToken === token) {
        await this.handleRoute("/admin/assignments");
        return;
      }
      await this.loadAssignments({ page: this.currentAssignmentsPage() });
    },

    updateAssignmentSummaryCount(previousItem, nextItem) {
      const previousNeedsAttention = Boolean(previousItem?.needs_attention);
      const nextNeedsAttention = Boolean(nextItem?.needs_attention);
      if (previousNeedsAttention === nextNeedsAttention) return;
      if (!this.assignments.summary || typeof this.assignments.summary !== "object") {
        this.assignments.summary = { unhandled_finished_count: 0 };
      }
      const current = Number(this.assignments.summary.unhandled_finished_count || 0);
      this.assignments.summary.unhandled_finished_count = Math.max(0, current + (nextNeedsAttention ? 1 : -1));
    },

    applyAssignmentItemUpdate(updatedItem, { updateAttemptDetail = true } = {}) {
      const token = String(updatedItem?.token || "").trim();
      if (!token) return;
      const items = Array.isArray(this.assignments?.items) ? this.assignments.items : [];
      const nextItems = items.map((item) => {
        if (String(item?.token || "").trim() !== token) {
          return item;
        }
        this.updateAssignmentSummaryCount(item, updatedItem);
        return { ...item, ...updatedItem };
      });
      this.assignments = {
        ...(this.assignments || {}),
        items: nextItems,
        summary: this.assignments?.summary || { unhandled_finished_count: 0 },
      };
      if (updateAttemptDetail && String(this.attemptDetail?.quiz_paper?.token || "").trim() === token) {
        this.attemptDetail = {
          ...(this.attemptDetail || {}),
          quiz_paper: { ...(this.attemptDetail?.quiz_paper || {}), ...updatedItem },
        };
      }
    },

    syncAttemptSuspectedAiQuestionState(updatedItem) {
      const token = String(updatedItem?.token || "").trim();
      const currentToken = String(this.attemptDetail?.quiz_paper?.token || this.attemptDetail?.assignment?.token || "").trim();
      if (!token || token !== currentToken) return;
      const questionIds = this.assignmentSuspectedAiQuestionIds(updatedItem);
      const count = this.assignmentSuspectedAiQuestionCount(updatedItem);
      const quizPaper = this.attemptDetail?.quiz_paper;
      if (!quizPaper || typeof quizPaper !== "object") return;
      quizPaper.suspected_ai_question_ids = questionIds;
      quizPaper.suspected_ai_question_count = count;
    },

    syncStatus() {
      return String(this.syncState?.status || "").trim().toLowerCase();
    },

    isSyncBusy() {
      return ["queued", "pending", "running"].includes(this.syncStatus());
    },

    hasRepoBinding() {
      return Boolean(String(this.repoBinding?.repo_url || "").trim());
    },

    resetRebindForm() {
      this.rebindForm = { open: false, repoUrl: "", confirmationText: "" };
    },

    openRebindForm() {
      if (this.isSyncBusy()) return;
      this.rebindForm.open = true;
      this.rebindForm.repoUrl = "";
      this.rebindForm.confirmationText = "";
    },

    closeRebindForm() {
      this.resetRebindForm();
    },

    stopSyncPolling() {
      if (!this.syncPollTimer) return;
      window.clearTimeout(this.syncPollTimer);
      this.syncPollTimer = null;
    },

    currentAssignmentsPage() {
      const page = Number(this.assignments?.page || 1);
      if (!Number.isFinite(page) || page <= 0) {
        return 1;
      }
      return Math.floor(page);
    },

    assignmentTotalPages() {
      const totalPages = Number(this.assignments?.total_pages || 1);
      if (!Number.isFinite(totalPages) || totalPages <= 0) {
        return 1;
      }
      return Math.floor(totalPages);
    },

    assignmentsHavePagination() {
      return this.assignmentTotalPages() > 1;
    },

    canGoToPreviousAssignmentsPage() {
      return this.currentAssignmentsPage() > 1;
    },

    canGoToNextAssignmentsPage() {
      return this.currentAssignmentsPage() < this.assignmentTotalPages();
    },

    assignmentPaginationButtonClass(disabled) {
      const classes = [
        "inline-flex items-center justify-center rounded-xl border px-3 py-2 text-xs font-semibold transition",
      ];
      if (disabled) {
        classes.push("cursor-not-allowed border-slate-200 bg-slate-100 text-slate-300");
      } else {
        classes.push("border-blue-100 bg-white text-slate-700 hover:bg-blue-50 hover:text-blue-700");
      }
      return classes.join(" ");
    },

    assignmentFilterSegmentClass(active) {
      const classes = [
        "inline-flex h-7 flex-1 items-center justify-center rounded-md px-2 text-xs font-semibold transition",
      ];
      if (active) {
        classes.push("bg-blue-600 text-white shadow-sm");
      } else {
        classes.push("text-slate-600 hover:bg-blue-50 hover:text-blue-700");
      }
      return classes.join(" ");
    },

    setAssignmentHandledFilter(value) {
      const handled = String(value || "").trim().toLowerCase();
      this.filters.assignments.handled = ["handled", "unhandled"].includes(handled) ? handled : "";
      return this.reloadAssignmentsFromFirstPage();
    },

    resetAssignmentFilters() {
      Object.assign(this.filters.assignments, {
        q: "",
        status: "",
        handled: "",
        quiz_key: "",
        start_from: "",
        end_to: "",
      });
      return this.reloadAssignmentsFromFirstPage();
    },

    normalizeAssignmentsPage(page, fallback = 1) {
      const candidate = Number(page);
      const fallbackPage = Number(fallback);
      if (!Number.isFinite(candidate) || candidate <= 0) {
        if (!Number.isFinite(fallbackPage) || fallbackPage <= 0) {
          return 1;
        }
        return Math.max(1, Math.floor(fallbackPage));
      }
      return Math.max(1, Math.floor(candidate));
    },

    async changeAssignmentsPage(page) {
      const nextPage = this.normalizeAssignmentsPage(page, this.currentAssignmentsPage());
      if (nextPage === this.currentAssignmentsPage()) {
        return;
      }
      this.closeAssignmentShare({ restoreFocus: false });
      this.assignments.page = nextPage;
      this.syncAdminListRoute({ replace: false });
      await this.loadAssignments({ page: nextPage });
    },

    async reloadAssignmentsFromFirstPage({ replace = false } = {}) {
      this.closeAssignmentShare({ restoreFocus: false });
      this.assignments.page = 1;
      this.syncAdminListRoute({ replace });
      await this.loadAssignments({ page: 1 });
    },

    scheduleAssignmentsReloadFromFirstPage() {
      window.clearTimeout(this.assignmentsFilterTimer);
      this.assignmentsFilterTimer = window.setTimeout(() => {
        this.reloadAssignmentsFromFirstPage({ replace: true });
      }, 220);
    },

    assignmentStatusValue(item) {
      return String(item?.status || "").trim().toLowerCase();
    },

    assignmentStatusSnapshotMap(items) {
      const out = {};
      for (const item of Array.isArray(items) ? items : []) {
        const token = String(item?.token || "").trim();
        if (!token) continue;
        out[token] = this.assignmentStatusValue(item);
      }
      return out;
    },

    notifyAssignmentTransitions(nextItems, previousSnapshot) {
      const startedGradingItems = [];
      const finishedItems = [];
      for (const item of Array.isArray(nextItems) ? nextItems : []) {
        const token = String(item?.token || "").trim();
        if (!token) continue;
        const previousStatus = String(previousSnapshot?.[token] || "").trim().toLowerCase();
        const nextStatus = this.assignmentStatusValue(item);
        if (previousStatus && previousStatus !== "grading" && previousStatus !== "finished" && nextStatus === "grading") {
          startedGradingItems.push(item);
          continue;
        }
        if (previousStatus === "grading" && nextStatus === "finished") {
          finishedItems.push(item);
        }
      }
      if (startedGradingItems.length === 1) {
        const candidateName = String(startedGradingItems[0]?.candidate_name || "").trim() || "该答题";
        this.showNotice(`${candidateName} 已结束答题，正在判卷`);
      } else if (startedGradingItems.length > 1) {
        this.showNotice(`${startedGradingItems.length} 条答题记录已结束，正在判卷`);
      }
      if (finishedItems.length === 1) {
        const candidateName = String(finishedItems[0]?.candidate_name || "").trim() || "该答题";
        this.showNotice(`${candidateName} 的判卷已结束`);
        return;
      }
      if (finishedItems.length > 1) {
        this.showNotice(`${finishedItems.length} 条答题记录已完成判卷`);
      }
    },

    stopAssignmentsPolling() {
      if (!this.assignmentsPollTimer) return;
      window.clearTimeout(this.assignmentsPollTimer);
      this.assignmentsPollTimer = null;
    },

    scheduleAssignmentsPolling() {
      if (this.assignmentsPollTimer || !this.session.authenticated) return;
      if (!["assignments", "attempt-detail"].includes(this.route.name)) return;
      this.assignmentsPollTimer = window.setTimeout(async () => {
        this.assignmentsPollTimer = null;
        if (!this.session.authenticated) return;
        if (this.route.name === "assignments") {
          await this.loadAssignments({ quiet: true, source: "assignments-poll" });
          return;
        }
        if (this.route.name === "attempt-detail") {
          await this.loadAttemptDetail(this.route.params.token, { quiet: true, source: "assignments-poll" });
        }
      }, this.assignmentsPollIntervalMs);
    },

    scheduleSyncPolling() {
      if (this.syncPollTimer || !this.isSyncBusy() || this.route.name !== "quizzes") return;
      this.syncPollTimer = window.setTimeout(async () => {
        this.syncPollTimer = null;
        if (this.route.name !== "quizzes" || !this.session.authenticated) return;
        const previousSyncStatus = this.syncStatus();
        const previousSyncJobId = String(this.syncState?.last_job_id || "").trim();
        await this.loadQuizzes({
          quiet: true,
          source: "sync-poll",
          previousSyncStatus,
          previousSyncJobId,
        });
      }, this.syncPollIntervalMs);
    },

    async loadAssignments({ quiet = false, source = "manual", page = null } = {}) {
      const requestId = ++listRequestId;
      const query = new URLSearchParams();
      const nextPage = this.normalizeAssignmentsPage(page, this.assignments?.page || 1);
      query.set("page", String(nextPage));
      if (this.filters.assignments.q) query.set("q", this.filters.assignments.q);
      if (this.filters.assignments.status) query.set("status", this.filters.assignments.status);
      if (this.filters.assignments.handled) query.set("handled", this.filters.assignments.handled);
      if (this.filters.assignments.quiz_key) query.set("quiz_key", this.filters.assignments.quiz_key);
      if (this.filters.assignments.start_from) query.set("start_from", this.filters.assignments.start_from);
      if (this.filters.assignments.end_to) query.set("end_to", this.filters.assignments.end_to);
      const previousSnapshot = { ...(this.assignmentStatusSnapshot || {}) };
      const data = await this.api(`/api/admin/assignments?${query.toString()}`, { quiet });
      if (!data || requestId !== listRequestId) return;
      const nextItems = Array.isArray(data?.items) ? data.items : [];
      if (this.assignmentShareToken && !nextItems.some((item) => String(item.token || "") === this.assignmentShareToken)) {
        this.closeAssignmentShare({ restoreFocus: false });
      }
      this.assignments = {
        ...(this.assignments || {}),
        items: nextItems,
        summary: data?.summary || { unhandled_finished_count: 0 },
        ...data,
      };
      this.assignmentStatusSnapshot = this.assignmentStatusSnapshotMap(nextItems);
      if (source !== "assignments-poll" && this.route.name === "assignments") this.syncAdminListRoute({ replace: true });
      if (source === "assignments-poll") {
        this.notifyAssignmentTransitions(nextItems, previousSnapshot);
      }
      if (this.route.name === "assignments") {
        this.scheduleAssignmentsPolling();
      }
    },

    async createAssignment() {
      const quizKeys = this.selectedAssignmentQuizKeys();
      const payload = {
        ...this.assignmentForm,
        quiz_key: quizKeys[0] || "",
        quiz_keys: quizKeys,
        candidate_id: Number(this.assignmentForm.candidate_id),
        require_phone_verification: Boolean(this.assignmentForm.require_phone_verification),
        ignore_timing: Boolean(this.assignmentForm.ignore_timing),
      };
      const result = await this.api("/api/admin/assignments", {
        method: "POST",
        body: JSON.stringify(payload),
        headers: { "Content-Type": "application/json" },
      });
      const inviteStartDate = String(payload.invite_start_date || "").trim();
      const inviteEndDate = String(payload.invite_end_date || "").trim();
      if (inviteStartDate) {
        const currentStart = String(this.filters.assignments.start_from || "").trim();
        if (!currentStart || currentStart > inviteStartDate) {
          this.filters.assignments.start_from = inviteStartDate;
        }
      }
      if (inviteEndDate) {
        const currentEnd = String(this.filters.assignments.end_to || "").trim();
        if (!currentEnd || currentEnd < inviteEndDate) {
          this.filters.assignments.end_to = inviteEndDate;
        }
      }
      this.assignmentForm.ignore_timing = false;
      this.resetAssignmentCandidateSelection();
      const createdCount = Number(result?.created_count || (Array.isArray(result?.items) ? result.items.length : 1));
      this.showNotice(createdCount > 1 ? `已创建 ${createdCount} 个邀约` : `邀约已创建：${result.url}`);
      await this.loadAssignments({ page: 1 });
    },

    async loadAttemptDetail(token, { quiet = false, source = "manual" } = {}) {
      const requestId = ++detailRequestId;
      const currentToken = String(token || "").trim();
      const previousStatus = this.assignmentStatusValue(this.attemptDetail?.quiz_paper);
      if (String(this.attemptDetail?.quiz_paper?.token || "") !== currentToken) {
        this.attemptDetail = { assignment: {}, quiz_paper: {}, archive: {}, review: { answers: [], evaluation: {} } };
      }
      const data = await this.api(`/api/admin/attempts/${encodeURIComponent(currentToken)}`, { quiet });
      if (!data || requestId !== detailRequestId || this.route.name !== "attempt-detail" || this.route.params.token !== currentToken) return;
      const nextDetail = {
        assignment: data?.assignment || {},
        quiz_paper: data?.quiz_paper || {},
        archive: data?.archive || {},
        review: data?.review || { answers: [], evaluation: {} },
      };
      const detailChanged = JSON.stringify(this.attemptDetail) !== JSON.stringify(nextDetail);
      if (detailChanged) {
        this.attemptDetail = nextDetail;
      }
      const nextStatus = this.assignmentStatusValue(nextDetail.quiz_paper);
      if (currentToken) {
        this.assignmentStatusSnapshot = {
          ...(this.assignmentStatusSnapshot || {}),
          [currentToken]: nextStatus,
        };
      }
      if (source === "assignments-poll") {
        const candidateName = String(this.attemptDetail?.quiz_paper?.candidate_name || "").trim() || "该答题";
        if (previousStatus && previousStatus !== "grading" && previousStatus !== "finished" && nextStatus === "grading") {
          this.showNotice(`${candidateName} 已结束答题，正在判卷`);
        } else if (previousStatus === "grading" && nextStatus === "finished") {
          this.showNotice(`${candidateName} 的判卷已结束`);
        }
      }
      if (this.route.name === "attempt-detail") {
        this.scheduleAssignmentsPolling();
      }
      if (detailChanged) {
        await this.$nextTick();
        this.queueMathTypeset();
      }
    },

  };
}
