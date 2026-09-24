import { copyTextToClipboard, queueMathTypeset } from "/static/assets/js/shared/runtime.js";
import { ADMIN_COMPACT_BREAKPOINT_QUERY, ADMIN_COMPACT_TAB_CONFIG } from "./constants.js";

const ADMIN_SIDEBAR_STORAGE_KEY = "md-quiz-admin-sidebar-collapsed";

export function createAdminShellModule() {
  return {
    async boot() {
      this.booting = true;
      this.bootError = "";
      try {
        this.initAdminSidebar();
        this.initAdminCompactLayout();
        history.scrollRestoration = "manual";
        if (!history.state?.admin?.entryId) this.rememberAdminScroll();
        window.addEventListener("scroll", () => this.trackAdminScroll(), { passive: true });
        window.addEventListener("popstate", () =>
          this.handleRoute(location.pathname, { replace: true, search: location.search, fromPop: true }),
        );
        window.addEventListener("beforeunload", (event) => {
          this.rememberAdminScroll();
          if (!this.hasUnsavedJobDescriptionChanges?.()) return;
          event.preventDefault();
          event.returnValue = "";
        });
        document.addEventListener("keydown", (event) => this.handleAdminTabKeydown(event));
        await this.refreshSession();
        if (this.session.authenticated) {
          await this.loadBootstrap();
          await this.handleRoute(location.pathname, { replace: true, search: location.search });
        } else {
          this.route = this.resolveRoute("/admin/login", "");
          await this.renderCurrentRoute();
        }
      } catch (error) {
        this.bootError = error?.message || "请检查网络连接后重新加载。";
      } finally {
        this.booting = false;
      }
    },

    initAdminSidebar() {
      try {
        this.isAdminSidebarCollapsed = window.localStorage.getItem(ADMIN_SIDEBAR_STORAGE_KEY) === "true";
      } catch (_error) {
        // 浏览器禁用存储时，侧栏仍可在当前会话中切换。
        this.isAdminSidebarCollapsed = false;
      }
    },

    async toggleAdminSidebar() {
      this.isAdminSidebarCollapsed = !this.isAdminSidebarCollapsed;
      try {
        window.localStorage.setItem(ADMIN_SIDEBAR_STORAGE_KEY, String(this.isAdminSidebarCollapsed));
      } catch (_error) {
        this.showNotice("浏览器未允许保存侧栏偏好，本次调整仍然生效");
      }
      await this.$nextTick();
      this.updateAdminStickyLayoutState();
    },

    async openAdminUserMenu() {
      this.userMenuOpen = true;
      await this.$nextTick();
      this.$refs.userMenuLogout?.focus();
    },

    closeAdminUserMenu() {
      this.userMenuOpen = false;
      this.$refs.userMenuButton?.focus();
    },

    toggleAdminUserMenu() {
      if (this.userMenuOpen) {
        this.closeAdminUserMenu();
      } else {
        this.openAdminUserMenu();
      }
    },

    adminParentNavItem() {
      const parentPaths = {
        "quiz-detail": "/admin/quizzes",
        "candidate-detail": "/admin/candidates",
        "attempt-detail": "/admin/assignments",
      };
      return this.navItems.find((item) => item.href === parentPaths[this.route?.name]) || null;
    },

    adminSystemStatusLabel() {
      return {
        ok: "运行正常",
        warn: "需要关注",
        danger: "状态异常",
        critical: "严重异常",
      }[this.statusSummary?.overall_level] || "状态未知";
    },

    initAdminCompactLayout() {
      if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
        return;
      }
      if (!this.adminRightStackResizeHandler) {
        this.adminRightStackResizeHandler = () => this.updateAdminRightStackStickyOffsets();
        window.addEventListener("resize", this.adminRightStackResizeHandler, { passive: true });
      }
      if (!this.adminCompactMediaQuery) {
        this.adminCompactMediaQuery = window.matchMedia(ADMIN_COMPACT_BREAKPOINT_QUERY);
        this.adminCompactMediaQueryHandler = (event) => {
          this.handleAdminCompactLayoutChange(Boolean(event?.matches));
        };
        if (typeof this.adminCompactMediaQuery.addEventListener === "function") {
          this.adminCompactMediaQuery.addEventListener("change", this.adminCompactMediaQueryHandler);
        } else if (typeof this.adminCompactMediaQuery.addListener === "function") {
          this.adminCompactMediaQuery.addListener(this.adminCompactMediaQueryHandler);
        }
      }
      this.handleAdminCompactLayoutChange(Boolean(this.adminCompactMediaQuery.matches));
    },

    async handleAdminCompactLayoutChange(matches) {
      this.isAdminCompactLayout = Boolean(matches);
      this.ensureAdminCompactTab(this.route?.name);
      await this.$nextTick();
      if (this.route?.name === "logs") {
        if (this.shouldRenderLogsChart()) {
          this.renderLogsChart();
        } else {
          this.destroyLogsChart();
        }
      }
      this.updateAdminStickyLayoutState();
    },

    adminCompactTabConfig(routeName) {
      return ADMIN_COMPACT_TAB_CONFIG[String(routeName || "").trim()] || null;
    },

    adminCompactTabs(routeName) {
      return this.adminCompactTabConfig(routeName)?.tabs || [];
    },

    ensureAdminCompactTab(routeName) {
      const key = String(routeName || "").trim();
      const config = this.adminCompactTabConfig(key);
      if (!config) return;
      const currentTab = String(this.adminCompactTabsState?.[key] || "").trim();
      const validTabIds = new Set((config.tabs || []).map((item) => item.id));
      if (!validTabIds.has(currentTab)) {
        this.adminCompactTabsState = {
          ...(this.adminCompactTabsState || {}),
          [key]: config.defaultTab,
        };
      }
    },

    adminCompactTab(routeName) {
      const key = String(routeName || "").trim();
      if (key === "status") return this.route.query?.tab === "config" ? "config" : "summary";
      const config = this.adminCompactTabConfig(key);
      if (!config) return "";
      this.ensureAdminCompactTab(key);
      return String(this.adminCompactTabsState?.[key] || config.defaultTab || "").trim();
    },

    adminCompactPanelVisible(routeName, tabId) {
      if (routeName === "status") return this.adminCompactTab(routeName) === tabId;
      if (!this.isAdminCompactLayout || !this.adminCompactTabs(routeName).length) {
        return true;
      }
      return this.adminCompactTab(routeName) === String(tabId || "").trim();
    },

    shouldShowAdminCompactTabs(routeName) {
      if (routeName === "status") return true;
      return this.isAdminCompactLayout && this.adminCompactTabs(routeName).length > 0;
    },

    updateAdminStickyLayoutState() {
      this.revealAdminActiveNavigation();
      this.updateAdminRightStackStickyOffsets();
      this.syncAdminTabSemantics();
    },

    syncAdminTabSemantics() {
      const routeName = this.route.name;
      const tabs = this.adminCompactTabs(routeName);
      if (!tabs.length) return;
      const groups = Array.from(document.querySelectorAll(`[data-admin-compact-tabs="${routeName}"] [role="tablist"]`));
      for (const [groupIndex, group] of groups.entries()) {
        const visible = group.getClientRects().length > 0;
        for (const [index, button] of Array.from(group.querySelectorAll('[role="tab"]')).entries()) {
          const tab = tabs[index];
          if (!tab) continue;
          const selected = this.adminCompactTab(routeName) === tab.id;
          button.id = `admin-tab-${routeName}-${groupIndex}-${tab.id}`;
          button.dataset.adminTab = tab.id;
          button.tabIndex = selected ? 0 : -1;
          button.setAttribute("aria-selected", String(selected));
          const expected = `adminCompactPanelVisible('${routeName}', '${tab.id}')`.replaceAll(" ", "");
          const panel = Array.from(document.querySelectorAll("[x-show]")).find((node) => String(node.getAttribute("x-show")).replaceAll(" ", "") === expected);
          if (panel) {
            panel.id = `admin-panel-${routeName}-${tab.id}`;
            button.setAttribute("aria-controls", panel.id);
            if (visible) {
              panel.setAttribute("role", "tabpanel");
              panel.setAttribute("aria-labelledby", button.id);
            }
          }
        }
      }
    },

    async handleAdminTabKeydown(event) {
      if (event.defaultPrevented) return;
      const button = event.target.closest?.('[role="tab"][data-admin-tab]');
      if (!button || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      const buttons = Array.from(button.closest('[role="tablist"]').querySelectorAll('[role="tab"]'));
      const index = buttons.indexOf(button);
      const target = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length;
      event.preventDefault();
      await this.setAdminCompactTab(this.route.name, buttons[target].dataset.adminTab);
      buttons[target].focus();
    },

    updateAdminRightStackStickyOffsets() {
      if (typeof window === "undefined" || typeof document === "undefined") {
        return;
      }
      const nodes = Array.from(document.querySelectorAll(".admin-right-pane--stack"));
      const viewportHeight = window.innerHeight || document.documentElement?.clientHeight || 0;
      const gap = 20;
      const headerHeight = document.querySelector(".admin-topbar-shell")?.getBoundingClientRect().height || 72;
      for (const node of nodes) {
        if (!(node instanceof HTMLElement)) {
          continue;
        }
        if (!node.getClientRects().length || viewportHeight <= 0) {
          node.style.removeProperty("--admin-right-pane-stack-top");
          continue;
        }
        const height = Math.ceil(node.getBoundingClientRect().height || node.scrollHeight || 0);
        const stickyTop = Math.min(headerHeight + gap, viewportHeight - height - gap);
        node.style.setProperty("--admin-right-pane-stack-top", `${Math.round(stickyTop)}px`);
      }
    },

    isPrimaryNavItemActive(href) {
      const target = String(href || "").trim();
      if (!target) return false;
      return (this.adminParentNavItem()?.href || this.route?.path) === target;
    },

    revealAdminActiveNavigation() {
      const nav = document.querySelector(".admin-mobile-nav-shell nav");
      const active = nav?.querySelector('[aria-current="page"]');
      if (!active || !nav.getClientRects().length) return;
      const bounds = nav.getBoundingClientRect();
      const item = active.getBoundingClientRect();
      if (item.right > bounds.right) nav.scrollLeft += item.right - bounds.right + 8;
      else if (item.left < bounds.left) nav.scrollLeft -= bounds.left - item.left + 8;
    },

    async setAdminCompactTab(routeName, tabId, { scroll = false } = {}) {
      const key = String(routeName || "").trim();
      const nextTab = String(tabId || "").trim();
      const config = this.adminCompactTabConfig(key);
      if (!config || !(config.tabs || []).some((item) => item.id === nextTab)) {
        return;
      }
      if (key === "attempt-detail" && nextTab === "review") this.attemptEvaluationExpanded = false;
      this.adminCompactTabsState = {
        ...(this.adminCompactTabsState || {}),
        [key]: nextTab,
      };
      if (key === "status") this.setRouteSearchParams({ ...this.route.query, tab: nextTab });
      await this.$nextTick();
      if (key === "logs") {
        if (this.shouldRenderLogsChart()) {
          this.renderLogsChart();
        } else {
          this.destroyLogsChart();
        }
      }
      if (scroll && this.isAdminCompactLayout) {
        this.scrollAdminCompactTabsIntoView(key);
      }
      this.updateAdminStickyLayoutState();
    },

    resetAdminCompactTab(routeName) {
      const key = String(routeName || "").trim();
      if (!this.adminCompactTabConfig(key)) return;
      const nextState = { ...(this.adminCompactTabsState || {}) };
      delete nextState[key];
      this.adminCompactTabsState = nextState;
    },

    shouldRenderLogsChart() {
      if (this.route?.name !== "logs") return false;
      return !this.isAdminCompactLayout || this.adminCompactPanelVisible("logs", "trend");
    },

    scrollAdminCompactTabsIntoView(routeName) {
      if (typeof document === "undefined") return;
      const selector = `[data-admin-compact-tabs="${String(routeName || "").trim()}"]`;
      const candidates = Array.from(document.querySelectorAll(selector));
      const target = candidates.find((node) => node instanceof HTMLElement && node.getClientRects().length > 0) || candidates[0];
      if (!target || typeof target.scrollIntoView !== "function") return;
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      target.scrollIntoView({ behavior: reducedMotion ? "instant" : "smooth", block: "start" });
    },

    pretty(value) {
      return JSON.stringify(value || {}, null, 2);
    },

    queueMathTypeset(root = null) {
      queueMathTypeset(root instanceof Element ? root : this.$root);
    },

    removeNotice(noticeId) {
      const targetId = Number(noticeId || 0);
      this.notices = (Array.isArray(this.notices) ? this.notices : []).filter(
        (item) => Number(item?.id || 0) !== targetId,
      );
      this.notice = this.notices.length
        ? String(this.notices[this.notices.length - 1]?.message || "")
        : "";
    },

    showNotice(message) {
      const text = String(message || "").trim();
      if (!text) return;
      const id = ++this.noticeSeq;
      const next = [...(Array.isArray(this.notices) ? this.notices : []), { id, message: text }];
      this.notices = next.slice(-3);
      this.notice = text;
      window.setTimeout(() => {
        this.removeNotice(id);
      }, 3600);
    },

    scheduleAssignmentsReload() {
      window.clearTimeout(this.assignmentsFilterTimer);
      this.assignmentsFilterTimer = window.setTimeout(() => {
        this.loadAssignments();
      }, 220);
    },

    async copyText(text, successMessage) {
      await copyTextToClipboard(text);
      this.showNotice(successMessage || "内容已复制");
      return true;
    },

    async login() {
      this.error = "";
      try {
        await this.api("/api/admin/session/login", {
          method: "POST",
          body: JSON.stringify(this.loginForm),
          headers: { "Content-Type": "application/json" },
        });
        await this.refreshSession();
        this.loginForm = { username: "", password: "" };
        await this.$nextTick();
        await this.loadBootstrap();
        await this.handleRoute("/admin/quizzes");
      } catch (error) {
        this.error = error.message || "登录失败";
      }
    },

    async logout() {
      if (this.confirmJobDescriptionLeave && !(await this.confirmJobDescriptionLeave())) return;
      this.userMenuOpen = false;
      this.destroyLogsChart();
      this.stopSyncPolling();
      this.stopAssignmentsPolling();
      this.stopCandidateResumeUploadPolling();
      this.stopCandidateResumeReparsePolling();
      window.clearTimeout(this.jobDescriptionsFilterTimer);
      this.jobDescriptionsFilterTimer = null;
      this.adminCompactTabsState = {};
      this.adminListLocations = {};
      this.dashboard = null;
      await this.api("/api/admin/session/logout", { method: "POST", quiet: true });
      this.session = { authenticated: false, username: "" };
      this.loginForm = { username: "", password: "" };
      this.error = "";
      this.repoBinding = {};
      this.mcpSummary = {};
      this.mcpTokenVisible = false;
      this.resetRebindForm();
      history.replaceState({}, "", "/admin/login");
      this.route = this.resolveRoute("/admin/login", "");
      await this.renderCurrentRoute();
    },
  };
}
