"""
同花顺下单客户端中转服务（在 Windows 机器上单独运行，不属于 Q-Limit 主服务）

作用：把已登录的同花顺版下单客户端（xiadan.exe）里的资金和持仓，
通过带口令的 HTTP 接口提供给局域网里的 Q-Limit。默认只读；
加 --allow-trade 后才开放限价买卖和撤单。

用法见同目录 README.md。快速开始：
    pip install -r requirements.txt
    python ths_bridge.py --exe "C:\\同花顺软件\\同花顺\\xiadan.exe"
"""
import argparse
import hmac
import json
import os
import secrets
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_token.txt")
CAPTCHA_IMAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captcha.png")

MOCK_ACCOUNT = {
    "balance": {"资金余额": 10000.0, "可用金额": 8000.0, "可取金额": 8000.0, "股票市值": 52000.0, "总资产": 62000.0},
    "position": [
        {"证券代码": "600030", "证券名称": "中信证券", "股票余额": 1000, "可用余额": 1000, "成本价": 25.123, "市价": 26.0},
        {"证券代码": "000001", "证券名称": "平安银行", "股票余额": 2000, "可用余额": 0, "参考成本价": 12.5, "市价": 13.0},
    ],
}

_mock_orders = []

# 同花顺客户端是单个窗口，所有操作必须串行
_client_lock = threading.Lock()
_trader = None

_OCR = None


def _captcha_recognize(image_path):
    """本地 OCR 识别同花顺的拷贝保护验证码（默认实现在 easytrader 里，但依赖 pytesseract+Tesseract）"""
    global _OCR
    try:
        import ddddocr
    except ImportError:
        raise RuntimeError(
            "缺少验证码识别库 ddddocr，请执行: .venv\\Scripts\\python.exe -m pip install ddddocr"
        )
    if _OCR is None:
        _OCR = ddddocr.DdddOcr(show_ad=False)
    with open(image_path, "rb") as f:
        code = _OCR.classification(f.read())
    # 只保留字母数字，去掉空格等杂质
    return "".join(ch for ch in str(code) if ch.isalnum()).strip()


def _setup_ocr():
    """把本地 OCR 接进 easytrader（模块级引用都要替换掉）"""
    import easytrader.grid_strategies as _gs
    import easytrader.utils.captcha as _cap

    _cap.captcha_recognize = _captcha_recognize
    _gs.captcha_recognize = _captcha_recognize


def _find_popup(trader):
    """找同花顺弹出的『拷贝保护验证码』对话框"""
    for w in trader.app.windows():
        try:
            if w.class_name() == "#32770" and w.is_visible():
                return w
        except Exception:
            pass
    return None


def _solve_captcha(trader, attempts=5):
    """刷新验证码 → 截图 → 本地 OCR → 真实键盘输入 → 点确定。返回是否通过。
    注意：输入必须用 type_keys 真实键盘事件，set_text 客户端不认；提交用点「确定」按钮。"""
    for _ in range(attempts):
        d = _find_popup(trader)
        if d is None:
            return True
        try:
            sp = trader.app.window(handle=d.handle)
            img = sp.window(control_id=0x965, class_name="Static")
            img.click()  # 刷新验证码
            trader.wait(0.6)
            img.capture_as_image().save(CAPTCHA_IMAGE)
            code = _captcha_recognize(CAPTCHA_IMAGE)
            if len(code) >= 4:
                edit = sp.window(control_id=0x964, class_name="Edit")
                edit.set_focus()
                edit.type_keys(code)
                trader.wait(0.3)
                sp.window(control_id=0x1, class_name="Button").click()  # 确定
                trader.wait(1.0)
            if _find_popup(trader) is None:
                return True
        except Exception:
            trader.wait(0.3)
    return False


