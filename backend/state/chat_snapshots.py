"""按会话保存完整对话快照（用户消息 + 完整 ChatResponse），供商城会话联动与调试台主对话区还原。

Trace 只记录事件流；这里保存每轮的完整响应结构（含 reasoning / tool_calls / citations /
clarification / session_state），使调试台可以直接把商城会话渲染成与本地调试一致的对话。
"""

from __future__ import annotations

import threading
import time
from typing import Any

from api.schemas import ChatRequest, ChatResponse
from state.persistence import load_namespace, save as persist_save

_SESSION_SNAPSHOTS: dict[str, list[dict[str, Any]]] = {}
_SESSION_META: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def load_chat_snapshots() -> None:
    """启动时从 SQLite 回填对话快照，重启后商城会话联动/调试台历史可恢复。"""
    for session_id, record in load_namespace("chat_snapshot").items():
        _SESSION_SNAPSHOTS[session_id] = record.get("turns", [])
        if record.get("meta"):
            _SESSION_META[session_id] = record["meta"]


def record_chat_snapshot(request: ChatRequest, response: ChatResponse) -> None:
    """记录一轮完整的商城/调试对话快照。"""
    try:
        response_dict = response.model_dump(mode="json")
    except Exception:
        response_dict = {"answer": response.answer}
    now = time.time()
    with _LOCK:
        turns = _SESSION_SNAPSHOTS.setdefault(request.session_id, [])
        turns.append(
            {
                "role": "user",
                "content": request.user_message,
                "created_at": now,
            }
        )
        turns.append(
            {
                "role": "assistant",
                "content": response.answer,
                "response": response_dict,
                "created_at": now,
            }
        )
        _SESSION_META[request.session_id] = {
            "session_id": request.session_id,
            "user_id": request.runtime_user_id,
            "nickname": request.runtime_nickname,
            "role": request.runtime_role,
            "updated_at": now,
            "last_message": request.user_message,
            "turn_count": len(turns) // 2,
        }
        persist_save(
            "chat_snapshot",
            request.session_id,
            {"turns": list(turns), "meta": dict(_SESSION_META[request.session_id])},
        )


def list_recent_sessions(limit: int = 10) -> list[dict[str, Any]]:
    """按最近更新时间倒序返回会话元信息（联动面板轮询用）。"""
    with _LOCK:
        sessions = sorted(_SESSION_META.values(), key=lambda meta: meta["updated_at"], reverse=True)
        return sessions[:limit]


def list_session_messages(session_id: str) -> list[dict[str, Any]]:
    """返回指定会话的完整对话消息列表（role/content/response）。"""
    with _LOCK:
        turns = _SESSION_SNAPSHOTS.get(session_id, [])
        return [dict(turn) for turn in turns]
