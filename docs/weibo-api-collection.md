# 微博 API 采集指南

更新时间：2026-08-12

## 1. 方案定位

`weibo-chat-collector` 是主系统，负责多账号、群聊、任务队列、逐页采集、SQLite 落库、监控、检索和删除。

相邻的 `weibo-chat-auto` 只承担两项辅助工作：

1. 打开登录窗口，让每个微博账号扫码并生成自己的 `cookies.json`。
2. 打开目标群聊，从 `query_messages.json` 请求的查询参数中发现群 `id`。

auto 的归档、查看器、AI 分析结果和数据库不导入 collector；collector 的运行也不依赖 auto AI。

```text
账号扫码 -> cookies.json -> data/auth/account-<id>.cookies.json
目标群请求 -> query_messages?id=123 -> chat_groups.source_group_id
                                           |
用户指定闭区间时间范围 --------------------+
                                           v
                    collector 后台全局串行队列
                                           |
                         逐页请求、事务提交、断点
                                           v
                           SQLite / 消息检索与管理
```

## 2. 接口性质与使用边界

当前采集器访问的是从微博 Web 客户端行为中观察到的内部接口：

```text
https://api.weibo.com/webim/groupchat/query_messages.json
```

它不是微博公开、受支持或保证稳定的官方 API。地址、字段、Cookie 要求、分页、业务错误码和限流策略都可能不经通知改变。使用者必须：

- 只采集当前账号有权查看的数据。
- 遵守微博服务条款和适用法律。
- 不绕过账号权限或平台风控。
- 发生未知响应或错误时停止并检查，不能静默丢消息。

当前只有临时 SQLite 与模拟响应层面的自动化验证，尚未声称完成真实微博网络端到端验证，也尚未确认真实附件字段或附件原件下载。

## 3. 运行项目

要求 Python `>= 3.10`，推荐 Python `3.12`；前端需要 Node.js `>= 18`。所有命令从 `weibo-chat-collector` 根目录执行，路径都相对该根目录解析。

终端一启动后端，固定使用 8000：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python -m uvicorn app.main:app --reload --app-dir backend --host 127.0.0.1 --port 8000
```

终端二仍从项目根目录启动前端：

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

打开 <http://127.0.0.1:5173>。Vite 将 `/api` 和 `/health` 代理到 `http://127.0.0.1:8000`。

项目路径示例：

```text
data/weibo_chat_collector.sqlite3
data/auth/
data/attachments/
data/imports/
```

## 4. 用 auto 准备账号

以下步骤必须对每个微博账号分别执行。

### 4.1 扫码生成 Cookie

按 auto 自己的说明启动扫码流程，例如从 collector 根目录进入相邻项目：

```bash
cd ../weibo-chat-auto
npm run save-cookies
```

扫码成功后 auto 生成 `cookies.json`。多账号建议一次处理一个：账号 A 生成后先导入 collector 的账号 A，再为账号 B 扫码并导入账号 B。

`cookies.json` 等同于登录凭据。不要把文件内容、完整请求头、Cookie、Token 或 Authorization 放进聊天、Issue、日志、截图或 Git。

### 4.2 发现群 ID

在同一账号的登录会话中打开目标群聊，通过 Network 请求查找：

```text
query_messages.json
```

只记录查询参数中的 `id`，例如 `id=123456789`。这个值最终写入 `chat_groups.source_group_id`。应针对每个账号和每个目标群实际确认，不能仅凭群名猜测或复制另一账号的绑定。

## 5. UI 配置与启动

UI 已按职责拆分。

### 5.1 高级工具 / 数据导入

先在“高级工具 / 数据导入”的“API 采集目标”中：

1. 选择现有账号和群聊，或留空以新建。
2. 填写账号显示名称、群聊名称和纯数字微博群 ID。
3. 点击“保存账号与群 ID”。

同一账号下不能把两个本地群绑定到同一个 `source_group_id`。已有归档历史的群也不能随意改绑到另一个真实群，避免混库。

### 5.2 采集任务

“采集任务”只负责配置和创建一次 API 任务：

1. 选择已配置的账号与该账号下的群聊。
2. 为当前账号选择 auto 生成的 `cookies.json`。
3. 确认“Cookie 已就绪”和“群 ID 已绑定”。
4. 选择开始、结束时间。
5. 点击“启动采集”。

此按钮只调用 `POST /api/collection-jobs/weibo-api` 创建任务。接口返回 `202`，任务进入 `queued`，不会在当前 HTTP 请求里同步采集。

