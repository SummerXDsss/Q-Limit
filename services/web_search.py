"""
Tavily 全网搜索服务
- 支持更完整的官方参数
- 支持 SQLite 缓存
- 支持股票场景查询词优化
"""
import hashlib
import json
from datetime import datetime, timedelta
from urllib.parse import urlparse

import requests

from config import (
    TAVILY_API_KEY,
    TAVILY_AUTO_PARAMETERS,
    TAVILY_BASE_URL,
    TAVILY_CACHE_EXPIRE,
    TAVILY_CHUNKS_PER_SOURCE,
    TAVILY_COUNTRY,
    TAVILY_EXCLUDE_DOMAINS,
    TAVILY_INCLUDE_ANSWER,
    TAVILY_INCLUDE_DOMAINS,
    TAVILY_INCLUDE_FAVICON,
    TAVILY_INCLUDE_IMAGE_DESCRIPTIONS,
    TAVILY_INCLUDE_IMAGES,
    TAVILY_INCLUDE_RAW_CONTENT,
    TAVILY_MAX_RESULTS,
    TAVILY_SEARCH_DEPTH,
    TAVILY_TIME_RANGE,
    TAVILY_TIMEOUT_SECONDS,
)
from models.database import get_collection

CACHE_COLLECTION = "web_search_cache"
ALLOWED_TOPICS = {"general", "news", "finance"}
ALLOWED_SEARCH_DEPTHS = {"basic", "advanced"}
ALLOWED_TIME_RANGES = {"day", "week", "month", "year", "d", "w", "m", "y"}


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = []
        for item in value:
            raw_items.extend(str(item).split(","))
    else:
        raw_items = [str(value)]

    result = []
    for item in raw_items:
        cleaned = str(item).strip()
        if cleaned:
            result.append(cleaned)
    return result


def _coerce_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raw = str(value).strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _coerce_mode(value, default=False, allowed_modes=None):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raw = str(value).strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    if allowed_modes and raw in allowed_modes:
        return raw
    return default


def _normalize_domains(value):
    seen = set()
    domains = []
    for item in _as_list(value):
        parsed = urlparse(item if "://" in item else f"https://{item}")
        host = (parsed.netloc or parsed.path or "").strip().lower().strip("/")
        if host.startswith("www."):
            host = host[4:]
        if not host or host in seen:
            continue
        seen.add(host)
        domains.append(host)
    return domains[:50]


def _normalize_date(value):
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) >= 10:
        return text[:10]
    return ""


def _normalize_options(
    query,
    max_results=None,
    topic="news",
    search_depth=None,
    time_range=None,
    start_date=None,
    end_date=None,
    days=None,
    include_answer=None,
    include_raw_content=None,
    include_images=None,
    include_image_descriptions=None,
    include_favicon=None,
    include_domains=None,
    exclude_domains=None,
    country=None,
    auto_parameters=None,
    chunks_per_source=None,
    use_cache=True,
):
    limit = max(1, min(int(max_results or TAVILY_MAX_RESULTS), 20))
    topic = str(topic or "news").strip().lower()
    if topic not in ALLOWED_TOPICS:
        topic = "news"

    search_depth = str(search_depth or TAVILY_SEARCH_DEPTH or "basic").strip().lower()
    if search_depth not in ALLOWED_SEARCH_DEPTHS:
        search_depth = "basic"

    time_range = str(time_range or TAVILY_TIME_RANGE or "").strip().lower()
    if time_range not in ALLOWED_TIME_RANGES:
        time_range = ""

    days = None if days in (None, "") else max(1, min(int(days), 365))
    start_date = _normalize_date(start_date)
    end_date = _normalize_date(end_date)

    include_answer = _coerce_mode(
        include_answer,
        default=TAVILY_INCLUDE_ANSWER,
        allowed_modes={"basic", "advanced"},
    )
    include_raw_content = _coerce_mode(
        include_raw_content,
        default=TAVILY_INCLUDE_RAW_CONTENT,
        allowed_modes={"text", "markdown"},
    )
    include_images = _coerce_bool(include_images, TAVILY_INCLUDE_IMAGES)
    include_image_descriptions = _coerce_bool(
        include_image_descriptions,
        TAVILY_INCLUDE_IMAGE_DESCRIPTIONS,
    )
    include_favicon = _coerce_bool(include_favicon, TAVILY_INCLUDE_FAVICON)
    auto_parameters = _coerce_bool(auto_parameters, TAVILY_AUTO_PARAMETERS)
    use_cache = _coerce_bool(use_cache, True)
    include_domains = _normalize_domains(include_domains if include_domains is not None else TAVILY_INCLUDE_DOMAINS)
    exclude_domains = _normalize_domains(exclude_domains if exclude_domains is not None else TAVILY_EXCLUDE_DOMAINS)
    country = str(country or TAVILY_COUNTRY or "").strip().lower()
    chunks_per_source = max(1, min(int(chunks_per_source or TAVILY_CHUNKS_PER_SOURCE or 3), 3))

    payload = {
        "query": str(query).strip(),
        "search_depth": search_depth,
        "topic": topic,
        "max_results": limit,
        "include_answer": include_answer,
        "include_raw_content": include_raw_content,
        "include_images": include_images,
        "include_image_descriptions": include_image_descriptions,
        "include_favicon": include_favicon,
        "auto_parameters": auto_parameters,
    }

    if include_domains:
        payload["include_domains"] = include_domains
    if exclude_domains:
        payload["exclude_domains"] = exclude_domains
    if country and topic == "general":
        payload["country"] = country
    if time_range:
        payload["time_range"] = time_range
    if start_date:
        payload["start_date"] = start_date
    if end_date:
        payload["end_date"] = end_date
    if days and topic in ("news", "finance"):
        payload["days"] = days
    if search_depth == "advanced":
        payload["chunks_per_source"] = chunks_per_source

    return {
        "payload": payload,
        "use_cache": use_cache,
        "cache_ttl": max(0, int(TAVILY_CACHE_EXPIRE)),
    }


