"""小黄鱼二手电商交易平台业务后端集成层。项目代码通过这里获取实时订单事实。"""

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

from config.settings import ecommerce_base_url, load_app_env

SEED_ORDER_MIRRORS: dict[str, dict[str, Any]] = {
    "SO20260601090000008-a1000008": {
        "orderNo": "SO20260601090000008-a1000008",
        "userId": "U1001",
        "paymentStatus": "PAID",
        "fulfillmentStatus": "PAID_PENDING_SHIPMENT",
        "items": [{"productName": "降噪蓝牙耳机"}],
    },
    "SO20260602103000009-a1000009": {
        "orderNo": "SO20260602103000009-a1000009",
        "userId": "U1001",
        "paymentStatus": "PAID",
        "fulfillmentStatus": "SHIPPED",
        "logisticsStatus": "IN_TRANSIT",
        "items": [{"productName": "65W GaN 快充充电器"}],
    },
    "SO20260712090000010-a1000010": {
        "orderNo": "SO20260712090000010-a1000010",
        "userId": "U1001",
        "paymentStatus": "PAID",
        "fulfillmentStatus": "DELIVERED",
        "logisticsStatus": "SIGNED",
        "deliveredAt": "2026-07-12T09:00:00",
        "returnable": True,
        "items": [{"productName": "降噪蓝牙耳机", "returnable": True}],
    },
}

SEED_PRODUCT_MIRRORS: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "降噪蓝牙耳机",
        "code": "SKU-AUD-101",
        "category": "消费电子",
        "price": 599.0,
        "stock": 520,
        "active": True,
        "returnable": True,
        "highlights": "通勤首选；支持快充；参加会员满减活动",
        "promotion": {
            "promotionName": "消费电子活动会场",
            "promotionType": "member_discount",
            "discountSummary": "耳机、音箱和快充配件进入 618 消费电子会场，活动价和会员条件以结算页为准。",
            "promotionPrice": 529.0,
            "requiredMemberLevel": "gold",
            "conditionSummary": "金卡会员专享",
        },
    }
]

SEED_AFTER_SALE_MIRRORS: dict[str, list[dict[str, Any]]] = {
    "SO20260602103000009-a1000009": [
        {
            "requestId": "AS-STORY-REFUND-0009",
            "orderNo": "SO20260602103000009-a1000009",
            "userId": "U1001",
            "requestType": "REFUND",
            "status": "REVIEWING",
        }
    ]
}


def seed_mirror_enabled() -> bool:
    """业务种子镜像只供显式离线测试使用，不能掩盖在线接口故障。"""
    return os.getenv("AGENT_OFFLINE_FACTS") == "1"


def _with_fact_source(value: dict[str, Any], source: str) -> dict[str, Any]:
    return {**value, "_fact_source": source}

def delegated_service_headers(current_user_id: str | None) -> dict[str, str]:
    """构造已认证 Agent 服务身份头：token 是认证必需，user_id 仅作为可选的代理用户信息。

    业务后端 AgentServiceAuthenticationFilter 只校验 token 即授予 AGENT_SERVICE 角色，
    因此不带 user_id 的通用只读查询（如商城在售商品列表）也可以带上 token 访问。
    """
    load_app_env()
    user_id = str(current_user_id or "").strip()
    token = os.getenv(
        "AGENT_ECOMMERCE_SERVICE_TOKEN",
        os.getenv("AGENT_SERVICE_AUTH_TOKEN", "debug-agent-service"),
    ).strip()
    if not token:
        return {}
    headers = {"X-Agent-Service-Token": token}
    if user_id:
        headers["X-Agent-User-Id"] = user_id
    return headers