相同账号、群聊和时间范围已有未完成任务（`queued`、`awaiting_confirmation`、`running` 或 `stopped`）时，创建接口返回 `409`；应在监控页处理原任务，而不是复制一个新任务。

### 5.3 采集监控

所有账号共享一个后台队列和一个活动位：

```text
queued -> awaiting_confirmation -> running -> completed
                                 \-> stopped
```

1. 后台 worker 仅把队首任务提升为 `awaiting_confirmation`。
2. 待确认任务会阻塞后续队列。先关闭微博 App 和所有微博网页，再点击“确认并启动”。
3. 确认后任务进入 `running`，并创建一条新的 attempt 记录。
4. 监控页每 5 秒刷新活动状态，并显示队列位置、attempt 次数、累计页数、当前最早消息时间、`next_max_mid`、各类计数和停止原因。
5. `stopped` 任务可点击“沿断点续传”，重新进入 `queued`；再次轮到时仍需人工确认。

`awaiting_confirmation` 不是自动倒计时。没有人工确认就不会向微博发请求。

## 6. Cookie 存储规则

UI 可读取顶层 Puppeteer Cookie 数组，或包含 `cookies` 数组的对象。后端接口为：

```text
PUT /api/weibo-api/accounts/{account_id}/cookies
Content-Type: application/json

{"cookies": [Puppeteer Cookie 对象...]}
```

保存规则：

- 只保留微博/新浪相关域 Cookie。
- 必须存在可发送到 `api.weibo.com`、非空且未在客户端判定过期的 `SUB`，否则拒绝导入且不覆盖原文件。
- 每个账号保存为 `data/auth/account-<id>.cookies.json`。
- 先写临时文件再原子替换。
- POSIX 系统将目录设为 `0700`、文件设为 `0600`；Windows 继承项目目录 ACL。
- Cookie 值不写入 SQLite，不由状态或任务 API 返回，也不进入 Git。
- 数据库中的 `weibo_accounts.login_profile_name` 只记录不敏感的文件名。

`GET /api/weibo-api/status` 只报告文件存在性、可发送的 `SUB`、Cookie 数量和更新时间等非敏感信息。它不能证明微博服务端仍接受该登录态。

Cookie 失效后 collector 不会自动扫码。应回到 auto 重新扫码，为同一个 collector 账号重新导入，再对停止任务执行人工续传。

## 7. 时间范围与分页

创建请求示例：

```http
POST /api/collection-jobs/weibo-api
```

```json
{
  "account_id": 1,
  "group_id": 1,
  "range_start": "2026-08-01 08:00:00",
  "range_end": "2026-08-01 09:00:00"
}
```

- 无时区偏移的输入按 `WEIBO_API_TIMEZONE` 解释，默认 `Asia/Shanghai`。
- `range_start` 必须严格早于 `range_end`。
- 消息入选条件为闭区间：`range_start <= sent_at <= range_end`。
- 初始请求使用 `max_mid=0`，随后用已提交页返回的下一游标继续向旧消息翻页。
- 当页最旧消息恰好等于开始时间时仍继续翻页，以覆盖同一秒的边界消息；翻过开始边界或服务端返回历史终点后才完成。
- 页内顺序异常、分页向新消息移动、游标重复或缺失都会停止任务。
- 达到单次 `WEIBO_API_MAX_PAGES` 时进入 `stopped/page_limit`，保留原任务断点供下一次人工续传，不将不完整范围标为成功。

## 8. 逐页事务与续传

每次微博请求只获取一页。成功响应会在一个 SQLite 事务中完成：

- 严格时间范围过滤。
- 高置信度消息过滤与历史重复检查。
- 用户、群成员、消息和附件元数据写入。
- `collection_job_pages` 页记录写入。
- 任务和当前 attempt 的页数及各类计数累加。
- `checkpoint_oldest_at` 与 `next_max_mid` 更新。

只有整个事务提交成功，断点才前进。网络失败、响应错误或事务回滚都不会留下半页结果，也不会推进页数、计数或 `next_max_mid`。

人工续传不会新建任务：

1. 原 `stopped` 任务改回 `queued`。
2. 轮到后进入 `awaiting_confirmation`。
3. 用户再次确认时新增一条 `collection_job_attempts`。
4. 新 attempt 的 `start_max_mid` 取原任务已提交的 `next_max_mid`。

因此任务累计进度保留在 `collection_jobs`，每次运行的边界和结果保留在 attempts，逐页明细保留在 pages。

## 9. 节流、错误和冷却

