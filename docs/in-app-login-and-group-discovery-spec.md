# Collector 内置扫码登录与群 ID 发现规格

更新时间：2026-08-25

## 1. 文档目的

本文定义如何把 `weibo-chat-auto` 中与初始化相关的两项能力迁移到
`weibo-chat-collector`：

1. 在 collector 页面内发起微博扫码登录，并把 Cookie 直接绑定到所选账号。
2. 在同一受控浏览器会话中打开目标群聊，自动发现
   `query_messages.json` 请求中的群 `id`，经用户确认后绑定到所选本地群聊。

完成后，用户只需要启动 `weibo-chat-collector`，不需要单独启动或操作
`weibo-chat-auto`。

本文最初是功能规格。相关功能现已实现；后续修改仍需遵守本文定义的安全边界、
状态机、接口契约和验收标准。

截至 2026-08-25 的实现与验证状态：

- 后端单例浏览器会话、前端状态轮询、取消、重新发现和人工确认绑定已实现。
- Cookie 已通过私有 JSONL 管道保存到对应本机账号文件，不进入数据库或前端响应。
- 账号 A 已实机完成扫码、Cookie 就绪、候选群 ID 发现、确认绑定，并使用该凭据完成真实 API 采集。
- 账号 B 的独立 Cookie/临时目录隔离，以及取消、10 分钟超时、关闭 Chrome 和服务重启后的实机清理仍待验证。

## 2. 背景与当前状态

当前项目职责如下：

- collector 负责账号和群聊配置、内置扫码、Cookie 安全保存、群 ID 发现、任务队列、
  微博 API 请求、SQLite 入库、监控、检索和删除。
- collector 可自行打开隔离的可见 Chrome 扫码窗口；已有 `cookies.json` 只保留为备用导入。
- collector 从精确匹配的 `query_messages.json?id=纯数字` 请求发现候选群 ID，
  并要求用户确认后才写入数据库。
- `weibo-chat-auto` 不再是主流程依赖；其实现仅作为早期来源和兼容参考。

collector 已有能力应继续复用：

- `CookieProfileStore` 对微博 Cookie 进行过滤、校验和按账号隔离保存。
- 必须存在可发送给 `api.weibo.com` 的非空、未过期 `SUB`。
- Cookie 按账号保存为 `data/auth/account-<id>.cookies.json`。
- Cookie 内容不写入 SQLite、不返回前端、不进入任务 API。
- `chat_groups.source_group_id` 保存确认后的微博群 ID。
- 已有归档历史的本地群不能随意改绑到另一个真实群。

## 3. 产品目标

### 3.1 核心目标

用户在 collector 内完成以下闭环：

```text
选择本地账号和群聊
        |
        v
点击“扫码登录”
        |
        v
collector 打开独立 Chrome 窗口
        |
        v
用户使用微博 App 扫码
        |
        v
collector 提取并安全保存 Cookie
        |
        v
用户在该窗口打开目标群聊
        |
        v
collector 捕获 query_messages.json?id=...
        |
        v
页面展示候选群 ID 和目标群聊名称
        |
        v
用户二次确认绑定
        |
        v
Cookie 已就绪 + 群 ID 已绑定
        |
        v
用户创建采集任务
```

### 3.2 用户体验目标

- 用户不需要进入另一个项目目录。
- 用户不需要运行 `npm run save-cookies`。
- 用户不需要寻找和上传 `cookies.json`。
- 用户不需要打开开发者工具寻找群 ID。
- 敏感 Cookie 不经过前端 JavaScript 状态或浏览器本地存储。
- 账号 A、账号 B 分别建立独立登录会话和 Cookie 文件。

## 4. 非目标

本次迁移不包括 `weibo-chat-auto` 的以下能力：

- auto 自己的消息归档和输出目录。
- auto 查看器。
- AI 摘要与问答。
- 定时归档。
- auto 数据库或 JSON 归档结果。
- auto 的统计、搜索和上下文界面。
- 自动绕过验证码、登录验证、访问权限或平台风控。
- 后台自动刷新失效登录态。

