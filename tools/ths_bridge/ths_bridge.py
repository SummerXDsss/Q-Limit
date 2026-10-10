"""
同花顺下单客户端中转服务（在 Windows 机器上单独运行，不属于 Q-Limit 主服务）

作用：把已登录的同花顺版下单客户端（xiadan.exe）里的资金和持仓，
通过带口令的 HTTP 接口提供给局域网里的 Q-Limit。只读，不提供下单接口。

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
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_token.txt")

MOCK_ACCOUNT = {
    "balance": {"资金余额": 10000.0, "可用金额": 8000.0, "可取金额": 8000.0, "股票市值": 52000.0, "总资产": 62000.0},
    "position": [
        {"证券代码": "600030", "证券名称": "中信证券", "股票余额": 1000, "可用余额": 1000, "成本价": 25.123, "市价": 26.0},
        {"证券代码": "000001", "证券名称": "平安银行", "股票余额": 2000, "可用余额": 0, "参考成本价": 12.5, "市价": 13.0},
    ],
}

# 同花顺客户端是单个窗口，所有操作必须串行
_client_lock = threading.Lock()
_trader = None


def _connect(exe_path):
    import easytrader

    trader = easytrader.use("universal_client")
    trader.connect(exe_path)
    return trader


def read_account(options):
    """返回 {"balance": {...}, "position": [...]}，字段名保持客户端原样"""
    global _trader
    if options.mock:
        return MOCK_ACCOUNT

    with _client_lock:
        try:
            if _trader is None:
                _trader = _connect(options.exe)
            return {"balance": _trader.balance, "position": _trader.position}
        except Exception:
            # 客户端被关掉、重启或弹窗卡住后，下次请求重新连接
            _trader = None
            raise


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

        def do_GET(self):
            path = self.path.split("?", 1)[0].rstrip("/")
            if path == "/health":
                # 不需要口令，只用来确认服务在跑
                return self._send(200, {"ok": True, "mock": options.mock})
            if not self._authorized():
                return self._send(401, {"error": "口令错误"})
            if path == "/account":
                try:
                    return self._send(200, {"data": read_account(options)})
                except Exception as e:
                    traceback.print_exc()
                    return self._send(502, {"error": f"读取同花顺客户端失败: {type(e).__name__}: {e}"})
            return self._send(404, {"error": "not found"})

        def log_message(self, fmt, *args):
            print(f"[{self.log_date_time_string()}] {self.client_address[0]} {fmt % args}")

    return BridgeHandler


def main():
    parser = argparse.ArgumentParser(description="同花顺下单客户端中转服务（只读）")
    parser.add_argument("--exe", help=r"下单客户端路径，例如 C:\同花顺软件\同花顺\xiadan.exe")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0（局域网可访问）")
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    parser.add_argument("--token", help="访问口令；不指定则自动生成并保存到 bridge_token.txt")
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
    print("在钉钉里私聊机器人发送 #CONFIG 同花顺，按提示填入本机局域网地址和口令。")
    print("=" * 60)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
