# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Q-Limit：股票行情分析 + AI 多角色（多头 / 空头 / 裁判）辩论平台。后端 Flask + SQLite，前端原生 JS + ECharts 单页应用，另带一个钉钉 Stream 机器人（`#命令`）。界面文案、注释、提示词均为中文。

## 常用命令

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 填 AI_DEFAULT_* / TAVILY_API_KEY / DINGTALK_*
python3 app.py                # http://127.0.0.1:5000
```

- 没有测试、lint、构建步骤；前端是静态文件，改完刷新即可。
- 不启动钉钉也能调试机器人命令：

  ```bash
  curl -X POST localhost:5000/api/bot/command -H 'Content-Type: application/json' \
    -d '{"text":"#PRICE AAPL","user_id":"u1","conversation_id":"c1"}'
  ```

- `FLASK_HOST` / `FLASK_PORT` / `FLASK_DEBUG` 可用环境变量覆盖，默认 `0.0.0.0:5000`、debug 开启。debug 重载器会让 `__main__` 块执行两次，所以钉钉 Stream 机器人只在 `WERKZEUG_RUN_MAIN=true` 的子进程里启动。
- SQLite 文件默认 `data/stock_analysis.db`（已 gitignore，首次访问自动建库）；删掉它即可清空所有缓存和聊天记录。

## 架构

### 分层

- `app.py`：全部路由，只做参数解析和转发，无业务逻辑。统一返回 `{"data": ...}` 或 `{"error": ...}`。
- `services/`：每个领域一个模块，函数式风格，无类。失败时普遍 `print` 后返回空值（`[]` / `{}` / `None`），而不是抛异常。
- `models/database.py`：唯一的持久层。
- `config.py`：读 `.env`，同时存放 AI 角色定义（`AI_MODELS`，含 system prompt）和 `AI_TOOLS`。

### 股票标识：code + market + product

几乎所有数据函数都带 `market="US", product="ST"` 两个参数，拼成长桥的 `counter_id = "{product}/{market}/{code}"`（例如 `ST/US/AAPL`、`ST/SH/601988`）。前端从搜索结果里拿到 market/product，存进 `AppState`，之后每个请求都以 query 参数带上。**新增数据接口时必须把这两个参数从路由一路透传到 service**，否则非美股会静默查错标的。

AI 对话同样如此：`/api/chat`、`/api/chat/debate` 的请求体带 `market` / `product`，经 `chat_with_role` → `_build_background_context` 传到各数据函数。

### 数据来源

行情、K 线、分时、公司信息、新闻、财报全部来自长桥的**非官方 Web 接口**（`https://m.lbkrs.com/api/forward/...`），靠 `LONGBRIDGE_HEADERS` 伪装成网页端。这组请求头在 `stock_data.py`、`news.py`、`financial.py` 中各有一份副本。`config.py` 里的 `LONGBRIDGE_API` / `JIN10_API` 是未使用的预留项。

`stock_data.fetch_kline` 按 period 分流：
- `timeshare` / `5day` → 独立的分时接口
- 其他 → `PERIOD_LINE_TYPE` 映射到长桥 `line_type`，失败后降级到 akshare（仅 A 股日/周/月）
- `kline_session`（100 盘中 / 101 全部 / 102 盘前 / 103 盘后 / 104 夜盘）与分时接口的 `trade_session` 编号不同，由 `TIMESHARE_SESSION_MAP` 转换；前端对应表是 `app.js` 的 `SESSION_MAP`。

技术指标（`analysis.py`）、形态识别（`pattern_detector.py`）、估值（`valuation.py`）都是在 `fetch_kline` / `fetch_stock_detail` 结果上本地计算。

### 持久层：SQLite 上的仿 MongoDB 接口

`models/database.py` 只有一张 `documents(collection, doc JSON)` 表，`get_collection(name)` 返回的对象模拟了 pymongo 的一个子集（项目原先用 MongoDB）：

- 支持：`find` / `find_one` / `update_one`（仅 `$set`，可 `upsert`）/ `insert_one`，游标 `.sort().limit()`
- 查询操作符仅 `$or`、`$regex`+`$options`、`$gt/$gte/$lt/$lte`；**未知操作符一律匹配失败**
- 没有 `delete`、`update_many`、`$in`、`$inc` 等
- 每次查询都会把整个 collection 读进内存、逐条 JSON 解码后在 Python 里过滤，所以别把它当成可以无限增长的存储

现有 collection：`stocks`、`daily_quotes`、`company_info`、`news`、`financial_reports`、`web_search_cache`、`chat_history`、`bot_sessions`、`broker_accounts`、`bot_user_preferences`、`bot_processed_messages`。

### AI 对话（`services/ai_chat.py`）

- 调用任意 OpenAI 兼容的 `{base_url}/chat/completions`，**非流式**。`/api/chat` 和 `/api/chat/debate` 虽然以 SSE 返回，但每个角色只在整段回复完成后吐一个 chunk。
- **不使用 function calling**：`chat_with_role` 明确传 `tools=None`（为了兼容纯聊天的中转接口）。取而代之的是 `_build_background_context`，在每次提问前由后端抓好 K 线摘要 / 技术面 / 估值 / 新闻 / Tavily 结果，拼成文本塞进 user 消息。`AI_TOOLS` 和 `TOOL_FUNCTIONS` 目前是遗留的死代码。想让 AI 看到新数据，改 `_build_background_context`。
- 配置优先级：前端 `localStorage`（`ai_config_{role}`，随请求体 `api_config` 发来）> `.env` 的 `AI_{BULL|BEAR|JUDGE}_*` > `AI_DEFAULT_*`，由 `_resolve_llm_config` 合并。`/api/chat/debate` 的 `api_config` 是按角色分键的字典，`/api/chat` 的是单个角色的配置。
- `debate()` 依次跑 bull → bear → judge。裁判能看到前两者的观点，**靠的是共享 `session_id` 的 `chat_history` 回放**（`_build_messages` 取最近 10 条），并没有显式把内容传过去；调用方没给 `session_id` 时 `debate()` 会自己生成一个临时的。
- `PUT /api/ai/models/<role>` 只改当前进程内存里的角色默认配置（`AI_ROLE_DEFAULT_CONFIGS`），不写回 `.env`，重启即失效。