JSON/CSV 导入、网页快照和现有 Cookie 文件上传继续作为高级备用工具保留。

## 5. 总体架构

### 5.1 推荐实现

collector 内新增一个本地浏览器助手，复用 Node.js + Puppeteer 的成熟逻辑：

```text
React 前端
    |
    | 仅发送账号 ID、群聊 ID 和用户操作
    v
FastAPI 登录会话服务
    |
    | 启动/停止本项目内的 Node 浏览器助手
    v
Puppeteer + 系统 Chrome
    |
    | Cookie 通过受控进程通信交回后端
    | 群 ID 作为候选发现结果交回后端
    v
CookieProfileStore / SQLite
```

浏览器助手属于 `weibo-chat-collector` 仓库和运行流程的一部分。用户不需要把它
作为第二个项目或第二个服务手工启动。

### 5.2 为什么优先复用 Puppeteer

- auto 现有登录流程已经基于 Puppeteer 运行。
- 已有 Chrome 路径探测、登录完成判断和包含 HttpOnly Cookie 的提取逻辑。
- collector 前端本身已经依赖 Node.js，不会新增一门系统运行时。
- 相比改写为 Python Playwright，迁移范围更小，行为差异更少。

后续可以评估纯 Python Playwright，但不应作为首版迁移的前置条件。

### 5.3 建议目录

```text
weibo-chat-collector/
  browser-helper/
    package.json
    src/
      login-session.js
      chrome-path.js
      protocol.js
  backend/app/
    api/
      browser_login.py
    services/
      browser_login_manager.py
  frontend/src/
    main.tsx
```

浏览器助手只包含登录和群 ID 发现相关代码，不复制 auto 的归档器、查看器和 AI
依赖。

## 6. 功能范围

### 6.1 扫码登录

用户在 collector 中选择本地账号后，可以点击“扫码登录”。系统必须：

1. 校验账号存在且启用。
2. 确保当前没有其他活动的浏览器登录会话。
3. 启动独立、可见的 Chrome 窗口。
4. 打开 `https://api.weibo.com/chat#/chat`。
5. 等待用户扫码并在手机端确认。
6. 判断页面已离开扫码登录状态并进入聊天区域。
7. 等待页面短暂稳定。
8. 提取浏览器会话中微博、新浪相关域的全部 Cookie，包括 HttpOnly Cookie。
9. 把 Cookie 直接传给 FastAPI 后端。
10. 使用现有 `CookieProfileStore` 校验和保存。
11. 页面显示“Cookie 已就绪”。

不得把 Cookie 先写入前端、普通日志或临时明文响应文件。

### 6.2 群 ID 发现

Cookie 保存成功后，浏览器会话可继续用于群 ID 发现：

1. 用户在 collector 中已经选择一个本地群聊。
2. 页面提示用户在扫码窗口中打开该目标群聊。
3. 浏览器助手监听请求或响应 URL。
4. 只处理路径匹配以下接口的请求：

```text
/webim/groupchat/query_messages.json
```

5. 从查询参数读取纯数字 `id`。
6. 将其保存为当前会话的候选群 ID，不立即写数据库。
7. collector 页面展示候选群 ID、所选账号和所选本地群聊名称。
8. 用户点击“确认绑定”后，后端再次校验并写入
   `chat_groups.source_group_id`。

如果同一会话捕获多个不同群 ID，应展示最新候选值和捕获时间，并明确提示用户
重新打开目标群后再确认。

### 6.3 手工 Cookie 导入保留

现有“选择 Cookie 文件”入口应保留，但移动到高级或备用区域。用途包括：

- 浏览器助手在某个平台不可用时导入已有文件。
- 从受信任的本地环境迁移登录状态。
- 调试 Cookie 格式。

手工导入与扫码登录必须共用同一个 `CookieProfileStore`，不能产生两套安全规则。

## 7. 登录会话状态机

### 7.1 状态定义

```text
idle
  -> launching_browser
  -> awaiting_scan
  -> login_detected
  -> cookie_saved
  -> awaiting_group_selection
  -> group_candidate_found
  -> binding_confirmed
  -> completed
```

