# 消息列表与搜索 API

2026-09-13 更新：消息与上下文改为旧到新，引用入口和附件展示去重，新增按账号读取图片的受控入口。最新契约与验证见 [消息展示修复](message-display-fixes.md)。

前端展示说明见 [消息列表改版](message-list-redesign.md)。2026-09-08 新增独立只读引用上下文端点，原消息列表的分页/筛选和删除接口契约保持不变；未新增引用源 ID 或附件二进制字段。

更新时间：2026-09-12

新增 `viewer` JSON 字符串筛选、列表 `link_previews` 白名单字段及 `/api/message-view/` 下的 facets/context/matches/export 只读端点。完整字段、参数限制和统计口径见 [阅读器 API 契约](message-reader.md#api-契约)。`viewer` 同时应用于原删除/恢复预览与执行接口；旧调用省略该参数保持兼容。

截图修订：context 支持 `direction=center|older|newer`，方向页最多 5 条且排除游标；错误范围 404、方向非法 422。对应 `message_type` 与 `viewer.media` 合并成一个媒体条件，冲突 422，不再重复相交；仅传标准类型的旧请求仍精确筛选。前端已取消丰富预览，只使用安全 URL；接口兼容字段不删除。细节见 [修订契约](reader-simplification.md#后端契约变化)。

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
- `viewer`：JSON 字符串，支持 members/hours/media/hide_noise，非法条件返回 422。
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

### 引用定位与上下文

引用定位另有只读端点：`GET /api/messages/{message_id}/quote-context?account_id=1&group_id=11&deleted_only=false`。路径 ID 是原引用消息；后端在同账号、同群、同删除状态中做完整文字匹配，唯一匹配返回目标和前后各最多 5 条上下文。无匹配/重复/格式不支持有明确状态，错误范围返回 404，非法参数返回 422。完整契约见 [引用上下文](quote-context.md)。它忽略日期等内容筛选，但不改变原列表和删除范围。

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

2026-10-21（TEST-005–006）：`hard-delete` 在写事务内先固定命中 ID，再按固定范围删除附件、索引、消息，失败整体回滚；附件筛选不因中途删除而丢失目标。返回的 `preview_count` 是执行事务内的匹配数，`deleted_count` 与该集合一致。请求字段和原件保留策略不变。前端三类批量操作的预览与确认绑定同一操作/视图/筛选快照，过期预览不可执行，详见 [回收站](recycle-bin.md)。

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
