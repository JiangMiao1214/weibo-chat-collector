# 运行与调试指南

更新时间：2026-08-12

## 环境与路径

- Python `>= 3.10`，推荐 Python `3.12`。
- Node.js `>= 18`。
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
```

普通请求等待 3–8 秒；全局每累计 20 个请求后，到下一请求的间隔使用 30–60 秒并替代普通等待。当前没有重试次数或退避配置，所有微博采集错误均为零自动重试。

## UI 联调顺序

### 1. 准备账号和群 ID

`weibo-chat-auto` 只用于：

- 每个账号扫码生成 `cookies.json`。
- 从对应群的 `query_messages.json?id=...` 发现群 ID。

collector 不依赖 auto 的归档、查看器或 AI 分析。不要把 Cookie、Token、Authorization、完整请求头或密码放入调试记录。

### 2. 高级工具 / 数据导入

在“API 采集目标”区域创建或更新：

- collector 账号。
- 属于该账号的群聊。
- `chat_groups.source_group_id`。

JSON/CSV 和网页快照也位于该页面。它们是高级/备用导入工具，不占微博 API 队列；网页跨域快照因浏览器安全策略收紧，不作为主采集路径。

### 3. 采集任务

在“采集任务”：

1. 选择已配置账号和群聊。
2. 导入当前账号的 `cookies.json`。
3. 确认 Cookie、群 ID 就绪。
4. 填写严格的开始和结束时间。
5. 创建任务。

创建成功只表示任务进入 `queued`，不表示已经访问微博。

### 4. 采集监控

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
```

Windows：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s .\backend\tests -p 'test_*.py' -v
```

这些测试使用临时 SQLite、mock 响应或直接函数调用，不访问真实微博网络，也不覆盖真实附件下载和完整前端浏览器链路。

## 真实账号验证边界

首次真实试跑应选很短、可人工核对的范围，并验证时间边界、发送人、正文、分页、重复、两类高置信度过滤和普通问候。图片、文件、链接、视频的字段必须基于脱敏真实响应另行确认。

`query_messages.json` 是内部 Web API，不是官方稳定接口。在实机验证前，不应声称：

- 真实微博 API 已稳定可用。
- 所有消息类型字段都已确认。
- 附件文件字段或附件原件下载已验证。

当前 UI 交付以桌面端为主，移动端任务宽表与确认/停止/续传流程的完整验证延后。
