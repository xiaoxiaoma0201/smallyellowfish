"""轻量 SQLite 持久化层：把会话记忆/对话快照/消息计数/工作流 checkpoint/幂等记录/trace 落盘，
支持服务重启后的断线恢复与多轮审计（零外部依赖，Python 内置 sqlite3，不动 Docker/Java）。

边界说明：这是 Demo 级的单机持久化（单进程 uvicorn + SQLite 文件），
生产环境应替换为 Redis/数据库并补充并发、过期与清理策略。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.environ.get("AGENT_SESSION_DB", os.path.join(_DATA_DIR, "session_store.db"))

_LOCK = threading.Lock()


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_schema() -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kv_store (
                namespace TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (namespace, key)
            )
            """
        )


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def save(namespace: str, key: str, value: Any) -> None:
    """写入/覆盖一条记录；失败只告警，不阻断对话主流程。"""
    try:
        with _LOCK, _connect() as conn:
            conn.execute(
                "INSERT INTO kv_store (namespace, key, value, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(namespace, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (namespace, key, _dump(value), time.time()),
            )
    except Exception as exc:  # 持久化故障不应拖垮对话
        print(f"[persistence] save {namespace}/{key} 失败: {exc}", flush=True)


def delete(namespace: str, key: str) -> None:
    """删除一条记录（如评测清空 trace 时同步清理磁盘，避免串扰）。"""
    try:
        with _LOCK, _connect() as conn:
            conn.execute("DELETE FROM kv_store WHERE namespace = ? AND key = ?", (namespace, key))
    except Exception as exc:
        print(f"[persistence] delete {namespace}/{key} 失败: {exc}", flush=True)


def load_namespace(namespace: str) -> dict[str, Any]:
    """读取某个 namespace 的全部记录为 {key: value}。"""
    try:
        with _LOCK, _connect() as conn:
            rows = conn.execute(
                "SELECT key, value FROM kv_store WHERE namespace = ?", (namespace,)
            ).fetchall()
        return {key: json.loads(value) for key, value in rows}
    except Exception as exc:
        print(f"[persistence] load {namespace} 失败: {exc}", flush=True)
        return {}


def init() -> None:
    """应用启动时建表；各状态模块在启动流程里自行 load 回填。"""
    try:
        _init_schema()
    except Exception as exc:
        print(f"[persistence] init 失败，将以纯内存模式运行: {exc}", flush=True)
