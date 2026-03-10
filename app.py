"""
股票分析平台 - Flask 主应用
"""
import json
from flask import Flask, render_template, request, jsonify, Response, stream_with_context
from config import FLASK_HOST, FLASK_PORT, FLASK_DEBUG, SECRET_KEY
from services import stock_data, analysis, valuation, news, financial, ai_chat, web_search, notifier
from services import bot_skills, dingtalk_robot
from services.pattern_detector import analyze_patterns

app = Flask(__name__)
app.secret_key = SECRET_KEY


# ============================================================
# 页面路由
# ============================================================

@app.route("/")
def index():
    """主页"""
    return render_template("index.html")


# ============================================================
# 股票数据 API
# ============================================================

@app.route("/api/stock/search")
def api_stock_search():
    """搜索股票"""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "请输入搜索关键词"}), 400
    results = stock_data.search_stock(q)
    return jsonify({"data": results})


@app.route("/api/market/indices")
def api_market_indices():
    """获取大盘指数行情"""
    data = stock_data.fetch_market_indices()
    return jsonify({"data": data})


@app.route("/api/stock/<code>/kline")
def api_stock_kline(code):
    """获取K线数据（走长桥 API）"""
    period = request.args.get("period", "daily")
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    adjust = request.args.get("adjust", "qfq")
    market = request.args.get("market", "US")
    product = request.args.get("product", "ST")
    kline_session = request.args.get("kline_session", "101")
    data = stock_data.fetch_kline(
        code, period, start or None, end or None, adjust,
        market=market, product=product, kline_session=kline_session
    )
    return jsonify({"data": data})


@app.route("/api/stock/<code>/info")
def api_stock_info(code):
    """获取股票基本信息"""
    info = stock_data.fetch_stock_info(code)
    return jsonify({"data": info})


@app.route("/api/stock/<code>/detail")
def api_stock_detail(code):
    """获取股票实时详情（走长桥 API）"""
    market = request.args.get("market", "US")
    product = request.args.get("product", "ST")
    data = stock_data.fetch_stock_detail(code, market=market, product=product)
    return jsonify({"data": data})


@app.route("/api/stock/<code>/company-info")
def api_company_info(code):
    """获取公司简介（SQLite 缓存优先）"""
    market = request.args.get("market", "US")
    product = request.args.get("product", "ST")
    data = stock_data.fetch_company_info(code, market=market, product=product)
    return jsonify({"data": data})


@app.route("/api/stock/<code>/actions")
def api_company_actions(code):
    """获取公司日程/公告（分红拆股等）"""
    market = request.args.get("market", "US")
    product = request.args.get("product", "ST")
    data = stock_data.fetch_company_actions(code, market=market, product=product)
    return jsonify({"data": data})


# ============================================================
# 技术分析 API
# ============================================================

@app.route("/api/stock/<code>/analysis")
def api_stock_analysis(code):
    """获取压力位/支撑位分析"""
    period = request.args.get("period", "daily")
    market = request.args.get("market", "US")
    product = request.args.get("product", "ST")
    sr = analysis.calc_support_resistance(code, period=period, market=market, product=product)
    ma = analysis.calc_moving_averages(code, market=market, product=product)
    macd = analysis.calc_macd(code, market=market, product=product)
    kdj = analysis.calc_kdj(code, market=market, product=product)

    return jsonify({
        "data": {
            "support_resistance": sr,
            "moving_averages": ma,
            "macd": macd,
            "kdj": kdj,
        }
    })


@app.route("/api/stock/<code>/technical")
def api_stock_technical(code):
    """获取技术指标概要"""
    market = request.args.get("market", "US")
    product = request.args.get("product", "ST")
    summary = analysis.get_technical_summary(code, market=market, product=product)
    return jsonify({"data": summary})


@app.route("/api/stock/<code>/patterns")
def api_stock_patterns(code):
    """K线形态 + 趋势结构 + 假突破预警"""
    period = request.args.get("period", "daily")
    market = request.args.get("market", "US")
    product = request.args.get("product", "ST")
    data = analyze_patterns(code, period=period, market=market, product=product)
    return jsonify({"data": data})


# ============================================================
# 估值分析 API
# ============================================================

@app.route("/api/stock/<code>/valuation")
def api_stock_valuation(code):
    """获取PE/PB估值分析"""
    market = request.args.get("market", "US")
    product = request.args.get("product", "ST")
    pe = valuation.get_pe_analysis(code, market=market, product=product)
    pb = valuation.get_pb_analysis(code, market=market, product=product)

    result = {"pe": pe, "pb": pb}

    # PE/PB 都无数据时，返回 ETF 专属分析
    if (not pe.get("current_pe") or pe["current_pe"] <= 0) and \
       (not pb.get("current_pb") or pb["current_pb"] <= 0):
        etf = valuation.get_etf_analysis(code, market=market, product=product)
        if etf:
            result["etf_analysis"] = etf
    else:
        # 有 PE/PB 时给出投资建议
        advice = valuation.get_stock_advice(pe, pb)
        if advice:
            result["stock_advice"] = advice

    return jsonify({"data": result})


