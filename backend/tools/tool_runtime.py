"""业务工具执行层。这里封装订单详情和物流查询，并返回可观察 ToolCallTrace。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import httpx
import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from api.schemas import *
from integrations.ecommerce_client import after_sale_requests_from_ecommerce, cart_from_ecommerce, order_fact_from_ecommerce, products_from_ecommerce, recommend_budget, recommend_condition, recommend_excludes, recommend_keyword, recommend_products_from_ecommerce, seller_orders_from_ecommerce, seller_products_from_ecommerce, _EXCLUDE_CATEGORY_SEMANTICS
from tools.runtime_context import *

def get_order_detail(
    order_id: str | None,
    runtime_user_id: str,
    runtime_context: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, ToolCallTrace]:
    """执行订单详情工具，同时校验订单归属，防止越权售后操作。"""
    arguments = {"order_id": order_id}
    if order_id is None:
        return None, ToolCallTrace(
            tool_name="get_order_detail",
            arguments=arguments,
            output_summary="缺少订单号，不能查询订单。",
            status="error",
            error_type="missing_order_id",
            next_action="ask_clarification",
        )
    order = find_context_order(runtime_context, order_id) or order_fact_from_ecommerce(order_id, runtime_user_id)
    if order is None:
        return None, ToolCallTrace(
            tool_name="get_order_detail",
            arguments=arguments,
            output_summary=f"订单 {order_id} 不存在或当前业务系统暂未返回该订单。",
            status="error",
            error_type="not_found",
            risk_level="medium",
            next_action="ask_clarification",
        )
    if order_user_id(order) and order_user_id(order) != runtime_user_id:
        return None, ToolCallTrace(
            tool_name="get_order_detail",
            arguments=arguments,
            output_summary=f"订单 {order_id} 没有通过当前用户归属校验。",
            status="error",
            error_type="owner_mismatch",
            risk_level="medium",
            next_action="transfer_to_human",
        )
    arguments["fact_source"] = order.get("_fact_source", "runtime_context")
    summary = f"订单 {order_no(order)} 当前状态为{order_status_label(order)}，物流状态为{logistics_status_label(order)}，商品 {item_names(order)[0]}。"
    return order, ToolCallTrace(tool_name="get_order_detail", arguments=arguments, output_summary=summary, status="success", risk_level="low")


def get_order_logistics(order: dict[str, Any]) -> ToolCallTrace:
    """基于订单事实生成物流工具结果，不让模型凭空猜测实时状态。"""
    return ToolCallTrace(
        tool_name="get_order_logistics",
        arguments={"order_id": order_no(order)},
        output_summary=f"物流状态为{logistics_status_label(order)}，订单状态为{order_status_label(order)}。",
        status="success",
        risk_level="low",
        next_action="answer_user",
    )


def get_refund_status(order: dict[str, Any], runtime_user_id: str) -> ToolCallTrace:
    """读取已存在的售后申请状态；查询进度绝不能隐式创建新退款。"""
    requests = after_sale_requests_from_ecommerce(order_no(order), runtime_user_id)
    if requests is None:
        return ToolCallTrace(
            tool_name="get_refund_status",
            arguments={"order_id": order_no(order), "fact_source": "business_api_unavailable"},
            output_summary=f"订单 {order_no(order)} 的退款进度暂时无法从业务系统核实，本轮不会猜测退款状态，建议稍后重试或转人工客服。",
            status="error",
            risk_level="medium",
            next_action="transfer_to_human",
            error_type="business_api_unavailable",
        )
    refund_requests = [item for item in requests if str(item.get("requestType") or item.get("type") or "").upper() in {"REFUND", "CANCEL_ORDER"}]
    if not refund_requests:
        return ToolCallTrace(
            tool_name="get_refund_status",
            arguments={"order_id": order_no(order), "fact_source": order.get("_fact_source", "runtime_context")},
            output_summary=f"订单 {order_no(order)} 暂未查询到退款申请记录。",
            status="success",
            risk_level="low",
            next_action="answer_user",
        )
    latest = max(
        refund_requests,
        key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""),
    )
    request_id = str(latest.get("requestId") or latest.get("requestNo") or "未知申请号")
    status = str(latest.get("status") or "UNKNOWN").upper()
    status_label = {"PENDING": "待处理", "REVIEWING": "审核中", "APPROVED": "已通过", "REJECTED": "未通过", "COMPLETED": "已完成"}.get(status, status)
    return ToolCallTrace(
        tool_name="get_refund_status",
        arguments={"order_id": order_no(order), "fact_source": latest.get("_fact_source", "business_api")},
        output_summary=f"退款申请 {request_id} 当前状态为{status_label}。",
        status="success",
        risk_level="low",
        next_action="answer_user",
    )


def search_products(keyword: str) -> tuple[list[dict[str, Any]], ToolCallTrace]:
    """查询商品实时价格、库存和活动事实；稳定叠加规则仍由 RAG 提供。"""
    products = products_from_ecommerce(keyword)
    if not products:
        return [], ToolCallTrace(
            tool_name="search_products",
            arguments={"keyword": keyword, "fact_source": "business_api_unavailable"},
            output_summary="没有查询到匹配商品，不能猜测库存或价格。",
            status="error",
            risk_level="low",
            next_action="ask_clarification",
            error_type="product_not_found",
        )
    product = products[0]
    name = str(product.get("name") or product.get("productName") or "商品")
    stock = product.get("stock")
    price = product.get("price")
    summary = f"{name} 商品标价 {price} 元，库存 {stock} 件。"
    promotion = product.get("promotion")
    if isinstance(promotion, dict):
        promotion_name = promotion.get("promotionName")
        promotion_price = promotion.get("promotionPrice")
        condition = promotion.get("conditionSummary")
        discount_summary = promotion.get("discountSummary")
        promotion_facts = [
            f"当前活动为{promotion_name}" if promotion_name else None,
            f"活动价 {promotion_price} 元" if promotion_price is not None else None,
            f"适用条件为{condition}" if condition else None,
            str(discount_summary).rstrip("。") if discount_summary else None,
        ]
        facts = "，".join(fact for fact in promotion_facts if fact)
        if facts:
            summary += f" {facts}。"
    return products, ToolCallTrace(
        tool_name="search_products",
        arguments={
            "keyword": keyword,
            "product_name": name,
            "fact_source": product.get("_fact_source", "business_api"),
        },
        output_summary=summary,
        status="success",
        risk_level="low",
        next_action="answer_user",
    )


def recommend_products(user_message: str) -> tuple[list[dict[str, Any]], ToolCallTrace]:
    """按用户需求（类别/用途/预算/排除项）从商城在售商品库推荐真实商品。

    支持"不要电子产品/别推荐数码的/除了耳机"这类排除约束：先按正向关键词取候选，
    再剔除命中的排除类别/品类，最后组织推荐话术。数据必须来自商城在售接口
    （/api/shop/products，只含已上架可购商品），价格、库存、活动均以实时数据为准，不能编造。
    """
    keyword = recommend_keyword(user_message)
    budget = recommend_budget(user_message)
    condition = recommend_condition(user_message)
    excludes = recommend_excludes(user_message)
    products = recommend_products_from_ecommerce(keyword)
    if products is None:
        return [], ToolCallTrace(
            tool_name="recommend_products",
            arguments={
                "user_message": user_message,
                "keyword": keyword,
                "budget": budget,
                "condition": condition,
                "excludes": excludes,
            },
            output_summary="商城商品服务暂不可用，本轮不会凭空推荐商品，建议稍后重试或到商城首页浏览在售好物。",
            status="error",
            risk_level="medium",
            next_action="transfer_to_human",
            error_type="business_api_unavailable",
        )
    candidates = [p for p in products if int(p.get("stockQuantity") or p.get("stock") or 0) > 0]
    if excludes:
        candidates = [p for p in candidates if not _product_excluded(p, excludes)]
    if condition is not None:
        # 成色严格要求：只保留明确标注成色且数值 >= 下限的商品，未标注成色的不参与此类推荐。
        candidates = [p for p in candidates if (_product_condition_score(p) or 0.0) >= condition]
    if budget is not None:
        within_budget = [p for p in candidates if float(p.get("price") or 0) <= budget]
        if within_budget:
            candidates = within_budget
    candidates = sorted(candidates, key=lambda p: float(p.get("price") or 0))
    if not candidates:
        hint = "商城当前没有在售的匹配商品，不能强行推荐。你可以换个类别关键词，或到商城首页按分类浏览在售好物。"
        if excludes:
            exclude_text = "、".join(f"「{item['value']}」" for item in excludes)
            hint = f"已按你的要求排除{exclude_text}，排除后暂无其他符合条件的在售商品，不能强行推荐。你可以去掉排除条件或换个类别关键词试试。"
        elif condition is not None:
            hint = f"商城当前没有标注「{condition:g} 成新以上」的在售商品，不能强行推荐。你可以降低成色要求，或到商城首页按分类浏览在售好物。"
        return [], ToolCallTrace(
            tool_name="recommend_products",
            arguments={
                "user_message": user_message,
                "keyword": keyword,
                "budget": budget,
                "condition": condition,
                "excludes": excludes,
            },
            output_summary=hint,
            status="success",
            risk_level="low",
            next_action="answer_user",
        )
    picked = candidates[:5]
    parts: list[str] = []
    for idx, product in enumerate(picked, 1):
        name = str(product.get("name") or product.get("productName") or "商品")
        price = product.get("price")
        category = str(product.get("category") or "").strip()
        stock = int(product.get("stockQuantity") or product.get("stock") or 0)
        description = str(product.get("description") or "")
        condition_match = re.search(r"\d+(?:\.\d+)?成新", description)
        condition_label = condition_match.group(0) if condition_match else ""
        extra = "、".join(part for part in (category, condition_label) if part)
        promotion = product.get("promotion")
        promotion_text = ""
        if isinstance(promotion, dict) and promotion.get("promotionPrice") is not None:
            promotion_text = f"（活动价 {promotion['promotionPrice']} 元）"
        part = f"{idx}. {name}（{extra}）标价 {price} 元{promotion_text}，库存 {stock} 件"
        parts.append(part)
    if keyword:
        head = f"根据你需要的「{keyword}」，商城当前在售为你找到以下推荐"
    else:
        head = "为你推荐商城当前在售好物（按价格从低到高）"
    modifiers: list[str] = []
    if budget is not None:
        within_budget = [p for p in picked if float(p.get("price") or 0) <= budget]
        if len(within_budget) == len(picked):
            modifiers.append(f"均在预算 {budget:g} 元以内")
        else:
            modifiers.append(f"预算 {budget:g} 元内暂无在售匹配，以下为相近价位的选择")
    if excludes:
        exclude_text = "、".join(f"「{item['value']}」" for item in excludes)
        modifiers.append(f"已按你的要求排除{exclude_text}")
    if condition is not None:
        modifiers.append(f"已按成色要求筛选（{condition:g} 成新以上）")
    if modifiers:
        head += f"（{'；'.join(modifiers)}）"
    head += "："
    tail = f"以上均来自商城实时在售商品。可在商城搜索「{keyword}」查看更多" if keyword else "以上均来自商城实时在售商品。可在商城首页按分类浏览更多好物"
    tail += "；购买前请与卖家确认成色、配件和售后细节。"
    summary = f"{head}{'；'.join(parts)}。{tail}"
    return picked, ToolCallTrace(
        tool_name="recommend_products",
        arguments={
            "user_message": user_message,
            "keyword": keyword,
            "budget": budget,
            "condition": condition,
            "excludes": excludes,
            "fact_source": picked[0].get("_fact_source", "business_api"),
            "recommended_count": len(picked),
        },
        output_summary=summary,
        status="success",
        risk_level="low",
        next_action="answer_user",
    )


def _product_excluded(product: dict[str, Any], excludes: list[dict[str, str]]) -> bool:
    """判断商品是否命中排除约束：类别精确匹配、类别语义兜底或名称/描述含排除关键词。"""
    name = str(product.get("name") or product.get("productName") or "")
    category = str(product.get("category") or "")
    description = str(product.get("description") or "")
    haystack = f"{name}{category}{description}"
    for item in excludes:
        if item["type"] == "category":
            if category == item["value"] or item["value"] in category:
                return True
            # 语义兜底：商城类目与用户直觉不一致时（如运动相机归在"户外闲置"），
            # 排除"数码闲置/家电闲置"也要剔除名称/描述明显属于该类别的商品。
            semantics = _EXCLUDE_CATEGORY_SEMANTICS.get(item["value"])
            if semantics and any(term in haystack for term in semantics):
                return True
        elif item["value"] in haystack:
            return True
    return False


def _product_condition_score(product: dict[str, Any]) -> float | None:
    """从商品名称/描述中提取成色数值（如 9成新→9.0、8.5成新→8.5）；未标注返回 None。"""
    text = f"{product.get('name') or product.get('productName') or ''}{product.get('description') or ''}"
    m = re.search(r"(\d+(?:\.\d+)?)\s*成新", text)
    return float(m.group(1)) if m else None


def query_seller_product_sales(
    seller_user_id: str, status_filter: str | None = None
) -> tuple[list[dict[str, Any]], ToolCallTrace]:
    """查询卖家自己发布商品的售卖状态。数据必须来自业务后端商品库，不能凭话术编造。

    status_filter 为可选状态过滤（SOLD/ON_SALE/PENDING_REVIEW）：用户按状态提问
    （"已经卖掉的有哪些""在售的有哪些"）时，只列出对应状态的商品，不把全部商品
    一次性倒出；汇总统计（共发布/已售出/在售数）仍保留，保证全局事实不丢失。
    """
    products = seller_products_from_ecommerce(seller_user_id)
    if products is None:
        return [], ToolCallTrace(
            tool_name="query_seller_product_sales",
            arguments={"seller_user_id": seller_user_id},
            output_summary="业务后端暂未返回你发布的商品数据，本轮不会猜测售卖情况，建议稍后重试或转人工客服。",
            status="error",
            risk_level="medium",
            next_action="transfer_to_human",
            error_type="business_api_unavailable",
        )
    if not products:
        return [], ToolCallTrace(
            tool_name="query_seller_product_sales",
            arguments={"seller_user_id": seller_user_id, "fact_source": "business_api"},
            output_summary="你当前账号暂未发布任何商品。",
            status="success",
            risk_level="low",
            next_action="answer_user",
        )
    status_label = {"PENDING_REVIEW": "待审核", "ON_SALE": "在售", "SOLD": "已售出"}
    approval_label = {"pending": "审核中", "approved": "已通过", "rejected": "未通过"}
    on_sale_count = sum(1 for product in products if str(product.get("saleStatus") or "") == "ON_SALE")
    sold_count = sum(1 for product in products if str(product.get("saleStatus") or "") == "SOLD")
    parts: list[str] = []
    for product in products:
        name = str(product.get("name") or "商品")
        price = product.get("price")
        status = str(product.get("saleStatus") or "UNKNOWN")
        if status_filter and status != status_filter:
            continue
        if status == "SOLD":
            buyer = str(product.get("buyerUserId") or "未知买家")
            sold_at = str(product.get("soldAt") or "").replace("T", " ")[:16]
            order_no = str(product.get("soldOrderNo") or "")
            parts.append(f"{name}（标价{price}元）已售出，买家 {buyer}，售出时间 {sold_at}，订单号 {order_no}")
        elif status == "PENDING_REVIEW":
            approval_status = approval_label.get(str(product.get("approvalStatus") or ""), "审核中")
            parts.append(f"{name}（标价{price}元）待审核，商品发布审批状态为{approval_status}")
        else:
            stock = product.get("stockQuantity") if product.get("stockQuantity") is not None else product.get("stock")
            parts.append(f"{name}（标价{price}元）在售中，库存 {stock or 0} 件")
    summary = f"你共发布 {len(products)} 件商品，已售出 {sold_count} 件，在售 {on_sale_count} 件。"
    if status_filter and not parts:
        empty_hint = {
            "SOLD": "当前没有已售出的商品。",
            "ON_SALE": "当前没有在售中的商品。",
            "PENDING_REVIEW": "当前没有待审核的商品。",
        }
        summary += empty_hint.get(status_filter, "")
    if parts:
        summary += "；".join(parts) + "。"
    return products, ToolCallTrace(
        tool_name="query_seller_product_sales",
        arguments={
            "seller_user_id": seller_user_id,
            "status_filter": status_filter,
            "fact_source": products[0].get("_fact_source", "business_api"),
            "product_count": len(products),
        },
        output_summary=summary,
        status="success",
        risk_level="low",
        next_action="answer_user",
    )


def query_seller_orders(seller_user_id: str) -> tuple[list[dict[str, Any]], ToolCallTrace]:
    """查询卖家自己商品的卖出订单及履约状态。数据必须来自业务后端订单库，不能凭话术编造。

    卖出订单由买家在商城的购买支付实时写入业务后端（:8081），买家支付后卖家侧与
    客服查询都读取这份真实数据：待发货 → 已发货 → 已签收 的推进在商城内实时同步。
    """
    orders = seller_orders_from_ecommerce(seller_user_id)
    if orders is None:
        return [], ToolCallTrace(
            tool_name="query_seller_orders",
            arguments={"seller_user_id": seller_user_id},
            output_summary="业务后端暂未返回你的卖出订单数据，本轮不会猜测订单状态，建议稍后重试或转人工客服。",
            status="error",
            risk_level="medium",
            next_action="transfer_to_human",
            error_type="business_api_unavailable",
        )
    if not orders:
        return [], ToolCallTrace(
            tool_name="query_seller_orders",
            arguments={"seller_user_id": seller_user_id, "fact_source": "business_api"},
            output_summary="你当前账号暂未查询到卖出订单，买家拍下并完成支付后订单会实时出现在这里。",
            status="success",
            risk_level="low",
            next_action="answer_user",
        )
    status_label = {
        "PENDING_PAYMENT": "待支付", "PAID_PENDING_SHIPMENT": "待发货", "PENDING_SHIPMENT": "待发货",
        "SHIPPED": "已发货", "DELIVERED": "已签收", "CANCELED": "已取消",
        "COMPLETED": "已完成", "REFUNDED": "已退款", "UNPAID": "未支付", "PAID": "已支付",
    }
    pending_ship_count = sum(
        1 for order in orders
        if str(order.get("orderStatus") or order.get("fulfillmentStatus") or "") in {"PAID_PENDING_SHIPMENT", "PENDING_SHIPMENT"}
    )
    shipped_count = sum(
        1 for order in orders
        if str(order.get("orderStatus") or order.get("fulfillmentStatus") or "") == "SHIPPED"
    )
    parts: list[str] = []
    for order in orders:
        order_no_text = str(order.get("orderNo") or "-")
        status_raw = str(order.get("orderStatus") or order.get("fulfillmentStatus") or "").upper()
        status = status_label.get(status_raw, status_raw or "未知状态")
        item_summary = str(order.get("itemSummary") or "").strip() or "商品"
        buyer_name = str(order.get("buyerName") or order.get("buyerUserId") or "买家")
        total = order.get("totalAmount")
        total_text = f"¥{total:g}" if total is not None else ""
        part = f"订单 {order_no_text}（{item_summary}，买家 {buyer_name}）{total_text} 状态为{status}"
        if status == "待发货":
            paid_at = str(order.get("paidAt") or "").replace("T", " ")[:16]
            logistics_no = str(order.get("logisticsNo") or "").strip()
            if logistics_no:
                part += f"，物流单号 {logistics_no} 已登记，等待买家签收"
            elif paid_at:
                part += f"，买家已于 {paid_at} 完成支付，请尽快安排发货"
            else:
                part += "，等待安排发货"
        elif status == "已发货":
            shipped_at = str(order.get("shippedAt") or "").replace("T", " ")[:16]
            logistics_no = str(order.get("logisticsNo") or "").strip()
            part += f"，已于 {shipped_at} 发货" if shipped_at else "，已发货"
            if logistics_no:
                part += f"（物流单号 {logistics_no}）"
            part += "，等待买家签收"
        elif status == "已签收":
            delivered_at = str(order.get("deliveredAt") or "").replace("T", " ")[:16]
            part += f"，已于 {delivered_at} 签收" if delivered_at else "，买家已签收"
        parts.append(part)
    pending_text = f"其中 {pending_ship_count} 笔待发货，请尽快安排发货" if pending_ship_count else "没有待发货的订单"
    shipped_text = f"；{shipped_count} 笔运输中" if shipped_count else ""
    summary = f"你当前共有 {len(orders)} 笔卖出订单：{'；'.join(parts)}。{pending_text}{shipped_text}。"
    return orders, ToolCallTrace(
        tool_name="query_seller_orders",
        arguments={
            "seller_user_id": seller_user_id,
            "fact_source": orders[0].get("_fact_source", "business_api"),
            "order_count": len(orders),
        },
        output_summary=summary,
        status="success",
        risk_level="low",
        next_action="answer_user",
    )


def get_cart_items(runtime_user_id: str) -> tuple[dict[str, Any] | None, ToolCallTrace]:
    """读取当前用户购物车实时事实。数据必须来自业务后端购物车，不能凭话术编造。

    购物车由用户在商城的加购/改量/勾选操作写入业务后端，客服回答时引用这份真实数据。
    """
    arguments = {"cart_owner_user_id": runtime_user_id}
    cart = cart_from_ecommerce(runtime_user_id)
    if cart is None:
        return None, ToolCallTrace(
            tool_name="get_cart_items",
            arguments=arguments,
            output_summary="业务后端暂未返回你的购物车数据，本轮不会猜测购物车内容，建议稍后重试或转人工客服。",
            status="error",
            risk_level="medium",
            next_action="transfer_to_human",
            error_type="business_api_unavailable",
        )
    items = [item for item in cart.get("items") if isinstance(item, dict)]
    if not items:
        return cart, ToolCallTrace(
            tool_name="get_cart_items",
            arguments={**arguments, "fact_source": cart.get("_fact_source", "business_api")},
            output_summary="你当前购物车是空的，还没有添加任何商品。",
            status="success",
            risk_level="low",
            next_action="answer_user",
        )
    parts: list[str] = []
    for item in items:
        name = str(item.get("productName") or item.get("name") or "商品")
        quantity = item.get("quantity") or 0
        unit_price = item.get("promotionPrice") if item.get("promotionPrice") is not None else item.get("unitPrice")
        promotion = str(item.get("promotionName") or "").strip()
        part = f"{name}×{quantity}"
        if unit_price is not None:
            part += f"（单价{unit_price}元）"
        if promotion:
            part += f"，已参与活动「{promotion}」"
        parts.append(part)
    selected_count = cart.get("selectedItemCount") or 0
    selected_total = cart.get("selectedTotalAmount")
    total_text = ""
    if selected_count and selected_total is not None:
        total_text = f"，已勾选 {selected_count} 件合计 {selected_total} 元"
    summary = f"你当前购物车共 {len(items)} 种商品：{'；'.join(parts)}{total_text}。"
    return cart, ToolCallTrace(
        tool_name="get_cart_items",
        arguments={
            **arguments,
            "fact_source": cart.get("_fact_source", "business_api"),
            "item_kind_count": len(items),
        },
        output_summary=summary,
        status="success",
        risk_level="low",
        next_action="answer_user",
    )
