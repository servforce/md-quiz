import { TRAIT_COLOR_PALETTE } from "../constants.js";
export function createAdminQuizzesModule() {
  let listRequestId = 0;
  let detailRequestId = 0;
  return {
    currentQuizzesPage() {
      return Math.max(1, Math.floor(Number(this.quizzes?.page) || 1));
    },

    quizTotalPages() {
      return Math.max(1, Math.floor(Number(this.quizzes?.total_pages) || 1));
    },

    async changeQuizzesPage(page) {
      const nextPage = Math.max(1, Math.min(this.quizTotalPages(), Math.floor(Number(page) || 1)));
      if (nextPage === this.currentQuizzesPage()) return;
      this.quizzes.page = nextPage;
      this.syncAdminListRoute({ replace: false });
      await this.loadQuizzes({ page: nextPage });
    },

    async reloadQuizzesFromFirstPage({ replace = false } = {}) {
      this.quizzes.page = 1;
      this.syncAdminListRoute({ replace });
      await this.loadQuizzes({ page: 1 });
    },

    quizSyncResult() {
      const result = this.syncState?.last_result;
      return result && typeof result === "object" ? result : {};
    },

    quizSyncErrors() {
      const errors = this.quizSyncResult().errors;
      return Array.isArray(errors) ? errors : [];
    },

    quizSyncStatusLabel() {
      const status = this.syncStatus();
      if (!this.hasRepoBinding() && !status) return "尚未绑定仓库";
      if (status === "done" && Number(this.quizSyncResult().error_count || 0) > 0) return "同步完成，部分条目失败";
      return ({ idle: "尚未同步", queued: "等待同步", pending: "等待同步", running: "同步中", done: "同步完成", failed: "同步失败" })[status] || status || "尚未同步";
    },

    quizSyncStatusClass() {
      if (this.syncStatus() === "failed") return "border-rose-200 bg-rose-50 text-rose-700";
      if (this.syncStatus() === "done") {
        return Number(this.quizSyncResult().error_count || 0) > 0
          ? "border-amber-200 bg-amber-50 text-amber-800"
          : "border-emerald-200 bg-emerald-50 text-emerald-700";
      }
      return this.isSyncBusy() ? "border-blue-200 bg-blue-50 text-blue-700" : "border-slate-200 bg-slate-50 text-slate-600";
    },

    quizSyncCountGroups() {
      const result = this.quizSyncResult();
      if (this.syncStatus() !== "done") return [];
      return [
        { label: "测验", counts: [["扫描", "scanned_md"], ["新增版本", "created_versions"], ["更新版本", "updated_versions"], ["未变", "unchanged_versions"], ["删除", "deleted_exams"]] },
        { label: "职位", counts: [["扫描", "scanned_job_descriptions"], ["新增", "created_job_descriptions"], ["更新", "updated_job_descriptions"], ["未变", "unchanged_job_descriptions"], ["归档", "archived_job_descriptions"]] },
      ].map((group) => ({
        label: group.label,
        counts: group.counts.map(([label, key]) => ({ label, value: Number(result[key] || 0) })),
      }));
    },

    quizQuestions() {
      const questions = this.quizDetail?.selected_quiz_version?.spec?.questions;
      return Array.isArray(questions) ? questions : [];
    },

    questionTypeLabel(value) {
      const labels = {
        single: "单选",
        multiple: "多选",
        short: "简答",
        unknown: "其他",
      };
      const key = String(value || "").trim().toLowerCase();
      return labels[key] || String(value || "其他");
    },

    formatDateTime(value) {
      const text = String(value || "").trim();
      if (!text) return "";
      const date = new Date(text);
      if (Number.isNaN(date.getTime())) {
        return text;
      }
      const year = date.getFullYear();
      const month = String(date.getMonth() + 1).padStart(2, "0");
      const day = String(date.getDate()).padStart(2, "0");
      const hour = String(date.getHours()).padStart(2, "0");
      const minute = String(date.getMinutes()).padStart(2, "0");
      const second = String(date.getSeconds()).padStart(2, "0");
      return `${year}/${month}/${day} ${hour}:${minute}:${second}`;
    },

    formatDate(value) {
      const text = String(value || "").trim();
      if (!text) return "";
      const date = new Date(text);
      if (Number.isNaN(date.getTime())) {
        return text;
      }
      return new Intl.DateTimeFormat("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      }).format(date);
    },

    quizListRespondentBadgeClass() {
      const base = "rounded-full border px-2.5 py-1 text-xs font-normal";
      const active = String(this.filters?.quizzes?.sortBy || "").trim() === "respondent_count";
      return active
        ? `${base} border-blue-200 bg-blue-50 text-blue-700`
        : `${base} border-slate-200 bg-slate-100 text-slate-600`;
    },

    quizListUpdateTimeClass() {
      const active = String(this.filters?.quizzes?.sortBy || "").trim() === "updated_at";
      return active
        ? "shrink-0 whitespace-nowrap text-xs font-normal tabular-nums text-blue-700"
        : "shrink-0 whitespace-nowrap text-xs font-normal tabular-nums text-slate-500";
    },

    quizOptionItems() {
      if (Array.isArray(this.quizOptions) && this.quizOptions.length) {
        return this.quizOptions;
      }
      return Array.isArray(this.quizzes?.items) ? this.quizzes.items : [];
    },

    quizOptionByKey(quizKey) {
      const key = String(quizKey || "").trim();
      if (!key) return null;
      return this.quizOptionItems().find((item) => String(item?.quiz_key || "").trim() === key) || null;
    },

    quizOptionLabel(quizKey) {
      const key = String(quizKey || "").trim();
      if (!key) return "";
      const item = this.quizOptionByKey(key);
      return String(item?.title || item?.quiz_key || key).trim();
    },

    async loadQuizOptions({ quiet = true } = {}) {
      const data = await this.api("/api/admin/quizzes/options", { quiet });
      if (!data) return;
      this.quizOptions = Array.isArray(data?.items) ? data.items : [];
    },

    traitPalette(index) {
      return TRAIT_COLOR_PALETTE[Math.abs(Number(index || 0)) % TRAIT_COLOR_PALETTE.length];
    },

    traitDimensions() {
      const names = [];
      const seen = new Set();
      const usedNames = [];
      const usedSet = new Set();
      const pushUsedName = (value) => {
        const name = String(value || "").trim();
        if (!name || usedSet.has(name)) return;
        usedSet.add(name);
        usedNames.push(name);
      };
      for (const question of this.quizQuestions()) {
        for (const option of question?.options || []) {
          for (const traitName of Object.keys(option?.traits || {})) {
            pushUsedName(traitName);
          }
        }
      }
      if (!usedNames.length) {
        return [];
      }
      const pushName = (value) => {
        const name = String(value || "").trim();
        if (!name || seen.has(name) || !usedSet.has(name)) return;
        seen.add(name);
        names.push(name);
      };
      const configured = this.quizDetail?.selected_quiz_version?.trait?.dimensions || this.quizDetail?.quiz?.trait?.dimensions;
      if (Array.isArray(configured)) {
        configured.forEach(pushName);
      }
      usedNames.forEach(pushName);
      return names.map((name, index) => ({
        name,
        ...this.traitPalette(index),
      }));
    },

    traitMeta(name) {
      const current = String(name || "").trim();
      if (!current) {
        return this.traitPalette(0);
      }
      const found = this.traitDimensions().find((item) => item.name === current);
      if (found) {
        return found;
      }
      const hash = Array.from(current).reduce((sum, char) => sum + (char.codePointAt(0) || 0), 0);
      return {
        name: current,
        ...this.traitPalette(hash),
      };
    },

    traitBadgeStyle(name) {
      const meta = this.traitMeta(name);
      return {
        borderColor: meta.border,
        backgroundColor: meta.background,
        color: meta.text,
      };
    },

    traitDotStyle(name) {
      const meta = this.traitMeta(name);
      return { backgroundColor: meta.accent };
    },

    optionTraits(option) {
      const traits = option?.traits;
      if (!traits || typeof traits !== "object") {
        return [];
      }
      return Object.entries(traits)
        .filter(([name]) => String(name || "").trim())
        .map(([name, score]) => {
          const value = Number(score || 0);
          const text = Number.isFinite(value) && value > 0 ? `+${value}` : String(score ?? "");
          return {
            name: String(name || "").trim(),
            scoreText: text,
          };
        });
    },

    async loadQuizzes({ quiet = false, source = "manual", page = null, previousSyncStatus = "", previousSyncJobId = "" } = {}) {
      const requestId = ++listRequestId;
      const query = new URLSearchParams();
      query.set("page", String(page === null ? this.currentQuizzesPage() : Math.max(1, Math.floor(Number(page) || 1))));
      if (this.filters.quizzes.q) query.set("q", this.filters.quizzes.q);
      if (this.filters.quizzes.public_invite) query.set("public_invite", this.filters.quizzes.public_invite);
      query.set("sort_by", this.filters.quizzes.sortBy || "updated_at");
      query.set("sort_order", this.filters.quizzes.sortOrder || "desc");
      const data = await this.api(`/api/admin/quizzes?${query.toString()}`, { quiet });
      if (!data || requestId !== listRequestId) return;
      this.quizzes = data;
      if (data.filters) {
        Object.assign(this.filters.quizzes, {
          sortBy: String(data.filters.sort_by || "updated_at"),
          sortOrder: String(data.filters.sort_order || "desc"),
        });
      }
      if (source !== "sync-poll" && this.route.name === "quizzes") this.syncAdminListRoute({ replace: true });
      this.repoBinding = data.repo_binding || {};
      this.syncState = data.sync_state || {};
      if (!this.hasRepoBinding() && this.syncState.repo_url && (this.isSyncBusy() || !this.syncForm.repoUrl)) {
        this.syncForm.repoUrl = this.syncState.repo_url;
      }
      if (this.hasRepoBinding()) {
        this.syncForm.repoUrl = "";
      } else {
        this.resetRebindForm();
      }
      const currentSyncStatus = this.syncStatus();
      if (this.route.name === "quizzes" && this.isSyncBusy()) {
        this.scheduleSyncPolling();
      } else {
        this.stopSyncPolling();
      }
      if (
        source === "sync-poll" &&
        ["queued", "pending", "running"].includes(String(previousSyncStatus || "").trim().toLowerCase()) &&
        !["queued", "pending", "running"].includes(currentSyncStatus)
      ) {
        const finishedJobId = String(this.syncState?.last_job_id || "").trim();
        if (!previousSyncJobId || previousSyncJobId === finishedJobId) {
          this.showNotice(currentSyncStatus === "done" ? `${this.quizSyncStatusLabel()}，列表已刷新` : "测验同步失败");
        }
      }
    },

    async toggleQuizSortOrder() {
      this.filters.quizzes.sortOrder = this.filters.quizzes.sortOrder === "asc" ? "desc" : "asc";
      await this.reloadQuizzesFromFirstPage();
    },

    async bindRepo() {
      if (this.isSyncBusy() || this.hasRepoBinding()) return;
      const result = await this.api("/api/admin/quizzes/binding", {
        method: "POST",
        body: JSON.stringify({ repo_url: this.syncForm.repoUrl || "" }),
        headers: { "Content-Type": "application/json" },
      });
      this.repoBinding = result.binding || {};
      this.syncForm.repoUrl = "";
      if (result.sync?.error) {
        this.showNotice("仓库已绑定，但自动同步投递失败");
      } else {
        this.showNotice("仓库已绑定，已开始同步");
      }
      await this.loadQuizzes({ quiet: true });
    },

    async syncQuizzes() {
      if (this.isSyncBusy() || !this.hasRepoBinding()) return;
      const result = await this.api("/api/admin/quizzes/sync", {
        method: "POST",
        body: JSON.stringify({}),
        headers: { "Content-Type": "application/json" },
      });
      this.showNotice(result.created ? "测验同步任务已创建" : "已复用正在运行的同步任务");
      await this.loadQuizzes({ quiet: true });
    },

    async confirmRebind() {
      if (this.isSyncBusy() || !this.hasRepoBinding()) return;
      const result = await this.api("/api/admin/quizzes/binding/rebind", {
        method: "POST",
        body: JSON.stringify({
          repo_url: this.rebindForm.repoUrl || "",
          confirmation_text: this.rebindForm.confirmationText || "",
        }),
        headers: { "Content-Type": "application/json" },
      });
      this.quizzes = { items: [], page: 1, per_page: 20, total: 0, total_pages: 1 };
      this.quizDetail = { quiz: {}, selected_quiz_version: {}, quiz_version_history: [], stats: {} };
      this.repoBinding = result.binding || {};
      this.resetRebindForm();
      if (result.sync?.error) {
        this.showNotice("仓库已重新绑定，现有测验数据已清空，但自动同步投递失败");
      } else {
        this.showNotice("仓库已重新绑定，现有测验数据已清空并开始同步");
      }
      await this.loadQuizzes({ quiet: true });
    },

    async loadQuizDetail(quizKey) {
      const requestId = ++detailRequestId;
      this.quizDetail = { quiz: {}, selected_quiz_version: {}, quiz_version_history: [], stats: {} };
      this.resetQuizAnalyticsDetail();
      const [detail] = await Promise.all([
        this.api(`/api/admin/quizzes/${encodeURIComponent(quizKey)}`),
        this.loadQuizAnalyticsDetail(quizKey, { quiet: true, syncRoute: false }),
      ]);
      if (requestId !== detailRequestId || this.route.name !== "quiz-detail" || this.route.params.quizKey !== quizKey) return;
      this.quizDetail = detail;
      await this.$nextTick();
      this.queueMathTypeset();
    },

    quizDetailTab() {
      const tab = String(this.route?.query?.tab || "").trim();
      return ["content", "analytics", "history"].includes(tab) ? tab : "content";
    },

    quizDetailPanelVisible(tabId) {
      return this.quizDetailTab() === tabId;
    },

    async setQuizDetailTab(tabId) {
      if (!["content", "analytics", "history"].includes(tabId)) return;
      if (this.quizDetailTab() !== tabId) this.setRouteSearchParams({ ...this.route.query, tab: tabId }, { replace: false });
      await this.$nextTick();
      this.queueMathTypeset();
    },

    async handleQuizDetailTabKeydown(event) {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const tabs = ["content", "analytics", "history"];
      const index = tabs.indexOf(this.quizDetailTab());
      const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      await this.setQuizDetailTab(tabs[next]);
      document.getElementById(`quiz-detail-tab-${tabs[next]}`)?.focus();
    },

    async loadQuizVersion(versionId) {
      const requestId = ++detailRequestId;
      const quizKey = this.route.params.quizKey;
      const detail = await this.api(`/api/admin/quiz-versions/${versionId}`);
      if (requestId !== detailRequestId || this.route.name !== "quiz-detail" || this.route.params.quizKey !== quizKey) return;
      this.quizDetail = detail;
      await this.setQuizDetailTab("content");
      await this.$nextTick();
      this.queueMathTypeset();
    },

    async togglePublicInvite() {
      const enabled = !Boolean(this.quizDetail.quiz?.public_invite_enabled);
      const result = await this.api(`/api/admin/quizzes/${encodeURIComponent(this.quizDetail.quiz.quiz_key)}/public-invite`, {
        method: "POST",
        body: JSON.stringify({ enabled }),
        headers: { "Content-Type": "application/json" },
      });
      this.quizDetail.quiz.public_invite_enabled = result.enabled;
      this.quizDetail.quiz.public_invite_token = result.token || "";
      this.quizDetail.quiz.public_invite_url = result.public_url;
      this.quizDetail.quiz.public_invite_qr_url = result.qr_url || "";
      this.showNotice(result.enabled ? "公开邀约已开启" : "公开邀约已关闭");
    },

  };
}
