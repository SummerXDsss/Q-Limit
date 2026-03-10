"""
钉钉应用机器人（Stream 模式）接入
参考:
- https://open.dingtalk.com/document/development/development-robot-overview
- https://developers.dingtalk.com/document/app/quickly-build-chatbot
"""
import hashlib
import json
import threading

import requests

from config import (
    DINGTALK_STREAM_ENABLED,
    DINGTALK_CLIENT_ID,
    DINGTALK_CLIENT_SECRET,
)
from models.database import get_collection
from services.bot_skills import handle_incoming_message

PROCESSED_MESSAGE_COLLECTION = "bot_processed_messages"

_client_thread = None
_started = False


def _extract_text_content(incoming, raw):
    if getattr(incoming, "text", None):
        return (incoming.text.content or "").strip()

    text_block = raw.get("text") or {}
    if isinstance(text_block, dict):
        return str(text_block.get("content", "")).strip()
    if isinstance(text_block, str):
        return text_block.strip()
    return ""


def _extract_message_id(incoming, raw):
    for key in ("msgId", "messageId", "processQueryKey"):
        value = raw.get(key) or getattr(incoming, key, None)
        if value:
            return str(value)

    try:
        seed = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        seed = str(raw)
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()


def _is_duplicate_message(message_id):
    if not message_id:
        return False
    doc = get_collection(PROCESSED_MESSAGE_COLLECTION).find_one(
        {"message_id": message_id},
        {"_id": 0},
    )
    return bool(doc)


def _mark_message_processed(message_id, raw):
    if not message_id:
        return
    get_collection(PROCESSED_MESSAGE_COLLECTION).update_one(
        {"message_id": message_id},
        {"$set": {
            "message_id": message_id,
            "conversation_id": raw.get("conversationId") or raw.get("conversation_id") or "",
            "sender_id": raw.get("senderId") or raw.get("sender_id") or "",
            "created_at": raw.get("createAt") or raw.get("create_at") or "",
        }},
        upsert=True,
    )


def _send_session_webhook(webhook, payload):
    resp = requests.post(webhook, json=payload, timeout=10)
    resp.raise_for_status()
    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if isinstance(data, dict) and data.get("errcode") not in (None, 0):
        raise RuntimeError(data.get("errmsg") or f"webhook 返回 errcode={data.get('errcode')}")


def _reply_with_payload(handler, result, incoming, raw):
    payload = result.get("reply_payload") or {}
    reply_type = str(payload.get("type") or "text").lower()
    session_webhook = raw.get("sessionWebhook") or raw.get("session_webhook") or getattr(incoming, "session_webhook", None)
    at_user_ids = [uid for uid in [raw.get("senderStaffId") or raw.get("sender_staff_id") or getattr(incoming, "sender_staff_id", None)] if uid]

    if reply_type == "markdown":
        title = str(payload.get("title") or "Q-Limit")
        text = str(payload.get("text") or result.get("reply") or "")
        image_url = str(payload.get("image_url") or "").strip()
        if image_url and image_url not in text:
            text = f"![]({image_url})\n\n{text}"

        body = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": text},
            "at": {"atUserIds": at_user_ids},
        }
        if session_webhook:
            _send_session_webhook(session_webhook, body)
            return

    text = str(payload.get("text") or result.get("reply") or "")
    if session_webhook:
        body = {
            "msgtype": "text",
            "text": {"content": text},
            "at": {"atUserIds": at_user_ids},
        }
        _send_session_webhook(session_webhook, body)
        return

    handler.reply_text(text, incoming)


def _build_handler(dingtalk_stream):
    class SkillCommandHandler(dingtalk_stream.ChatbotHandler):
        def process(self, callback: dingtalk_stream.CallbackMessage):
            incoming = None
            try:
                raw = callback.data or {}
                incoming = dingtalk_stream.ChatbotMessage.from_dict(raw)
                text_content = _extract_text_content(incoming, raw)
                if not text_content:
                    return dingtalk_stream.AckMessage.STATUS_OK, "empty"

                message_id = _extract_message_id(incoming, raw)
                if _is_duplicate_message(message_id):
                    return dingtalk_stream.AckMessage.STATUS_OK, "duplicate"

                _mark_message_processed(message_id, raw)

                context = {
                    "sender_staff_id": incoming.sender_staff_id,
                    "sender_id": incoming.sender_id,
                    "sender_nick": incoming.sender_nick,
                    "conversation_id": incoming.conversation_id,
                    "conversation_type": incoming.conversation_type,
                }
                result = handle_incoming_message(text_content, context)
                if result.get("handled") and (result.get("reply") or result.get("reply_payload")):
                    _reply_with_payload(self, result, incoming, raw)
                return dingtalk_stream.AckMessage.STATUS_OK, "OK"
            except Exception as e:
                try:
                    if incoming is not None:
                        self.reply_text(f"处理消息失败: {e}", incoming)
                except Exception:
                    pass
                return dingtalk_stream.AckMessage.STATUS_OK, f"error:{e}"

    return SkillCommandHandler()


def start_dingtalk_stream_bot():
    """
    启动钉钉 Stream 机器人监听线程
    """
    global _client_thread, _started

    if _started:
        return True, "already started"

    if not DINGTALK_STREAM_ENABLED:
        return False, "DINGTALK_STREAM_ENABLED=false"

    if not DINGTALK_CLIENT_ID or not DINGTALK_CLIENT_SECRET:
        return False, "缺少 DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET"

    try:
        import dingtalk_stream
    except Exception as e:
        return False, f"未安装 dingtalk-stream 或导入失败: {e}"

    def _run():
        credential = dingtalk_stream.Credential(
            client_id=DINGTALK_CLIENT_ID,
            client_secret=DINGTALK_CLIENT_SECRET,
        )
        client = dingtalk_stream.DingTalkStreamClient(credential)
        client.register_callback_handler(
            dingtalk_stream.ChatbotMessage.TOPIC,
            _build_handler(dingtalk_stream),
        )
        client.start_forever()

    _client_thread = threading.Thread(target=_run, daemon=True, name="dingtalk-stream-bot")
    _client_thread.start()
    _started = True
    return True, "started"


def get_stream_status():
    if _client_thread and _client_thread.is_alive():
        return {"enabled": DINGTALK_STREAM_ENABLED, "started": True, "alive": True}
    return {"enabled": DINGTALK_STREAM_ENABLED, "started": _started, "alive": False}
