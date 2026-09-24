const LIST_ROUTES = {
  quizzes: { state: "quizzes", fields: { q: "q", public_invite: "public_invite", sort_by: "sortBy", sort_order: "sortOrder" } },
  candidates: { state: "candidates", fields: { q: "q", created_from: "created_from", created_to: "created_to" } },
  assignments: { state: "assignments", fields: { q: "q", status: "status", handled: "handled", quiz_key: "quiz_key", start_from: "start_from", end_to: "end_to" } },
  logs: { state: "logs", fields: { q: "q", category: "category" } },
  "job-descriptions": { state: "jobDescriptions", fields: { q: "q", status: "status" } },
};

export function createAdminNavigationModule() {
  return {
    createAdminHistoryEntryId() {
      // 内网 HTTP 也需要导航标识；getRandomValues 不要求安全上下文。
      const bytes = crypto.getRandomValues(new Uint8Array(16));
      return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
    },

    applyAdminListRoute(route = this.route) {
      const config = LIST_ROUTES[route.name];
      if (!config) return;
      const query = route.query || {};
      const defaults = route.name === "quizzes"
        ? { sort_by: "updated_at", sort_order: "desc" }
        : route.name === "assignments" ? this.assignmentDefaultDates : {};
      for (const [key, field] of Object.entries(config.fields)) {
        this.filters[config.state][field] = Object.hasOwn(query, key)
          ? String(query[key] || "") : String(defaults[key] || "");
      }
      if (route.name === "quizzes" && !["", "enabled", "disabled"].includes(this.filters.quizzes.public_invite)) {
        this.filters.quizzes.public_invite = "";
      }
      const page = Number(query.page || 1);
      this[config.state].page = Number.isInteger(page) && page > 0 ? page : 1;
    },

    syncAdminListRoute({ replace = true } = {}) {
      const config = LIST_ROUTES[this.route.name];
      if (!config) return;
      const query = {};
      for (const [key, field] of Object.entries(config.fields)) {
        const value = String(this.filters[config.state][field] ?? "").trim();
        if (value || (this.route.name === "assignments" && ["start_from", "end_to"].includes(key))) query[key] = value;
      }
      query.page = String(this[config.state].page || 1);
      this.setRouteSearchParams(query, { replace });
      this.adminListLocations[this.route.name] = this.route.fullPath;
    },

    rememberAdminScroll() {
      if (this.adminRouteLoading) return;
      const current = history.state || {};
      const entryId = current.admin?.entryId || this.createAdminHistoryEntryId();
      this.adminScrollPositions[entryId] = window.scrollY;
      history.replaceState({ ...current, admin: { ...current.admin, entryId, scrollY: window.scrollY } }, "", location.href);
      if (LIST_ROUTES[this.route.name]) this.adminListLocations[this.route.name] = this.route.fullPath;
    },

    trackAdminScroll() {
      if (this.adminRouteLoading) return;
      const entryId = history.state?.admin?.entryId;
      if (entryId) this.adminScrollPositions[entryId] = window.scrollY;
      const href = location.href;
      window.clearTimeout(this.adminScrollTimer);
      this.adminScrollTimer = window.setTimeout(() => {
        if (location.href === href) this.rememberAdminScroll();
      }, 300);
    },

    adminNavigationHref(item) {
      const route = this.resolveRoute(item.href);
      return this.adminListLocations[route.name] || item.href;
    },

    adminDetailTitle() {
      if (this.route.name === "quiz-detail" && this.quizDetail?.quiz?.quiz_key === this.route.params.quizKey) {
        return this.quizDetail.quiz.title || this.route.title;
      }
      if (this.route.name === "candidate-detail" && Number(this.candidateDetail?.candidate?.id) === this.route.params.candidateId) {
        return this.candidateDetail.candidate.name || this.route.title;
      }
      if (this.route.name === "attempt-detail") {
        const row = this.attemptDetail?.quiz_paper || {};
        const assignment = this.attemptDetail?.assignment || {};
        if (String(row.token || assignment.token || "") === this.route.params.token) {
          const quizKey = row.quiz_key || assignment.quiz_key;
          const quizTitle = this.attemptDetail?.quiz_title || row.quiz_title || assignment.quiz_title || this.quizOptionByKey(quizKey)?.title || quizKey;
          return [row.candidate_name || assignment.candidate_name, quizTitle].filter(Boolean).join(" · ") || this.route.title;
        }
      }
      return this.route.title;
    },

    adminBreadcrumbParent() {
      const parent = this.adminParentNavItem();
      if (!parent) return null;
      const source = history.state?.admin?.source;
      if (source?.href?.startsWith("/admin/") && source.href !== this.route.fullPath) {
        return { href: source.href, label: source.label || parent.label };
      }
      return { ...parent, href: this.adminNavigationHref(parent) };
    },

    async returnToAdminParent(event) {
      const parent = this.adminBreadcrumbParent();
      if (!parent) return;
      if (event && (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)) return;
      event?.preventDefault();
      const source = history.state?.admin?.source;
      const destination = this.normalizeRouteLocation(parent.href);
      await this.handleRoute(destination.pathname, {
        search: destination.search,
        restoreScroll: source?.scrollY || 0,
        parentSource: source?.parent || null,
      });
    },
  };
}