# ============================================================
# 资讯 API
# ============================================================

@app.route("/api/stock/<code>/news")
def api_stock_news(code):
    """获取个股资讯（走长桥 API）"""
    limit = request.args.get("limit", 20, type=int)
    market = request.args.get("market", "US")
    product = request.args.get("product", "ST")
    data = news.fetch_news(code, limit, market=market, product=product)
    return jsonify({"data": data})


@app.route("/api/news/search")
def api_news_search():
    """搜索资讯"""
    keyword = request.args.get("q", "")
    stock_code = request.args.get("stock_code", "")
    limit = request.args.get("limit", 50, type=int)
    data = news.search_news(keyword=keyword or None, stock_code=stock_code or None, limit=limit)
    return jsonify({"data": data})


# ============================================================
# 全网搜索 API（Tavily）
# ============================================================


def _query_bool(name):
    raw = request.args.get(name)
    if raw is None or str(raw).strip() == "":
        return None
    value = str(raw).strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return None


def _query_mode(name):
    raw = request.args.get(name)
    if raw is None or str(raw).strip() == "":
        return None
    value = str(raw).strip()
    lowered = value.lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    return value


def _query_list(name):
    values = request.args.getlist(name)
    if not values:
        raw = request.args.get(name, "")
        values = [raw] if raw else []

    result = []
    for item in values:
        for part in str(item).split(","):
            cleaned = part.strip()
            if cleaned:
                result.append(cleaned)
    return result


def _tavily_error_status(error):
    return 400 if error in ("搜索关键词不能为空", "未配置 TAVILY_API_KEY", "stock_code 不能为空") else 502


@app.route("/api/stock/<code>/web-search")
def api_stock_web_search(code):
    """股票维度的 Tavily 全网搜索"""
    data = web_search.search_stock_web(
        code,
        keyword=request.args.get("q", "").strip() or None,
        limit=request.args.get("limit", 6, type=int),
        topic=request.args.get("topic", "finance"),
        search_depth=request.args.get("search_depth", "").strip() or None,
        time_range=request.args.get("time_range", "").strip() or None,
        start_date=request.args.get("start_date", "").strip() or None,
        end_date=request.args.get("end_date", "").strip() or None,
        days=request.args.get("days", type=int),
        include_answer=_query_mode("include_answer"),
        include_raw_content=_query_mode("include_raw_content"),
        include_images=_query_bool("include_images"),
        include_image_descriptions=_query_bool("include_image_descriptions"),
        include_favicon=_query_bool("include_favicon"),
        include_domains=_query_list("include_domains"),
        exclude_domains=_query_list("exclude_domains"),
        country=request.args.get("country", "").strip() or None,
        auto_parameters=_query_bool("auto_parameters"),
        chunks_per_source=request.args.get("chunks_per_source", type=int),
        use_cache=_query_bool("use_cache"),
    )
    if data.get("error"):
        return jsonify({"error": data["error"], "data": data}), _tavily_error_status(data["error"])
    return jsonify({"data": data})


@app.route("/api/search/web")
def api_web_search():
    """全网搜索"""
    query = request.args.get("q", "").strip()
    topic = request.args.get("topic", "news")
    limit = request.args.get("limit", 5, type=int)
    data = web_search.search_web(
        query=query,
        max_results=limit,
        topic=topic,
        search_depth=request.args.get("search_depth", "").strip() or None,
        time_range=request.args.get("time_range", "").strip() or None,
        start_date=request.args.get("start_date", "").strip() or None,
        end_date=request.args.get("end_date", "").strip() or None,
        days=request.args.get("days", type=int),
        include_answer=_query_mode("include_answer"),
        include_raw_content=_query_mode("include_raw_content"),
        include_images=_query_bool("include_images"),
        include_image_descriptions=_query_bool("include_image_descriptions"),
        include_favicon=_query_bool("include_favicon"),
        include_domains=_query_list("include_domains"),
        exclude_domains=_query_list("exclude_domains"),
        country=request.args.get("country", "").strip() or None,
        auto_parameters=_query_bool("auto_parameters"),
        chunks_per_source=request.args.get("chunks_per_source", type=int),
        use_cache=_query_bool("use_cache"),
    )
    if data.get("error"):
        return jsonify({"error": data["error"], "data": data}), _tavily_error_status(data["error"])
    return jsonify({"data": data})


# ============================================================
# 财报 API
# ============================================================

@app.route("/api/stock/<code>/financial")
def api_stock_financial(code):
    """获取财报分析"""
    market = request.args.get("market", "US")
    product = request.args.get("product", "ST")
    data = financial.get_financial_report(code, market=market, product=product)
    return jsonify({"data": data})


