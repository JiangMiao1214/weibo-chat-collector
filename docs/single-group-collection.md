# 单群聊文件采集

更新时间：2026-08-12

## 功能目标

本页说明保留的单群聊 JSON/CSV 导入闭环。

把整理好的 JSON 或 CSV 放入 `data/imports`，然后在前端“高级工具 / 数据导入”中选择账号、群聊、时间段和文件，系统会把对应时间段内的消息导入数据库。

文件导入本身不登录微博，也不读取 Cookie、Token 或 Authorization。当前项目另有独立的微博 API 任务主链路；文件导入作为离线兜底保留，并且不占 API 全局队列。

## 已实现能力

- 列出 `data/imports` 下的 JSON/CSV 文件。
- 前端在“高级工具 / 数据导入”视图中选择采集文件。
- 选择一个账号和一个群聊。
- 选择开始时间和结束时间。
- 执行单群聊文件采集。
- 采集时按时间段过滤消息。
- 自动过滤红包消息。
- 自动识别重复消息并跳过。
- 自动维护用户和群成员。
- 自动写入消息和附件记录。
- 对本地附件路径执行原件复制。
- 自动生成并完成一条 `collection_jobs` 记录。

## 后端接口

### 文件列表

```text
GET /api/import-files
```

返回 `data/imports` 下可采集的 JSON/CSV 文件。

### 执行单群聊文件采集

```text
POST /api/collection-jobs/single-group-file
```

请求体：

```json
{
  "account_id": 1,
  "group_id": 1,
  "range_start": "2026-06-08 00:00:00",
  "range_end": "2026-06-08 23:59:59",
  "source_file": "single-group-sample.json",
  "copy_local_attachments": true
}
```

返回内容包含：

- `summary.source_total_count`：文件原始消息数。
- `summary.total_count`：落在时间段内的消息数。
- `summary.inserted_count`：新增入库消息数。
- `summary.skipped_count`：跳过消息数。
- `summary.red_packet_count`：红包过滤数。
- `summary.filtered_system_notice_count`：粉丝群标识过滤数。
- `summary.duplicate_count`：重复消息数。
- `summary.attachment_count`：附件记录数。
- `summary.out_of_range_count`：时间段外消息数。
- `summary.invalid_count`：无效消息数。
- `job`：采集任务详情。

## 前端入口

前端顶部进入：

```text
高级工具 / 数据导入
```

文件采集表单包含：

- 账号。
- 群聊。
- 开始时间。
- 结束时间。
- 采集文件。
- 创建空任务。
- 执行文件采集。

## 文件格式

JSON 文件推荐格式：

```json
{
  "account": "account_a",
  "group": "group_a",
  "messages": [
    {
      "source_message_id": "msg-001",
      "source_user_id": "user-001",
      "sender_name": "示例用户",
      "sent_at": "2026-06-08 09:12:00",
      "message_type": "text",
      "content_text": "示例消息",
      "attachments": []
    }
  ]
}
```

说明：

- 前端选择的账号和群聊优先，文件里的 `account`、`group` 只作为阅读提示。
- `sent_at` 必须能解析成日期时间。
- `message_type` 为 `red_packet`、`hongbao`、`weibo_red_packet` 时会跳过。
- 原始字段或模板高置信命中红包、最佳手气、粉丝群“今日获得标识”时也会跳过；普通问候不会过滤。
- 图片、文件、链接放在 `attachments` 数组里。
- 如果附件包含可访问的本地 `local_path`，系统会复制到 `data/attachments`。
- 如果只有 `source_url`，当前先记录链接和下载状态，不自动联网下载。

## 样例文件

已新增样例：

```text
data/imports/single-group-sample.json
```

该样例包含：

- 2 条可入库消息。
- 1 条红包消息。
- 1 条图片附件记录。

## 验证结果

已在临时数据库验证：

- 文件原始消息数：3。
- 时间段内消息数：3。
- 入库消息数：2。
- 红包过滤数：1。
- 附件记录数：1。
- 采集任务状态：`completed`。

后端编译和前端构建均已通过。

## 与 API 采集的关系

新历史采集优先使用“采集任务”中的 API 队列；已有导出文件、异常恢复检查或字段对照仍可使用本页流程。不同账号和群聊共用同一导入实现，但数据会按 `account_id` 和 `group_id` 隔离。
