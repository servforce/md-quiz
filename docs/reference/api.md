# REST API 约定

## System

### `GET /api/system/health`

返回 API 健康状态。

### `GET /api/system/processes`

返回 API / Worker / Scheduler 心跳列表。

### `GET /api/system/bootstrap`

返回品牌信息、后台入口、运行时配置，以及 MCP 接入摘要。

其中 `mcp` 字段包括：

- `enabled`
- `path`
- `transport`
- `auth_scheme`
- `docs_path`

说明：

- 这是公开 bootstrap，只返回非敏感 MCP 摘要
- `MCP_AUTH_TOKEN` 不会通过此接口返回

- 管理端和候选人端仍以 REST 为主协议面
- 智能体自动化链路可通过 `/mcp` 调用同一批后台业务能力

## Admin

### `POST /api/admin/session/login`

请求：

```json
{ "username": "admin", "password": "password" }
```

### `POST /api/admin/session/logout`

清空后台会话。

### `GET /api/admin/session`

返回当前后台登录状态。

### `GET /api/admin/bootstrap`

返回后台入口导航与概览卡片。

### `GET /api/admin/dashboard`

返回 `generated_at`、`window`、`unhandled`、`completed`、`failed_jobs`。后三项各含全量 `total` 和最多 6 条 `items`，条目提供管理页面 `href`。

- `tz_offset_minutes` 为浏览器时区相对 UTC 的分钟偏移，范围 `-720..840`，默认 `0`；`window` 给出含今天的近七个自然日及起止时间。
- `unhandled` 包含所有已完成且未处理答卷，不受近七天限制。`completed` 和 `failed_jobs` 按实际 `finished_at` 过滤；空结束时间不计入这两项。
- 历史答卷保留已软删除候选人，条目通过 `candidate_deleted` 标识。失败任务不返回任务参数或原始错误。

### `GET /api/admin/quizzes`

返回测验列表、分页信息、实例级仓库绑定信息与当前同步状态。

可传 `public_invite=enabled|disabled` 筛选实际公开邀约状态，省略或空字符串表示全部。该条件与搜索一起在分页前生效，`total`、`total_pages` 和 `filters.public_invite` 对应筛选结果；兼容入口 `/api/admin/exams` 使用同一约定。

### `GET /api/admin/quizzes/options`

返回后台表单选择器使用的测验选项列表，不分页。

### `GET /api/admin/quizzes/{quiz_key}`

返回测验详情、版本历史、公开邀约状态与测验快照。

题目快照中的展示字段约定：

- `stem_md` / `rubric` / `options[].text`：原始文本
- `stem_html` / `rubric_html` / `options[].text_html`：供前端直接展示的 HTML
- `rubric_html` 仅在管理端详情与答题回放接口返回，公开答题接口不暴露评分标准

### `POST /api/admin/quizzes/binding`

首次绑定内容仓库，并自动尝试创建同步任务。

请求：

```json
{ "repo_url": "https://github.com/example/repo.git" }
```

### `POST /api/admin/quizzes/binding/rebind`

重新绑定内容仓库。会删除当前实例中的测验、版本、邀约与答题归档数据，但保留候选人与简历；成功后自动尝试创建同步任务。

请求：

```json
{
  "repo_url": "https://github.com/example/new-repo.git",
  "confirmation_text": "重新绑定"
}
```

### `POST /api/admin/quizzes/sync`

为当前已绑定仓库创建或复用同步任务。同步内容包括 manifest 中声明的测验与职位。

说明：

- 未绑定仓库时返回 `409`
- 请求体中的 `repo_url` 仅为兼容保留，服务端会忽略它，不允许借此覆盖当前绑定仓库
- 职位使用 `md-quiz-repo.yaml` 中的 `job_descriptions` 清单，路径为 `job-descriptions/<jd_key>/jd.md`

### `POST /api/admin/quizzes/{quiz_key}/public-invite`

