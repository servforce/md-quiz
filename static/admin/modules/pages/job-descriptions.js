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

    jobDescriptionEditorLocked() {
      return this.jobDescriptionReadOnly() || Boolean(this.jobDescriptionOperation || this.jobDescriptionPendingTransition);
    },

    hasUnsavedJobDescriptionChanges() {
      return this.jobDescriptionFormDirty();
    },

    async confirmJobDescriptionLeave() {
      if (this.jobDescriptionOperation || this.jobDescriptionPendingTransition) return false;
      if (!this.jobDescriptionFormDirty()) return true;
      this.jobDescriptionPendingTransition = { title: this.jobDescriptionForm.title || "新建职位", error: "" };
      const decision = new Promise((resolve) => { this.jobDescriptionTransitionResolver = resolve; });
      await this.$nextTick();
      const dialog = this.$refs?.jobDescriptionUnsavedDialog;
      if (!dialog || typeof dialog.showModal !== "function") {
        this.showNotice("请先保存职位更改，再继续操作");
        this.finishJobDescriptionTransition(false);
        return decision;
      }
      dialog.showModal();
      return decision;
    },

    finishJobDescriptionTransition(proceed) {
      const resolve = this.jobDescriptionTransitionResolver;
      this.jobDescriptionTransitionResolver = null;
      this.jobDescriptionPendingTransition = null;
      this.$refs?.jobDescriptionUnsavedDialog?.close();
      if (resolve) resolve(Boolean(proceed));
    },

    async resolveJobDescriptionTransition(choice) {
      if (!this.jobDescriptionPendingTransition || this.jobDescriptionOperation) return;
      if (choice === "save") {
        this.jobDescriptionPendingTransition.error = "";
        const saved = await this.saveJobDescription({ fromTransition: true });
        if (saved) this.finishJobDescriptionTransition(true);
        // 会话失效已由统一 API 跳转登录页，结束待处理决定，不执行原切换。
        else if (!this.session?.authenticated) this.finishJobDescriptionTransition(false);
        return;
      }
      if (choice === "discard") {
        if (this.jobDescriptionDetail?.id) this.applyJobDescriptionDetail(this.jobDescriptionDetail);
        else this.resetJobDescriptionForm();
      }
      this.finishJobDescriptionTransition(choice === "discard");
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
        "inline-flex min-h-11 min-w-16 items-center justify-center rounded-md px-3 text-sm font-semibold transition",
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
      if (this.jobDescriptionEditorLocked()) return;
      const key = String(quizKey || "").trim();
      if (!key) return;
      const current = this.normalizeJobDescriptionRelatedQuizzes(this.jobDescriptionForm?.related_quizzes);
      if (current.includes(key)) return;
      this.jobDescriptionForm.related_quizzes = [...current, key];
    },

    removeJobDescriptionRelatedQuiz(quizKey) {
      if (this.jobDescriptionEditorLocked()) return;
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
      } else {
        await this.previewJobDescriptionDraft();
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
      this.jobDescriptionEditorInitialized = true;
      this.resetJobDescriptionPreview();
      this.scheduleJobDescriptionEditorAutosize();
    },

    async startCreateJobDescription() {
      if (!(await this.confirmJobDescriptionLeave())) return;
      if (this.jobDescriptionOperation) return;
      this.jobDescriptionOperation = "loading";
      try {
        this.resetJobDescriptionForm();
        await this.setAdminCompactTab("job-descriptions", "editor", { scroll: true });
        await this.$nextTick();
        this.autosizeJobDescriptionEditor();
      } finally {
        this.jobDescriptionOperation = "";
      }
    },

    jobDescriptionEditorTitle() {
      return this.jobDescriptionForm?.id ? "编辑职位" : "创建职位";
    },

    jobDescriptionPreviewHtml() {
      if (this.jobDescriptionPreview?.source !== String(this.jobDescriptionForm?.content_md || "")) return "";
      return String(this.jobDescriptionPreview?.html || "");
    },

    resetJobDescriptionPreview(detail = null) {
      this.jobDescriptionPreview = {
        html: String(detail?.content_html || ""),
        source: String(detail?.content_md || ""),
        loading: false,
        error: "",
        requestId: Number(this.jobDescriptionPreview?.requestId || 0) + 1,
      };
    },

    async previewJobDescriptionDraft() {
      const source = String(this.jobDescriptionForm?.content_md || "");
      const requestId = Number(this.jobDescriptionPreview?.requestId || 0) + 1;
      this.jobDescriptionPreview = { html: "", source, loading: Boolean(source.trim()), error: "", requestId };
      if (!source.trim()) return;
      try {
        const data = await this.api("/api/admin/job-descriptions/preview", {
          method: "POST",
          body: JSON.stringify({ content_md: source }),
          headers: { "Content-Type": "application/json" },
        });
        if (!data || this.jobDescriptionPreview.requestId !== requestId
            || source !== String(this.jobDescriptionForm?.content_md || "")) return;
        this.jobDescriptionPreview.html = String(data.content_html || "");
      } catch (error) {
        if (this.jobDescriptionPreview.requestId === requestId) {
          this.jobDescriptionPreview.error = error.message || "预览失败，请重试";
        }
      } finally {
        if (this.jobDescriptionPreview.requestId === requestId) this.jobDescriptionPreview.loading = false;
      }
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
          || String(this.jobDescriptionForm?.status || "draft") !== "draft"
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
      this.jobDescriptions.page = nextPage;
      this.syncAdminListRoute({ replace: false });
      await this.loadJobDescriptions({ page: nextPage });
    },

    async reloadJobDescriptionsFromFirstPage({ replace = false } = {}) {
      window.clearTimeout(this.jobDescriptionsFilterTimer);
      this.jobDescriptionsFilterTimer = null;
      this.jobDescriptions.page = 1;
      this.syncAdminListRoute({ replace });
      await this.loadJobDescriptions({ page: 1 });
    },

    scheduleJobDescriptionsReloadFromFirstPage() {
      window.clearTimeout(this.jobDescriptionsFilterTimer);
      this.jobDescriptionsFilterTimer = window.setTimeout(() => {
        this.jobDescriptionsFilterTimer = null;
        this.reloadJobDescriptionsFromFirstPage({ replace: true });
      }, 220);
    },

    async loadJobDescriptions({ quiet = false, page = null } = {}) {
      const requestId = ++this.jobDescriptionsRequestId;
      const query = new URLSearchParams();
      const nextPage = this.normalizeJobDescriptionsPage(page, this.jobDescriptions?.page || 1);
      query.set("page", String(nextPage));
      if (this.filters.jobDescriptions.q) query.set("q", this.filters.jobDescriptions.q);
      if (this.filters.jobDescriptions.status) query.set("status", this.filters.jobDescriptions.status);
      const data = await this.api(`/api/admin/job-descriptions?${query.toString()}`, { quiet });
      if (!data || requestId !== this.jobDescriptionsRequestId) return;
      this.jobDescriptions = {
        ...(this.jobDescriptions || {}),
        items: Array.isArray(data?.items) ? data.items : [],
        ...data,
      };
      if (!this.jobDescriptionEditorInitialized && !this.jobDescriptionFormDirty()
          && !this.jobDescriptionOperation && !this.jobDescriptionPendingTransition) {
        const first = this.jobDescriptions.items?.[0];
        if (first?.id) {
          await this.loadJobDescription(first.id, { quiet: true, scroll: false });
        }
      }
    },

    async loadJobDescription(id, { quiet = false, scroll = true } = {}) {
      const numericId = Number(id || 0);
      if (!Number.isFinite(numericId) || numericId <= 0) return;
      if (this.jobDescriptionOperation || this.jobDescriptionPendingTransition) return;
      if (numericId === Number(this.jobDescriptionForm?.id || 0)) {
        if (scroll) await this.setAdminCompactTab("job-descriptions", "editor", { scroll: true });
        return;
      }
      if (!(await this.confirmJobDescriptionLeave()) || this.jobDescriptionOperation) return;
      this.jobDescriptionOperation = "loading";
      try {
        const data = await this.api(`/api/admin/job-descriptions/${numericId}`, { quiet });
        if (!data) return;
        this.applyJobDescriptionDetail(data);
        this.jobDescriptionContentTab = "preview";
        if (scroll) await this.setAdminCompactTab("job-descriptions", "editor", { scroll: true });
        await this.$nextTick();
        this.autosizeJobDescriptionEditor();
      } catch (error) {
        if (quiet) this.showNotice(error.message || "职位加载失败");
      } finally {
        this.jobDescriptionOperation = "";
      }
    },

    applyJobDescriptionDetail(data) {
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
      this.jobDescriptionEditorInitialized = true;
      this.resetJobDescriptionPreview(data);
    },

    async saveJobDescription({ fromTransition = false } = {}) {
      if (this.jobDescriptionOperation || (this.jobDescriptionPendingTransition && !fromTransition)) return false;
      if (this.jobDescriptionReadOnly()) {
        this.showNotice("仓库来源职位请在 Git 仓库中修改");
        return false;
      }
      const payload = {
        title: String(this.jobDescriptionForm?.title || "").trim(),
        content_md: String(this.jobDescriptionForm?.content_md || ""),
        status: String(this.jobDescriptionForm?.status || "draft"),
        related_quizzes: this.normalizeJobDescriptionRelatedQuizzes(this.jobDescriptionForm?.related_quizzes),
      };
      if (!payload.title) {
        if (fromTransition && this.jobDescriptionPendingTransition) {
          this.jobDescriptionPendingTransition.error = "岗位名称不能为空，请继续编辑后填写";
        }
        this.showNotice("岗位名称不能为空");
        return false;
      }
      const id = Number(this.jobDescriptionForm?.id || 0);
      this.jobDescriptionOperation = "saving";
      try {
        const data = await this.api(id > 0 ? `/api/admin/job-descriptions/${id}` : "/api/admin/job-descriptions", {
          method: id > 0 ? "PUT" : "POST",
          body: JSON.stringify(payload),
          headers: { "Content-Type": "application/json" },
        });
        if (!data?.id) {
          if (fromTransition && this.jobDescriptionPendingTransition) {
            this.jobDescriptionPendingTransition.error = "保存未成功，当前内容已保留";
          }
          return false;
        }
        this.applyJobDescriptionDetail(data);
        await this.$nextTick();
        this.autosizeJobDescriptionEditor();
        this.showNotice(id > 0 ? "职位已保存" : "职位已创建");
        try {
          await this.loadJobDescriptions({ quiet: true, page: id > 0 ? this.currentJobDescriptionsPage() : 1 });
        } catch (_error) {
          this.showNotice("职位已保存，列表刷新失败，请稍后重试");
        }
        return Boolean(this.session?.authenticated);
      } catch (error) {
        // api 已显示请求错误；失败不能清空草稿或继续原切换。
        if (fromTransition && this.jobDescriptionPendingTransition) {
          this.jobDescriptionPendingTransition.error = error.message || "保存失败，请重试";
        }
        return false;
      } finally {
        this.jobDescriptionOperation = "";
      }
    },

    async deleteJobDescription() {
      if (this.jobDescriptionOperation || this.jobDescriptionPendingTransition) return;
      const id = Number(this.jobDescriptionForm?.id || 0);
      if (!id) return;
      if (this.jobDescriptionReadOnly()) {
        this.showNotice("仓库来源职位请在 Git 仓库中归档或移除");
        return;
      }
      const message = this.jobDescriptionFormDirty()
        ? "确定删除该职位吗？未保存的更改也会被放弃。" : "确定删除该职位吗？";
      if (!window.confirm(message)) return;
      this.jobDescriptionOperation = "deleting";
      try {
        const data = await this.api(`/api/admin/job-descriptions/${id}`, { method: "DELETE" });
        if (!data?.ok) return;
        this.showNotice("职位已删除");
        this.resetJobDescriptionForm();
        this.jobDescriptions.page = 1;
        this.syncAdminListRoute({ replace: true });
        await this.loadJobDescriptions({ quiet: true, page: 1 });
        await this.setAdminCompactTab("job-descriptions", "list", { scroll: true });
      } catch (error) {
        if (!this.jobDescriptionForm?.id) this.showNotice("职位已删除，列表刷新失败，请稍后重试");
      } finally {
        this.jobDescriptionOperation = "";
      }
    },
  };
}
