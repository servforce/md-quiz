export const LOG_TREND_WINDOW_DAYS = 30;
export const ADMIN_COMPACT_BREAKPOINT_QUERY = "(max-width: 1279px)";
export const MCP_CAPABILITY_GROUPS = [
  {
    key: "system",
    label: "系统与运维",
    icon: "monitoring",
    tools: [
      "system_health",
      "system_processes",
      "runtime_config_get",
      "runtime_config_update",
      "system_status_summary",
      "system_status_range",
      "system_status_update_thresholds",
      "job_list",
      "job_get",
      "job_wait",
    ],
  },
  {
    key: "quiz",
    label: "测验与同步",
    icon: "library_books",
    tools: [
      "quiz_repo_get_binding",
      "quiz_repo_bind",
      "quiz_repo_rebind",
      "quiz_repo_sync",
      "quiz_list",
      "quiz_get",
      "quiz_set_public_invite",
    ],
  },
  {
    key: "candidate",
    label: "候选人与档案",
    icon: "group",
    tools: [
      "candidate_list",
      "candidate_ensure",
      "candidate_get",
      "candidate_add_evaluation",
      "candidate_delete",
    ],
  },
  {
    key: "assignment",
    label: "邀约与结果",
    icon: "assignment",
    tools: [
      "assignment_list",
      "assignment_create",
      "assignment_get",
      "assignment_set_handling",
      "assignment_delete",
    ],
  },
];
export const MCP_FLOW_STEPS = [
  "1. 绑定或同步测验仓库，确保题库版本已落库。",
  "2. 使用 candidate_ensure 建立候选人，避免重复建档。",
  "3. 调用 assignment_create 生成邀约，再分发链接或二维码。",
  "4. 通过 assignment_get / assignment_list 查看作答进度与结果摘要。",
  "5. 通过 system_status_* 与 runtime_config_* 查看或调整系统状态。",
];
export const MCP_SECURITY_RULES = [
  "默认脱敏返回手机号、简历敏感字段与答卷细节。",
  "需要明文时，工具需显式传 include_sensitive=true。",
  "重新绑定仓库、删除候选人、删除邀约等高危操作默认只预检，confirm=true 才执行。",
  "远程 MCP 走 Bearer Token 鉴权，不复用后台浏览器登录态。",
];
export const ADMIN_COMPACT_TAB_CONFIG = {
  quizzes: {
    defaultTab: "list",
    tabs: [
      { id: "list", label: "测验列表" },
      { id: "repo", label: "仓库绑定" },
    ],
  },
  "quiz-detail": {
    defaultTab: "analytics",
    tabs: [
      { id: "analytics", label: "测验分析" },
      { id: "content", label: "测验内容" },
      { id: "history", label: "版本历史" },
    ],
  },
  candidates: {
    defaultTab: "list",
    tabs: [
      { id: "list", label: "候选人列表" },
      { id: "create", label: "创建候选人" },
    ],
  },
  "job-descriptions": {
    defaultTab: "list",
    tabs: [
      { id: "list", label: "职位列表" },
      { id: "editor", label: "编辑预览" },
    ],
  },
  "candidate-detail": {
    defaultTab: "profile",
    tabs: [
      { id: "profile", label: "候选人档案" },
      { id: "actions", label: "管理操作" },
    ],
  },
  assignments: {
    defaultTab: "list",
    tabs: [
      { id: "list", label: "邀约列表" },
      { id: "create", label: "创建邀约" },
    ],
  },
  "attempt-detail": {
    defaultTab: "review",
    tabs: [
      { id: "review", label: "答题回放" },
      { id: "evaluation", label: "智能评价" },
    ],
  },
  logs: {
    defaultTab: "list",
    tabs: [
      { id: "list", label: "日志列表" },
      { id: "trend", label: "分类趋势" },
    ],
  },
  status: {
    defaultTab: "summary",
    tabs: [
      { id: "summary", label: "状态摘要" },
      { id: "config", label: "阈值配置" },
    ],
  },
  mcp: {
    defaultTab: "overview",
    tabs: [
      { id: "overview", label: "接入摘要" },
      { id: "capabilities", label: "能力范围" },
    ],
  },
};
export const LOG_SERIES_META = [
  { key: "candidate", label: "候选人", color: "#2563eb", hint: "创建、编辑与简历入库" },
  { key: "quiz", label: "测验", color: "#14b8a6", hint: "测验查看、更新与同步" },
  { key: "grading", label: "判卷", color: "#f59e0b", hint: "判卷完成与得分回写" },
  { key: "assignment", label: "邀约", color: "#7c3aed", hint: "邀约创建、验证与答题过程" },
  { key: "system", label: "系统", color: "#ef4444", hint: "告警与短信相关事件" },
];
export const TRAIT_COLOR_PALETTE = [
  { accent: "#2563eb", border: "rgba(37,99,235,0.22)", background: "rgba(37,99,235,0.10)", text: "#1d4ed8" },
  { accent: "#059669", border: "rgba(5,150,105,0.24)", background: "rgba(5,150,105,0.10)", text: "#047857" },
  { accent: "#7c3aed", border: "rgba(124,58,237,0.22)", background: "rgba(124,58,237,0.10)", text: "#6d28d9" },
  { accent: "#d97706", border: "rgba(217,119,6,0.24)", background: "rgba(217,119,6,0.10)", text: "#b45309" },
  { accent: "#db2777", border: "rgba(219,39,119,0.22)", background: "rgba(219,39,119,0.10)", text: "#be185d" },
  { accent: "#0891b2", border: "rgba(8,145,178,0.22)", background: "rgba(8,145,178,0.10)", text: "#0e7490" },
  { accent: "#4f46e5", border: "rgba(79,70,229,0.22)", background: "rgba(79,70,229,0.10)", text: "#4338ca" },
  { accent: "#ea580c", border: "rgba(234,88,12,0.24)", background: "rgba(234,88,12,0.10)", text: "#c2410c" },
];
export const RESUME_PHASE_META = {
  idle: { label: "待选择", border: "rgba(148,163,184,0.24)", background: "rgba(248,250,252,0.92)", text: "#475569" },
  confirm: { label: "待确认", border: "rgba(245,158,11,0.28)", background: "rgba(255,251,235,0.96)", text: "#b45309" },
  running: { label: "解析中", border: "rgba(37,99,235,0.24)", background: "rgba(239,246,255,0.96)", text: "#1d4ed8" },
  success: { label: "成功", border: "rgba(5,150,105,0.26)", background: "rgba(236,253,245,0.96)", text: "#047857" },
  error: { label: "失败", border: "rgba(225,29,72,0.24)", background: "rgba(255,241,242,0.96)", text: "#be123c" },
};
export const RESUME_PARSE_META = {
  done: { label: "解析完成", border: "rgba(5,150,105,0.22)", background: "rgba(236,253,245,0.92)", text: "#047857" },
  empty: { label: "结果为空", border: "rgba(245,158,11,0.24)", background: "rgba(255,251,235,0.96)", text: "#b45309" },
  failed: { label: "解析失败", border: "rgba(225,29,72,0.22)", background: "rgba(255,241,242,0.96)", text: "#be123c" },
};
export const createCandidateResumeUploadState = () => ({
  phase: "idle",
  busy: false,
  jobId: "",
  fileName: "",
  message: "选择 PDF 或图片简历后，系统会自动解析手机号并创建或更新候选人。",
  error: "",
  created: null,
  candidateName: "",
  candidateId: 0,
});
export const createCandidateResumeReparseState = (
  message = "选择新简历后会先要求确认，再覆盖当前简历并重新解析。",
) => ({
  phase: "idle",
  busy: false,
  jobId: "",
  fileName: "",
  message,
  error: "",
  pendingFile: null,
});
export const formatLogTrendCount = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "0";
  }
  return String(Math.round(numeric));
};
