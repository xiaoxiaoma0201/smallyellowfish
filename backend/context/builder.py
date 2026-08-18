"""把 Runtime Context、短期记忆和历史消息整理成受控模型上下文。"""

from __future__ import annotations

import json
import re
from typing import Any

from api.schemas import ChatRequest, HistoryMessage, Intent
from safety.source_guard import inspect_source
from state.persistence import load_namespace, save as persist_save
from tools.planning import estimate_tokens


SESSION_MEMORIES: dict[str, dict[str, Any]] = {}
# 最近窗口：保留最近 8 条消息（约 4 轮对话）作为"最近一段上下文"。
RECENT_WINDOW_SIZE = 8
# Token 预算：约 1200 汉字（estimate_tokens=len//2），足够覆盖最近 8-12 条中文短消息，
# 模型既能理解最近一段语义，又不会因上下文过长引入噪声导致幻觉。
MAX_HISTORY_TOKENS = 1200
# 窗口外回溯硬上限：当窗口内不足以理解用户意思时，允许回溯更早历史，
# 但受此上限约束，防止长对话把所有历史塞进上下文。
MAX_BACKTRACK_TOKENS = 2000
# 回溯词表：当前消息命中这些实体/意图词时，历史中含同类词的消息会被回溯保留，
# 即使它已经超出最近窗口——"窗口内不够就往前想"。
_CONTEXT_BACKTRACK_TERMS = (
    "订单", "订单号", "退", "换", "退款", "退货", "发货", "物流", "快递", "到货",
    "签收", "收货", "商品", "购物车", "砍价", "议价", "运费", "邮费", "发票",
    "成色", "验货", "真假", "假货", "风险", "举报", "客服", "转账", "收款",
)
_PHONE_PATTERN = re.compile(r"\b1[3-9]\d{9}\b")
_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def load_session_memories() -> None:
    """启动时从 SQLite 回填会话记忆，重启后多轮承接/身份/已见订单不丢。"""
    for session_id, memory in load_namespace("session_memory").items():
        SESSION_MEMORIES[session_id] = memory


def current_memory(session_id: str, runtime_user_id: str | None = None) -> dict[str, Any]:
    """短期记忆保存当前会话内已验证的订单/商品、意图历史与身份信号（多值轨迹，非单槽位）。"""
    memory = SESSION_MEMORIES.get(session_id)
    if memory is None or (runtime_user_id and memory.get("runtime_user_id") not in {None, runtime_user_id}):
        memory = {
            "runtime_user_id": runtime_user_id,
            "last_order_id": None,
            "last_product_name": None,
            "recent_intent": None,
            "low_risk_preferences": {},
            "write_decisions": [],
            "excluded_items": [],
            # 结构化会话轨迹：多值追加，解决"那笔订单/之前聊的"对不上早前轮次的问题。
            "seen_order_ids": [],
            "seen_product_names": [],
            "intent_history": [],
            "identity_confirm": None,
            "ttl": "session",
        }
        SESSION_MEMORIES[session_id] = memory
        persist_save("session_memory", session_id, memory)
    elif runtime_user_id and memory.get("runtime_user_id") is None:
        memory["runtime_user_id"] = runtime_user_id
        persist_save("session_memory", session_id, memory)
    return memory


