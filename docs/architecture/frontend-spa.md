# 前端 SPA 结构

当前前端保持两套 Alpine SPA，并继续复用 FastAPI 的统一入口契约：

- `/admin*` 返回 `static/admin/index.html`
- `/p/*`、`/t/*`、`/resume/*`、`/quiz/*`、`/exam/*`、`/done/*`、`/a/*` 返回 `static/public/index.html`

## 总体形态

- 后台与候选人端都不再直接在 `index.html` 内堆业务逻辑
- HTML 只保留应用壳、挂载点和静态资源引用
- 业务状态、路由、API 调用和页面交互都拆到 `modules/`
- 实际视图片段拆到 `pages/` 或 `views/`，通过共享的 fragment loader 动态挂载

## 后台 SPA

### 目录

- `static/admin/index.html`
  - 后台应用壳
  - 只保留侧栏、顶部标题区、通知区和 `pageMount` / `loginMount`
- `static/admin/app.js`
  - 注册 `adminApp`
  - 把 state、api、shell、router 和页面模块组合成一个 Alpine data 对象
- `static/admin/modules/state.js`
  - 后台全局状态、筛选条件、表单默认值、页面缓存与紧凑布局状态
- `static/admin/modules/constants.js`
  - 紧凑布局 tab 配置、MCP 说明页常量、简历上传状态工厂
- `static/admin/modules/api.js`
  - 后台统一请求入口、错误处理、401 会话失效收敛
- `static/admin/modules/router.js`
  - `/admin/*` 路由解析
  - 片段路径映射
  - `loadHtmlFragment(...)` 挂载与 history 协调
- `static/admin/modules/shell.js`
  - 启动流程、登录退出、主导航、通知、移动端紧凑布局与局部滚动状态
- `static/admin/modules/pages/*.js`
  - 页面级状态与动作，按领域拆分为 `quizzes / quiz-analytics / candidates / assignments / logs / status`
- `static/admin/pages/*.html`
  - 后台页面片段
  - 当前页面包括：`login`、`quizzes`、`quiz-analytics`、`quiz-detail`、`candidates`、`candidate-detail`、`assignments`、`attempt-detail`、`logs`、`status`、`mcp`

### 路由装载流程

1. `index.html` 只启动 `adminApp().boot()`
2. `boot()` 先请求 `/api/admin/session`
3. 已登录时再预加载 `/api/admin/bootstrap`、状态摘要及部分列表数据
4. `router.js` 根据当前路径选择页面片段
5. 片段加载到 `pageMount` 或 `loginMount`
6. 片段中的 Alpine 指令通过共享 loader 重新 `initTree`

## 候选人端 SPA

### 目录

- `static/public/index.html`
  - 候选人端应用壳
  - 保留答题进度头部、错误提示区和 `viewMount`
- `static/public/app.js`
  - 注册 `publicApp`
  - 组合 state、api、router、verify、resume、quiz、view-loader
- `static/public/modules/state.js`
  - 公开流程全局状态、短信状态、答题计时器、自动保存草稿和 fragment cache
- `static/public/modules/constants.js`
  - OTP 长度、会话 ID 生成等常量
- `static/public/modules/api.js`
  - 候选人端统一请求入口
  - 负责附带 `X-Public-Session-Id`
- `static/public/modules/router.js`
  - 解析 `/p/{public_token}` 与 `/t/{token}` 等路径
  - 管理 `sessionStorage` 中的每 token 会话 ID
  - 根据 `/api/public/attempt/{token}` 返回的 `state.step` 推导当前视图
- `static/public/modules/view-loader.js`
  - `viewCard -> HTML fragment` 映射
  - 挂载 `static/public/views/*.html`
- `static/public/modules/verify.js`
  - 短信验证码发送、OTP 输入与校验
- `static/public/modules/resume.js`
  - 公开邀约简历上传
