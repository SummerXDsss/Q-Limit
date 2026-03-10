"""
钉钉机器人通知服务
"""
import base64
import hashlib
import hmac
import time
import urllib.parse

import requests

from config import (
    DINGTALK_ENABLED,
    DINGTALK_WEBHOOK,
    DINGTALK_SECRET,
    DINGTALK_AT_MOBILES,
)


def _sign_webhook(webhook, secret):
    """带签名的钉钉 webhook"""
    if not secret:
        return webhook

    timestamp = str(int(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    sign = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(sign))

    separator = "&" if "?" in webhook else "?"
    return f"{webhook}{separator}timestamp={timestamp}&sign={sign}"


def _post(payload):
    if not DINGTALK_ENABLED:
        return {"ok": False, "error": "钉钉通知未启用（DINGTALK_ENABLED=false）"}

    if not DINGTALK_WEBHOOK:
        return {"ok": False, "error": "未配置 DINGTALK_WEBHOOK"}

    url = _sign_webhook(DINGTALK_WEBHOOK, DINGTALK_SECRET)

    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        ok = data.get("errcode") == 0
        return {"ok": ok, "error": "" if ok else data.get("errmsg", "发送失败"), "raw": data}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_text(content, at_mobiles=None, is_at_all=False):
    mobiles = at_mobiles if at_mobiles is not None else DINGTALK_AT_MOBILES
    payload = {
        "msgtype": "text",
        "text": {"content": content},
        "at": {
            "atMobiles": mobiles,
            "isAtAll": bool(is_at_all),
        },
    }
    return _post(payload)


def send_markdown(title, text, at_mobiles=None, is_at_all=False):
    mobiles = at_mobiles if at_mobiles is not None else DINGTALK_AT_MOBILES
    payload = {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": text},
        "at": {
            "atMobiles": mobiles,
            "isAtAll": bool(is_at_all),
        },
    }
    return _post(payload)


def send_debate_result(stock_code, prompt, judge_content):
    """发送 AI 裁判结果到钉钉"""
    title = f"Q-Limit 辩论结果 {stock_code}"
    text = (
        f"### Q-Limit 辩论结果\n\n"
        f"- 股票: `{stock_code}`\n"
        f"- 问题: {prompt}\n\n"
        f"---\n\n"
        f"{judge_content}"
    )
    return send_markdown(title=title, text=text)
