export function createAdminJobDescriptionsModule() {
  return {
    jobDescriptionStatusOptions() {
      const options = this.jobDescriptions?.status_options;
      if (Array.isArray(options) && options.length) {
        return options;
      }
      return [
        { key: "draft", label: "草稿" },
        { key: "active", label: "启用" },
        { key: "archived", label: "归档" },
      ];
    },

    jobDescriptionStatusLabel(value) {
      const key = String(value || "").trim().toLowerCase();
      const found = this.jobDescriptionStatusOptions().find((item) => item.key === key);
      return found?.label || key || "草稿";
    },

    jobDescriptionStatusBadgeClass(value) {
      const key = String(value || "").trim().toLowerCase();
      const classes = [
        "inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-semibold leading-4",
      ];
      if (key === "active") {
        classes.push("border-emerald-200 bg-emerald-50 text-emerald-700");
      } else if (key === "archived") {
        classes.push("border-slate-200 bg-slate-100 text-slate-500");
      } else {
        classes.push("border-amber-200 bg-amber-50 text-amber-700");
      }
      return classes.join(" ");
    },

    jobDescriptionSourceLabel(value) {
      const key = String(value || "manual").trim().toLowerCase();
      if (key === "git") return "仓库";
      return "手动";
    },

    jobDescriptionSourceBadgeClass(value) {
      const key = String(value || "manual").trim().toLowerCase();
      const classes = [
        "inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-semibold leading-4",
      ];
      if (key === "git") {
        classes.push("border-sky-200 bg-sky-50 text-sky-700");
      } else {
        classes.push("border-slate-200 bg-slate-50 text-slate-600");
      }
      return classes.join(" ");
    },

    jobDescriptionReadOnly() {
      return String(this.jobDescriptionForm?.source_kind || this.jobDescriptionDetail?.source_kind || "manual")
        .trim()
        .toLowerCase() === "git";
    },

    jobDescriptionContentTabs() {
      return [
        { key: "edit", label: "编辑" },
        { key: "preview", label: "预览" },
      ];
    },

    jobDescriptionContentTabClass(key) {
      const active = String(this.jobDescriptionContentTab || "preview") === String(key || "");
      const classes = [
        "inline-flex h-8 min-w-16 items-center justify-center rounded-md px-3 text-sm font-semibold transition",
      ];
      if (active) {
        classes.push("bg-blue-600 text-white shadow-sm");
      } else {
        classes.push("text-slate-600 hover:bg-blue-50 hover:text-blue-700");
      }
      return classes.join(" ");
    },

    normalizeJobDescriptionRelatedQuizzes(value = null) {
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

    jobDescriptionRelatedQuizOptions() {
      return this.quizOptionItems();
    },

    jobDescriptionRelatedQuizItems() {
      return this.normalizeJobDescriptionRelatedQuizzes(this.jobDescriptionForm?.related_quizzes)
        .map((quizKey) => {
          const option = this.quizOptionByKey(quizKey);
          return {
            quiz_key: quizKey,
            title: String(option?.title || option?.quiz_key || quizKey).trim(),
          };
        });
    },

    jobDescriptionRelatedQuizSearchResults(query = "") {
      const keyword = String(query || "").trim().toLocaleLowerCase();
      if (!keyword) return [];
      const selected = new Set(this.normalizeJobDescriptionRelatedQuizzes(this.jobDescriptionForm?.related_quizzes));
      return this.jobDescriptionRelatedQuizOptions().filter((quiz) => {
        const quizKey = String(quiz?.quiz_key || "").trim();
        if (!quizKey || selected.has(quizKey)) return false;
        const searchable = `${quiz?.title || ""} ${quizKey}`.toLocaleLowerCase();
        return searchable.includes(keyword);
      });
    },

    addJobDescriptionRelatedQuiz(quizKey) {
      if (this.jobDescriptionReadOnly()) return;
      const key = String(quizKey || "").trim();
      if (!key) return;
      const current = this.normalizeJobDescriptionRelatedQuizzes(this.jobDescriptionForm?.related_quizzes);
      if (current.includes(key)) return;
      this.jobDescriptionForm.related_quizzes = [...current, key];
    },

    removeJobDescriptionRelatedQuiz(quizKey) {
      if (this.jobDescriptionReadOnly()) return;
      const key = String(quizKey || "").trim();
      if (!key) return;
      this.jobDescriptionForm.related_quizzes = this.normalizeJobDescriptionRelatedQuizzes(
        this.jobDescriptionForm?.related_quizzes,
      ).filter((item) => item !== key);
    },

    async setJobDescriptionContentTab(key) {
      const next = String(key || "edit").trim() === "preview" ? "preview" : "edit";
      this.jobDescriptionContentTab = next;
      if (next === "edit") {
        await this.$nextTick();
        this.autosizeJobDescriptionEditor();
      }
    },

    resetJobDescriptionForm() {
      this.jobDescriptionDetail = {};
      this.jobDescriptionForm = {
        id: 0,
        title: "",
        content_md: "",
        status: "draft",
        related_quizzes: [],
        source_kind: "manual",
        jd_key: "",
        source_path: "",
        git_repo_url: "",
      };
      this.jobDescriptionContentTab = "edit";
      this.scheduleJobDescriptionEditorAutosize();
    },

    async startCreateJobDescription() {
      this.resetJobDescriptionForm();
      await this.setAdminCompactTab("job-descriptions", "editor", { scroll: true });
      await this.$nextTick();
      this.autosizeJobDescriptionEditor();
    },

    jobDescriptionEditorTitle() {
      return this.jobDescriptionForm?.id ? "编辑职位" : "创建职位";
    },

    jobDescriptionPreviewHtml() {
      const detailId = Number(this.jobDescriptionDetail?.id || 0);
      const formId = Number(this.jobDescriptionForm?.id || 0);
      if (!detailId || detailId !== formId) {
        return "";
      }
      return String(this.jobDescriptionDetail?.content_html || "");
    },

    jobDescriptionFormDirty() {
      if (this.jobDescriptionReadOnly()) {
        return false;
      }
      const detailId = Number(this.jobDescriptionDetail?.id || 0);
      const formId = Number(this.jobDescriptionForm?.id || 0);
      if (!formId || detailId !== formId) {
        return Boolean(
          String(this.jobDescriptionForm?.title || "").trim()
          || String(this.jobDescriptionForm?.content_md || "").trim()
          || this.normalizeJobDescriptionRelatedQuizzes(this.jobDescriptionForm?.related_quizzes).length > 0,
        );
      }
      return (
        String(this.jobDescriptionForm?.title || "") !== String(this.jobDescriptionDetail?.title || "")
        || String(this.jobDescriptionForm?.content_md || "") !== String(this.jobDescriptionDetail?.content_md || "")
        || String(this.jobDescriptionForm?.status || "") !== String(this.jobDescriptionDetail?.status || "")
        || this.normalizeJobDescriptionRelatedQuizzes(this.jobDescriptionForm?.related_quizzes).join("\n")
          !== this.normalizeJobDescriptionRelatedQuizzes(this.jobDescriptionDetail?.related_quizzes).join("\n")
      );
    },

    autosizeJobDescriptionEditor(target = null) {
      const targetIsTextarea = typeof HTMLTextAreaElement !== "undefined"
        && target instanceof HTMLTextAreaElement;
      const textarea = targetIsTextarea ? target : this.$refs?.jobDescriptionContentEditor;
      if (!textarea || !textarea.style || typeof textarea.scrollHeight !== "number") {
        return;
      }
      const computedStyle = typeof window !== "undefined" && typeof window.getComputedStyle === "function"
        ? window.getComputedStyle(textarea)
        : null;
      const minHeight = Number.parseFloat(computedStyle?.minHeight || "0") || 0;
      textarea.style.height = "auto";
      textarea.style.height = `${Math.max(textarea.scrollHeight, minHeight)}px`;
    },

    scheduleJobDescriptionEditorAutosize() {
      if (typeof this.$nextTick === "function") {
        this.$nextTick(() => this.autosizeJobDescriptionEditor());
        return;
      }
      if (typeof window !== "undefined" && typeof window.requestAnimationFrame === "function") {
        window.requestAnimationFrame(() => this.autosizeJobDescriptionEditor());
      }
    },

    currentJobDescriptionsPage() {
      const page = Number(this.jobDescriptions?.page || 1);
      return Number.isFinite(page) && page > 0 ? Math.floor(page) : 1;
    },

    jobDescriptionTotalPages() {
      const totalPages = Number(this.jobDescriptions?.total_pages || 1);
      return Number.isFinite(totalPages) && totalPages > 0 ? Math.floor(totalPages) : 1;
    },

    jobDescriptionsHavePagination() {
      return this.jobDescriptionTotalPages() > 1;
    },

    canGoToPreviousJobDescriptionsPage() {
      return this.currentJobDescriptionsPage() > 1;
    },

    canGoToNextJobDescriptionsPage() {
      return this.currentJobDescriptionsPage() < this.jobDescriptionTotalPages();
    },

    jobDescriptionPaginationButtonClass(disabled) {
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

    normalizeJobDescriptionsPage(page, fallback = 1) {
      const candidate = Number(page);
      const fallbackPage = Number(fallback);
      if (!Number.isFinite(candidate) || candidate <= 0) {
        return Number.isFinite(fallbackPage) && fallbackPage > 0 ? Math.floor(fallbackPage) : 1;
      }
      return Math.max(1, Math.floor(candidate));
    },

    async changeJobDescriptionsPage(page) {
      const nextPage = this.normalizeJobDescriptionsPage(page, this.currentJobDescriptionsPage());
      if (nextPage === this.currentJobDescriptionsPage()) {
        return;
      }
      await this.loadJobDescriptions({ page: nextPage });
    },

    async reloadJobDescriptionsFromFirstPage() {
      window.clearTimeout(this.jobDescriptionsFilterTimer);
      this.jobDescriptionsFilterTimer = null;
      await this.loadJobDescriptions({ page: 1 });
    },

    scheduleJobDescriptionsReloadFromFirstPage() {
      window.clearTimeout(this.jobDescriptionsFilterTimer);
      this.jobDescriptionsFilterTimer = window.setTimeout(() => {
        this.jobDescriptionsFilterTimer = null;
        this.loadJobDescriptions({ page: 1 });
      }, 220);
    },

    async loadJobDescriptions({ quiet = false, page = null } = {}) {
      const query = new URLSearchParams();
      const nextPage = this.normalizeJobDescriptionsPage(page, this.jobDescriptions?.page || 1);
      query.set("page", String(nextPage));
      if (this.filters.jobDescriptions.q) query.set("q", this.filters.jobDescriptions.q);
      if (this.filters.jobDescriptions.status) query.set("status", this.filters.jobDescriptions.status);
      const data = await this.api(`/api/admin/job-descriptions?${query.toString()}`, { quiet });
      if (!data) return;
      this.jobDescriptions = {
        ...(this.jobDescriptions || {}),
        items: Array.isArray(data?.items) ? data.items : [],
        ...data,
      };
      const formId = Number(this.jobDescriptionForm?.id || 0);
      const hasDraftInput = Boolean(
        !formId
        && (
          String(this.jobDescriptionForm?.title || "").trim()
          || String(this.jobDescriptionForm?.content_md || "").trim()
        ),
      );
      if (!formId && !hasDraftInput && !Number(this.jobDescriptionDetail?.id || 0)) {
        const first = this.jobDescriptions.items?.[0];
        if (first?.id) {
          await this.loadJobDescription(first.id, { quiet: true, scroll: false });
        }
      }
    },

    async loadJobDescription(id, { quiet = false, scroll = true } = {}) {
      const numericId = Number(id || 0);
      if (!Number.isFinite(numericId) || numericId <= 0) return;
      const data = await this.api(`/api/admin/job-descriptions/${numericId}`, { quiet });
      if (!data) return;
      this.jobDescriptionDetail = data;
      this.jobDescriptionForm = {
        id: Number(data.id || 0),
        title: String(data.title || ""),
        content_md: String(data.content_md || ""),
        status: String(data.status || "draft"),
        related_quizzes: this.normalizeJobDescriptionRelatedQuizzes(data.related_quizzes),
        source_kind: String(data.source_kind || "manual"),
        jd_key: String(data.jd_key || ""),
        source_path: String(data.source_path || ""),
        git_repo_url: String(data.git_repo_url || ""),
      };
      this.jobDescriptionContentTab = "preview";
      if (scroll) {
        await this.setAdminCompactTab("job-descriptions", "editor", { scroll: true });
      }
      await this.$nextTick();
      this.autosizeJobDescriptionEditor();
    },

    async saveJobDescription() {
      if (this.jobDescriptionReadOnly()) {
        this.showNotice("仓库来源职位请在 Git 仓库中修改");
        return;
      }
      const payload = {
        title: String(this.jobDescriptionForm?.title || "").trim(),
        content_md: String(this.jobDescriptionForm?.content_md || ""),
        status: String(this.jobDescriptionForm?.status || "draft"),
        related_quizzes: this.normalizeJobDescriptionRelatedQuizzes(this.jobDescriptionForm?.related_quizzes),
      };
      if (!payload.title) {
        this.showNotice("岗位名称不能为空");
        return;
      }
      const id = Number(this.jobDescriptionForm?.id || 0);
      const data = await this.api(id > 0 ? `/api/admin/job-descriptions/${id}` : "/api/admin/job-descriptions", {
        method: id > 0 ? "PUT" : "POST",
        body: JSON.stringify(payload),
        headers: { "Content-Type": "application/json" },
      });
      if (!data) return;
      this.jobDescriptionDetail = data;
      this.jobDescriptionForm = {
        id: Number(data.id || 0),
        title: String(data.title || ""),
        content_md: String(data.content_md || ""),
        status: String(data.status || "draft"),
        related_quizzes: this.normalizeJobDescriptionRelatedQuizzes(data.related_quizzes),
        source_kind: String(data.source_kind || "manual"),
        jd_key: String(data.jd_key || ""),
        source_path: String(data.source_path || ""),
        git_repo_url: String(data.git_repo_url || ""),
      };
      await this.$nextTick();
      this.autosizeJobDescriptionEditor();
      this.showNotice(id > 0 ? "职位已保存" : "职位已创建");
      await this.loadJobDescriptions({ quiet: true, page: id > 0 ? this.currentJobDescriptionsPage() : 1 });
    },

    async deleteJobDescription() {
      const id = Number(this.jobDescriptionForm?.id || 0);
      if (!id) return;
      if (this.jobDescriptionReadOnly()) {
        this.showNotice("仓库来源职位请在 Git 仓库中归档或移除");
        return;
      }
      if (!window.confirm("确定删除该职位吗？")) return;
      await this.api(`/api/admin/job-descriptions/${id}`, { method: "DELETE" });
      this.showNotice("职位已删除");
      this.resetJobDescriptionForm();
      await this.loadJobDescriptions({ quiet: true, page: 1 });
      await this.setAdminCompactTab("job-descriptions", "list", { scroll: true });
    },
  };
}
