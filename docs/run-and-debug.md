# 运行与调试指南

更新时间：2026-08-25

## 环境与路径

- Python `>= 3.10`，推荐 Python `3.12`。
- Node.js `>= 18`。
- 本机 Chrome、Chromium 或 Edge。自动探测失败时设置 `WEIBO_BROWSER_CHROME_PATH`。
- 后端固定使用 `127.0.0.1:8000`。
- 前端 Vite 默认使用 `127.0.0.1:5173`，并将 `/api`、`/health` 代理到 8000。
- 所有命令都从 `weibo-chat-collector` 项目根目录执行。

数据库、附件、导入和鉴权路径按项目根解析，不依赖 shell 当前目录或某台电脑的绝对路径：

```text
data/weibo_chat_collector.sqlite3
data/attachments/
data/imports/
data/auth/
```

## 启动后端

首次安装或 `browser-helper/package-lock.json` 更新后，先安装浏览器助手依赖。配置已禁止 Puppeteer 额外下载浏览器，运行时使用本机浏览器：

```bash
npm --prefix browser-helper install
```

macOS / Linux：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python -m uvicorn app.main:app --reload --app-dir backend --host 127.0.0.1 --port 8000
```

如果使用 `python3`，先确认版本不低于 3.10。

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
npm --prefix browser-helper install
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --app-dir .\backend --host 127.0.0.1 --port 8000
```

后端启动时会：

1. 初始化/迁移 SQLite schema。
2. 初始化 `collector_runtime_state` 单例。
3. 启动全局串行采集 worker。
4. 检查上一次进程遗留的运行任务；遗留任务会停止为 `process_interrupted`，不会自动恢复。

检查：

- 健康检查：<http://127.0.0.1:8000/health>
- API 文档：<http://127.0.0.1:8000/docs>

开发模式 `--reload` 会重启后端进程。不要把代码热重载当成任务恢复机制；运行中的 attempt 可能停止，需要在监控页人工续传。

## 启动前端

另开一个终端，仍从项目根目录执行：

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

打开 <http://127.0.0.1:5173>。

如果页面请求了错误端口，检查 Vite proxy 和网页快照脚本是否都仍指向 `http://127.0.0.1:8000`。

## 当前配置项

可在项目根目录的 `.env` 覆盖：

```text
APP_ENV=development
DATABASE_PATH=./data/weibo_chat_collector.sqlite3
ATTACHMENTS_DIR=./data/attachments
IMPORTS_DIR=./data/imports
WEIBO_API_AUTH_DIR=./data/auth
DEFAULT_COLLECTION_DAYS=7
DELETE_MODE=soft
WEIBO_API_TIMEZONE=Asia/Shanghai
WEIBO_API_PAGE_SIZE=20
WEIBO_API_MAX_PAGES=500
WEIBO_API_PAGE_DELAY_MIN_SECONDS=3
WEIBO_API_PAGE_DELAY_MAX_SECONDS=8
WEIBO_API_LONG_REST_EVERY_PAGES=20
WEIBO_API_LONG_REST_MIN_SECONDS=30
WEIBO_API_LONG_REST_MAX_SECONDS=60
WEIBO_API_REQUEST_TIMEOUT_SECONDS=30
WEIBO_BROWSER_HELPER_PATH=./browser-helper/src/login-session.js
WEIBO_BROWSER_HELPER_NODE_PATH=node
WEIBO_BROWSER_CHROME_PATH=
WEIBO_BROWSER_SESSION_TIMEOUT_SECONDS=600
```

普通请求等待 3–8 秒；全局每累计 20 个请求后，到下一请求的间隔使用 30–60 秒并替代普通等待。当前没有重试次数或退避配置，所有微博采集错误均为零自动重试。

浏览器会话超时允许 30–1800 秒，默认 600 秒。助手只由后端启动，使用独立临时用户目录；不要直接把助手 stdout 重定向到日志，因为该私有进程管道会短暂传递 Cookie 给后端保存。

## UI 联调顺序

### 1. 创建账号和群聊占位

先在“高级工具 / 数据导入”的“API 采集目标”中创建 collector 账号和属于该账号的群聊。首次绑定时微博群 ID 可以留空。

### 2. 扫码登录并发现群 ID

回到“采集任务”，选择账号和群聊后点击“扫码登录并发现群 ID”：

1. 后端启动独立可见 Chrome；使用当前选中的微博账号扫码。
2. 登录成功后 Cookie 直接由后端保存到 `data/auth/account-<id>.cookies.json`，不会返回页面。
3. 在同一个 Chrome 窗口中打开当前目标群聊。
4. 页面捕获到候选 ID 后核对账号和群聊，点击“确认绑定”。候选值未经确认不会写入数据库。
5. 选错群时点击“重新发现”，再打开正确群；要中止则点击“取消”。

同一时刻只允许一个浏览器登录会话。API 采集任务处于 `awaiting_confirmation` 或 `running` 时禁止启动登录会话。不要把 Cookie、Token、Authorization、完整请求头或密码放入调试记录。

### 3. 高级工具 / 数据导入

