# 当前状态与下一步

更新时间：2026-08-25

## 当前结论

项目主线已经确定为：collector 管理多账号、群聊、后台任务、SQLite 落库、监控、检索和删除；采集来源以微博 Web 客户端的内部 `query_messages.json` 接口为主。

collector 已内置账号扫码登录和群 ID 发现：后端启动隔离的可见 Chrome，会话内保存当前账号 Cookie，并只从精确匹配的 `query_messages.json?id=纯数字` 请求产生候选群 ID；候选值必须由用户确认后才能绑定。相邻 `weibo-chat-auto` 不再是主流程依赖，其 `cookies.json` 仅保留为手动导入备用。

网页快照和 JSON/CSV 导入仍保留在“高级工具 / 数据导入”，但网页跨域快照受 CORS、Private Network Access 和页面安全策略限制，不再是推荐主流程。

## 已实现能力

### Collector 基础能力

- FastAPI 后端、React + TypeScript + Vite 前端和 SQLite 数据库。
- 多账号、多群聊隔离，并统一在一个本地数据库中管理。
- 消息列表、详情、关键词与多条件筛选、软删除、回收站恢复和彻底删除。
- JSON/CSV 文件导入与网页快照显式导入。
- 文字、图片、文件、链接、视频等消息和附件元数据的数据模型。

### 微博 API 目标与凭据

- `POST /api/weibo-api/browser-sessions` 启动后端管理的单例浏览器登录会话；状态轮询、重新发现、确认绑定和取消均不返回 Cookie。
- 会话使用独立临时 Chrome 用户目录，默认 10 分钟超时，并在取消、完成、超时或后端退出后清理。
- 浏览器会话与 `awaiting_confirmation`/`running` 的 API 采集任务互斥；会话接口只接受 loopback 客户端。
- Cookie 就绪后立即保存到当前账号；群 ID 仅以候选状态展示，点击确认后才写入对应群聊。
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

- “采集任务”：选择账号/群聊后可扫码登录、观察群 ID 候选并确认绑定；Cookie 和群 ID 未同时就绪时禁止创建采集任务。
- “采集监控”：每 5 秒刷新活动任务，展示队列、状态、页数、时间覆盖、断点、计数、原因，并提供确认启动、安全停止和沿断点续传。
- “高级工具 / 数据导入”：账号与群 ID 配置、已有 Cookie 文件备用导入、JSON/CSV 导入、网页快照生成/预览/显式导入。
- “消息”：消息类型筛选固定提供文本、图片、链接、文件、视频和系统类型；列表顶部按当前筛选条件展示含附件消息数、附件总数及各附件类型数量。

当前交付以桌面端为目标；移动端监控宽表、操作流程和完整浏览器验证延后。

## 验证状态与边界

本地自动化测试以临时 SQLite、模拟 API 响应和直接函数调用覆盖队列推进、确认、逐页提交、续传、节流、错误停止、风险冷却、安全停止、过滤、数据库迁移，以及浏览器会话单例、取消幂等、超时、候选发现/确认和敏感字段不外泄。浏览器助手的 URL 提取协议有独立 Node 测试；前端已完成类型检查、构建和本地页面只读布局检查。

截至 2026-08-25，账号 A“微博账号A”与群聊“汉语从句研究会”已完成以下真实验证：

- 内置 Chrome 扫码、Cookie 本机保存、候选群 ID 发现、人工确认绑定和使用该凭据创建 API 任务的完整链路可用。
- 任务 #5 在 `2026-08-22 10:15:00` 至 `2026-08-23 10:15:00` 范围内完成 9 页：看到 180 条、入库 163 条、过滤粉丝群标识 2 条、失败 0 条。
- 任务 #5 入库消息包括文本 146 条、图片 10 条、链接 7 条；共写入附件元数据 22 条，其中图片 15 条、链接 7 条。这里验证的是元数据映射和统计，不代表附件原件已下载。
- 任务 #6 对相同范围再次采集，识别历史重复 163 条、新增 0 条，验证了重复采集不会重复入库。
- 任务 #7 第一次 attempt 在 4 页后安全停止为 `manual_stop`，停止断点为 `5335811483501459`；第二次 attempt 的 `start_max_mid` 与该断点完全一致，沿同一任务续传 30 页后完成。任务 ID 未改变，累计 34 页、失败 0 条。

仍未覆盖或尚不能外推的范围：

- 账号 B 的独立扫码、Cookie 隔离、群 ID 绑定和端到端采集。
- 真实文件、视频消息，以及图片/链接原始地址的长期有效性、鉴权要求和附件原件下载。
- 内置浏览器取消、10 分钟超时、手动关闭 Chrome 和后端重启后的实机清理行为。
- 更长时间范围、多账号轮换及微博接口变化后的长期稳定性。

`query_messages.json` 是内部 Web API，不是微博公开、受支持或承诺稳定的官方 API。当前不能声称它已长期稳定，也不能声称全部附件字段或附件原件下载已经验证。

## 运行方式

要求 Python `>= 3.10`，推荐 Python `3.12`。后端和前端均从 collector 项目根目录启动，后端固定使用 8000：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
npm --prefix browser-helper install
.venv/bin/python -m uvicorn app.main:app --reload --app-dir backend --host 127.0.0.1 --port 8000
```

另一个终端：

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

前端 <http://127.0.0.1:5173> 的 `/api` 和 `/health` 代理到后端 `http://127.0.0.1:8000`。完整调试说明见 [run-and-debug.md](run-and-debug.md)。

## 下一步

下一步优先验证附件原件，而不是继续扩大采集范围：

1. 在“消息”页选择账号 A、目标群聊和已验证日期，分别筛选图片与链接消息。
2. 从图片和链接消息中各抽查至少 3 条；记录消息 ID、附件类型、`source_url`、文件名/标题、下载状态，并与微博页面逐条核对。
3. 只在当前登录态和用户有权访问的前提下验证 URL 是否可打开，区分公开 URL、需要 Cookie 的 URL、临时签名 URL和已失效 URL；文档中不得记录 Cookie、Token 或完整请求头。
4. 根据结果确定附件策略：仅保留元数据、前端受控打开原地址，或由后端携带当前账号 Cookie 下载到 `data/attachments/`。
5. 完成策略后再实现“附件预览或下载入口”，补充下载成功、鉴权失败、URL 过期、重复文件和本地路径测试。
6. 随后对账号 B 重复独立扫码、群 ID 绑定和短范围采集，确认不复用账号 A 的 Cookie 或临时浏览器目录。

账号 A 的一次性真实链路已经通过，但在账号 B、附件原件和长期运行结果完成前，仍不应在发布说明中写“真实微博 API 已稳定可用”或“附件原件下载已验证”。
