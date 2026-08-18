# 采集任务、队列与监控

更新时间：2026-08-12

## 适用范围

本文说明当前 `weibo_api_v2` 后台任务。JSON/CSV 文件导入、网页快照导入和早期占位任务仍使用兼容接口，但不属于微博 API 全局队列。

## 创建任务

```text
POST /api/collection-jobs/weibo-api
```

```json
{
  "account_id": 1,
  "group_id": 1,
  "range_start": "2026-08-01 00:00:00",
  "range_end": "2026-08-01 23:59:59"
}
```

创建接口会验证账号、群聊归属、`chat_groups.source_group_id`、Cookie 和严格时间范围，随后返回 `202` 与新任务。它不在当前 HTTP 请求中同步访问微博。

同一账号、群聊和时间范围已有 `queued`、`awaiting_confirmation`、`running` 或 `stopped` 任务时返回 `409`，避免创建两个未完成副本。

## 全局串行队列

所有账号共用一个持久化后台 worker，不存在“每账号各跑一个”的并发模式。任意时刻最多只有一个 API v2 任务处于 `awaiting_confirmation` 或 `running`。

```text
queued -> awaiting_confirmation -> running -> completed
                                 \-> stopped
```

| 状态 | 含义 | 可用操作 |
| --- | --- | --- |
| `queued` | 已进入全局 FIFO 队列，尚未占到活动位 | 移出队列/停止 |
| `awaiting_confirmation` | 已到队首，等待用户确认关闭微博 App 和网页 | 确认并启动，或停止等待 |
| `running` | worker 正在按页处理 | 安全停止 |
| `stopped` | 人工停止、错误、进程中断或单次页数上限；保留已提交断点 | 沿断点人工续传 |
| `completed` | 已翻过开始边界或到达历史终点，目标范围覆盖完成 | 无 |

`awaiting_confirmation` 会占用唯一活动位；在用户确认或停止前，后续任务不会越过它。确认后会创建一个新的 attempt，并将任务转为 `running`。

数据库仍保留 `pending`、`failed`、`cancelled` 等兼容状态供旧任务类型使用。当前 API v2 的运行错误统一进入 `stopped`，不能把旧的 `failed` 语义套用到新 worker。

## 逐页事务

worker 每次只请求一页，成功页在一个数据库事务中同时提交：

- 本页通过时间范围与高置信度过滤后的消息和附件元数据。
- 重复、红包、粉丝群标识、范围外等计数。
- 一条 `collection_job_pages` 记录。
- `collection_jobs` 的累计页数、计数、`checkpoint_oldest_at` 和 `next_max_mid`。
- 当前 `collection_job_attempts` 的页数、计数和 `end_max_mid`。

只有整个事务提交成功才推进断点。请求错误、响应解析错误或事务失败都不会留下半页数据，也不会推进该页的计数、页号或 `next_max_mid`。

`collection_job_pages` 对 `(job_id, request_max_mid)` 建有唯一约束，worker 也会在发请求前检查该游标是否已经提交，防止同一任务重复提交相同页。

## 同任务断点续传

```text
POST /api/collection-jobs/{job_id}/resume
```

续传只接受当前 `stopped` 的 API v2 任务。操作过程：

1. 原任务 ID 不变，状态改回 `queued`。
2. 原任务累计计数、已提交 pages 和 `next_max_mid` 保留。
3. 轮到该任务后进入 `awaiting_confirmation`。
4. 用户再次确认时 `attempt_count + 1`，新 attempt 的 `start_max_mid` 使用任务当前 `next_max_mid`。
5. worker 从这个断点重新发下一页请求。

恢复不是自动重试，也不是创建新任务。每一次人工确认的运行区间都独立记录在 `collection_job_attempts` 中。

## 请求节流

节流按全局请求计数执行，对所有账号、任务和 attempts 共享：

- 普通请求之间随机等待 3–8 秒。
- 全局每累计 20 个请求后，到下一请求的间隔使用 30–60 秒长等待。
- 长等待替代该次普通 3–8 秒等待，不叠加。
- `collector_runtime_state.global_request_count`、`next_allowed_request_at` 和 `last_request_at` 让该节流状态跨任务持久化。

这里按实际微博 API 请求计数；正常情况下一个请求对应一页。

## 零重试与停止原因

当前微博采集对所有错误执行零自动重试。一次请求或本地处理失败后：

