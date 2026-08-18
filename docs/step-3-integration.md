# 第三步联调指南

更新时间：2026-06-13

> 历史阶段文档：本页只记录第三步“消息列表和搜索”的联调方式。当前采集队列与运行说明以根目录 `README.md`、`docs/current-status.md` 和 `docs/run-and-debug.md` 为准。

## 第三步是什么

第三步是“消息列表和搜索”。

目标：

- 后端提供消息列表 API。
- 后端提供消息详情 API。
- 前端能调用后端 API。
- 前端能展示数据库里的消息。
- 前端筛选条件能传给后端。
- 数据来自当前 SQLite 数据库。

第三步已经完成，现在可以开始完整的前后端联调。

## 现在能不能联调

当前项目已完成到第三步，可以做完整联调：

- 数据库检查。
- JSON/CSV 导入。
- 后端健康检查。
- 前端消息检索页面启动。
- 前端调用后端消息接口。

完整联调指的是：

```text
浏览器前端页面 -> FastAPI 后端接口 -> SQLite 数据库 -> 返回消息列表 -> 前端展示
```

## 需要安装的软件

### 必装

#### 1. Python

用途：

- 运行 FastAPI 后端。
- 运行数据库脚本。
- 运行导入脚本。

建议：

- 安装 Python 3.12 或 3.13。
- 安装时勾选 `Add python.exe to PATH`。

官方下载：

```text
https://www.python.org/downloads/windows/
```

如果你只在 Codex 里运行，也可以先使用 Codex 桌面自带 Python，不一定立刻安装系统 Python。

当前已验证的 Codex 自带 Python 路径：

```powershell
C:\Users\SethJ\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
```

当前本机已发现：

```text
Python: D:\Dev\Python\python.exe
Node: D:\Dev\node\node.exe
npm: D:\Dev\node\npm.cmd
```

如果 `node`、`npm`、`python` 在 PowerShell 里仍然不可识别，说明安装目录还没有加入 PATH。可以先用完整路径运行，后续再配置环境变量。

#### 2. Node.js

用途：

- 运行 React + Vite 前端。
- 提供 npm 包管理器。

建议：

- 安装 Node.js LTS 版本。
- 安装 Node.js 时会自动带 npm。

官方下载：

```text
https://nodejs.org/en/download
```

### 推荐安装

#### 3. Visual Studio Code

用途：

- 打开项目。
- 查看前后端代码。
- 看终端输出。

不是必须，但方便。

#### 4. DB Browser for SQLite

用途：

- 可视化查看 SQLite 数据库。
- 检查 `messages`、`attachments` 等表。

官方下载：

```text
https://sqlitebrowser.org/dl/
```

不是必须，因为项目已有：

```powershell
python .\scripts\inspect_db.py
```

#### 5. Postman、Insomnia 或 Apifox

用途：

- 手动测试后端 API。

不是必须，因为 FastAPI 自带接口文档：

```text
http://127.0.0.1:8000/docs
```

### 当前不需要安装

第三步暂时不需要：

- MySQL。
- PostgreSQL。
- Redis。
- Docker。
- Nginx。
- 微博采集工具。
- 抓包代理工具。

微博真实采集是第八步和第九步的内容，不是第三步。

## 检查安装是否成功

打开 PowerShell，执行：

```powershell
python --version
pip --version
node --version
npm --version
```

如果 `python` 不可用，但你在 Codex 桌面里，可以用：

```powershell
& 'C:\Users\SethJ\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' --version
```

## 启动后端

进入项目根目录：

```powershell
cd D:\codex\weibo-chat-collector
```

创建虚拟环境：

```powershell
python -m venv .venv
```

启用虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

如果 PowerShell 不允许启用脚本，可以临时执行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

然后再启用：

```powershell
.\.venv\Scripts\Activate.ps1
```

安装后端依赖：

```powershell
pip install -r .\backend\requirements.txt
```

检查数据库：

```powershell
python .\scripts\inspect_db.py
```

启动后端：

```powershell
python -m uvicorn app.main:app --reload --app-dir .\backend --host 127.0.0.1 --port 8000
```

打开健康检查：

```text
http://127.0.0.1:8000/health
```

打开 FastAPI 接口文档：

