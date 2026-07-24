const QUIZ_ANALYTICS_WINDOW_OPTIONS = [
  { key: "week", label: "周" },
  { key: "month", label: "月" },
  { key: "half_year", label: "半年" },
  { key: "year", label: "年" },
];

const QUIZ_ANALYTICS_LIST_SORT_OPTIONS = [
  { key: "time", label: "时间" },
  { key: "score", label: "得分" },
];

export function createAdminQuizAnalyticsModule() {
  return {
    quizAnalyticsWindowOptions() {
      return QUIZ_ANALYTICS_WINDOW_OPTIONS;
    },

    currentQuizAnalyticsWindow() {
      return String(this.route?.query?.window || "month").trim() || "month";
    },

    currentQuizAnalyticsStartDate() {
      return String(this.route?.query?.start_date || "").trim();
    },

    currentQuizAnalyticsEndDate() {
      return String(this.route?.query?.end_date || "").trim();
    },

    currentQuizAnalyticsVersionScope() {
      return "current";
    },

    currentQuizAnalyticsVersionId() {
      return String(this.route?.query?.version_id || "").trim();
    },

    currentQuizAnalyticsDistributionMode() {
      return "range";
    },

    currentQuizAnalyticsListSortKey() {
      const current = String(this.route?.query?.list_sort || "time").trim().toLowerCase();
      return current === "score" && this.quizAnalyticsHasScoredResults() ? "score" : "time";
    },

    currentQuizAnalyticsListSortOrder() {
      const current = String(this.route?.query?.list_order || "desc").trim().toLowerCase();
      return current === "asc" ? "asc" : "desc";
    },

    currentQuizAnalyticsScoreFilter() {
      const summary = this.quizAnalyticsDetail?.summary;
      if (summary && Object.keys(summary).length && !this.quizAnalyticsHasScoredResults()) return null;
      const scoreMax = Number(this.route?.query?.score_filter_score_max || 0);
      const start = Number(this.route?.query?.score_filter_start || 0);
      const end = Number(this.route?.query?.score_filter_end || 0);
      if (!Number.isFinite(scoreMax) || scoreMax <= 0) return null;
      if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
      if (start < 0 || end < start) return null;
      return {
        scoreMax,
        start,
        end,
      };
    },

    currentQuizAnalyticsTraitFilterCombination() {
      return String(this.route?.query?.trait_filter_combination || "").trim();
    },

    currentQuizAnalyticsKey() {
      return String(this.route?.query?.quiz_key || "").trim();
    },

    quizAnalyticsListSortOptions() {
      if (!this.quizAnalyticsHasScoredResults()) {
        return QUIZ_ANALYTICS_LIST_SORT_OPTIONS.filter((option) => option.key !== "score");
      }
      return QUIZ_ANALYTICS_LIST_SORT_OPTIONS;
    },

    quizAnalyticsAvailableVersions() {
      return Array.isArray(this.quizAnalyticsDetail?.quiz?.available_versions)
        ? this.quizAnalyticsDetail.quiz.available_versions
        : [];
    },

    shouldShowQuizAnalyticsVersionSelect() {
      return this.quizAnalyticsAvailableVersions().length > 0;
    },

    quizAnalyticsSelectedTitle() {
      return String(this.quizAnalyticsDetail?.quiz?.title || "").trim();
    },

    syncQuizAnalyticsDateInputs(startDate = this.currentQuizAnalyticsStartDate(), endDate = this.currentQuizAnalyticsEndDate()) {
      if (!this.filters?.quizAnalytics) return;
      this.filters.quizAnalytics.start_date = String(startDate || "").trim();
      this.filters.quizAnalytics.end_date = String(endDate || "").trim();
    },

    quizAnalyticsCanApplyCustomDateRange() {
      const startDate = String(this.filters?.quizAnalytics?.start_date || "").trim();
      const endDate = String(this.filters?.quizAnalytics?.end_date || "").trim();
      return Boolean(startDate && endDate && startDate <= endDate);
    },

    quizAnalyticsSummaryCards() {
      const summary = this.quizAnalyticsDetail?.summary || {};
      return [
        { key: "total", label: "答题数量", value: Number(summary.total_attempt_count || 0), tone: "slate" },
        { key: "finished", label: "已完成", value: Number(summary.finished_count || 0), tone: "emerald" },
        { key: "progress", label: "进行中", value: Number(summary.in_progress_count || 0), tone: "amber" },
        { key: "scored", label: "可计分完成", value: Number(summary.scored_finished_count || 0), tone: "blue" },
        { key: "traits", label: "量表完成", value: Number(summary.traits_only_finished_count || 0), tone: "violet" },
      ];
    },

    quizAnalyticsVersionLabel(item) {
      const versionNo = Number(item?.version_no || 0);
      return versionNo > 0 ? `V${versionNo}` : "未知版本";
    },

    quizAnalyticsStatusBadgeClass(item) {
      const status = String(item?.status || "").trim();
      if (status === "finished") {
        return "rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-[11px] font-semibold text-emerald-700";
      }
      if (status === "grading") {
        return "rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-[11px] font-semibold text-amber-700";
      }
      return "rounded-full border border-sky-200 bg-sky-50 px-2.5 py-1 text-[11px] font-semibold text-sky-700";
    },

    quizAnalyticsSourceBadgeClass(item) {
      if (String(item?.source_kind || "").trim() === "public") {
        return "rounded-full border border-violet-200 bg-violet-50 px-2.5 py-1 text-[11px] font-semibold text-violet-700";
      }
      return "rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-semibold text-slate-600";
    },

    quizAnalyticsListSortButtonClass(sortKey) {
      const active = this.currentQuizAnalyticsListSortKey() === String(sortKey || "").trim();
      return active
        ? "rounded-full border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs font-semibold text-blue-700 transition"
        : "rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-600 transition hover:border-blue-200 hover:text-blue-700";
    },

    quizAnalyticsListSortDirectionLabel(sortKey) {
      if (this.currentQuizAnalyticsListSortKey() !== String(sortKey || "").trim()) {
        return "";
      }
      return this.currentQuizAnalyticsListSortOrder() === "asc" ? "↑" : "↓";
    },

    quizAnalyticsHasActiveScoreFilter() {
      return Boolean(this.currentQuizAnalyticsScoreFilter());
    },

    quizAnalyticsScoreFilterLabel() {
      const filter = this.currentQuizAnalyticsScoreFilter();
      if (!filter) return "";
      const rangeLabel = filter.start === filter.end
        ? `${filter.start} 分`
        : `${filter.start}-${filter.end} 分`;
      return `已筛选：满分 ${filter.scoreMax} · ${rangeLabel}`;
    },

    quizAnalyticsVisibleItems() {
      let items = this.quizAnalyticsSortedItems();
      const scoreFilter = this.currentQuizAnalyticsScoreFilter();
      const traitFilter = this.currentQuizAnalyticsTraitFilterCombination();
      if (scoreFilter) {
        items = items.filter((item) => this.quizAnalyticsItemMatchesScoreFilter(item, scoreFilter));
      }
      if (traitFilter) {
        items = items.filter((item) => this.quizAnalyticsItemMatchesTraitFilter(item, traitFilter));
      }
      return items;
    },

    quizAnalyticsVisibleItemsCount() {
      return this.quizAnalyticsVisibleItems().length;
    },

    quizAnalyticsSortedItems() {
      const items = Array.isArray(this.quizAnalyticsDetail?.items) ? this.quizAnalyticsDetail.items.slice() : [];
      const sortKey = this.currentQuizAnalyticsListSortKey();
      const sortOrder = this.currentQuizAnalyticsListSortOrder();
      const factor = sortOrder === "asc" ? 1 : -1;
      items.sort((left, right) => {
        const primary = this.compareQuizAnalyticsListItems(left, right, sortKey, factor);
        if (primary !== 0) return primary;
        if (sortKey !== "time") {
          const byTime = this.compareQuizAnalyticsListItems(left, right, "time", -1);
          if (byTime !== 0) return byTime;
        }
        if (sortKey !== "score") {
          const byScore = this.compareQuizAnalyticsListItems(left, right, "score", -1);
          if (byScore !== 0) return byScore;
        }
        return Number(right?.attempt_id || 0) - Number(left?.attempt_id || 0);
      });
      return items;
    },

    compareQuizAnalyticsListItems(left, right, sortKey, factor) {
      const leftValue = this.quizAnalyticsListSortValue(left, sortKey);
      const rightValue = this.quizAnalyticsListSortValue(right, sortKey);
      const leftMissing = leftValue === null;
      const rightMissing = rightValue === null;
      if (leftMissing && rightMissing) return 0;
      if (leftMissing) return 1;
      if (rightMissing) return -1;
      if (leftValue === rightValue) return 0;
      return leftValue > rightValue ? factor : -factor;
    },

    quizAnalyticsListSortValue(item, sortKey) {
      if (String(sortKey || "").trim() === "score") {
        const raw = Number(item?.score);
        return Number.isFinite(raw) ? raw : null;
      }
      const timestamp = Date.parse(String(item?.attempt_at || "").trim());
      return Number.isFinite(timestamp) ? timestamp : null;
    },

    quizAnalyticsItemMatchesScoreFilter(item, filter = this.currentQuizAnalyticsScoreFilter()) {
      if (!filter) return true;
      const score = Number(item?.score);
      const scoreMax = Number(item?.score_max);
      if (!Number.isFinite(score) || !Number.isFinite(scoreMax)) return false;
      return scoreMax === filter.scoreMax && score >= filter.start && score <= filter.end;
    },

    quizAnalyticsItemMatchesTraitFilter(item, traitFilter = this.currentQuizAnalyticsTraitFilterCombination()) {
      const current = String(traitFilter || "").trim();
      if (!current) return true;
      return String(item?.trait_combination || "").trim() === current;
    },

    quizAnalyticsHasScoredResults() {
      return Number(this.quizAnalyticsDetail?.summary?.scored_finished_count || 0) > 0;
    },

    quizAnalyticsResultDisplay(item) {
      if (String(item?.result_mode || "").trim() === "traits") {
        return String(item?.trait_summary || "量表").trim();
      }
      return String(item?.score_display || item?.trait_summary || "-").trim();
    },

    quizAnalyticsResultBadgeClass(item) {
      if (String(item?.result_mode || "").trim() === "traits") {
        return "rounded-full border border-violet-100 bg-violet-50 px-2.5 py-1 text-[11px] font-semibold text-violet-700";
      }
      return "rounded-full border border-blue-100 bg-blue-50 px-2.5 py-1 text-[11px] font-semibold text-blue-700";
    },

    quizAnalyticsHasDistribution() {
      return (this.quizAnalyticsDetail?.distribution_groups || []).length > 0;
    },

    quizAnalyticsDistributionGroups() {
      return Array.isArray(this.quizAnalyticsDetail?.distribution_groups)
        ? this.quizAnalyticsDetail.distribution_groups
        : [];
    },

    quizAnalyticsDistributionRows(group) {
      return this.quizAnalyticsRangeRows(group);
    },

    quizAnalyticsRangeRows(group) {
      const scoreMax = Math.max(0, Number(group?.score_max || 0));
      const buckets = Array.isArray(group?.buckets) ? group.buckets : [];
      const countByScore = new Map(
        buckets.map((bucket) => [Number(bucket?.score || 0), Number(bucket?.count || 0)]),
      );
      const rows = [];
      for (let start = 0; start <= scoreMax; start += 10) {
        const end = Math.min(scoreMax, start + 9);
        let count = 0;
        for (let score = start; score <= end; score += 1) {
          count += Number(countByScore.get(score) || 0);
        }
        rows.push({
          kind: "bucket",
          key: `range-${start}-${end}`,
          label: start === end ? `${start} 分` : `${start}-${end} 分`,
          count,
          start,
          end,
        });
      }
      return rows;
    },

    quizAnalyticsDistributionMaxCount(group) {
      const rows = this.quizAnalyticsDistributionRows(group);
      return rows.reduce(
        (max, row) => row?.kind === "bucket" ? Math.max(max, Number(row?.count || 0)) : max,
        0,
      );
    },

    quizAnalyticsDistributionColumnStyle(group, bucket) {
      const maxCount = this.quizAnalyticsDistributionMaxCount(group);
      const count = Number(bucket?.count || 0);
      const percent = maxCount > 0 && count > 0 ? Math.max(8, Math.round((count / maxCount) * 100)) : 0;
      return { height: `${percent}%` };
    },

    quizAnalyticsDistributionColumnButtonClass(group, row) {
      const count = Number(row?.count || 0);
      const active = this.quizAnalyticsDistributionRowIsActive(group, row);
      const widthClass = "w-14";
      if (count <= 0) {
        return `flex ${widthClass} shrink-0 flex-col items-center gap-2 rounded-xl px-1 py-1 text-center opacity-55`;
      }
      if (active) {
        return `flex ${widthClass} shrink-0 flex-col items-center gap-2 rounded-xl border border-blue-200 bg-blue-50/80 px-1 py-1 text-center transition`;
      }
      return `flex ${widthClass} shrink-0 flex-col items-center gap-2 rounded-xl px-1 py-1 text-center transition hover:bg-slate-100/90 cursor-pointer`;
    },

    quizAnalyticsDistributionColumnsClass() {
      return "flex min-w-max items-end gap-2";
    },

    quizAnalyticsDistributionColumnTrackClass(group, row) {
      return this.quizAnalyticsDistributionRowIsActive(group, row)
        ? "flex h-40 w-full items-end overflow-hidden rounded-xl border border-blue-200 bg-white px-1.5 py-1"
        : "flex h-40 w-full items-end overflow-hidden rounded-xl bg-slate-100/85 px-1.5 py-1";
    },

    quizAnalyticsDistributionGapClass() {
      return "flex h-[13.5rem] w-8 shrink-0 flex-col items-center justify-end gap-3 pb-1";
    },

    quizAnalyticsDistributionRowIsActive(group, row) {
      const filter = this.currentQuizAnalyticsScoreFilter();
      if (!filter || row?.kind !== "bucket") return false;
      return Number(group?.score_max || 0) === filter.scoreMax
        && Number(row?.start || 0) === filter.start
        && Number(row?.end || 0) === filter.end;
    },

    quizAnalyticsTraitDistribution() {
      return this.quizAnalyticsDetail?.trait_distribution || {};
    },

    quizAnalyticsHasTraitDistribution() {
      return Number(this.quizAnalyticsTraitDistribution()?.total_count || 0) > 0
        && this.quizAnalyticsTraitCombinationRows().length > 0;
    },

    quizAnalyticsTraitCombinationRows() {
      const rows = this.quizAnalyticsTraitDistribution()?.combination_counts;
      return Array.isArray(rows) ? rows : [];
    },

    quizAnalyticsTraitCombinationRowClass(row) {
      const active = this.quizAnalyticsTraitCombinationIsActive(row);
      const base = "grid w-full gap-2 rounded-xl border px-3 py-3 text-left transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-200 sm:grid-cols-[7rem_minmax(0,1fr)_5.5rem] sm:items-center";
      if (active) {
        return `${base} border-violet-200 bg-white shadow-sm`;
      }
      return `${base} border-transparent hover:border-violet-100 hover:bg-white/70`;
    },

    quizAnalyticsTraitCombinationIsActive(row) {
      return String(row?.combination || "").trim() === this.currentQuizAnalyticsTraitFilterCombination();
    },

    quizAnalyticsTraitPairRows() {
      const rows = this.quizAnalyticsTraitDistribution()?.pair_counts;
      return Array.isArray(rows) ? rows : [];
    },

    quizAnalyticsTraitMaxCombinationCount() {
      return this.quizAnalyticsTraitCombinationRows().reduce(
        (max, row) => Math.max(max, Number(row?.count || 0)),
        0,
      );
    },

    quizAnalyticsTraitCombinationBarStyle(row) {
      const maxCount = this.quizAnalyticsTraitMaxCombinationCount();
      const count = Number(row?.count || 0);
      const percent = maxCount > 0 && count > 0 ? Math.max(4, Math.round((count / maxCount) * 100)) : 0;
      return { width: `${percent}%` };
    },

    quizAnalyticsTraitPairSideStyle(pair, side) {
      const total = Number(pair?.total_count || 0);
      const key = side === "right" ? "right_count" : "left_count";
      const count = Number(pair?.[key] || 0);
      const percent = total > 0 && count > 0 ? Math.round((count / total) * 100) : 0;
      return { width: `${percent}%` };
    },

    quizAnalyticsPercentLabel(value) {
      const percent = Number(value || 0);
      if (!Number.isFinite(percent)) return "0%";
      return Number.isInteger(percent) ? `${percent}%` : `${percent.toFixed(1)}%`;
    },

    quizAnalyticsHasActiveTraitFilter() {
      return Boolean(this.currentQuizAnalyticsTraitFilterCombination());
    },

    quizAnalyticsTraitFilterLabel() {
      const current = this.currentQuizAnalyticsTraitFilterCombination();
      return current ? `已筛选：${current}` : "";
    },

    quizAnalyticsListEmptyTitle() {
      if (this.quizAnalyticsHasActiveTraitFilter()) return "当前主倾向组合筛选下没有匹配记录";
      if (this.quizAnalyticsHasActiveScoreFilter()) return "当前分数筛选下没有匹配记录";
      return "当前窗口内没有可展示的答题记录";
    },

    quizAnalyticsListEmptyDescription() {
      if (this.quizAnalyticsHasActiveTraitFilter()) return "可以再次点击已选组合取消筛选，或点击“清除组合”。";
      if (this.quizAnalyticsHasActiveScoreFilter()) return "可以再次点击得分图中的分数段取消筛选，或点击“清除筛选”。";
      return "只统计已进入答题的进行中记录，以及已完成的归档记录。";
    },

    resetQuizAnalyticsDetail() {
      this.quizAnalyticsDetail = { quiz: {}, filters: {}, summary: {}, distribution_groups: [], trait_distribution: {}, items: [] };
    },

    syncQuizAnalyticsRoute({
      quizKey,
      window,
      startDate,
      endDate,
      versionScope,
      versionId,
      distributionMode,
      listSort,
      listOrder,
      scoreFilter,
      traitFilterCombination,
    } = {}) {
      const normalizedVersionId = versionId ?? this.currentQuizAnalyticsVersionId() ?? "";
      const normalizedStartDate = startDate ?? this.currentQuizAnalyticsStartDate();
      const normalizedEndDate = endDate ?? this.currentQuizAnalyticsEndDate();
      const activeScoreFilter = scoreFilter === null ? null : (scoreFilter || this.currentQuizAnalyticsScoreFilter());
      const normalizedTraitFilterCombination = traitFilterCombination ?? this.currentQuizAnalyticsTraitFilterCombination();
      const detailQuizKey = this.route?.name === "quiz-detail"
        ? String(this.route?.params?.quizKey || "").trim()
        : "";
      this.setRouteSearchParams({
        quiz_key: detailQuizKey === String(quizKey || "").trim() ? "" : String(quizKey || "").trim(),
        window: String(window || this.currentQuizAnalyticsWindow()).trim() || "month",
        start_date: String(normalizedStartDate || "").trim(),
        end_date: String(normalizedEndDate || "").trim(),
        version_scope: "current",
        version_id: String(normalizedVersionId || "").trim(),
        distribution_mode: String(distributionMode || this.currentQuizAnalyticsDistributionMode()).trim() || "range",
        list_sort: String(listSort || this.currentQuizAnalyticsListSortKey()).trim() || "time",
        list_order: String(listOrder || this.currentQuizAnalyticsListSortOrder()).trim() || "desc",
        score_filter_score_max: activeScoreFilter ? String(activeScoreFilter.scoreMax) : "",
        score_filter_start: activeScoreFilter ? String(activeScoreFilter.start) : "",
        score_filter_end: activeScoreFilter ? String(activeScoreFilter.end) : "",
        trait_filter_combination: String(normalizedTraitFilterCombination || "").trim(),
      });
    },

    async loadQuizAnalyticsDetail(quizKey, { quiet = false, syncRoute = true } = {}) {
      const currentKey = String(quizKey || "").trim();
      if (!currentKey) {
        this.resetQuizAnalyticsDetail();
        return null;
      }
      const window = this.currentQuizAnalyticsWindow();
      const startDate = this.currentQuizAnalyticsStartDate();
      const endDate = this.currentQuizAnalyticsEndDate();
      const versionScope = this.currentQuizAnalyticsVersionScope();
      const versionId = this.currentQuizAnalyticsVersionId();
      const query = new URLSearchParams({
        window,
        version_scope: versionScope,
      });
      if (startDate && endDate) {
        query.set("start_date", startDate);
        query.set("end_date", endDate);
      }
      if (versionScope === "current" && versionId) {
        query.set("version_id", versionId);
      }
      const data = await this.api(`/api/admin/quiz-analytics/${encodeURIComponent(currentKey)}?${query.toString()}`, { quiet });
      if (!data) return null;
      this.quizAnalyticsDetail = data;
      if (syncRoute) {
        this.syncQuizAnalyticsRoute({
          quizKey: currentKey,
          window,
          startDate: String(data?.filters?.start_date || ""),
          endDate: String(data?.filters?.end_date || ""),
          versionScope,
          versionId: String(data?.filters?.version_id || ""),
          distributionMode: this.currentQuizAnalyticsDistributionMode(),
          listSort: this.currentQuizAnalyticsListSortKey(),
          listOrder: this.currentQuizAnalyticsListSortOrder(),
          scoreFilter: this.currentQuizAnalyticsScoreFilter(),
          traitFilterCombination: this.currentQuizAnalyticsTraitFilterCombination(),
        });
      }
      this.syncQuizAnalyticsDateInputs(
        String(data?.filters?.start_date || ""),
        String(data?.filters?.end_date || ""),
      );
      return data;
    },

    async changeQuizAnalyticsWindow(window) {
      const next = String(window || "").trim();
      if (!next || next === this.currentQuizAnalyticsWindow()) return;
      const quizKey = this.currentQuizAnalyticsKey() || this.quizAnalyticsDetail?.quiz?.quiz_key || "";
      this.syncQuizAnalyticsRoute({
        quizKey,
        window: next,
        startDate: "",
        endDate: "",
        versionScope: this.currentQuizAnalyticsVersionScope(),
        versionId: this.currentQuizAnalyticsVersionId(),
        distributionMode: this.currentQuizAnalyticsDistributionMode(),
        listSort: this.currentQuizAnalyticsListSortKey(),
        listOrder: this.currentQuizAnalyticsListSortOrder(),
        scoreFilter: this.currentQuizAnalyticsScoreFilter(),
      });
      this.syncQuizAnalyticsDateInputs("", "");
      await this.loadQuizAnalyticsDetail(quizKey);
    },

    async changeQuizAnalyticsVersionId(versionId) {
      const quizKey = this.currentQuizAnalyticsKey() || this.quizAnalyticsDetail?.quiz?.quiz_key || "";
      const next = String(versionId || "").trim();
      if (!quizKey || !next || next === this.currentQuizAnalyticsVersionId()) return;
      this.syncQuizAnalyticsRoute({
        quizKey,
        window: this.currentQuizAnalyticsWindow(),
        startDate: this.currentQuizAnalyticsStartDate(),
        endDate: this.currentQuizAnalyticsEndDate(),
        versionScope: "current",
        versionId: next,
        distributionMode: this.currentQuizAnalyticsDistributionMode(),
        listSort: this.currentQuizAnalyticsListSortKey(),
        listOrder: this.currentQuizAnalyticsListSortOrder(),
        scoreFilter: this.currentQuizAnalyticsScoreFilter(),
      });
      await this.loadQuizAnalyticsDetail(quizKey);
    },

    changeQuizAnalyticsListSort(sortKey) {
      const nextKey = String(sortKey || "").trim().toLowerCase();
      if (!["time", "score"].includes(nextKey)) return;
      const currentKey = this.currentQuizAnalyticsListSortKey();
      const currentOrder = this.currentQuizAnalyticsListSortOrder();
      const nextOrder = currentKey === nextKey
        ? (currentOrder === "desc" ? "asc" : "desc")
        : "desc";
      this.syncQuizAnalyticsRoute({
        quizKey: this.currentQuizAnalyticsKey() || this.quizAnalyticsDetail?.quiz?.quiz_key || "",
        window: this.currentQuizAnalyticsWindow(),
        startDate: this.currentQuizAnalyticsStartDate(),
        endDate: this.currentQuizAnalyticsEndDate(),
        versionScope: this.currentQuizAnalyticsVersionScope(),
        versionId: this.currentQuizAnalyticsVersionId(),
        distributionMode: this.currentQuizAnalyticsDistributionMode(),
        listSort: nextKey,
        listOrder: nextOrder,
        scoreFilter: this.currentQuizAnalyticsScoreFilter(),
      });
    },

    toggleQuizAnalyticsScoreFilter(group, row) {
      if (row?.kind !== "bucket") return;
      const count = Number(row?.count || 0);
      if (count <= 0) return;
      const nextFilter = {
        scoreMax: Number(group?.score_max || 0),
        start: Number(row?.start || 0),
        end: Number(row?.end || 0),
      };
      if (!nextFilter.scoreMax || nextFilter.end < nextFilter.start) return;
      const active = this.quizAnalyticsDistributionRowIsActive(group, row);
      this.syncQuizAnalyticsRoute({
        quizKey: this.currentQuizAnalyticsKey() || this.quizAnalyticsDetail?.quiz?.quiz_key || "",
        window: this.currentQuizAnalyticsWindow(),
        startDate: this.currentQuizAnalyticsStartDate(),
        endDate: this.currentQuizAnalyticsEndDate(),
        versionScope: this.currentQuizAnalyticsVersionScope(),
        versionId: this.currentQuizAnalyticsVersionId(),
        distributionMode: this.currentQuizAnalyticsDistributionMode(),
        listSort: this.currentQuizAnalyticsListSortKey(),
        listOrder: this.currentQuizAnalyticsListSortOrder(),
        scoreFilter: active ? null : nextFilter,
        traitFilterCombination: "",
      });
    },

    clearQuizAnalyticsScoreFilter() {
      if (!this.currentQuizAnalyticsScoreFilter()) return;
      this.syncQuizAnalyticsRoute({
        quizKey: this.currentQuizAnalyticsKey() || this.quizAnalyticsDetail?.quiz?.quiz_key || "",
        window: this.currentQuizAnalyticsWindow(),
        startDate: this.currentQuizAnalyticsStartDate(),
        endDate: this.currentQuizAnalyticsEndDate(),
        versionScope: this.currentQuizAnalyticsVersionScope(),
        versionId: this.currentQuizAnalyticsVersionId(),
        distributionMode: this.currentQuizAnalyticsDistributionMode(),
        listSort: this.currentQuizAnalyticsListSortKey(),
        listOrder: this.currentQuizAnalyticsListSortOrder(),
        scoreFilter: null,
      });
    },

    toggleQuizAnalyticsTraitCombinationFilter(row) {
      const combination = String(row?.combination || "").trim();
      if (!combination) return;
      const active = this.quizAnalyticsTraitCombinationIsActive(row);
      this.syncQuizAnalyticsRoute({
        quizKey: this.currentQuizAnalyticsKey() || this.quizAnalyticsDetail?.quiz?.quiz_key || "",
        window: this.currentQuizAnalyticsWindow(),
        startDate: this.currentQuizAnalyticsStartDate(),
        endDate: this.currentQuizAnalyticsEndDate(),
        versionScope: this.currentQuizAnalyticsVersionScope(),
        versionId: this.currentQuizAnalyticsVersionId(),
        distributionMode: this.currentQuizAnalyticsDistributionMode(),
        listSort: this.currentQuizAnalyticsListSortKey(),
        listOrder: this.currentQuizAnalyticsListSortOrder(),
        scoreFilter: null,
        traitFilterCombination: active ? "" : combination,
      });
    },

    clearQuizAnalyticsTraitFilter() {
      if (!this.currentQuizAnalyticsTraitFilterCombination()) return;
      this.syncQuizAnalyticsRoute({
        quizKey: this.currentQuizAnalyticsKey() || this.quizAnalyticsDetail?.quiz?.quiz_key || "",
        window: this.currentQuizAnalyticsWindow(),
        startDate: this.currentQuizAnalyticsStartDate(),
        endDate: this.currentQuizAnalyticsEndDate(),
        versionScope: this.currentQuizAnalyticsVersionScope(),
        versionId: this.currentQuizAnalyticsVersionId(),
        distributionMode: this.currentQuizAnalyticsDistributionMode(),
        listSort: this.currentQuizAnalyticsListSortKey(),
        listOrder: this.currentQuizAnalyticsListSortOrder(),
        scoreFilter: this.currentQuizAnalyticsScoreFilter(),
        traitFilterCombination: "",
      });
    },

    async applyQuizAnalyticsCustomDateRange() {
      const quizKey = this.currentQuizAnalyticsKey() || this.quizAnalyticsDetail?.quiz?.quiz_key || "";
      const startDate = String(this.filters?.quizAnalytics?.start_date || "").trim();
      const endDate = String(this.filters?.quizAnalytics?.end_date || "").trim();
      if (!startDate || !endDate) {
        this.showNotice("请选择开始日期和结束日期");
        return;
      }
      if (startDate > endDate) {
        this.showNotice("开始日期不能晚于结束日期");
        return;
      }
      this.syncQuizAnalyticsRoute({
        quizKey,
        window: "custom",
        startDate,
        endDate,
        versionScope: this.currentQuizAnalyticsVersionScope(),
        versionId: this.currentQuizAnalyticsVersionId(),
        distributionMode: this.currentQuizAnalyticsDistributionMode(),
        listSort: this.currentQuizAnalyticsListSortKey(),
        listOrder: this.currentQuizAnalyticsListSortOrder(),
        scoreFilter: this.currentQuizAnalyticsScoreFilter(),
      });
      await this.loadQuizAnalyticsDetail(quizKey);
    },

    async applyQuizAnalyticsCustomDateRangeIfReady() {
      const startDate = String(this.filters?.quizAnalytics?.start_date || "").trim();
      const endDate = String(this.filters?.quizAnalytics?.end_date || "").trim();
      if (!startDate || !endDate) return;
      await this.applyQuizAnalyticsCustomDateRange();
    },

  };
}