默认节流是跨账号、跨任务共享的全局策略：

- 普通请求间随机等待 3–8 秒。
- 全局请求计数每累计 20 个请求后，到下一请求的间隔改为 30–60 秒。
- 这次长等待替代普通 3–8 秒等待，不是两段相加。
- `collector_runtime_state` 持久化全局请求计数和下一次允许请求时间。

当前微博采集请求对所有错误都是零重试。超时、HTTP 错误、微博业务错误、登录失效、群不可访问、JSON/字段异常、分页异常、数据库错误和未知本地错误都会停止当前 attempt：

- 状态变为 `stopped`，并记录 `stop_code`、`stop_reason`、HTTP 状态或业务错误码。
- 不会在后台自动重试。
- 不会静默跳过错误页。
- 不会自动创建新任务或在进程重启后恢复。

风险码 `http_429`、`api_10023`、`api_10024` 会设置 60 分钟冷却：

- 冷却期内 `POST /api/collection-jobs/{id}/resume` 返回 `409`。
- 60 分钟到期只代表允许人工续传；任务不会自己运行。
- 用户仍需点击“沿断点续传”，等待全局队列，再点击“确认并启动”。

后端进程中断时，遗留的运行任务会变为 `stopped/process_interrupted`，同样需要人工续传。

当前相关配置名和默认值：

```text
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

没有重试次数或重试退避配置。

## 10. 安全停止

`POST /api/collection-jobs/{id}/stop` 的行为按状态区分：

- `queued` 或 `awaiting_confirmation`：直接转为 `stopped/manual_stop`，不再发下一次微博请求。
- `running`：写入停止请求，worker 在安全页边界处理。
- 若停止发生在等待下一次请求期间，停止前不会再发请求。
- 若一页请求已经发出且成功返回，该页会完整事务提交，然后停止。
- 若该页请求或事务失败，页断点不推进，按错误原因停止。

安全停止不会丢弃一个已经成功取得且能够完整提交的页，也不会把失败页标记为已完成。

## 11. 消息过滤与入库

当前只做高置信度过滤：

- 红包消息：不写入 `messages`，计入 `filtered_red_packet_count`。
- 粉丝群标识：不写入 `messages`，计入 `filtered_system_notice_count`。

`filtered_system_notice_count` 是现有字段名，当前具体统计被过滤的粉丝群标识，不表示所有系统通知都被过滤。普通问候、普通文本和其他未命中高置信度规则的消息正常入库。

入库涉及：

- `weibo_accounts`、`chat_groups` 和 `chat_groups.source_group_id`。
- `chat_users`、`group_members`。
- `messages` 和可识别的 `attachments` URL/元数据。
- `collection_jobs`、`collection_job_attempts`、`collection_job_pages`。
- `collector_runtime_state` 的全局 worker 与节流状态。

当前采集器类型为 `weibo_api_v2`。

## 12. 任务操作接口

```text
POST /api/collection-jobs/weibo-api
GET  /api/collection-jobs
GET  /api/collection-jobs/{id}
GET  /api/collection-jobs/{id}/attempts
GET  /api/collection-jobs/{id}/pages
POST /api/collection-jobs/{id}/confirm
POST /api/collection-jobs/{id}/stop
POST /api/collection-jobs/{id}/resume
```

API v2 任务不能通过通用 `PATCH /api/collection-jobs/{id}/status` 任意改状态；必须使用 confirm、stop、resume 操作。

## 13. 高级工具与移动端范围

JSON/CSV 文件导入和网页快照位于“高级工具 / 数据导入”，都不占微博 API 队列。网页快照保留用于研究和显式导入，但因跨域和浏览器安全策略收紧，不再作为推荐采集主流程。

当前以桌面端为交付目标。移动端监控宽表、确认/停止/续传交互和完整浏览器验证延后。

## 14. 首次真实验证清单

首次实机试跑应只选很短且可人工核对的范围，并确认：

- 当前账号 Cookie 能否访问内部接口。
- 群 `id` 是否确实对应当前账号下的目标群。
- 时间戳、消息 ID、发送人和正文的真实字段。
- `max_mid` 的真实历史分页行为。
- 红包和粉丝群标识是否只命中高置信度样例，普通问候是否保留。
- 图片、文件、链接、视频的真实字段和鉴权要求。
- 安全停止、同任务续传和 attempt/page 记录是否符合预期。

在完成真实且脱敏的验证前，不应声称 API 已稳定可用，也不应声称附件文件字段或附件原件下载已验证。
