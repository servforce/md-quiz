<img src="static/logo.svg" alt="MD Quiz 标志" width="72" />

# MD Quiz

`md-quiz` 是一套 `code-first`、`agent-ready`、持续演进的`agent-native`在线评估运行时。它把题库管理、候选人流程、答题执行、结果留档和后台运维放在同一套系统里，支持 Git 仓库维护题库、公开邀约、手机号短信验证、LLM 自动评分，以及后台和 MCP 两种接入方式。

![界面截图](img/intro-by-gemini.png)

## 适合什么场景

- 招聘筛选与在线笔试
- 内部考核与培训结课测试
- 访谈式评估、晋升评审、认证类测验
- 需要公开邀约、短信验证、版本化题库和结果留档的场景

## 核心能力

- 题库与职位可放在同一个 Git 仓库里维护，`md-quiz-repo.yaml`、`quiz.md`、`assets/` 和 `job-descriptions/<jd_key>/jd.md` 一起定义测验与岗位内容，适合评审、回滚和长期迭代
- `quizzes/<quiz_id>/quiz.md` 使用 QML 编写；QML 是面向测验场景的 Markdown 扩展 DSL，用来描述题型、评分、计时和结构化判卷规则，详细格式见 [QML 测验格式](skills/qml-authoring/references/qml-spec.md)
- 管理端支持内容仓库绑定、题库/职位同步、版本浏览、候选人管理、邀约创建、专属答题链接生成，以及通过链接或二维码分发邀约，并支持答题结果回看
- 候选人端支持通过专属答题链接、二维码或公开邀约入口进入测验，内置手机号短信验证码校验，可按当前状态自动进入验证、简历、答题或完成阶段
- 答题流程支持单题计时、整卷累计时长、超时自动跳题、自动交卷和跨会话重进控制
- 结果支持客观题、主观题和 `traits` 量表；可接入 LLM 做判卷、结构化评分和简历解析
- 系统同时暴露 REST API 和 `/mcp`，便于后台操作和自动化接入
- 项目知识沉淀在 `docs/` 与 `skills/`；其中 `md-quiz` skill 可作为统一入口，用来出问卷、维护 `quiz.md` 和维护问卷仓库

## 典型流程

