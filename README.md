# Weibo Chat Collector

微博群聊消息的本地采集、归档、检索和管理工具。当前主线保留 collector 的多账号、群聊配置、SQLite 落库和消息管理能力，以微博 Web 客户端的内部 JSON 接口作为主要采集来源。

## 当前架构

`weibo-chat-collector` 是主项目，负责账号与群聊配置、采集队列、严格时间范围过滤、逐页落库、断点续传、监控、检索和删除。

相邻的 `weibo-chat-auto` 只提供两项初始化辅助：

1. 每个账号扫码登录后生成 `cookies.json`。
2. 打开目标群聊并从 `query_messages.json` 请求中发现群 `id`。

auto 的归档、查看器和 AI 分析均不属于 collector 的采集链路，collector 运行时也不依赖 auto 的 AI 能力或数据库。

```text
auto 扫码 -> cookies.json ---------------------+
                                                |
query_messages 请求 -> 群 id ------------------+-> collector 全局队列
                                                    -> 逐页调用 API
                                                    -> SQLite
```

collector 调用的是观察自微博 Web 客户端的内部接口，不是公开、受支持或保证稳定的官方 API：

```text
https://api.weibo.com/webim/groupchat/query_messages.json
```

接口地址、字段、鉴权、分页和限流行为都可能随时改变。只能采集当前账号有权查看的数据，并应遵守微博服务条款和适用法律。

## 任务执行模型

`POST /api/collection-jobs/weibo-api` 只创建任务并返回 `202`，不会在当前 HTTP 请求里同步采集。所有账号共用一个后台串行队列：

```text
queued -> awaiting_confirmation -> running -> completed
                                 \-> stopped
```

- 后台只把队首任务提升为 `awaiting_confirmation`；该任务等待人工确认期间也占用唯一活动位。
- 用户关闭微博 App 和微博网页后，在“采集监控”中确认，任务才从 `awaiting_confirmation` 进入 `running`。
- 每次 API 请求只处理一页。消息、过滤/去重计数、页记录和 `next_max_mid` 在同一个数据库事务中提交。
- 已停止任务沿用原任务 ID 和已提交的 `next_max_mid` 重新排队；再次确认时新增一次 attempt，而不是新建任务或从头开始。
- 所有错误都采用零重试：当前 attempt 立即停止，不自动恢复，失败请求或失败事务不会推进页记录、计数和断点。
- 普通请求之间随机等待 3–8 秒；全局每累计 20 个请求后，到下一请求的间隔改为 30–60 秒，这次长等待替代普通等待，不与 3–8 秒叠加。
- HTTP 429、微博业务码 10023 或 10024 会设置 60 分钟冷却。冷却结束后仍需人工点击续传，随后等待队列并再次确认，不会自动恢复。
- 对排队中或待确认任务停止时，不会发出下一次请求；运行中“安全停止”会停在页边界。若请求已经发出，成功取得的该页会先完整提交再停止。
- 达到单次运行页数上限时任务进入 `stopped`，保留断点供人工续传，不会把未覆盖完整的时间范围标成完成。

当前高置信度过滤规则只过滤红包和粉丝群标识，两类消息都不写入 `messages`。普通问候等未命中高置信度规则的内容照常入库；`filtered_system_notice_count` 当前统计的是被过滤的粉丝群标识，不代表所有系统消息都会被删除。

详细状态、操作和接口见 [采集任务说明](docs/collection-jobs.md) 与 [微博 API 采集指南](docs/weibo-api-collection.md)。

## Cookie 与群 ID 安全

- 每个账号的 Cookie 独立保存为 `data/auth/account-<id>.cookies.json`。
- Cookie 内容不写入数据库，也不由任何状态或采集 API 返回；数据库只保存不敏感的配置文件名。
- POSIX 系统把 Cookie 目录/文件权限收紧为 `0700`/`0600`；Windows 继承当前项目目录 ACL，应确保其他本机用户无权读取。
- `data/auth/` 不进入 Git。不要把 Cookie、Token、Authorization、账号密码或完整请求头提交到 Git、聊天、Issue、日志或截图。
- 群 ID 存在 `chat_groups.source_group_id`，应从对应账号、对应群聊的 `query_messages.json?id=...` 中取得，不能只凭群名猜测。
- 服务默认只监听 `127.0.0.1`；不要把带 Cookie 导入能力的本地 API 暴露到公网。