- 当前 attempt 立即变为 `stopped`。
- 任务保存 `stop_code`、`stop_reason`、`last_http_status`、`last_error_code` 和 `error_message`。
- 失败页不写入 pages，不推进 `next_max_mid`，也不增加成功页计数。
- 后台不会静默跳页、自动恢复或自动新建任务。

常见停止码包括 Cookie 无效、微博鉴权/群错误、HTTP 错误、超时、网络错误、无效响应、分页不完整、数据库错误、进程中断、人工停止和页数上限。

后端启动时如果发现前一次进程遗留的 `running` 任务，会将其停止为 `process_interrupted`；用户检查后再人工续传。

### 风险冷却

以下停止码设置 60 分钟 `resume_not_before`：

- `http_429`
- `api_10023`
- `api_10024`

冷却期内 resume 返回 `409`。到期后也不会自动运行，仍须用户点击续传、等待队列并再次确认。

## 安全停止

```text
POST /api/collection-jobs/{job_id}/stop
```

- `queued`、`awaiting_confirmation`：直接改为 `stopped/manual_stop`，不会发下一请求。
- `running`：只先写入 `stop_requested_at`，由 worker 在安全页边界停止。
- 正在等待节流窗口时收到停止请求，不会再发下一次请求。
- 请求已经发出时，若该页成功，则整页事务提交后停止；若失败，则按错误停止且断点不推进。

因此监控页刚点击“安全停止”时可能短暂仍显示 `running`，直到当前页到达安全边界。

## 页数上限

`WEIBO_API_MAX_PAGES` 是单次 attempt 的页数上限。达到上限且尚未覆盖开始边界时：

- 任务进入 `stopped/page_limit`。
- 已成功提交的页和 `next_max_mid` 保留。
- 不把部分范围标成 `completed`。
- 用户可沿同一断点人工续传，产生新的 attempt。

## 过滤与计数语义

API v2 每页执行高置信度过滤：

- 红包：不写入 `messages`，增加 `filtered_red_packet_count` 和 `skipped_count`。
- 粉丝群标识：不写入 `messages`，增加 `filtered_system_notice_count` 和 `skipped_count`。
- 历史重复：不再次写入，增加 `duplicate_count` 和 `skipped_count`。
- 普通问候或未命中高置信度规则的内容：正常写入。

`filtered_system_notice_count` 当前只代表被规则识别的粉丝群标识，不代表全部系统通知。

## 监控字段

“采集监控”每 5 秒刷新活动任务，主要字段包括：

| 区域 | 字段 |
| --- | --- |
| 身份 | 任务 ID、账号、群聊、时间范围、`collector_type` |
| 队列 | `status`、`queue_position`、`confirmed_at` |
| 进度 | `page_count`、`checkpoint_oldest_at`、`next_max_mid`、`last_progress_at` |
| 运行次数 | `attempt_count` |
| 计数 | `total_seen_count`、`inserted_count`、`skipped_count`、`duplicate_count`、两类过滤计数、`failed_count` |
| 停止信息 | `stop_code`、`stop_reason`、`last_http_status`、`last_error_code`、`resume_not_before` |

任务详情、attempts 和 pages API 可查看 UI 未展开的完整字段。

## 后端接口

### 查询

```text
GET /api/collection-jobs
GET /api/collection-jobs/{job_id}
GET /api/collection-jobs/{job_id}/attempts
GET /api/collection-jobs/{job_id}/pages
```

列表支持 `account_id`、`group_id`、`status`、`limit` 和 `offset`。pages 支持 `limit` 和 `offset`。

### 状态操作

```text
POST /api/collection-jobs/{job_id}/confirm
POST /api/collection-jobs/{job_id}/stop
POST /api/collection-jobs/{job_id}/resume
```

- confirm 只接受 `awaiting_confirmation` 的 API v2 任务，并再次检查 Cookie。
- stop 只接受当前可停止的 API v2 状态。
- resume 只接受 `stopped`，并检查风险冷却。
- API v2 不允许通过通用 `PATCH /api/collection-jobs/{job_id}/status` 任意改状态。

旧的 `POST /api/collection-jobs`、状态 patch 和单群聊文件接口仅用于兼容/高级工具，不驱动 API v2 worker。

## 前端入口

- “采集任务”：选择目标、导入当前账号 Cookie、设置时间范围并创建任务。
- “采集监控”：筛选与查看队列，执行确认、安全停止和沿断点续传。
- “高级工具 / 数据导入”：账号与群 ID 配置、JSON/CSV 导入、网页快照。

当前任务监控按桌面端宽表设计，移动端信息布局和完整交互验证延后。
