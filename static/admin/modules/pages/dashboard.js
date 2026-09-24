export function createAdminDashboardModule() {
  return {
    async loadDashboard() {
      const requestId = ++this.dashboardRequestId;
      this.dashboardLoading = true;
      this.dashboardError = "";
      try {
        const query = new URLSearchParams({ tz_offset_minutes: String(this.browserTzOffsetMinutes()) });
        const data = await this.api(`/api/admin/dashboard?${query}`, { quiet: true });
        if (requestId !== this.dashboardRequestId || !data) return;
        this.dashboard = data;
      } catch (_error) {
        if (requestId === this.dashboardRequestId) this.dashboardError = "总览更新失败，请重试。已加载的数据仍保留。";
      } finally {
        if (requestId === this.dashboardRequestId) this.dashboardLoading = false;
      }
    },

    dashboardJobLabel(kind) {
      return {
        git_sync_exams: "仓库同步",
        admin_candidate_resume_reparse: "简历重新解析",
        resume_parse: "简历解析",
        grade_attempt: "答卷判卷",
        sync_metrics: "指标同步",
      }[kind] || kind || "后台任务";
    },

    dashboardWindowLabel() {
      const window = this.dashboard?.window;
      return window ? `${window.start_date} 至 ${window.end_date}` : "近七个自然日，含今天";
    },
  };
}