1. 绑定一个内容仓库并执行同步。可以直接用示例仓库 [Scisaga/Shire](https://github.com/Scisaga/Shire) 体验测验与职位同步流程。
2. 在后台创建候选人后，可生成专属答题链接，并通过链接或二维码分发给候选人；也可以直接开启公开邀约。系统会保存版本快照、资源路径和测验历史。
3. 候选人通过专属链接、二维码或公开邀约入口进入流程，经手机号短信验证码校验后，再根据配置继续上传简历、开始答题或恢复未完成会话。
4. 系统按线性题流执行测验，并在提交后生成可回看的结果记录；如已配置 LLM，还可以补充自动评分和结构化分析。
5. 管理员可在后台查看结果、日志、任务和系统状态；需要自动化时，也可以通过 MCP 调用同一套能力。

## 系统形态

### 进程

- `API`：对外提供 `/api/*`、会话、SPA 入口、静态资源和测验资源路由
- `Worker`：执行异步任务，例如内容仓库同步、判卷等后台工作
- `Scheduler`：投递周期任务，例如指标同步

默认 `docker compose` 部署为两个容器：

- `db`：PostgreSQL
- `app`：单个应用容器，内部同时托管 `API`、`Worker`、`Scheduler` 三个进程

### 对外入口

- 管理端：`/admin`
- 候选人端：`/p/*`、`/t/*`、`/resume/*`、`/quiz/*`、`/exam/*`、`/done/*`、`/a/*`
- REST API：`/api/admin/*`、`/api/public/*`、`/api/system/*`
- MCP：`/mcp`

更细的架构说明见 [docs/architecture/overview.md](docs/architecture/overview.md)。

前端当前仍是 `static/admin/` 和 `static/public/` 两套 Alpine SPA；CSS 构建命令保持 `npm run build:css` 与 `npm run build:admin-css` 不变，内部源码目录已拆为 `pages` / `views` / `modules` / `shared`。

## 快速开始

### 前置依赖

- Python 3
- PostgreSQL 16

在 Debian / Ubuntu 宿主机上执行 `./scripts/dev/install-deps.sh` 时，如果检测到缺少 `node` / `npm`，脚本会尝试通过 `apt-get install -y nodejs npm` 自动补装；其它系统仍需提前手动安装 Node.js 与 npm。

仓库自带两容器编排：

```bash
docker compose up -d
```

如果你只需要本地数据库，应用继续在宿主机上通过 `devctl` 运行：

```bash
docker compose up -d db
```

### 1. 复制环境变量

```bash
cp .env.example .env
```

同一份 `.env` 同时给宿主机直跑和 `docker compose` 使用；其中 Compose 内的 `app` 容器会自动把 `DATABASE_URL` 覆盖成 `db:5432`。
`db` 容器会直接读取 `.env` 里的 `POSTGRES_USER`、`POSTGRES_PASSWORD`、`POSTGRES_DB`；默认库名是 `md_quiz`。

宿主机本地开发可优先使用仓库默认数据库：

```text
postgresql+psycopg2://admin:pasword@127.0.0.1:5433/md_quiz
```

如果你本地已经用旧配置初始化过 `pgdata`，仅修改 `docker-compose.yml` 不会自动重建数据库账号；需要删除旧 volume 后再重新执行 `docker compose up -d db`。

### 2. 安装依赖

```bash
./scripts/dev/install-deps.sh
```

### 3. 启动服务

```bash
./scripts/dev/devctl.sh start
```

常用命令：

```bash
./scripts/dev/devctl.sh stop
./scripts/dev/devctl.sh restart
./scripts/dev/devctl.sh status
./scripts/dev/devctl.sh logs
```

`devctl` 每次实际启动已停止进程前，会把已有的非空日志归档到 `tmp/logs/archive/<timestamp>/`，新进程继续写入 `tmp/logs/api.log`、`tmp/logs/worker.log`、`tmp/logs/scheduler.log`。

单进程调试：

```bash
./scripts/dev/run-api.sh
./scripts/dev/run-worker.sh
./scripts/dev/run-scheduler.sh
```

默认访问地址：

- 管理端：`http://127.0.0.1:8000/admin`
- 根路径：`http://127.0.0.1:8000/`
- 健康检查：`http://127.0.0.1:8000/api/system/health`
- MCP：`http://127.0.0.1:8000/mcp`
- 公开邀约示例：`http://127.0.0.1:8000/p/<token>`

## 关键配置

### 基础运行

- `DATABASE_URL`：PostgreSQL 连接串
- `APP_SECRET_KEY`：会话签名密钥
- `ADMIN_USERNAME` / `ADMIN_PASSWORD`：后台登录账号
- `APP_HOST` / `PORT`：服务监听地址和端口

### LLM 配置

- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `OPENAI_BASE_URL`

用于自动判卷、简历解析等依赖 OpenAI-compatible `Responses API` 的能力，推荐服务商：

- 火山引擎方舟：当前 `.env.example` 默认就是按方舟接入编写，适合作为国内部署的优先选项。官方文档提供了 `OpenAI` SDK + `Responses API` 的直接调用方式，`base_url` 可配置为 `https://ark.cn-beijing.volces.com/api/v3`。

接入路径：

1. 在对应服务商控制台开通模型服务，获取 API Key，并确认可用的模型 ID。
2. 在 `.env` 中填写这 3 个变量：

```text
# 火山引擎方舟
OPENAI_API_KEY=<ARK_API_KEY>
OPENAI_MODEL=<你的模型 ID>
OPENAI_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
```

3. 重启服务后，通过简历解析、自动判卷或后台系统状态页验证接入是否生效。
4. 如果要接入其它平台，先确认目标平台和目标模型支持 OpenAI-compatible 的 `Responses API`；若只兼容 `chat/completions`，当前实现不可直接使用。

官方资料：

- OpenAI Responses API：https://platform.openai.com/docs/api-reference/responses
- 火山引擎方舟 OpenAI SDK / Responses API：https://www.volcengine.com/docs/82379/1338552

### MCP 配置

- `MCP_ENABLED=1`
- `MCP_AUTH_TOKEN=<token>`
- `MCP_CORS_ALLOW_ORIGINS=...`

启用后可通过 `/mcp` 暴露远程 HTTP MCP 服务，后台说明页位于 `/admin/mcp`。
`OpenClaw / VS Code / Codex` 的客户端配置示例见 [docs/reference/mcp.md](docs/reference/mcp.md)。

### 短信配置

- `ALIYUN_ACCESS_KEY_ID`
- `ALIYUN_ACCESS_KEY_SECRET`
- `ALIYUN_PNVS_SIGN_NAME`
- `ALIYUN_PNVS_TEMPLATE_CODE`

接入路径：

1. 在阿里云开通号码认证服务中的“短信认证服务”。
2. 进入控制台选择短信签名与模板。优先使用平台赠送的签名和模板，这也是阿里云官方推荐的路径：不需要自己再走额外的资质、签名和模板审核，发送成功率也更稳定。
3. 在 `.env` 中至少配置以下变量：

```text
ALIYUN_ACCESS_KEY_ID=<你的 AccessKey ID>
ALIYUN_ACCESS_KEY_SECRET=<你的 AccessKey Secret>
ALIYUN_PNVS_SIGN_NAME=<短信签名>
ALIYUN_PNVS_TEMPLATE_CODE=<短信模板 ID>
```

4. 如有需要，再补充 `ALIYUN_PNVS_REGION_ID`、`ALIYUN_PNVS_TEMPLATE_PARAM`、`ALIYUN_PNVS_VALID_TIME`、`ALIYUN_PNVS_CASE_AUTH_POLICY` 等高级参数。
5. 重启服务后，通过候选人验证流程或公开邀约验证流程触发短信发送与验证码校验。

官方资料：

- 阿里云短信认证服务说明：https://help.aliyun.com/zh/pnvs/user-guide/sms-authentication-service/
- 阿里云号码认证服务版本说明（含 `SendSmsVerifyCode` / `CheckSmsVerifyCode`）：https://help.aliyun.com/zh/pnvs/developer-reference/api-dypnsapi-2017-05-25-changeset
- 阿里云短信认证服务新手指引（个人/企业、免申请资质说明）：https://help.aliyun.com/zh/pnvs/getting-started/sms-authentication-service-novice-guide

完整变量说明见 [docs/reference/configuration.md](docs/reference/configuration.md)。

## 使用约定

- 一套 `md-quiz` 实例只绑定一个内容仓库
- 首次绑定后，“立即同步”只会针对当前绑定仓库执行
- 更换仓库必须走显式“重新绑定”流程
- 运行时阈值和主题等配置保存在数据库，不以 `.env` 为唯一事实来源
- `/legacy/*` 只保留为兼容跳转入口，不作为正式使用路径

## 测试

```bash
./scripts/dev/test.sh -q
```

如果只想跑 FastAPI 相关测试：

```bash
./scripts/dev/test.sh tests/test_fastapi_app.py -q
```

## 参考文档

- [文档导航](docs/README.md)
- [架构总览](docs/architecture/overview.md)
- [运行拓扑](docs/architecture/runtime-topology.md)
- [配置项说明](docs/reference/configuration.md)
- [REST API 约定](docs/reference/api.md)
- [MCP 能力说明](docs/reference/mcp.md)
- [UI 主题覆盖](docs/ui/theme.md)

## License

本项目采用 [Apache License 2.0](LICENSE) 开源协议发布。

你可以在保留许可证与版权声明的前提下使用、修改和分发本项目；完整条款见根目录 [LICENSE](LICENSE)。