开启或关闭公开邀约。

返回字段包括：

- `enabled`
- `token`
- `public_url`
- `qr_url`

当关闭公开邀约时，`public_url` 和 `qr_url` 会返回空字符串。

### `GET /api/admin/quiz-analytics`

返回“测验分析”页左侧测验列表。

支持筛选参数：

- `q`
- `page`

### `GET /api/admin/quiz-analytics/{quiz_key}`

返回单个测验在指定时间窗口下的答题分析详情。

支持筛选参数：

- `window=week|month|half_year|year`
- `version_scope=all|current`

返回字段包括：

- `quiz`
- `filters`
- `summary`
- `distribution_groups`
- `trait_distribution`
- `items`

其中：

- `summary` 会区分总答题数、已完成数、进行中数、可计分完成数、traits-only 完成数
- `distribution_groups` 按 `score_max` 分组展示原始分数分布
- `trait_distribution` 只统计当前窗口内已完成且带 traits 结果的答卷，并按实际出现过的主倾向组合和维度对照聚合
- `items` 展示候选人、版本、状态、来源、进入/完成时间、得分与答题详情入口

### `GET /api/admin/candidates`

返回候选人列表、最近答题简报与筛选条件。支持 `q`、`created_from`、`created_to` 和 `page` 查询参数，其中日期使用 `YYYY-MM-DD`；省略日期参数时默认查询全部时间范围。列表按候选人创建时间倒序排列，同一时间以候选人 ID 倒序稳定排序。列表项会包含 `default_quiz_key` 与 `default_quiz_keys`，用于创建邀约时按候选人默认带出试题。

### `POST /api/admin/candidates`

创建候选人。请求体必须包含 `name`、`phone` 和非空 `job_description_id`，创建成功后会写入候选人与职位的关联。

### `POST /api/admin/candidates/resume/upload`

从简历直接创建或更新候选人。`multipart/form-data` 必须同时包含简历 `file` 和手动选择的 `job_description_id`。

### `POST /api/admin/candidates/resume/upload-job`

创建后台简历入库任务。`multipart/form-data` 必须同时包含简历 `file` 和手动选择的 `job_description_id`。

### `GET /api/admin/candidates/{candidate_id}`

返回候选人详情、关联职位、简历解析结果与答题记录。候选人字段会包含 `default_quiz_key` 与 `default_quiz_keys`，关联职位项会包含 `related_quizzes`。

### `POST /api/admin/candidates/{candidate_id}/job-descriptions`

为候选人增加一个或多个职位关联。请求体：

```json
{ "job_description_ids": [1, 2] }
```

### `DELETE /api/admin/candidates/{candidate_id}/job-descriptions/{job_description_id}`

取消候选人与指定职位的关联。

### `POST /api/admin/candidates/{candidate_id}/job-descriptions/remove`

取消候选人与指定职位的关联。请求体：

```json
{ "job_description_id": 1 }
```

### `POST /api/admin/candidates/{candidate_id}/evaluation`

追加面试评价。

### `GET /api/admin/candidates/{candidate_id}/resume`

下载候选人简历。

传 `preview=true` 时，服务端验证实际文件内容，仅 PNG、JPEG、WebP、BMP 图片可内嵌预览；返回真实图片 MIME、`Content-Disposition: inline`、`Cache-Control: no-store` 与 `X-Content-Type-Options: nosniff`。不支持或损坏的图片返回 `415`，未登录返回 `401`，候选人或文件不存在返回 `404`。两种模式均保留安全的 UTF-8 文件名；默认下载行为不变。

### `POST /api/admin/candidates/{candidate_id}/resume/reparse`

上传新简历并重新解析。

### `DELETE /api/admin/candidates/{candidate_id}`

删除候选人；若已有答题记录则执行软删除。

### `GET /api/admin/job-descriptions/options`

返回候选人创建、简历入库和候选人详情增加关联时可选的启用职位列表。

