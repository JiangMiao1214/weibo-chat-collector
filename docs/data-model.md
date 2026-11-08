# 当前数据模型

2026-10-21：TEST-002–004 已实现：仅允许预览确认后删除无历史账号及空群/本机凭据；所有配置与导入入口限制一账号一群，旧多群原样保留并提示待整理；小时区仅在日期生效时显示，清日期同步清小时。没有更改表结构或自动迁移真实关联。范围和验收见 [测试问题记录](testing-issues.md)。

2026-10-12 更新：已新增连续上下文、有界补采集、统计/日志页及本地 OCR，新增 `activity_events`、OCR 队列/结果/索引表与补采集页数上限；备份恢复及管理停用会暂停 OCR。最新契约见 [F4–F7 使用说明](reader-insights-ocr.md)，下方此前记录保留历史语义。

2026-10-09：新增 `message_references`（消息外键、目标本地/源 ID、来源方法及时间）和 `message_reference_dismissals`（明确解除标记），启动兼容建表并回填标准化导入字段。账号/群管理复用既有资料和启用字段。详见 [永久引用](permanent-references.md) 与 [管理页](target-management.md)。

2026-09-13 本地归档更新：图片原文件、微博正文快照、直链视频和文件现可按群排队保存，并优先离线打开。使用步骤、格式限制和验证记录见 [本地归档](local-archives.md)。

更新时间：2026-08-12

SQLite schema 位于 `scripts/schema.sql`。后端启动时会初始化新表，并为旧数据库补齐 API v2 任务字段。

## 账号与群聊

### weibo_accounts

- `id`
- `display_name`
- `weibo_uid`
- `login_profile_name`
- `auth_type`
- `is_active`
- `notes`
- `created_at`
- `updated_at`

`login_profile_name` 只记录不敏感的 Cookie 文件名。Cookie 内容不写入数据库，而是按账号保存到 `data/auth/account-<id>.cookies.json`；POSIX 文件权限为 `0600`，该目录不进入 Git，也不由 API 返回 Cookie 值。

### chat_groups

- `id`
- `account_id`
- `name`
- `source_group_id`
- `description`
- `is_active`
- `created_at`
- `updated_at`

`source_group_id` 保存对应账号打开目标群聊时，`query_messages.json` 请求中的群 `id`。同名群在不同账号下仍是不同来源，必须通过 `account_id` 区分。

### chat_users

- `id`
- `display_name`
- `source_user_id`
- `alias`
- `avatar_url`
- `notes`
- `created_at`
- `updated_at`

用户身份识别（2026-10-21，TEST-007）：已知 `source_user_id` 只能命中同 UID；不会仅凭昵称复用无 UID 用户。无 UID 历史保持原 `user_id`，不同 UID 同名消息在后备去重中互不合并。同 UID 改昵称继续复用用户，较旧消息不覆盖较新昵称。新导入缺失/null/空字符串 UID 统一按无 UID 处理；没有 schema 迁移或真实历史自动合并。

### group_members

- `id`
- `group_id`
- `user_id`
- `display_name_in_group`
- `first_seen_at`
- `last_seen_at`
- `is_active`

`(group_id, user_id)` 唯一。

## API v2 任务持久化

### collection_jobs

一条记录代表用户指定账号、群聊和时间范围的一项逻辑任务。停止后续传仍使用同一条任务记录。

身份与范围：

- `id`
- `account_id`
- `group_id`
- `source_group_id`
- `range_start`
- `range_end`
- `timezone_name`
- `page_size`
- `collector_type`

状态与断点：

- `status`
- `next_max_mid`
- `checkpoint_oldest_at`
- `started_at`
- `finished_at`
- `confirmed_at`
- `last_progress_at`
- `heartbeat_at`
- `stop_requested_at`
- `resume_not_before`

累计计数：

- `page_count`
- `attempt_count`
- `total_seen_count`
- `inserted_count`
- `skipped_count`
- `duplicate_count`
- `filtered_red_packet_count`
- `filtered_system_notice_count`
- `failed_count`

停止与错误：

- `stop_code`
- `stop_reason`
- `last_http_status`
- `last_error_code`
- `error_message`
- `created_at`
- `updated_at`

当前主采集器类型是 `weibo_api_v2`，主状态流为：

```text
queued -> awaiting_confirmation -> running -> completed
                                 \-> stopped
```

`pending`、`failed`、`cancelled` 仍保留给早期/兼容任务。当前 API v2 运行错误写为 `stopped`。

`filtered_system_notice_count` 是兼容字段名；当前 API v2 用它统计被高置信度规则过滤的粉丝群标识，不代表所有系统消息。

