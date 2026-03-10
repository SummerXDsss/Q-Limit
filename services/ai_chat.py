"""
AI 多角色辩论聊天服务
支持 Tool Calling，优先使用前端传入配置，缺省时回退到 .env
"""
import json
import requests
from datetime import datetime
from config import (
    AI_MODELS,
    AI_ROLE_DEFAULT_CONFIGS,
    AI_REQUEST_TIMEOUT_SECONDS,
    AI_TOOLS,
    ENABLE_WEB_SEARCH_CONTEXT,
    DINGTALK_NOTIFY_ON_DEBATE,
)
from models.database import get_collection

# Tool Calling 函数映射
from services.analysis import get_technical_summary, calc_support_resistance
from services.valuation import get_valuation_summary
from services.news import get_news_for_ai
from services.financial import get_financial_report
from services.web_search import get_web_search_for_ai
from services.notifier import send_debate_result

TOOL_FUNCTIONS = {
    "get_pe_analysis": lambda args: get_valuation_summary(args["stock_code"]),
    "get_support_resistance": lambda args: calc_support_resistance(args["stock_code"]),
    "get_technical_indicators": lambda args: get_technical_summary(args["stock_code"]),
    "get_stock_news": lambda args: get_news_for_ai(args["stock_code"], args.get("limit", 10)),
    "get_financial_report": lambda args: get_financial_report(args["stock_code"]),
    "get_web_search": lambda args: get_web_search_for_ai(args["stock_code"], args.get("limit", 5)),
    "get_kline_summary": lambda args: _get_kline_summary(args["stock_code"], args.get("days", 30)),
}


def chat_with_role(role, message, stock_code, api_config, session_id=None):
    """
    与指定角色对话（纯聊天模式，适配 Web2API 中转）
    api_config: {"api_key": "xxx", "base_url": "http://...", "model": "gemini-3.0-flash"}
    """
    role_meta = AI_MODELS.get(role)
    if not role_meta:
        yield {"error": f"未知角色: {role}"}
        return

    llm_config = _resolve_llm_config(role, api_config)
    api_key = llm_config.get("api_key", "")
    base_url = llm_config.get("base_url", "")
    model = llm_config.get("model", "")

    if not api_key or not base_url or not model:
        yield {"error": "请先在 .env 或设置面板中配置 API Key、API 地址和模型。"}
        return

    messages = _build_messages(role, message, stock_code, session_id)

    # 纯Web2API聊天模式，不传递 tools，防止解析错误
    response_data = _call_llm(llm_config, messages, tools=None)
    
    if not response_data:
        yield {"error": "AI 接口调用失败，请检查 API 地址和密钥"}
        return

    choice = response_data.get("choices", [{}])[0]
    msg = choice.get("message", {})
    
    content = msg.get("content", "")
    if content:
        yield {"content": content, "role": role}
        _save_chat(stock_code, session_id, role, message, content)
    else:
        yield {"error": "AI 未返回有效内容"}


def debate(stock_code, api_config, session_id=None, user_prompt=None):
    """一键辩论：多头→空头→裁判"""
    prompt = user_prompt or f"请对股票 {stock_code} 进行全面分析"
    all_role_configs = api_config if isinstance(api_config, dict) else {}

    for role in ["bull", "bear", "judge"]:
        yield {"role": role, "status": "thinking", "content": ""}

        role_prompt = prompt
        if role == "judge":
            role_prompt = f"请综合前面多头和空头的分析，对股票 {stock_code} 给出裁判意见。\n\n用户原始问题: {prompt}"

        # 从传递的大 api_config 对象中提取当前角色的配置
        role_config = all_role_configs.get(role, {})

        full_content = ""
        for chunk in chat_with_role(role, role_prompt, stock_code, role_config, session_id):
            if "content" in chunk:
                full_content = chunk["content"]
            elif "tool_call" in chunk:
                yield {"role": role, "status": "calling_tool", "tool": chunk["tool_call"]}
            elif "error" in chunk:
                yield {"role": role, "status": "error", "content": chunk["error"]}
                break

        if full_content:
            if role == "judge" and DINGTALK_NOTIFY_ON_DEBATE:
                notify_res = send_debate_result(stock_code, prompt, full_content)
                if not notify_res.get("ok"):
                    print(f"[ai_chat] 钉钉通知发送失败: {notify_res.get('error')}")
            yield {"role": role, "status": "done", "content": full_content}


def get_chat_history(stock_code, session_id=None, limit=50):
    col = get_collection("chat_history")
    if col is None:
        return []
    query = {"stock_code": stock_code}
    if session_id:
        query["session_id"] = session_id
    return list(col.find(query, {"_id": 0}).sort("created_at", -1).limit(limit))


def get_model_configs():
    """获取角色元数据与 .env 默认配置摘要"""
    configs = {}
    for role, cfg in AI_MODELS.items():
        env_default = cfg.get("default_api_config", {})
        configs[role] = {
            "name": cfg["name"],
            "icon": cfg["icon"],
            "color": cfg["color"],
            "env_default": {
                "base_url": env_default.get("base_url", ""),
                "model": env_default.get("model", ""),
                "has_api_key": bool(env_default.get("api_key")),
            },
        }
    return configs


