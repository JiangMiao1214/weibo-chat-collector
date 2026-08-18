# 消息列表与搜索 API

更新时间：2026-06-13

本页保留已经实现的消息列表、详情和筛选项接口说明；采集任务状态与运行方式请看 `collection-jobs.md`。

## 接口列表

### 健康检查

```text
GET /health
```

### 筛选项

```text
GET /api/filter-options
```

返回：

- 账号列表。
- 群聊列表。
- 用户列表。
- 已存在的消息类型。

### 消息列表

```text
GET /api/messages
```

支持参数：

- `account_id`
- `account`
- `group_id`
- `group`
- `user`
- `date_from`
- `date_to`
- `keyword`
- `message_type`
- `has_attachment`
- `include_deleted`
- `deleted_only`
- `limit`
- `offset`
- `before_sent_at`
- `before_id`

默认：

- `include_deleted=false`
- `limit=50`
- `offset=0`

示例：

```text
GET /api/messages?keyword=Sample&has_attachment=true
```

只看回收站中的已删除消息：

```text
GET /api/messages?include_deleted=true&deleted_only=true
```

大量消息建议使用游标分页：

```text
GET /api/messages?limit=100
GET /api/messages?limit=100&before_sent_at=2026-06-10%2022:00:00&before_id=123
```

返回中包含：

- `has_more`：是否还有更早消息。
- `next_cursor`：下一页游标。

`next_cursor` 示例：

```json
{
  "before_sent_at": "2026-06-10 22:00:00",
  "before_id": 123
}
```

### 消息详情

```text
GET /api/messages/{message_id}
```

返回：

- 消息基础字段。
- 账号名。
- 群聊名。
- 用户名。
- 原始 payload。
- 附件列表。

### 删除预览

```text
POST /api/messages/delete-preview
```

请求体：

```json
{
  "filters": {
    "account_id": 1,
    "group_id": 1,
    "keyword": "Sample",
    "date_from": "2026-06-10 00:00:00",
    "date_to": "2026-06-10 23:59:59",
    "message_type": "text",
    "has_attachment": null
  }
}
```

返回：

- `preview_count`
- `delete_mode`
- `filters`

### 执行软删除

```text
POST /api/messages/soft-delete
```

请求体：

```json
{
  "filters": {
    "keyword": "Sample"
  },
  "confirm": true,
  "delete_attachments": false,
  "created_by": "local_user"
}
```

说明：

- 当前只做软删除。
- 消息会设置 `is_deleted=1` 和 `deleted_at`。
- 附件原件不会删除。
- 删除任务会写入 `deletion_jobs`。

### 恢复预览

```text
POST /api/messages/restore-preview
```

只统计当前筛选条件下的已删除消息。

### 执行恢复

```text
POST /api/messages/restore
```

请求体：

```json
{
  "filters": {
    "keyword": "Sample"
  },
  "confirm": true,
  "created_by": "local_user"
}
```

恢复会执行：

```text
is_deleted = 0
deleted_at = NULL
```

### 彻底删除预览

```text
POST /api/messages/hard-delete-preview
```

只统计当前筛选条件下的已删除消息，并返回将被删除的附件记录数量。

### 执行彻底删除

```text
POST /api/messages/hard-delete
```

彻底删除只作用于回收站中的消息。当前会删除数据库中的附件记录、搜索索引记录和消息记录，不删除磁盘上的附件原件。

## 前端页面

前端已接入这些接口，默认请求：

```text
http://127.0.0.1:8000
```

页面能力：

- 展示消息列表。
- 展示消息详情。
- 按日期分组展示消息。
- 使用游标分页加载更早消息。
- 按账号筛选。
- 按群聊筛选。
- 按用户筛选。
- 按日期范围筛选。
- 按关键词搜索。
- 按消息类型筛选。
- 按是否有附件筛选。
- 预览当前搜索结果的软删除数量。
- 二次确认后批量软删除当前搜索结果。
- 切换回收站。
- 查看已删除消息。
- 预览并恢复已删除消息。
- 预览并彻底删除已删除消息。
