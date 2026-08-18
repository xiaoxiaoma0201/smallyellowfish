"""项目级内存状态。这里模拟会话、反馈、缓存、checkpoint 和幂等记录。

项目边界：这些全局对象用于本地快照观察，不是生产级持久化存储。
真实系统应把会话、反馈、缓存、checkpoint 和幂等记录放进数据库、
Redis、队列或审计系统，并处理过期、并发、权限和清理策略。
"""

from __future__ import annotations

from typing import Any

from api.schemas import FeedbackRecord

MESSAGE_COUNT_BY_SESSION: dict[str, int] = {}
FEEDBACK_RECORDS: list[FeedbackRecord] = []
BACKFILLED_CASES: list[dict[str, Any]] = []
COMMON_HIT_CACHE: dict[str, dict[str, Any]] = {}
WORKFLOW_CHECKPOINTS: dict[tuple[str, str], dict[str, Any]] = {}
SUBMITTED_ACTIONS: dict[str, dict[str, Any]] = {}


def load_persisted_state() -> None:
    """启动时从 SQLite 回填消息计数/工作流 checkpoint/幂等记录，支持断线恢复 HITL 审批。"""
    from state.persistence import load_namespace

    for session_id, count in load_namespace("message_count").items():
        MESSAGE_COUNT_BY_SESSION[session_id] = count
    for key, checkpoint in load_namespace("workflow_checkpoint").items():
        WORKFLOW_CHECKPOINTS[tuple(key.split("|", 1))] = checkpoint
    SUBMITTED_ACTIONS.update(load_namespace("submitted_action"))