## 运行要求

- Python `>= 3.10`，推荐 Python `3.12`。
- Node.js `>= 18`。
- 所有命令从本项目根目录执行。数据库、附件、导入和鉴权路径都相对项目根解析。

终端一启动后端，固定使用 8000 端口：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python -m uvicorn app.main:app --reload --app-dir backend --host 127.0.0.1 --port 8000
```

如果系统只有 `python3`，可替换第一行，但应先确认版本不低于 3.10。Windows 可使用 `py -3.12` 创建环境，并把后续解释器路径换成 `.\.venv\Scripts\python.exe`。

终端二仍从项目根目录启动前端：

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

打开 <http://127.0.0.1:5173>。Vite 将 `/api` 和 `/health` 代理到 `http://127.0.0.1:8000`。

- 健康检查：<http://127.0.0.1:8000/health>
- API 文档：<http://127.0.0.1:8000/docs>

## UI 使用流程

首次配置：

1. 在 auto 中为账号扫码，取得该账号的 `cookies.json`。
2. 用同一账号打开目标群聊，记录 `query_messages.json` 查询参数中的 `id`。
3. 在 collector 的“高级工具 / 数据导入”中创建或更新账号、群聊和群 ID 绑定。

开始采集：

1. 在“采集任务”中选择已配置的账号和群聊。
2. 为当前账号导入对应的 `cookies.json`，确认 Cookie 和群 ID 都已就绪。
3. 选择开始、结束时间并创建任务。任务先进入 `queued`。
4. 在“采集监控”查看队列位置、状态、attempt 次数、页数、当前最早消息时间、`next_max_mid`、计数和停止原因。
5. 任务进入 `awaiting_confirmation` 后，关闭微博 App 和所有微博网页，再点击“确认并启动”。
6. 已停止任务按提示处理后点击“沿断点续传”；风险冷却任务需先等待 60 分钟。

第二个账号必须重新扫码并导入自己的 Cookie，不能复用第一个账号的文件。

## 高级工具与备用导入

“高级工具 / 数据导入”集中放置：

- API 采集目标配置。
- `data/imports/` 下的 JSON/CSV 文件导入。
- 网页快照生成、预览和显式导入。

JSON/CSV 和网页快照不占微博 API 队列。文件导入继续保留；网页跨域快照受 CORS、Private Network Access 和页面安全策略影响，已不再是推荐主流程。

当前交付以桌面端为目标。前端虽然有基础响应式样式，但移动端任务监控、宽表操作和完整交互验证延后处理。

## 验证边界

本地自动化测试使用临时 SQLite 和模拟响应验证核心实现，但不代表已经完成真实微博网络端到端验证。当前不能声称：

- 内部 API 已在真实微博账号上稳定可用。
- 所有账号、群聊和消息类型都符合当前字段映射。
- 图片、文件、链接、视频等附件字段已经实机确认。
- 附件原件已经成功下载；当前主要记录可识别的 URL 和元数据。

## 文档

- [微博 API 采集指南](docs/weibo-api-collection.md)
- [采集任务与监控](docs/collection-jobs.md)
- [当前状态与下一步](docs/current-status.md)
- [数据模型](docs/data-model.md)
- [运行与调试](docs/run-and-debug.md)
- [JSON/CSV 导入格式](docs/import-format.md)
- [消息接口](docs/message-api.md)
- [回收站](docs/recycle-bin.md)

## 项目结构

```text
weibo-chat-collector/
  backend/              # FastAPI、后台采集 worker 和入库服务
  frontend/             # React + Vite
  data/
    auth/               # 按账号隔离的 Cookie，仅本机保存且不进 Git
    attachments/        # 附件原件目录
    imports/            # JSON/CSV 备用导入目录
    weibo_chat_collector.sqlite3
  docs/
  scripts/
```