### `GET /api/admin/job-descriptions`

返回职位列表。职位项会包含 `related_quizzes`。Git 仓库来源职位会包含 `source_kind=git`、`jd_key`、`source_path`、`git_repo_url`、`last_synced_commit`、`last_sync_at` 和 `last_sync_error`。

### `POST /api/admin/job-descriptions`

创建手动职位。请求体可传 `related_quizzes`，内容为测验 `quiz_key` 数组。

### `POST /api/admin/job-descriptions/preview`

请求 `{ "content_md": "当前草稿" }`，返回 `{ "content_html": "…" }`。要求管理员登录，复用职位保存时的 Markdown 渲染与清洗，不保存职位或修改时间。

### `PUT /api/admin/job-descriptions/{job_description_id}`

更新手动职位。请求体可传 `related_quizzes`，内容为测验 `quiz_key` 数组。Git 仓库来源职位只读，需在 Git 仓库中修改后同步。

### `DELETE /api/admin/job-descriptions/{job_description_id}`

删除手动职位。Git 仓库来源职位需在 Git 仓库中归档或从 manifest 移除后同步。

### `GET /api/admin/assignments`

返回邀约与答题实例列表。

支持筛选参数：

- `q`
- `start_from`
- `start_to`
- `end_from`
- `end_to`
- `page`

列表项会返回邀约访问地址 `url`、二维码地址 `qr_url`，以及人工标记的主观题字段 `suspected_ai_question_ids`、`suspected_ai_question_count`。

### `POST /api/admin/assignments`

创建新的答题邀约。整卷答题时长不再由请求手填，服务端会按测验中每道题的 `answer_time` 自动累计写入 assignment。

- 请求体可传 `quiz_key`（兼容单选）或 `quiz_keys`（多选）；传多个 `quiz_keys` 时会为同一候选人分别创建多条邀约。
- 若请求未提供 `quiz_key`/`quiz_keys`，服务端会从候选人关联的启用职位中取 `related_quizzes` 作为默认测验列表。
- `ignore_timing=true` 时，当前邀约会关闭单题倒计时、超时自动跳题和整卷超时自动交卷。
- 返回的 assignment/list item 会包含 `ignore_timing` 字段。

### `GET /api/admin/attempts/{token}`

返回 assignment 与归档详情；`/api/admin/results/{token}` 为同义接口。

`review.answers[*]` 中会同时返回：

- `stem_html`
- `rubric_html`
- `options[].text_html`

### `POST /api/admin/assignments/{token}/suspected-ai-question`

为当前答题的一道主观题设置或取消“疑似 AI”人工标记。请求体：

```json
{ "qid": "Q3", "suspected": true }
```

仅接受当前答题回放中的 `short` 类型题目；响应中的 `item` 会返回更新后的 `suspected_ai_question_ids` 与 `suspected_ai_question_count`。

### `GET /api/admin/assignments/{token}/qr.png`

返回邀约二维码 PNG。

### `GET /api/admin/logs`

返回系统日志列表、分类计数，以及近 N 天的分类趋势序列。

`page`、`limit` 控制分页；纯数字 `q` 匹配完整日志编号，其他文字匹配操作者的字面子串（不区分大小写，`%`、`_` 不作通配符），不搜索原始上下文字段。`category` 可为 `candidate`、`quiz`、`grading`、`assignment`、`system`，省略或空字符串表示全部。筛选在分页前生效，返回对应的 `total`、`total_pages` 和 `filters`。分类总计与近 N 天趋势保留全局口径，不随列表筛选变化。

日志条目保留 `at`/`at_display` 作为单点时间；当条目包含完整起止时间时，会额外返回
`started_at`、`started_at_display`、`finished_at`、`finished_at_display`、`duration_display` 和
`has_time_range=true`。

### `GET /api/admin/logs/updates`

按 `after_id` 返回增量日志。

### `GET /api/admin/system-status/summary`

