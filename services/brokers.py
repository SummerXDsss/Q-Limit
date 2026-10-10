"""
券商账户接入
- 长桥 / 老虎：官方个人 OpenAPI，纯靠开发者凭证远程登录
- 同花顺（A 股券商）：通过局域网里的中转服务 tools/ths_bridge 读取下单客户端
- login() 会真实调用券商接口校验凭证；get_account() 返回资产与持仓
- SDK 按需导入，未安装时给出明确提示而不是让整个服务起不来
"""


import requests


def _field(key, label, secret=False, hint=""):
    return {"key": key, "label": label, "secret": secret, "hint": hint}


BROKERS = {
    "longbridge": {
        "name": "长桥证券",
        "aliases": ("长桥", "长桥证券", "longbridge", "longport", "lb"),
        "guide": "在 https://open.longbridge.com 的「用户中心」获取 App Key / App Secret / Access Token",
        "fields": [
            _field("app_key", "App Key"),
            _field("app_secret", "App Secret", secret=True),
            _field("access_token", "Access Token", secret=True),
        ],
    },
    "tiger": {
        "name": "老虎证券",
        "aliases": ("老虎", "老虎证券", "tiger", "tigeropen", "老虎国际"),
        "guide": "在 https://www.itiger.com/openapi/info 注册开发者，获取 tiger_id 并生成 RSA 私钥",
        "fields": [
            _field("tiger_id", "开发者 ID (tiger_id)"),
            _field("account", "交易账户", hint="综合账户、环球账户或模拟账户的账号"),
            _field("private_key", "RSA 私钥", secret=True, hint="PKCS#1 格式，可带或不带 BEGIN/END 行"),
        ],
    },
    "ths": {
        "name": "同花顺客户端（A 股券商）",
        "aliases": ("同花顺", "ths", "浙商", "浙商证券", "中信建投", "中信建投证券"),
        "guide": "先在装有券商同花顺版下单客户端的 Windows 机器上运行 tools/ths_bridge（见该目录 README）",
        "fields": [
            _field("bridge_url", "中转服务地址", hint="例如 http://192.168.1.50:8765"),
            _field("token", "中转服务口令", secret=True, hint="中转服务启动时打印的访问口令"),
        ],
    },
}

# 常被问到但无法接入的券商：给出原因，而不是假装保存成功
UNSUPPORTED_REASONS = (
    (("富途", "futu", "moomoo", "牛牛"), "富途 OpenAPI 必须先在本机运行并登录 OpenD 网关，无法只凭凭证远程登录，暂未接入。"),
    (("盈透", "ibkr", "interactive"), "盈透需要本机运行 TWS / IB Gateway 并手动登录，暂未接入。"),
)
A_SHARE_REASON = (
    "没有可直接登录的个人接口。如果是 A 股券商且有同花顺版下单客户端，"
    "可以在 Windows 机器上运行中转服务后发送 #CONFIG 同花顺 接入。"
)


def find_broker(name):
    """按名称或别名找到券商 id，找不到返回 None"""
    key = (name or "").strip().lower()
    if not key:
        return None
    for broker_id, broker in BROKERS.items():
        if key == broker_id or key in (alias.lower() for alias in broker["aliases"]):
            return broker_id
    return None


def unsupported_reason(name):
    key = (name or "").strip().lower()
    for keywords, reason in UNSUPPORTED_REASONS:
        if any(word in key for word in keywords):
            return reason
    return A_SHARE_REASON


def supported_names():
    return [broker["name"] for broker in BROKERS.values()]


def mask(value):
    value = str(value or "")
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}***{value[-3:]}"


def login(broker_id, credentials):
    """
    用凭证真实登录一次。
    返回 (ok, message, summary)；summary 是登录成功后的资产概览文本
    """
    account, error = get_account(broker_id, credentials)
    if error:
        return False, error, ""
    return True, "", format_account(account, with_positions=False)


def get_account(broker_id, credentials):
    """返回 (account, error)。account: {"assets": [...], "positions": [...]}"""
    fetcher = _FETCHERS.get(broker_id)
    if fetcher is None:
        return None, f"不支持的券商: {broker_id}"
    try:
        return fetcher(credentials or {}), ""
    except ImportError as e:
        return None, f"服务端未安装对应的券商 SDK: {e}"
    except Exception as e:
        return None, _clean_error(e)


def format_account(account, with_positions=True):
    lines = []
    for item in account.get("assets", []):
        lines.append(
            f"- 净资产: {_num(item.get('net_assets'))} {item.get('currency', '')}"
            f" / 现金: {_num(item.get('cash'))} / 购买力: {_num(item.get('buy_power'))}"
        )
    if not lines:
        lines.append("- 暂无资产数据")
    if with_positions:
        positions = account.get("positions", [])
        lines.append("")
        lines.append(f"持仓（{len(positions)}）：" if positions else "当前无持仓。")
        for pos in positions[:30]:
            lines.append(
                f"- {pos.get('name') or pos.get('symbol')} {pos.get('symbol')}: "
                f"{_num(pos.get('quantity'), 0)} 股 @ 成本 {_num(pos.get('cost_price'), 3)} {pos.get('currency', '')}"
            )
    return "\n".join(lines)


def _num(value, digits=2):
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "--"


def _clean_error(error):
    text = str(error).strip() or type(error).__name__
    if "request sign failed" in text:
        return "私钥格式不正确，无法签名（需要 PKCS#1 格式的 RSA 私钥）"
    return text[:300]


