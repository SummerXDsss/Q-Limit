"""
.env 配置读写服务（供前端设置面板使用）
- 只允许读写下方 SETTING_GROUPS 中声明的键
- 密钥类字段只返回"是否已配置"，不回传明文
- 保存后写回 .env，并尽量在当前进程内即时生效
"""
import importlib
import os
import re
import sys
import threading

import config

ENV_PATH = config.ENV_FILE_PATH

_LOCK = threading.Lock()
_MAX_VALUE_LENGTH = 2000
_SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9_./:@,+\-=%?&~]*$")

_BOOL = "bool"
_INT = "int"
_SECRET = "secret"
_SELECT = "select"
_TEXT = "text"


def _f(key, label, type_=_TEXT, default="", options=None, hint="", restart=False):
    return {
        "key": key,
        "label": label,
        "type": type_,
        "default": default,
        "options": options or [],
        "hint": hint,
        "restart": restart,
    }


def _ai_role_fields(prefix, name):
    return [
        _f(f"AI_{prefix}_API_KEY", f"{name} API 密钥", _SECRET, hint="留空则使用全局默认"),
        _f(f"AI_{prefix}_BASE_URL", f"{name} API 地址", hint="留空则使用全局默认"),
        _f(f"AI_{prefix}_MODEL", f"{name} 模型", hint="留空则使用全局默认"),
    ]


SETTING_GROUPS = [
    {
        "id": "ai",
        "title": "🤖 AI 模型",
        "desc": "支持 OpenAI 兼容的 /chat/completions 接口；浏览器「AI 模型配置」里填写的值会覆盖这里。",
        "fields": [
            _f("AI_DEFAULT_API_KEY", "全局默认 API 密钥", _SECRET),
            _f("AI_DEFAULT_BASE_URL", "全局默认 API 地址", hint="例如 https://api.openai.com/v1"),
            _f("AI_DEFAULT_MODEL", "全局默认模型"),
            _f("AI_REQUEST_TIMEOUT_SECONDS", "请求超时（秒）", _INT, default="120"),
        ]
        + _ai_role_fields("BULL", "多头")
        + _ai_role_fields("BEAR", "空头")
        + _ai_role_fields("JUDGE", "裁判"),
    },
    {
        "id": "tavily",
        "title": "🔎 Tavily 全网搜索",
        "desc": "",
        "fields": [
            _f("TAVILY_API_KEY", "API 密钥", _SECRET),
            _f("ENABLE_WEB_SEARCH_CONTEXT", "AI 对话时附带全网搜索结果", _BOOL, default="true"),
            _f("TAVILY_SEARCH_DEPTH", "搜索深度", _SELECT, default="basic", options=["basic", "advanced"]),
            _f("TAVILY_MAX_RESULTS", "最大结果数", _INT, default="5"),
            _f("TAVILY_TIME_RANGE", "默认时间范围", _SELECT, options=["", "day", "week", "month", "year"]),
            _f("TAVILY_INCLUDE_ANSWER", "返回摘要", _SELECT, default="true",
               options=["true", "false", "basic", "advanced"]),
            _f("TAVILY_INCLUDE_RAW_CONTENT", "返回原文", _SELECT, default="false",
               options=["false", "true", "markdown", "text"]),
            _f("TAVILY_INCLUDE_IMAGES", "返回图片", _BOOL, default="false"),
            _f("TAVILY_INCLUDE_IMAGE_DESCRIPTIONS", "返回图片描述", _BOOL, default="false"),
            _f("TAVILY_INCLUDE_FAVICON", "返回站点图标", _BOOL, default="false"),
            _f("TAVILY_AUTO_PARAMETERS", "自动参数", _BOOL, default="false"),
            _f("TAVILY_INCLUDE_DOMAINS", "域名白名单", hint="逗号分隔"),
            _f("TAVILY_EXCLUDE_DOMAINS", "域名黑名单", hint="逗号分隔"),
            _f("TAVILY_COUNTRY", "国家/地区"),
            _f("TAVILY_CHUNKS_PER_SOURCE", "每个来源的片段数", _INT, default="3"),
            _f("TAVILY_CACHE_EXPIRE", "缓存时间（秒）", _INT, default="1800"),
            _f("TAVILY_TIMEOUT_SECONDS", "请求超时（秒）", _INT, default="20"),
            _f("TAVILY_BASE_URL", "接口地址", default="https://api.tavily.com/search"),
        ],
    },
    {
        "id": "dingtalk_stream",
        "title": "💬 钉钉应用机器人（Stream）",
        "desc": "接收并回复 #命令。机器人已在运行时修改凭证需要重启服务才会生效。",
        "fields": [
            _f("DINGTALK_STREAM_ENABLED", "启用 Stream 机器人", _BOOL, default="false"),
            _f("DINGTALK_CLIENT_ID", "AppKey (Client ID)"),
            _f("DINGTALK_CLIENT_SECRET", "AppSecret (Client Secret)", _SECRET),
            _f("DINGTALK_AGENT_ID", "AgentId"),
        ],
    },
    {
        "id": "dingtalk_notify",
        "title": "🔔 钉钉群机器人通知（Webhook）",
        "desc": "单向推送，例如辩论后的裁判结论。",
        "fields": [
            _f("DINGTALK_ENABLED", "启用通知", _BOOL, default="false"),
            _f("DINGTALK_WEBHOOK", "Webhook 地址", _SECRET, hint="地址中包含 access_token，按密钥处理"),
            _f("DINGTALK_SECRET", "加签密钥", _SECRET),
            _f("DINGTALK_AT_MOBILES", "@ 手机号", hint="逗号分隔"),
            _f("DINGTALK_NOTIFY_ON_DEBATE", "辩论结束后自动推送裁判结论", _BOOL, default="false"),
        ],
    },
    {
        "id": "base",
        "title": "🧱 基础",
        "desc": "",
        "fields": [
            _f("SQLITE_DB_PATH", "SQLite 数据库路径", hint="留空使用 data/stock_analysis.db", restart=True),
            _f("SECRET_KEY", "Flask SECRET_KEY", _SECRET, restart=True),
        ],
    },
]