返回当天系统状态摘要。

- `llm` / `sms` 除了当天用量、阈值、配置缺失信息外，还会返回当前接入摘要：
  - `integration.title`
  - `integration.summary`

### `GET /api/admin/mcp/summary`

返回管理员可见的 MCP 接入摘要。

当前字段包括：

- `enabled`
- `path`
- `transport`
- `auth_scheme`
- `docs_path`
- `auth_token`
- `auth_token_configured`

### `GET /api/admin/system-status`

返回区间系统状态数据；其中 `summary` 字段与 `GET /api/admin/system-status/summary` 保持一致。

### `PUT /api/admin/system-status/config`

更新系统状态阈值。

### `GET /api/admin/config`

返回 runtime config。

### `PUT /api/admin/config`

更新 runtime config。

### `GET /api/admin/jobs`

列出任务。

返回的任务项当前至少包括：

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
- `created_at`
- `updated_at`
- `started_at`
- `lease_expires_at`
- `finished_at`

### `POST /api/admin/jobs`

投递任务。

说明：

- 这是通用后台/运维入口
- `grade_attempt` 通常不会由前端手工调用，而是由公开提交流程自动幂等投递

请求：

```json
{
  "kind": "scan_exams",
  "payload": {}
}
```

## Public

### `GET /api/public/bootstrap`

返回候选人端入口配置与功能开关。

### `GET /api/public/attempt/{token}`

返回候选人当前答题状态与公开测验快照。

其中 `quiz.spec.questions[*]` 的展示字段包括：

- `stem_html`
- `options[].text_html`

公开接口不返回 `rubric` / `rubric_html`。

### `POST /api/public/invites/{public_token}/ensure`

根据公开邀约 token 复用或创建答题实例。

### `GET /api/public/invites/{public_token}/qr.png`

返回公开邀约二维码 PNG。

### `GET /api/public/attempt/{token}`

返回候选人端当前步骤、答题状态、线性题流状态、简历状态或判卷结果。

- `step=verify`：返回开始卡片所需的验证信息；主动邀约只返回手机号掩码和验证码入口，公开邀约返回姓名/手机号/验证码入口。
- `step=resume`：公开邀约验证码通过但尚未建档时返回简历上传卡片信息。
- `step=quiz`：返回开始卡片或当前题卡片需要的公开测验快照、当前题索引、当前题开始时间、跨会话重进计数等；若 assignment 的 `ignore_timing=true`，倒计时相关字段会归零。
- `step=done`：返回结束卡片与判卷状态。

### `POST /api/public/attempt/{token}/enter`

进入第 1 题并启动线性答题流程。请求头会读取 `X-Public-Session-Id`，用于识别跨会话重进。

### `POST /api/public/sms/send`

发送短信验证码。

- 主动邀约 + 短信验证：只依赖 token 对应候选人的目标手机号，不再要求前端填写姓名/手机号。
- 公开邀约：仍要求姓名与手机号。

### `POST /api/public/verify`

验证短信认证并推进到下一步。

- 主动邀约：只要求验证码。
- 公开邀约：要求姓名、手机号与验证码；验证成功后进入简历上传或直接进入开始卡片。

### `POST /api/public/resume/upload`

公开邀约场景上传简历并创建候选人。上传成功后立即放行到开始卡片，简历结构化解析改为异步 job 回填。

### `POST /api/public/answers/{token}`

保存当前题答案，并可按请求语义推进到下一题或直接提交。旧题保存会返回冲突，候选人端据此禁止回退修改。

### `POST /api/public/answers_bulk/{token}`

兼容旧接口；当前仅接受“单题自动保存”语义，不再支持整卷批量保存。

### `POST /api/public/submit/{token}`

直接提交当前答卷并触发后台判卷。线性答题模式下，前端通常在最后一题通过 `POST /api/public/answers/{token}` 的 `submit=true` 直接完成提交。