任意活动状态都可以转为：

```text
cancelled
timed_out
failed
```

### 7.2 状态说明

| 状态 | 含义 |
| --- | --- |
| `idle` | 没有活动会话 |
| `launching_browser` | 正在探测 Chrome 并启动浏览器 |
| `awaiting_scan` | 登录页面已打开，等待用户扫码 |
| `login_detected` | 已检测到登录成功，正在提取 Cookie |
| `cookie_saved` | Cookie 已通过校验并按账号保存 |
| `awaiting_group_selection` | 等待用户在浏览器中打开目标群聊 |
| `group_candidate_found` | 已发现候选群 ID，等待用户确认 |
| `binding_confirmed` | 候选群 ID 已绑定到本地群聊 |
| `completed` | 会话已完成，可关闭浏览器 |
| `cancelled` | 用户主动取消 |
| `timed_out` | 超过会话最大时长 |
| `failed` | 浏览器、登录、Cookie 或捕获过程失败 |

### 7.3 并发限制

- collector 全局任意时刻最多允许一个浏览器初始化会话。
- 重复点击启动时返回 `409`，并返回当前非敏感会话状态。
- 会话必须记录所选 `account_id` 和 `group_id`，不能在活动过程中静默切换。
- API 采集任务运行期间默认拒绝开启扫码/发现会话，避免同一账号同时访问微博。
- `awaiting_confirmation` 任务也应视为准备运行状态，前端需提示先处理该任务。

## 8. 后端接口规格

接口路径最终实现时可以微调，但语义必须保持一致。

### 8.1 启动登录与发现会话

```text
POST /api/weibo-api/browser-sessions
```

请求：

```json
{
  "account_id": 1,
  "group_id": 1
}
```

成功响应：`202 Accepted`

```json
{
  "session_id": "随机不可预测的会话 ID",
  "account_id": 1,
  "group_id": 1,
  "status": "launching_browser",
  "expires_at": "2026-08-22T13:10:00+08:00"
}
```

接口不得返回 Cookie、请求头、浏览器调试地址或进程命令行中的敏感信息。

### 8.2 查询会话状态

```text
GET /api/weibo-api/browser-sessions/{session_id}
```

响应示例：

```json
{
  "session_id": "...",
  "account_id": 1,
  "group_id": 1,
  "status": "group_candidate_found",
  "cookie_ready": true,
  "candidate_source_group_id": "123456789",
  "candidate_captured_at": "2026-08-22T13:03:12+08:00",
  "error_code": null,
  "error_message": null,
  "expires_at": "2026-08-22T13:10:00+08:00"
}
```

### 8.3 确认绑定群 ID

```text
POST /api/weibo-api/browser-sessions/{session_id}/confirm-group
```

请求：

```json
{
  "candidate_source_group_id": "123456789"
}
```

后端必须：

1. 校验会话属于当前所选账号和群聊。
2. 校验请求值与当前会话最新候选值一致。
3. 校验群 ID 只包含数字。
4. 校验同一账号下没有其他群绑定相同源群 ID。
5. 复用现有“已有历史不可改绑”保护。
6. 在事务中更新 `chat_groups.source_group_id`。

### 8.4 取消会话

```text
POST /api/weibo-api/browser-sessions/{session_id}/cancel
```

取消必须：

- 终止等待逻辑。
- 关闭 Puppeteer browser。
- 结束子进程。
- 清理临时浏览器目录。
- 保留已经成功保存的 Cookie，不用失败结果覆盖旧 Cookie。

### 8.5 当前状态兼容

现有接口继续保留：

```text
PUT /api/weibo-api/accounts/{account_id}/cookies
GET /api/weibo-api/status
POST /api/weibo-api/targets
```

扫码成功后，`GET /api/weibo-api/status` 应立即反映当前账号 Cookie 已就绪；群绑定
确认后也应立即返回新的 `source_group_id`。

## 9. 浏览器助手协议

### 9.1 进程通信