def _connect(exe_path):
    import easytrader
    from easytrader import grid_strategies

    class _CaptchaWMCopy(grid_strategies.WMCopy):
        """WM 复制到剪贴板 + 自动过拷贝保护验证码"""

        def _get_clipboard_data(self):
            import pywinauto.clipboard as clipboard

            solved = True
            for _ in range(3):
                if _find_popup(self._trader) is None:
                    break
                solved = _solve_captcha(self._trader)
            if not solved:
                raise RuntimeError(
                    "同花顺拷贝保护验证码多次未通过（检查客户端窗口是否可见/被遮挡）"
                )
            last_err = None
            for _ in range(5):
                try:
                    data = clipboard.GetData()
                    if data:
                        return data
                except Exception as e:
                    last_err = e
                self._trader.wait(0.2)
            if last_err is not None:
                raise last_err
            return ""

    _setup_ocr()
    trader = easytrader.use("universal_client")
    # 读取表格（持仓）默认走「另存为 xls」策略，在部分同花顺客户端版本上保存对话框
    # 行为不一致，会落成 FileNotFoundError。改用「复制到剪贴板」策略，兼容性更好。
    # 注意：复制会触发客户端的「拷贝保护验证码」，_CaptchaWMCopy 会用本地 OCR 自动通过。
    trader.grid_strategy = _CaptchaWMCopy
    trader.connect(exe_path)
    return trader


def _with_trader(options, action):
    """串行地在已连接的客户端上执行 action(trader)"""
    global _trader
    with _client_lock:
        try:
            if _trader is None:
                _trader = _connect(options.exe)
            return action(_trader)
        except Exception:
            # 客户端被关掉、重启或弹窗卡住后，下次请求重新连接
            _trader = None
            raise


def read_account(options):
    """返回 {"balance": {...}, "position": [...]}，字段名保持客户端原样"""
    if options.mock:
        return MOCK_ACCOUNT
    # 剪贴板策略在空表格时会返回 None，统一兜成空列表
    return _with_trader(options, lambda t: {"balance": t.balance, "position": t.position or []})


def read_orders(options):
    """当日委托"""
    if options.mock:
        return list(_mock_orders)
    return _with_trader(options, lambda t: t.today_entrusts or [])


def read_trades(options):
    """当日成交"""
    if options.mock:
        return []
    return _with_trader(options, lambda t: t.today_trades or [])


def _parse_order(body):
    """校验下单参数，返回 (side, code, price, amount)；不合法时抛 ValueError"""
    side = str(body.get("side", "")).lower()
    if side not in ("buy", "sell"):
        raise ValueError("side 只能是 buy 或 sell")
    code = str(body.get("code", "")).strip()
    if not (code.isdigit() and len(code) == 6):
        raise ValueError("证券代码必须是 6 位数字")
    try:
        price = float(body.get("price"))
        amount = int(body.get("amount"))
    except (TypeError, ValueError):
        raise ValueError("价格或数量不是数字")
    if not (0 < price < 100000) or amount <= 0:
        raise ValueError("价格和数量必须大于 0")
    return side, code, price, amount


def place_order(options, body):
    """限价买入 / 卖出，返回客户端给出的结果（通常含 entrust_no）"""
    side, code, price, amount = _parse_order(body)
    if options.mock:
        entrust_no = str(1000 + len(_mock_orders) + 1)
        _mock_orders.append({
            "委托时间": time.strftime("%H:%M:%S"), "证券代码": code, "证券名称": "示例股票",
            "操作": "买入" if side == "buy" else "卖出", "备注": "已报",
            "委托数量": amount, "成交数量": 0, "委托价格": price, "成交均价": 0.0, "合同编号": entrust_no,
        })
        return {"entrust_no": entrust_no}

    def action(trader):
        if side == "buy":
            return trader.buy(code, price=price, amount=amount)
        return trader.sell(code, price=price, amount=amount)

    return _with_trader(options, action)


def cancel_order(options, body):
    entrust_no = str(body.get("entrust_no", "")).strip()
    if not entrust_no:
        raise ValueError("缺少 entrust_no")
    if options.mock:
        for order in _mock_orders:
            if order["合同编号"] == entrust_no and order["备注"] == "已报":
                order["备注"] = "已撤"
                return {"message": "撤单申报成功"}
        return {"message": "委托单状态错误不能撤单, 该委托单可能已经成交或者已撤"}
    return _with_trader(options, lambda t: t.cancel_entrust(entrust_no))


