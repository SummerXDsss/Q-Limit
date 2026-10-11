"""
网页端交易（同花顺中转）
- 账户配置是服务端级别的（.env 里的 THS_BRIDGE_*），后端没有用户概念
- 查询随时可用；下单 / 撤单需要 TRADE_ENABLED=true 且设置了 TRADE_PIN，每次请求都要带口令
- 所有下单、撤单请求（包括被拒绝的）都会写进运行日志（app_log，分类 trade）
- 网页和钉钉机器人共用这一份配置和这一套校验
"""
import hmac
import time
from datetime import datetime

from config import THS_BRIDGE_URL, THS_BRIDGE_TOKEN, TRADE_ENABLED, TRADE_PIN
from services import app_log, brokers

MAX_ORDER_AMOUNT = 1_000_000
MAX_ORDER_PRICE = 100_000
# 口令连续输错的锁定：窗口内错满次数后，窗口结束前一律拒绝（进程内状态，重启清零）
PIN_MAX_FAILURES = 5
PIN_LOCK_SECONDS = 600
_pin_failures = []


def _credentials():
    return {"bridge_url": THS_BRIDGE_URL, "token": THS_BRIDGE_TOKEN}


def _configured():
    return bool(THS_BRIDGE_URL and THS_BRIDGE_TOKEN)


def get_status(check_bridge=True):
    """交易开关状态；check_bridge 时顺便探测中转服务是否在线"""
    health = brokers.ths_health(_credentials()) if check_bridge and _configured() else None
    return {
        "configured": _configured(),
        "trade_enabled": bool(TRADE_ENABLED),
        "pin_set": bool(TRADE_PIN),
        "bridge_online": health is not None,
        "bridge_trade": bool(health and health.get("trade")),
        "bridge_mock": bool(health and health.get("mock")),
    }


def _query(func):
    """返回 (data, error)"""
    if not _configured():
        return None, "还没有配置同花顺中转服务，请在「设置 → 服务端配置 → 同花顺交易」里填写地址和口令"
    try:
        return func(_credentials()), ""
    except Exception as e:
        return None, str(e)[:300]


def get_account():
    return _query(lambda cred: brokers.get_account_or_raise("ths", cred))


def get_orders():
    return _query(brokers.ths_orders)


def get_trades():
    return _query(brokers.ths_trades)


def _check_trade_allowed(pin):
    if not _configured():
        return "还没有配置同花顺中转服务"
    if not TRADE_ENABLED:
        return "服务端未开启网页交易（TRADE_ENABLED）"
    if not TRADE_PIN:
        return "服务端还没有设置交易口令（TRADE_PIN），为避免局域网内他人下单，必须先设置"
    now = time.time()
    _pin_failures[:] = [t for t in _pin_failures if now - t < PIN_LOCK_SECONDS]
    if len(_pin_failures) >= PIN_MAX_FAILURES:
        wait = int(PIN_LOCK_SECONDS - (now - _pin_failures[0])) // 60 + 1
        return f"交易口令连续输错 {PIN_MAX_FAILURES} 次，已暂停交易，请约 {wait} 分钟后再试"
    if not hmac.compare_digest(str(pin or "").encode("utf-8"), TRADE_PIN.encode("utf-8")):
        _pin_failures.append(now)
        return "交易口令错误"
    _pin_failures.clear()
    return ""


def _validate_order(side, code, price, amount):
    """返回 (side, code, price, amount, error)"""
    side = str(side or "").lower()
    if side not in ("buy", "sell"):
        return None, None, None, None, "方向只能是买入或卖出"
    code = str(code or "").strip()
    if not (code.isdigit() and len(code) == 6):
        return None, None, None, None, "证券代码必须是 6 位数字（仅支持 A 股）"
    try:
        price = round(float(price), 3)
        if float(amount) != int(float(amount)):
            raise ValueError
        amount = int(float(amount))
    except (TypeError, ValueError, OverflowError):
        return None, None, None, None, "价格或数量不是有效数字"
    # 写成区间判断，NaN / inf 也会被挡住
    if not 0 < price < MAX_ORDER_PRICE:
        return None, None, None, None, "价格必须大于 0"
    if amount <= 0 or amount > MAX_ORDER_AMOUNT:
        return None, None, None, None, f"数量必须在 1 到 {MAX_ORDER_AMOUNT} 之间"
    if side == "buy":
        if code.startswith(("688", "689")):
            if amount < 200:
                return None, None, None, None, "科创板买入至少 200 股"
        elif code.startswith(("4", "8", "92")):
            if amount < 100:
                return None, None, None, None, "北交所买入至少 100 股"
        elif amount % 100 != 0:
            return None, None, None, None, "买入数量必须是 100 的整数倍"
    return side, code, price, amount, ""


_SIDE_TEXT = {"buy": "买入", "sell": "卖出"}


def _log(action, detail, result=None, error="", source=""):
    if action == "order":
        text = (
            f"{_SIDE_TEXT.get(detail.get('side'), detail.get('side'))} {detail.get('code')} "
            f"{detail.get('amount')} 股 @ {detail.get('price')}"
        )
    else:
        text = f"撤单 {detail.get('entrust_no')}"
    if error:
        message = f"{text} 失败：{error}"
    else:
        message = f"{text} 已提交：{(result or {}).get('entrust_no') or (result or {}).get('message') or ''}"
    app_log.log(
        "trade", message, "error" if error else "info",
        {"action": action, "source": source, "result": result or {}, **detail},
    )


def place_order(side, code, price, amount, pin, source=""):
    """限价委托。返回 (result, error)"""
    detail = {"side": side, "code": code, "price": price, "amount": amount}
    error = _check_trade_allowed(pin)
    if not error:
        side, code, price, amount, error = _validate_order(side, code, price, amount)
    if error:
        _log("order", detail, error=error, source=source)
        return None, error

    detail = {"side": side, "code": code, "price": price, "amount": amount}
    try:
        result = brokers.ths_place_order(_credentials(), side, code, price, amount)
    except Exception as e:
        error = str(e)[:300]
        _log("order", detail, error=error, source=source)
        return None, error
    _log("order", detail, result=result, source=source)
    return result, ""


def cancel_order(entrust_no, pin, source=""):
    """撤单。返回 (result, error)"""
    entrust_no = str(entrust_no or "").strip()
    detail = {"entrust_no": entrust_no}
    error = _check_trade_allowed(pin)
    if not error and not entrust_no:
        error = "缺少委托编号"
    if error:
        _log("cancel", detail, error=error, source=source)
        return None, error
    try:
        result = brokers.ths_cancel_order(_credentials(), entrust_no)
    except Exception as e:
        error = str(e)[:300]
        _log("cancel", detail, error=error, source=source)
        return None, error
    _log("cancel", detail, result=result, source=source)
    return result, ""