- `static/public/modules/quiz.js`
  - 当前题展示、自动保存、倒计时、超时提交、手势交互
- `static/public/views/*.html`
  - 候选人视图片段
  - 当前视图包括：`start`、`resume`、`question`、`done`、`unavailable`

### 视图切换流程

1. `publicApp().init()` 解析当前路径
2. `/p/{public_token}` 先调用 `/api/public/invites/{public_token}/ensure` 换到真实 attempt token
3. 路由模块为当前 token 建立 `sessionStorage` 会话 ID
4. 再请求 `/api/public/attempt/{token}`
5. 根据返回的 `state.step` 把 `viewCard` 切到 `start / resume / question / done / unavailable`
6. `view-loader.js` 把对应 HTML 片段挂到 `viewMount`

## 共享层

### 共享脚本

- `static/assets/js/shared/runtime.js`
  - HTML 片段加载与缓存
  - `Alpine.initTree(...)` / 销毁辅助
  - MathJax 触发
  - 文本复制与绝对路径拼接
- `static/assets/js/math-render.js`
  - 公式渲染辅助

### 共享样式

- `static/assets/css/shared/tokens.css`
- `static/assets/css/shared/rich-content.css`
- `static/assets/css/shared/utilities.css`

这些共享层同时被后台和候选人端复用。

## CSS 与运行时资源构建

### 构建命令

- `npm run build:css`
- `npm run build:admin-css`

当前 `build:admin-css` 实际上只是 `build:css` 的别名；两套 SPA 的 CSS 会在一次构建里同时产出。

### CSS 拼接与输出

`static/scripts/build-admin-css.cjs` 会按固定顺序拼接源码，再交给 PostCSS + Tailwind：

- 后台输出 `static/admin.css`
  - `assets/css/admin/theme.css`
  - `assets/css/shared/tokens.css`
  - `assets/css/shared/rich-content.css`
  - `assets/css/shared/utilities.css`
  - `assets/css/admin/shell.css`
  - `assets/css/admin/components.css`
  - `assets/css/admin/pages.css`
  - `assets/css/admin/responsive.css`
- 候选人端输出 `static/public.css`
  - `assets/css/public/theme.css`
  - `assets/css/shared/tokens.css`
  - `assets/css/shared/rich-content.css`
  - `assets/css/shared/utilities.css`
  - `assets/css/public/components.css`
  - `assets/css/public/views.css`

### 本地运行时资源同步

`npm run build:css` 在 CSS 编译后还会执行 `static/scripts/sync-runtime-assets.cjs`，把运行时依赖同步到仓库内静态目录：

- `node_modules/alpinejs/dist/cdn.min.js` -> `static/assets/js/alpine.min.js`
- `node_modules/mathjax/es5/tex-svg.js` -> `static/assets/js/vendor/mathjax/tex-svg.js`

因此生产环境和 Docker 镜像不依赖外部 CDN。

## 管理端启动与内网 HTTP

- 管理页面支持普通内网 HTTP。导航历史条目的标识通过 `createAdminHistoryEntryId()` 生成，使用 `crypto.getRandomValues()`，不依赖仅安全上下文可用的 `crypto.randomUUID()`；该标识只供浏览器历史与滚动位置关联，不参与登录认证。
- `boot()` 捕获初始化异常并在 `finally` 结束加载状态。失败时展示 `bootError` 和重新加载按钮，避免永久停留在加载遮罩。
- 浏览器验收必须覆盖非 loopback 的 HTTP 地址。`localhost` 和 `127.0.0.1` 被视为安全上下文，不能代替实际内网 HTTP 场景。
- 回归检查：`python -m pytest --noconftest tests/test_admin_http_browser.py -q`。需要安装 agent-browser 与 Chromium；测试启动临时静态服务器，执行真实导航模块及 API 404 失败路径，不访问业务数据库。可用 `ADMIN_BROWSER_TEST_HOST` 指定测试机的非 loopback IPv4 地址。
