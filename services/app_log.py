"""
运行日志
- log() 把一条结构化日志写进 app_logs collection，同时打印到标准输出
- 分类：trade（交易）/ bot（机器人）/ ai（模型调用）/ settings（配置变更）/ system（启动、连接等）
- 调用方负责不把口令、密钥放进 message / detail；这里不做脱敏
- 写日志永远不抛异常，不能因为记日志影响业务
"""
import threading
import time
from datetime import datetime

from config import LOG_RETENTION_DAYS
from models.database import get_collection

LOG_COLLECTION = "app_logs"
CATEGORIES = ("trade", "bot", "ai", "settings", "system")
LEVELS = ("info", "warning", "error")
_PRUNE_EVERY = 200

_lock = threading.Lock()
_writes_since_prune = _PRUNE_EVERY  # 进程启动后的第一条日志就清理一次


def log(category, message, level="info", detail=None):
    global _writes_since_prune
    level = level if level in LEVELS else "info"
    print(f"[{category}] {level.upper()} {message}")
    try:
        get_collection(LOG_COLLECTION).insert_one({
            "ts": time.time(),
            "time": datetime.now().isoformat(timespec="seconds"),
            "category": category,
            "level": level,
            "message": str(message)[:500],
            "detail": detail or {},
        })
        with _lock:
            _writes_since_prune += 1
            need_prune = _writes_since_prune >= _PRUNE_EVERY
            if need_prune:
                _writes_since_prune = 0
        if need_prune:
            prune()
    except Exception as e:
        print(f"[app_log] 写入日志失败: {e}")


def prune():
    """删除超过保留天数的日志，返回删除条数"""
    days = max(1, int(LOG_RETENTION_DAYS or 14))
    cutoff = time.time() - days * 86400
    return get_collection(LOG_COLLECTION).delete_many({"ts": {"$lt": cutoff}})


def query(category="", level="", keyword="", limit=200):
    """按时间倒序返回日志"""
    condition = {}
    if category in CATEGORIES:
        condition["category"] = category
    if level in LEVELS:
        condition["level"] = level
    rows = list(get_collection(LOG_COLLECTION).find(condition, {"_id": 0}).sort("ts", -1))
    keyword = (keyword or "").strip().lower()
    if keyword:
        rows = [
            row for row in rows
            if keyword in str(row.get("message", "")).lower() or keyword in str(row.get("detail", "")).lower()
        ]
    return rows[:max(1, min(int(limit or 200), 1000))]
