# 手动导入格式

JSON 和 CSV 手动导入是保留的高级工具，用于导入既有聊天记录、离线验证搜索/附件/删除，或在微博 API 暂不可用时兜底。当前微博 API 采集器已经实现；文件导入不占 API 全局队列。

2026-10-21：JSON/CSV 与管理页统一限制一账号一群，包含已停用群。导入同账号已有同名群可继续；尝试创建第二群时明确报错且事务回滚，不产生部分批次/任务/消息。已有多群数据保留，不能借导入增加新群。

## JSON 格式

```json
{
  "account": "account_a",
  "group": "group_a",
  "messages": [
    {
      "source_message_id": "msg-001",
      "sender_name": "用户昵称",
      "source_user_id": "user-001",
      "sent_at": "2026-06-10 21:00:00",
      "message_type": "text",
      "content_text": "消息内容",
      "attachments": []
    }
  ]
}
```

## CSV 字段

CSV 首行需要包含：

```text
account,group,source_message_id,sender_name,source_user_id,sent_at,message_type,content_text,attachments_json
```

其中 `attachments_json` 是一个 JSON 数组字符串。

## 附件格式

```json
{
  "attachment_type": "image",
  "source_url": "https://example.com/image.jpg",
  "local_path": "C:/path/to/image.jpg",
  "file_name": "image.jpg",
  "mime_type": "image/jpeg",
  "download_status": "pending"
}
```

`attachment_type` 可用值：

- `image`
- `file`
- `link`
- `video`
- `audio`
- `unknown`

## 红包过滤

导入时会跳过红包类消息。

当前识别规则：

- `message_type` 为 `red_packet`、`hongbao`、`weibo_red_packet`。
- 消息正文为 `[红包]`、`微博红包`、`发了一个红包`、`领取了红包` 等。
- `is_redenvelope` / `is_red_packet`、红包图标、最佳手气模板，以及受微博系统 `appid` 约束的金额通知。
- 粉丝群“今日获得标识”通知也会作为系统噪声过滤；普通“早上好”等问候正常入库。

## 用户身份（TEST-007，2026-10-21）

- 带 `source_user_id` 的消息只匹配相同 UID；相同昵称的无 UID 用户和不同 UID 用户都不合并。
- UID 相同仍复用原用户，较新的昵称/头像可更新；补导较旧消息不会把新昵称覆盖回旧昵称。
- 未提供、`null` 或空字符串 UID 统一视为无 UID，仅在无 UID 用户中按昵称查找。这只是缺少身份信息时的兼容回退，不能证明同名匿名记录属于同一真人。
- 无 UID 历史保持原归属，不自动补 UID 或合并。JSON/CSV、文件与 API 采集入库共用该识别逻辑。

此次修复防止后续导入把不同已知 UID 误判为同一用户；不会自动恢复此前漏导的消息或重写历史归属。如需整理真实历史，应先单独核查原始来源与范围。

## 去重规则

优先按以下字段去重：

```text
account_id + group_id + source_message_id
```

如果没有 `source_message_id`，则使用：

```text
account_id + group_id + user_id + sent_at + content_hash
```

## 运行导入

JSON：

```powershell
python .\scripts\import_messages.py .\data\imports\sample-import.json
```

CSV：

```powershell
python .\scripts\import_messages.py .\data\imports\sample-import.csv
```

如果消息里的附件提供了本地路径，并且需要复制原件：

```powershell
python .\scripts\import_messages.py .\data\imports\sample-import.json --copy-local-attachments
```

检查数据库：

```powershell
python .\scripts\inspect_db.py
```
