"""
SQLite 数据库访问层
提供与旧版 get_collection 类似的轻量接口：
- find / find_one
- update_one / insert_one
"""
import json
import os
import re
import sqlite3
import threading
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from config import SQLITE_DB_PATH

_INIT_LOCK = threading.Lock()
_INITIALIZED = False


def _json_default(obj: Any):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    # 兼容 numpy 标量等可转换对象
    if hasattr(obj, "item"):
        try:
            return obj.item()
        except Exception:
            pass
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _get_conn() -> sqlite3.Connection:
    _ensure_db()
    conn = sqlite3.connect(SQLITE_DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_db():
    global _INITIALIZED
    if _INITIALIZED:
        return
    with _INIT_LOCK:
        if _INITIALIZED:
            return

        db_dir = os.path.dirname(SQLITE_DB_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        with sqlite3.connect(SQLITE_DB_PATH, timeout=30, check_same_thread=False) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection TEXT NOT NULL,
                    doc TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_collection_updated_at ON documents(collection, updated_at)"
            )

        _INITIALIZED = True


def _get_field(doc: Dict[str, Any], key: str):
    if "." not in key:
        return doc.get(key)
    value = doc
    for part in key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _match_condition(value: Any, condition: Any) -> bool:
    if not isinstance(condition, dict):
        return value == condition

    # 正则
    if "$regex" in condition:
        pattern = str(condition.get("$regex", ""))
        options = str(condition.get("$options", ""))
        flags = re.IGNORECASE if "i" in options.lower() else 0
        text = "" if value is None else str(value)
        return re.search(pattern, text, flags) is not None

    # 区间比较
    for op, target in condition.items():
        if op == "$options":
            continue
        if op == "$gte":
            if value is None or value < target:
                return False
        elif op == "$lte":
            if value is None or value > target:
                return False
        elif op == "$gt":
            if value is None or value <= target:
                return False
        elif op == "$lt":
            if value is None or value >= target:
                return False
        else:
            # 未知操作符，保守返回 False
            return False
    return True


def _match_query(doc: Dict[str, Any], query: Optional[Dict[str, Any]]) -> bool:
    if not query:
        return True

    for key, cond in query.items():
        if key == "$or":
            if not isinstance(cond, list) or not cond:
                return False
            if not any(_match_query(doc, sub) for sub in cond):
                return False
            continue

        value = _get_field(doc, key)
        if not _match_condition(value, cond):
            return False

    return True


def _apply_projection(doc: Dict[str, Any], projection: Optional[Dict[str, int]]) -> Dict[str, Any]:
    if not projection:
        return dict(doc)

    include_keys = [k for k, v in projection.items() if k != "_id" and bool(v)]
    if include_keys:
        return {k: doc.get(k) for k in include_keys if k in doc}

    exclude_keys = {k for k, v in projection.items() if not bool(v)}
    exclude_keys.add("_id")
    return {k: v for k, v in doc.items() if k not in exclude_keys}


class SQLiteCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def sort(self, field: str, direction: int = 1):
        reverse = direction == -1

        def _key(item):
            val = _get_field(item, field)
            return (val is None, val)

        self._docs.sort(key=_key, reverse=reverse)
        return self

    def limit(self, n: int):
        if isinstance(n, int) and n >= 0:
            self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)

    def __len__(self):
        return len(self._docs)

    def __getitem__(self, item):
        return self._docs[item]


class SQLiteCollection:
    def __init__(self, name: str):
        self.name = name

    def _fetch_rows(self) -> Iterable[Tuple[int, Dict[str, Any]]]:
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT id, doc FROM documents WHERE collection = ?",
                (self.name,),
            ).fetchall()

        result = []
        for row in rows:
            try:
                result.append((int(row["id"]), json.loads(row["doc"])))
            except Exception:
                continue
        return result

    def find(self, query: Optional[Dict[str, Any]] = None, projection: Optional[Dict[str, int]] = None):
        docs = []
        for _, doc in self._fetch_rows():
            if _match_query(doc, query):
                docs.append(_apply_projection(doc, projection))
        return SQLiteCursor(docs)

    def find_one(self, query: Optional[Dict[str, Any]] = None, projection: Optional[Dict[str, int]] = None):
        cursor = self.find(query, projection).limit(1)
        for item in cursor:
            return item
        return None

    def update_one(self, query: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        set_payload = update.get("$set", {}) if isinstance(update, dict) else {}

        # 1) 先尝试更新已存在文档
        for row_id, doc in self._fetch_rows():
            if _match_query(doc, query):
                doc.update(set_payload)
                encoded = json.dumps(doc, ensure_ascii=False, default=_json_default)
                with _get_conn() as conn:
                    conn.execute(
                        "UPDATE documents SET doc = ?, updated_at = datetime('now') WHERE id = ?",
                        (encoded, row_id),
                    )
                return {"matched_count": 1, "modified_count": 1, "upserted_id": None}

        # 2) 不存在则 upsert
        if upsert:
            doc = {}
            for k, v in (query or {}).items():
                if k.startswith("$") or isinstance(v, dict):
                    continue
                doc[k] = v
            doc.update(set_payload)
            encoded = json.dumps(doc, ensure_ascii=False, default=_json_default)
            with _get_conn() as conn:
                cur = conn.execute(
                    "INSERT INTO documents(collection, doc) VALUES(?, ?)",
                    (self.name, encoded),
                )
            return {"matched_count": 0, "modified_count": 0, "upserted_id": cur.lastrowid}

        return {"matched_count": 0, "modified_count": 0, "upserted_id": None}

    def insert_one(self, doc: Dict[str, Any]):
        encoded = json.dumps(doc, ensure_ascii=False, default=_json_default)
        with _get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO documents(collection, doc) VALUES(?, ?)",
                (self.name, encoded),
            )
        return {"inserted_id": cur.lastrowid}

    # 兼容旧接口（SQLite 下无意义，保留为 no-op）
    def create_index(self, *args, **kwargs):
        return None


def get_db():
    """兼容旧代码：返回 SQLite DB 路径。"""
    _ensure_db()
    return SQLITE_DB_PATH


def get_collection(name: str):
    """获取集合对象（SQLite 文档存储实现）"""
    return SQLiteCollection(name)
