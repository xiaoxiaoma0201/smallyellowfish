"""确定性规则断言校验：最终润色答案与确定性结论（工作流资格/会话身份/关键事实）矛盾时，
回退到确定性话术，防止模型润色改写出与结论自相矛盾的内容（如工作流判定可申请退款，
润色答案却写"二手商品不支持七天无理由"）。纯规则匹配，零模型调用、零延迟。

接入点：customer_service_agent._compose_final_answer 的润色路径（compose_answer 之后）。
"""

from __future__ import annotations

import re
from typing import Any

_ORDER_NO_RE = re.compile(r"SO\d{14,24}")

# 工作流判定 eligible_for_application 时，答案禁止出现的否定话术（防润色把"可申请"改写成"不支持"）。
_ELIGIBLE_FORBIDDEN_TERMS = (
    "不支持七天无理由", "不支持退", "不支持换", "无法退", "不能退", "不能换", "不能申请",
    "无法申请", "不符合退货", "不符合条件", "没有资格", "不能退换", "无法退换", "不能办理", "无法办理",
)

# 工作流判定 not_eligible 时，答案必须包含的拦截说明（防止润色漏掉资格校验结论）。
_NOT_ELIGIBLE_REQUIRED_TERMS = ("不支持", "不能退", "无法", "不符合", "不能申请")

# 工作流判定 not_eligible 时，答案禁止出现的可申请话术（防止润色反向改写）。
_NOT_ELIGIBLE_FORBIDDEN_TERMS = ("可以申请", "可以退", "准备退货", "可以为您办理", "符合退货条件", "可以办理")

# 身份断言：买家身份答案禁止自称卖家侧，卖家身份答案禁止自称买家侧（防双端串台）。
_BUYER_FORBIDDEN_TERMS = (
    "作为卖家", "您是卖家", "你上架的商品", "您上架的商品", "您的店铺", "您的商品已售出", "您的卖出订单", "您的货款",
)
_SELLER_FORBIDDEN_TERMS = (
    "作为买家", "您是买家", "您购买的", "您的购物车", "您的退款申请", "您的售后",
)


def _order_nos(text: str) -> set[str]:
    return set(_ORDER_NO_RE.findall(text))


def check_workflow_consistency(workflow: dict[str, Any] | None, answer: str) -> str | None:
    """工作流结论断言：eligibility_status 与话术方向必须一致，返回冲突描述或 None。"""
    if not workflow:
        return None
    status = workflow.get("eligibility_status")
    if status == "eligible_for_application":
        for term in _ELIGIBLE_FORBIDDEN_TERMS:
            if term in answer:
                return f"工作流判定可申请，但答案含否定话术「{term}」"
    elif status == "not_eligible":
        if not any(term in answer for term in _NOT_ELIGIBLE_REQUIRED_TERMS):
            return "工作流判定不可申请，但答案未包含拦截说明"
        for term in _NOT_ELIGIBLE_FORBIDDEN_TERMS:
            if term in answer:
                return f"工作流判定不可申请，但答案含可申请话术「{term}」"
    return None


def check_role_consistency(role: str | None, answer: str) -> str | None:
    """身份断言：显式身份下，答案不得出现另一侧的自称表述。"""
    if role == "buyer":
        for term in _BUYER_FORBIDDEN_TERMS:
            if term in answer:
                return f"买家身份但答案含卖家表述「{term}」"
    elif role == "seller":
        for term in _SELLER_FORBIDDEN_TERMS:
            if term in answer:
                return f"卖家身份但答案含买家表述「{term}」"
    return None


def check_order_no_fidelity(deterministic_answer: str, composed_answer: str) -> str | None:
    """确定性事实断言：确定性答案中的订单号必须完整保留在润色答案中（关键信息保全）。"""
    missing = _order_nos(deterministic_answer) - _order_nos(composed_answer)
    if missing:
        return f"润色答案丢失订单号 {sorted(missing)}"
    return None


def run_answer_assertions(
    *,
    workflow: dict[str, Any] | None,
    role: str | None,
    deterministic_answer: str,
    composed_answer: str,
) -> str | None:
    """按序执行全部断言，返回第一个冲突描述；全部通过返回 None。"""
    checks = (
        lambda: check_workflow_consistency(workflow, composed_answer),
        lambda: check_role_consistency(role, composed_answer),
        lambda: check_order_no_fidelity(deterministic_answer, composed_answer),
    )
    for check in checks:
        violation = check()
        if violation:
            return violation
    return None