_FIELDS = {field["key"]: field for group in SETTING_GROUPS for field in group["fields"]}

# 这些值只在进程启动时读取一次，不做热更新
_NO_HOT_RELOAD = {
    "BASE_DIR", "ENV_FILE_PATH", "SQLITE_DB_PATH", "SECRET_KEY",
    "FLASK_HOST", "FLASK_PORT", "FLASK_DEBUG", "SETTINGS_ALLOW_REMOTE",
}
_STREAM_KEYS = {"DINGTALK_STREAM_ENABLED", "DINGTALK_CLIENT_ID", "DINGTALK_CLIENT_SECRET"}


def get_settings():
    """返回分组后的配置项及当前生效值（密钥只返回是否已配置）"""
    groups = []
    for group in SETTING_GROUPS:
        fields = []
        for field in group["fields"]:
            current = (os.environ.get(field["key"]) or "").strip()
            item = dict(field)
            if field["type"] == _SECRET:
                item["is_set"] = bool(current)
                item["value"] = ""
            else:
                item["value"] = current
            fields.append(item)
        groups.append({"id": group["id"], "title": group["title"], "desc": group["desc"], "fields": fields})
    return {"groups": groups, "env_file_exists": os.path.exists(ENV_PATH)}


def update_settings(values=None, clear=None):
    """
    values: {KEY: 新值}；clear: [KEY, ...] 需要清空的键
    返回 {"ok": bool, "error": str, "updated": [...], "restart_required": [...], "notes": [...]}
    """
    values = values if isinstance(values, dict) else {}
    clear = clear if isinstance(clear, list) else []

    changes = {}
    for key in clear:
        if key not in _FIELDS:
            return {"ok": False, "error": f"不支持的配置项: {key}"}
        changes[key] = ""
    for key, raw in values.items():
        field = _FIELDS.get(key)
        if field is None:
            return {"ok": False, "error": f"不支持的配置项: {key}"}
        value, error = _validate(field, raw)
        if error:
            return {"ok": False, "error": f"{field['label']}: {error}"}
        # 密钥输入框留空表示"不修改"，清空必须走 clear
        if field["type"] == _SECRET and value == "":
            continue
        changes[key] = value

    if not changes:
        return {"ok": True, "updated": [], "restart_required": [], "notes": []}

    with _LOCK:
        from services import dingtalk_robot

        stream_was_started = dingtalk_robot.get_stream_status().get("started", False)
        try:
            _write_env_file(changes)
        except OSError as e:
            return {"ok": False, "error": f"写入 .env 失败: {e}"}

        for key, value in changes.items():
            if value == "":
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        _reload_config()

        restart_required = [key for key in changes if _FIELDS[key]["restart"]]
        notes = []
        if _STREAM_KEYS & set(changes):
            if stream_was_started:
                restart_required.extend(sorted(_STREAM_KEYS & set(changes)))
            elif config.DINGTALK_STREAM_ENABLED:
                ok, msg = dingtalk_robot.start_dingtalk_stream_bot()
                notes.append("钉钉 Stream 机器人已启动" if ok else f"钉钉 Stream 机器人未启动: {msg}")

    return {
        "ok": True,
        "updated": sorted(changes),
        "restart_required": restart_required,
        "notes": notes,
    }


def _validate(field, raw):
    value = "" if raw is None else str(raw).strip()
    if "\n" in value or "\r" in value:
        return "", "不能包含换行"
    if len(value) > _MAX_VALUE_LENGTH:
        return "", "内容过长"
    if value == "":
        return "", ""

    if field["type"] == _BOOL:
        lowered = value.lower()
        if lowered not in ("true", "false"):
            return "", "只能是 true 或 false"
        return lowered, ""
    if field["type"] == _INT:
        if not value.isdigit():
            return "", "必须是非负整数"
        return value, ""
    if field["type"] == _SELECT:
        if value not in field["options"]:
            return "", "不在可选范围内"
    return value, ""


def _format_env_line(key, value):
    if _SAFE_VALUE_RE.match(value):
        return f"{key}={value}"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{key}="{escaped}"'


def _write_env_file(changes):
    """就地更新 .env：已存在的键原位替换，其余追加到末尾，注释与顺序保持不变"""
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()

    remaining = dict(changes)
    result = []
    for line in lines:
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        key = match.group(1) if match else None
        if key in changes:
            # 同一个键重复出现时只保留第一处
            if key in remaining:
                result.append(_format_env_line(key, remaining.pop(key)))
            continue
        result.append(line)

    for key, value in remaining.items():
        result.append(_format_env_line(key, value))

    tmp_path = ENV_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write("\n".join(result) + "\n")
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, ENV_PATH)


def _reload_config():
    """重新计算 config，并把新值同步到已经 `from config import X` 的 service 模块"""
    importlib.reload(config)
    names = [n for n in vars(config) if n.isupper() and n not in _NO_HOT_RELOAD]
    for mod_name, module in list(sys.modules.items()):
        if module is None or not mod_name.startswith("services."):
            continue
        for name in names:
            if name in vars(module):
                setattr(module, name, getattr(config, name))