def build_context(request: ChatRequest, explicit_order_id: str | None) -> tuple[str | None, dict[str, Any], dict[str, Any]]:
    """按 explicit > Runtime Context > Session Memory 的顺序选择订单并压缩历史。"""
    memory = current_memory(request.session_id, request.runtime_user_id)
    page = request.runtime_context or {}
    page_order_id = page.get("current_order_id") or page.get("relatedOrderNo")
    chosen_order_id = explicit_order_id or page_order_id or memory.get("last_order_id")
    conflicts: list[str] = []
    if page_order_id and memory.get("last_order_id") and page_order_id != memory["last_order_id"] and not explicit_order_id:
        conflicts.append("order_id: 页面 Runtime Context 与 Session Memory 冲突，采用页面订单。")
    if any(term in request.user_message for term in ("我是VIP", "我是 VIP", "我是黑卡")):
        if (request.runtime_member_level or "unknown").lower() not in {"vip", "black", "黑卡"}:
            conflicts.append("member_level: 用户自称与 Runtime Context 冲突，采用系统会员等级。")
    if any(term in request.user_message for term in ("已经批准", "主管同意", "客服说可以退")):
        conflicts.append("refund_approval: 用户说法不能覆盖 Workflow 审批状态。")

    kept_history, dropped_history = _compress_history(request.history_messages, chosen_order_id, request.user_message)
    # user_id 只在服务端工具层使用；模型只看到做过最小化处理的非敏感运行标签。
    model_context = [
        f"[runtime_context/trusted] member_level={request.runtime_member_level or 'unknown'}, risk_level={request.runtime_risk_level or 'unknown'}",
    ]
    source_reports: list[dict[str, Any]] = []
    user_report = inspect_source("user_message", request.user_message)
    source_reports.append({key: value for key, value in user_report.items() if key != "sanitized_content"})
    for item in kept_history:
        report = inspect_source("history_messages", _redact_history(item.content))
        source_reports.append({key: value for key, value in report.items() if key != "sanitized_content"})
        model_context.append(f"[history/session] {item.role}: {report['sanitized_content']}")
    if chosen_order_id:
        model_context.append(f"[order_reference/session] chosen_order_id={chosen_order_id}")
    # 结构化会话轨迹（全部为工具验证事实，非模型生成）：用户提过的订单/商品/意图序列/身份。
    track_parts: list[str] = []
    if memory.get("seen_order_ids"):
        track_parts.append(f"seen_orders={json.dumps(memory['seen_order_ids'], ensure_ascii=False)}")
    if memory.get("seen_product_names"):
        track_parts.append(f"seen_products={json.dumps(memory['seen_product_names'], ensure_ascii=False)}")
    if memory.get("intent_history"):
        track_parts.append(f"intent_seq={json.dumps(memory['intent_history'][-10:], ensure_ascii=False)}")
    if memory.get("identity_confirm"):
        track_parts.append(f"identity={memory['identity_confirm']}")
    if track_parts:
        model_context.append(f"[session_track/verified] {'; '.join(track_parts)}")
    # L3 窗口外轻量检索：超出窗口的历史中，按 char-bigram 与当前消息的相似度拉回相关片段
    # （复用项目检索技术的轻量版，不调模型、不产生幻觉）。仅在确实有窗口外历史时执行。
    retrieved_items = _retrieve_dropped_history(dropped_history, request.user_message, top_k=2)
    for item in retrieved_items:
        model_context.append(f"[history_retrieved/session] {item.role}: {_redact_history(item.content)[:220]}")
    # L4 超长对话统计概览：全部来自真实计数（轮数/话题序列/订单数），无模型生成，
    # 让模型对"前面聊了很多"有整体认知，又不承担任何编造风险。
    if len(request.history_messages) > RECENT_WINDOW_SIZE * 2:
        overview_parts = [
            f"turns={len(request.history_messages) // 2}",
            f"seen_orders={len(memory.get('seen_order_ids', []))}",
            f"seen_products={len(memory.get('seen_product_names', []))}",
        ]
        model_context.append(f"[session_overview/stat] {'; '.join(overview_parts)}")
    context_report = {
        "schema_version": "context_build_report_v1",
        "sources": ["runtime_context", "session_memory", "history_messages", "user_message"],
        "trust_order": ["runtime_context", "verified_tool_fact", "session_memory", "history_messages", "user_message"],
        "chosen_order_id": chosen_order_id,
        "conflict_resolutions": conflicts,
        "model_context": model_context,
        "source_safety": {
            "tainted": any(report["tainted"] for report in source_reports),
            "tainted_sources": sorted({report["source"] for report in source_reports if report["tainted"]}),
            "reports": source_reports,
        },
    }
    compression_report = {
        "schema_version": "context_compression_v2",
        "recent_window_size": RECENT_WINDOW_SIZE,
        "token_budget": MAX_HISTORY_TOKENS,
        "backtrack_budget": MAX_BACKTRACK_TOKENS,
        "input_count": len(request.history_messages),
        "kept_count": len(kept_history),
        "dropped_count": len(dropped_history),
        "kept_indexes": [request.history_messages.index(item) for item in kept_history],
        "strategy": "recent_window_plus_order_and_keyword_backtrack",
        "relevance_score": {"matching_order_reference": 100, "recent_window": 80, "keyword_backtrack": 60, "older_history": 20},
    }
    return str(chosen_order_id) if chosen_order_id else None, context_report, compression_report