### 设置面板里的服务端配置（`services/env_settings.py`）

设置弹窗的「服务端配置」Tab 通过 `GET/PUT /api/settings/env` 读写 `.env`，表单完全由 `SETTING_GROUPS` 里的字段声明驱动（`static/js/settings.js` 只负责通用渲染）。**在 `config.py` 新增环境变量时，要在 `SETTING_GROUPS` 里补一条才会出现在界面上。**

- 只有声明过的键可写；密钥类字段只返回 `is_set`，从不回传明文；接口仅允许本机（loopback）访问，除非设置 `SETTINGS_ALLOW_REMOTE=true`。
- 保存后会 `importlib.reload(config)`，再把新值按名字同步到所有已 `from config import X` 的 `services.*` 模块，所以多数配置即时生效。`SQLITE_DB_PATH`、`SECRET_KEY`、Flask 主机/端口不热更新；钉钉 Stream 机器人未运行时会按新配置启动，已运行时改凭证需要重启。

### Docker / 生产运行

`docker compose up -d --build`。容器通过 `wsgi.py` 用 gunicorn 启动，**必须单 worker**（钉钉 Stream 线程、配置热更新都是进程内状态），并发靠线程。镜像把 `SQLITE_DB_PATH` 和 `ENV_FILE_PATH` 都指到 `/app/data`（数据卷 `qlimit-data`），所以页面上保存的配置会持久化；`.env` 不能做成单文件挂载，因为保存时是"写临时文件再 rename"。compose 默认只把端口绑在宿主机 `127.0.0.1`，并设 `SETTINGS_ALLOW_REMOTE=true`（容器里看到的来源是网桥地址）——对外暴露端口时要把它关掉。

### 钉钉机器人

- `dingtalk_robot.py`：启动守护线程跑 Stream 客户端，收消息 → 去重 → 交给 `bot_skills.handle_incoming_message` → 通过 session webhook 回复。
- `bot_skills.py`：`handle_skill_command` 是一长串 `if cmd_upper == ...` 分发。命令规格写在根目录的 `Bot_Skills.txt`，这个文件**同时在运行时被读取**用于生成 `#HELP` 输出——增删命令时两处都要改。
- 多轮流程（`#CONFIG` 券商登录）的状态存在 `bot_sessions`，按 user + conversation 定位，60 秒超时；有进行中的会话时，非 `#` 开头的文本会被当作该流程的输入。
- 券商接入在 `services/brokers.py`：`BROKERS` 声明每家券商要收集的凭证字段，`_FETCHERS` 里是对应的登录 + 资产/持仓查询（SDK 懒加载）。长桥（`longbridge`）和老虎（`tigeropen`）用官方 OpenAPI 的个人开发者凭证；A 股券商走 `ths`：Q-Limit 只请求局域网里的中转服务 `tools/ths_bridge`（独立脚本，跑在装了同花顺版下单客户端的 Windows 上，用 easytrader 读资金和持仓，只读、带口令，不打进 Docker 镜像），各券商客户端字段名不同，映射在 `_THS_BALANCE_KEYS` / `_THS_POSITION_KEYS`。富途 / 盈透需要本机网关，`unsupported_reason` 会直接告诉用户原因。**新增券商 = 在这两个表里各加一项**，`#CONFIG` 流程不用改。
- `#CONFIG` 收齐凭证后会真实登录，成功才写入 `broker_accounts`（明文存在 SQLite 里），失败不保存；群聊（`conversation_type == "2"`）里拒绝 `#CONFIG`，`#ACCOUNT` 也不回资产。
- `#ANALYZE` 复用 `ai_chat.chat_with_role("judge", ...)`，AI 失败时必须退回 `_fallback_analysis_markdown` 输出已抓取的材料，而不是只报错。
- `#BUY` / `#SELL` 只是占位，没有真实下单（即使已登录券商）。
- `_resolve_stock` 把用户输入解析成标的：支持 `.SH/.SZ/.HK/.US/.SG` 市场后缀；同名多地上市且未指定时优先 A 股，否则取搜索结果第一条。
- `/api/bot/status` 的 `alive` 只表示线程存活，是否真的连上钉钉看 `connected` / `last_error`（由 SDK 日志捕获，凭证错误时 SDK 只会无限重试而不退出）。
- `notifier.py` 是另一条独立通道：自定义机器人 webhook 的单向推送（辩论裁判结果等），与 Stream 机器人的配置项互不相干。

### 前端

`templates/index.html` 按顺序加载 `app.js` → `chart.js` → `analysis.js` → `chat.js`，全部是全局作用域的普通脚本（无模块、无打包），靠全局函数和 `AppState` / `ChatState` 互相调用。ECharts 和 marked 是放在 `static/lib/` 的本地副本。

- `app.js`：状态、搜索、选股后并行拉取各面板数据、自选股、主题
- `chart.js`：K 线 / 分时 / 指标图
- `analysis.js`：各分析面板的 HTML 渲染
- `chat.js`：聊天与辩论的 SSE 消费、AI 设置弹窗

自选股、主题、涨跌配色、AI 配置都只存在浏览器 `localStorage`，后端无用户概念。
