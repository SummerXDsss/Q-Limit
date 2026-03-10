"""
钉钉机器人技能路由
根据 Bot_Skills.txt 解析并执行 #xxx 指令，并支持多轮会话。
"""
import base64
import os
from datetime import datetime, timedelta

from config import BASE_DIR
from models.database import get_collection
from services import ai_chat, analysis, financial, news, stock_data, valuation, web_search

SESSION_COLLECTION = "bot_sessions"
BROKER_COLLECTION = "broker_accounts"
PREFERENCE_COLLECTION = "bot_user_preferences"
LOGIN_FLOW_TIMEOUT_SECONDS = 60


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _read_skill_lines():
    path = os.path.join(BASE_DIR, "Bot_Skills.txt")
    if not os.path.exists(path):
        return []

    lines = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s.startswith("#"):
                lines.append(s)
    return lines


def _group_help_lines(lines):
    groups = {
        "行情分析": [],
        "交易配置": [],
        "扩展能力": [],
        "其他": [],
    }
    for line in lines:
        upper = line.upper()
        if upper.startswith(("#PRICE", "#KDJ", "#NEWS", "#MA10", "#ANALYZE", "#T1", "#T7", "#T30", "#T365")):
            groups["行情分析"].append(line)
        elif upper.startswith(("#BUY", "#SELL", "#CONFIG", "#CANCEL", "#Q")):
            groups["交易配置"].append(line)
        elif upper.startswith(("#ANALYZE_MCP", "#GRAPH", "#SEARCH")):
            groups["扩展能力"].append(line)
        elif upper.startswith(("#HELP", "#指数".upper(), "#新股".upper())):
            groups["其他"].append(line)
        else:
            groups["其他"].append(line)
    return groups


def _is_help_message(text):
    raw = (text or "").strip()
    if not raw:
        return False
    body = raw[1:].strip() if raw.startswith("#") else raw
    return body.upper() == "HELP" or body == "帮助"


def _help_text():
    lines = _read_skill_lines()
    if not lines:
        return "未找到 Bot_Skills.txt，请联系管理员。"

    grouped = _group_help_lines(lines)
    panel = [
        "帮助面板",
        "",
        "基础说明：",
        "- 命令以 # 开头",
        "- 大小写不区分，例如 Help / help / #HELP / #Help 都可识别",
        "- 股票参数可用代码或名称，例如 AAPL / BABA / 中信证券",
        "",
    ]

    ordered_titles = ["行情分析", "交易配置", "扩展能力", "其他"]
    for title in ordered_titles:
        items = grouped.get(title) or []
        if not items:
            continue
        panel.append(f"{title}：")
        panel.extend(items)
        panel.append("")

    panel.extend([
        "常用示例：",
        "- #PRICE AAPL",
        "- #NEWS 中信证券",
        "- #ANALYZE 601998",
        "- #GRAPH on",
        "- #CONFIG 浙商证券",
        "- #CONFIG 查看",
        "",
        "流程提示：",
        f"- 登录流程 {LOGIN_FLOW_TIMEOUT_SECONDS}s 内不回复会自动退出",
        "- 发送 #q 或 #CANCEL 可退出当前流程",
    ])
    return "\n".join(panel)


def _markdown_response(title, text, image_url=""):
    payload = {
        "type": "markdown",
        "title": (title or "Q-Limit").strip() or "Q-Limit",
        "text": (text or "").strip(),
    }
    if image_url:
        payload["image_url"] = image_url
    return payload


def _text_response(text):
    return {"type": "text", "text": (text or "").strip()}


def _normalize_reply_payload(reply, fallback_title="Q-Limit"):
    if isinstance(reply, dict):
        payload = dict(reply)
        payload.setdefault("type", "text")
        if payload["type"] == "markdown":
            payload.setdefault("title", fallback_title)
            payload.setdefault("text", "")
            payload["text"] = str(payload.get("text") or "")
        else:
            payload.setdefault("text", payload.get("content", ""))
            payload["text"] = str(payload.get("text") or "")
        return payload

    text = str(reply or "")
    return _text_response(text)


def _fmt_num(num, digits=2):
    try:
        return f"{float(num):.{digits}f}"
    except Exception:
        return "--"


def _fmt_big_num(v):
    try:
        n = float(v)
    except Exception:
        return "--"
    if abs(n) >= 1e12:
        return f"{n / 1e12:.2f}万亿"
    if abs(n) >= 1e8:
        return f"{n / 1e8:.2f}亿"
    if abs(n) >= 1e4:
        return f"{n / 1e4:.2f}万"
    return f"{n:.0f}"


