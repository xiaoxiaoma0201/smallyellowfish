"""项目综合演练 Agent 编排层。它复用 Tool、RAG、Workflow、Trace、Eval 和成本治理能力。"""

from __future__ import annotations

import os
import re
from typing import Any

from api.schemas import *
from config.settings import load_app_env
from context.builder import SESSION_MEMORIES, build_context, update_memory
from cost.governance import build_cost_summary
from hooks.manager import HookManager
from integrations.ecommerce_client import product_query_keyword
from mcp_catalog.catalog import MCP_CATALOG
from models.answer_client import FinalAnswerModelClient, FinalAnswerModelResult
from models.router_client import ModelRouteResult, RouteModelClient
from observability.trace import public_trace_summary, record_initial_chat_trace, record_trace_events, trace_store
from rag.knowledge import (
    REFUND_POLICY,
    RETURN_POLICY,
    OFF_PLATFORM_RISK,
    fulfillment_consult_result,
    invoice_faq_result,
    low_confidence_result,
    policy_knowledge_result,
    promotion_policy_result,
)
from safety.answer_assertions import run_answer_assertions
from safety.source_guard import inspect_source
from state.session_state import COMMON_HIT_CACHE, MESSAGE_COUNT_BY_SESSION
from state.persistence import save as persist_save
from tools.langchain_runtime import LangChainToolRunner
from tools.planning import apply_role_guard, build_order_clarification, build_route_plan, classify_intent, detect_off_platform_risk_keywords, estimate_tokens, extract_order_id, extract_return_reason, follow_up_intent_from_history, history_context_hint, infer_user_role, resolve_order_reference, resolve_order_reference_by_model
from tools.runtime_context import (
    general_chat_answer,
    is_runtime_identity_query,
    logistics_status_label,
    order_no,
    order_status_label,
    runtime_context_summary,
    runtime_identity_answer,
)
from tools.tool_runtime import get_cart_items, get_order_detail, get_order_logistics, get_refund_status, query_seller_orders, query_seller_product_sales, recommend_products, search_products
from workflows.after_sale_graph import RECEIVED_RETURN_GRAPH, REFUND_APPROVAL_GRAPH
from workflows.resume import resume_from_checkpoint

# 订单"发货/到货时间"追问识别：用户追问"什么时候发/多久能到/预计什么时候发货"时，
# 若仍复读状态模板（"目前待发货，物流状态是暂未发货"）等于没回答。这里用订单真实时间
# 字段（paidAt/createdAt/shippedAt/deliveredAt）组织确定性话术，不依赖模型编造预计时间。
_SHIP_TIME_QUERY_TERMS = (
    "什么时候", "啥时候", "何时", "多久", "几天", "预计", "几号",
    "什么时候发", "什么时候到", "什么时候发货", "几天能到", "多久能到",
    "什么时候到货", "什么时候签收", "什么时候能到",
)


def _is_ship_time_follow_up(user_message: str) -> bool:
    return any(term in user_message for term in _SHIP_TIME_QUERY_TERMS)


def _resolved_role(request: ChatRequest, memory: dict[str, Any]) -> str | None:
    """解析会话的确定性身份：商城登录角色（runtime_role）最可信，其次会话已确认身份。

    仅这两种显式身份参与角色门控；消息级推断（infer_user_role）不参与，
    避免单轮表述把双端通用问题误判成跨身份操作。
    """
    if request.runtime_role in ("buyer", "seller"):
        return request.runtime_role
    return memory.get("identity_confirm")


def _seller_product_status_filter(user_message: str) -> str | None:
    """按用户对商品状态的问法抽取过滤条件，只回答用户关心的那一类商品。

    示例："已经卖掉的有哪些" → SOLD（只列已售出）；"在售的有哪些" → ON_SALE。
    不依赖模型，状态词有限枚举、按售卖词优先（"卖掉的"含"卖"不能先被"在售"类词截胡）；
    无状态词时返回 None（全量汇总），保持原有行为。
    """
    sold_terms = ("已卖", "卖掉", "卖掉了", "卖出去", "卖出", "售出", "卖了", "卖了的", "出掉", "已售")
    on_sale_terms = ("在售", "正在卖", "出售中", "上架的", "卖的东西", "挂着卖")
    pending_terms = ("待审核", "审核中", "还没审核", "审核结果")
    for term in sold_terms:
        if term in user_message:
            return "SOLD"
    for term in on_sale_terms:
        if term in user_message:
            return "ON_SALE"
    for term in pending_terms:
        if term in user_message:
            return "PENDING_REVIEW"
    return None


def _format_iso_time(value: Any) -> str:
    return str(value).replace("T", " ")[:16] if value else ""


def _build_ship_time_answer(order: dict[str, Any]) -> str:
    """针对"什么时候发货/到货"的追问，基于订单真实时间字段给出确定性回答。"""
    order_no_text = order_no(order)
    fulfillment = str(order.get("fulfillmentStatus") or "").upper()
    logistics_text = logistics_status_label(order)
    paid_at = _format_iso_time(order.get("paidAt"))
    created_at = _format_iso_time(order.get("createdAt"))
    delivered_at = _format_iso_time(order.get("deliveredAt"))
    shipped_at = _format_iso_time(order.get("shippedAt"))
    if "PENDING_SHIPMENT" in fulfillment or "暂未发货" in logistics_text:
        time_part = f"已于 {paid_at} 完成支付" if paid_at else (f"下单时间为 {created_at}" if created_at else "")
        prefix = f"订单 {order_no_text} {time_part}，" if time_part else f"订单 {order_no_text} "
        return (
            f"{prefix}目前卖家尚未发货，平台暂无预计发货时间。卖家发货后物流单号会第一时间"
            "同步到订单页；如需尽快发货，可通过站内信联系卖家确认发货安排。"
        )
    if "运输中" in logistics_text or "IN_TRANSIT" in logistics_text:
        shipped_part = f"已于 {shipped_at} 发货" if shipped_at else "已发货"
        return (
            f"订单 {order_no_text} {shipped_part}，当前物流状态为{logistics_text}。"
            "平台暂无预计到货时间，请以物流轨迹更新为准；也可通过物流单号联系承运商查询。"
        )
    if delivered_at or "已签收" in logistics_text:
        delivered_part = f"已于 {delivered_at} 签收" if delivered_at else "已签收"
        return f"订单 {order_no_text} {delivered_part}，无需等待发货。"
    return ""


# 退货/退换咨询触发词：命中"退/换/退款/退货"且处于订单上下文（会话已解析订单）时，
# 复用该订单查真实状态给出结合订单的退货判断，避免纯规则话术让模型润色时编造
# "暂未关联您的具体订单信息"（上一轮明明已查到这个订单）。
_RETURN_CONSULT_TERMS = ("退", "换", "退款", "退货", "退换", "退钱")


def _is_return_consult(user_message: str) -> bool:
    return any(term in user_message for term in _RETURN_CONSULT_TERMS)


def _build_return_consult_answer(order: dict[str, Any], user_message: str) -> str:
    """针对"还能退吗/能退吗"类退货咨询，基于订单真实状态组织确定性话术。"""
    order_no_text = order_no(order)
    fulfillment = str(order.get("fulfillmentStatus") or "").upper()
    logistics_text = logistics_status_label(order)
    status_text = str(order.get("status") or "")
    if "PENDING" in fulfillment or "暂未发货" in logistics_text or "待发货" in status_text or "待支付" in status_text:
        return (
            f"订单 {order_no_text} 尚未发货。平台支持未发货订单申请退款，您可以直接在订单详情页"
            "发起退款申请，资金将按原路退回；如卖家已发货则按售后流程处理。"
        )
    if "IN_TRANSIT" in fulfillment or "运输中" in logistics_text or "已发货" in logistics_text:
        return (
            f"订单 {order_no_text} 当前已发货（物流状态：{logistics_text}）。二手商品不支持七天无理由退换；"
            "如商品存在描述不符、真假争议或功能故障，可先通过站内信联系卖家协商，或签收后发起售后申诉并上传凭证。"
        )
    if "DELIVERED" in fulfillment or "已签收" in logistics_text:
        return (
            f"订单 {order_no_text} 已签收。按平台规则，签收后 7 天内如商品存在描述不符、真假争议或功能故障，"
            "可发起售后申诉并上传有效凭证；超出时效或无凭证将无法支持退换。"
        )
    if "COMPLETED" in fulfillment or "已完成" in status_text:
        return (
            f"订单 {order_no_text} 已完成，超过常规售后时效。如仍有售后问题，建议联系卖家协商"
            "或通过平台申诉渠道反馈，按双方举证结果处理。"
        )
    return (
        f"订单 {order_no_text} 当前状态为{logistics_text}。二手商品不支持七天无理由退换；"
        "如存在描述不符、真假争议或功能故障，可按规则发起售后申诉并上传凭证。"
    )