def _resolve_llm_config(role, api_config):
    env_default = AI_ROLE_DEFAULT_CONFIGS.get(role, {})
    req = api_config if isinstance(api_config, dict) else {}
    return {
        "api_key": req.get("api_key") or env_default.get("api_key", ""),
        "base_url": req.get("base_url") or env_default.get("base_url", ""),
        "model": req.get("model") or env_default.get("model", ""),
        "timeout": AI_REQUEST_TIMEOUT_SECONDS,
    }


# ============================================================
# 内部函数
# ============================================================

def _build_background_context(stock_code):
    """为纯聊天模型自动在后台抓取当前股票的各项数据指标，拼凑为纯文本上下文"""
    try:
        from services.analysis import get_technical_summary
        from services.valuation import get_valuation_summary
        from services.news import get_news_for_ai
        from services.web_search import get_web_search_for_ai
        
        kline = _get_kline_summary(stock_code, 30)
        tech = get_technical_summary(stock_code)
        val = get_valuation_summary(stock_code)
        news = get_news_for_ai(stock_code, limit=3)
        web_ctx = get_web_search_for_ai(stock_code, limit=3) if ENABLE_WEB_SEARCH_CONTEXT else None
        
        ctx = "【系统自动为您检索到的这只股票当前最新数据参考】\n"
        ctx += f"- 近期走势: {kline.get('summary', '无')}\n"
        if tech:
            ctx += (
                f"- 技术面: MACD({tech.get('macd_status', '无')}), "
                f"KDJ({tech.get('kdj_status', '无')}), "
                f"均线({tech.get('ma_arrangement', '无')})\n"
            )
        if "description" in val:
            ctx += f"- 估值面: {val['description']}\n"
        elif "pe" in val:
            ctx += f"- 估值面: 当前PE {val['pe']}, 历史分位 {val.get('pe_percentile', '未知')}%\n"
        
        if news and isinstance(news, dict) and len(news.get("news", [])) > 0:
            titles = [n.get("title", "") for n in news.get("news", []) if n.get("title")]
            ctx += f"- 最新动态: {' | '.join(titles)}\n"

        if web_ctx and web_ctx.get("summary"):
            ctx += f"- 全网检索: {web_ctx['summary']}\n"
            
        return ctx
    except Exception as e:
        print(f"[AI Chat Context Error] {e}")
        return "【系统提示：当前这只股票后台数据获取失败，可以直接依常识或默认回复】"

def _build_messages(role, user_message, stock_code, session_id):
    role_meta = AI_MODELS[role]
    messages = [
        {"role": "system", "content": role_meta["system_prompt"]},
    ]
    if session_id:
        history = get_chat_history(stock_code, session_id, limit=10)
        for h in reversed(history):
            messages.append({"role": "user", "content": h.get("user_message", "")})
            messages.append({"role": "assistant", "content": h.get("ai_response", "")})

    user_msg = user_message
    if stock_code:
        bg_info = _build_background_context(stock_code)
        user_msg = f"[当前分析股票代码: {stock_code}]\n\n{bg_info}\n\n[用户的提问]:\n{user_message}"
        
    messages.append({"role": "user", "content": user_msg})
    return messages


def _call_llm(llm_config, messages, tools=None):
    """调用 LLM — 标准 OpenAI Chat Completions 格式"""
    try:
        url = f"{llm_config['base_url'].rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {llm_config['api_key']}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": llm_config["model"],
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        print(f"[ai_chat] POST {url} model={llm_config['model']}")
        resp = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=llm_config.get("timeout", AI_REQUEST_TIMEOUT_SECONDS),
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[ai_chat] LLM调用失败: {e}")
        return None


def _save_chat(stock_code, session_id, role, user_message, ai_response):
    col = get_collection("chat_history")
    if col is None:
        return
    col.insert_one({
        "stock_code": stock_code,
        "session_id": session_id or "default",
        "role": role,
        "user_message": user_message,
        "ai_response": ai_response,
        "created_at": datetime.now(),
    })


def _get_kline_summary(stock_code, days=30):
    from services.stock_data import fetch_kline
    kline = fetch_kline(stock_code)
    if not kline:
        return {"summary": "无K线数据"}

    recent = kline[-days:] if len(kline) >= days else kline
    if not recent:
        return {"summary": "数据不足"}

    closes = [k["close"] for k in recent]
    start_price = closes[0]
    end_price = closes[-1]
    max_price = max(closes)
    min_price = min(closes)
    change_pct = (end_price - start_price) / start_price * 100

    volumes = [k["volume"] for k in recent]
    avg_vol = sum(volumes) / len(volumes)
    recent_vol = sum(volumes[-5:]) / min(5, len(volumes[-5:]))
    vol_change = "放量" if recent_vol > avg_vol * 1.2 else ("缩量" if recent_vol < avg_vol * 0.8 else "平量")

    return {
        "stock_code": stock_code,
        "period": f"近{len(recent)}个交易日",
        "start_price": round(start_price, 2),
        "end_price": round(end_price, 2),
        "max_price": round(max_price, 2),
        "min_price": round(min_price, 2),
        "change_pct": round(change_pct, 2),
        "amplitude": round((max_price - min_price) / min_price * 100, 2),
        "volume_status": vol_change,
        "trend": "上涨" if change_pct > 3 else ("下跌" if change_pct < -3 else "震荡"),
        "summary": (
            f"近{len(recent)}日{'上涨' if change_pct > 0 else '下跌'}{abs(change_pct):.1f}%，"
            f"最高{max_price:.2f}，最低{min_price:.2f}，{vol_change}。"
        ),
    }
