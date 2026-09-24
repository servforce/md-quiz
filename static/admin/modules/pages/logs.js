import { LOG_SERIES_META, LOG_TREND_WINDOW_DAYS, formatLogTrendCount } from "../constants.js";
export function createAdminLogsModule() {
  return {
    logCategoryCards() {
      return LOG_SERIES_META.map((item) => ({
        ...item,
        count: Number(this.logs?.counts?.[item.key] || 0),
      }));
    },

    logTrendRangeLabel() {
      const trend = this.logs?.trend || {};
      if (!trend.start_day || !trend.end_day) {
        return `近 ${LOG_TREND_WINDOW_DAYS} 天`;
      }
      return `${trend.start_day} 至 ${trend.end_day}`;
    },

    hasLogTrendData() {
      return LOG_SERIES_META.some((item) =>
        (this.logs?.trend?.series?.[item.key] || []).some((point) => Number(point?.count || 0) > 0),
      );
    },

    destroyLogsChart() {
      if (this.logsChartResizeObserver) {
        this.logsChartResizeObserver.disconnect();
        this.logsChartResizeObserver = null;
      }
      if (this.logsChartWindowResize) {
        window.removeEventListener("resize", this.logsChartWindowResize);
        this.logsChartWindowResize = null;
      }
      if (this.logsChart && typeof this.logsChart.remove === "function") {
        this.logsChart.remove();
      }
      this.logsChart = null;
      this.logsChartContainer = null;
      this.logsChartSeries = {};
    },

    resizeLogsChart() {
      const chart = this.logsChart;
      const container = this.logsChartContainer;
      if (!chart || !container) return;
      const width = Math.max(Math.round(container.clientWidth || 0), 280);
      const height = Math.max(Math.round(container.clientHeight || 0), 320);
      if (typeof chart.resize === "function") {
        chart.resize(width, height);
        return;
      }
      if (typeof chart.applyOptions === "function") {
        chart.applyOptions({ width, height });
      }
    },

    createLogsChartSeries(chart, options) {
      const chartLib = window.LightweightCharts;
      if (typeof chart?.addSeries === "function" && chartLib?.LineSeries) {
        return chart.addSeries(chartLib.LineSeries, options);
      }
      if (typeof chart?.addLineSeries === "function") {
        return chart.addLineSeries(options);
      }
      return null;
    },

    ensureLogsChart() {
      const chartLib = window.LightweightCharts;
      const container = this.$refs.logsTrendChart;
      if (!container || !chartLib?.createChart) {
        return null;
      }
      if (this.logsChart && this.logsChartContainer === container) {
        this.resizeLogsChart();
        return this.logsChart;
      }
      this.destroyLogsChart();
      this.logsChartContainer = container;
      this.logsChart = chartLib.createChart(container, {
        width: Math.max(Math.round(container.clientWidth || 0), 280),
        height: Math.max(Math.round(container.clientHeight || 0), 320),
        layout: {
          background: {
            color: "#f8fafc",
            type: chartLib.ColorType ? chartLib.ColorType.Solid : "solid",
          },
          textColor: "#475569",
          fontFamily: "\"SF Pro Display\", \"Segoe UI Variable\", \"PingFang SC\", system-ui, sans-serif",
        },
        grid: {
          vertLines: { color: "rgba(148, 163, 184, 0.08)" },
          horzLines: { color: "rgba(148, 163, 184, 0.08)" },
        },
        rightPriceScale: {
          borderColor: "rgba(148, 163, 184, 0.18)",
        },
        timeScale: {
          borderColor: "rgba(148, 163, 184, 0.18)",
          tickMarkFormatter: (time) => {
            if (typeof time !== "string") return "";
            const parts = time.split("-");
            return parts.length === 3 ? `${parts[1]}/${parts[2]}` : time;
          },
        },
        crosshair: {
          vertLine: {
            color: "rgba(59, 130, 246, 0.28)",
            labelBackgroundColor: "#1d4ed8",
          },
          horzLine: {
            color: "rgba(148, 163, 184, 0.24)",
            labelBackgroundColor: "#0f172a",
          },
        },
        localization: {
          locale: "zh-CN",
          priceFormatter: formatLogTrendCount,
          tickmarksPriceFormatter: (prices) => prices.map((price) => formatLogTrendCount(price)),
        },
      });
      if (typeof ResizeObserver === "function") {
        this.logsChartResizeObserver = new ResizeObserver(() => this.resizeLogsChart());
        this.logsChartResizeObserver.observe(container);
      } else {
        this.logsChartWindowResize = () => this.resizeLogsChart();
        window.addEventListener("resize", this.logsChartWindowResize);
      }
      return this.logsChart;
    },

    renderLogsChart() {
      if (!this.hasLogTrendData()) {
        this.destroyLogsChart();
        return;
      }
      const chart = this.ensureLogsChart();
      if (!chart) return;
      for (const item of LOG_SERIES_META) {
        const points = (this.logs?.trend?.series?.[item.key] || []).map((point) => ({
          time: point.day,
          value: Number(point.count || 0),
        }));
        let series = this.logsChartSeries[item.key];
        if (!series) {
          series = this.createLogsChartSeries(chart, {
            color: item.color,
            lineWidth: 2,
            priceFormat: {
              type: "price",
              precision: 0,
              minMove: 1,
            },
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerRadius: 4,
            crosshairMarkerBorderColor: item.color,
            crosshairMarkerBackgroundColor: "#f8fafc",
            title: item.label,
          });
          if (!series) continue;
          this.logsChartSeries[item.key] = series;
        }
        series.setData(points);
      }
      if (typeof chart.timeScale === "function") {
        chart.timeScale().fitContent();
      }
    },

    async reloadLogsFromFirstPage({ replace = false } = {}) {
      this.logs.page = 1;
      this.syncAdminListRoute({ replace });
      await this.loadLogs();
    },

    scheduleLogsSearch() {
      window.clearTimeout(this.logsFilterTimer);
      this.logsFilterTimer = window.setTimeout(() => this.reloadLogsFromFirstPage({ replace: true }), 250);
    },

    async changeLogsPage(page) {
      this.logs.page = Math.max(1, Math.min(Number(page || 1), this.logs.total_pages || 1));
      this.syncAdminListRoute({ replace: false });
      await this.loadLogs();
    },

    async loadLogs() {
      const requestId = ++this.logsRequestId;
      this.logsLoading = true;
      const query = new URLSearchParams({
        days: String(LOG_TREND_WINDOW_DAYS),
        tz_offset_minutes: String(this.browserTzOffsetMinutes()),
        page: String(this.logs.page || 1),
        limit: "20",
        q: this.filters.logs.q || "",
        category: this.filters.logs.category || "",
      });
      try {
        const data = await this.api(`/api/admin/logs?${query.toString()}`);
        if (!data || requestId !== this.logsRequestId || this.route.name !== "logs") return;
        this.logs = data;
        this.syncAdminListRoute({ replace: true });
        await this.$nextTick();
        if (this.shouldRenderLogsChart()) {
          this.renderLogsChart();
        } else {
          this.destroyLogsChart();
        }
      } catch (_error) {
        if (requestId === this.logsRequestId) this.showNotice("日志加载失败，请重试");
      } finally {
        if (requestId === this.logsRequestId) this.logsLoading = false;
      }
    },

  };
}
