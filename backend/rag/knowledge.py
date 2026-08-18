"""项目内置知识片段。大促场景验证把售后、发票、活动和会员规则集中成可引用 Citation。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from api.schemas import *
from rag.documents import load_knowledge_citation
from rag.hybrid_retrieval import retrieve_knowledge
from state.session_state import COMMON_HIT_CACHE

REFUND_POLICY = load_knowledge_citation("after_sale_policy.md")
RETURN_POLICY = load_knowledge_citation("received_return_policy.md")
INVOICE_FAQ = load_knowledge_citation("payment_invoice_policy.md")
PROMOTION_POLICY = load_knowledge_citation("promotion_policy.md")
MEMBER_COUPON_POLICY = load_knowledge_citation("member_coupon_policy.md")
OFF_PLATFORM_RISK = load_knowledge_citation("off_platform_transaction_risk.md")


@dataclass(frozen=True)
class KnowledgePathResult:
    """稳定知识路径的确定性结果，供 Agent 编排层直接拼装响应。"""

    answer: str
    citations: list[Citation]
    risk_level: RiskLevel
    next_action: NextAction
    needs_human_approval: bool
    cache_hit: bool = False
    rerank: dict[str, Any] | None = None
    retrieval_debug: dict[str, Any] | None = None
    trace_events: tuple[tuple[str, dict[str, Any]], ...] = ()


def low_confidence_result(session_id: str, intent: Intent) -> KnowledgePathResult:
    """纯知识低置信场景保守兜底，不让模型编造规则。

    文案刻意与具体主题解耦：知识库无命中可能发生在任何场景（售后争议、卖家规则、
    一般闲聊等），不能套用"活动/会员/隐藏券"话术，否则会答非所问。
    """
    return KnowledgePathResult(
        answer="抱歉，你这个问题暂时没有检索到小黄鱼平台已发布的相关规则或准确信息。为避免给你错误信息，建议你补充订单号或商品链接等细节，我会转人工客服为你进一步核实。",
        citations=[],
        risk_level="medium",
        next_action="transfer_to_human",
        needs_human_approval=False,
        trace_events=(
            (
                "rag_low_confidence_fallback",
                {
                    "session_id": session_id,
                    "intent": intent,
                    "hit_count": 0,
                    "retrieval_stage": "pre_retrieval",
                    "pending_action": "transfer_to_human",
                    "status": "low_confidence",
                },
            ),
        ),
    )


def invoice_faq_result(session_id: str) -> KnowledgePathResult:
    """发票 FAQ 读取最终回答缓存；首次模型回答由编排层在生成后写入。"""
    cache_key = "faq:invoice_issue"
    cached = COMMON_HIT_CACHE.get(cache_key)
    if cached:
        return KnowledgePathResult(
            answer=str(cached["answer"]),
            citations=[INVOICE_FAQ],
            risk_level="low",
            next_action="answer_user",
            needs_human_approval=False,
            cache_hit=True,
        )

    retrieval = retrieve_knowledge("电子发票通常多久能准备好", "faq_query")
    citation = next(
        (item for item in retrieval.citations if (item.metadata or {}).get("policy_id") == "invoice_issue"),
        INVOICE_FAQ,
    )
    answer = "电子发票通常在订单完成后 24 小时内开具，你可以在订单详情页查看和下载。"
    return KnowledgePathResult(
        answer=answer,
        citations=[citation],
        risk_level="low",
        next_action="answer_user",
        needs_human_approval=False,
        retrieval_debug=retrieval.debug,
        trace_events=(
            (
                "rag_pre_retrieved",
                {
                    "session_id": session_id,
                    "hit_count": 1,
                    "retrieval_stage": "pre_retrieval",
                    "policy_id": "invoice_issue",
                },
            ),
        ),
    )


FULFILLMENT_BUYER_DEFAULT = load_knowledge_citation("buyer_default_policy.md")
FULFILLMENT_GUARANTEE = load_knowledge_citation("trading_guarantee_policy.md")
FULFILLMENT_RETURN_RULE = load_knowledge_citation("received_return_policy.md")
FULFILLMENT_AFTER_SALE_BASIC = load_knowledge_citation("after_sale_basic_policy.md")

# 履约咨询的三个子类关键词，决定组织哪一段标准话术与对应引用。
# 注意与 tools/planning.py 的 _FULFILLMENT_CONCERN_TERMS 保持同一套口语覆盖，
# 否则意图判定命中（如"没动静"）但子类组织没命中时会落到 low_confidence 兜底，等于没回答。
_FULFILLMENT_SHIP_TERMS = ("不发货", "还没发货", "还不发货", "一直不发货", "怎么还不发", "催发货", "催促发货", "拖了", "拖我", "拖着", "没动静", "没下落", "没音信", "没消息")
_FULFILLMENT_SCAM_TERMS = ("被骗", "遇到骗子", "是骗子", "是不是骗", "怕被骗", "会不会是骗", "骗人的", "骗钱的", "钱都付了", "付了钱", "钱付了", "款都付了", "钱都给了", "没影", "没消息")
_FULFILLMENT_RETURN_LOGISTICS_TERMS = ("退的货", "退货的", "退回去", "退货物流", "退货到", "退件", "退回的", "寄回去的")


def fulfillment_consult_result(session_id: str, user_message: str) -> KnowledgePathResult:
    """履约担忧/售后物流咨询：催发货、卖家不发货、担心被骗、退货物流状态等。

    按子类预检索对应规则文档并组织确定性话术：催发货（联系卖家/催促发货/平台介入/可退款申诉）、
    被骗担忧（担保交易资金托管安抚 + 站外交易才是诈骗）、退货物流（售后记录查看/卖家签收后审核/可申诉）。
    不编造统一发货时限、物流位置、退货到账时间等平台无统一数据的事实。
    """
    sections: list[str] = []
    citations: list[Citation] = []

    if any(term in user_message for term in _FULFILLMENT_SHIP_TERMS):
        citations.append(FULFILLMENT_BUYER_DEFAULT)
        rule_text = _select_relevant_sections(FULFILLMENT_BUYER_DEFAULT.snippet, "卖家已收款却迟迟不发货 催促发货")
        section = (
            "关于催发货：小黄鱼平台暂不承诺统一的卖家发货时限。你可以先在订单页站内聊天联系卖家确认发货安排；"
            "若卖家迟迟不发货，可在订单详情页发起「催促发货」，超时未发货平台会介入处理；"
            "仍无进展时，可在订单页申请退款或发起申诉，平台会按规则同步处理。所有沟通请保留在站内。"
        )
        if rule_text and rule_text not in sections:
            section += f"\n{rule_text}"
        sections.append(section)

    if any(term in user_message for term in _FULFILLMENT_SCAM_TERMS):
        citations.append(FULFILLMENT_GUARANTEE)
        guarantee_text = _select_relevant_sections(FULFILLMENT_GUARANTEE.snippet, "担保交易 资金托管 卖家发货 确认收货 解冻")
        section = (
            "关于资金安全请放心：小黄鱼采用担保交易模式，你付款后货款由平台统一托管，不会直接到卖家账户，"
            "卖家发货、你确认收货后资金才会解冻给卖家。付款后还没收到货并不等于被骗，常见原因是卖家尚未发货"
            "或物流在途；你可以在订单详情页查看物流轨迹，也可以把订单号告诉我，我帮你核实当前状态。"
            "真正需要警惕的是站外交易：任何引导你加微信、QQ 私下转账或扫码付款的都是诈骗，请务必留在平台内完成交易。"
        )
        if guarantee_text and guarantee_text not in sections:
            section += f"\n{guarantee_text}"
        sections.append(section)
        if not any((citation.metadata or {}).get("policy_id") == "off_platform_transaction_risk" for citation in citations):
            citations.append(OFF_PLATFORM_RISK)

    if any(term in user_message for term in _FULFILLMENT_RETURN_LOGISTICS_TERMS):
        citations.append(FULFILLMENT_RETURN_RULE)
        citations.append(FULFILLMENT_AFTER_SALE_BASIC)
        return_text = _select_relevant_sections(FULFILLMENT_AFTER_SALE_BASIC.snippet, "退货 售后 申诉 凭证")
        section = (
            "关于退货物流：退货寄出后，你可以在订单详情页的「售后/退款」记录中查看退货物流进度；"
            "卖家签收退货后，平台会按规则进入退款审核。若卖家收到退货后迟迟不处理，"
            "你可以在订单页发起申诉并上传退货物流凭证，平台会按规则介入处理。"
        )
        if return_text and return_text not in sections:
            section += f"\n{return_text}"
        sections.append(section)

    if not sections:
        result = low_confidence_result(session_id, "fulfillment_consult_query")
        return KnowledgePathResult(
            answer=result.answer,
            citations=result.citations,
            risk_level=result.risk_level,
            next_action=result.next_action,
            needs_human_approval=result.needs_human_approval,
            trace_events=result.trace_events,
        )
    return KnowledgePathResult(
        answer="\n\n".join(sections),
        citations=citations,
        risk_level="medium",
        next_action="answer_user",
        needs_human_approval=False,
        trace_events=(
            (
                "rag_pre_retrieved",
                {
                    "session_id": session_id,
                    "intent": "fulfillment_consult_query",
                    "hit_count": len(citations),
                    "retrieval_stage": "pre_retrieval",
                    "policy_ids": [(citation.metadata or {}).get("policy_id") for citation in citations],
                },
            ),
        ),
    )


def promotion_policy_result(session_id: str, user_message: str) -> KnowledgePathResult:
    """活动和会员券问题先过证据门，再用轻量 reranker 决定最终引用顺序。"""
    normalized = user_message.replace(" ", "").lower()
    if any(term in normalized for term in ("隐藏券", "火星会员", "不存在的活动", "未知活动", "未发布")):
        candidates: list[tuple[Citation, float, list[str]]] = []
        retrieval_debug = {"mode": "evidence_gate_blocked_before_retrieval", "reason": "unsupported_policy_claim"}
    else:
        retrieval = retrieve_knowledge(user_message, "promotion_query")
        retrieval_debug = retrieval.debug
        allowed_policy_ids = _promotion_scope_policy_ids(normalized)
        candidates = [
            (
                citation,
                citation.score,
                list(
                    retrieval.debug.get("source_scores", {})
                    .get((citation.metadata or {}).get("policy_id"), {})
                    .get("sources", [])
                ),
            )
            for citation in retrieval.citations
            if (citation.metadata or {}).get("policy_id") in allowed_policy_ids
        ]
        if "叠加" in normalized and len(candidates) < 2:
            candidates = []
    reranked = _rerank_promotion_candidates(user_message, candidates)
    citations = [citation for citation, _score, _reasons in reranked]
    if not citations:
        result = low_confidence_result(session_id, "promotion_query")
        # 用户明确询问"隐藏券/火星会员"等平台未发布的活动时，话术要直接点明"以页面为准"，
        # 不能套用通用兜底让用户去"补充订单号转人工"；其余无命中情况才用中性兜底文案。
        if any(term in normalized for term in ("隐藏券", "火星会员", "不存在的活动", "未知活动", "未发布")):
            answer = (
                "小黄鱼平台当前未发布你提到的该活动或会员规则，我无法核实其真实性。"
                "请以活动页和结算页展示为准，谨防站外流传的所谓「隐藏券」信息。"
            )
        else:
            answer = result.answer
        return KnowledgePathResult(
            answer=answer,
            citations=result.citations,
            risk_level=result.risk_level,
            next_action=result.next_action,
            needs_human_approval=result.needs_human_approval,
            retrieval_debug=retrieval_debug,
            trace_events=(
                (
                    "rag_evidence_gate_blocked",
                    {
                        "session_id": session_id,
                        "intent": "promotion_query",
                        "hit_count": 0,
                        "retrieval_stage": "pre_retrieval",
                        "status": "low_confidence",
                        "reason": "no_trusted_policy_citation",
                    },
                ),
                *result.trace_events,
            ),
        )
    rerank_debug = _build_rerank_debug(reranked)
    rerank_debug["retrieval"] = retrieval_debug
    return KnowledgePathResult(
        answer=_promotion_policy_answer(citations),
        citations=citations,
        risk_level="low",
        next_action="answer_user",
        needs_human_approval=False,
        rerank=rerank_debug,
        retrieval_debug=retrieval_debug,
        trace_events=(
            (
                "rag_pre_retrieved",
                {
                    "session_id": session_id,
                    "hit_count": len(citations),
                    "retrieval_stage": "pre_retrieval",
                    "policy_id": "promotion_618_stack_rule",
                    "candidate_policy_ids": [
                        citation.metadata.get("policy_id") for citation, _score, _reasons in candidates if citation.metadata
                    ],
                },
            ),
            (
                "rag_reranked",
                {
                    "session_id": session_id,
                    "mode": rerank_debug["mode"],
                    "reranked_policy_ids": rerank_debug["policy_ids"],
                    "top_policy_id": rerank_debug["policy_ids"][0] if rerank_debug["policy_ids"] else None,
                    "rerank_reasons": rerank_debug["reasons"],
                },
            ),
        ),
    )


def _promotion_scope_policy_ids(normalized_query: str) -> set[str]:
    """单一问题只引用对应规则；明确问叠加时才联合两类证据。"""
    asks_promotion = any(term in normalized_query for term in ("618", "大促", "活动", "满减", "300减40"))
    asks_member = any(term in normalized_query for term in ("会员", "会员券", "金卡", "银卡", "优惠券", "会员折扣"))
    if asks_promotion and not asks_member:
        return {"promotion_618_stack_rule"}
    if asks_member and not asks_promotion:
        return {"member_coupon_gold_rule"}
    return {"promotion_618_stack_rule", "member_coupon_gold_rule"}


def _rerank_promotion_candidates(
    user_message: str,
    candidates: list[tuple[Citation, float, list[str]]],
) -> list[tuple[Citation, float, list[str]]]:
    """轻量 reranker：在候选池里按当前问题的业务约束重新排序。

    这里刻意不调用外部商业模型，避免大促场景验证依赖网络；但保留 reranker 的核心闭环：
    初召回候选、按问题重排、citation 跟随最终排序。
    """
    normalized = user_message.replace(" ", "").lower()
    reranked: list[tuple[Citation, float, list[str]]] = []
    for citation, score, reasons in candidates:
        policy_id = citation.metadata.get("policy_id") if citation.metadata else ""
        final_score = score
        final_reasons = list(reasons)
        if policy_id == "promotion_618_stack_rule" and any(term in normalized for term in ("618", "满减", "300减40", "大促")):
            final_score += 0.18
            final_reasons.append("当前大促规则加权")
        if policy_id == "member_coupon_gold_rule" and any(term in normalized for term in ("金卡", "银卡", "会员券", "会员折扣")):
            final_score += 0.16
            final_reasons.append("会员券条件匹配")
        if "叠加" in normalized:
            final_score += 0.08
            final_reasons.append("叠加问题需要联合引用")
        reranked.append((citation, round(min(1.0, final_score), 3), final_reasons))
    return sorted(reranked, key=lambda item: item[1], reverse=True)


def _build_rerank_debug(reranked: list[tuple[Citation, float, list[str]]]) -> dict[str, Any]:
    """把 rerank 结果压成公开调试状态，方便观察最终闭环。"""
    policy_ids = [citation.metadata.get("policy_id") for citation, _score, _reasons in reranked if citation.metadata]
    return {
        "mode": "lightweight_reranker",
        "policy_ids": policy_ids,
        "scores": {citation.metadata.get("policy_id"): score for citation, score, _reasons in reranked if citation.metadata},
        "reasons": {citation.metadata.get("policy_id"): reasons for citation, _score, reasons in reranked if citation.metadata},
    }


def _promotion_policy_answer(citations: list[Citation]) -> str:
    """按实际命中的 citation 组织回答，避免单一证据问题被迫套完整叠加规则。"""
    policy_ids = {citation.metadata.get("policy_id") for citation in citations if citation.metadata}
    if {"promotion_618_stack_rule", "member_coupon_gold_rule"}.issubset(policy_ids):
        return (
            "根据小黄鱼二手电商交易平台 618 大促规则，满 300 减 40 可以与平台会员券叠加，"
            "但不能与同类型满减券重复叠加；金卡会员券需要在有效期内由本人账号使用。"
            "活动与会员折扣产生的让利由平台补贴，不由卖方承担。"
            "具体适用情况以商品页和结算页为准。"
        )
    if "promotion_618_stack_rule" in policy_ids:
        return "根据小黄鱼二手电商交易平台 618 大促规则，满 300 减 40 活动可用，但不能与同类型满减券重复叠加。具体是否适用于当前商品以商品页和结算页为准。"
    if "member_coupon_gold_rule" in policy_ids:
        return "根据小黄鱼二手电商交易平台会员规则，金卡、银卡会员可享受平台会员购物折扣；折扣由平台补贴，不由卖方承担，卖家仍按订单成交价格结算货款。会员券需在有效期内由本人账号使用，不能转让。具体使用条件以商品页和结算页为准。"
    return "没有检索到小黄鱼二手电商交易平台已发布的可信活动或会员规则，我不能编造隐藏券规则。建议以活动页和结算页展示为准，或转人工客服进一步核实。"


def platform_rule_result(session_id: str, user_message: str, role: str = "unknown", answer_query: str | None = None) -> KnowledgePathResult:
    """平台通用规则标准话术：担保交易、禁售商品、客服职责、信用分、资金冻结、账号处罚。"""
    return _policy_knowledge_result(
        session_id=session_id,
        intent="platform_rule_query",
        user_message=user_message,
        role=role,
        answer_query=answer_query,
        answer_builder=_policy_snippet_answer,
    )


def buyer_service_result(session_id: str, user_message: str, role: str = "unknown", answer_query: str | None = None) -> KnowledgePathResult:
    """买家侧交易咨询标准话术：真伪成色、议价砍价、验货宝、同城自提、退换货、空包裹。"""
    return _policy_knowledge_result(
        session_id=session_id,
        intent="buyer_service_query",
        user_message=user_message,
        role=role,
        answer_query=answer_query,
        answer_builder=_policy_snippet_answer,
    )


def seller_service_result(session_id: str, user_message: str, role: str = "unknown", answer_query: str | None = None) -> KnowledgePathResult:
    """卖家侧交易咨询标准话术：发布规范、发货运费、货款到账、改价、违约、曝光。"""
    return _policy_knowledge_result(
        session_id=session_id,
        intent="seller_service_query",
        user_message=user_message,
        role=role,
        answer_query=answer_query,
        answer_builder=_policy_snippet_answer,
    )


def dispute_result(session_id: str, user_message: str, role: str = "unknown", answer_query: str | None = None) -> KnowledgePathResult:
    """纠纷维权标准话术：只陈述举证标准和申诉流程，不判定买卖双方责任归属。"""
    return _policy_knowledge_result(
        session_id=session_id,
        intent="dispute_query",
        user_message=user_message,
        role=role,
        answer_query=answer_query,
        answer_builder=_policy_snippet_answer,
        risk_level="medium",
    )


def risk_prevention_result(session_id: str, user_message: str, role: str = "unknown", answer_query: str | None = None) -> KnowledgePathResult:
    """风险防控标准预警话术：站外交易、低价风险、同城面交安全、违规举报。"""
    return _policy_knowledge_result(
        session_id=session_id,
        intent="risk_prevention_query",
        user_message=user_message,
        role=role,
        answer_query=answer_query,
        answer_builder=_policy_snippet_answer,
    )


def policy_knowledge_result(
    intent: Intent, session_id: str, user_message: str, role: str = "unknown", answer_query: str | None = None
) -> KnowledgePathResult:
    """按二手交易场景意图派发到对应标准话术知识结果，供 Agent 编排层统一调用。

    answer_query 用于回答裁剪：与检索输入解耦，避免历史拼接污染段落相关性判断。
    """
    dispatch = {
        "platform_rule_query": platform_rule_result,
        "buyer_service_query": buyer_service_result,
        "seller_service_query": seller_service_result,
        "dispute_query": dispute_result,
        "risk_prevention_query": risk_prevention_result,
    }
    return dispatch[intent](session_id, user_message, role=role, answer_query=answer_query)


def _policy_knowledge_result(
    *,
    session_id: str,
    intent: Intent,
    user_message: str,
    role: str = "unknown",
    answer_query: str | None = None,
    answer_builder: Callable[[list[Citation], str], str],
    risk_level: RiskLevel = "low",
) -> KnowledgePathResult:
    """检索标准话术知识：相关度低于阈值视为无证据，避免答非所问。"""
    retrieval_query = user_message
    if role in {"buyer", "seller"}:
        # 同一问题按身份视角补充检索上下文，让买家/卖家话术在语义上更相关。
        retrieval_query = f"{role}视角 {user_message}" if role == "seller" else f"买家 {user_message}"
    retrieval = retrieve_knowledge(retrieval_query, intent)
    citations = sorted(
        (citation for citation in retrieval.citations if citation.score >= 0.3),
        key=lambda citation: (-citation.score, (citation.metadata or {}).get("policy_id") or ""),
    )
    if not citations:
        result = low_confidence_result(session_id, intent)
        return KnowledgePathResult(
            answer=result.answer,
            citations=result.citations,
            risk_level=result.risk_level,
            next_action=result.next_action,
            needs_human_approval=result.needs_human_approval,
            retrieval_debug=retrieval.debug,
            trace_events=(
                (
                    "rag_relevance_gate_blocked",
                    {
                        "session_id": session_id,
                        "intent": intent,
                        "hit_count": 0,
                        "retrieval_stage": "pre_retrieval",
                        "status": "low_confidence",
                        "reason": "no_policy_citation_above_threshold",
                    },
                ),
                *result.trace_events,
            ),
        )
    return KnowledgePathResult(
        answer=answer_builder(citations, answer_query or user_message),
        citations=citations[:2],
        risk_level=risk_level,
        next_action="answer_user",
        needs_human_approval=False,
        retrieval_debug=retrieval.debug,
        trace_events=(
            (
                "rag_pre_retrieved",
                {
                    "session_id": session_id,
                    "intent": intent,
                    "hit_count": len(citations),
                    "retrieval_stage": "pre_retrieval",
                    "policy_ids": [(citation.metadata or {}).get("policy_id") for citation in citations],
                },
            ),
        ),
    )


def _policy_snippet_answer(citations: list[Citation], user_message: str) -> str:
    """按用户问题的核心字裁剪每篇话术，只保留相关小节，再按相关度拼接，避免答非所问。"""
    lines: list[str] = []
    seen: set[str] = set()
    for citation in citations:
        text = _select_relevant_sections(citation.snippet, user_message).strip()
        if text and text not in seen:
            seen.add(text)
            lines.append(text)
    if lines:
        return "\n\n".join(lines)
    # 兜底：裁剪全部为空时回退原文，避免空答。
    for citation in citations:
        text = citation.snippet.strip()
        if text and text not in seen:
            seen.add(text)
            lines.append(text)
    return "\n\n".join(lines)


# 中文虚词/常见疑问词，用于从用户问题里提取核心实义字进行段落裁剪。
_STOP_CORE_CHARS = set(
    "的了么吗呢吧啊哦呀哈还需多久什么怎么请问一下你我他她它着过在正商品这那有哪些多少"
    "应该可以能要不要个两正在是哪个些没没很都就也只而或不与及"
)


def _query_core_chars(query: str) -> list[str]:
    """提取用户问题中的核心实义汉字（去除虚词与疑问词）。"""
    return [ch for ch in query if ch not in _STOP_CORE_CHARS and "\u4e00" <= ch <= "\u9fff"]


def _select_relevant_sections(snippet: str, user_message: str) -> str:
    """按用户问题的核心字裁剪整篇知识文档，只保留相关小节，避免答非所问。

    先按小节标题（块首行以冒号结尾）把文档分组：标题块连同其后内容作为一个小节。
    小节标题含核心字时只输出该小节（如"正在审核的商品还需要审核多久"只输出
    "发布审核时效"一节）；没有任何标题命中时退到逐块核心字匹配。
    相关度 = 块内命中的不同核心字数量，全部不命中时回退整篇，避免裁空。
    """
    core_chars = _query_core_chars(user_message)
    if not core_chars:
        return snippet
    unique_chars = set(core_chars)
    blocks = [block.strip() for block in re.split(r"\n\s*\n", snippet) if block.strip()]

    def heading(block: str) -> str | None:
        first = block.splitlines()[0].strip()
        if first.endswith("：") or first.endswith(":"):
            return first[:-1].strip()
        return None

    def score(block: str) -> int:
        return sum(1 for ch in unique_chars if ch in block)

    # 按小节标题分组：标题块吸收其后直到下一个标题块之前的内容。
    sections: list[list[str]] = []
    current: list[str] | None = None
    for block in blocks:
        if heading(block):
            current = [block]
            sections.append(current)
        elif current is None:
            current = [block]
            sections.append(current)
        else:
            current.append(block)

    hit_sections = [
        section
        for section in sections
        if heading(section[0]) and any(ch in heading(section[0]) for ch in unique_chars)
    ]
    if hit_sections:
        selected = [block for section in hit_sections for block in section]
    else:
        # 问题简短（核心字少）时要求至少两个核心字命中，避免"上/架"等泛字误匹配整节。
        threshold = 2 if len(unique_chars) <= 6 else 1
        selected = [block for block in blocks if score(block) >= threshold]
    if not selected:
        return ""
    # 同一篇文档内保持原始段落顺序，跨文档顺序由 citation 相关度控制，
    # 避免风控要点反把风险提示开头排到前面。
    return "\n\n".join(block for _index, block in sorted(enumerate(selected), key=lambda item: item[0]))