class CustomerServiceAgent:
    """综合演练版：复用前面能力，服务大促场景验证和项目答辩验证。"""

    def __init__(self) -> None:
        self.route_model_client = RouteModelClient()
        self.answer_model_client = FinalAnswerModelClient()
        self.langchain_tool_runner = LangChainToolRunner()

    def chat(self, request: ChatRequest) -> ChatResponse:
        """编排大促场景验证聊天链路，串联 Tool、RAG、Workflow/HITL、降级、安全、Trace 和成本治理。"""
        load_app_env()
        MESSAGE_COUNT_BY_SESSION[request.session_id] = MESSAGE_COUNT_BY_SESSION.get(request.session_id, 0) + 1
        persist_save("message_count", request.session_id, MESSAGE_COUNT_BY_SESSION[request.session_id])
        message_count = MESSAGE_COUNT_BY_SESSION[request.session_id]
        fallback_intent = classify_intent(request.user_message)
        # 多轮承接：当前消息没有独立意图时，沿用本会话最近一次意图（如"那我需要准备什么"继续验货宝话题）。
        follow_up_intent = follow_up_intent_from_history(
            request.user_message, SESSION_MEMORIES.get(request.session_id, {}).get("recent_intent")
        )
        is_follow_up = bool(follow_up_intent)
        if follow_up_intent:
            fallback_intent = follow_up_intent
            trace_store.add(
                request.session_id,
                "intent_follow_up_from_history",
                {
                    "session_id": request.session_id,
                    "user_message": request.user_message,
                    "follow_up_intent": follow_up_intent,
                    "status": "resolved",
                },
            )
        if fallback_intent and fallback_intent not in ("general_chat", "unknown"):
            # 规则已识别出具体意图（二手专属/工具/风控等），模型路由结果必然被守卫覆盖，
            # 直接跳过模型 API 调用，避免硅基流动在线推理偶发延迟（实测 5~80s）击穿
            # 商城网关 45s 读超时，导致用户看到"客服服务暂时繁忙"。
            route_result = ModelRouteResult(
                intent=fallback_intent,
                used_model=False,
                fallback_reason="rule_intent_direct",
                framework="rules",
            )
        else:
            route_result = self.route_model_client.plan_intent(request.user_message, fallback_intent=fallback_intent)
        route_result = self._apply_route_guard(
            fallback_intent=fallback_intent,
            route_result=route_result,
        )
        # 模型未识别出承接意图时，强制沿用历史意图，保证离线/确定性路径也能接上上文。
        if follow_up_intent and route_result.intent == "general_chat":
            route_result = ModelRouteResult(
                intent=follow_up_intent,
                used_model=route_result.used_model,
                model_name=route_result.model_name,
                fallback_reason="follow_up_from_history",
                framework=route_result.framework,
                prompt_fragments=route_result.prompt_fragments,
            )
        intent = route_result.intent
        # ---- 角色-意图职责隔离（根治双端串味）----
        # 显式身份（商城登录 runtime_role 或会话已确认身份）下，卖家专属意图/买家专属
        # 意图与当前身份互斥：买家账号不执行卖家侧查询、卖家账号不执行买家侧售后/购物车。
        # 拦截直接返回确定性引导话术，不进入任何工具/RAG/工作流执行。
        resolved_role = _resolved_role(request, SESSION_MEMORIES.get(request.session_id, {}))
        role_block_message = apply_role_guard(intent, resolved_role)
        if role_block_message:
            trace_store.add(
                request.session_id,
                "role_boundary_blocked",
                {
                    "session_id": request.session_id,
                    "intent": intent,
                    "resolved_role": resolved_role,
                    "status": "blocked",
                },
            )
            update_memory(
                session_id=request.session_id,
                runtime_user_id=request.runtime_user_id,
                intent=intent,
                verified_order_id=None,
                user_message=request.user_message,
                verified_product_name=None,
                runtime_role=request.runtime_role,
            )
            return ChatResponse(
                session_id=request.session_id,
                answer=role_block_message,
                citations=[],
                tool_calls=[],
                clarification=None,
                reasoning_summary=[
                    "Trace 记录的是公开执行摘要：Runtime Context、Context、Tool、RAG、Workflow/HITL、Hooks 和 Cost。",
                    "role_boundary_blocked 表示会话身份与意图专属角色不匹配，未执行任何工具/知识。",
                ],
                reasoning_content=None,
                session_state={
                    "agent_version": "xiaohuangyu-cs-agent-v1",
                    "message_count": message_count,
                    "intent": intent,
                    "model": {
                        "route_planner": {
                            "used_model": route_result.used_model,
                            "model_name": route_result.model_name,
                            "fallback_reason": "role_boundary_blocked",
                            "prompt_fragments": [],
                        },
                        "final_answer": {"used_model": False, "model_name": None, "fallback_reason": "role_boundary_blocked"},
                    },
                    "role_boundary_blocked": True,
                    "resolved_role": resolved_role,
                    "needs_human_approval": False,
                    "cost": {"tokens": {"prompt": 0, "completion": 0, "total": 0}},
                    "runtime_context": runtime_context_summary(request),
                },
            )
        explicit_order_id = extract_order_id(request.user_message)
        if (
            explicit_order_id
            and intent == "fulfillment_consult_query"
            and not any(term in request.user_message for term in ("退的货", "退货的", "退回去", "退货物流", "退货到", "退件", "退回的", "寄回去的"))
        ):
            # 消息已给出具体订单号且属于发货/到货类担忧（"SO... 怎么还没发货"）：
            # 直接按订单事实查询真实履约状态，不让规则话术替代事实查询；
            # 退货物流类（"退的货到哪了"）仍走履约咨询，因为订单物流查不到退货在途信息。
            route_result = ModelRouteResult(
                intent="order_query",
                used_model=route_result.used_model,
                model_name=route_result.model_name,
                fallback_reason="fulfillment_with_order_id_promoted_to_order_query",
                framework=route_result.framework,
                prompt_fragments=route_result.prompt_fragments,
            )
            intent = route_result.intent
        # 指代消解：上一轮澄清已列出订单候选，本轮回复"第一个/第二笔/最近那笔"
        # 时解析成具体订单号，避免再次生成澄清模板造成死循环。
        order_family = {"order_query", "refund_request", "refund_status_query"}
        recent_intent = SESSION_MEMORIES.get(request.session_id, {}).get("recent_intent")
        # 当前意图本身就是订单类（如"帮我发起退款"命中 refund_request）也属于订单上下文，
        # 不能只看上一轮意图，否则动作延续句接不上上轮的指代对象。
        in_order_context = recent_intent in order_family or intent in order_family
        session_seen_orders = SESSION_MEMORIES.get(request.session_id, {}).get("seen_order_ids") or []
        # 上一轮用户消息：纯动作延续句（"帮我退款""申请退款"）的指代对象
        # 在上一轮（"买的那台手机能退吗"），合并匹配让动作句接上上下文。
        prev_user_message: str | None = None
        for _item in reversed(request.history_messages):
            if _item.role == "user":
                prev_user_message = _item.content
                break
        # 订单指代词提升：用户省略式表达"第一笔呢/最近那笔呢/那单呢"指向本会话查过的订单时，
        # 即使 follow_up 把意图承接成买家服务/闲聊（如上一轮聊成色），也按订单查询处理，
        # 让指代消解在会话轨迹（seen_order_ids）里解析，避免答非所问。
        # 含退换/退款词时保持原意图，由退货咨询分支复用订单上下文处理。
        if (
            intent not in order_family
            and session_seen_orders
            and any(
                term in request.user_message
                for term in ("第一笔", "第二笔", "第三笔", "那笔呢", "那笔的", "这笔", "上一单", "最近那单", "最近那笔", "最新那笔", "那单呢", "那单的")
            )
            and not any(term in request.user_message for term in ("退", "换", "退款", "退钱", "退货"))
        ):
            route_result = ModelRouteResult(
                intent="order_query",
                used_model=route_result.used_model,
                model_name=route_result.model_name,
                fallback_reason="order_reference_prompt_lifted_to_order_query",
                framework=route_result.framework,
                prompt_fragments=route_result.prompt_fragments,
            )
            intent = route_result.intent
            in_order_context = True
        if not explicit_order_id and intent in order_family:
            explicit_order_id = resolve_order_reference(
                request.user_message,
                request.runtime_user_id,
                request.runtime_context,
                seen_order_ids=session_seen_orders,
                extra_user_message=prev_user_message,
            )
        # 规则词表未覆盖的自然语言指代（"没发货的啊""昨晚下的那单"等），交给模型
        # 结合候选订单列表理解，整体解决"换一种说法就匹配不到"这一类问题。
        # 触发条件放宽到"处于订单上下文的多轮对话"：即使用户消息含商品词被分类成
        # product_query（如"那对耳机里的另一笔"），只要最近意图是订单类就尝试模型
        # 消解；模型判断"不是指代订单"时返回 null 自然回退，不会误伤商品咨询。
        if (
            not explicit_order_id
            and in_order_context
            and message_count > 1
        ):
            explicit_order_id = resolve_order_reference_by_model(
                request.user_message,
                request.runtime_user_id,
                request.runtime_context,
                self.route_model_client,
                seen_order_ids=session_seen_orders,
                extra_user_message=prev_user_message,
            )
            if explicit_order_id:
                # 模型已确认用户指的是某笔订单，即使当前意图被分类成商品咨询
                # （如"那对耳机里的另一笔"命中 product_query），也按订单意图走工具。
                if intent not in order_family:
                    route_result = ModelRouteResult(
                        intent="order_query",
                        used_model=route_result.used_model,
                        model_name=route_result.model_name,
                        fallback_reason="order_reference_resolved_by_model",
                        framework=route_result.framework,
                        prompt_fragments=route_result.prompt_fragments,
                    )
                    intent = route_result.intent
                trace_store.add(
                    request.session_id,
                    "order_reference_resolved_by_model",
                    {
                        "session_id": request.session_id,
                        "user_message": request.user_message,
                        "order_id": explicit_order_id,
                        "status": "resolved",
                    },
                )
            else:
                # 模型也无法消解（如"那对耳机里的另一笔"指代不唯一）时，用户消息
                # 明显在指订单（含"那/这/另一/第X/最X"等指代词），应回落到订单澄清
                # 而不是被商品词误导向 product_query 答非所问。
                if (
                    intent not in order_family
                    and re.search(r"(那|这)(笔|单|个|一笔)|另一|第[一二两三四五六七八九1-9]|最[近贵新].*笔", request.user_message)
                ):
                    route_result = ModelRouteResult(
                        intent="order_query",
                        used_model=route_result.used_model,
                        model_name=route_result.model_name,
                        fallback_reason="order_reference_ambiguous",
                        framework=route_result.framework,
                        prompt_fragments=route_result.prompt_fragments,
                    )
                    intent = route_result.intent
        order_id, context_report, compression_report = build_context(request, explicit_order_id)
        route_was_guarded = str(route_result.fallback_reason or "").startswith("rule_guard_")
        route_plan = build_route_plan(
            intent=intent,
            user_message=request.user_message,
            order_id=order_id,
            model_used=route_result.used_model and not route_was_guarded,
            role=resolved_role or "unknown",
        )
        runtime_context = runtime_context_summary(request)
        uses_runtime_identity = is_runtime_identity_query(request.user_message)
        # 主动风控：无论当前意图是什么，先检测站外交易高危词，命中即插入风险预警。
        risk_keywords = detect_off_platform_risk_keywords(request.user_message)
        if risk_keywords:
            trace_store.add(
                request.session_id,
                "risk_keyword_intercepted",
                {
                    "session_id": request.session_id,
                    "intent": intent,
                    "matched_keywords": risk_keywords,
                    "policy_id": "off_platform_transaction_risk",
                    "status": "intercepted",
                },
            )
        citations: list[Citation] = []
        tool_calls: list[ToolCallTrace] = []
        workflow: dict[str, Any] | None = None
        cache_hit = False
        degraded = False
        degradation_reason: str | None = None
        rag_rerank: dict[str, Any] | None = None
        rag_retrieval: dict[str, Any] | None = None
        verified_order_id: str | None = None
        verified_product_name: str | None = None
        deterministic_return_answer = False
        clarification = build_order_clarification(request, route_plan)
        hooks = HookManager()
        tool_calling_state: dict[str, Any] = {
            "create_agent": False,
            "skip_reason": "route_does_not_need_business_tools",
            "available_tools": route_plan.required_tools,
            "selected_tools": [],
            "message_types": [],
            "tool_message_count": 0,
            "fallback_used": False,
        }
        tool_agent_prompt_fragments: list[dict[str, Any]] = []
        tool_agent_model_calls = 0

        record_initial_chat_trace(
            session_id=request.session_id,
            runtime_user_id=request.runtime_user_id,
            runtime_nickname=request.runtime_nickname,
            runtime_member_level=request.runtime_member_level,
            runtime_risk_level=request.runtime_risk_level,
            intent=intent,
            estimated_tokens=estimate_tokens(request.user_message),
            route_result=route_result,
            context_report=context_report,
            compression_report=compression_report,
        )
        # 记录用户消息，供商城会话联动观察台还原完整对话流（脱敏后写入）。
        trace_store.add(
            request.session_id,
            "user_message_received",
            {
                "session_id": request.session_id,
                "role": "user",
                "user_message": request.user_message,
            },
        )
        trace_store.add(
            request.session_id,
            "route_plan_built",
            {
                "session_id": request.session_id,
                "intent": route_plan.intent,
                "source": route_plan.source,
                "required_tools": route_plan.required_tools,
                "needs_rag": route_plan.needs_rag,
                "needs_business_tools": route_plan.needs_business_tools,
                "requires_workflow": route_plan.requires_workflow,
                "risk_level": route_plan.risk_level,
            },
        )
        if clarification:
            trace_store.add(
                request.session_id,
                "tool_clarification_required",
                {
                    "session_id": request.session_id,
                    "clarification_field": clarification.clarification_field,
                    "candidate_count": len(clarification.candidates),
                    "candidate_order_ids": [candidate.value for candidate in clarification.candidates],
                    "status": "waiting_for_user",
                },
            )

        if uses_runtime_identity:
            answer = runtime_identity_answer(request)
            risk_level = "low"
            next_action = "answer_user"
            needs_human_approval = False
            trace_store.add(
                request.session_id,
                "runtime_identity_answered",
                {
                    "session_id": request.session_id,
                    "intent": intent,
                    "runtime_user_id": request.runtime_user_id,
                    "used_runtime_context": True,
                },
            )
        elif intent == "degradation_request":
            degraded = True
            degradation_reason = "business_tool_unavailable"
            degradation_source = (
                "explicit_project_fault_injection"
                if "故障注入演示" in request.user_message
                else "user_reported_service_failure"
            )
            trace_store.add(
                request.session_id,
                "degradation_triggered",
                {
                    "session_id": request.session_id,
                    "intent": intent,
                    "degraded": True,
                    "reason": degradation_reason,
                    "source": degradation_source,
                },
            )
            answer = "订单或物流服务暂时不可用，本轮不继续猜测业务事实。建议稍后重试，或转人工客服继续核验。"
            risk_level = "medium"
            next_action = "transfer_to_human"
            needs_human_approval = False
        elif intent == "security_request":
            trace_store.add(
                request.session_id,
                "prompt_security_blocked",
                {"session_id": request.session_id, "intent": intent, "risk_level": "high", "status": "blocked"},
            )
            answer = "我不能提供受保护系统信息、受保护推理摘要、工具细节或内部策略。"
            risk_level = "high"
            next_action = "answer_user"
            needs_human_approval = False
        elif intent == "low_confidence_query":
            knowledge_result = low_confidence_result(request.session_id, intent)
            record_trace_events(request.session_id, knowledge_result.trace_events)
            answer = knowledge_result.answer
            citations = knowledge_result.citations
            risk_level = knowledge_result.risk_level
            next_action = knowledge_result.next_action
            needs_human_approval = knowledge_result.needs_human_approval
        elif intent == "faq_query":
            knowledge_result = invoice_faq_result(request.session_id)
            record_trace_events(request.session_id, knowledge_result.trace_events)
            answer = knowledge_result.answer
            citations = knowledge_result.citations
            risk_level = knowledge_result.risk_level
            next_action = knowledge_result.next_action
            needs_human_approval = knowledge_result.needs_human_approval
            cache_hit = knowledge_result.cache_hit
            rag_retrieval = knowledge_result.retrieval_debug
        elif intent == "promotion_query":
            knowledge_result = promotion_policy_result(request.session_id, request.user_message)
            if not knowledge_result.citations:
                intent = "low_confidence_query"
            record_trace_events(request.session_id, knowledge_result.trace_events)
            answer = knowledge_result.answer
            citations = knowledge_result.citations
            rag_rerank = knowledge_result.rerank
            rag_retrieval = knowledge_result.retrieval_debug
            risk_level = knowledge_result.risk_level
            next_action = knowledge_result.next_action
            needs_human_approval = knowledge_result.needs_human_approval
        elif intent == "buyer_service_query":
            # 退货/退款类咨询（"还能退吗""能退吗""我要退"）且会话已解析订单（上一轮查过订单/
            # 页面上下文带订单号）时，复用该订单查真实状态，按状态给出退货判断，保证上下文承接；
            # 否则走标准话术知识路径。
            if order_id and _is_return_consult(request.user_message):
                hooks.pre_tool_call("get_order_detail", {"order_id": order_id}, request.runtime_user_id)
                trace_store.add(
                    request.session_id,
                    "tool_started",
                    {"session_id": request.session_id, "tool_name": "get_order_detail", "order_id": order_id},
                )
                order, detail_call = get_order_detail(order_id, request.runtime_user_id, request.runtime_context)
                tool_calls.append(detail_call)
                hooks.post_tool_call(detail_call)
                trace_store.add(
                    request.session_id,
                    "tool_finished",
                    {
                        "session_id": request.session_id,
                        "tool_name": detail_call.tool_name,
                        "order_id": order_id,
                        "status": detail_call.status,
                        "risk_level": detail_call.risk_level,
                        "next_action": detail_call.next_action,
                    },
                )
                if order is None:
                    answer = detail_call.output_summary
                    risk_level = "high"
                    next_action = "transfer_to_human"
                    needs_human_approval = False
                else:
                    verified_order_id = order_no(order)
                    answer = _build_return_consult_answer(order, request.user_message)
                    citations.append(RETURN_POLICY)
                    risk_level = "low"
                    next_action = "answer_user"
                    needs_human_approval = False
                    deterministic_return_answer = True
            else:
                role = infer_user_role(request.user_message, request.runtime_role)
                # 多轮承接：附加上一轮 Agent 回答的主题句作为检索语境，让省略式追问召回上一话题文档。
                history_hint = history_context_hint(request.history_messages)
                retrieval_input = f"{history_hint} {request.user_message}".strip() if history_hint else request.user_message
                knowledge_result = policy_knowledge_result(
                    intent, request.session_id, retrieval_input, role=role, answer_query=request.user_message
                )
                record_trace_events(request.session_id, knowledge_result.trace_events)
                answer = knowledge_result.answer
                citations = knowledge_result.citations
                rag_retrieval = knowledge_result.retrieval_debug
                risk_level = knowledge_result.risk_level
                next_action = knowledge_result.next_action
                needs_human_approval = knowledge_result.needs_human_approval
        elif intent in {
            "platform_rule_query",
            "seller_service_query",
            "dispute_query",
            "risk_prevention_query",
        }:
            # 二手交易平台规则、买卖双方服务、纠纷维权、风险防控类咨询全部走标准话术知识路径。
            # 同一问题按 Runtime 角色或消息表述推断身份，让买家/卖家话术按身份差异化应答。
            role = infer_user_role(request.user_message, request.runtime_role)
            # 多轮承接：附加上一轮 Agent 回答的主题句作为检索语境，让省略式追问召回上一话题文档。
            history_hint = history_context_hint(request.history_messages)
            retrieval_input = f"{history_hint} {request.user_message}".strip() if history_hint else request.user_message
            knowledge_result = policy_knowledge_result(
                intent, request.session_id, retrieval_input, role=role, answer_query=request.user_message
            )
            record_trace_events(request.session_id, knowledge_result.trace_events)
            answer = knowledge_result.answer
            citations = knowledge_result.citations
            rag_retrieval = knowledge_result.retrieval_debug
            risk_level = knowledge_result.risk_level
            next_action = knowledge_result.next_action
            needs_human_approval = knowledge_result.needs_human_approval
        elif intent == "fulfillment_consult_query":
            # 履约担忧/售后物流咨询（催发货、不发货、担心被骗、退货物流）：预检索真实规则文档，
            # 按子类组织确定性话术（担保交易安抚/催促发货指引/退货物流查询），不编造发货时限。
            # 不调用业务工具——无订单号时给出规则指引，避免把担忧类问题顶成订单列表澄清。
            knowledge_result = fulfillment_consult_result(request.session_id, request.user_message)
            record_trace_events(request.session_id, knowledge_result.trace_events)
            answer = knowledge_result.answer
            citations = knowledge_result.citations
            risk_level = knowledge_result.risk_level
            next_action = knowledge_result.next_action
            needs_human_approval = knowledge_result.needs_human_approval
        elif intent == "return_request":
            if clarification:
                answer = clarification.message
                risk_level = "high"
                next_action = "ask_clarification"
                needs_human_approval = False
            else:
                hooks.pre_tool_call("get_order_detail", {"order_id": order_id}, request.runtime_user_id)
                trace_store.add(
                    request.session_id,
                    "tool_started",
                    {"session_id": request.session_id, "tool_name": "get_order_detail", "order_id": order_id},
                )
                order, detail_call = get_order_detail(order_id, request.runtime_user_id, request.runtime_context)
                hooks.post_tool_call(detail_call)
                tool_calls.append(detail_call)
                trace_store.add(
                    request.session_id,
                    "tool_finished",
                    {
                        "session_id": request.session_id,
                        "tool_name": detail_call.tool_name,
                        "order_id": order_id,
                        "status": detail_call.status,
                        "risk_level": detail_call.risk_level,
                    },
                )
                if order is None:
                    answer = detail_call.output_summary
                    risk_level = "high"
                    next_action = "transfer_to_human"
                    needs_human_approval = False
                else:
                    verified_order_id = order_no(order)
                    return_reason = extract_return_reason(request.user_message)
                    citations.append(RETURN_POLICY)
                    trace_store.add(
                        request.session_id,
                        "rag_pre_retrieved",
                        {
                            "session_id": request.session_id,
                            "hit_count": 1,
                            "retrieval_stage": "pre_retrieval",
                            "policy_id": "return_after_delivery",
                        },
                    )
                    workflow = RECEIVED_RETURN_GRAPH.run(order, return_reason=return_reason, citations=citations)
                    eligible = workflow["eligibility_status"] == "eligible_for_application"
                    reason_labels = {
                        "order_not_received": "订单尚未签收",
                        "product_not_returnable": "商品不支持无理由退货",
                        "product_returnability_unknown": "暂未核实商品可退属性",
                        "signed_time_missing": "缺少可信签收时间",
                        "signed_time_in_future": "签收时间晚于当前业务日期，需要核实",
                        "return_window_expired": "签收已超过 7 天",
                        "return_reason_missing": "还需要补充明确的退货原因",
                    }
                    answer = (
                        f"订单 {order_no(order)} 已根据小黄鱼二手电商交易平台签收后退货 SOP，完成签收时间、商品可退属性、退货原因和七天窗口检查，可以准备退货申请；当前还不是退货成功，仍需人工复核。"
                        if eligible
                        else f"订单 {order_no(order)} 暂不满足签收后退货条件：{reason_labels.get(workflow['eligibility_reason'], workflow['eligibility_reason'])}。"
                    )
                    risk_level = "high"
                    next_action = "transfer_to_human" if eligible else "answer_user"
                    needs_human_approval = eligible
                    trace_store.add(request.session_id, "received_return_workflow_completed", {"session_id": request.session_id, **workflow})
        elif intent == "seller_products_query":
            # 卖家查询自己商品的售卖情况：只读业务后端商品库，用真实数据回答，不走模型话术猜测。
            hooks.pre_tool_call(
                "query_seller_product_sales",
                {"seller_user_id": request.runtime_user_id},
                request.runtime_user_id,
            )
            trace_store.add(
                request.session_id,
                "tool_started",
                {
                    "session_id": request.session_id,
                    "tool_name": "query_seller_product_sales",
                    "seller_user_id": request.runtime_user_id,
                },
            )
            sales_products, sales_call = query_seller_product_sales(
                request.runtime_user_id,
                status_filter=_seller_product_status_filter(request.user_message),
            )
            tool_calls.append(sales_call)
            hooks.post_tool_call(sales_call)
            trace_store.add(
                request.session_id,
                "tool_finished",
                {
                    "session_id": request.session_id,
                    "tool_name": sales_call.tool_name,
                    "status": sales_call.status,
                    "risk_level": sales_call.risk_level,
                    "next_action": sales_call.next_action,
                },
            )
            answer = sales_call.output_summary
            risk_level = sales_call.risk_level
            next_action = sales_call.next_action or "answer_user"
            needs_human_approval = False
            if sales_call.status == "error":
                degraded = True
                degradation_reason = sales_call.error_type or "seller_products_unavailable"
        elif intent == "seller_orders_query":
            # 卖家查询卖出订单（买家购买了自己商品的订单）：只读业务后端订单库，
            # 用真实履约状态回答（待发货/已发货/已签收随商城操作实时变化），禁止编造。
            hooks.pre_tool_call(
                "query_seller_orders",
                {"seller_user_id": request.runtime_user_id},
                request.runtime_user_id,
            )
            trace_store.add(
                request.session_id,
                "tool_started",
                {
                    "session_id": request.session_id,
                    "tool_name": "query_seller_orders",
                    "seller_user_id": request.runtime_user_id,
                },
            )
            sold_orders, orders_call = query_seller_orders(request.runtime_user_id)
            tool_calls.append(orders_call)
            hooks.post_tool_call(orders_call)
            trace_store.add(
                request.session_id,
                "tool_finished",
                {
                    "session_id": request.session_id,
                    "tool_name": orders_call.tool_name,
                    "status": orders_call.status,
                    "risk_level": orders_call.risk_level,
                    "next_action": orders_call.next_action,
                },
            )
            answer = orders_call.output_summary
            risk_level = orders_call.risk_level
            next_action = orders_call.next_action or "answer_user"
            needs_human_approval = False
            if orders_call.status == "error":
                degraded = True
                degradation_reason = orders_call.error_type or "seller_orders_unavailable"
        elif intent == "cart_query":
            # 买家/用户查询自己购物车加购记录：只读业务后端购物车，用真实数据回答，禁止编造。
            hooks.pre_tool_call(
                "get_cart_items",
                {"cart_owner_user_id": request.runtime_user_id},
                request.runtime_user_id,
            )
            trace_store.add(
                request.session_id,
                "tool_started",
                {
                    "session_id": request.session_id,
                    "tool_name": "get_cart_items",
                    "cart_owner_user_id": request.runtime_user_id,
                },
            )
            cart, cart_call = get_cart_items(request.runtime_user_id)
            tool_calls.append(cart_call)
            hooks.post_tool_call(cart_call)
            trace_store.add(
                request.session_id,
                "tool_finished",
                {
                    "session_id": request.session_id,
                    "tool_name": cart_call.tool_name,
                    "status": cart_call.status,
                    "risk_level": cart_call.risk_level,
                    "next_action": cart_call.next_action,
                },
            )
            answer = cart_call.output_summary
            risk_level = cart_call.risk_level
            next_action = cart_call.next_action or "answer_user"
            needs_human_approval = False
            if cart_call.status == "error":
                degraded = True
                degradation_reason = cart_call.error_type or "cart_unavailable"
        elif intent == "recommend_products":
            # 商品推荐：按类别/用途/预算从商城在售商品库取真实商品，确定性组织推荐列表，禁止编造价格库存。
            hooks.pre_tool_call(
                "recommend_products",
                {"user_message": request.user_message},
                request.runtime_user_id,
            )
            trace_store.add(
                request.session_id,
                "tool_started",
                {"session_id": request.session_id, "tool_name": "recommend_products"},
            )
            rec_products, rec_call = recommend_products(request.user_message)
            tool_calls.append(rec_call)
            hooks.post_tool_call(rec_call)
            trace_store.add(
                request.session_id,
                "tool_finished",
                {
                    "session_id": request.session_id,
                    "tool_name": rec_call.tool_name,
                    "status": rec_call.status,
                    "risk_level": rec_call.risk_level,
                    "next_action": rec_call.next_action,
                },
            )
            answer = rec_call.output_summary
            risk_level = rec_call.risk_level
            next_action = rec_call.next_action or "answer_user"
            needs_human_approval = False
            if rec_call.status == "error":
                degraded = True
                degradation_reason = rec_call.error_type or "recommend_products_unavailable"
        elif intent in {"order_query", "refund_status_query", "refund_request", "product_query"}:
            tool_result = self.langchain_tool_runner.run(
                request=request,
                intent=intent,
                required_tools=route_plan.required_tools,
                hooks=hooks,
                expected_order_id=order_id,
                expected_product_keyword=product_query_keyword(request.user_message) if intent == "product_query" else None,
            )
            tool_calling_state = tool_result.state
            tool_agent_prompt_fragments = tool_result.prompt_fragments
            tool_agent_model_calls = tool_result.model_calls
            trace_store.add(
                request.session_id,
                "langchain_tool_agent_completed",
                {
                    "session_id": request.session_id,
                    "create_agent": tool_calling_state.get("create_agent"),
                    "selected_tools": tool_calling_state.get("selected_tools", []),
                    "message_types": tool_calling_state.get("message_types", []),
                    "fallback_used": tool_calling_state.get("fallback_used"),
                    "status": "success" if tool_result.executed else "fallback",
                },
            )
            if tool_result.executed:
                order = tool_result.order
                tool_calls.extend(tool_result.tool_calls)
                detail_call = tool_calls[0]
                for call in tool_result.tool_calls:
                    trace_store.add(
                        request.session_id,
                        "tool_started",
                        {"session_id": request.session_id, "tool_name": call.tool_name, "order_id": order_id},
                    )
                    trace_store.add(
                        request.session_id,
                        "tool_finished",
                        {
                            "session_id": request.session_id,
                            "tool_name": call.tool_name,
                            "order_id": order_id,
                            "status": call.status,
                            "risk_level": call.risk_level,
                            "next_action": call.next_action,
                        },
                    )
            elif clarification is None and intent == "product_query":
                hooks.pre_tool_call("search_products", {"keyword": request.user_message}, request.runtime_user_id)
                trace_store.add(
                    request.session_id,
                    "tool_started",
                    {"session_id": request.session_id, "tool_name": "search_products"},
                )
                products, product_call = search_products(request.user_message)
                tool_calls.append(product_call)
                hooks.post_tool_call(product_call)
                trace_store.add(
                    request.session_id,
                    "tool_finished",
                    {
                        "session_id": request.session_id,
                        "tool_name": product_call.tool_name,
                        "status": product_call.status,
                        "risk_level": product_call.risk_level,
                    },
                )
                order = None
                detail_call = None
            elif clarification is None:
                hooks.pre_tool_call("get_order_detail", {"order_id": order_id}, request.runtime_user_id)
                trace_store.add(
                    request.session_id,
                    "tool_started",
                    {"session_id": request.session_id, "tool_name": "get_order_detail", "order_id": order_id},
                )
                order, detail_call = get_order_detail(order_id, request.runtime_user_id, request.runtime_context)
                tool_calls.append(detail_call)
                hooks.post_tool_call(detail_call)
                trace_store.add(
                    request.session_id,
                    "tool_finished",
                    {
                        "session_id": request.session_id,
                        "tool_name": detail_call.tool_name,
                        "order_id": order_id,
                        "status": detail_call.status,
                        "risk_level": detail_call.risk_level,
                        "next_action": detail_call.next_action,
                    },
                )
            else:
                order = None
                detail_call = None
            if order is not None:
                verified_order_id = order_no(order)
            if clarification:
                answer = clarification.message
                if clarification.candidates:
                    choices = "；".join(f"{candidate.value}（{candidate.hint}）" for candidate in clarification.candidates)
                    answer = f"{answer} 当前账号下可选订单：{choices}。"
                risk_level = "medium"
                next_action = "ask_clarification"
                needs_human_approval = False
            elif intent == "order_query" and order:
                logistics_call = next((call for call in tool_calls if call.tool_name == "get_order_logistics"), None)
                if logistics_call is None:
                    hooks.pre_tool_call("get_order_logistics", {"order_id": order_id}, request.runtime_user_id)
                    logistics_call = get_order_logistics(order)
                    tool_calls.append(logistics_call)
                    hooks.post_tool_call(logistics_call)
                    trace_store.add(
                        request.session_id,
                        "tool_finished",
                        {
                            "session_id": request.session_id,
                            "tool_name": logistics_call.tool_name,
                            "order_id": order_id,
                            "status": logistics_call.status,
                            "risk_level": logistics_call.risk_level,
                        },
                    )
                answer = f"我帮你查到了，订单 {order_no(order)} 目前{order_status_label(order)}，物流状态是{logistics_status_label(order)}。"
                if _is_ship_time_follow_up(request.user_message):
                    answer = _build_ship_time_answer(order) or answer
                risk_level = "low"
                next_action = "answer_user"
                needs_human_approval = False
            elif intent == "refund_status_query" and order:
                status_call = next((call for call in tool_calls if call.tool_name == "get_refund_status"), None)
                if status_call is None:
                    hooks.pre_tool_call("get_refund_status", {"order_id": order_id}, request.runtime_user_id)
                    trace_store.add(
                        request.session_id,
                        "tool_started",
                        {"session_id": request.session_id, "tool_name": "get_refund_status", "order_id": order_id},
                    )
                    status_call = get_refund_status(order, request.runtime_user_id)
                    tool_calls.append(status_call)
                    hooks.post_tool_call(status_call)
                    trace_store.add(
                        request.session_id,
                        "tool_finished",
                        {
                            "session_id": request.session_id,
                            "tool_name": status_call.tool_name,
                            "order_id": order_id,
                            "status": status_call.status,
                            "risk_level": status_call.risk_level,
                        },
                    )
                answer = status_call.output_summary
                risk_level = status_call.risk_level
                next_action = status_call.next_action or "answer_user"
                needs_human_approval = False
                if status_call.status == "error":
                    degraded = True
                    degradation_reason = status_call.error_type or "refund_status_unavailable"
            elif intent == "product_query":
                product_call = next((call for call in tool_calls if call.tool_name == "search_products"), None)
                # 模型生成的搜索关键词与受控 RoutePlan 不一致被阻止时，不能把内部安全提示
                # 当答案返回给用户（"模型工具参数与受控 RoutePlan 的商品关键词不一致，已阻止执行"
                # 是内部机制，不是对用户问题的回答）；回退到规则抽取关键词重新搜索，商城无该
                # 商品时如实告知"暂无匹配"，不幻觉。
                if product_call is not None and product_call.status == "error" and product_call.error_type == "route_plan_argument_mismatch":
                    products, product_call = search_products(request.user_message)
                    tool_calls.append(product_call)
                elif product_call is None:
                    products, product_call = search_products(request.user_message)
                    tool_calls.append(product_call)
                needs_promotion_disclaimer = any(term in request.user_message for term in ["活动", "优惠", "满减", "会员"])
                if needs_promotion_disclaimer:
                    knowledge_result = promotion_policy_result(request.session_id, request.user_message)
                    record_trace_events(request.session_id, knowledge_result.trace_events)
                    citations = knowledge_result.citations
                    rag_retrieval = knowledge_result.retrieval_debug
                    rag_rerank = knowledge_result.rerank
                    policy_answer = (
                        f"平台通用规则：{knowledge_result.answer} "
                        "这不代表该规则一定适用于当前商品活动，具体组合以商品页和结算页为准。"
                        if citations
                        else "活动规则请以商品页和结算页为准。"
                    )
                    answer = f"{product_call.output_summary} {policy_answer}"
                else:
                    answer = product_call.output_summary
                if needs_promotion_disclaimer and "以商品页和结算页为准" not in answer:
                    answer = f"{answer.rstrip('。！？')}；以上信息最终以商品页和结算页为准。"
                risk_level = "low" if product_call.status == "success" else "medium"
                next_action = product_call.next_action or "answer_user"
                needs_human_approval = False
                if product_call.status == "success":
                    verified_product_name = str(product_call.arguments.get("product_name") or "") or None
            elif intent == "refund_request":
                citations.append(REFUND_POLICY)
                trace_store.add(
                    request.session_id,
                    "rag_pre_retrieved",
                    {
                        "session_id": request.session_id,
                        "hit_count": 1,
                        "retrieval_stage": "pre_retrieval",
                        "policy_id": "refund_before_shipping",
                    },
                )
                eligible, eligibility_reason = REFUND_APPROVAL_GRAPH.assess_eligibility(order)
                if not eligible:
                    workflow = None
                    answer = (
                        "该订单已经发货，不能进入未发货退款审批；请按签收后的退货或售后流程处理。"
                        if eligibility_reason == "order_already_shipped_or_not_eligible"
                        else "订单事实或支付状态没有通过退款资格校验，暂不能创建退款审批，请转人工核验。"
                    )
                    risk_level = "high"
                    next_action = "transfer_to_human"
                    needs_human_approval = False
                    trace_store.add(
                        request.session_id,
                        "refund_eligibility_blocked",
                        {"session_id": request.session_id, "order_id": order_id, "reason": eligibility_reason, "status": "blocked"},
                    )
                else:
                    workflow = REFUND_APPROVAL_GRAPH.run(
                        request=request,
                        order=order,
                        order_id=order_id,
                        citations=citations,
                    )
                if workflow is None:
                    pass
                else:
                    for node_name in workflow.get("node_history", []):
                        trace_store.add(
                            request.session_id,
                            "workflow_node_finished",
                            {
                                "session_id": request.session_id,
                                "workflow_id": workflow["workflow_id"],
                                "graph_name": workflow.get("graph_name"),
                                "node_name": node_name,
                                "status": "completed",
                            },
                        )
                    trace_store.add(
                        request.session_id,
                        "workflow_completed",
                        {
                            "session_id": request.session_id,
                            **workflow,
                            "risk_level": "high",
                            "needs_human_approval": True,
                        },
                    )
                    trace_store.add(
                        request.session_id,
                        "human_approval_required",
                        {
                            "session_id": request.session_id,
                            "workflow_id": workflow["workflow_id"],
                            "pending_action": "require_approval",
                            "risk_level": "high",
                            "needs_human_approval": True,
                        },
                    )
                    answer = f"{order_no(order)} 可以进入未发货退款申请判断，但资金动作必须等待人工审批。"
                    risk_level = "high"
                    next_action = "transfer_to_human"
                    needs_human_approval = True
            else:
                answer = detail_call.output_summary
                risk_level = "medium"
                next_action = "ask_clarification" if detail_call.error_type == "missing_order_id" else "transfer_to_human"
                needs_human_approval = False
        else:
            answer = general_chat_answer(request.user_message)
            risk_level = "low"
            next_action = "answer_user"
            needs_human_approval = False

        # Tool/RAG 文本也是外部数据，进入最终模型前必须按来源做污染检查。
        external_reports: list[dict[str, Any]] = []
        for call in tool_calls:
            report = inspect_source("tool_result", call.output_summary)
            if report["tainted"]:
                call.output_summary = str(report["sanitized_content"])
            external_reports.append({key: value for key, value in report.items() if key != "sanitized_content"})
        for citation in citations:
            report = inspect_source("rag_document", citation.snippet)
            if report["tainted"]:
                citation.snippet = str(report["sanitized_content"])
            external_reports.append({key: value for key, value in report.items() if key != "sanitized_content"})
        if external_reports:
            source_safety = context_report["source_safety"]
            source_safety["reports"].extend(external_reports)
            source_safety["tainted"] = source_safety["tainted"] or any(report["tainted"] for report in external_reports)
            source_safety["tainted_sources"] = sorted(
                set(source_safety["tainted_sources"])
                | {report["source"] for report in external_reports if report["tainted"]}
            )
            trace_store.add(
                request.session_id,
                "context_source_safety_checked",
                {
                    "session_id": request.session_id,
                    "tainted": source_safety["tainted"],
                    "tainted_sources": source_safety["tainted_sources"],
                    "source_count": len(source_safety["reports"]),
                },
            )

        if rag_retrieval:
            trace_store.add(
                request.session_id,
                "rag_hybrid_retrieved",
                {
                    "session_id": request.session_id,
                    "mode": rag_retrieval.get("mode"),
                    "rewritten_query": (rag_retrieval.get("plan") or {}).get("rewritten_query"),
                    "index_version": rag_retrieval.get("index_version"),
                    "index_chunk_count": rag_retrieval.get("index_chunk_count"),
                    "index_cache_hit": rag_retrieval.get("index_cache_hit"),
                    "retrieval_cache_hit": rag_retrieval.get("retrieval_cache_hit"),
                    "vector_policy_ids": rag_retrieval.get("vector_policy_ids", []),
                    "keyword_policy_ids": rag_retrieval.get("keyword_policy_ids", []),
                    "hit_count": len(citations),
                    "retrieval_stage": "hybrid_retrieval",
                },
            )

        # 主动风控话术插入：命中站外交易高危词时，将平台风险预警作为独立段落附加到回答前，
        # 引用官方知识话术，不交给模型自由改写。
        if risk_keywords:
            # 知识路径已命中 off_platform_transaction_risk 时不再重复前置整篇，
            # 避免同一段风控文案在回答里出现两遍。
            if not any(citation.metadata.get("policy_id") == "off_platform_transaction_risk" for citation in citations):
                citations.append(OFF_PLATFORM_RISK)
                answer = f"{OFF_PLATFORM_RISK.snippet}\n\n{answer}"

        model_answer = self._compose_final_answer(
            request=request,
            intent=intent,
            answer=answer,
            risk_level=risk_level,
            next_action=next_action,
            tool_calls=tool_calls,
            citations=citations,
            workflow=workflow,
            cache_hit=cache_hit,
            degraded=degraded,
            risk_keywords=risk_keywords,
            skip_final_model=uses_runtime_identity or deterministic_return_answer,
            enable_reasoning=request.reasoning_view == "detailed",
            context_report=context_report,
        )
        answer = model_answer.answer
        # 最终安全兜底：如果意图涉及商品+活动规则（Tool+RAG 联合），确保免责声明精确子串存在，
        # 避免 LLM 自由发挥改写话术（如"显示的信息为准"）导致评测信号缺失。
        if intent in {"product_query", "promotion_query"} and any(
            term in request.user_message for term in ["活动", "优惠", "满减", "会员", "618", "大促"]
        ):
            if "以商品页和结算页为准" not in answer:
                answer = f"{answer.rstrip()} 最终适用情况以商品页和结算页为准。"
        if (
            intent == "faq_query"
            and not cache_hit
            and (model_answer.used_model or os.getenv("AGENT_DISABLE_LLM") == "1")
        ):
            # 在线缓存模型基于证据生成的最终话术；离线测试则缓存显式兜底话术。
            COMMON_HIT_CACHE["faq:invoice_issue"] = {
                "answer": answer,
                "citation": "invoice_issue",
                "source": "model_final_answer" if model_answer.used_model else "explicit_offline_fallback",
            }
        reasoning_content = model_answer.reasoning_content if request.reasoning_view == "detailed" else None
        prompt_fragments = [
            *route_result.prompt_fragments,
            *tool_agent_prompt_fragments,
            *model_answer.prompt_fragments,
        ]
        selected_mcp_tool = next((call.tool_name for call in reversed(tool_calls) if call.status == "success"), None)
        mcp_binding = MCP_CATALOG.binding_summary(selected_mcp_tool, risk_level)
        trace_store.add(request.session_id, "mcp_binding_resolved", {"session_id": request.session_id, **mcp_binding})

        if prompt_fragments:
            trace_store.add(
                request.session_id,
                "prompt_context_built",
                {
                    "session_id": request.session_id,
                    "registry_schema": "prompt_registry_v1",
                    "selected_fragments": prompt_fragments,
                    "prompt_body_exposed": False,
                },
            )

        memory = update_memory(
            session_id=request.session_id,
            runtime_user_id=request.runtime_user_id,
            intent=intent,
            verified_order_id=verified_order_id,
            user_message=request.user_message,
            verified_product_name=verified_product_name,
            runtime_role=request.runtime_role,
        )
        hook_completion = hooks.on_completion(risk_level=risk_level, next_action=next_action, degraded=degraded)
        for hook_event in hooks.events:
            trace_store.add(request.session_id, "hook_executed", {"session_id": request.session_id, **hook_event})
        cost_summary = build_cost_summary(
            request=request,
            intent=intent,
            tool_calls=tool_calls,
            citations=citations,
            workflow=workflow,
            answer=answer,
            cache_hit=cache_hit,
            route_model_used=route_result.used_model,
            answer_model_used=model_answer.used_model,
            reasoning_content_returned=bool(reasoning_content),
            reasoning_source=model_answer.reasoning_source,
            degraded=degraded,
            degradation_reason=degradation_reason,
            prompt_fragments=prompt_fragments,
            tool_agent_model_calls=tool_agent_model_calls,
        )
        trace_store.add(
            request.session_id,
            "cost_recorded",
            cost_summary,
        )
        trace_store.add(
            request.session_id,
            "final_answer_generated",
            {
                "session_id": request.session_id,
                "intent": intent,
                "status": "success",
                "risk_level": risk_level,
                "used_model": model_answer.used_model,
                "reasoning_content_returned": bool(reasoning_content),
                # 记录最终客服回答（脱敏后写入），供商城会话联动观察台还原对话流。
                "answer": answer,
            },
        )

        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            citations=citations,
            tool_calls=tool_calls,
            clarification=clarification,
            reasoning_summary=[
                "Trace 记录的是公开执行摘要：Runtime Context、Context、Tool、RAG、Workflow/HITL、Hooks 和 Cost。",
                "tool_calls 与 citations 是可观察证据，不是 hidden CoT。",
                "详细模式会尝试展示主链路最终模型返回的 reasoning_content；系统提示词、密钥、隐私原文和内部堆栈不会写入公开 trace。",
            ],
            reasoning_content=reasoning_content,
            session_state={
                "agent_version": "xiaohuangyu-cs-agent-v1",
                "message_count": message_count,
                "intent": intent,
                "model": {
                    "route_planner": {
                        "used_model": route_result.used_model,
                        "model_name": route_result.model_name,
                        "fallback_reason": route_result.fallback_reason,
                        "prompt_fragments": route_result.prompt_fragments,
                    },
                    "final_answer": {
                        "used_model": model_answer.used_model,
                        "model_name": model_answer.model_name,
                        "fallback_reason": model_answer.fallback_reason,
                        "prompt_fragments": model_answer.prompt_fragments,
                    },
                },
                "prompt_registry": {
                    "schema_version": "prompt_registry_v1",
                    "selected_fragments": prompt_fragments,
                    "selected_fragment_ids": [fragment["name"] for fragment in prompt_fragments],
                    "prompt_body_exposed": False,
                },
                "route_plan": route_plan.model_dump(),
                "tool_calling": {
                    **tool_calling_state,
                    "clarification": clarification.model_dump() if clarification else None,
                },
                "mcp": mcp_binding,
                "frameworks": {
                    "langchain": {
                        "used": route_result.used_model or model_answer.used_model,
                        "route_chain": route_result.framework,
                        "final_answer_chain": model_answer.framework,
                        "prompt_registry": "prompts/prompt_registry.yml",
                        "selected_fragment_ids": [fragment["name"] for fragment in prompt_fragments],
                        "create_agent": bool(tool_calling_state.get("create_agent")),
                    },
                    "langgraph": {
                        "used": bool(workflow and workflow.get("used_langgraph")),
                        "graph_name": workflow.get("graph_name") if workflow else None,
                        "current_node": workflow.get("current_node") if workflow else None,
                        "node_history": workflow.get("node_history", []) if workflow else [],
                    },
                },
                "risk_level": risk_level,
                "next_action": next_action,
                "needs_human_approval": needs_human_approval,
                "runtime_context": runtime_context,
                "memory": memory,
                "context_report": context_report,
                "compression_report": compression_report,
                "hook_events": hooks.events,
                "hook_completion": hook_completion,
                "workflow": workflow,
                "rag": {
                    "low_confidence": intent == "low_confidence_query",
                    "hit_count": len(citations),
                    "citation_ids": [citation.metadata.get("policy_id") for citation in citations if citation.metadata],
                    "rerank_mode": rag_rerank["mode"] if rag_rerank else None,
                    "reranked_policy_ids": rag_rerank["policy_ids"] if rag_rerank else [],
                    "rerank_scores": rag_rerank["scores"] if rag_rerank else {},
                    "rerank_reasons": rag_rerank["reasons"] if rag_rerank else {},
                    "retrieval_mode": rag_retrieval.get("mode") if rag_retrieval else None,
                    "rewritten_query": (rag_retrieval.get("plan") or {}).get("rewritten_query") if rag_retrieval else None,
                    "index_version": rag_retrieval.get("index_version") if rag_retrieval else None,
                    "index_chunk_count": rag_retrieval.get("index_chunk_count") if rag_retrieval else 0,
                    "index_cache_hit": rag_retrieval.get("index_cache_hit") if rag_retrieval else False,
                    "retrieval_cache_hit": rag_retrieval.get("retrieval_cache_hit") if rag_retrieval else False,
                    "vector_policy_ids": rag_retrieval.get("vector_policy_ids", []) if rag_retrieval else [],
                    "keyword_policy_ids": rag_retrieval.get("keyword_policy_ids", []) if rag_retrieval else [],
                    "source_scores": rag_retrieval.get("source_scores", {}) if rag_retrieval else {},
                    "embedding": rag_retrieval.get("embedding") if rag_retrieval else None,
                },
                "degraded": degraded,
                "cost_summary": cost_summary,
                "trace": public_trace_summary(request.session_id),
                "next_gap": "用同一阶段 Agent 做大促场景验证，并把证据整理成项目答辩表达。",
            },
        )

    def _compose_final_answer(
        self,
        *,
        request: ChatRequest,
        intent: Intent,
        answer: str,
        risk_level: str,
        next_action: str,
        tool_calls: list[ToolCallTrace],
        citations: list[Citation],
        workflow: dict[str, Any] | None,
        cache_hit: bool,
        degraded: bool,
        risk_keywords: list[str] | None = None,
        skip_final_model: bool = False,
        enable_reasoning: bool = False,
        context_report: dict[str, Any],
    ) -> FinalAnswerModelResult:
        """让真实模型生成最终话术，但安全、低置信、降级和缓存命中保留确定性边界。"""
        skip_reason: str | None = None
        if skip_final_model:
            skip_reason = "runtime_context_direct_answer"
        elif next_action == "ask_clarification":
            skip_reason = "clarification_required"
        elif intent in {"security_request", "low_confidence_query"}:
            skip_reason = "safety_or_low_confidence_boundary"
        elif risk_keywords:
            # 站外交易高危词命中的回答必须原样保留官方风险预警话术，防止模型润色稀释。
            # 该边界优先于知识路径，保证任意意图命中高危词时都记录风险拦截原因。
            skip_reason = "risk_keyword_interception"
        elif intent == "seller_products_query":
            # 卖家售卖情况必须原样返回后端真实数据，禁止最终模型润色改写。
            skip_reason = "seller_products_deterministic_answer"
        elif intent == "seller_orders_query":
            # 卖家卖出订单必须原样返回后端真实履约状态，禁止最终模型润色改写。
            skip_reason = "seller_orders_deterministic_answer"
        elif intent == "recommend_products":
            # 商品推荐必须原样返回商城在售真实商品（价格/库存/活动），禁止最终模型润色改写或补编商品。
            skip_reason = "recommend_deterministic_answer"
        elif intent == "cart_query":
            # 购物车内容必须原样返回后端真实加购记录，禁止最终模型润色改写。
            skip_reason = "cart_deterministic_answer"
        elif intent == "order_query" and any(
            call.tool_name == "get_order_detail" and call.status == "success" for call in tool_calls
        ):
            # 已消解到具体订单的查询（"第一个/最贵那笔"等）：订单状态+物流均由后端工具
            # 直查得到，答案完整，跳过最终模型润色。既避免模型在润色时反问"您是买家还是
            # 卖家"（身份已由 runtime_context 推断），也避免硅基流动偶发 5~80s 延迟击穿
            # 商城网关 45s 读超时导致用户看到"客服服务暂时繁忙"。
            skip_reason = "order_query_deterministic_answer"
        elif intent in {"refund_request", "return_request"} and any(
            call.tool_name == "get_order_detail" and call.status == "success" for call in tool_calls
        ):
            # 未发货退款审批 / 签收后退货工作流已基于真实订单数据给出确定性结论（含资格校验
            # 被拦截的已发货退款），必须原样返回工作流话术，防止模型润色改写出与工作流结论
            # 矛盾的内容（例如把"可以准备退货申请"润色成"二手商品不支持七天无理由"）。
            skip_reason = "after_sale_workflow_deterministic_answer"
        elif degraded:
            skip_reason = "degraded_path"
        elif cache_hit:
            skip_reason = "common_hit_cache"
        if skip_reason:
            result = FinalAnswerModelResult(answer=answer, fallback_reason=skip_reason)
            trace_store.add(
                request.session_id,
                "model_answer_skipped",
                {"session_id": request.session_id, "intent": intent, "reason": skip_reason},
            )
            return result

        result = self.answer_model_client.compose_answer(
            request=request,
            intent=intent,
            deterministic_answer=answer,
            risk_level=risk_level,
            next_action=next_action,
            tool_calls=tool_calls,
            citations=citations,
            workflow=workflow,
            enable_reasoning=enable_reasoning,
            model_context=context_report["model_context"],
        )
        # 规则断言校验：润色答案与确定性结论（工作流资格/会话身份/订单号保真）矛盾时，
        # 回退确定性话术。纯规则匹配零模型调用，防止润色改写出自相矛盾的内容（如
        # 工作流判定可申请退款，润色答案却写"二手商品不支持七天无理由"）。
        if result.answer:
            violation = run_answer_assertions(
                workflow=workflow,
                role=_resolved_role(request, SESSION_MEMORIES.get(request.session_id, {})),
                deterministic_answer=answer,
                composed_answer=result.answer,
            )
            if violation:
                result = FinalAnswerModelResult(answer=answer, fallback_reason=f"assertion_violation:{violation}")
                trace_store.add(
                    request.session_id,
                    "model_answer_assertion_violated",
                    {
                        "session_id": request.session_id,
                        "intent": intent,
                        "violation": violation,
                        "status": "reverted_to_deterministic",
                    },
                )
                return result
        trace_store.add(
            request.session_id,
            "model_answer_generated",
            {
                "session_id": request.session_id,
                "intent": intent,
                "used_model": result.used_model,
                "model_name": result.model_name,
                "fallback_reason": result.fallback_reason,
            },
        )
        return result

    @staticmethod
    def _apply_route_guard(
        *,
        fallback_intent: Intent,
        route_result: ModelRouteResult,
    ) -> ModelRouteResult:
        """规则识别到具体意图时优先于模型路由，模型只负责 general_chat 兜底。

        在线模式下真实模型对二手交易专属意图（验货宝、售卖情况、纠纷、风控等）
        路由不稳定，规则词表已按 route_contract 优先级覆盖这些场景，因此只要
        规则给出了非通用意图，就用规则结果兜底，防止模型绕过 RAG/业务工具路径。
        """
        if route_result.intent == "unknown":
            return ModelRouteResult(
                intent=fallback_intent,
                used_model=route_result.used_model,
                model_name=route_result.model_name,
                fallback_reason="rule_guard_unknown_override",
                framework=route_result.framework,
                prompt_fragments=route_result.prompt_fragments,
            )
        if fallback_intent == "general_chat" or fallback_intent == "unknown":
            return route_result
        if route_result.intent == fallback_intent:
            return route_result
        return ModelRouteResult(
            intent=fallback_intent,
            used_model=route_result.used_model,
            model_name=route_result.model_name,
            fallback_reason=f"rule_guard_{fallback_intent}",
            framework=route_result.framework,
            prompt_fragments=route_result.prompt_fragments,
        )

    def resume(self, request: ChatResumeRequest) -> ChatResumeResponse:
        """恢复暂停的 HITL workflow，具体校验和幂等由 workflow 层负责。"""
        return resume_from_checkpoint(request, trace_store)