# ============================================================
# AI 聊天 API
# ============================================================

@app.route("/api/chat", methods=["POST"])
def api_chat():
    """
    AI 单角色对话
    Body: {"role": "bull", "message": "分析一下", "stock_code": "600519", "api_config": {...}}
    """
    body = request.get_json()
    role = body.get("role", "bull")
    message = body.get("message", "")
    stock_code = body.get("stock_code", "")
    session_id = body.get("session_id", "")
    api_config = body.get("api_config", {})

    if not message:
        return jsonify({"error": "消息不能为空"}), 400

    def generate():
        for chunk in ai_chat.chat_with_role(role, message, stock_code, api_config, session_id):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/chat/debate", methods=["POST"])
def api_chat_debate():
    """
    一键辩论
    Body: {"stock_code": "600519", "prompt": "全面分析", "api_config": {...}}
    """
    body = request.get_json()
    stock_code = body.get("stock_code", "")
    prompt = body.get("prompt", "")
    session_id = body.get("session_id", "")
    api_config = body.get("api_config", {})

    if not stock_code:
        return jsonify({"error": "请先选择股票"}), 400

    def generate():
        for chunk in ai_chat.debate(stock_code, api_config, session_id, prompt):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/chat/history")
def api_chat_history():
    """获取聊天历史"""
    stock_code = request.args.get("stock_code", "")
    session_id = request.args.get("session_id", "")
    limit = request.args.get("limit", 50, type=int)
    data = ai_chat.get_chat_history(stock_code, session_id or None, limit)
    return jsonify({"data": data})


# ============================================================
# AI 模型配置 API
# ============================================================

@app.route("/api/ai/models")
def api_ai_models():
    """获取 AI 模型配置"""
    return jsonify({"data": ai_chat.get_model_configs()})


@app.route("/api/ai/models/<role>", methods=["PUT"])
def api_ai_model_update(role):
    """更新 AI 模型配置"""
    body = request.get_json()
    success = ai_chat.update_model_config(role, body)
    if success:
        return jsonify({"message": "配置更新成功"})
    return jsonify({"error": f"未知角色: {role}"}), 400


# ============================================================
# 钉钉通知 API
# ============================================================

@app.route("/api/notify/dingtalk", methods=["POST"])
def api_notify_dingtalk():
    """
    钉钉通知
    Body:
    {
      "msgtype": "text|markdown",
      "content": "text消息内容",
      "title": "markdown标题",
      "text": "markdown正文",
      "at_mobiles": ["138xxxx"],
      "is_at_all": false
    }
    """
    body = request.get_json() or {}
    msgtype = (body.get("msgtype") or "text").lower()
    at_mobiles = body.get("at_mobiles")
    is_at_all = bool(body.get("is_at_all", False))

    if msgtype == "markdown":
        title = body.get("title", "Q-Limit 通知")
        text = body.get("text", "")
        if not text:
            return jsonify({"error": "markdown 消息 text 不能为空"}), 400
        result = notifier.send_markdown(
            title=title,
            text=text,
            at_mobiles=at_mobiles,
            is_at_all=is_at_all,
        )
    else:
        content = body.get("content", "")
        if not content:
            return jsonify({"error": "text 消息 content 不能为空"}), 400
        result = notifier.send_text(
            content=content,
            at_mobiles=at_mobiles,
            is_at_all=is_at_all,
        )

    if result.get("ok"):
        return jsonify({"message": "发送成功", "data": result})
    return jsonify({"error": result.get("error", "发送失败"), "data": result}), 400


# ============================================================
# 机器人技能命令 API（本地调试）
# ============================================================

@app.route("/api/bot/command", methods=["POST"])
def api_bot_command():
    """
    本地调试 #技能指令
    Body: {"text": "#PRICE 601988", "user_id": "u1", "conversation_id": "c1"}
    """
    body = request.get_json() or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text 不能为空"}), 400

    context = {
        "user_id": body.get("user_id") or "debug-user",
        "conversation_id": body.get("conversation_id") or "debug-conversation",
        "sender_nick": body.get("sender_nick") or "debug",
    }
    result = bot_skills.handle_incoming_message(text, context)
    return jsonify({
        "data": {
            "request": text,
            "handled": result.get("handled", False),
            "reply": result.get("reply", ""),
            "reply_payload": result.get("reply_payload", {}),
            "context": context,
        }
    })


@app.route("/api/bot/status")
def api_bot_status():
    """查看钉钉 Stream 机器人状态"""
    return jsonify({"data": dingtalk_robot.get_stream_status()})


# ============================================================
# 启动
# ============================================================

if __name__ == "__main__":
    ok, msg = dingtalk_robot.start_dingtalk_stream_bot()
    print(f"  🤖 钉钉机器人: {msg}")
    print("=" * 50)
    print("  📊 股票分析平台启动")
    print(f"  🌐 http://localhost:{FLASK_PORT}")
    print("=" * 50)
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)