```text
http://127.0.0.1:8000/docs
```

## 启动前端

另开一个 PowerShell 窗口。

进入前端目录：

```powershell
cd D:\codex\weibo-chat-collector\frontend
```

安装前端依赖：

```powershell
npm install
```

启动前端：

```powershell
npm run dev
```

默认访问：

```text
http://127.0.0.1:5173
```

如果 Vite 提示用了其他端口，就以终端输出为准。

## 第三步完成后的联调流程

### 1. 确认数据库有数据

在项目根目录执行：

```powershell
python .\scripts\inspect_db.py
```

应该能看到类似：

```text
messages: 4
attachments: 2
```

如果没有数据，先导入样例：

```powershell
python .\scripts\import_messages.py .\data\imports\sample-import.json
python .\scripts\import_messages.py .\data\imports\sample-import.csv
```

### 2. 启动后端

```powershell
python -m uvicorn app.main:app --reload --app-dir .\backend --host 127.0.0.1 --port 8000
```

确认：

```text
http://127.0.0.1:8000/health
```

### 3. 测试后端消息接口

第三步实现后，预计会有类似接口：

```text
GET /api/messages
GET /api/messages/{message_id}
```

筛选参数预计包括：

```text
account
group
user
date_from
date_to
keyword
message_type
has_attachment
```

可以先在 FastAPI 文档里测试：

```text
http://127.0.0.1:8000/docs
```

### 4. 启动前端

```powershell
cd D:\codex\weibo-chat-collector\frontend
npm run dev
```

打开：

```text
http://127.0.0.1:5173
```

### 5. 在前端页面验证

需要验证：

- 能看到消息列表。
- 能按账号筛选。
- 能按群聊筛选。
- 能按用户筛选。
- 能按日期范围筛选。
- 能按关键词搜索。
- 能按消息类型筛选。
- 能筛选有附件的消息。
- 点击消息能看到详情。

### 6. 看浏览器网络请求

在浏览器按 `F12`，打开开发者工具。

进入 `Network` 面板，检查：

- 前端是否请求了 `http://127.0.0.1:8000/api/messages`。
- 请求状态是否是 `200`。
- 返回内容是否包含消息数据。
- 筛选条件变化时，请求参数是否变化。

## 常见问题

### python 不是可识别命令

说明 Python 没装进 PATH。

解决：

- 重新安装 Python，并勾选 `Add python.exe to PATH`。
- 或使用 Codex 自带 Python 路径。

### npm 不是可识别命令

说明 Node.js 没装好，或没有进入 PATH。

解决：

- 安装 Node.js LTS。
- 重新打开 PowerShell。
- 执行 `node --version` 和 `npm --version`。

### 后端端口 8000 被占用

网页快照脚本默认固定提交到后端 8000 端口。请先结束占用 8000 的旧进程，再按上面的标准命令重新启动后端；不要只临时切换端口而遗漏前端代理和快照脚本配置。


### 前端端口 5173 被占用

Vite 通常会自动换端口，以终端输出为准。

### 前端请求后端失败

常见原因：

- 后端没启动。
- 后端端口不是 8000。
- 前端 API 地址写错。
- 后端没有允许跨域。

第三步实现 API 时需要同时配置 CORS。

### 数据库没有消息

重新导入样例：

```powershell
python .\scripts\import_messages.py .\data\imports\sample-import.json
python .\scripts\import_messages.py .\data\imports\sample-import.csv
```

## 微博接口信息什么时候需要

第三步不需要微博接口信息。

微博接口信息在第八步需要：

- 微博侧实机验证。
- 确认历史消息接口形状。
- 确认分页方式。
- 确认图片、文件、链接字段。
- 确认红包消息特征。

第三步只需要本地样例数据就能完成联调。

## 如果要提前准备微博侧信息

只准备脱敏信息，不要提供账号凭据。

可以准备：

- 脱敏接口路径。
- 请求方法。
- 请求参数字段名。
- 分页字段名。
- 响应顶层字段。
- 消息对象字段。
- 图片字段。
- 文件字段。
- 链接字段。
- 红包消息字段。

不要提供：

- 微博密码。
- Cookie。
- Authorization。
- Token。
- XSRF。
- App Secret。
- 未脱敏 HAR 文件。