### collection_job_attempts

一次用户确认启动对应一个 attempt。沿断点续传会增加 attempt，不会新建 collection job。

- `id`
- `job_id`
- `attempt_no`
- `status`
- `start_max_mid`
- `end_max_mid`
- `started_at`
- `finished_at`
- `page_count`
- `total_seen_count`
- `inserted_count`
- `skipped_count`
- `duplicate_count`
- `filtered_red_packet_count`
- `filtered_system_notice_count`
- `failed_count`
- `stop_code`
- `stop_reason`
- `last_http_status`
- `last_error_code`
- `created_at`

`(job_id, attempt_no)` 唯一。新 attempt 的 `start_max_mid` 取任务当前已提交的 `next_max_mid`。

### collection_job_pages

一条记录代表一个成功事务提交的 API 页：

- `id`
- `job_id`
- `attempt_id`
- `job_page_no`
- `attempt_page_no`
- `request_max_mid`
- `next_max_mid`
- `newest_sent_at`
- `oldest_sent_at`
- `raw_count`
- `in_range_count`
- `inserted_count`
- `skipped_count`
- `duplicate_count`
- `filtered_red_packet_count`
- `filtered_system_notice_count`
- `outside_range_count`
- `fetched_at`
- `committed_at`

`(job_id, request_max_mid)` 唯一。失败请求或回滚事务不会产生 page 记录，也不会更新 job/attempt 计数或任务 `next_max_mid`。

同一事务内提交该页的消息、附件元数据、page 记录、job 累计值、attempt 累计值和下一断点，从而避免半页成功。

### collector_runtime_state

全局 worker 单例状态，固定使用 `id = 1`：

- `id`
- `worker_id`
- `lease_expires_at`
- `current_job_id`
- `global_request_count`
- `next_allowed_request_at`
- `last_request_at`
- `updated_at`

它支撑跨账号的全局串行执行和持久化节流。普通请求间隔为 3–8 秒；全局每累计 20 个请求后，到下一请求的间隔使用 30–60 秒长等待并替代普通等待。

worker 租约过期或进程中断后，不会自动恢复原运行任务；启动检查将其标为 `stopped/process_interrupted`，等待人工续传。

## 消息与附件

### messages

- `id`
- `account_id`
- `group_id`
- `user_id`
- `source_message_id`
- `sent_at`
- `message_type`
- `content_text`
- `normalized_text`
- `raw_payload`
- `content_hash`
- `collection_job_id`
- `is_red_packet`
- `is_system_message`
- `is_deleted`
- `deleted_at`
- `created_at`
- `updated_at`

主要索引覆盖账号/群聊/时间、用户、类型、软删除状态、源消息 ID 和内容哈希。优先用 `account_id + group_id + source_message_id` 判重；没有稳定消息 ID 时使用账号、群聊、用户、时间和内容哈希。

高置信度红包和粉丝群标识在写入 messages 前被过滤。普通问候和其他未命中高置信度规则的内容正常写入。

### attachments

- `id`
- `message_id`
- `attachment_type`
- `source_url`
- `local_path`
- `file_name`
- `mime_type`
- `file_size`
- `content_hash`
- `title`
- `description`
- `download_status`
- `downloaded_at`
- `created_at`

`attachment_type` 可为 `image`、`file`、`link`、`video`、`audio` 或 `unknown`。当前 API 采集主要保存可识别的 URL 和元数据，不能据此声称真实附件字段或附件原件下载已验证。

## 高级工具与观察记录

### import_batches

JSON/CSV 导入批次：

- `id`
- `account_id`
- `group_id`
- `source_type`
- `source_file`
- `imported_count`
- `skipped_count`
- `status`
- `created_at`

### browser_page_captures

网页快照：

- `id`
- `account_id`
- `group_id`
- `range_start`
- `range_end`
- `page_url`
- `page_title`
- `captured_at`
- `visible_text`
- `blocks_json`
- `script_version`
- `status`
- `notes`
- `created_at`

网页快照位于“高级工具 / 数据导入”，可预览后显式导入。由于跨域与浏览器安全策略收紧，它不是 API 主流程。

### weibo_verification_reports

记录账号/群聊的脱敏实机验证结论，不保存 Cookie、Token、Authorization 或密码。

### weibo_interface_observations

保存脱敏后的接口路径、字段结构、分页形状和样例。所有样例必须先移除鉴权信息和用户隐私字段。

## 删除与检索辅助

### deletion_jobs

记录批量删除预览、过滤条件、软删除/附件策略、结果和状态。

### search_indexes

可选的消息检索索引表；当前 schema 保留其基础结构。