def _cache_key(payload):
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _get_cache(cache_key):
    try:
        col = get_collection(CACHE_COLLECTION)
        doc = col.find_one({"cache_key": cache_key, "active": True}, {"_id": 0})
        if not doc:
            return None
        expires_at = _parse_iso(doc.get("expires_at"))
        if expires_at and datetime.now() >= expires_at:
            return None
        data = dict(doc.get("data") or {})
        data["cached"] = True
        return data
    except Exception:
        return None


def _save_cache(cache_key, payload, data, ttl_seconds):
    if ttl_seconds <= 0:
        return
    try:
        col = get_collection(CACHE_COLLECTION)
        doc = {
            "cache_key": cache_key,
            "query": payload.get("query", ""),
            "payload": payload,
            "data": data,
            "active": True,
            "expires_at": (datetime.now() + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds"),
            "updated_at": _now_iso(),
            "created_at": _now_iso(),
        }
        col.update_one({"cache_key": cache_key}, {"$set": doc}, upsert=True)
    except Exception as e:
        print(f"[web_search] cache save failed: {e}")


def _source_from_url(url):
    host = urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _trim_text(value, limit):
    text = " ".join((value or "").strip().split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _normalize_results(raw_results):
    seen = set()
    items = []
    for row in raw_results or []:
        url = (row.get("url") or "").strip()
        title = (row.get("title") or "").strip()
        uniq = url or title
        if not uniq or uniq in seen:
            continue
        seen.add(uniq)

        item = {
            "title": title,
            "url": url,
            "content": _trim_text(row.get("content", "") or "", 600),
            "score": row.get("score", 0),
            "published_date": str(row.get("published_date") or row.get("publishedDate") or "").strip(),
            "source": (row.get("source") or "").strip() or _source_from_url(url),
        }

        favicon = (row.get("favicon") or "").strip()
        if favicon:
            item["favicon"] = favicon

        raw_content = row.get("raw_content")
        if raw_content:
            item["raw_content"] = _trim_text(raw_content, 4000)

        items.append(item)
    return items


def _build_response(query, payload, raw, cached=False):
    return {
        "query": query,
        "answer": (raw.get("answer") or "").strip(),
        "results": _normalize_results(raw.get("results") or []),
        "images": raw.get("images") or [],
        "response_time": raw.get("response_time"),
        "request_id": raw.get("request_id") or raw.get("id") or "",
        "usage": raw.get("usage") or {},
        "auto_parameters": raw.get("auto_parameters") or {},
        "filters": {
            "topic": payload.get("topic", ""),
            "search_depth": payload.get("search_depth", ""),
            "max_results": payload.get("max_results", 0),
            "time_range": payload.get("time_range", ""),
            "days": payload.get("days"),
            "include_domains": payload.get("include_domains", []),
            "exclude_domains": payload.get("exclude_domains", []),
        },
        "cached": cached,
        "error": "",
    }


def _error_response(query, payload, error):
    return {
        "query": query,
        "answer": "",
        "results": [],
        "images": [],
        "response_time": None,
        "request_id": "",
        "usage": {},
        "auto_parameters": {},
        "filters": {
            "topic": payload.get("topic", ""),
            "search_depth": payload.get("search_depth", ""),
            "max_results": payload.get("max_results", 0),
            "time_range": payload.get("time_range", ""),
            "days": payload.get("days"),
            "include_domains": payload.get("include_domains", []),
            "exclude_domains": payload.get("exclude_domains", []),
        },
        "cached": False,
        "error": str(error),
    }


def search_web(query, max_results=None, topic="news", **kwargs):
    """
    调用 Tavily 搜索。
    支持参数：
    - topic: general|news|finance
    - search_depth: basic|advanced
    - time_range: day|week|month|year
    - start_date/end_date: YYYY-MM-DD
    - days: 最近 N 天（news/finance）
    - include_domains/exclude_domains: 域名白名单/黑名单
    - include_answer/include_raw_content/include_images 等 Tavily 原生参数
    """
    query = (query or "").strip()
    if not query:
        return _error_response("", {"topic": topic, "max_results": max_results or 0}, "搜索关键词不能为空")

    if not TAVILY_API_KEY:
        return _error_response(query, {"topic": topic, "max_results": max_results or 0}, "未配置 TAVILY_API_KEY")

    opts = _normalize_options(query=query, max_results=max_results, topic=topic, **kwargs)
    payload = opts["payload"]
    cache_key = _cache_key(payload)

    if opts["use_cache"]:
        cached = _get_cache(cache_key)
        if cached:
            return cached

    headers = {
        "Authorization": f"Bearer {TAVILY_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            TAVILY_BASE_URL,
            headers=headers,
            json=payload,
            timeout=max(5, TAVILY_TIMEOUT_SECONDS),
        )
        resp.raise_for_status()
        raw = resp.json()
        data = _build_response(query, payload, raw, cached=False)
        _save_cache(cache_key, payload, data, opts["cache_ttl"])
        return data
    except requests.HTTPError as e:
        detail = ""
        try:
            body = resp.json()
            detail = body.get("detail") or body.get("message") or body.get("error") or ""
        except Exception:
            detail = (getattr(resp, "text", "") or "")[:300]
        msg = f"Tavily 请求失败: HTTP {getattr(resp, 'status_code', '?')}"
        if detail:
            msg = f"{msg} - {detail}"
        return _error_response(query, payload, msg)
    except Exception as e:
        return _error_response(query, payload, f"Tavily 请求异常: {e}")


def _resolve_stock_context(stock_code):
    try:
        from services import stock_data

        rows = stock_data.search_stock(stock_code)
        if not rows:
            return {"code": stock_code.strip().upper()}

        target = stock_code.strip().upper()
        for item in rows:
            if str(item.get("code", "")).strip().upper() == target:
                return item
        return rows[0]
    except Exception:
        return {"code": stock_code.strip().upper()}


def _build_stock_query(stock_code, keyword=None):
    if keyword and str(keyword).strip():
        return str(keyword).strip()

    stock = _resolve_stock_context(stock_code)
    parts = []
    for key in ("code", "name", "name_en"):
        value = str(stock.get(key, "")).strip()
        if value and value not in parts:
            parts.append(value)

    market = str(stock.get("market", "")).strip().upper()
    if market in ("US", "HK"):
        suffix = "stock latest earnings guidance rating price target sec filing news"
    else:
        suffix = "股票 最新 财报 业绩 指引 评级 公告 研报"

    return " ".join(parts + [suffix]).strip()


def search_stock_web(stock_code, keyword=None, limit=5, topic="finance", **kwargs):
    """股票场景搜索：优先 finance，必要时回退 news。"""
    if not stock_code:
        return _error_response("", {"topic": topic, "max_results": limit}, "stock_code 不能为空")

    query = _build_stock_query(stock_code, keyword=keyword)
    primary = search_web(
        query=query,
        max_results=limit,
        topic=topic,
        time_range=kwargs.pop("time_range", "month"),
        **kwargs,
    )
    if primary.get("error") or primary.get("results"):
        primary["stock_code"] = stock_code
        return primary

    fallback = search_web(query=query, max_results=limit, topic="news", **kwargs)
    fallback["stock_code"] = stock_code
    return fallback


def get_web_search_for_ai(stock_code, limit=3):
    """
    返回更适合 AI 消费的全网搜索摘要。
    """
    result = search_stock_web(
        stock_code,
        limit=limit,
        topic="finance",
        time_range="month",
        include_answer=TAVILY_INCLUDE_ANSWER,
    )
    if result.get("error"):
        return {
            "stock_code": stock_code,
            "query": "",
            "answer": "",
            "summary": result["error"],
            "results": [],
            "cached": result.get("cached", False),
        }

    rows = []
    lines = []
    answer = (result.get("answer") or "").strip()
    if answer:
        lines.append(f"Tavily摘要: {_trim_text(answer, 220)}")

    for idx, item in enumerate((result.get("results") or [])[:limit], 1):
        row = {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "content": _trim_text(item.get("content", ""), 220),
            "source": item.get("source", ""),
            "published_date": item.get("published_date", ""),
        }
        rows.append(row)

        meta = [x for x in (row["source"], row["published_date"]) if x]
        line = f"{idx}. {row['title']}"
        if meta:
            line += f" ({', '.join(meta)})"
        if row["content"]:
            line += f": {row['content']}"
        lines.append(line)

    summary = "\n".join(lines) if lines else "未检索到可用全网搜索结果。"
    return {
        "stock_code": stock_code,
        "query": result.get("query", ""),
        "answer": answer,
        "summary": summary,
        "results": rows,
        "cached": result.get("cached", False),
    }