def ecommerce_get(
    path: str,
    *,
    delegated_user_id: str | None = None,
) -> dict[str, Any] | list[Any] | None:
    """封装业务后端 GET 调用，统一处理响应结构和错误边界。"""
    # 业务后端通常运行在 localhost；不继承宿主机 HTTP 代理，避免本地请求被代理劫持。
    with httpx.Client(timeout=5, trust_env=False) as client:
        response = client.get(
            f"{ecommerce_base_url()}{path}",
            headers=delegated_service_headers(delegated_user_id),
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return None
    return payload.get("data")

def order_fact_from_ecommerce(target_order_no: str, current_user_id: str) -> dict[str, Any] | None:
    """从业务后端读取订单事实；失败时返回空值，交给 Agent 降级或转人工。"""
    try:
        order = ecommerce_get(f"/api/orders/{target_order_no}", delegated_user_id=current_user_id)
    except Exception:
        order = None
    if isinstance(order, dict):
        enriched = _with_fact_source(order, "business_api")
        items = enriched.get("items") or []
        product_facts: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict) or item.get("productId") is None:
                product_facts.append(item)
                continue
            try:
                product = ecommerce_get(f"/api/products/{item['productId']}")
            except Exception:
                product = None
            product_facts.append({**item, "returnable": product.get("returnable")} if isinstance(product, dict) else item)
        if product_facts:
            enriched["items"] = product_facts
            returnability = [item.get("returnable") for item in product_facts if isinstance(item, dict)]
            if returnability and all(value is True for value in returnability):
                enriched["returnable"] = True
            elif any(value is False for value in returnability):
                enriched["returnable"] = False
            else:
                enriched["returnable"] = None
        return enriched
    if not seed_mirror_enabled():
        return None
    mirror = SEED_ORDER_MIRRORS.get(target_order_no)
    return _with_fact_source(mirror, "seed_mirror") if mirror else None


def products_from_ecommerce(keyword: str) -> list[dict[str, Any]]:
    """查询实时商品事实；离线演练只回退到固定、可识别的商品样例。"""
    try:
        query_keyword = product_query_keyword(keyword)
        products = ecommerce_get(f"/api/products?{httpx.QueryParams({'keyword': query_keyword})}")
    except Exception:
        products = None
    if isinstance(products, list):
        return [_with_fact_source(item, "business_api") for item in products if isinstance(item, dict)]
    if not seed_mirror_enabled():
        return []
    normalized = keyword.replace(" ", "")
    return [
        _with_fact_source(item, "seed_mirror")
        for item in SEED_PRODUCT_MIRRORS
        if any(term in normalized for term in ("耳机", "降噪", "通勤")) and "耳机" in str(item.get("name"))
    ]


def product_query_keyword(user_message: str) -> str:
    """把自然语言商品咨询收窄成业务后端可搜索的关键词。"""
    for term in ("降噪蓝牙耳机", "降噪耳机", "耳机", "音箱", "充电器", "投影仪", "键盘"):
        if term in user_message:
            return "降噪" if term in {"降噪蓝牙耳机", "降噪耳机"} else term
    return user_message.strip()


# 推荐需求里的"用户说的词" → "商城可搜索关键词"。长词在前，避免"运动相机"被"相机"抢先命中。
RECOMMEND_CATEGORY_TERMS: dict[str, str] = {
    "扫地机器人": "扫地机器人", "运动相机": "运动相机", "空气净化器": "空气净化器",
    "指纹门锁": "指纹门锁", "指纹锁": "指纹门锁", "露营便携电源": "露营便携电源",
    "咖啡机": "咖啡机", "显示器": "显示器", "充电器": "充电器", "充电头": "充电器",
    "自行车": "自行车", "骑行": "自行车", "单车": "自行车", "耳机": "耳机",
    "蓝牙": "蓝牙", "降噪": "降噪", "音箱": "音箱", "音响": "音箱",
    "手机": "手机", "平板": "平板", "电脑": "游戏本", "笔记本": "游戏本", "游戏本": "游戏本",
    "键盘": "键盘", "屏幕": "显示器", "相机": "相机", "摄影": "相机", "拍照": "相机",
    "手表": "手表", "手环": "手表", "空调": "空调", "吹风机": "吹风机",
    "咖啡": "咖啡机", "扫地": "扫地机器人", "机器人": "扫地机器人",
    "净化": "空气净化器", "净化器": "空气净化器", "门锁": "指纹门锁",
    "电源": "露营便携电源", "露营": "露营便携电源", "户外电源": "露营便携电源",
    "运动": "运动相机",
    # 场景/用途词 → 商城可搜词：送女生类场景映射到个护类在售商品（礼物场景典型选择）。
    "送女生": "吹风机", "送女朋友": "吹风机", "送女友": "吹风机", "送闺蜜": "吹风机",
}

_REC_BUDGET_UNITS: dict[str, float] = {"万": 10000.0, "千": 1000.0, "百": 100.0}
_REC_CHINESE_NUM: dict[str, float] = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}

# 推荐排除约束："不要电子产品/别推荐数码的/除了耳机/不含家电"等。
_EXCLUDE_RE = re.compile(
    r"(?:不要|别|不想|不用|排除|拒绝|去掉|删掉|除了|除去|不含|不带|不是)\s*([\u4e00-\u9fa5A-Za-z0-9]+)"
)

