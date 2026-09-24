import { createSessionId } from "./constants.js";

const BACK_GUARD_WINDOW_MS = 2000;
const BACK_GUARD_HISTORY_STATE_KEY = "__mdQuizPublicBackGuard";

export function createPublicRouterModule() {
  return {
          currentPublicPath() {
            return `${location.pathname}${location.search}${location.hash}`;
          },

          currentHistoryState() {
            const state = window.history.state;
            return state && typeof state === "object" ? state : {};
          },

          isBackGuardHistoryState(state = this.currentHistoryState()) {
            return Boolean(state?.[BACK_GUARD_HISTORY_STATE_KEY]);
          },

          clearBackGuardTimer() {
            if (this.backGuardTimer) {
              window.clearTimeout(this.backGuardTimer);
              this.backGuardTimer = null;
            }
          },

          resetBackGuardPrompt() {
            this.clearBackGuardTimer();
            this.backGuardArmed = false;
            this.backGuardDeadline = 0;
            this.backGuardHintVisible = false;
          },

          armBackGuardPrompt() {
            this.resetBackGuardPrompt();
            this.backGuardArmed = true;
            this.backGuardHintVisible = true;
            this.backGuardDeadline = Date.now() + BACK_GUARD_WINDOW_MS;
            this.backGuardTimer = window.setTimeout(() => {
              this.resetBackGuardPrompt();
            }, BACK_GUARD_WINDOW_MS);
          },

          shouldEnableBackGuard() {
            const step = String(this.state.step || "").trim();
            return this.route.kind === "attempt" && ["verify", "resume", "quiz"].includes(step);
          },

          shouldSkipSameFlowHistory(pathname = location.pathname) {
            const currentToken = String(this.route?.token || "").trim();
            const nextToken = String(this.parseRoute(pathname)?.token || "").trim();
            return Boolean(currentToken && nextToken && currentToken === nextToken);
          },

          shouldSkipSameFlowHistoryOutsideGuard(pathname = location.pathname) {
            return ["done", "unavailable"].includes(String(this.viewCard || "").trim())
              && this.shouldSkipSameFlowHistory(pathname);
          },

          pushBackGuardHistoryEntry() {
            const nextState = {
              ...this.currentHistoryState(),
              [BACK_GUARD_HISTORY_STATE_KEY]: true,
            };
            window.history.pushState(nextState, "", this.currentPublicPath());
            this.backGuardHistoryArmed = true;
          },

          syncBackGuardAfterRoute() {
            if (this.shouldEnableBackGuard()) {
              this.backGuardBypass = false;
              this.backGuardSkipToken = "";
              if (!this.isBackGuardHistoryState()) {
                this.pushBackGuardHistoryEntry();
              } else {
                this.backGuardHistoryArmed = true;
              }
              return;
            }
            this.resetBackGuardPrompt();
            this.backGuardBypass = false;
            this.backGuardSkipToken = "";
            this.backGuardHistoryArmed = false;
          },

          handleBeforeUnload(event) {
            if (this.backGuardBypass || !this.shouldEnableBackGuard()) return undefined;
            event.preventDefault();
            event.returnValue = "";
            return "";
          },

          async handlePopState(event) {
            const nextPathname = location.pathname;
            if (this.backGuardBypass) {
              if (this.backGuardSkipToken && this.shouldSkipSameFlowHistory(nextPathname)) {
                window.history.back();
                return;
              }
              this.backGuardBypass = false;
              this.backGuardSkipToken = "";
              this.backGuardHistoryArmed = this.isBackGuardHistoryState(event?.state);
              await this.syncRoute(nextPathname);
              return;
            }

            if (!this.shouldEnableBackGuard()) {
              if (this.shouldSkipSameFlowHistoryOutsideGuard(nextPathname)) {
                window.history.back();
                return;
              }
              this.backGuardHistoryArmed = this.isBackGuardHistoryState(event?.state);
              await this.syncRoute(nextPathname);
              return;
            }

            const stillArmed = this.backGuardArmed && Date.now() < Number(this.backGuardDeadline || 0);
            if (!stillArmed) {
              this.armBackGuardPrompt();
              this.pushBackGuardHistoryEntry();
              return;
            }

            this.resetBackGuardPrompt();
            this.backGuardBypass = true;
            this.backGuardSkipToken = String(this.route?.token || "").trim();
            this.backGuardHistoryArmed = false;
            window.history.back();
          },

          async init() {
            if (!this._popstateHandler) {
              this._popstateHandler = (event) => {
                this.handlePopState(event).catch((error) => {
                  this.error = error.message || "页面切换失败";
                });
              };
              window.addEventListener("popstate", this._popstateHandler);
            }
            if (!this._beforeUnloadHandler) {
              this._beforeUnloadHandler = (event) => this.handleBeforeUnload(event);
              window.addEventListener("beforeunload", this._beforeUnloadHandler);
            }
            try {
              await this.syncRoute(location.pathname);
            } catch (error) {
              this.error = error.message || "页面初始化失败";
            } finally {
              this.booting = false;
            }
          },

          async syncRoute(pathname) {
            this.error = "";
            this.resetSmsState();
            this.route = this.parseRoute(pathname);
            if (this.route.kind === "invite") {
              await this.ensureInvite(this.route.token);
              return;
            }
            if (!this.route.token) {
              throw new Error("无效链接");
            }
            this.ensureSession(this.route.token);
            await this.loadAttempt(this.route.token);
          },

          parseRoute(pathname) {
            let match = pathname.match(/^\/p\/([^/]+)$/);
            if (match) return { kind: "invite", token: decodeURIComponent(match[1]) };
            match = pathname.match(/^\/(?:t|resume|quiz|exam|done|a)\/([^/]+)$/);
            if (match) return { kind: "attempt", token: decodeURIComponent(match[1]) };
            return { kind: "invalid", token: "" };
          },

          sessionStorageKey(token) {
            return `md-quiz-public-session:${token}`;
          },

          ensureSession(token) {
            if (!token) return "";
            const key = this.sessionStorageKey(token);
            let sessionId = window.sessionStorage.getItem(key) || "";
            if (!sessionId) {
              sessionId = createSessionId();
              window.sessionStorage.setItem(key, sessionId);
            }
            this.sessionId = sessionId;
            return sessionId;
          },

          async ensureInvite(publicToken) {
            const result = await this.api(`/api/public/invites/${encodeURIComponent(publicToken)}/ensure`, {
              method: "POST",
            });
            history.replaceState({}, "", result.redirect);
            this.route = { kind: "attempt", token: result.token };
            this.ensureSession(result.token);
            await this.loadAttempt(result.token);
          },

    async syncFromState() {
          this.clearAutosaveTimer();
          this.stopQuestionTimer();
          this.verifySubmitting = false;
          if (this.state.step !== "verify") {
            this.resetSmsState();
          }

          if (this.state.step === "verify") {
            this.viewCard = "start";
            if (this.state.verify?.mode !== "direct_phone") {
              this.forms.verify.name = this.state.verify?.name || "";
              this.forms.verify.phone = this.state.verify?.phone || "";
            }
            this.resetVerifyCode();
          } else if (this.state.step === "resume") {
            this.viewCard = "resume";
          } else if (this.state.step === "quiz") {
            this.viewCard = this.state.quiz?.entered_at ? "question" : "start";
          } else if (this.state.step === "done") {
            this.viewCard = "done";
          } else {
            this.viewCard = "unavailable";
          }

          const question = this.currentQuestion();
          const answer = this.currentAnswer();
          if (question?.type === "short") {
            this.textDraft = typeof answer === "string" ? answer : "";
            this.selectedMultiple = [];
          } else if (question?.type === "multiple") {
            this.selectedMultiple = Array.isArray(answer) ? [...answer] : [];
            this.textDraft = "";
          } else {
            this.textDraft = "";
            this.selectedMultiple = [];
          }

          this.syncQuestionClock(question);
          this.resetActivityCapture(question);

          this.autosaveMessage = this.state.quiz?.entered_at ? this.deferredSaveText(question) : "";
          this.syncQuestionTimer();
          await this.renderCurrentView();
          await this.$nextTick();
          if (this.state.step === "verify") {
            this.focusOtpInput(0);
          }
          this.queueMathTypeset();
          this.syncBackGuardAfterRoute();
        },

          async loadAttempt(token) {
            const data = await this.api(`/api/public/attempt/${encodeURIComponent(token)}`);
            this.state = data || this.state;
            await this.syncFromState();
          },
  };
}
