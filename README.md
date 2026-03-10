<div align="right">
  <a href="README_EN.md">English</a> | <strong>简体中文</strong>
</div>

# Q-Limit 📈

**Q-Limit** 是一套现代化、高效的开源股票行情分析与 AI 多角色辅助决策系统。它融合了实时行情跟踪、全维度的技术/财务/估值分析，以及特色鲜明的**多阵营 AI 同台辩论**功能，致力于为投资者提供客观、深度的市场分析工具。

> 🤖 **关于本项目：** 本项目的前端渲染、后端架构设计及所有的 API 整合均在用户的自然语言设想下，由 AI 全程作为辅助协同开发生成。这既是一个实用的股票分析平台，也是 Agentic AI 在复杂全栈金融项目中落地的一个概念验证（PoC）。

![Q-Limit Demo](img.png)
![img_1.png](img_1.png)

## ✨ 核心特性

- **🌍 纯净前端大模型接入**：支持纯对话类 LLM 原生接入。无需复杂的 Function Calling 支持，系统会自动进行**上下文注入**，把股票最新资金、技术指标和新闻在后台查询并拼装喂给 AI，令聊天机器人瞬间拥有了真实世界的金融分析能力！
- **⚔️ 首创 AI 多空辩论场**：
  - 🐂 **多头分析师**：专注于挖掘潜在的各种利好和上涨趋势。
  - 🐻 **空头分析师**：如同达摩克利斯之剑，负责警示估值泡沫、揭示潜藏的各类风险。
  - ⚖️ **裁判员**：根据多空双方的论据进行客观复盘，最终给出现实可行的做单建议。
- **📊 全维度面板看板**：
  - K线绘制（日线、分钟线图表结合技术指标）
  - 面板集成：盘口报价、估值水温分析、技术形态监测、新闻热榜。
- **🔎 Tavily 全网搜索组件**：支持通过 Tavily 拉取站外最新资讯，作为 AI 分析补充上下文。
- **🔔 钉钉通知组件**：支持将关键分析内容（如辩论裁判结果）推送到钉钉机器人。
- **⚙️ AI 配置双通道**：支持在后端 `.env` 里配置 AI 模型默认值，也支持在浏览器设置面板里做本地覆盖，便于本机部署和快速切换。

---

## 🚀 快速开始

本项目后端采用轻量级的 **Python Flask** 构建，依赖简单，启动极速。

### 1. 环境准备

请确保系统已安装：
- Python 3.8+
- SQLite（Python 内置 `sqlite3`，无需额外安装数据库服务）

### 2. 获取代码与安装依赖

```bash
git clone https://github.com/zhaoboy9692/Q-Limit.git
cd Q-Limit

# 建议使用虚拟环境
python3 -m venv venv
source venv/bin/activate  # Windows 用户使用 venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 初始化环境变量（Tavily/钉钉配置）
cp .env.example .env
```

### 3. 配置数据库与启动

项目使用 SQLite 作为本地缓存（K线、资讯、聊天记录等），无需单独启动数据库服务。
可在 `config.py` 中通过 `SQLITE_DB_PATH` 指定数据库文件路径（默认 `data/stock_analysis.db`）。

**启动后端服务器：**
```bash
python3 app.py
```
> 服务器默认将在 `http://127.0.0.1:5000` 启动。

### 4. 配置 AI 模型

优先推荐直接编辑 `.env`：
- `AI_DEFAULT_API_KEY` / `AI_DEFAULT_BASE_URL` / `AI_DEFAULT_MODEL`：全局默认模型配置
- `AI_BULL_*` / `AI_BEAR_*` / `AI_JUDGE_*`：分别覆盖多头、空头、裁判角色
- `AI_REQUEST_TIMEOUT_SECONDS`：AI 请求超时时间

系统启动后，在浏览器访问 `http://127.0.0.1:5000`，也可以继续通过右上角 **"⚙️ 设置"** 面板给单个浏览器做本地覆盖。前端填写的值优先，留空项会自动回退到 `.env` 默认值。支持所有接轨于 OpenAI `/v1/chat/completions` 标准格式的中转接口。

### 5. 配置 Tavily 与钉钉（可选）

编辑 `.env`：
- `TAVILY_API_KEY`：Tavily 搜索密钥
- `TAVILY_SEARCH_DEPTH`：`basic` / `advanced`
- `TAVILY_TIME_RANGE`：默认时间范围，例如 `day/week/month/year`
- `TAVILY_INCLUDE_DOMAINS` / `TAVILY_EXCLUDE_DOMAINS`：域名白名单/黑名单，逗号分隔
- `TAVILY_INCLUDE_ANSWER` / `TAVILY_INCLUDE_RAW_CONTENT`：是否返回 Tavily 摘要和原文片段
- `TAVILY_CACHE_EXPIRE`：SQLite 缓存秒数，避免重复消耗 Tavily 配额
- `DINGTALK_ENABLED`：是否启用钉钉通知（`true/false`）
- `DINGTALK_WEBHOOK`：钉钉机器人 webhook
- `DINGTALK_SECRET`：钉钉加签密钥（如有）
- `DINGTALK_NOTIFY_ON_DEBATE`：是否在 AI 辩论后自动发送裁判结论
- `DINGTALK_STREAM_ENABLED`：是否启用钉钉应用机器人 Stream 接收
- `DINGTALK_CLIENT_ID`：应用 `AppKey`
- `DINGTALK_CLIENT_SECRET`：应用 `AppSecret`
- `DINGTALK_AGENT_ID`：应用 `AgentId`

钉钉机器人收到 `#命令` 后会按 [`Bot_Skills.txt`](Bot_Skills.txt) 规则执行，例如：
- `#PRICE 601988`
- `#NEWS AAPL`
- `#ANALYZE 中信证券`
- `#CONFIG 浙商证券` 后可继续直接发送账号、b64 密码完成多轮配置
- `#CONFIG 查看` 查看当前配置，`#q` 或 `#CANCEL` 取消当前流程
- 登录流程 60s 内不回复会自动超时退出

`/api/search/web` 现支持更完整的 Tavily 参数，例如：
- `topic=finance`
- `time_range=month`
- `days=7`
- `include_domains=sec.gov,investor.apple.com`
- `include_raw_content=text`

---

## 🛠 技术架构

- **后端层**：Flask + Requests + SQLite。主要负责承接前端数据、通过各种聚合行情接口查数据，拼接并转发 SSE 流式请求给第三方 AI。
- **数据层**：利用 SQLite 进行轻量级本地缓存与历史分析数据持久化。
- **前端层**：Vanilla JS + CSS 变量定制系统 + ECharts K线渲染引擎，全程单页面交互体验 (SPA)，UI 现代且自带优雅的 Dark Mode。

---

## 📖 使用指南

- **搜索与自选**：在上方搜索框键入想要分析的股票代码（如 `AAPL`），可以直接获取。也可以添加到左侧自选股列表。
- **一键辩论**：点击右侧聊天面板中的“一键辩论”。此时后端会自动抓取各项财务与技术因子，并组织三位AI进行轮番辩论。
- **单独交流**：点击下方的“🐂多头”或“🐻空头”头像，也可以单独针对他们所在的立场提出有关特定财务指标、近期事件的垂直提问。

---

## 📄 开源协议

本项目基于 [GPL-3.0 License](LICENSE) 协议开源。请注意，所有的股票接口与 AI 对话结果仅供开发与学习参考，**不能作为任何真实的投资或交易建议**。市场有风险，投资需谨慎。