# 用户说的排除名词 → 商城类别（用于"不要电子产品"这类排除整类）。
_EXCLUDE_CATEGORY_MAP: dict[tuple[str, ...], str] = {
    ("电子", "数码"): "数码闲置",
    ("家居",): "家居闲置",
    ("家电", "电器"): "家电闲置",
    ("个护",): "个护闲置",
    ("户外",): "户外闲置",
    ("二手", "闲置"): "二手闲置",
}

# 排除类别时的语义兜底：商城类目与用户直觉不完全一致（如 4K 运动相机被归在"户外闲置"），
# 排除"数码闲置"时名称/描述明显属于数码类的商品也要剔除，不能把相机/耳机/手机这类再推荐出来。
_EXCLUDE_CATEGORY_SEMANTICS: dict[str, tuple[str, ...]] = {
    "数码闲置": ("相机", "运动相机", "耳机", "手机", "平板", "电脑", "笔记本", "游戏本", "键盘", "音箱", "音响", "蓝牙", "充电", "充电器", "显示器", "屏幕"),
    "家电闲置": ("空调", "冰箱", "洗衣机", "电视", "咖啡机", "净化器", "吹风机", "电饭煲", "微波炉"),
}


def recommend_keyword(user_message: str) -> str:
    """把推荐需求收窄成商城可搜索的关键词（类别/用途/商品词）；抽不到返回空串表示推荐全部在售。

    先剔除"不要/除了/别 + 名词"排除短语，避免把排除对象（如"除了耳机"里的耳机）误当正向需求。
    """
    text = _EXCLUDE_RE.sub("", user_message)
    for term, keyword in RECOMMEND_CATEGORY_TERMS.items():
        if term in text:
            return keyword
    return ""


def recommend_excludes(user_message: str) -> list[dict[str, str]]:
    """从"不要电子产品/别推荐数码的/除了耳机"等表述中抽取排除约束。

    返回 [{"type": "category", "value": "数码闲置"}] 表示排除整个类别，
    或 [{"type": "keyword", "value": "耳机"}] 表示排除名称/描述含该词的单个品类。
    抽不到排除约束返回空列表。
    """
    excludes: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _EXCLUDE_RE.finditer(user_message):
        noun = match.group(1).rstrip("的东西们类的啦呢").strip()
        if not noun:
            continue
        # 按映射定义顺序收集命中的类别，保证话术输出顺序稳定。
        categories_found: list[str] = []
        for keys, value in _EXCLUDE_CATEGORY_MAP.items():
            if any(key in noun for key in keys) and value not in categories_found:
                categories_found.append(value)
        if categories_found:
            for category in categories_found:
                if ("category", category) not in seen:
                    excludes.append({"type": "category", "value": category})
                    seen.add(("category", category))
            continue
        for term, keyword in RECOMMEND_CATEGORY_TERMS.items():
            if term in noun:
                if ("keyword", keyword) not in seen:
                    excludes.append({"type": "keyword", "value": keyword})
                    seen.add(("keyword", keyword))
                break
    return excludes