def _trim_text(text, limit=120):
    value = " ".join((text or "").strip().split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."


def _safe_web_summary(summary):
    text = (summary or "").strip()
    if not text:
        return "暂无外部搜索结果"
    if any(keyword in text for keyword in ("未配置 TAVILY_API_KEY", "Tavily 请求失败", "Tavily 请求异常")):
        return "暂无外部搜索结果"
    return text


def _md_escape(text):
    return str(text or "").replace("|", "\\|")


def _normalize_context(context):
    ctx = dict(context or {})
    user_id = (
        ctx.get("user_id")
        or ctx.get("sender_staff_id")
        or ctx.get("sender_id")
        or "debug-user"
    )
    conversation_id = ctx.get("conversation_id") or "default-conversation"
    ctx["user_id"] = str(user_id)
    ctx["conversation_id"] = str(conversation_id)
    ctx["sender_nick"] = str(ctx.get("sender_nick") or "")
    ctx["conversation_type"] = str(ctx.get("conversation_type") or "")
    return ctx


def _session_key(ctx):
    return f"{ctx['user_id']}::{ctx['conversation_id']}"


def _get_session(ctx):
    col = get_collection(SESSION_COLLECTION)
    return col.find_one(
        {"session_key": _session_key(ctx), "active": True},
        {"_id": 0},
    )


def _save_session(ctx, session):
    doc = dict(session)
    doc["session_key"] = _session_key(ctx)
    doc["user_id"] = ctx["user_id"]
    doc["conversation_id"] = ctx["conversation_id"]
    doc["sender_nick"] = ctx.get("sender_nick", "")
    doc["updated_at"] = _now_iso()
    doc["expires_at"] = (datetime.now() + timedelta(seconds=LOGIN_FLOW_TIMEOUT_SECONDS)).isoformat(timespec="seconds")
    doc.setdefault("created_at", _now_iso())
    doc.setdefault("active", True)

    get_collection(SESSION_COLLECTION).update_one(
        {"session_key": doc["session_key"]},
        {"$set": doc},
        upsert=True,
    )
    return doc


def _clear_session(ctx):
    get_collection(SESSION_COLLECTION).update_one(
        {"session_key": _session_key(ctx)},
        {"$set": {"active": False, "updated_at": _now_iso()}},
        upsert=True,
    )


def _is_session_expired(session):
    expires_at = _parse_iso(session.get("expires_at"))
    if expires_at is None:
        updated_at = _parse_iso(session.get("updated_at")) or _parse_iso(session.get("created_at"))
        if updated_at is None:
            return False
        expires_at = updated_at + timedelta(seconds=LOGIN_FLOW_TIMEOUT_SECONDS)
    return datetime.now() >= expires_at


def _remaining_seconds(session):
    expires_at = _parse_iso(session.get("expires_at"))
    if expires_at is None:
        return 0
    return max(0, int((expires_at - datetime.now()).total_seconds()))


def _get_broker_profile(ctx):
    return get_collection(BROKER_COLLECTION).find_one(
        {"user_id": ctx["user_id"], "active": True},
        {"_id": 0},
    )


def _get_user_preferences(ctx):
    return get_collection(PREFERENCE_COLLECTION).find_one(
        {"user_id": ctx["user_id"]},
        {"_id": 0},
    ) or {}


def _save_user_preferences(ctx, **kwargs):
    doc = {
        "user_id": ctx["user_id"],
        "sender_nick": ctx.get("sender_nick", ""),
        "updated_at": _now_iso(),
    }
    doc.update(kwargs)
    get_collection(PREFERENCE_COLLECTION).update_one(
        {"user_id": ctx["user_id"]},
        {"$set": doc},
        upsert=True,
    )
    return doc


def _graph_enabled(ctx):
    return bool(_get_user_preferences(ctx).get("graph_enabled", False))


def _save_broker_profile(ctx, broker_name, account, password_b64):
    masked_account = account if len(account) <= 4 else f"{account[:2]}***{account[-2:]}"
    doc = {
        "user_id": ctx["user_id"],
        "sender_nick": ctx.get("sender_nick", ""),
        "broker_name": broker_name,
        "account": account,
        "account_masked": masked_account,
        "password_b64": password_b64,
        "password_len": len(password_b64),
        "active": True,
        "updated_at": _now_iso(),
        "configured_at": _now_iso(),
    }
    get_collection(BROKER_COLLECTION).update_one(
        {"user_id": ctx["user_id"]},
        {"$set": doc},
        upsert=True,
    )
    return doc


def _resolve_stock(keyword):
    keyword = (keyword or "").strip()
    if not keyword:
        return None, "请提供股票代码或名称。"

    results = stock_data.search_stock(keyword)
    if not results:
        return None, f"未找到股票：{keyword}"

    item = results[0]
    return {
        "code": item.get("code", ""),
        "name": item.get("name", "") or item.get("code", ""),
        "market": item.get("market", "US"),
        "product": item.get("product", "ST"),
    }, ""


def _cmd_price(arg):
    stock, err = _resolve_stock(arg)
    if err:
        return err

    detail = stock_data.fetch_stock_detail(
        stock["code"],
        market=stock["market"],
        product=stock["product"],
    )
    if detail.get("error"):
        return f"查询失败：{detail.get('error')}"

    sign = "+" if float(detail.get("change_pct", 0) or 0) >= 0 else ""
    return (
        f"【{stock['name']} {stock['code']}】\n"
        f"现价: {_fmt_num(detail.get('last_done'))}\n"
        f"涨跌: {sign}{_fmt_num(detail.get('change'))} ({sign}{_fmt_num(detail.get('change_pct'))}%)\n"
        f"今开/最高/最低: {_fmt_num(detail.get('open'))} / {_fmt_num(detail.get('high'))} / {_fmt_num(detail.get('low'))}\n"
        f"成交量: {_fmt_big_num(detail.get('volume'))}\n"
        f"成交额: {_fmt_big_num(detail.get('turnover'))}"
    )


def _cmd_kdj(arg):
    stock, err = _resolve_stock(arg)
    if err:
        return err

    rows = analysis.calc_kdj(stock["code"], market=stock["market"], product=stock["product"])
    if not rows:
        return f"{stock['code']} 暂无 KDJ 数据。"

    latest = rows[-1]
    return (
        f"【{stock['name']} {stock['code']}】KDJ\n"
        f"日期: {latest.get('date', '')}\n"
        f"K={_fmt_num(latest.get('k'))}, D={_fmt_num(latest.get('d'))}, J={_fmt_num(latest.get('j'))}"
    )


def _cmd_news(arg):
    return _cmd_news_with_context(arg, None)


def _pick_result_image(news_items, web_items):
    for item in news_items or []:
        cover = str(item.get("cover", "")).strip()
        if cover:
            return cover
    for item in web_items or []:
        url = str(item.get("image_url", "") or item.get("image", "")).strip()
        if url:
            return url
    return ""


def _cmd_news_with_context(arg, ctx):
    stock, err = _resolve_stock(arg)
    if err:
        return _text_response(err)

    items = news.fetch_news(stock["code"], limit=50, market=stock["market"], product=stock["product"])
    cutoff = datetime.now() - timedelta(days=7)
    filtered = []
    for item in items:
        ts = item.get("publish_time", "")
        try:
            dt = datetime.strptime(ts, "%Y-%m-%d %H:%M")
            if dt < cutoff:
                continue
        except Exception:
            pass
        filtered.append(item)

    filtered = filtered[:5]
    web_result = web_search.search_stock_web(
        stock["code"],
        limit=3,
        topic="finance",
        time_range="month",
        include_answer=True,
        include_images=True,
    )
    web_items = (web_result.get("results") or [])[:3]

    if not filtered and not web_items:
        return _text_response(f"{stock['code']} 近7日暂无新闻数据。")

    lines = [
        f"# {stock['name']} {stock['code']} 近 7 日新闻",
        "",
        "## 站内资讯",
    ]

    if filtered:
        for idx, item in enumerate(filtered, 1):
            title = item.get("title", "无标题")
            publish_time = item.get("publish_time", "")
            source = item.get("source", "长桥")
            content = _trim_text(item.get("ai_summary") or item.get("content", ""), 90)
            lines.append(f"{idx}. **{title}**")
            lines.append(f"   - 时间: {publish_time or '--'}")
            lines.append(f"   - 来源: {source}")
            if content:
                lines.append(f"   - 摘要: {content}")
            if item.get("url"):
                lines.append(f"   - 链接: {item.get('url')}")
    else:
        lines.append("- 近 7 日暂无站内资讯。")

    lines.append("")
    lines.append("## Tavily 全网搜索")
    if web_items:
        for idx, item in enumerate(web_items, 1):
            meta = " / ".join([x for x in (item.get("source", ""), item.get("published_date", "")) if x])
            title = item.get("title", "无标题")
            content = _trim_text(item.get("content", "") or item.get("summary", ""), 120)
            line = f"{idx}. **{title}**"
            if meta:
                line += f"  \n   - 来源: {meta}"
            if content:
                line += f"  \n   - 摘要: {content}"
            if item.get("url"):
                line += f"  \n   - 链接: {item.get('url')}"
            lines.append(line)
    else:
        lines.append("- 暂无全网搜索结果。")

    image_url = _pick_result_image(filtered, web_items) if ctx and _graph_enabled(ctx) else ""
    return _markdown_response(
        title=f"{stock['name']} {stock['code']} 新闻",
        text="\n".join(lines),
        image_url=image_url,
    )


def _cmd_ma10(arg):
    stock, err = _resolve_stock(arg)
    if err:
        return err

    ma = analysis.calc_moving_averages(stock["code"], market=stock["market"], product=stock["product"])
    ma10 = ma.get("ma10", [])
    if not ma10:
        return f"{stock['code']} 暂无 MA10 数据。"

    latest = ma10[-1]
    return (
        f"【{stock['name']} {stock['code']}】MA10\n"
        f"日期: {latest.get('date', '')}\n"
        f"MA10: {_fmt_num(latest.get('value'))}"
    )


def _cmd_t_days(arg, days):
    stock, err = _resolve_stock(arg)
    if err:
        return err

    rows = stock_data.fetch_kline(stock["code"], period="daily", market=stock["market"], product=stock["product"])
    if not rows:
        return f"{stock['code']} 暂无行情数据。"

    seg = rows[-days:] if len(rows) >= days else rows
    if not seg:
        return f"{stock['code']} 暂无行情数据。"

    start = float(seg[0].get("close", 0) or 0)
    end = float(seg[-1].get("close", 0) or 0)
    high = max(float(x.get("high", 0) or 0) for x in seg)
    low = min(float(x.get("low", 0) or 0) for x in seg)
    pct = ((end - start) / start * 100) if start else 0
    sign = "+" if pct >= 0 else ""

    return (
        f"【{stock['name']} {stock['code']}】近{len(seg)}日数据\n"
        f"起止收盘: {_fmt_num(start)} -> {_fmt_num(end)} ({sign}{_fmt_num(pct)}%)\n"
        f"区间最高/最低: {_fmt_num(high)} / {_fmt_num(low)}"
    )


def _cmd_analyze(arg):
    return _cmd_analyze_with_context(arg, None, use_mcp=False)


def _build_analysis_snapshot(stock):
    tech = analysis.get_technical_summary(stock["code"], market=stock["market"], product=stock["product"])
    pe = valuation.get_pe_analysis(stock["code"], market=stock["market"], product=stock["product"])
    pb = valuation.get_pb_analysis(stock["code"], market=stock["market"], product=stock["product"])
    fin = financial.get_financial_report(stock["code"], market=stock["market"], product=stock["product"])
    latest_news = news.fetch_news(stock["code"], limit=5, market=stock["market"], product=stock["product"])[:3]
    web_result = web_search.search_stock_web(
        stock["code"],
        limit=3,
        topic="finance",
        time_range="month",
        include_answer=True,
        include_images=True,
    )
    web_items = (web_result.get("results") or [])[:3]
    web_ctx = web_search.get_web_search_for_ai(stock["code"], limit=3)

    return {
        "stock": stock,
        "tech": tech,
        "pe": pe,
        "pb": pb,
        "fin": fin,
        "news": latest_news,
        "web_items": web_items,
        "web_ctx": web_ctx,
        "image_url": _pick_result_image(latest_news, web_items),
    }


def _analysis_fact_markdown(snapshot):
    stock = snapshot["stock"]
    tech = snapshot["tech"]
    pe = snapshot["pe"]
    pb = snapshot["pb"]
    fin = snapshot["fin"]
    latest_news = snapshot["news"]
    web_items = snapshot["web_items"]
    web_ctx = snapshot["web_ctx"]

    lines = [
        f"## 已检索事实材料",
        "",
        "### 技术面",
        f"- 均线: {tech.get('ma_arrangement', '未知')}",
        f"- MACD: {tech.get('macd_status', '未知')}",
        f"- KDJ: {tech.get('kdj_status', '未知')}",
        "",
        "### 估值面",
        f"- PE: {_fmt_num(pe.get('current_pe'))}，状态: {pe.get('pe_status', '--')}",
        f"- PB: {_fmt_num(pb.get('current_pb'))}，状态: {pb.get('pb_status', '--')}",
        "",
        "### 财报面",
        f"- 摘要: {fin.get('summary', '暂无财报摘要')}",
        "",
        "### 站内资讯",
    ]

    if latest_news:
        for idx, item in enumerate(latest_news, 1):
            lines.append(
                f"{idx}. {item.get('title', '无标题')} | {item.get('publish_time', '--')} | "
                f"{_trim_text(item.get('ai_summary') or item.get('content', ''), 80)}"
            )
    else:
        lines.append("- 暂无站内资讯")

    lines.extend([
        "",
        "### 全网检索",
        f"- Tavily 摘要: {_trim_text(_safe_web_summary(web_ctx.get('summary', '暂无外部搜索结果')), 240)}",
    ])
    for idx, item in enumerate(web_items, 1):
        lines.append(
            f"{idx}. {item.get('title', '无标题')} | {item.get('source', '--')} | "
            f"{_trim_text(item.get('content', '') or item.get('summary', ''), 100)}"
        )

    lines.extend([
        "",
        f"### 标的",
        f"- 股票: {stock['name']} ({stock['code']})",
    ])
    return "\n".join(lines)


def _analysis_prompt(snapshot, use_mcp=False):
    stock = snapshot["stock"]
    material = _analysis_fact_markdown(snapshot)
    source_note = "并优先利用 Tavily / 数据库等检索结果" if use_mcp else "并结合已检索到的新闻与全网信息"
    return (
        f"你现在要输出一份专业的中文股票分析简报。请严格基于我提供的事实材料作答，"
        f"不要编造数据，{source_note}。\n\n"
        "输出要求：\n"
        "1. 必须使用 Markdown。\n"
        "2. 先给出 2-3 句结论，明确偏多/中性/偏空。\n"
        "3. 结构固定为：\n"
        "# 标题\n"
        "## 结论\n"
        "## 核心逻辑\n"
        "## 利多因素\n"
        "## 风险因素\n"
        "## 估值与基本面\n"
        "## 观察点与催化剂\n"
        "## 策略建议\n"
        "4. 重点解释指标代表的含义，不要机械堆数字。\n"
        "5. 不要反问，不要让用户自己再判断。\n"
        "6. 如果数据不足，明确写出“资料不足”。\n\n"
        f"标的：{stock['name']} ({stock['code']})\n\n"
        f"{material}"
    )


def _fallback_analysis_markdown(snapshot, ai_error=""):
    stock = snapshot["stock"]
    tech = snapshot["tech"]
    pe = snapshot["pe"]
    pb = snapshot["pb"]
    fin = snapshot["fin"]
    latest_news = snapshot["news"]
    web_ctx = snapshot["web_ctx"]

    score = 0
    if "多头" in tech.get("ma_arrangement", "") or "金叉" in tech.get("macd_status", ""):
        score += 1
    if "空头" in tech.get("ma_arrangement", ""):
        score -= 1
    current_pe = pe.get("current_pe", 0) or 0
    current_pb = pb.get("current_pb", 0) or 0
    if current_pe and current_pe < 15:
        score += 1
    if current_pb and current_pb < 1:
        score += 1
    if current_pb and current_pb > 5:
        score -= 1

    if score >= 2:
        verdict = "偏多，适合继续跟踪基本面兑现和估值修复。"
    elif score <= -1:
        verdict = "偏谨慎，除非有新增催化剂，否则不宜激进追高。"
    else:
        verdict = "中性偏观察，当前更像等待催化剂验证的阶段。"

    lines = [
        f"# {stock['name']} ({stock['code']}) 投资分析",
        "",
        "## 结论",
        verdict,
        "",
        "## 核心逻辑",
        f"- 技术面当前为“{tech.get('ma_arrangement', '未知')}”，MACD 状态为“{tech.get('macd_status', '未知')}”，说明短线趋势仍以节奏观察为主。",
        f"- 估值面 PE={_fmt_num(current_pe)}、PB={_fmt_num(current_pb)}，对应状态分别为“{pe.get('pe_status', '--')} / {pb.get('pb_status', '--')}”。",
        f"- 财报层面：{fin.get('summary', '暂无财报摘要')}。",
        "",
        "## 利多因素",
        f"- 若低估值能够持续，同时基本面没有明显恶化，存在估值修复空间。",
        f"- 全网检索摘要显示：{_trim_text(_safe_web_summary(web_ctx.get('summary', '暂无外部搜索结果')), 160)}",
        "",
        "## 风险因素",
        "- 当前结论基于公开数据摘要，不等于完整投研结论。",
        "- 若宏观环境、行业政策或资产质量发生变化，低估值可能长期维持。",
        "",
        "## 观察点与催化剂",
    ]
    if latest_news:
        for item in latest_news:
            lines.append(f"- {item.get('publish_time', '--')}：{item.get('title', '无标题')}")
    else:
        lines.append("- 暂无近期公开新闻催化剂。")

    lines.extend([
        "",
        "## 策略建议",
        "- 更适合把它当作跟踪型标的，而不是只凭单一指标立即重仓。",
    ])

    if ai_error:
        lines.extend([
            "",
            "## AI 状态",
            f"> ⚠️ AI 接口调用失败：{ai_error}",
            "> 已返回基于本地检索材料生成的备用分析稿，请检查 .env 中 AI API 地址、密钥和模型配置。",
        ])
    return "\n".join(lines)


def _generate_ai_analysis_markdown(snapshot, ctx=None, use_mcp=False):
    stock = snapshot["stock"]
    prompt = _analysis_prompt(snapshot, use_mcp=use_mcp)
    session_id = None
    if ctx:
        session_id = f"bot-analyze:{ctx['user_id']}:{stock['code']}"

    content = ""
    error = ""
    for chunk in ai_chat.chat_with_role("judge", prompt, stock["code"], {}, session_id=session_id):
        if "content" in chunk:
            content = chunk["content"]
        elif "error" in chunk:
            error = chunk["error"]
            break

    if content:
        return content, ""
    return "", error or "AI 未返回有效内容"


def _cmd_analyze_with_context(arg, ctx, use_mcp=False):
    stock, err = _resolve_stock(arg)
    if err:
        return _text_response(err)

    snapshot = _build_analysis_snapshot(stock)
    if snapshot.get("error"):
        return _text_response(snapshot["error"])

    ai_markdown, ai_error = _generate_ai_analysis_markdown(snapshot, ctx=ctx, use_mcp=use_mcp)
    text = ai_markdown or _fallback_analysis_markdown(snapshot, ai_error=ai_error)
    image_url = snapshot.get("image_url", "") if ctx and _graph_enabled(ctx) else ""
    return _markdown_response(
        title=f"{stock['name']} {stock['code']} 投资分析",
        text=text,
        image_url=image_url,
    )


def _cmd_search(arg, ctx=None):
    stock, err = _resolve_stock(arg)
    if err:
        return _text_response(err)

    result = web_search.search_stock_web(
        stock["code"],
        keyword=arg.strip() or None,
        limit=5,
        topic="finance",
        time_range="month",
        include_answer=True,
        include_images=True,
    )
    items = (result.get("results") or [])[:5]
    if not items:
        return _text_response(f"{stock['code']} 暂无全网搜索结果。")

    lines = [
        f"# {stock['name']} {stock['code']} 全网搜索",
        "",
    ]
    answer = (result.get("answer") or "").strip()
    if answer:
        lines.append("## Tavily 摘要")
        lines.append(answer)
        lines.append("")

    lines.append("## 搜索结果")
    for idx, item in enumerate(items, 1):
        lines.append(f"{idx}. **{item.get('title', '无标题')}**")
        lines.append(f"   - 来源: {item.get('source', '--')}")
        if item.get("published_date"):
            lines.append(f"   - 时间: {item.get('published_date')}")
        lines.append(f"   - 摘要: {_trim_text(item.get('content', '') or item.get('summary', ''), 120)}")
        if item.get("url"):
            lines.append(f"   - 链接: {item.get('url')}")

    image_url = result.get("images", [None])[0] if ctx and _graph_enabled(ctx) else ""
    return _markdown_response(
        title=f"{stock['name']} {stock['code']} 搜索",
        text="\n".join(lines),
        image_url=image_url or "",
    )


def _cmd_graph(args, ctx):
    if not args:
        enabled = _graph_enabled(ctx)
        return _text_response(f"当前图文模式: {'on' if enabled else 'off'}")

    mode = args[0].strip().lower()
    if mode not in ("on", "off"):
        return _text_response("用法: #GRAPH on 或 #GRAPH off")

    enabled = mode == "on"
    _save_user_preferences(ctx, graph_enabled=enabled)
    message = (
        "已开启图文模式。后续支持 Markdown 的命令会优先以 Markdown 回复；"
        "若存在可用图片链接，会一并附带。"
        if enabled else
        "已关闭图文模式。后续命令将优先返回纯文本/纯 Markdown 内容，不附带图片。"
    )
    return _text_response(message)


def _cmd_buy_sell(cmd, args, ctx):
    if len(args) < 3:
        return f"用法: #{cmd} 股票代码 数量 价格"

    profile = _get_broker_profile(ctx)
    if not profile:
        return _text_response("未完成券商配置，请先发送 #CONFIG 券商名称，例如：#CONFIG 浙商证券")

    symbol, qty, price = args[0], args[1], args[2]
    action = "买入" if cmd == "BUY" else "卖出"
    return _text_response(
        f"已收到交易指令：{action} {symbol} {qty} 股 @ {price}\n"
        f"当前券商: {profile.get('broker_name', '--')} / 账户: {profile.get('account_masked', '--')}\n"
        "当前版本仅完成配置与指令解析，尚未接入真实交易服务商。"
    )


def _cmd_config_view(ctx):
    session = _get_session(ctx)
    profile = _get_broker_profile(ctx)

    lines = []
    if profile:
        lines.append("当前已保存券商配置：")
        lines.append(f"- 券商: {profile.get('broker_name', '--')}")
        lines.append(f"- 账号: {profile.get('account_masked', '--')}")
        lines.append(f"- 更新时间: {profile.get('updated_at', '--')}")
    else:
        lines.append("当前没有已保存的券商配置。")

    if session:
        lines.append("")
        lines.append("当前存在进行中的配置流程：")
        lines.append(f"- 阶段: {session.get('state', '--')}")
        if session.get("broker_name"):
            lines.append(f"- 券商: {session.get('broker_name')}")
        lines.append(f"- 剩余时间: {_remaining_seconds(session)}s")
        lines.append("可发送 #q 或 #CANCEL 取消当前流程。")

    return _text_response("\n".join(lines))


def _start_config_flow(ctx, broker_name):
    broker_name = (broker_name or "").strip()
    if broker_name:
        _save_session(
            ctx,
            {
                "active": True,
                "state": "awaiting_account",
                "broker_name": broker_name,
            },
        )
        return _text_response(
            f"开始 {broker_name} 登录流程配置。\n"
            f"请在 {LOGIN_FLOW_TIMEOUT_SECONDS}s 内直接回复券商账号。\n"
            "发送 #q 可退出流程。"
        )

    _save_session(
        ctx,
        {
            "active": True,
            "state": "awaiting_broker",
        },
    )
    return _text_response(
        "开始券商登录流程配置。\n"
        f"请在 {LOGIN_FLOW_TIMEOUT_SECONDS}s 内直接回复券商名称，例如：浙商证券。\n"
        "发送 #q 可退出流程。"
    )


def _cmd_config(arg, ctx):
    raw = (arg or "").strip()
    if raw in ("查看", "status", "STATUS", "查询"):
        return _cmd_config_view(ctx)
    return _start_config_flow(ctx, raw)


def _cmd_cancel(ctx):
    session = _get_session(ctx)
    if not session:
        return _text_response("当前没有进行中的流程。")
    _clear_session(ctx)
    return _text_response("已取消当前流程。")


def _cmd_index(args):
    keyword = " ".join(args).strip()
    rows = stock_data.fetch_market_indices()
    if not rows:
        return _text_response("暂无指数数据。")

    if not keyword:
        top = rows[:8]
        lines = ["指数列表："]
        for item in top:
            sign = "+" if item.get("change_pct", 0) >= 0 else ""
            lines.append(f"- {item.get('name')}: {item.get('last_done')} ({sign}{item.get('change_pct')}%)")
        return _markdown_response("指数列表", "\n".join([f"# 指数列表", ""] + lines[1:]))

    for item in rows:
        name = str(item.get("name", ""))
        cid = str(item.get("counter_id", ""))
        if keyword in name or keyword.upper() in cid.upper():
            sign = "+" if item.get("change_pct", 0) >= 0 else ""
            return _markdown_response(
                f"{name} 指数",
                f"【{name}】\n"
                f"最新: {item.get('last_done')}\n"
                f"涨跌: {item.get('change')} ({sign}{item.get('change_pct')}%)\n"
                f"代码: {cid}"
            )
    return _text_response(f"未找到指数：{keyword}")


def _cmd_new_stock():
    try:
        import akshare as ak
    except Exception:
        return _text_response("未安装 akshare，无法查询新股。")

    try:
        df = ak.stock_xgsglb_em()
        if df is None or df.empty:
            return _text_response("今日无可申购新股。")

        date_col = None
        for col in df.columns:
            if "申购" in col and "日" in col:
                date_col = col
                break

        today = datetime.now().strftime("%Y-%m-%d")
        rows = df
        if date_col:
            rows = df[df[date_col].astype(str).str.contains(today, na=False)]
        if rows.empty:
            return _text_response("今日无可申购新股。")

        rows = rows.head(8)
        lines = ["今日可申购新股："]
        for _, row in rows.iterrows():
            name = str(row.get("股票简称", row.get("证券简称", row.get("名称", ""))))
            code = str(row.get("股票代码", row.get("证券代码", row.get("代码", ""))))
            lines.append(f"- {name}({code})")
        return _markdown_response("今日新股", "\n".join([f"# 今日可申购新股", ""] + [f"- {line[2:]}" for line in lines[1:]]))
    except Exception as e:
        return _text_response(f"查询新股失败：{e}")


def _resume_session(session, text, ctx):
    state = session.get("state")
    value = (text or "").strip()

    if state == "awaiting_broker":
        if not value:
            return _text_response("券商名称不能为空，请重新输入。")
        _save_session(
            ctx,
            {
                "active": True,
                "state": "awaiting_account",
                "broker_name": value,
                "created_at": session.get("created_at", _now_iso()),
            },
        )
        return _text_response(
            f"已记录券商：{value}\n"
            f"请在 {LOGIN_FLOW_TIMEOUT_SECONDS}s 内直接回复券商账号。\n"
            "发送 #q 可退出流程。"
        )

    if state == "awaiting_account":
        if not value:
            return _text_response("券商账号不能为空，请重新输入。")
        _save_session(
            ctx,
            {
                "active": True,
                "state": "awaiting_password",
                "broker_name": session.get("broker_name", ""),
                "account": value,
                "created_at": session.get("created_at", _now_iso()),
            },
        )
        return _text_response(
            f"已记录账号：{value[:2]}***{value[-2:] if len(value) > 4 else value}\n"
            f"请在 {LOGIN_FLOW_TIMEOUT_SECONDS}s 内直接回复 b64 编码后的密码。\n"
            "发送 #q 可退出流程。"
        )

    if state == "awaiting_password":
        if not value:
            return _text_response("密码不能为空，请重新输入 b64 编码后的密码。")
        try:
            base64.b64decode(value.encode("utf-8"), validate=True)
        except Exception:
            return _text_response("密码不是合法的 b64 编码，请重新发送。")

        profile = _save_broker_profile(
            ctx,
            session.get("broker_name", ""),
            session.get("account", ""),
            value,
        )
        _clear_session(ctx)
        return _markdown_response(
            "券商配置完成",
            "券商配置已完成。\n"
            f"- 券商: {profile.get('broker_name', '--')}\n"
            f"- 账号: {profile.get('account_masked', '--')}\n"
            "- 密码: 已保存为 b64 密文\n"
            "当前版本仅完成配置存储，尚未接入真实登录。"
        )

    _clear_session(ctx)
    return _text_response("流程状态异常，已自动清理。请重新发送 #CONFIG。")


def handle_skill_command(text, context=None):
    """
    解析并执行 #xxx 指令
    """
    ctx = _normalize_context(context)
    msg = (text or "").strip()
    if _is_help_message(msg):
        return _markdown_response("Q-Limit 帮助", _help_text())
    if not msg.startswith("#"):
        return _text_response("请发送 #命令。可发送 #HELP 查看命令列表。")

    body = msg[1:].strip()
    if not body:
        return _markdown_response("Q-Limit 帮助", _help_text())

    cmd, _, tail = body.partition(" ")
    raw_cmd = cmd.strip()
    cmd_upper = raw_cmd.upper()
    args = tail.strip().split() if tail.strip() else []

    if cmd_upper in ("HELP",) or raw_cmd in ("help", "帮助"):
        return _markdown_response("Q-Limit 帮助", _help_text())
    if cmd_upper in ("CANCEL", "Q"):
        return _cmd_cancel(ctx)
    if cmd_upper == "PRICE":
        return _text_response(_cmd_price(tail.strip()))
    if cmd_upper == "KDJ":
        return _text_response(_cmd_kdj(tail.strip()))
    if cmd_upper == "NEWS":
        return _cmd_news_with_context(tail.strip(), ctx)
    if cmd_upper in ("BUY", "SELL"):
        return _cmd_buy_sell(cmd_upper, args, ctx)
    if cmd_upper == "MA10":
        return _text_response(_cmd_ma10(tail.strip()))
    if cmd_upper == "CONFIG":
        return _cmd_config(tail.strip(), ctx)
    if cmd_upper == "ANALYZE":
        return _cmd_analyze_with_context(tail.strip(), ctx, use_mcp=False)
    if cmd_upper == "ANALYZE_MCP":
        cleaned_tail = tail.strip()
        if cleaned_tail.upper().startswith("[MCP_TOOL"):
            closing = cleaned_tail.find("]")
            if closing >= 0:
                cleaned_tail = cleaned_tail[closing + 1:].strip()
        return _cmd_analyze_with_context(cleaned_tail, ctx, use_mcp=True)
    if cmd_upper in ("T1", "T7", "T30", "T365"):
        return _text_response(_cmd_t_days(tail.strip(), int(cmd_upper[1:])))
    if cmd_upper == "GRAPH":
        return _cmd_graph(args, ctx)
    if cmd_upper == "SEARCH":
        return _cmd_search(tail.strip(), ctx)
    if raw_cmd == "指数":
        return _cmd_index(args)
    if raw_cmd == "新股":
        return _cmd_new_stock()

    return _text_response(f"未识别命令: #{raw_cmd}\n\n{_help_text()}")


def handle_incoming_message(text, context=None):
    """
    统一处理机器人消息：
    - 会话中允许继续发送非 # 文本
    - 非会话状态下只处理 # 命令
    返回: {"handled": bool, "reply": str}
    """
    ctx = _normalize_context(context)
    message = (text or "").strip()
    session = _get_session(ctx)

    if _is_help_message(message):
        payload = _markdown_response("Q-Limit 帮助", _help_text())
        return {"handled": True, "reply": payload.get("text", ""), "reply_payload": payload}

    if session and _is_session_expired(session):
        _clear_session(ctx)
        if not message.startswith("#"):
            payload = _text_response(f"登录流程已超时（{LOGIN_FLOW_TIMEOUT_SECONDS}s），已自动退出。请重新发送 #CONFIG 开始。")
            return {
                "handled": True,
                "reply": payload["text"],
                "reply_payload": payload,
            }
        session = None

    if session and message and not message.startswith("#"):
        payload = _normalize_reply_payload(_resume_session(session, message, ctx))
        return {
            "handled": True,
            "reply": payload.get("text", ""),
            "reply_payload": payload,
        }

    if session and message.startswith("#"):
        upper = message.strip().upper()
        if upper == "#HELP":
            payload = _markdown_response("Q-Limit 帮助", _help_text())
            return {
                "handled": True,
                "reply": payload.get("text", ""),
                "reply_payload": payload,
            }
        if upper in ("#Q", "#CANCEL"):
            payload = _normalize_reply_payload(_cmd_cancel(ctx))
            return {
                "handled": True,
                "reply": payload.get("text", ""),
                "reply_payload": payload,
            }
        if upper.startswith("#CONFIG"):
            payload = _normalize_reply_payload(_cmd_config(message.strip()[len("#CONFIG"):].strip(), ctx))
            return {
                "handled": True,
                "reply": payload.get("text", ""),
                "reply_payload": payload,
            }
        payload = _text_response(
            f"当前有进行中的登录流程，请在 {LOGIN_FLOW_TIMEOUT_SECONDS}s 内继续回复。\n"
            "发送 #q 可退出当前流程。"
        )
        return {
            "handled": True,
            "reply": payload.get("text", ""),
            "reply_payload": payload,
        }

    if not message.startswith("#"):
        return {"handled": False, "reply": ""}

    payload = _normalize_reply_payload(handle_skill_command(message, ctx))
    return {
        "handled": True,
        "reply": payload.get("text", ""),
        "reply_payload": payload,
    }
