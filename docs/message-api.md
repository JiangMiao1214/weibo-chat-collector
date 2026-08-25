# 消息列表与搜索 API

更新时间：2026-08-25

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
- 标准消息类型 `text`、`image`、`link`、`file`、`video`、`system`，以及数据库中出现的其他兼容类型。

标准类型始终返回，因此页面在采集链接消息之前或之后加载，都不会缺少“链接”筛选项。重新进入消息页或点击页面“刷新”时会同时刷新筛选项和消息列表。

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
- `attachment_summary`：当前完整筛选结果的附件汇总，不只统计当前分页已经加载的消息。

`attachment_summary` 示例：

```json
{
  "total_count": 22,
  "message_count": 17,
  "by_type": {
    "image": 15,
    "link": 7
  }
}
```

- `total_count`：附件记录总数。一条消息有多张图片时会大于消息数。
- `message_count`：至少包含一条附件的消息数。
- `by_type`：按附件类型统计数量。

该统计遵守账号、群聊、用户、日期、关键词、消息类型、是否有附件和删除状态等当前筛选条件。

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
- 在消息列表顶部展示当前筛选结果的含附件消息数、附件总数和各附件类型数量。
- 预览当前搜索结果的软删除数量。
- 二次确认后批量软删除当前搜索结果。
- 切换回收站。
- 查看已删除消息。
- 预览并恢复已删除消息。
- 预览并彻底删除已删除消息。

2026-08-25 账号 A 的真实任务 #5 验证得到：文本 146 条、图片消息 10 条、链接消息 7 条；17 条消息共关联 22 条附件元数据，其中图片 15 条、链接 7 条。该结果验证了消息类型和附件统计，不代表附件原件已下载。