推荐使用受控的标准输入/标准输出 JSON Lines 协议，或者仅监听随机本地端口的受限
IPC。首版优先 JSON Lines，避免新增可被其他本机页面访问的 HTTP 服务。

事件示例：

```json
{"event":"browser_opened"}
{"event":"awaiting_scan"}
{"event":"login_detected"}
{"event":"cookies","cookies":["仅发送给父进程，不记录日志"]}
{"event":"group_candidate","source_group_id":"123456789"}
{"event":"closed"}
```

父进程必须把 `cookies` 事件作为敏感数据处理，不得把原始事件打印到日志。

### 9.2 浏览器配置

- 使用可见模式，禁止首版默认 headless 扫码。
- 优先探测 Google Chrome、Chromium 或 Edge。
- 使用独立临时用户数据目录，不读取日常浏览器用户目录。
- 默认不使用 `--no-sandbox`；只有经过明确平台评估后才允许特例。
- 浏览器窗口关闭视为用户取消或会话失败。
- 后端退出时必须关闭子进程和浏览器。

### 9.3 登录成功判断

首版可复用 auto 的页面文本判断，但必须设计为可替换策略：

- 登录页包含“扫描登录”或“立即注册”时继续等待。
- 页面进入聊天区域且内容长度达到合理阈值时视为候选登录成功。
- 最终成功必须以提取到有效 `SUB` 并通过 `CookieProfileStore` 校验为准。

仅根据页面文本判断不能把 Cookie 标记为已就绪。

## 10. 前端规格

### 10.1 采集任务页

账号和群聊选择区下方新增：

```text
账号登录
[ 扫码登录当前账号 ]
状态：未登录 / 等待扫码 / Cookie 已就绪 / 登录失败

群聊绑定
[ 打开浏览器并发现群 ID ]
候选群 ID：123456789
当前本地群聊：汉语从句研究会
[ 确认绑定 ] [ 重新发现 ]
```

### 10.2 按钮规则

- 未选择账号时禁用“扫码登录”。
- 未选择群聊时允许扫码登录，但不能确认群 ID。
- 活动会话存在时禁用重复启动。
- Cookie 未就绪或群 ID 未绑定时禁用“启动采集”，并显示明确原因。
- 发现候选群 ID 后必须显示所选账号和群聊，避免用户确认到错误目标。
- 关闭浏览器或取消后，页面应在下一次状态轮询中恢复为可操作状态。

### 10.3 轮询

- 活动会话期间每 1～2 秒查询一次状态。
- 进入 `completed`、`cancelled`、`timed_out` 或 `failed` 后停止轮询。
- 页面刷新后可通过“当前活动会话”接口恢复状态，而不是开启第二个窗口。

### 10.4 提示文案

必须明确提示：

- 扫码必须使用当前所选账号。
- 群 ID 发现时必须在浏览器中打开当前所选目标群聊。
- Cookie 只保存在本机，不会显示或返回页面。
- 不要同时使用微博 App 或其他微博网页进行操作。
- 群 ID 候选值只有在用户确认后才会绑定。

## 11. 安全与隐私要求

### 11.1 网络边界

- collector 默认只监听 `127.0.0.1`。
- 如果服务监听非回环地址，扫码登录接口必须拒绝启动或给出阻断性错误。
- 不提供远程扫码登录能力。

### 11.2 Cookie 安全

- Cookie 不进入 React 状态、LocalStorage、SessionStorage 或浏览器日志。
- Cookie 不写入 SQLite。
- Cookie 不出现在 API 响应、异常详情、任务状态或审计记录中。
- Cookie 文件继续使用原子替换。
- POSIX 继续使用目录 `0700`、文件 `0600`。
- 新登录没有有效 `SUB` 时不得覆盖原 Cookie 文件。

### 11.3 日志脱敏

禁止记录：

- Cookie 值。
- `Cookie`、`Set-Cookie`、`Authorization` 完整请求头。
- 浏览器调试协议中的原始敏感事件。
- 未脱敏响应 body。
- 个人账号密码、手机号或验证码。

允许记录：