def _load_or_create_token(cli_token):
    if cli_token:
        return cli_token
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            token = f.read().strip()
        if token:
            return token
    token = secrets.token_urlsafe(24)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token)
    return token


def _build_handler(options):
    class BridgeHandler(BaseHTTPRequestHandler):
        server_version = "ths-bridge/1.0"

        def _send(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self):
            given = self.headers.get("X-Bridge-Token", "")
            return hmac.compare_digest(given.encode("utf-8"), options.token.encode("utf-8"))

        def _run(self, func, failure):
            try:
                return self._send(200, {"data": func()})
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:
                traceback.print_exc()
                return self._send(502, {"error": f"{failure}: {type(e).__name__}: {e}"})

        def do_GET(self):
            path = self.path.split("?", 1)[0].rstrip("/")
            if path == "/health":
                # 不需要口令，只用来确认服务在跑
                return self._send(200, {"ok": True, "mock": options.mock, "trade": options.allow_trade})
            if not self._authorized():
                return self._send(401, {"error": "口令错误"})
            if path == "/account":
                return self._run(lambda: read_account(options), "读取同花顺客户端失败")
            if path == "/orders":
                return self._run(lambda: read_orders(options), "读取当日委托失败")
            if path == "/trades":
                return self._run(lambda: read_trades(options), "读取当日成交失败")
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            path = self.path.split("?", 1)[0].rstrip("/")
            if not self._authorized():
                return self._send(401, {"error": "口令错误"})
            if path not in ("/order", "/cancel"):
                return self._send(404, {"error": "not found"})
            if not options.allow_trade:
                return self._send(403, {"error": "中转服务未开启交易，请加 --allow-trade 重新启动"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._send(400, {"error": "请求体不是合法的 JSON 对象"})
            print(f"[交易] {path} {json.dumps(body, ensure_ascii=False)}")
            if path == "/order":
                # 出错时委托可能已经报出去了，提醒到客户端核对
                return self._run(lambda: place_order(options, body), "下单过程出错，结果未知，请到客户端核对当日委托")
            return self._run(lambda: cancel_order(options, body), "撤单过程出错，结果未知，请到客户端核对")

        def log_message(self, fmt, *args):
            print(f"[{self.log_date_time_string()}] {self.client_address[0]} {fmt % args}")

    return BridgeHandler


def main():
    parser = argparse.ArgumentParser(description="同花顺下单客户端中转服务")
    parser.add_argument("--exe", help=r"下单客户端路径，例如 C:\同花顺软件\同花顺\xiadan.exe")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0（局域网可访问）")
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    parser.add_argument("--token", help="访问口令；不指定则自动生成并保存到 bridge_token.txt")
    parser.add_argument("--allow-trade", action="store_true", help="开启下单和撤单接口；不加则只能查询")
    parser.add_argument("--mock", action="store_true", help="不连接客户端，返回示例数据，用来先验证网络是否打通")
    options = parser.parse_args()

    if not options.mock and not options.exe:
        parser.error("需要 --exe 指定 xiadan.exe 的路径（或用 --mock 先测试连通性）")
    options.token = _load_or_create_token(options.token)

    server = ThreadingHTTPServer((options.host, options.port), _build_handler(options))
    print("=" * 60)
    print(f"同花顺中转服务已启动: http://{options.host}:{options.port}")
    print(f"访问口令: {options.token}")
    print("模式: " + ("示例数据（--mock）" if options.mock else f"连接客户端 {options.exe}"))
    print("交易: " + ("已开启（可下单、撤单）" if options.allow_trade else "未开启（只读，加 --allow-trade 开启）"))
    print("在 Q-Limit 网页「设置 → 服务端配置 → 同花顺交易」填入本机局域网地址和口令。")
    print("=" * 60)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
