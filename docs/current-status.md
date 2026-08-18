# 当前状态与下一步

更新时间：2026-08-12

## 当前结论

项目主线已经确定为：collector 管理多账号、群聊、后台任务、SQLite 落库、监控、检索和删除；采集来源以微博 Web 客户端的内部 `query_messages.json` 接口为主。

`weibo-chat-auto` 只用于：

1. 每个账号扫码生成 `cookies.json`。
2. 在对应群聊的 `query_messages.json` 请求中发现群 `id`。

auto 的归档、查看器、AI 分析和数据库不进入 collector 主链路，collector 也不依赖 auto AI 才能采集或入库。

网页快照和 JSON/CSV 导入仍保留在“高级工具 / 数据导入”，但网页跨域快照受 CORS、Private Network Access 和页面安全策略限制，不再是推荐主流程。

## 已实现能力

### Collector 基础能力

- FastAPI 后端、React + TypeScript + Vite 前端和 SQLite 数据库。
- 多账号、多群聊隔离，并统一在一个本地数据库中管理。
- 消息列表、详情、关键词与多条件筛选、软删除、回收站恢复和彻底删除。
- JSON/CSV 文件导入与网页快照显式导入。
- 文字、图片、文件、链接、视频等消息和附件元数据的数据模型。

### 微博 API 目标与凭据

- `POST /api/weibo-api/targets` 创建或更新账号、群聊和微博群 ID 绑定。
- 群 ID 保存到 `chat_groups.source_group_id`，同一账号下不能把两个本地群绑定到相同源群 ID。
- `PUT /api/weibo-api/accounts/{account_id}/cookies` 导入 Puppeteer Cookie 数组。
- 每个账号的 Cookie 保存为 `data/auth/account-<id>.cookies.json`。
- POSIX 系统收紧为目录 `0700`、文件 `0600`；`data/auth/` 不进入 Git。
- Cookie 值不写入数据库，也不由状态或任务 API 返回。
- `GET /api/weibo-api/status` 只返回文件、可用域、非空且未在客户端判定过期的 `SUB`、数量和更新时间等非敏感状态；真实登录有效性仍由微博请求决定。

### 后台全局串行队列

`POST /api/collection-jobs/weibo-api` 返回 `202` 并创建 `weibo_api_v2` 任务，不同步执行采集。

所有账号共用一个后台 worker 和一个活动位：

```text
queued -> awaiting_confirmation -> running -> completed
                                 \-> stopped
```

- 队首任务先进入 `awaiting_confirmation`。用户确认已经关闭微博 App 和网页后，才进入 `running`。
- `awaiting_confirmation` 也占用活动位，因此后续任务继续保持 `queued`。
- 任务列表提供队列位置、attempt 次数、页数、最早时间检查点、`next_max_mid`、过滤/重复/失败计数和停止原因。
- 达到单次运行页数上限会进入 `stopped` 并保留断点，不会把尚未覆盖完整的范围标成完成。

### 逐页事务与断点续传

- 采集器从新消息向旧消息分页，严格只入库闭区间 `range_start <= sent_at <= range_end` 内的消息。
- 每次请求处理一页。该页的消息、去重与过滤计数、`collection_job_pages` 记录、任务/attempt 累计值和 `next_max_mid` 在同一事务中提交。
- 请求失败、响应解析失败或事务失败时，本页不会写入，也不会推进页数、计数或 `next_max_mid`。
- 人工续传复用同一个任务和已提交的 `next_max_mid`；任务重新进入 `queued`，再次轮到后需重新确认，确认时新增 `collection_job_attempts` 记录。
- `collection_job_pages` 记录每页请求与下一游标，`UNIQUE(job_id, request_max_mid)` 防止同一任务重复提交同一请求游标。
- `collector_runtime_state` 保存 worker 租约、当前任务、全局请求计数和下一次允许请求时间。

### 节流、错误与停止

- 普通请求间隔随机 3–8 秒。
- 全局每累计 20 个请求后，到下一请求的间隔使用 30–60 秒长等待，并替代普通等待，不叠加 3–8 秒。
- 所有微博请求和本地处理错误均为零自动重试：当前 attempt 立即进入 `stopped`，不静默忽略，也不自动恢复。
- HTTP 429、业务码 10023、10024 会设置 60 分钟 `resume_not_before`。冷却期内拒绝续传；到期后仍需人工续传、等待排队并再次确认。
- 进程中断时，遗留的 `running` 任务会被标为 `stopped/process_interrupted`，不会在服务重启后自行继续。
- 排队中或待确认任务可在发请求前停止；运行中安全停止在页边界生效。若一页请求已经发出，成功结果会完整提交后再停止。

### 高置信度消息过滤

- 高置信度红包消息不写入 `messages`，计入 `filtered_red_packet_count`。
- 高置信度粉丝群标识不写入 `messages`，当前计入 `filtered_system_notice_count`。
- 普通问候和其他未命中高置信度规则的内容正常入库，不做宽泛的“系统消息全过滤”。

### 前端信息架构

- “采集任务”：只负责选择已配置账号/群聊、导入该账号 Cookie、选择时间范围并创建任务。
- “采集监控”：每 5 秒刷新活动任务，展示队列、状态、页数、时间覆盖、断点、计数、原因，并提供确认启动、安全停止和沿断点续传。
- “高级工具 / 数据导入”：账号与群 ID 配置、JSON/CSV 导入、网页快照生成/预览/显式导入。

当前交付以桌面端为目标；移动端监控宽表、操作流程和完整浏览器验证延后。

## 验证状态与边界

本地自动化测试以临时 SQLite、模拟 API 响应和直接函数调用覆盖队列推进、确认、逐页提交、续传、节流、错误停止、风险冷却、安全停止、过滤和数据库迁移等实现行为。

这些测试不覆盖：

- 真实微博网络请求和账号端到端采集。
- 真实浏览器中的完整前端交互链路。
- 真实图片、文件、链接、视频等附件字段和附件下载。

`query_messages.json` 是内部 Web API，不是微博公开、受支持或承诺稳定的官方 API。当前不能声称它已实机稳定，也不能声称附件字段或附件原件下载已经验证。

## 运行方式

要求 Python `>= 3.10`，推荐 Python `3.12`。后端和前端均从 collector 项目根目录启动，后端固定使用 8000：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python -m uvicorn app.main:app --reload --app-dir backend --host 127.0.0.1 --port 8000
```

另一个终端：

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

前端 <http://127.0.0.1:5173> 的 `/api` 和 `/health` 代理到后端 `http://127.0.0.1:8000`。完整调试说明见 [run-and-debug.md](run-and-debug.md)。

## 下一步

建议只对当前账号有权访问的群聊做小范围、可人工核对的实机验证：

1. 在 auto 中扫码生成账号 A 的 Cookie，并立即导入 collector 的账号 A。
2. 用账号 A 打开目标群聊，只记录 `query_messages.json?id=...` 的群 ID，不保存完整请求头。
3. 创建很短时间范围的任务，观察 `queued -> awaiting_confirmation`，关闭微博 App/网页后人工确认。
4. 对照微博页面核验时间边界、发送人、正文、重复项、红包、粉丝群标识和普通问候。
5. 人工触发安全停止与续传，确认任务 ID 不变、attempt 增加且从原 `next_max_mid` 继续。
6. 用脱敏真实响应校准附件字段，再决定附件原件下载策略。
7. 对账号 B 重复独立扫码、群 ID 绑定和短范围验证，不复用 Cookie。

在这些实机结果完成前，不应在发布说明中写“真实微博 API 已稳定可用”或“附件文件字段已验证”。