- 会话 ID 的短标识。
- 账号数据库 ID。
- 状态变化。
- Cookie 数量。
- 是否存在有效 `SUB`。
- 候选群 ID 的确认事件；如需更严格隐私，可只记录掩码。

### 11.4 临时文件

- 浏览器临时用户目录必须创建在系统临时目录或项目受控临时目录。
- 会话结束后清理临时目录。
- 不使用用户日常 Chrome profile。
- 不生成通用的根目录 `cookies.json`。

## 12. 错误处理

建议错误码：

| 错误码 | 含义 | 用户操作 |
| --- | --- | --- |
| `browser_not_found` | 未找到可用 Chrome/Chromium/Edge | 安装浏览器或配置路径 |
| `browser_launch_failed` | 浏览器启动失败 | 查看非敏感错误后重试 |
| `login_session_busy` | 已有活动会话 | 回到现有会话或取消 |
| `login_timeout` | 扫码超时 | 重新启动扫码 |
| `browser_closed` | 用户提前关闭窗口 | 重新开始 |
| `login_cookie_missing` | 未提取到有效 `SUB` | 重新扫码并等待登录完成 |
| `group_not_observed` | 未捕获目标接口 | 重新打开目标群聊 |
| `group_candidate_changed` | 候选群 ID 已变化 | 重新核对并确认最新值 |
| `group_binding_conflict` | 同账号已有群绑定该 ID | 检查现有配置 |
| `group_rebind_blocked` | 有历史数据的群禁止改绑 | 新建本地群或保留原绑定 |
| `helper_protocol_error` | 浏览器助手输出异常 | 停止会话并检查版本兼容 |

错误不得自动重复打开浏览器或高频重试微博请求。

## 13. 数据模型

首版可以把活动登录会话只保存在后端内存中，不必新增数据库表。原因：

- 会话是短期本机交互。
- 后端重启后不应自动恢复浏览器扫码。
- Cookie 和群绑定结果已有正式存储位置。

如后续需要审计，可新增只包含非敏感元数据的表，但不得保存 Cookie、完整 URL、
请求头或浏览器存储内容。

内存会话至少包含：

```text
session_id
account_id
group_id
status
created_at
expires_at
cookie_ready
candidate_source_group_id
candidate_captured_at
helper_process_id
error_code
sanitized_error_message
```

## 14. 与采集队列的协调

- 浏览器初始化会话不进入微博 API 采集 FIFO 队列。
- 但浏览器初始化与 API 采集不能同时使用同一个账号。
- 首版建议任意 `weibo_api_v2` 任务处于 `running` 时拒绝启动浏览器会话。
- 如果已有任务处于 `awaiting_confirmation`，前端应提示用户先停止或完成该任务。
- 登录完成不会自动启动采集任务。
- 群 ID 绑定完成也不会自动创建采集任务。
- 用户仍需显式选择时间范围并创建任务。

## 15. 测试要求

### 15.1 后端单元测试

- 账号或群聊不存在时拒绝启动。
- 同时只能创建一个浏览器会话。
- 会话超时会关闭助手并清理状态。
- 取消会话是幂等的。
- Cookie 事件不会进入 API 响应或日志。
- 无有效 `SUB` 时不覆盖旧 Cookie。
- 账号 A、账号 B 的 Cookie 文件隔离。
- 群 ID 必须是纯数字。
- 候选值与确认值不一致时拒绝绑定。
- 同账号重复群 ID 冲突时返回 `409`。
- 有历史数据的群改绑时继续返回 `409`。

### 15.2 浏览器助手测试

- Chrome 路径探测。
- 重复启动保护。
- 登录页和已登录页状态判断。
- 微博域 Cookie 过滤。
- 从请求 URL 提取群 ID。
- 忽略非目标接口、无 `id`、非数字 `id`。
- 浏览器关闭、超时和父进程退出时正确结束。
- JSON Lines 协议不会在普通日志输出 Cookie。

### 15.3 前端测试

- 未选择账号时按钮禁用。
- 状态轮询和终止条件正确。
- 候选群 ID 未确认前不显示已绑定。
- Cookie 和群 ID 未就绪时不能启动采集。
- 错误提示不包含敏感数据。
- 页面刷新后能恢复当前活动会话显示。

