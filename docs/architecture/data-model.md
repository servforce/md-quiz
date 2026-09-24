# 数据模型与存储

## 业务概念

当前代码中的核心业务概念包括：

- `candidate`
- `candidate_resume`
- `job_description`
- `candidate_job_description`
- `quiz_definition`
- `quiz_version`
- `assignment`
- `quiz_paper`
- `quiz_archive`
- `system_metric`
- `operation_log`
- `runtime_config`
- `job`
- `process heartbeat`

## 当前运行时配置与状态存储

### `runtime_config`

用于保存运行时行为开关，例如：

- token / 短信阈值
- 是否允许公开邀约
- 最短交卷时长
- UI 主题名

### `job`

当前任务记录保存在 `runtime_job` 表。核心字段包括：

- `id`
- `kind`
- `status`
- `payload`
- `source`
- `dedupe_key`
- `attempts`
- `error`
- `result`
- `worker_name`
- `created_at / updated_at / started_at / lease_expires_at / finished_at`

当前任务模型约束：

- `status` 目前只有 `pending / running / done / failed`
- `grade_attempt` 使用 `dedupe_key=grade_attempt:<token>`
- 同一 `dedupe_key` 在 `pending / running` 状态下只能有一条活跃记录
- `running` 且 lease 过期的任务可以被 Worker 回收重跑

### `process heartbeat`

用于展示：

- API
- Worker
- Scheduler

的当前状态与最近更新时间。

## 当前数据库表

运行时状态统一保存在 PostgreSQL 中：

- `runtime_kv`
  - 保存 `runtime_config`
  - 保存内容仓库绑定、同步状态、运行时迁移标记等键值数据
- `runtime_daily_metric`
  - 保存系统状态页按日聚合指标与告警快照
- `runtime_job`
  - 保存后台任务队列、执行状态与结果
- `process_heartbeat`
  - 保存 API / Worker / Scheduler 心跳
- `job_description`
  - 保存后台维护的职位标题、Markdown 正文、状态与创建/更新时间
  - `related_quizzes` 保存该职位关联的测验 `quiz_key` 列表；手动职位由后台选择，Git 仓库来源职位由 `jd.md` Front Matter 同步
  - Git 仓库来源职位额外保存 `jd_key`、`source_kind`、`source_path`、`git_repo_url`、`last_synced_commit`、`last_sync_at`、`last_sync_error` 与 `content_hash`
  - 候选人关联仍指向数据库自增 `id`；同步时按稳定 `jd_key` upsert，避免仓库内容更新导致历史关联断裂
- `candidate_job_description`
  - 保存候选人与职位的多对多关联；后台创建候选人和后台简历入库必须先选择一个启用职位
  - 创建邀约时若请求未显式提供 `quiz_key`/`quiz_keys`，系统会从候选人关联的启用职位中取 `related_quizzes` 作为默认测验列表

业务主数据同样已经落在 PostgreSQL，对应表结构以 `backend/md_quiz/storage/db.py:init_db()` 为准。

`assignment_record.data.question_activity` 按题号保存开始与结束时间、耗时和结束原因。简答题还保存文本变动事件、字数汇总和人工复核线索；不保存中途文本或物理按键。判卷归档将对应记录写入 `quiz_archive.archive.questions[*].activity`，供管理端答卷详情回看。历史答卷可以没有这些字段。

## 迁移说明

历史 `storage/runtime/*.json` 只在需要兼容旧部署数据时作为一次性迁移输入源：

- 首次启动时若检测到旧 JSON，会自动导入数据库
- 导入完成后不再继续写入这些文件
- 后续运行时状态以数据库为唯一事实来源
- 若仓库里已经没有旧运行时 JSON，根目录 `storage/` 目录本身也不再是运行时依赖