def recommend_budget(user_message: str) -> float | None:
    """从"预算 500 元以内 / 一千块 / 预算一万"等表述中抽取金额上限；抽不到返回 None。"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块钱|块|以内|以下)", user_message)
    if m:
        return float(m.group(1))
    m = re.search(r"预算\s*(\d+(?:\.\d+)?)", user_message)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*[kK]", user_message)
    if m:
        return float(m.group(1)) * 1000.0
    m = re.search(r"([一二两三四五六七八九]+)([万千百])\s*(?:以内|以下|元|块钱|块)?", user_message)
    if m and m.group(1) in _REC_CHINESE_NUM:
        return float(_REC_CHINESE_NUM[m.group(1)] * _REC_BUDGET_UNITS[m.group(2)])
    return None


# 无数字成色表述 → 成色下限数值（全新/充新=10，九五新=9.5 等）。
_REC_CONDITION_WORDS: dict[str, float] = {
    "全新": 10.0, "充新": 10.0, "未拆封": 10.0, "没用过": 10.0,
    "九五新": 9.5, "95新": 9.5, "9.5成新": 9.5,
    "九成新": 9.0, "9成新": 9.0,
}


def recommend_condition(user_message: str) -> float | None:
    """从"成色九成新以上/要9成新以上的/九五新/全新"等表述中抽取成色下限；抽不到返回 None。

    返回数值表示成色下限（9.0=九成新，9.5=九五新，10=全新），"以上/及以上/往上/起步"均为 >= 语义。
    推荐过滤时要求商品明确标注成色且数值不低于该下限，未标注成色的商品不参与此类推荐。
    """
    for text, score in sorted(_REC_CONDITION_WORDS.items(), key=lambda kv: len(kv[0]), reverse=True):
        if text in user_message:
            return score
    m = re.search(r"(\d+(?:\.\d+)?)\s*成新", user_message)
    if m:
        return float(m.group(1))
    m = re.search(r"([一二两三四五六七八九]+)\s*成", user_message)
    if m and m.group(1) in _REC_CHINESE_NUM:
        return float(_REC_CHINESE_NUM[m.group(1)])
    return None


def recommend_products_from_ecommerce(keyword: str) -> list[dict[str, Any]] | None:
    """读取商城在售商品（GET /api/shop/products，只含已上架可购买商品）用于推荐；None 表示接口不可用。"""
    try:
        if keyword:
            products = ecommerce_get(f"/api/shop/products?{httpx.QueryParams({'keyword': keyword})}")
        else:
            products = ecommerce_get("/api/shop/products")
    except Exception:
        products = None
    if isinstance(products, list):
        return [_with_fact_source(item, "business_api") for item in products if isinstance(item, dict)]
    return None


def seller_products_from_ecommerce(seller_user_id: str) -> list[dict[str, Any]] | None:
    """查询卖家自己发布的商品及售卖状态；None 表示业务接口不可用。"""
    if not seller_user_id:
        return None
    try:
        products = ecommerce_get(
            f"/api/shop/seller/products?{httpx.QueryParams({'sellerId': seller_user_id})}",
            delegated_user_id=seller_user_id,
        )
    except Exception:
        products = None
    if isinstance(products, list):
        return [_with_fact_source(item, "business_api") for item in products if isinstance(item, dict)]
    return None


def seller_orders_from_ecommerce(seller_user_id: str, status: str | None = None) -> list[dict[str, Any]] | None:
    """查询卖家的卖出订单（买家购买该卖家商品的订单）；None 表示业务接口不可用。

    卖出订单由买家在商城的购买支付实时写入业务后端，卖家侧与 Agent 查询都读取这份真实数据。
    """
    if not seller_user_id:
        return None
    try:
        params = httpx.QueryParams({"sellerId": seller_user_id})
        if status:
            params = params.set("status", status)
        orders = ecommerce_get(f"/api/shop/seller/orders?{params}", delegated_user_id=seller_user_id)
    except Exception:
        orders = None
    if isinstance(orders, list):
        return [_with_fact_source(item, "business_api") for item in orders if isinstance(item, dict)]
    return None


def user_orders_from_ecommerce(user_id: str) -> list[dict[str, Any]] | None:
    """查询用户订单列表（按创建时间倒序）；None 表示业务接口不可用。"""
    if not user_id:
        return None
    try:
        orders = ecommerce_get(
            f"/api/shop/orders/by-user?{httpx.QueryParams({'userId': user_id})}",
            delegated_user_id=user_id,
        )
    except Exception:
        orders = None
    if isinstance(orders, list):
        return [_with_fact_source(item, "business_api") for item in orders if isinstance(item, dict)]
    return None


def cart_from_ecommerce(user_id: str) -> dict[str, Any] | None:
    """读取当前用户购物车实时事实；None 表示业务接口不可用。

    购物车由用户在商城的加购/改量/勾选操作写入业务后端（:8081），
    客服查询时读取的必须是这份真实数据，禁止编造购物车内容。
    """
    if not user_id:
        return None
    try:
        cart = ecommerce_get("/api/shop/cart", delegated_user_id=user_id)
    except Exception:
        cart = None
    if isinstance(cart, dict) and isinstance(cart.get("items"), list):
        return _with_fact_source(cart, "business_api")
    return None


def after_sale_requests_from_ecommerce(order_id: str, current_user_id: str) -> list[dict[str, Any]] | None:
    """按订单号读取售后进度；None 表示业务接口不可用，空列表表示确认没有记录。"""
    try:
        order = order_fact_from_ecommerce(order_id, current_user_id)
        if not current_user_id:
            raise ValueError("missing_current_user_id")
        requests = ecommerce_get(
            f"/api/after-sale/requests?orderNo={order_id}",
            delegated_user_id=current_user_id,
        )
    except Exception:
        requests = None
    if isinstance(requests, list):
        return [_with_fact_source(item, "business_api") for item in requests if isinstance(item, dict)]
    if not seed_mirror_enabled():
        return None
    return [_with_fact_source(item, "seed_mirror") for item in SEED_AFTER_SALE_MIRRORS.get(order_id, [])]