### 15.4 人工验收

分别对账号 A 和账号 B 执行：

1. 页面内启动扫码。
2. 使用正确账号扫码。
3. 登录成功后 Cookie 状态变为已就绪。
4. 检查只生成对应账号的 Cookie 文件。
5. 打开对应目标群聊。
6. 页面出现候选群 ID。
7. 确认绑定后状态变为已绑定。
8. 关闭浏览器助手。
9. 创建一个短时间范围采集任务。
10. 验证任务可以使用新 Cookie 和群 ID 请求。
11. 验证两个账号的数据仍进入同一数据库且正确隔离。

## 16. 验收标准

功能只有同时满足以下条件才算完成：

- 用户从 collector 页面可以启动可见扫码窗口。
- 用户不需要进入 `weibo-chat-auto` 目录或运行其命令。
- 扫码后 collector 自动保存所选账号的有效 Cookie。
- Cookie 不经过前端且不出现在日志和 API 响应中。
- 用户在浏览器中打开目标群聊后，collector 能发现候选群 ID。
- 群 ID 必须经过用户确认才绑定。
- 账号 A、账号 B 的 Cookie、群聊和发现会话相互隔离。
- 会话超时、取消和后端退出都不会留下浏览器僵尸进程。
- 现有手工 Cookie 导入继续可用。
- 现有 API 采集任务、队列、停止和续传测试继续通过。
- 前端类型检查和生产构建通过。
- 完成账号 A、账号 B 的本机人工验收。

## 17. 实施阶段

### 阶段 1：浏览器助手基础

- 在 collector 内建立最小 Node/Puppeteer 助手。
- 迁移 Chrome 路径探测和扫码登录判断。
- 建立受控进程协议。
- 完成进程超时、取消和清理。

产出：后端能够启动扫码窗口并接收经过过滤的 Cookie。

### 阶段 2：Cookie 后端与前端闭环

- 新增浏览器会话 API。
- 复用 `CookieProfileStore` 保存 Cookie。
- 新增“扫码登录”按钮和状态轮询。
- 保留手工 Cookie 文件导入。

产出：用户不离开 collector 即可让账号显示“Cookie 已就绪”。

### 阶段 3：群 ID 发现与确认

- 监听 `query_messages.json`。
- 提取候选群 ID。
- 新增候选展示、重新发现和确认绑定。
- 复用现有群绑定冲突保护。

产出：用户不打开开发者工具即可完成“群 ID 已绑定”。

### 阶段 4：安全、测试与文档收口

- 补齐敏感数据测试、并发测试、超时测试和浏览器清理测试。
- 完成账号 A、账号 B 实机验收。
- 更新 README、运行指南、当前状态和待办清单。
- 删除 collector 文档中“必须进入 auto 扫码”的运行要求，改为备用兼容说明。

产出：collector 成为唯一需要用户运行的项目。

## 18. 发布与回退

### 18.1 发布策略

- 首版把内置扫码标记为本地实验能力。
- 先在账号 A 上小范围验证，再在账号 B 上验证。
- 保留手工 Cookie 上传，至少经过一个稳定发布周期后再评估是否弱化入口。
- 不自动迁移或删除现有 `data/auth/` Cookie 文件。

### 18.2 回退策略

如果浏览器助手不可用：

- 不影响 collector 启动。
- 不影响消息查询、删除或已有采集任务记录。
- 用户仍可使用现有 Cookie 文件上传。
- 已绑定的群 ID 保持不变。
- 禁止以失败登录结果覆盖原有效 Cookie。

## 19. 开发约束

- 不恢复 auto 的归档数据库或查看器。
- 不让浏览器助手直接写 collector SQLite。
- 不让 Node 助手自行决定账号和群聊绑定。
- 不把 Cookie 通过普通 HTTP 响应返回给 React。
- 不在扫码成功后自动创建或确认采集任务。
- 不绕过微博登录、安全验证、账号权限或访问频率限制。
- 不把内部微博接口描述为公开、官方或保证稳定的 API。