在“API 采集目标”区域创建或更新：

- collector 账号。
- 属于该账号的群聊。
- `chat_groups.source_group_id`。

JSON/CSV 和网页快照也位于该页面。它们是高级/备用导入工具，不占微博 API 队列；网页跨域快照因浏览器安全策略收紧，不作为主采集路径。

如果已有可信来源的 Puppeteer `cookies.json`，可展开“备用：导入已有 Cookie 文件”手动导入。该入口仅作兼容备用，不是首次配置的推荐流程。

### 4. 采集任务

在“采集任务”：

1. 选择已配置账号和群聊。
2. 确认 Cookie、群 ID 就绪；缺任一项时“启动采集”保持禁用。
3. 填写严格的开始和结束时间。
4. 创建任务。

创建成功只表示任务进入 `queued`，不表示已经访问微博。

### 5. 采集监控

监控页应看到：

```text
queued -> awaiting_confirmation -> running
```

轮到任务后先关闭微博 App 和所有微博网页，再点击“确认并启动”。监控页每 5 秒刷新活动任务，重点检查：

- `queue_position` 是否按全局 FIFO 递减。
- 状态是否只允许一个任务处于 `awaiting_confirmation` 或 `running`。
- `attempt_count` 是否在每次确认时增加。
- 成功页是否增加 `page_count`、更新 `checkpoint_oldest_at` 和 `next_max_mid`。
- 新增、重复、红包、粉丝群标识、失败等计数是否合理。
- 停止时 `stop_code`、`stop_reason`、HTTP/业务码和冷却时间是否明确。

## API 调试

### 查询任务

```bash
curl http://127.0.0.1:8000/api/collection-jobs
curl http://127.0.0.1:8000/api/collection-jobs/1
curl http://127.0.0.1:8000/api/collection-jobs/1/attempts
curl http://127.0.0.1:8000/api/collection-jobs/1/pages
```

### 创建 API 任务

```bash
curl -X POST http://127.0.0.1:8000/api/collection-jobs/weibo-api \
  -H 'Content-Type: application/json' \
  -d '{"account_id":1,"group_id":1,"range_start":"2026-08-01 08:00:00","range_end":"2026-08-01 09:00:00"}'
```

创建返回 `202`。相同目标与范围已有未完成任务时返回 `409`，应继续处理原任务。

### 确认、停止与续传

```bash
curl -X POST http://127.0.0.1:8000/api/collection-jobs/1/confirm
curl -X POST http://127.0.0.1:8000/api/collection-jobs/1/stop
curl -X POST http://127.0.0.1:8000/api/collection-jobs/1/resume
```

- confirm 只适用于 `awaiting_confirmation`。
- stop 对 `queued`/`awaiting_confirmation` 立即生效，对 `running` 在安全页边界生效。
- resume 只适用于 `stopped`；风险码冷却期内返回 `409`。
- API v2 不允许用通用 status patch 任意跳转状态。

## 断点与事务检查

成功页应同时出现以下变化：

1. `collection_job_pages` 新增一行。
2. `collection_jobs.page_count` 和各类累计计数增加。
3. 当前 `collection_job_attempts` 的对应计数增加。
4. job 的 `next_max_mid` 等于该 page 的 `next_max_mid`。

这些写入属于同一事务。若网络、解析或事务失败：

- 不新增 page。
- 不增加成功页计数。
- 不推进 `next_max_mid`。
- 当前任务进入 `stopped`，等待人工检查和续传。

续传后任务 ID 与累计 pages 不变；重新确认会新增 attempt，并从原 `next_max_mid` 继续。

## 安全停止检查

建议分别验证：

- `queued`：停止后不进入待确认。
- `awaiting_confirmation`：停止后不发微博请求，后续队列可继续。
- 节流等待中的 `running`：点击安全停止后不再发下一请求。
- 请求已发出的 `running`：成功页完整提交后停止。

不要以“按钮点击后必须瞬间变为 stopped”判断运行中停止失败；worker 需要到达安全页边界。

### 2026-08-25 实机验证记录

账号 A、群聊“汉语从句研究会”的任务 #7 已完成运行中安全停止与同任务续传验证：

1. 第一次 attempt 从 `max_mid=0` 启动，成功提交 4 页后执行安全停止。
2. 第一次 attempt 进入 `stopped/manual_stop`，失败数为 0，停止断点为 `5335811483501459`。
3. 点击“沿断点续传”后仍使用任务 #7；再次确认启动时 attempt 数从 1 增为 2。
4. 第二次 attempt 的 `start_max_mid` 为 `5335811483501459`，与第一次 attempt 的 `end_max_mid` 完全一致。
5. 第二次 attempt 继续完成 30 页；任务累计 34 页、看到 680 条、失败 0 条，最终正常完成。

这次结果证明真实运行中的已提交页、任务 ID、累计计数和游标都能跨人工停止保留。它不替代排队中停止、风险冷却、进程中断等其他分支的自动化测试。

## 附件原件验证（下一步）

先在“消息”页选择账号 A、目标群聊和已验证日期，分别筛选“图片”和“链接”。每类至少抽查 3 条，并记录：

