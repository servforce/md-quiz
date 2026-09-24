import { absoluteUrl } from "/static/assets/js/shared/runtime.js";

export function createAdminStatusModule() {
  return {
    formatNumber(value) {
      const number = Number(value || 0);
      if (!Number.isFinite(number)) {
        return "0";
      }
      return new Intl.NumberFormat("zh-CN").format(number);
    },

    statusRangeLabel() {
      const data = this.statusRange?.data || {};
      if (!data.range_start || !data.range_end) {
        return "近 30 天";
      }
      return `${data.range_start} 至 ${data.range_end}`;
    },

    statusConfigAlerts() {
      return Array.isArray(this.statusSummary?.config_alerts) ? this.statusSummary.config_alerts : [];
    },

    statusModuleConfigured(key) {
      const module = this.statusSummary?.[key];
      if (!module || typeof module.configured !== "boolean") {
        return true;
      }
      return Boolean(module.configured);
    },

    statusModuleMissingFields(key) {
      return Array.isArray(this.statusSummary?.[key]?.missing_fields) ? this.statusSummary[key].missing_fields : [];
    },

    statusModuleMissingText(key) {
      const fields = this.statusModuleMissingFields(key);
      return fields.length ? `缺少 ${fields.join("、")}` : "";
    },

    statusDailyRows() {
      const items = Array.isArray(this.statusRange?.data?.items) ? this.statusRange.data.items : [];
      return [...items].reverse();
    },

    statusDailyTotals() {
      const totals = {
        quizzes_new: 0,
        invites_new: 0,
        candidates_new: 0,
        llm_tokens: 0,
        sms_calls: 0,
      };
      for (const item of this.statusDailyRows()) {
        totals.quizzes_new += Number(item?.quizzes_new || 0);
        totals.invites_new += Number(item?.invites_new || 0);
        totals.candidates_new += Number(item?.candidates_new || 0);
        totals.llm_tokens += Number(item?.llm_tokens || 0);
        totals.sms_calls += Number(item?.sms_calls || 0);
      }
      return totals;
    },

    browserTzOffsetMinutes() {
      return -new Date().getTimezoneOffset();
    },

    async loadStatusSummary() {
      this.statusSummary = await this.api("/api/admin/system-status/summary", { quiet: true }) || {};
    },

    async loadSystemBootstrap() {
      this.systemBootstrap = await this.api("/api/system/bootstrap", { quiet: true }) || {};
    },

    async loadStatus() {
      const data = await this.api("/api/admin/system-status");
      if (!data) return;
      this.statusRange = data;
      if (!this.statusConfigBaseline || JSON.stringify(this.statusConfig) === this.statusConfigBaseline) {
        this.statusConfig = { ...(data.config || {}) };
        this.statusConfigBaseline = JSON.stringify(this.statusConfig);
      }
      this.statusSummary = data?.summary || {};
      if (!Object.keys(this.statusSummary || {}).length) {
        await this.loadStatusSummary();
      }
    },

    async loadMcpPage() {
      if (!this.systemBootstrap?.mcp) {
        await this.loadSystemBootstrap();
      }
      this.mcpSummary = await this.api("/api/admin/mcp/summary", { quiet: true }) || {};
      this.mcpTokenVisible = false;
    },

    mcpInfo() {
      return {
        ...(this.systemBootstrap?.mcp || {}),
        ...(this.mcpSummary || {}),
      };
    },

    absoluteUrl(path) {
      return absoluteUrl(path);
    },

    mcpUrl() {
      return this.absoluteUrl(this.mcpInfo().path || "");
    },

    mcpDocsUrl() {
      return this.absoluteUrl(this.mcpInfo().docs_path || "");
    },

    mcpAuthToken() {
      return String(this.mcpInfo().auth_token || "").trim();
    },

    mcpHasAuthToken() {
      return Boolean(this.mcpAuthToken());
    },

    mcpAuthTokenDisplay() {
      const value = this.mcpAuthToken();
      if (!value) {
        return "未设置";
      }
      return this.mcpTokenVisible ? value : "****";
    },

    toggleMcpTokenVisibility() {
      if (!this.mcpHasAuthToken()) return;
      this.mcpTokenVisible = !this.mcpTokenVisible;
    },

    mcpTokenVisibilityIcon() {
      return this.mcpTokenVisible ? "visibility_off" : "visibility";
    },

    mcpTokenVisibilityLabel() {
      return this.mcpTokenVisible ? "隐藏 Token" : "显示 Token";
    },

    async copyMcpUrl() {
      const value = this.mcpUrl();
      if (!value) return;
      await this.copyText(value, "MCP 地址已复制");
    },

    async copyMcpToken() {
      const value = this.mcpAuthToken();
      if (!value) return;
      await this.copyText(value, "Bearer Token 已复制");
    },

    mcpClientConfigs() {
      const url = this.mcpUrl() || "https://your-host.example.com/mcp";
      const tokenPlaceholder = "<上方 Bearer Token>";
      const iconUrl = (path) => this.absoluteUrl(`${path}?v=20260406b`);
      return [
        {
          key: "openclaw",
          label: "OpenClaw",
          icon: iconUrl("/static/assets/img/brands/openclaw.svg"),
          location: "OpenClaw 配置里的 `mcp.servers`，也可用 `openclaw mcp set` 写入。",
          format: "json",
          snippet: JSON.stringify(
            {
              mcp: {
                servers: {
                  mdQuiz: {
                    url,
                    transport: "streamable-http",
                    headers: {
                      Authorization: `Bearer ${tokenPlaceholder}`,
                    },
                  },
                },
              },
            },
            null,
            2,
          ),
        },
        {
          key: "vscode",
          label: "VS Code",
          icon: iconUrl("/static/assets/img/brands/vscode.png"),
          location: "写到工作区 `.vscode/mcp.json`，或通过 `MCP: Open User Configuration` 打开用户级 `mcp.json`。",
          format: "json",
          snippet: JSON.stringify(
            {
              inputs: [
                {
                  type: "promptString",
                  id: "md-quiz-mcp-token",
                  description: "MD Quiz MCP Bearer Token",
                  password: true,
                },
              ],
              servers: {
                mdQuiz: {
                  type: "http",
                  url,
                  headers: {
                    Authorization: "Bearer ${input:md-quiz-mcp-token}",
                  },
                },
              },
            },
            null,
            2,
          ),
        },
        {
          key: "codex",
          label: "Codex",
          icon: iconUrl("/static/assets/img/brands/codex.png"),
          location: "写到 `~/.codex/config.toml`，并先把 Bearer Token 放进环境变量 `MD_QUIZ_MCP_TOKEN`。",
          format: "toml",
          snippet: [
            "[mcp_servers.mdQuiz]",
            'enabled = true',
            `url = "${url}"`,
            'bearer_token_env_var = "MD_QUIZ_MCP_TOKEN"',
          ].join("\n"),
        },
      ];
    },

    selectedMcpClient() {
      const clients = this.mcpClientConfigs();
      return clients.find((item) => item.key === this.mcpSelectedClient) || clients[0];
    },

    async copyMcpClientConfig() {
      try {
        await this.copyText(this.selectedMcpClient().snippet, "客户端配置已复制");
      } catch (_error) {
        this.showNotice("复制失败，请选择配置文本手动复制");
      }
    },

    openMcpDocs() {
      const target = this.mcpDocsUrl();
      if (!target || typeof window === "undefined") return;
      window.open(target, "_blank", "noopener,noreferrer");
    },

    async saveStatusConfig() {
      if (this.statusConfigSaving) return;
      this.statusConfigSaving = true;
      try {
      const data = await this.api("/api/admin/system-status/config", {
        method: "PUT",
        body: JSON.stringify(this.statusConfig),
        headers: { "Content-Type": "application/json" },
      });
      if (!data) return;
      this.statusSummary = data.summary || {};
      this.statusConfig = { ...(data.config || this.statusConfig) };
      this.statusConfigBaseline = JSON.stringify(this.statusConfig);
      this.showNotice("系统阈值已保存");
      } catch (_error) {
        this.showNotice("保存失败，当前修改已保留");
      } finally {
        this.statusConfigSaving = false;
      }
    },
  };
}
