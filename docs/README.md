# 文档导航

## 产品与范围

- [项目愿景](vision.md)

## 协作约定

- [通用协作与编码细则](agent-rules/general.md)

## 架构

- [架构总览](architecture/overview.md)
- [运行拓扑](architecture/runtime-topology.md)
- [后端模块分工](architecture/backend-modules.md)
- [数据模型与存储](architecture/data-model.md)
- [核心请求流程](architecture/request-flows.md)
- [前端 SPA 结构](architecture/frontend-spa.md)

## 配置与参考

- [配置项说明](reference/configuration.md)
- [REST API 约定](reference/api.md)
- [MCP 能力说明](reference/mcp.md)
- [QML 测验格式](../skills/qml-authoring/references/qml-spec.md)

## 项目内 Skills

- [MD-Quiz Skill](../skills/md-quiz/SKILL.md)
- [Quiz Repo Spec Skill](../skills/quiz-repo-spec/SKILL.md)
- [QML Authoring Skill](../skills/qml-authoring/SKILL.md)
- [Quiz Repo Sync 排障](../skills/quiz-repo-spec/references/sync-troubleshooting.md)
- [Quiz 仓库规范正文](../skills/quiz-repo-spec/references/repo-contract.md)
- [QML Parser 契约事实](../skills/qml-authoring/references/parser-truth.md)
- [QML 详细规范](../skills/qml-authoring/references/qml-spec.md)

## UI

- [UI 主题覆盖](ui/theme.md)
- [Utility 落地规则](ui/utility-authoring.md)
- [两项目 UI 差异分析与改造建议（2026-09-08）](ui/scisaga-comparison-review-20260908.md)
- [管理端改造实施与验收（2026-09-09）](ui/admin-redesign-20260909.md)

## 建议阅读顺序

1. 根目录 [README.md](../README.md)
2. [项目愿景](vision.md)
3. [架构总览](architecture/overview.md)
4. [运行拓扑](architecture/runtime-topology.md)
5. [后端模块分工](architecture/backend-modules.md)
6. [数据模型与存储](architecture/data-model.md)
7. [REST API 约定](reference/api.md)
8. [UI 主题覆盖](ui/theme.md)
9. [Utility 落地规则](ui/utility-authoring.md)

## 说明

- `docs/architecture/` 只记录当前代码能够证明的稳定边界与实现事实。
- 当前正式前端入口为 `static/admin/` 与 `static/public/` 两套 Alpine SPA。