def update_memory(
    *,
    session_id: str,
    runtime_user_id: str,
    intent: Intent,
    verified_order_id: str | None,
    user_message: str,
    verified_product_name: str | None = None,
    runtime_role: str | None = None,
) -> dict[str, Any]:
    """只有通过工具归属校验的订单能写入记忆，审批令牌和隐私不会写入。"""
    memory = current_memory(session_id, runtime_user_id)
    excluded = memory["excluded_items"]
    decisions: list[dict[str, Any]] = []
    if re.search(r"1[3-9]\d{9}", user_message) and "phone_number" not in excluded:
        excluded.append("phone_number")
        decisions.append({"field": "phone_number", "accepted": False, "reason": "privacy_data"})
    if any(term in user_message for term in ("resume-", "审批令牌", "系统提示词", "hidden reasoning")):
        if "high_risk_or_internal_text" not in excluded:
            excluded.append("high_risk_or_internal_text")
        decisions.append({"field": "internal_or_high_risk_text", "accepted": False, "reason": "unsafe_for_memory"})
    if verified_order_id:
        memory["last_order_id"] = verified_order_id
        if verified_order_id not in memory["seen_order_ids"]:
            memory["seen_order_ids"].append(verified_order_id)
        decisions.append({"field": "last_order_id", "accepted": True, "reason": "verified_tool_fact"})
    if verified_product_name:
        memory["last_product_name"] = verified_product_name
        if verified_product_name not in memory["seen_product_names"]:
            memory["seen_product_names"].append(verified_product_name)
        decisions.append({"field": "last_product_name", "accepted": True, "reason": "verified_tool_fact"})
    color_match = re.search(r"(?:喜欢|偏好|想要)(黑色|白色|蓝色|红色)", user_message)
    if color_match:
        memory["low_risk_preferences"]["preferred_color"] = color_match.group(1)
        decisions.append({"field": "preferred_color", "accepted": True, "reason": "explicit_low_risk_preference"})
    memory["recent_intent"] = intent
    # 结构化轨迹：意图序列（限长 20，超出丢最旧），身份信号仅在有明确证据时更新。
    memory["intent_history"] = (memory["intent_history"] + [intent])[-20:]
    role_signal = _infer_identity_signal(user_message, runtime_role)
    if role_signal:
        memory["identity_confirm"] = role_signal
    memory["write_decisions"] = decisions
    persist_save("session_memory", session_id, memory)
    return dict(memory)


def _infer_identity_signal(user_message: str, runtime_role: str | None) -> str | None:
    """轻量身份信号：Runtime 角色优先；否则用买卖双方视角词判断。

    只做有把握的二元判断，避免在"退换/纠纷"这类双视角词上乱猜（身份不明确时返回 None）。
    """
    if runtime_role and str(runtime_role).lower() in {"buyer", "seller"}:
        return "buyer" if str(runtime_role).lower() == "buyer" else "seller"
    seller_terms = ("我卖", "我挂", "我上架", "我的宝贝", "卖出", "买家拍", "买家不", "买家说", "给买家", "发货给", "改价", "下架", "定价")
    buyer_terms = ("我买", "我拍", "我下单", "我的订单", "收到货", "退货", "退款", "购物车", "寄给我", "卖家发")
    if any(term in user_message for term in seller_terms):
        return "seller"
    if any(term in user_message for term in buyer_terms):
        return "buyer"
    return None