def _fetch_longbridge(credentials):
    from longbridge.openapi import Config, TradeContext

    config = Config.from_apikey(
        credentials.get("app_key", ""),
        credentials.get("app_secret", ""),
        credentials.get("access_token", ""),
    )
    ctx = TradeContext(config)
    assets = [
        {
            "currency": balance.currency,
            "net_assets": balance.net_assets,
            "cash": balance.total_cash,
            "buy_power": balance.buy_power,
        }
        for balance in ctx.account_balance()
    ]
    positions = []
    for channel in ctx.stock_positions().channels:
        for pos in channel.positions:
            positions.append({
                "symbol": pos.symbol,
                "name": pos.symbol_name,
                "quantity": pos.quantity,
                "cost_price": pos.cost_price,
                "currency": pos.currency,
            })
    return {"assets": assets, "positions": positions}


def _normalize_private_key(value):
    lines = [line.strip() for line in str(value or "").splitlines()]
    return "".join(line for line in lines if line and not line.startswith("-----"))


def _fetch_tiger(credentials):
    from tigeropen.tiger_open_config import TigerOpenClientConfig
    from tigeropen.trade.trade_client import TradeClient

    config = TigerOpenClientConfig()
    config.tiger_id = credentials.get("tiger_id", "")
    config.account = credentials.get("account", "")
    config.private_key = _normalize_private_key(credentials.get("private_key", ""))
    client = TradeClient(config)

    portfolio = client.get_prime_assets()
    assets = []
    for segment in (getattr(portfolio, "segments", None) or {}).values():
        assets.append({
            "currency": getattr(segment, "currency", ""),
            "net_assets": getattr(segment, "net_liquidation", None),
            "cash": getattr(segment, "cash_balance", None),
            "buy_power": getattr(segment, "buying_power", None),
        })
    positions = []
    for pos in client.get_positions() or []:
        contract = getattr(pos, "contract", None)
        positions.append({
            "symbol": getattr(contract, "symbol", "") or "",
            "name": getattr(contract, "name", "") or "",
            "quantity": getattr(pos, "quantity", None),
            "cost_price": getattr(pos, "average_cost", None),
            "currency": getattr(contract, "currency", "") or "",
        })
    return {"assets": assets, "positions": positions}


THS_TIMEOUT_SECONDS = 60
# 各券商的同花顺客户端字段名不完全一致，按顺序取第一个存在的
_THS_BALANCE_KEYS = {
    "net_assets": ("总资产", "资产总值", "总 资 产"),
    "cash": ("资金余额", "资金帐户余额", "可用金额", "可用资金"),
    "buy_power": ("可用金额", "可用资金", "可用余额"),
}
_THS_POSITION_KEYS = {
    "symbol": ("证券代码", "股票代码"),
    "name": ("证券名称", "股票名称"),
    "quantity": ("股票余额", "当前持仓", "持仓数量", "实际数量", "证券数量", "参考持股"),
    "cost_price": ("成本价", "参考成本价", "买入成本", "摊薄成本价", "参考成本"),
}


def _pick(row, keys):
    for key in keys:
        if key in row and row[key] not in ("", None):
            return row[key]
    return None


def _ths_symbol(value):
    # 导出的 xls 里代码可能是 ="600030" 或被读成数字丢掉前导 0
    text = str(value or "").strip().strip("=").strip('"').strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6) if text.isdigit() else text


def _fetch_ths(credentials):
    base_url = str(credentials.get("bridge_url", "")).strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        base_url = "http://" + base_url
    try:
        resp = requests.get(
            f"{base_url}/account",
            headers={"X-Bridge-Token": str(credentials.get("token", "")).strip()},
            timeout=THS_TIMEOUT_SECONDS,
            # 中转服务在局域网里，不走系统代理
            proxies={"http": None, "https": None},
        )
    except requests.RequestException as e:
        raise RuntimeError(f"连不上中转服务 {base_url}（{type(e).__name__}），请确认它在运行且网络可达") from e
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    if resp.status_code != 200 or not isinstance(payload, dict) or "data" not in payload:
        detail = payload.get("error") if isinstance(payload, dict) else ""
        raise RuntimeError(detail or f"中转服务返回异常（HTTP {resp.status_code}），请确认地址指向的是 ths_bridge")

    data = payload.get("data") or {}
    balance = data.get("balance") or {}
    if isinstance(balance, list):
        balance = balance[0] if balance else {}
    assets = [{
        "currency": "CNY",
        "net_assets": _pick(balance, _THS_BALANCE_KEYS["net_assets"]),
        "cash": _pick(balance, _THS_BALANCE_KEYS["cash"]),
        "buy_power": _pick(balance, _THS_BALANCE_KEYS["buy_power"]),
    }]
    positions = []
    for row in data.get("position") or []:
        if not isinstance(row, dict):
            continue
        positions.append({
            "symbol": _ths_symbol(_pick(row, _THS_POSITION_KEYS["symbol"])),
            "name": str(_pick(row, _THS_POSITION_KEYS["name"]) or "").strip(),
            "quantity": _pick(row, _THS_POSITION_KEYS["quantity"]),
            "cost_price": _pick(row, _THS_POSITION_KEYS["cost_price"]),
            "currency": "CNY",
        })
    return {"assets": assets, "positions": positions}


_FETCHERS = {
    "ths": _fetch_ths,
    "longbridge": _fetch_longbridge,
    "tiger": _fetch_tiger,
}