- 消息 ID、发送时间和附件类型。
- `source_url`、文件名/标题、MIME 类型和当前 `download_status`。
- 微博页面可见内容是否与数据库记录一致。
- 原地址是否可直接访问、需要当前账号 Cookie、使用临时签名，或已经失效。

只记录非敏感结论，不要复制 Cookie、Token、Authorization 或完整请求头。验证前不要批量下载；先根据样例确定鉴权和 URL 生命周期，再决定附件下载实现。

当前数据库可直接用于首轮抽查的样例：

| 消息类型 | 消息 ID | 时间 | 附件 ID | 地址主机 | 当前状态 |
| --- | ---: | --- | ---: | --- | --- |
| 图片 | 486 | 2026-08-22 11:22:56 | 503 | `upload.api.weibo.com` | `pending`，无本地文件 |
| 图片 | 493 | 2026-08-22 11:24:28 | 504 | `upload.api.weibo.com` | `pending`，无本地文件 |
| 图片 | 496 | 2026-08-22 11:27:51 | 505 | `upload.api.weibo.com` | `pending`，无本地文件 |
| 链接 | 448 | 2026-08-22 16:08:14 | 496 | `weibo.com` | `pending`，无本地文件 |
| 链接 | 498 | 2026-08-22 11:30:13 | 506 | `t.cn` | `pending`，无本地文件 |
| 链接 | 500 | 2026-08-22 11:30:57 | 507 | `weibo.com` | `pending`，无本地文件 |

表中只记录主机名，不在文档保存完整 URL。完整地址只在本机消息详情中查看。

抽查完成后进入“微博验证”，选择账号 A 和目标群聊的验证记录，在“添加脱敏观察”中分别新增“图片”和“链接”观察。只填写消息 ID、附件 ID、主机名、能否打开、是否需要登录态和失效情况；路径中的查询参数、Cookie 和完整请求头必须省略。

## 错误与风险冷却检查

微博采集不自动重试。模拟超时、HTTP/业务错误、无效 JSON、分页异常或数据库错误时应检查：

- 任务和当前 attempt 均停止。
- `stop_code` 与 `stop_reason` 存在。
- 失败页没有推进断点。
- 服务重启后不会自行恢复。

`http_429`、`api_10023`、`api_10024` 会设置 60 分钟 `resume_not_before`。到期后仍须人工 resume、排队、confirm。

## 过滤检查

当前高置信度规则应满足：

- 红包不写入 `messages`，计入 `filtered_red_packet_count`。
- 粉丝群标识不写入 `messages`，计入 `filtered_system_notice_count`。
- 普通问候正常入库。

不要用宽泛关键字测试成“所有带红包字样或所有系统消息都删除”；实现刻意只过滤高置信度形状。

## 本地自动化测试

从项目根目录执行：

```bash
.venv/bin/python -m unittest discover -s backend/tests -p 'test_*.py' -v
npm --prefix browser-helper test
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Windows：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s .\backend\tests -p 'test_*.py' -v
npm --prefix browser-helper test
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

这些测试使用临时 SQLite、mock 响应或直接函数调用，不访问真实微博网络，也不覆盖真实附件下载和完整前端浏览器链路。

## 浏览器登录故障排查

- 提示“未找到可用浏览器”：确认已安装 Chrome/Chromium/Edge，或在 `.env` 中填写绝对路径 `WEIBO_BROWSER_CHROME_PATH` 后重启后端。
- 提示“浏览器助手依赖”或启动失败：重新运行 `npm --prefix browser-helper install`，并确认 `node --version` 不低于 18。
- 登录后仍显示 Cookie 未就绪：先保持扫码窗口打开，助手会持续等待可用于 `api.weibo.com` 的有效 `SUB`，不要提前关闭窗口；若最终超时，再取消会话并重新扫码。不要上传或粘贴 Cookie 到调试信息。
- 始终捕获不到群 ID：在助手打开的同一窗口中重新进入目标群聊，点击“重新发现”后再试。
- 页面提示已有登录会话：回到该会话继续、取消它，或等待默认 10 分钟超时。后端异常退出时会关闭子进程并清理临时用户目录。

## 真实账号验证边界

账号 A 已完成内置扫码、群 ID 绑定、真实时间段采集、同范围重复去重以及运行中安全停止/断点续传验证。任务 #5 还确认写入了 15 条图片和 7 条链接附件元数据。

仍需验证账号 B 的独立凭据与数据隔离，以及图片/链接原地址、文件、视频和附件原件下载。每次验证仍应选可人工核对的范围，并保留时间边界、发送人、正文、分页、重复和过滤计数。

`query_messages.json` 是内部 Web API，不是官方稳定接口。在实机验证前，不应声称：

- 该接口在其他账号、群聊或未来版本仍然稳定可用。
- 所有消息类型，尤其文件和视频字段，都已确认。
- 附件原件访问和下载已经验证。

当前 UI 交付以桌面端为主，移动端任务宽表与确认/停止/续传流程的完整验证延后。