def _compress_history(
    history: list[HistoryMessage], order_id: str | None, user_message: str
) -> tuple[list[HistoryMessage], list[HistoryMessage]]:
    """分层上下文压缩：最近窗口必保，订单号/回溯词命中的更早历史按相关性回溯保留。

    窗口内是"最近一段上下文"；当窗口内不足以理解用户当前消息时（命中回溯词），
    把更早的相关历史拉回模型视野。所有保留内容都是真实对话原文（脱敏后），
    不做摘要式编造，避免模型基于不完整上下文产生幻觉。
    """
    backtrack_terms = [term for term in _CONTEXT_BACKTRACK_TERMS if term in user_message]
    recent_start = max(0, len(history) - RECENT_WINDOW_SIZE)
    selected_indexes = set(range(recent_start, len(history)))
    # 订单相关历史：无论多久之前，含当前订单号的轮次都保留（订单上下文最关键）。
    if order_id:
        selected_indexes.update(index for index, item in enumerate(history) if order_id in item.content)
    # 相关性回溯：当前消息命中的实体/意图词，在更早历史中出现过的轮次一并拉回。
    backtrack_hit: set[int] = set()
    if backtrack_terms:
        backtrack_hit = {
            index
            for index, item in enumerate(history)
            if any(term in item.content for term in backtrack_terms)
        }
        selected_indexes.update(backtrack_hit)
    kept: list[HistoryMessage] = []
    used_tokens = 0
    for index in sorted(selected_indexes, reverse=True):
        item = history[index]
        tokens = estimate_tokens(item.content)
        is_core = (order_id and order_id in item.content) or index >= recent_start or index in backtrack_hit
        # 核心（窗口内/订单/回溯命中）可突破常规预算，但仍受硬上限约束防止失控。
        if used_tokens + tokens <= MAX_HISTORY_TOKENS or (is_core and used_tokens + tokens <= MAX_BACKTRACK_TOKENS):
            kept.append(item)
            used_tokens += tokens
    kept.reverse()
    kept_ids = {id(item) for item in kept}
    return kept, [item for item in history if id(item) not in kept_ids]


def _retrieve_dropped_history(
    dropped: list[HistoryMessage], user_message: str, top_k: int = 2
) -> list[HistoryMessage]:
    """窗口外轻量检索：用 char-bigram 重叠度从被丢弃的历史中找回与当前问题最相关的片段。

    不调用模型、不做摘要，返回的全是脱敏后的真实原文，从机制上杜绝幻觉。
    阈值 0.05 过滤噪声；无命中时返回空列表（保持原有行为）。
    """
    if not dropped:
        return []
    query_bigrams = _char_bigrams(user_message)
    if not query_bigrams:
        return []
    scored: list[tuple[float, HistoryMessage]] = []
    for item in dropped:
        content_bigrams = _char_bigrams(item.content)
        if not content_bigrams:
            continue
        overlap = len(query_bigrams & content_bigrams)
        score = overlap / len(query_bigrams)
        if score > 0.05:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:top_k]]


def _char_bigrams(text: str) -> set[str]:
    """字符级 bigram：跨中文/英文/数字的轻量相似度信号。"""
    normalized = text.lower()
    return {normalized[index : index + 2] for index in range(len(normalized) - 1)}


def _redact_history(content: str) -> str:
    """历史消息进入模型上下文前先做基础隐私脱敏。"""
    return _EMAIL_PATTERN.sub("[email-redacted]", _PHONE_PATTERN.sub("[phone-redacted]", content))
