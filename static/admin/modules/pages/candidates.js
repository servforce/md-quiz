import { RESUME_PARSE_META, RESUME_PHASE_META, createCandidateResumeUploadState, createCandidateResumeReparseState } from "../constants.js";

export function createAdminCandidatesModule() {
  return {
    isSupportedResumeFile(file) {
      const name = String(file?.name || "").trim().toLowerCase();
      if (!name) return false;
      return [".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"].some((ext) => name.endsWith(ext));
    },

    jobDescriptionOptionLabel(item) {
      return String(item?.title || "").trim() || `职位 #${item?.id || ""}`;
    },

    async loadCandidateJobDescriptionOptions({ quiet = true } = {}) {
      const data = await this.api("/api/admin/job-descriptions/options", { quiet });
      if (!data) return;
      this.candidateJobDescriptionOptions = Array.isArray(data?.items) ? data.items : [];
    },

    selectedCandidateJobDescriptionId() {
      const value = Number(this.candidateForm?.job_description_id || 0);
      return Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
    },

    selectedResumeUploadJobDescriptionId() {
      const value = Number(this.candidateResumeUploadForm?.job_description_id || 0);
      return Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
    },

    candidateJobDescriptions() {
      const items = this.candidateDetail?.job_descriptions;
      return Array.isArray(items) ? items.filter((item) => item && Number(item.id || 0) > 0) : [];
    },

    candidateJobDescriptionIds() {
      return new Set(this.candidateJobDescriptions().map((item) => Number(item.id || 0)).filter((id) => id > 0));
    },

    candidateAvailableJobDescriptionOptions() {
      const linked = this.candidateJobDescriptionIds();
      const options = Array.isArray(this.candidateJobDescriptionOptions) ? this.candidateJobDescriptionOptions : [];
      return options.filter((item) => {
        const id = Number(item?.id || 0);
        return id > 0 && !linked.has(id);
      });
    },

    candidateJobDescriptionSelected(id) {
      const numericId = Number(id || 0);
      return (this.candidateJobDescriptionAddSelection || []).map((item) => Number(item || 0)).includes(numericId);
    },

    toggleCandidateJobDescriptionSelection(id) {
      const numericId = Number(id || 0);
      if (!Number.isFinite(numericId) || numericId <= 0) return;
      const selected = (this.candidateJobDescriptionAddSelection || []).map((item) => Number(item || 0)).filter((item) => item > 0);
      if (selected.includes(numericId)) {
        this.candidateJobDescriptionAddSelection = selected.filter((item) => item !== numericId);
      } else {
        this.candidateJobDescriptionAddSelection = [...selected, numericId];
      }
    },

    async addCandidateJobDescriptions() {
      const ids = (this.candidateJobDescriptionAddSelection || []).map((item) => Number(item || 0)).filter((item) => item > 0);
      if (!this.candidateDetail?.candidate?.id || !ids.length) {
        this.showNotice("请选择要增加的职位");
        return;
      }
      this.candidateDetail = await this.api(`/api/admin/candidates/${this.candidateDetail.candidate.id}/job-descriptions`, {
        method: "POST",
        body: JSON.stringify({ job_description_ids: ids }),
        headers: { "Content-Type": "application/json" },
      });
      this.candidateJobDescriptionAddSelection = [];
      this.showNotice("候选人职位已更新");
    },

    async removeCandidateJobDescription(item) {
      const candidateId = Number(this.candidateDetail?.candidate?.id || 0);
      const jobDescriptionId = Number(item?.id || 0);
      if (!candidateId || !jobDescriptionId) return;
      const title = String(item?.title || "").trim() || `职位 #${jobDescriptionId}`;
      if (!window.confirm(`确定取消关联「${title}」吗？`)) return;
      try {
        this.candidateDetail = await this.api(`/api/admin/candidates/${candidateId}/job-descriptions/${jobDescriptionId}`, {
          method: "DELETE",
        });
      } catch (error) {
        if (String(error?.message || "").trim() !== "Not Found") {
          throw error;
        }
        this.candidateDetail = await this.api(`/api/admin/candidates/${candidateId}/job-descriptions/remove`, {
          method: "POST",
          body: JSON.stringify({ job_description_id: jobDescriptionId }),
          headers: { "Content-Type": "application/json" },
        });
      }
      this.candidateJobDescriptionAddSelection = [];
      this.showNotice("已取消职位关联");
    },

    candidateResumeParsedData() {
      const details = this.candidateDetail?.resume_parsed?.details;
      const data = details?.data;
      return data && typeof data === "object" ? data : {};
    },

    candidateResumeStatus() {
      const status = String(this.candidateDetail?.profile?.details_status || this.candidateDetail?.resume_parsed?.details?.status || "").trim().toLowerCase();
      return status || "empty";
    },

    candidateResumeStatusMeta() {
      return RESUME_PARSE_META[this.candidateResumeStatus()] || RESUME_PARSE_META.empty;
    },

    candidateResumeStatusBadgeStyle() {
      const meta = this.candidateResumeStatusMeta();
      return {
        borderColor: meta.border,
        backgroundColor: meta.background,
        color: meta.text,
      };
    },

    candidateResumeMethodLabel() {
      const method = this.candidateDetail?.resume_parsed?.method;
      if (!method || typeof method !== "object") return "";
      const labels = {
        llm_attachment: "附件推理",
        llm: "模型抽取",
        fast: "规则识别",
      };
      const entries = Object.entries(method)
        .map(([key, value]) => [String(key || "").trim(), String(value || "").trim()])
        .filter(([key, value]) => key && value);
      if (!entries.length) return "";
      const uniqueValues = [...new Set(entries.map(([, value]) => value))];
      if (uniqueValues.length === 1) {
        return labels[uniqueValues[0]] || uniqueValues[0];
      }
      return entries
        .map(([key, value]) => {
          const keyLabel = key === "identity" ? "身份" : key === "name" ? "姓名" : key === "details" ? "详情" : key;
          return `${keyLabel}:${labels[value] || value}`;
        })
        .join(" / ");
    },

    candidateResumeConfidenceItems() {
      const confidence = this.candidateDetail?.resume_parsed?.confidence;
      if (!confidence || typeof confidence !== "object") return [];
      const items = [
        { label: "姓名置信度", value: Number(confidence.name || 0) },
        { label: "手机置信度", value: Number(confidence.phone || 0) },
      ];
      return items.filter((item) => Number.isFinite(item.value) && item.value > 0);
    },

    candidateResumeSummary() {
      const profileSummary = String(this.candidateDetail?.profile?.evaluation_llm || "").trim();
      if (profileSummary) return profileSummary;
      const evaluation = String(this.candidateResumeParsedData().evaluation || "").trim();
      if (evaluation) return evaluation;
      return String(this.candidateResumeParsedData().summary || "").trim();
    },

    candidateResumeJobMatchScore() {
      const raw = this.candidateDetail?.profile?.job_match_score ?? this.candidateResumeParsedData().job_match_score;
      if (raw === null || raw === undefined || raw === "") return null;
      const score = Number(raw);
      if (!Number.isFinite(score)) return null;
      return Math.max(0, Math.min(100, Math.round(score)));
    },

    candidateResumeJobMatchScoreText() {
      const score = this.candidateResumeJobMatchScore();
      return score === null ? "" : String(score);
    },

    syncCandidateResumeEvaluationForm() {
      this.candidateResumeEvaluationForm = {
        evaluation: this.candidateResumeSummary(),
        job_match_score: this.candidateResumeJobMatchScoreText(),
        saving: false,
        editing: false,
        scoreEditing: false,
      };
    },

    candidateResumeEvaluationSourceLabel() {
      const label = String(this.candidateDetail?.profile?.evaluation_source_label || "").trim();
      if (label) return label;
      return this.candidateResumeSummary() ? "系统生成" : "";
    },

    candidateResumeEvaluationSourceClass() {
      const source = String(this.candidateDetail?.profile?.evaluation_source || "").trim();
      const classes = [
        "rounded-full border px-2.5 py-1 text-[11px] font-semibold leading-5",
      ];
      if (source === "manual") {
        classes.push("border-amber-200 bg-amber-50 text-amber-700");
      } else {
        classes.push("border-slate-200 bg-slate-50 text-slate-600");
      }
      return classes.join(" ");
    },

    normalizeCandidateResumeJobMatchScoreInput() {
      const raw = String(this.candidateResumeEvaluationForm?.job_match_score ?? "").trim();
      if (!raw) return null;
      if (!/^\d+$/.test(raw)) {
        throw new Error("匹配度必须是 0-100 的整数");
      }
      const score = Number(raw);
      if (!Number.isFinite(score) || score < 0 || score > 100) {
        throw new Error("匹配度必须是 0-100 的整数");
      }
      return Math.round(score);
    },

    async saveCandidateResumeEvaluation() {
      const candidateId = Number(this.candidateDetail?.candidate?.id || 0);
      if (!candidateId || this.candidateResumeEvaluationForm?.saving) return;
      let jobMatchScore = null;
      try {
        jobMatchScore = this.normalizeCandidateResumeJobMatchScoreInput();
      } catch (error) {
        this.showNotice(error.message || "匹配度格式不正确");
        return;
      }
      this.candidateResumeEvaluationForm = {
        ...this.candidateResumeEvaluationForm,
        saving: true,
      };
      try {
        this.candidateDetail = await this.api(`/api/admin/candidates/${candidateId}/resume/evaluation`, {
          method: "POST",
          body: JSON.stringify({
            evaluation: String(this.candidateResumeEvaluationForm?.evaluation || ""),
            job_match_score: jobMatchScore,
          }),
          headers: { "Content-Type": "application/json" },
        });
        this.syncCandidateResumeEvaluationForm();
        this.showNotice("简历评价已保存");
      } finally {
        this.candidateResumeEvaluationForm = {
          ...this.candidateResumeEvaluationForm,
          saving: false,
        };
      }
    },

    beginCandidateResumeEvaluationEdit() {
      if (this.candidateResumeEvaluationForm?.saving) return;
      this.candidateResumeEvaluationForm = {
        ...this.candidateResumeEvaluationForm,
        evaluation: this.candidateResumeSummary(),
        editing: true,
      };
    },

    cancelCandidateResumeEvaluationEdit() {
      if (this.candidateResumeEvaluationForm?.saving) return;
      this.candidateResumeEvaluationForm = {
        ...this.candidateResumeEvaluationForm,
        evaluation: this.candidateResumeSummary(),
        editing: false,
      };
    },

    beginCandidateResumeScoreEdit() {
      if (this.candidateResumeEvaluationForm?.saving) return;
      this.candidateResumeEvaluationForm = {
        ...this.candidateResumeEvaluationForm,
        job_match_score: this.candidateResumeJobMatchScoreText(),
        scoreEditing: true,
      };
      this.$nextTick(() => {
        const input = this.$refs?.candidateResumeScoreInput;
        if (!input) return;
        input.focus();
        input.select();
      });
    },

    cancelCandidateResumeScoreEdit() {
      if (this.candidateResumeEvaluationForm?.saving) return;
      this.candidateResumeEvaluationForm = {
        ...this.candidateResumeEvaluationForm,
        job_match_score: this.candidateResumeJobMatchScoreText(),
        scoreEditing: false,
      };
    },

    async saveCandidateResumeScore() {
      if (!this.candidateResumeEvaluationForm?.scoreEditing || this.candidateResumeEvaluationForm?.saving) return;
      await this.saveCandidateResumeEvaluation();
    },

    candidateResumeError() {
      return String(this.candidateDetail?.profile?.details_error || this.candidateDetail?.resume_parsed?.details?.error || "").trim();
    },

    candidateResumeBasicFacts() {
      const data = this.candidateResumeParsedData();
      const candidate = this.candidateDetail?.candidate || {};
      const facts = [
        { label: "姓名", value: String(candidate.name || "").trim() },
        { label: "手机号", value: String(candidate.phone || "").trim(), mono: true },
        { label: "性别", value: String(this.candidateDetail?.profile?.gender || data.gender || "").trim() },
        { label: "最高学历", value: String(this.candidateDetail?.profile?.highest_education || data.highest_education || "").trim() },
        { label: "邮箱", value: String(this.candidateDetail?.profile?.email || (Array.isArray(data.emails) ? data.emails[0] || "" : "")).trim() },
        { label: "经验年限", value: this.formatExperienceYears(data.experience_years) },
      ];
      return facts.filter((item) => item.value);
    },

    candidateResumeEducations() {
      const educations = this.candidateDetail?.profile?.educations;
      return Array.isArray(educations) ? educations.filter((item) => item && typeof item === "object") : [];
    },

    candidateResumeTags(key) {
      const raw = this.candidateResumeParsedData()[key];
      return Array.isArray(raw)
        ? raw.map((item) => String(item || "").trim()).filter(Boolean)
        : [];
    },

    candidateResumeEnglishItems() {
      const english = this.candidateDetail?.profile?.english;
      if (!english || typeof english !== "object") return [];
      const items = [];
      const pushItem = (label, value) => {
        const score = Number(value);
        if (!Number.isFinite(score) || score <= 0) return;
        items.push({ label, value: `${score}` });
      };
      pushItem("CET-4", english?.cet4?.score);
      pushItem("CET-6", english?.cet6?.score);
      return items;
    },

    candidateResumeWorkExperiences() {
      const rows = this.candidateResumeParsedData().work_experiences;
      if (Array.isArray(rows) && rows.length) {
        return rows
          .filter((item) => item && typeof item === "object")
          .map((item) => ({
            title: [String(item.company || "").trim(), String(item.title || "").trim()].filter(Boolean).join(" · "),
            period: String(item.period || "").trim(),
            bullets: Array.isArray(item.description)
              ? item.description.map((bullet) => String(bullet || "").trim()).filter(Boolean)
              : [],
          }))
          .filter((item) => item.title || item.period || item.bullets.length);
      }
      return this.candidateResumeExperienceBlocks("work");
    },

    candidateResumeProjects() {
      const rows = this.candidateDetail?.profile?.projects;
      if (Array.isArray(rows) && rows.length) {
        return rows
          .filter((item) => item && typeof item === "object")
          .map((item) => ({
            title: [String(item.name || "").trim(), String(item.role || "").trim()].filter(Boolean).join(" · "),
            period: String(item.period || "").trim(),
            bullets: Array.isArray(item.description)
              ? item.description.map((bullet) => String(bullet || "").trim()).filter(Boolean)
              : [],
          }))
          .filter((item) => item.title || item.period || item.bullets.length);
      }
      return this.candidateResumeExperienceBlocks("project");
    },

    candidateResumeExperienceBlocks(kind) {
      const blocks = this.candidateDetail?.profile?.experience_blocks;
      if (!Array.isArray(blocks)) return [];
      const expectedKind = String(kind || "").trim().toLowerCase();
      return blocks
        .filter((item) => item && typeof item === "object")
        .filter((item) => {
          const currentKind = String(item.kind || "").trim().toLowerCase();
          return expectedKind ? currentKind === expectedKind : true;
        })
        .map((item) => ({
          title: String(item.title || "").trim(),
          period: String(item.period || "").trim(),
          bullets: String(item.body || "")
            .split(/\n+/)
            .map((line) => line.trim())
            .filter(Boolean),
        }))
        .filter((item) => item.title || item.period || item.bullets.length);
    },

    candidateResumeCollectionGroups() {
      const groups = [
        { key: "awards", label: "奖项" },
        { key: "certifications", label: "证书" },
        { key: "publications", label: "发表" },
      ];
      return groups
        .map((group) => ({
          ...group,
          items: this.candidateResumeTags(group.key),
        }))
        .filter((group) => group.items.length);
    },

    candidateResumeAdminSummaries() {
      const items = [];
      const adminItems = Array.isArray(this.candidateDetail?.profile?.admin_evaluations)
        ? this.candidateDetail.profile.admin_evaluations
        : [];
      adminItems.slice(0, 3).forEach((item) => {
        const text = String(item?.text || "").trim();
        if (!text) return;
        items.push({
          label: "面试评价",
          text,
          meta: String(item?.at_display || item?.at || "").trim(),
        });
      });
      return items;
    },

    candidateResumeMainHasStructuredContent() {
      return Boolean(
        this.candidateResumeBasicFacts().length
        || this.candidateResumeEducations().length
        || this.candidateResumeTags("skills").length
        || this.candidateResumeEnglishItems().length
        || this.candidateResumeWorkExperiences().length
        || this.candidateResumeProjects().length
        || this.candidateResumeCollectionGroups().length
        || this.candidateResumeAdminSummaries().length,
      );
    },

    candidateResumeHasStructuredContent() {
      return Boolean(
        this.candidateResumeSummary()
        || this.candidateResumeMainHasStructuredContent(),
      );
    },

    candidateAttemptSummary(item) {
      const summary = item?.attempt_summary;
      return summary && typeof summary === "object" ? summary : {};
    },

    candidateAttemptSummaries(item) {
      const summaries = item?.attempt_summaries;
      if (Array.isArray(summaries)) {
        return summaries
          .filter((summary) => summary && typeof summary === "object")
          .slice(0, 2);
      }
      const summary = this.candidateAttemptSummary(item);
      return summary && Object.keys(summary).length ? [summary] : [];
    },

    candidateAttemptDisplayRows(item) {
      const rows = this.candidateAttemptSummaries(item);
      return rows.length ? rows : [{}];
    },

    candidateAttemptTitle(summary) {
      return String(summary.quiz_name || summary.quiz_key || "").trim();
    },

    candidateAttemptHasSummary(summary) {
      return Boolean(
        this.candidateAttemptTitle(summary)
        || String(summary.status || "").trim()
        || String(summary.score_display || "").trim(),
      );
    },

    candidateAttemptCompletionLabel(summary) {
      if (!this.candidateAttemptHasSummary(summary)) {
        return "暂无答题";
      }
      if (summary.completed) return "已完成";
      switch (String(summary.status || "").trim()) {
        case "in_quiz":
          return "正在答题";
        case "grading":
          return "正在判卷";
        default:
          return "未完成";
      }
    },

    candidateAttemptCompletionClass(summary) {
      const classes = [
        "rounded-full border px-2 py-0.5 text-[11px] font-semibold leading-5",
      ];
      if (!this.candidateAttemptHasSummary(summary)) {
        classes.push("border-slate-200 bg-slate-50 text-slate-500");
      } else if (summary.completed) {
        classes.push("border-emerald-200 bg-emerald-50 text-emerald-700");
      } else if (String(summary.status || "").trim() === "in_quiz") {
        classes.push("border-blue-200 bg-blue-50 text-blue-700");
      } else if (String(summary.status || "").trim() === "grading") {
        classes.push("border-amber-200 bg-amber-50 text-amber-700");
      } else {
        classes.push("border-amber-200 bg-amber-50 text-amber-700");
      }
      return classes.join(" ");
    },

    candidateAttemptShowScore(summary) {
      if (!this.candidateAttemptHasSummary(summary)) return false;
      const score = String(summary.score_display || "").trim();
      return Boolean(score && score !== "-");
    },

    candidateAttemptScoreLabel(summary) {
      if (!this.candidateAttemptHasSummary(summary)) {
        return "暂无分数";
      }
      const score = String(summary.score_display || "").trim();
      return score && score !== "-" ? `得分 ${score}` : "暂无分数";
    },

    formatExperienceYears(value) {
      const number = Number(value);
      if (!Number.isFinite(number) || number <= 0) return "";
      if (Number.isInteger(number)) {
        return `${number} 年`;
      }
      return `${number.toFixed(1)} 年`;
    },

    resumePhaseMeta(phase) {
      const key = String(phase || "").trim().toLowerCase();
      return RESUME_PHASE_META[key] || RESUME_PHASE_META.idle;
    },

    resumeStateLabel(phase) {
      return this.resumePhaseMeta(phase).label;
    },

    resumeStateBadgeStyle(phase) {
      const meta = this.resumePhaseMeta(phase);
      return {
        borderColor: meta.border,
        backgroundColor: meta.background,
        color: meta.text,
      };
    },

    resumeStatePanelStyle(phase) {
      const meta = this.resumePhaseMeta(phase);
      return {
        borderColor: meta.border,
        backgroundColor: meta.background,
      };
    },

    candidateResumeReparseDefaultMessage() {
      return this.candidateDetail?.candidate?.resume_filename
        ? "选择新简历后会先要求确认，再覆盖当前简历并重新解析。"
        : "当前候选人还没有简历，选择文件后会先要求确认并开始解析。";
    },

    resetCandidateResumeUploadState() {
      this.stopCandidateResumeUploadPolling();
      this.candidateResumeUploadState = createCandidateResumeUploadState();
    },

    resetCandidateResumeReparseState() {
      this.stopCandidateResumeReparsePolling();
      this.candidateResumeReparseState = createCandidateResumeReparseState(this.candidateResumeReparseDefaultMessage());
    },

    stopCandidateResumeUploadPolling() {
      if (!this.candidateResumeUploadPollTimer) return;
      window.clearTimeout(this.candidateResumeUploadPollTimer);
      this.candidateResumeUploadPollTimer = null;
    },

    stopCandidateResumeReparsePolling() {
      if (!this.candidateResumeReparsePollTimer) return;
      window.clearTimeout(this.candidateResumeReparsePollTimer);
      this.candidateResumeReparsePollTimer = null;
    },

    scheduleCandidateResumeUploadPolling() {
      const jobId = String(this.candidateResumeUploadState?.jobId || "").trim();
      if (!jobId || this.candidateResumeUploadPollTimer || !this.session.authenticated) return;
      if (this.route.name !== "candidates") return;
      this.candidateResumeUploadPollTimer = window.setTimeout(async () => {
        this.candidateResumeUploadPollTimer = null;
        await this.pollCandidateResumeUploadJob();
      }, this.candidateResumeUploadPollIntervalMs);
    },

    scheduleCandidateResumeReparsePolling() {
      const jobId = String(this.candidateResumeReparseState?.jobId || "").trim();
      if (!jobId || this.candidateResumeReparsePollTimer || !this.session.authenticated) return;
      if (this.route.name !== "candidate-detail") return;
      this.candidateResumeReparsePollTimer = window.setTimeout(async () => {
        this.candidateResumeReparsePollTimer = null;
        await this.pollCandidateResumeReparseJob();
      }, this.candidateResumeReparsePollIntervalMs);
    },

    async pollCandidateResumeUploadJob() {
      const jobId = String(this.candidateResumeUploadState?.jobId || "").trim();
      if (!jobId || this.route.name !== "candidates") return;
      const job = await this.api(`/api/admin/jobs/${encodeURIComponent(jobId)}`, { quiet: true });
      if (!job) return;
      const status = String(job?.status || "").trim().toLowerCase();
      if (["pending", "running"].includes(status)) {
        this.scheduleCandidateResumeUploadPolling();
        return;
      }
      if (status === "done") {
        const result = job?.result || {};
        const created = Boolean(result?.created);
        const candidateId = Number(result?.candidate_id || 0);
        this.candidateResumeUploadState = {
          ...createCandidateResumeUploadState(),
          phase: "success",
          fileName: String(result?.resume_filename || this.candidateResumeUploadState.fileName || ""),
          message: created ? "已创建候选人并写入简历。" : "已更新候选人并更新简历。",
          created,
          candidateName: String(result?.candidate_name || "").trim(),
          candidateId,
        };
        this.showNotice(created ? "简历已入库并创建候选人" : "简历已入库并更新候选人");
        await this.loadCandidates({ quiet: true });
        if (candidateId > 0) {
          await this.handleRoute(`/admin/candidates/${candidateId}`);
        }
        return;
      }
      this.candidateResumeUploadState = {
        ...createCandidateResumeUploadState(),
        phase: "error",
        fileName: this.candidateResumeUploadState.fileName,
        message: "本次简历入库失败，请重新选择文件。",
        error: String(job?.error || "简历入库失败").trim(),
      };
    },

    async pollCandidateResumeReparseJob() {
      const jobId = String(this.candidateResumeReparseState?.jobId || "").trim();
      if (!jobId || this.route.name !== "candidate-detail") return;
      const job = await this.api(`/api/admin/jobs/${encodeURIComponent(jobId)}`, { quiet: true });
      if (!job) return;
      const status = String(job?.status || "").trim().toLowerCase();
      if (["pending", "running"].includes(status)) {
        this.scheduleCandidateResumeReparsePolling();
        return;
      }
      if (status === "done") {
        const result = job?.result || {};
        const candidateId = Number(result?.candidate_id || this.candidateDetail?.candidate?.id || 0);
        if (candidateId > 0) {
          await this.loadCandidateDetail(candidateId);
        }
        this.candidateResumeReparseState = {
          ...createCandidateResumeReparseState(this.candidateResumeReparseDefaultMessage()),
          phase: "success",
          fileName: String(result?.resume_filename || this.candidateResumeReparseState.fileName || ""),
          message: "新简历已覆盖，解析结果已刷新。",
        };
        this.showNotice("简历重新解析完成");
        return;
      }
      this.candidateResumeReparseState = {
        ...createCandidateResumeReparseState(this.candidateResumeReparseDefaultMessage()),
        phase: "error",
        fileName: this.candidateResumeReparseState.fileName,
        message: "重新解析失败，请重新选择文件。",
        error: String(job?.error || "简历重新解析失败").trim(),
      };
    },

    openFilePicker(refName) {
      const input = this.$refs?.[refName];
      if (!input) return;
      input.value = "";
      input.click();
    },

    currentCandidatesPage() {
      const page = Number(this.candidates?.page || 1);
      if (!Number.isFinite(page) || page <= 0) {
        return 1;
      }
      return Math.floor(page);
    },

    candidateTotalPages() {
      const totalPages = Number(this.candidates?.total_pages || 1);
      if (!Number.isFinite(totalPages) || totalPages <= 0) {
        return 1;
      }
      return Math.floor(totalPages);
    },

    candidatesHavePagination() {
      return this.candidateTotalPages() > 1;
    },

    canGoToPreviousCandidatesPage() {
      return this.currentCandidatesPage() > 1;
    },

    canGoToNextCandidatesPage() {
      return this.currentCandidatesPage() < this.candidateTotalPages();
    },

    candidatePaginationButtonClass(disabled) {
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

    normalizeCandidatesPage(page, fallback = 1) {
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

    async changeCandidatesPage(page) {
      const nextPage = this.normalizeCandidatesPage(page, this.currentCandidatesPage());
      if (nextPage === this.currentCandidatesPage()) {
        return;
      }
      await this.loadCandidates({ page: nextPage });
    },

    async reloadCandidatesFromFirstPage() {
      window.clearTimeout(this.candidatesFilterTimer);
      this.candidatesFilterTimer = null;
      await this.loadCandidates({ page: 1 });
    },

    scheduleCandidatesReloadFromFirstPage() {
      window.clearTimeout(this.candidatesFilterTimer);
      this.candidatesFilterTimer = window.setTimeout(() => {
        this.candidatesFilterTimer = null;
        this.loadCandidates({ page: 1 });
      }, 220);
    },

    async loadCandidates({ quiet = false, page = null } = {}) {
      await this.loadCandidateJobDescriptionOptions({ quiet: true });
      const query = new URLSearchParams();
      const nextPage = this.normalizeCandidatesPage(page, this.candidates?.page || 1);
      query.set("page", String(nextPage));
      if (this.filters.candidates.q) query.set("q", this.filters.candidates.q);
      if (this.filters.candidates.created_from) query.set("created_from", this.filters.candidates.created_from);
      if (this.filters.candidates.created_to) query.set("created_to", this.filters.candidates.created_to);
      const data = await this.api(`/api/admin/candidates?${query.toString()}`, { quiet });
      if (!data) return;
      this.candidates = {
        ...(this.candidates || {}),
        items: Array.isArray(data?.items) ? data.items : [],
        ...data,
      };
    },

    async createCandidate() {
      const jobDescriptionId = this.selectedCandidateJobDescriptionId();
      if (!jobDescriptionId) {
        this.showNotice("请选择职位");
        return;
      }
      const payload = {
        ...this.candidateForm,
        job_description_id: jobDescriptionId,
      };
      await this.api("/api/admin/candidates", {
        method: "POST",
        body: JSON.stringify(payload),
        headers: { "Content-Type": "application/json" },
      });
      this.candidateForm = { name: "", phone: "", job_description_id: "" };
      this.showNotice("候选人创建成功");
      await this.loadCandidates({ quiet: true, page: 1 });
    },

    openCandidateResumeUploadPicker() {
      if (this.candidateResumeUploadState.busy) return;
      if (!this.selectedResumeUploadJobDescriptionId()) {
        this.showNotice("请先为简历入库选择职位");
        return;
      }
      this.openFilePicker("candidateResumeUpload");
    },

    async handleCandidateResumeUploadSelected(event) {
      const file = event?.target?.files?.[0];
      if (!file || this.candidateResumeUploadState.busy) return;
      if (!this.isSupportedResumeFile(file)) {
        this.candidateResumeUploadState = {
          ...createCandidateResumeUploadState(),
          phase: "error",
          fileName: file.name,
          message: "仅支持 PDF 或图片简历，请重新选择文件。",
          error: "当前前端仅支持上传 PDF 或图片格式。",
        };
        return;
      }
      this.stopCandidateResumeUploadPolling();
      this.candidateResumeUploadState = {
        ...createCandidateResumeUploadState(),
        phase: "running",
        busy: true,
        fileName: file.name,
        message: "正在上传简历并创建后台解析任务。",
      };
      const form = new FormData();
      form.append("file", file);
      form.append("job_description_id", String(this.selectedResumeUploadJobDescriptionId()));
      try {
        const data = await this.api("/api/admin/candidates/resume/upload-job", {
          method: "POST",
          body: form,
          quiet: true,
        });
        if (!data) {
          this.resetCandidateResumeUploadState();
          return;
        }
        this.candidateResumeUploadState = {
          ...createCandidateResumeUploadState(),
          phase: "running",
          busy: true,
          jobId: String(data?.job_id || "").trim(),
          fileName: String(data?.file_name || file.name),
          message: "简历已接收，正在后台解析手机号、姓名和简历详情。",
        };
        this.showNotice("简历已接收，正在后台解析");
        if (this.candidateResumeUploadState.jobId) {
          this.scheduleCandidateResumeUploadPolling();
        } else {
          throw new Error("后台任务创建失败");
        }
      } catch (error) {
        this.candidateResumeUploadState = {
          ...createCandidateResumeUploadState(),
          phase: "error",
          fileName: file.name,
          message: "本次简历入库失败，请重新选择文件。",
          error: error.message || "简历入库失败",
        };
      }
    },

    async loadCandidateDetail(candidateId) {
      await this.loadCandidateJobDescriptionOptions({ quiet: true });
      this.candidateDetail = await this.api(`/api/admin/candidates/${candidateId}`);
      this.candidateJobDescriptionAddSelection = [];
      this.candidateEvaluation = "";
      this.syncCandidateResumeEvaluationForm();
      if (!this.candidateResumeReparseState.busy) {
        this.resetCandidateResumeReparseState();
      }
    },

    async saveCandidateEvaluation() {
      const payload = { evaluation: this.candidateEvaluation };
      this.candidateDetail = await this.api(`/api/admin/candidates/${this.candidateDetail.candidate.id}/evaluation`, {
        method: "POST",
        body: JSON.stringify(payload),
        headers: { "Content-Type": "application/json" },
      });
      this.candidateEvaluation = "";
      this.showNotice("面试评价已保存");
    },

    downloadCandidateResume() {
      if (!this.candidateDetail.candidate?.id) return;
      window.location.href = `/api/admin/candidates/${this.candidateDetail.candidate.id}/resume`;
    },

    openCandidateResumeReparsePicker() {
      if (this.candidateResumeReparseState.busy) return;
      this.openFilePicker("candidateResumeReparse");
    },

    handleCandidateResumeReparseSelected(event) {
      const file = event?.target?.files?.[0];
      if (!file || this.candidateResumeReparseState.busy) return;
      if (!this.isSupportedResumeFile(file)) {
        this.candidateResumeReparseState = {
          ...createCandidateResumeReparseState(this.candidateResumeReparseDefaultMessage()),
          phase: "error",
          fileName: file.name,
          message: "仅支持 PDF 或图片简历，请重新选择文件。",
          error: "当前前端仅支持上传 PDF 或图片格式。",
          pendingFile: null,
        };
        return;
      }
      this.stopCandidateResumeReparsePolling();
      this.candidateResumeReparseState = {
        ...createCandidateResumeReparseState(this.candidateResumeReparseDefaultMessage()),
        phase: "confirm",
        fileName: file.name,
        message: "确认后会覆盖当前简历并重算解析结果。",
        pendingFile: file,
      };
    },

    cancelCandidateResumeReparse() {
      if (this.candidateResumeReparseState.busy) return;
      this.resetCandidateResumeReparseState();
    },

    async confirmCandidateResumeReparse() {
      const file = this.candidateResumeReparseState.pendingFile;
      if (!file || this.candidateResumeReparseState.busy || !this.candidateDetail.candidate?.id) return;
      this.stopCandidateResumeReparsePolling();
      this.candidateResumeReparseState = {
        ...this.candidateResumeReparseState,
        phase: "running",
        busy: true,
        error: "",
        message: "正在上传新简历并创建后台解析任务。",
      };
      const form = new FormData();
      form.append("file", file);
      try {
        const data = await this.api(
          `/api/admin/candidates/${this.candidateDetail.candidate.id}/resume/reparse-job`,
          {
            method: "POST",
            body: form,
            quiet: true,
          },
        );
        if (!data) {
          this.resetCandidateResumeReparseState();
          return;
        }
        this.candidateResumeReparseState = {
          ...createCandidateResumeReparseState(this.candidateResumeReparseDefaultMessage()),
          phase: "running",
          busy: true,
          jobId: String(data?.job_id || "").trim(),
          fileName: String(data?.file_name || file.name),
          message: "新简历已接收，正在后台重新解析。",
          pendingFile: null,
        };
        this.showNotice("新简历已接收，正在后台重新解析");
        if (this.candidateResumeReparseState.jobId) {
          this.scheduleCandidateResumeReparsePolling();
        } else {
          throw new Error("后台任务创建失败");
        }
      } catch (error) {
        this.candidateResumeReparseState = {
          ...createCandidateResumeReparseState(this.candidateResumeReparseDefaultMessage()),
          phase: "error",
          fileName: file.name,
          message: "重新解析失败，请重新选择文件。",
          error: error.message || "简历重新解析失败",
        };
      }
    },

    async deleteCandidate() {
      if (!window.confirm("确定删除该候选人吗？")) return;
      await this.api(`/api/admin/candidates/${this.candidateDetail.candidate.id}`, { method: "DELETE" });
      this.showNotice("候选人已删除");
      await this.handleRoute("/admin/candidates");
    },

  };
}
