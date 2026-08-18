"""轻量规划工具。这里展示轻量路由、订单号抽取和 token 估算。"""

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
from integrations.ecommerce_client import RECOMMEND_CATEGORY_TERMS, _EXCLUDE_CATEGORY_MAP, _EXCLUDE_RE, recommend_condition, user_orders_from_ecommerce

def classify_intent(user_message: str) -> Intent:
    """用可读规则做路由，展示 Tool、RAG、Workflow、降级、安全和新手二手交易咨询分流入口。"""
    if any(term in user_message for term in ["SERVICE_TIMEOUT", "服务抽风", "工具超时", "接口不可用", "故障注入"]):
        return "degradation_request"
    if any(term in user_message for term in ["系统提示词", "hidden reasoning", "隐藏推理", "工具 schema", "内部策略"]):
        return "security_request"
    # 履约担忧/售后物流咨询（催发货、不发货、被骗担忧、退货物流）优先于退货动作与订单查询：
    # "卖家一直不发货，拖了我一星期了""钱都付了东西还没影，我是不是被骗了""我退的货卖家收到没有"
    # 这类问题要的是"怎么办"的规则指引，不是订单列表澄清，更不是进入退货 Workflow。
    if _is_fulfillment_consult_query(user_message):
        return "fulfillment_consult_query"
    # 退货/退换/退的货口语咨询：含咨询句型的走买家服务话术，明确退货动作才进入退货 Workflow。
    # "还能退嘛/能退不/可以退吗"等同属咨询（buyer_service_query），由 agent 层在订单上下文
    # 中复用已解析订单给出结合状态的退货判断；"我要退/申请退货"才是退货动作（return_request）。
    if any(term in user_message for term in ["退货", "七天无理由", "退换", "不想要了", "想退", "能退吗", "还能退", "能退不", "可退吗", "怎么退", "退的货", "退回去", "退货物流", "我要退"]):
        if any(term in user_message for term in ("吗", "嘛", "能不能", "可以", "支持", "规则", "多久", "怎么", "流程", "不想要", "想退", "能退", "能换", "还能退")):
            return "buyer_service_query"
        return "return_request"
    if "退款" in user_message and any(term in user_message for term in ["进度", "状态", "处理到哪", "什么时候到账"]):
        return "refund_status_query"
    if any(term in user_message for term in ["退款", "退钱", "取消订单"]):
        # 卖家举报买家恶意退款/掉包，或买家拍下后砍价不履约，属于纠纷维权，不是发起退款动作。
        if any(term in user_message for term in ("恶意退款", "掉包", "到手刀")) or (
            "买家" in user_message and any(term in user_message for term in ("砍价", "降价", "还价", "私下降价", "不愿意", "不同意", "改价"))
        ):
            return "dispute_query"
        # 卖家视角：描述"买家要退款/申请退款"是卖家在处理售后申请，不是买家本人发起退款。
        if "买家" in user_message and any(term in user_message for term in ("要退款", "申请退款", "退款申请", "要求退款", "要退")):
            return "seller_service_query"
        return "refund_request"
    if _is_seller_products_query(user_message):
        # 卖家查询自己商品的售卖状态（"已经卖掉的有哪些""在售的有哪些""有没有待审核的"）
        # 必须走业务工具读真实数据，先于卖家规则咨询/商品搜索判定，避免被"审核""商品"等
        # 泛词抢走变成话术咨询或商品搜索。
        return "seller_products_query"
    if _is_seller_service_consult(user_message):
        # 卖家规则/操作咨询（取消、审核时效、货款到账、下架、改价、确认收货等）先于卖出订单/商品
        # 查询判定，避免"买家拍下了不付款，我能取消吗"被当成订单列表查询、"价格改高一点"被当成
        # 商品查询而答非所问。
        return "seller_service_query"
    if _is_seller_orders_query(user_message):
        # 卖家查询卖出订单（买家购买了自己商品的订单）必须走业务工具读真实履约状态，
        # 先于通用"订单"查询判定，避免"我卖出的订单/买家下的单/待发货的订单"被当成买家订单查询。
        return "seller_orders_query"
    # 签收/验货流程咨询优先于订单查询："快递员让我先签收再验货"是流程咨询，不该去查订单。
    if any(term in user_message for term in ("签收", "验货", "当面验", "先签", "再验")) and any(term in user_message for term in ("正常吗", "可以", "应该", "流程", "先", "再", "吗", "怎么办")):
        return "buyer_service_query"
    # 纠纷/质量争议（说坏、坏了、货不对板、假货等）优先于订单查询，避免"买家说坏了要退"被当成订单查询。
    if _is_dispute_query(user_message):
        return "dispute_query"
    if any(term in user_message for term in ["订单", "物流", "快递", "发货", "到货", "到哪", "到哪了", "到哪儿", "货在哪", "寄到哪", "到没到"]):
        return "order_query"
    if "发票" in user_message:
        return "faq_query"
    if any(term in user_message for term in ["火星会员", "隐藏券", "不存在的活动", "未知活动"]):
        return "low_confidence_query"
    # 二手交易平台规则、纠纷维权、风险防控类咨询优先于商品与活动查询，
    # 保证标准话术在同时命中业务关键词时仍然生效。
    if _is_risk_prevention_query(user_message):
        return "risk_prevention_query"
    if _is_prohibited_goods_query(user_message):
        # "能不能卖 XX" 类禁售品咨询直接走平台通用规则，命中禁售品类时用知识库话术判断违规。
        return "platform_rule_query"
    if _is_platform_rule_query(user_message):
        return "platform_rule_query"
    if _is_recommend_query(user_message):
        # 商品推荐优先于买家服务咨询："推荐一些成色九成新以上的商品"含"成色"（买家服务词表），
        # 但用户要的是真实商品推荐，必须走商城在售库，不能降级成平台规则话术。
        return "recommend_products"
    if _is_buyer_service_query(user_message):
        return "buyer_service_query"
    if _is_seller_service_query(user_message):
        return "seller_service_query"
    if any(term in user_message for term in ["活动", "满减", "会员券", "优惠券", "会员规则", "大促", "会员", "金卡", "银卡", "会员折扣"]):
        if any(term in user_message for term in ["商品", "耳机", "音箱", "库存", "价格", "多少钱", "有货"]):
            return "product_query"
        return "promotion_query"
    # 购物车查询优先于普通商品查询：问"购物车里有什么/合计多少钱"时读的是用户真实加购记录。
    if any(term in user_message for term in ["购物车", "购物车里", "加购了", "我加购的", "购物车有", "购物车里有", "购物车合计", "购物车一共", "购物车多少钱", "购物车总价"]):
        return "cart_query"
    # 兼容性/适配性咨询（"我买的充电器能给我的iPhone用吗"）查商城商品真实信息。
    if _is_compatibility_query(user_message):
        return "product_query"
    if any(term in user_message for term in ["商品", "耳机", "音箱", "库存", "价格", "多少钱", "有货"]):
        return "product_query"
    return "general_chat"


_FULFILLMENT_CONCERN_TERMS = (
    # 催发货/不发货投诉
    "不发货", "还没发货", "还不发货", "一直不发货", "怎么还不发", "催发货", "催促发货",
    "拖了", "拖我", "拖着", "拖了一星期", "拖了一个星期", "拖了很久",
    "没动静", "没影", "没消息", "没下落",
    # 被骗担忧（担保交易安抚）
    "被骗", "遇到骗子", "是骗子", "是不是骗", "怕被骗", "会不会是骗", "骗人的", "骗钱的",
    # 钱付了没收到货
    "钱都付了", "付了钱", "钱付了", "款都付了", "钱都给了",
    # 退货物流/退货状态咨询
    "退的货", "退货的", "退回去", "退货物流", "退货到", "退件", "退回的", "寄回去的",
)


def _is_fulfillment_consult_query(user_message: str) -> bool:
    """识别履约担忧/售后物流咨询：催发货、不发货、拖发货、担心被骗、退货物流状态等。

    这类问题要的是"怎么办"的规则指引（联系卖家/催促发货/平台介入/担保交易安抚/查询退货物流），
    不是订单列表澄清。明确表达物流位置查询（"东西到哪了/货到哪了"）仍需订单号才能查明细，
    交给 order_query 澄清，不能误判为履约咨询；卖家视角的"买家拖着不收货/不付款"也不是
    买家履约担忧，交给卖家服务分支（排除"买家/对方 + 收货/确认/付款"表述）。
    """
    # 退货物流语境（"我退的货到哪了/退回去的东西到哪了"）优先于位置词排除，
    # 这类仍是退货物流咨询；其余"到哪了"位置查询交给 order_query 澄清订单。
    if not any(term in user_message for term in ("退的货", "退货的", "退回去", "退件", "退回的", "寄回去的")):
        if any(term in user_message for term in ("到哪", "到哪儿", "到哪了", "到那", "物流到", "快递到", "货在哪", "货到没", "到没到", "寄到哪")):
            return False
    if any(term in user_message for term in ("买家", "对方", "客户", "别人")) and any(
        term in user_message for term in ("收货", "确认", "签收", "付款", "不付款", "提货", "取货")
    ):
        return False
    # 用户明确要执行退货/退款动作（"我要退/想退/申请退款"）时不拦截，交给退货 Workflow 或退款分支；
    # 履约咨询只接住"担忧/投诉/状态追问"类表述（退的货到哪了、卖家收到没有等）。
    if any(term in user_message for term in ("我要退", "想退", "申请退", "怎么退", "能退吗", "退款", "退货流程", "退货可以", "退货吧", "帮忙退")):
        return False
    return any(term in user_message for term in _FULFILLMENT_CONCERN_TERMS)


def _is_seller_service_consult(user_message: str) -> bool:
    """识别卖家规则/操作咨询：取消订单、审核时效、货款到账、下架、改价、确认收货、侵权、曝光限流等。

    与卖出订单/商品销售等查询类区分：含"有哪些/几单/卖出没/卖出去了"等查询句型时不判定为咨询，
    避免"我卖出的订单有哪些""我上架的耳机卖出去了吗"这类真实查询被咨询话术吞掉；
    买家视角表述（我买的/什么时候发货等催货说法）也不是卖家服务咨询，交给订单/商品链路。
    """
    if any(term in user_message for term in (
        "我买的", "我买", "我拍的", "我拍下", "我下单", "我付", "我收到", "我收了",
        "我的订单", "我的快递", "我的物流", "东西到了", "到货了", "什么时候发货", "怎么还不发货", "还不发货",
    )):
        return False
    # 买家视角的同城自提/当面交易（"同城自提怎么交易""要不要当面确认收货"）不是卖家操作咨询，
    # 交给买家服务/风险指引分支，避免"确认收货"等词被卖家咨询吞掉。
    if any(term in user_message for term in ("同城自提", "自提", "当面", "面交", "提货", "取货")):
        return False
    if any(term in user_message for term in ("有哪些", "有几个", "几单", "几件", "卖出没", "卖出去了", "查一下")):
        return False
    topic_terms = (
        "取消", "关闭订单", "下架", "审核", "到账", "提现", "货款", "改价", "调价", "改高", "改低", "价格改",
        "确认收货", "点确认", "不点确认", "自动确认",
        "运费", "邮费", "违规", "处罚", "保证金", "曝光", "限流", "流量", "封号", "扣分", "侵权",
        "发货时间", "什么时候发", "不付款", "拍下不买", "违约",
        # 发货前准备/留证咨询（"发货前我要注意什么""怎么留证据""要不要打包视频"）
        "发货前", "发货时", "发货注意", "打包", "快递面单", "面单", "留证据", "留证", "留好证据", "留凭证",
        # 买家拍下/咨询后不下单的口语说法："有人问但一直不下单""拍了又不付款"
        "不下单", "拍下不", "拍了不", "一直不买", "只看不买",
    )
    return any(term in user_message for term in topic_terms)


def follow_up_intent_from_history(user_message: str, recent_intent: str | None) -> Intent | None:
    """多轮对话承接：当前消息没有独立意图时，沿用本会话最近一次意图。

    例如上一轮在咨询验货宝，本轮"那我需要准备什么"没有词表可命中，
    返回上一轮 buyer_service_query，让 Agent 继续按该话题回答。
    """
    if classify_intent(user_message) != "general_chat":
        return None
    if recent_intent and recent_intent != "general_chat" and recent_intent != "unknown":
        return recent_intent  # type: ignore[return-value]
    return None


def history_context_hint(history_messages: list[Any]) -> str:
    """从最近一轮对话中抽取承接上下文：优先取上一轮 Agent 回答的主题句。

    用于给意图检索补充语境，让"那我需要准备什么"这类省略式追问能召回上一话题文档。
    """
    for item in reversed(history_messages):
        if getattr(item, "role", None) == "assistant":
            content = getattr(item, "content", "") or ""
            return content[:60].strip()
    return ""


def _is_buyer_authenticity_concern(user_message: str) -> bool:
    """买家真伪/假货担忧："这手机不会是高仿的吧""是不是假的""怕买到假货"。

    这类是买家下单前的真伪疑虑，应走买家服务（真伪成色话术），
    而不是平台禁售规则（"能不能卖高仿"才是规则咨询，由禁售分支单独处理）。
    以怀疑语气（不会是/是不是/怕/担心/怀疑）为前提，避免把卖家"卖高仿会被封吗"
    这类规则咨询误判成买家担忧。
    """
    if not any(term in user_message for term in (
        "不会是", "该不会是", "是不是", "是假的", "怕是", "怕买到", "担心", "有点怕", "怀疑", "好慌", "不太放心"
    )):
        return False
    return any(term in user_message for term in ("高仿", "山寨", "假货", "仿品", "盗版", "真伪", "正品"))


def _is_compatibility_query(user_message: str) -> bool:
    """识别商品兼容性/适配性咨询："我买的充电器能给我的iPhone用吗""这耳机能连电脑吗"。

    用户要的是商品真实规格与兼容性事实（接口/型号/参数），应查商城商品信息，
    而不是平台规则话术，更不能落到 general_chat 兜底。
    排除词保证"能不能退/能不能卖"等规则问句不被吞（那些由更靠前的分支处理）。
    """
    if not any(term in user_message for term in (
        "能不能用", "能用吗", "能给我的", "能不能给我的", "能连", "能接", "兼容", "适配", "配不配", "适不适合", "可以用吗"
    )):
        return False
    if any(term in user_message for term in ("退", "卖", "退货", "退款", "优惠", "规则", "客服")):
        return False
    return True


def _is_platform_rule_query(user_message: str) -> bool:
    """识别平台通用规则类咨询：担保交易、禁售商品、客服职责、信用分、资金冻结、账号处罚。"""
    # 买家真伪担忧（"这手机不会是高仿的吧"）不是平台规则咨询，交给买家服务话术。
    if _is_buyer_authenticity_concern(user_message):
        return False
    platform_terms = [
        "担保交易", "资金托管", "站内交易", "私下转账", "交易保障",
        "禁售", "禁售品", "高仿", "盗版", "票务", "卡券", "活体宠物",
        "客服职责", "客服负责", "官方客服", "客服能", "客服会", "客服可以", "客服是否",
        "信用分", "信誉分",
        "资金冻结", "冻结原因", "被冻结", "风控",
        "处罚申诉", "账号处罚", "限制功能",
        "货款到账", "解冻", "货款",
    ]
    if any(term in user_message for term in platform_terms):
        return True
    # 担保交易资金场景："确认收货"同时出现在同城自提场景（走买家服务），
    # 只有叠加资金/账期语境才判定为担保资金规则咨询。
    if "确认收货" in user_message and any(
        term in user_message for term in ("到账", "货款", "卖家", "账上", "解冻", "提现", "什么时候")
    ):
        return True
    return False


def _is_buyer_service_query(user_message: str) -> bool:
    """识别买家侧交易咨询：真伪成色、议价砍价、验货宝、同城自提、退换货基础、空包裹。"""
    # 卖家视角的砍价/议价（"有人拍了我东西又来砍价""买家来砍价""客户一直砍价"）不是买家服务咨询，
    # 而是到手刀/恶意砍价类纠纷（可检索"买家到手刀与恶意砍价维权"文档），必须交给 dispute 分支，
    # 否则买家话术会答非所问（把卖家当买家）。买家自称砍价（"能砍价吗""便宜点"）仍走买家服务。
    if any(term in user_message for term in ("有人", "别人", "对方", "买家", "客户", "客人")) and any(
        term in user_message for term in ("砍价", "议价", "还价", "降价", "砍")
    ):
        return False
    buyer_terms = [
        "真伪", "成色", "正品", "实拍图", "功能测试", "高仿", "仿品",
        "议价", "砍价", "还价", "便宜", "降价",
        "验货宝", "验机", "鉴定", "第三方鉴定",
        "同城自提", "自提", "当面", "面交",
        "七天无理由退换", "退换", "无理由退",
        "空包裹", "没收到货", "未收到货", "虚假发货",
    ]
    return any(term in user_message for term in buyer_terms)


def _is_seller_products_query(user_message: str) -> bool:
    """识别卖家查询自己商品售卖情况：我的商品卖出没有、卖出几件、售卖如何、在售/待审核/已售有哪些。"""
    seller_query_terms = [
        "我的商品", "我的货", "我发布", "我上架的",
        "卖出去", "卖出去了", "卖出没", "卖出多少", "卖出几件", "卖了几件",
        "卖得怎么", "售卖情况", "出了几单", "有没有人买", "卖得如何", "卖了几单",
        "卖掉", "卖掉的", "已卖", "已售", "已售出", "售出的", "卖了的", "出掉的",
        # 按状态问商品：在售/待审核/审核中（"审核"裸词留给卖家规则咨询的审核时效问题；
        # 不用"审核的"——"正在审核的商品"含该子串会把审核时效咨询误判成商品查询）
        "在售", "待审核", "审核中", "挂着卖",
    ]
    if not any(term in user_message for term in seller_query_terms):
        return False
    # 操作咨询类表述（改价/取消/下架/审核时效等"怎么办/要多久"）不是商品状态查询，
    # 且没有明确的查询句型时，让位给卖家规则咨询分支，避免"我发布的商品怎么改价"被抢。
    if any(term in user_message for term in (
        "改价", "调价", "改高", "改低", "价格改", "取消", "下架", "删除", "编辑", "修改",
        "怎么办", "如何", "怎么改", "怎么取消", "要多久", "需要多久", "为什么", "还没通过",
    )):
        if not any(term in user_message for term in (
            "有哪些", "有几件", "卖出没", "卖出去了", "卖掉", "卖掉了", "已卖", "已售",
            "在售", "待审核", "审核中", "卖了几件", "卖得怎么", "售卖情况", "出掉了",
        )):
            return False
    return True


def _is_seller_orders_query(user_message: str) -> bool:
    """识别卖家查询卖出订单：买家购买了自己商品的订单及其履约状态（待发货/运输中/已签收）。

    与 _is_seller_products_query 的分工：商品视角问"卖了几件/卖出没有"走商品查询，
    订单视角问"卖出订单/买家下的单/待发货的订单"走卖出订单查询。
    含买家视角表述（我买的/我收到的/我的订单）不判定为卖家卖出订单。
    """
    if any(hint in user_message for hint in ("我买的", "我收到的", "我的订单", "我下的单", "我的物流", "我的快递")):
        return False
    # 纠纷维权/售后表述（买家拍下后到手刀、退款纠纷等）不判定为卖出订单查询，交给后续纠纷分支。
    if any(term in user_message for term in ("到手刀", "恶意退款", "掉包", "仲裁", "举证", "申诉", "退款", "退货")):
        return False
    seller_order_terms = [
        "卖出订单", "卖出的订单", "卖的订单", "卖出单",
        "买家下的单", "买家下单", "买家拍下", "买家买的", "买家订单", "谁买了", "谁买的",
        "卖出几单", "卖了几单", "出了几单", "收到几单", "接到订单", "有没有订单", "有几单",
        "待发货的订单", "待发货订单", "要发货", "需要发货", "该发货", "去发货", "发货单",
        "卖出记录", "订单记录", "订单明细",
    ]
    return any(term in user_message for term in seller_order_terms)


def _is_recommend_query(user_message: str) -> bool:
    """识别商品推荐请求：用户要客服从商城在售库里推荐某类/某方面/某预算的商品。

    排除纠纷/售后/规则等处理类场景，避免"推荐个纠纷处理办法"误判为商品推荐；
    这些场景由更靠前的风险/纠纷/售后分支或一般对话兜底处理。
    同时识别"不要电子产品/别推荐数码的/除了耳机"这类纯排除约束：排除后仍推荐剩余在售。
    """
    if any(term in user_message for term in ("纠纷", "维权", "仲裁", "申诉", "退款", "退货", "售后", "客服", "规则", "举报", "怎么处理", "如何处理", "怎么办", "如何维权", "验货宝", "鉴定", "验机", "验货", "退换", "无理由")):
        return False
    recommend_terms = [
        "推荐", "求推荐", "有推荐", "安利", "种草", "帮我选", "帮我挑", "帮我买",
        "选一款", "挑一款", "选一个", "挑一个", "买什么", "入手什么", "有什么好",
        "性价比", "哪款", "哪个好", "几款",
        # 礼物/场景类软推荐："帮我看看有没有适合送女生的东西""有什么好物推荐"
        "帮我看看", "给我看看", "有没有适合", "有什么适合", "适合送", "适合买",
        "送女生", "送女朋友", "送女友", "送闺蜜", "送男友", "送男生", "送人", "好物",
    ]
    if any(term in user_message for term in recommend_terms):
        return True
    # 成色约束检索："有没有九五新的耳机/有没有九成新的手机"——带成色下限的商品检索走推荐链路，
    # 推荐工具会按成色过滤并如实告知无匹配，避免单商品查询返回不达成色要求的商品。
    if ("有没有" in user_message or "有没" in user_message) and recommend_condition(user_message) is not None:
        return True
    # 纯排除约束触发：如"不要电子产品"（无正向推荐词），抽取排除名词后按推荐处理。
    exclude_match = _EXCLUDE_RE.search(user_message)
    if exclude_match:
        noun = exclude_match.group(1)
        if any(key in noun for keys in _EXCLUDE_CATEGORY_MAP for key in keys):
            return True
        if any(term in noun for term in RECOMMEND_CATEGORY_TERMS):
            return True
    return False


def _is_seller_service_query(user_message: str) -> bool:
    """识别卖家侧交易咨询：发布规范、发货运费、到账、改价、违约、曝光。"""
    seller_terms = [
        "发布商品", "发布规范", "发布", "发布时", "上架", "下架", "违规关键词", "盗用图片",
        "打包视频", "快递面单", "运费", "发货前",
        "货款到账", "提现", "解冻打给", "确认收货后",
        "改价", "修改价格", "修改定价",
        "拍下不买", "违约", "无理由取消",
        "曝光", "限流", "流量",
        "审核", "审批",
    ]
    return any(term in user_message for term in seller_terms)


def _is_dispute_query(user_message: str) -> bool:
    """识别纠纷维权类咨询：货不对板、假货、到手刀、恶意退款掉包、仲裁、时效、收货后质量问题。"""
    dispute_terms = [
        "货不对板", "描述不符", "货不对",
        # 口语化描述不符："收到的东西跟描述完全不一样""跟图片差太多"
        "跟描述", "和描述", "与描述", "描述完全不一样", "跟图片不一样", "和图片不一样",
        "差太多", "差别太大", "完全不一样", "差得远",
        # 成色/新旧落差："旧得没法看""太旧了""跟旧的一样"
        "旧得", "太旧", "很旧", "破旧", "旧到", "没法看", "没法用", "根本用不了",
        "假货", "仿品", "假货申诉",
        "到手刀", "恶意砍价", "砍价纠纷",
        # 卖家视角的砍价纠纷：有人/买家/对方来砍价、一直砍、被砍价（买家自称砍价走买家服务）。
        "有人砍", "来砍价", "又砍价", "跟我砍", "被砍价", "买家砍价", "一直砍", "砍来砍去", "拍了又砍",
        "恶意退款", "掉包", "退款纠纷",
        "质量问题", "说坏", "说坏了", "坏了", "坏掉", "用不了", "破损",
        # 卖家视角"买家说没收到货"纠纷："货发了买家说没收到，咋整""买家说没到"
        "说没收到", "说没到", "称没收到", "说没签收", "说没拿到", "说没看到",
        "仲裁", "举证", "凭证", "站外沟通", "申诉",
        "申诉时效", "处理时效", "工作日",
    ]
    return any(term in user_message for term in dispute_terms)


def _is_prohibited_goods_query(user_message: str) -> bool:
    """识别禁售品咨询："能不能卖 XX" 且 XX 命中禁售品类时给出违规判断。"""
    ask_sell = any(term in user_message for term in ("能不能卖", "可以卖", "能卖", "卖不卖", "可以出售", "可以发布", "能发布", "能不能发"))
    prohibited_terms = [
        "高仿", "假货", "仿品", "复刻", "盗版", "破解版", "盗版资源",
        "虚拟物品", "游戏账号", "充值卡", "卡密", "代练", "外挂",
        "票务", "门票", "卡券", "优惠券倒卖",
        "活体", "宠物", "动物",
        "管制刀具", "仿真枪", "枪支", "弹药", "烟花爆竹", "管制物品",
        "医疗器械", "处方药", "药品", "违禁",
        "色情", "低俗", "隐私",
        "文物", "赃物", "来路不明",
    ]
    return ask_sell and any(term in user_message for term in prohibited_terms)


def _is_risk_prevention_query(user_message: str) -> bool:
    """识别风险防控类场景：站外交易、低价风险、面交安全、违规举报。"""
    risk_terms = [
        "微信", "QQ", "私聊", "转账", "加v", "加vx", "加微", "站外", "脱离平台", "二维码",
        "私下", "私底下", "先转", "先付", "预付", "定金",
        "远低于市场价", "低价", "低于市场", "诈骗风险", "骗子", "骗局",
        "同城面交", "面交", "公共场所", "带现金",
        "举报", "投诉", "诈骗",
    ]
    return any(term in user_message for term in risk_terms)


def detect_off_platform_risk_keywords(user_message: str) -> list[str]:
    """主动风控：从当前用户消息中提取站外交易高危词，供编排层插入风险预警。

    返回命中的高危词列表，供 Agent 在任意意图下都优先插入站外交易安全提示。
    """
    high_risk_terms = [
        "加微信", "加我微信", "微信聊", "微信交易", "微信转账",
        "QQ私聊", "QQ聊", "QQ交易", "加QQ",
        "加v", "加vx", "加微", "加微信详聊",
        "私下转账", "私下交易", "私下付款", "直接转账", "先转账", "先付款",
        "脱离平台", "不走平台", "站外交易", "平台外", "绕过平台",
        "扫码付款", "扫二维码", "二维码转账",
    ]
    return [term for term in high_risk_terms if term in user_message]


def infer_user_role(user_message: str, runtime_role: str | None) -> str:
    """身份上下文推断：Runtime Context 显式角色优先（商城登录身份是事实，最可信），
    其次按消息中的自称动作/他称主体判断买/卖视角。
    注意"发货/收货/退款"等动作词在买家和卖家视角都会出现（买家催卖家发货、
    卖家被买家催发货、卖家要处理买家退款），不能仅凭单个动作词定身份，
    否则会出现买家被当卖家、卖家被当买家的身份串台。
    """
    if runtime_role in ("buyer", "seller"):
        return runtime_role
    # 自称买家动作（第一人称，最明确）：我买/我拍/我收/我退 → 买家视角。
    # 注意口语中"我昨天买的"并非"我买的"连续子串，需用"买的"等宽松词兜底。
    if any(term in user_message for term in (
        "我买", "我拍", "我下单", "我付", "我收", "我退", "我签收",
        "买的", "拍的", "下的单", "我的订单", "我的快递", "我的物流", "东西到了", "到货了",
    )):
        return "buyer"
    # 自称卖家动作：我卖/我挂/我上架/我发布/我发货 → 卖家视角
    if any(term in user_message for term in (
        "我卖", "我挂", "我上架", "我发布", "我发货",
        "卖的", "挂的", "上架的", "发布的", "我的货", "我的商品", "我的宝贝", "货款", "提现",
    )):
        return "seller"
    # 他称主体：句中提到"卖家/买家"时，说话者通常是另一侧身份
    if "卖家" in user_message:
        return "buyer"
    if "买家" in user_message:
        return "seller"
    return str(runtime_role or "unknown")


# ---- 角色-意图职责隔离（根治双端串味）----
# 买家专属意图：卖家身份会话不执行，引导切换买家账号
BUYER_ONLY_INTENTS = frozenset({"cart_query", "refund_request", "return_request", "refund_status_query"})
# 卖家专属意图：买家身份会话不执行，引导切换卖家账号
SELLER_ONLY_INTENTS = frozenset({"seller_products_query", "seller_orders_query", "seller_service_query"})

_ROLE_INTENT_LABEL = {
    "cart_query": "购物车",
    "refund_request": "退款申请",
    "return_request": "退货申请",
    "refund_status_query": "退款进度",
    "seller_products_query": "商品售卖情况",
    "seller_orders_query": "卖出订单",
    "seller_service_query": "卖家服务",
}


def apply_role_guard(intent: str, role: str | None) -> str | None:
    """角色-意图职责隔离：会话身份与意图专属角色不匹配时返回拦截引导话术，否则返回 None。

    role 只在显式身份（商城登录 runtime_role 或会话已确认身份 identity_confirm）下生效，
    不依赖消息级推断（避免单轮推断误伤双端通用问题）。拦截时不执行任何工具/知识，
    直接返回引导切换账号的确定性话术，杜绝买家账号查卖家数据、卖家账号操作买家售后。
    """
    if role not in ("buyer", "seller"):
        return None
    label = _ROLE_INTENT_LABEL.get(intent, intent)
    if intent in SELLER_ONLY_INTENTS and role == "buyer":
        return (
            f"你是买家身份，卖家侧的「{label}」需要用卖家账号查看。"
            "请先切换到卖家账号（商城右上角可一键切换为卖家）再问我，避免串台。"
        )
    if intent in BUYER_ONLY_INTENTS and role == "seller":
        return (
            f"你是卖家身份，买家侧的「{label}」需要用买家账号操作。"
            "请先切换到买家账号（商城右上角可一键切换为买家）再问我。"
        )
    return None


def extract_order_id(user_message: str) -> str | None:
    """从用户问题中抽取小黄鱼二手电商交易平台订单号，供工具调用前参数校验。"""
    match = re.search(r"\b(?:SO[A-Za-z0-9_-]{6,}|ORD\d{4,})\b", user_message, flags=re.IGNORECASE | re.ASCII)
    return match.group(0) if match else None


_CN_INDEX = {"一": 1, "两": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

# 状态口语联想词 → 订单状态字段（兼容 orderStatus/fulfillmentStatus/status 的多种取值）
# 每个状态覆盖最常见的自然口语说法（含"没/未/还没"等否定表达），
# 避免用户换一种说法就匹配不到。
_STATUS_REFERENCE_TERMS: dict[str, tuple[str, ...]] = {
    "待发货": ("PENDING_SHIPMENT", "PAID_PENDING_SHIPMENT"),
    "运输中": ("SHIPPED", "IN_TRANSIT"),
    "已签收": ("DELIVERED", "SIGNED"),
    "已完成": ("COMPLETED",),
    "已取消": ("CANCELED",),
    "待支付": ("PENDING_PAYMENT", "UNPAID"),
    "已退款": ("REFUNDED",),
}
# 状态口语表达别名（命中任一即按对应状态联想），比状态字段更贴近用户原话。
_STATUS_REFERENCE_ALIASES: dict[str, tuple[str, ...]] = {
    "待发货": ("没发货", "未发货", "还没发货", "还没发", "没发", "未发", "还没有发货", "发货没", "发货了吗", "没给我发"),
    "运输中": ("在路上", "物流中", "在途", "已发出", "发出去了", "已经发了", "发货了"),
    "已签收": ("签收了", "收到了", "已收货", "收到货", "已经收到"),
    "已完成": ("完成了", "完成的", "完结", "交易完成"),
    "已取消": ("取消了", "取消的"),
    "待支付": ("没付款", "未付款", "还没付款", "没支付", "未支付", "还没支付"),
    "已退款": ("退款了", "退回来了", "已退款", "退回了"),
}
_ORDER_ATTENTION_WORDS = {"那笔", "这单", "那单", "这笔", "订单", "的单", "那笔订单"}
# 品类别名：用户说"手机"但商品名可能是"iPhone 13"这类品牌名（不含"手机"二字）。
# 商品名联想先做子串匹配，再按别名映射到品牌/品类关键词做兜底。
_ORDER_ALIAS_TERMS: dict[str, tuple[str, ...]] = {
    "手机": ("iphone", "手机", "华为", "小米", "荣耀", "三星", "oppo", "vivo", "红米", "魅族", "一加"),
    "平板": ("ipad", "平板", "matepad"),
    "电脑": ("笔记本", "电脑", "macbook", "thinkpad", "拯救者", "surface"),
    "耳机": ("耳机", "airpods", "earbuds", "蓝牙耳机"),
    "手表": ("手表", "watch"),
    "相机": ("相机", "gopro", "摄像机"),
    "灯": ("灯", "夜灯", "台灯"),
    "吹风机": ("吹风机", "吹风"),
    "净化器": ("净化器", "空气净化"),
    "咖啡机": ("咖啡机", "咖啡"),
}


def _order_field(order: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = order.get(key)
        if value is not None:
            return value
    return None


def _order_no_of(order: dict[str, Any]) -> str | None:
    order_no = str(_order_field(order, "orderNo", "order_no") or "").strip()
    return order_no or None


def _order_status_of(order: dict[str, Any]) -> str:
    return str(_order_field(order, "orderStatus", "fulfillmentStatus", "status") or "").upper()


def _product_names_of(order: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in order.get("items") or []:
        if not isinstance(item, dict):
            continue
        name = str(_order_field(item, "productName", "name") or "").strip()
        if name:
            names.append(name)
    return names


def _price_of(order: dict[str, Any]) -> float | None:
    raw = _order_field(order, "totalAmount", "amount", "price")
    if raw is None:
        return None
    try:
        return float(str(raw).replace("¥", "").replace(",", "").strip())
    except ValueError:
        return None


def resolve_order_reference(
    user_message: str,
    runtime_user_id: str,
    runtime_context: dict[str, Any] | None,
    seen_order_ids: list[str] | None = None,
    extra_user_message: str | None = None,
) -> str | None:
    """把"第一个/第二笔/最近那笔/待发货那笔/耳机那笔"等指代解析成订单号。

    上一轮澄清已向用户展示最近订单候选（按创建时间倒序），本轮回复任何能
    定位到具体订单的指代（序号、状态、商品名、金额、最近一笔）都应直接命中，
    而不是再次要求用户粘贴订单号。
    seen_order_ids 传入本会话提过的订单（提出顺序），指代优先在会话轨迹内解析：
    用户说"第一笔"通常指自己先问的那笔，而不是全局订单列表里最新的那笔。
    extra_user_message 传入上一轮用户消息：纯动作延续句（"帮我退款""申请退款"）
    的指代对象在上一轮（"买的那台手机能退吗"），合并匹配让动作句接上上下文。
    """
    orders = _get_user_orders(runtime_user_id, runtime_context)
    if not orders:
        return None
    # 与 build_order_clarification 保持一致：按创建时间倒序，最近订单优先。
    def _sort_key(order: dict[str, Any]) -> str:
        return str(_order_field(order, "createdAt", "orderDate") or "")
    sorted_orders = sorted(orders, key=_sort_key, reverse=True)
    seen_ids = seen_order_ids or []
    seen_set = set(seen_ids)
    # 会话轨迹子集：本会话提过、且在用户订单列表中的订单（保持澄清同款时间序）。
    seen_orders = [order for order in sorted_orders if _order_no_of(order) in seen_set]

    def _pick(index: int) -> str | None:
        if 1 <= index <= len(sorted_orders):
            return _order_no_of(sorted_orders[index - 1])
        return None

    # 序号指代只认当前消息；状态/商品/金额联想把上一轮消息一并纳入（动作延续句）。
    combined = user_message if not extra_user_message else f"{user_message} {extra_user_message}"

    # 1) 序数指代：第一个 / 第2笔 / 第三单——优先按"本会话提出的顺序"解析
    match = re.search(r"第\s*([一二两三四五六七八九1-9])", combined)
    if match:
        raw = match.group(1)
        index = _CN_INDEX.get(raw)
        if index is None:
            index = int(raw)
        if seen_ids and 1 <= index <= len(seen_ids):
            return seen_ids[index - 1]
        return _pick(index)

    # 2) 状态联想："待发货那笔 / 运输中的 / 已签收的 / 没发货的啊"
    #    先按口语别名匹配（"没发货/签收了/在路上"），再按状态字段名匹配（"待发货/运输中"）。
    status_hit: str | None = None
    for status_label, aliases in _STATUS_REFERENCE_ALIASES.items():
        if any(alias in combined for alias in aliases):
            status_hit = status_label
            break
    if status_hit is None:
        for status_label, statuses in _STATUS_REFERENCE_TERMS.items():
            if status_label in combined:
                status_hit = status_label
                break
    if status_hit:
        for order in (seen_orders or sorted_orders):
            if _order_status_of(order) in _STATUS_REFERENCE_TERMS[status_hit]:
                return _order_no_of(order)
        return None

    # 3) 商品名联想："耳机那笔 / 那个山地车"（按商品名连续子串匹配，去提示词）
    def _core_substrings(name: str) -> list[str]:
        core = name.replace("二手", "").replace("全新", "").replace("官方", "").strip()
        if len(core) < 2:
            return [core]
        result: set[str] = set()
        for width in (4, 3, 2):
            for start in range(len(core) - width + 1):
                part = core[start:start + width]
                if part not in _ORDER_ATTENTION_WORDS and not any(
                    hint in part for hint in ("那笔", "这个", "那个", "订单", "帮我", "看看", "查询", "查下", "哪个")
                ):
                    result.add(part)
        return sorted(result, key=len, reverse=True)

    for order in (seen_orders or sorted_orders):
        names = _product_names_of(order)
        if not names:
            continue
        for name in names:
            for part in _core_substrings(name):
                if part in combined:
                    return _order_no_of(order)

    # 3.5) 品类别名兜底：用户说"手机"但商品名是"iPhone 13"（不含"手机"二字），
    #      按别名映射到品牌/品类关键词匹配。
    alias_keywords: tuple[str, ...] | None = None
    for alias, keywords in _ORDER_ALIAS_TERMS.items():
        if alias in combined:
            alias_keywords = keywords
            break
    if alias_keywords:
        for order in (seen_orders or sorted_orders):
            names_text = " ".join(_product_names_of(order) or []).lower()
            if any(keyword in names_text for keyword in alias_keywords):
                return _order_no_of(order)

    # 4) 金额联想："529 那笔 / ¥529 的"
    money = re.search(r"[¥￥]?\s*(\d{2,}(?:\.\d+)?)", combined)
    if money:
        target = float(money.group(1))
        for order in (seen_orders or sorted_orders):
            order_price = _price_of(order)
            if order_price is not None and abs(order_price - target) < 0.01:
                return _order_no_of(order)
        return None

    # 5) "最近那笔/最新那笔/上一单" 等口语指代：会话内最近提过的一笔优先。
    if any(term in combined for term in ("最近那笔", "最新那笔", "最近那单", "上一单", "最近一笔")):
        if seen_ids:
            return seen_ids[-1]
        return _pick(1)
    return None


def resolve_order_reference_by_model(
    user_message: str,
    runtime_user_id: str,
    runtime_context: dict[str, Any] | None,
    route_model_client: Any,
    seen_order_ids: list[str] | None = None,
    extra_user_message: str | None = None,
) -> str | None:
    """模型指代消解兜底：把上一轮澄清展示的订单候选列表交给模型，让模型
    从自然语言里挑出用户指的是哪一笔（如"没发货的啊""第二个""昨天那单"）。

    规则路径（resolve_order_reference）只覆盖已枚举的表达，模型路径负责
    兜底未枚举的自然语言，从而把"用户换一种说法就匹配不到"这一类问题
    整体交给模型理解，而不是继续堆关键词。
    seen_order_ids 传入本会话提过的订单（提出顺序），模型优先在其中理解指代。
    extra_user_message 传入上一轮用户消息，供"帮我退款"这类纯动作延续句
    结合上一轮指代对象理解。
    """
    orders = _get_user_orders(runtime_user_id, runtime_context)
    if not orders:
        return None

    def _sort_key(order: dict[str, Any]) -> str:
        return str(order.get("createdAt") or order.get("orderDate") or "")
    sorted_orders = sorted(orders, key=_sort_key, reverse=True)

    status_label = {
        "PENDING_PAYMENT": "待支付", "PENDING_SHIPMENT": "待发货",
        "SHIPPED": "运输中", "DELIVERED": "已签收",
        "CANCELED": "已取消", "COMPLETED": "已完成",
    }
    lines: list[str] = []
    valid_order_nos: set[str] = set()
    for idx, order in enumerate(sorted_orders, start=1):
        order_no = _order_no_of(order)
        if not order_no:
            continue
        valid_order_nos.add(order_no)
        status_raw = _order_status_of(order)
        status_text = status_label.get(status_raw, status_raw)
        price = _price_of(order)
        price_text = f"¥{price:g}" if price is not None else ""
        names = "、".join(_product_names_of(order)) or "-"
        lines.append(
            f"{idx}. 订单号 {order_no} | 状态 {status_text} | 金额 {price_text} | 商品 {names}"
        )
    if not valid_order_nos:
        return None

    candidates_text = "\n".join(lines)
    seen_text = ""
    if seen_order_ids:
        # 会话轨迹：用户本会话提过的订单（提出顺序），模型理解指代时优先考虑。
        seen_text = (
            "\n\n用户在本会话中提过以下订单（按提出顺序，优先在这些订单中理解指代，"
            "仍须从候选列表里输出实际订单号）：\n" + "、".join(seen_order_ids)
        )
    prev_text = ""
    if extra_user_message:
        # 上一轮用户消息：动作延续句（如"帮我退款"）的对象在上一轮里。
        prev_text = f"\n用户上一轮消息（可能与本次指代相关）：{extra_user_message}"
    system_prompt = (
        "你是电商客服的订单指代消解模块。上一轮客服向用户展示了候选订单列表，"
        "用户本轮用自然语言回复（例如\"第一个\"\"没发货的啊\"\"耳机那笔\"\"第二笔\""
        "\"最近那单\"\"¥599 那笔\"），你需要判断用户指的是列表中的哪一笔订单。\n"
        "只能从下列候选订单中挑选，输出 JSON：{\"orderNo\": \"xxx\"}。"
        "如果用户消息无法对应到任何一笔候选订单，输出 JSON：{\"orderNo\": null}。"
        "不要输出任何其他内容。\n\n候选订单列表：\n" + candidates_text + seen_text + prev_text
    )
    try:
        raw = None
        # 模型 API 在线推理偶发失败（实测同 prompt 间隔调用会偶尔返回空），
        # 短重试最多 2 次，每次 20s，总耗时控制在网关读超时（45s）以内。
        for _attempt in range(2):
            raw = route_model_client.structured_query(
                system_prompt=system_prompt, user_message=user_message, timeout=20
            )
            if raw:
                break
    except Exception:
        return None
    if not raw:
        return None
    match = re.search(r"\"orderNo\"\s*:\s*\"([^\"]+)\"", raw)
    if not match:
        return None
    picked = match.group(1)
    return picked if picked in valid_order_nos else None


def extract_return_reason(user_message: str) -> str | None:
    """只接受用户明确表达的退货原因，不由模型代填高风险售后事实。"""
    reason_terms = ("七天无理由", "质量问题", "商品破损", "发错货", "少件", "与描述不符")
    return next((term for term in reason_terms if term in user_message), None)


def build_route_plan(
    *,
    intent: Intent,
    user_message: str,
    order_id: str | None,
    model_used: bool,
    role: str = "unknown",
) -> RoutePlan:
    """把意图收敛成白名单 RoutePlan，模型不能自由增加工具或高风险动作。

    role 用于意图×身份双层白名单（纵深防御）：身份明确时，卖家侧工具（商品售卖/卖出订单）
    只放行卖家、买家侧工具（购物车）只放行买家，配合 apply_role_guard 杜绝双端串味。
    """
    candidate_catalog = {
        "get_order_detail": ToolCandidate(
            name="get_order_detail",
            domain="order",
            risk_level="low",
            reason="读取当前用户订单事实，不执行业务写操作。",
        ),
        "get_order_logistics": ToolCandidate(
            name="get_order_logistics",
            domain="logistics",
            risk_level="low",
            reason="读取当前用户订单及物流状态。",
        ),
        "get_refund_status": ToolCandidate(
            name="get_refund_status",
            domain="after_sale",
            risk_level="low",
            reason="只读查询当前用户订单的售后申请状态，不创建退款申请。",
        ),
        "search_products": ToolCandidate(
            name="search_products",
            domain="product",
            risk_level="low",
            reason="查询商品价格、库存和活动等实时事实。",
        ),
        "recommend_products": ToolCandidate(
            name="recommend_products",
            domain="product",
            risk_level="low",
            reason="按类别/用途/预算从商城在售商品库推荐真实商品，数据来自业务后端商品库。",
        ),
        "query_seller_product_sales": ToolCandidate(
            name="query_seller_product_sales",
            domain="product",
            risk_level="low",
            reason="查询当前卖家自己发布商品的售卖状态，数据来自业务后端商品库。",
        ),
        "query_seller_orders": ToolCandidate(
            name="query_seller_orders",
            domain="order",
            risk_level="low",
            reason="查询当前卖家的卖出订单（买家购买该卖家商品的订单）及履约状态，数据来自业务后端订单库。",
        ),
        "get_cart_items": ToolCandidate(
            name="get_cart_items",
            domain="cart",
            risk_level="low",
            reason="读取当前用户购物车加购记录，数据来自业务后端购物车表。",
        ),
    }
    required_tools: list[str] = []
    knowledge_domains: list[str] = []
    risk_level: RiskLevel = "low"
    requires_workflow = False
    if intent == "order_query":
        required_tools = ["get_order_logistics"]
    elif intent == "refund_status_query":
        required_tools = ["get_refund_status"]
    elif intent == "refund_request":
        required_tools = ["get_order_detail"]
        knowledge_domains = ["after_sale_policy"]
        risk_level = "high"
        requires_workflow = True
    elif intent == "return_request":
        required_tools = ["get_order_detail"]
        knowledge_domains = ["received_return_policy"]
        risk_level = "high"
        requires_workflow = True
    elif intent == "product_query":
        required_tools = ["search_products"]
        knowledge_domains = ["promotion_and_member_policy"] if any(term in user_message for term in ["活动", "优惠", "满减", "会员"]) else []
    elif intent == "recommend_products":
        # 商品推荐只读商城在售商品库（真实价格/库存/活动），不依赖 RAG 话术编造。
        required_tools = ["recommend_products"]
    elif intent == "seller_products_query":
        # 卖家售卖情况查询只读业务后端商品库，不依赖 RAG 话术。
        required_tools = ["query_seller_product_sales"]
    elif intent == "seller_orders_query":
        # 卖家卖出订单查询只读业务后端订单库，返回买家购买的真实履约状态，不依赖 RAG 话术。
        required_tools = ["query_seller_orders"]
    elif intent == "cart_query":
        # 购物车查询只读业务后端购物车，返回用户真实加购记录，不依赖 RAG 话术。
        required_tools = ["get_cart_items"]
    elif intent in {"faq_query", "promotion_query", "low_confidence_query"}:
        knowledge_domains = ["faq"] if intent == "faq_query" else ["promotion_and_member_policy"]
    elif intent in {"platform_rule_query", "buyer_service_query", "seller_service_query", "dispute_query", "risk_prevention_query", "fulfillment_consult_query"}:
        # 平台规则与二手交易咨询类问题全部走知识检索路径，不调用业务写工具。
        # 履约担忧/售后物流咨询（催发货、被骗、退货物流）同样只读知识话术，无业务工具。
        knowledge_domains = [intent.replace("_query", "")]
    elif intent in {"security_request", "degradation_request"}:
        risk_level = "high" if intent == "security_request" else "medium"

    # 缺少订单号时保留候选工具，但不允许模型凭空生成参数并执行。
    order_bound_tools = {"get_order_detail", "get_order_logistics", "get_refund_status"}
    executable_tools = required_tools if order_id or not any(name in order_bound_tools for name in required_tools) else []
    # 意图×身份工具白名单（纵深防御）：身份明确时，卖家侧工具只放行卖家、买家侧工具只放行买家。
    # apply_role_guard 已在意图层拦截跨身份意图，这里兜住模型/规则未拦截的漏网参数路径。
    if role in ("buyer", "seller"):
        role_tool_restriction = {
            "query_seller_product_sales": "seller",
            "query_seller_orders": "seller",
            "get_cart_items": "buyer",
        }
        executable_tools = [name for name in executable_tools if role_tool_restriction.get(name, role) == role]
    return RoutePlan(
        intent=intent,
        needs_rag=bool(knowledge_domains),
        needs_business_tools=bool(executable_tools),
        required_tools=executable_tools,
        tool_candidates=[candidate_catalog[name] for name in executable_tools],
        knowledge_domains=knowledge_domains,
        entity_refs=[order_id] if order_id else [],
        risk_level=risk_level,
        requires_workflow=requires_workflow,
        confidence=0.9 if model_used else 0.75,
        source="llm_with_policy_constraints" if model_used else "deterministic_fallback",
        fallback_policy="ask_order_id" if required_tools and not order_id and any(name in order_bound_tools for name in required_tools) else "safe_deterministic_path",
    )


def _get_user_orders(runtime_user_id: str, runtime_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    """获取用户订单列表：优先 Runtime Context，退化为后端业务接口查询。"""
    context_orders = (runtime_context or {}).get("currentUserOrders", [])
    if context_orders:
        result: list[dict[str, Any]] = []
        for order in context_orders:
            if not isinstance(order, dict) or not str(order.get("orderNo") or "").strip():
                continue
            owner = order.get("userId")
            # 商城网关注入的订单摘要无 userId 字段（已按登录用户过滤归属），直接放行；
            # 调试台等其他来源带 userId 时仍需与当前用户匹配，防止越权引用。
            if owner is None or str(owner) == runtime_user_id:
                result.append(order)
        if result:
            return result
    backend_orders = user_orders_from_ecommerce(runtime_user_id)
    if backend_orders is None:
        return []
    return backend_orders


def build_order_clarification(request: ChatRequest, route_plan: RoutePlan) -> ClarificationRequest | None:
    """后端根据 RoutePlan 必填参数和可信 Runtime Context 生成候选，不让模型代选订单。
    买家无订单号时，从 Runtime Context 或业务后端拉取用户订单列表并展示最近 3 笔优先。"""
    if route_plan.fallback_policy != "ask_order_id" or not route_plan.tool_candidates:
        return None
    orders = _get_user_orders(request.runtime_user_id, request.runtime_context)
    if not orders:
        return ClarificationRequest(
            clarification_field="order_id",
            message=(
                "你当前账号下暂未查询到订单记录。"
                "如果你有具体的订单号，可以直接告诉我，我帮你查询。"
            ),
            candidates=[],
        )
    # 按创建时间倒序，最近订单优先
    def _sort_key(order: dict[str, Any]) -> str:
        return str(order.get("createdAt") or order.get("orderDate") or "")
    sorted_orders = sorted(orders, key=_sort_key, reverse=True)
    recent_count = min(3, len(sorted_orders))
    recent_orders = sorted_orders[:recent_count]
    candidates: list[ClarificationCandidate] = []
    for order in recent_orders:
        order_no = str(order.get("orderNo") or "").strip()
        items = order.get("items") or []
        product_names = "、".join(
            str(item.get("productName") or item.get("name") or "")
            for item in items[:2]
            if isinstance(item, dict) and item.get("productName") or item.get("name")
        )
        total = order.get("totalAmount") or order.get("price") or ""
        status_text = str(order.get("orderStatus") or order.get("status") or "")
        status_label = {
            "PENDING_PAYMENT": "待支付", "PENDING_SHIPMENT": "待发货", "SHIPPED": "运输中",
            "DELIVERED": "已签收", "CANCELED": "已取消", "COMPLETED": "已完成",
        }
        hint_parts = []
        if status_text:
            hint_parts.append(status_label.get(status_text, status_text))
        if total:
            hint_parts.append(f"¥{total}")
        if product_names:
            hint_parts.append(product_names)
        candidates.append(
            ClarificationCandidate(
                value=order_no,
                label=order_no,
                hint=" | ".join(hint_parts) if hint_parts else "当前账号订单",
            )
        )
    action = "退款" if route_plan.intent == "refund_request" else "查询"
    status_label = {
        "PENDING_PAYMENT": "待支付", "PAID_PENDING_SHIPMENT": "待发货", "PENDING_SHIPMENT": "待发货",
        "SHIPPED": "运输中", "DELIVERED": "已签收", "CANCELED": "已取消",
        "COMPLETED": "已完成", "REFUNDED": "已退款", "UNPAID": "未支付", "PAID": "已支付",
    }
    # 按状态统计订单概况，生成数据驱动的自然语言介绍（订单变化时回答随之变化，避免固定模板感）
    status_counter: dict[str, int] = {}
    for order in sorted_orders:
        status_text = str(order.get("orderStatus") or order.get("status") or "")
        label = status_label.get(status_text, status_text or "其他")
        status_counter[label] = status_counter.get(label, 0) + 1
    summary = "、".join(f"{label} {count} 笔" for label, count in status_counter.items()) or "状态暂无"
    # 待办提醒：优先关注需要用户行动的订单（待支付 / 运输中）
    attention: list[str] = []
    for order in sorted_orders:
        status_text = str(order.get("orderStatus") or order.get("status") or "")
        if status_text == "PENDING_PAYMENT":
            items = order.get("items") or []
            first_item = items[0] if isinstance(items[0], dict) else {}
            name = str(first_item.get("productName") or first_item.get("name") or "")
            total = order.get("totalAmount") or order.get("price") or ""
            if name and total:
                attention.append(f"{name}（¥{total}）这笔还未付款，尽快完成支付就能安排发货")
            elif name:
                attention.append(f"{name}这笔还未付款，尽快完成支付就能安排发货")
            else:
                attention.append("有 1 笔订单还未付款，尽快完成支付就能安排发货")
    shipped_count = sum(
        1 for order in sorted_orders
        if str(order.get("orderStatus") or order.get("status") or "") == "SHIPPED"
    )
    if shipped_count:
        attention.append(f"有 {shipped_count} 笔订单正在运输中，需要的话我可以帮你查最新物流")
    intro = f"你名下共有 {len(sorted_orders)} 笔订单：{summary}。"
    if attention:
        intro += " " + "；".join(attention) + "。"
    if len(sorted_orders) > recent_count:
        intro += f" 最近 {recent_count} 笔如下："
    else:
        intro += " 订单如下："
    return ClarificationRequest(
        clarification_field="order_id",
        message=(
            f"{intro} 要{action}哪一笔？告诉我订单号，我帮你查明细；"
            "也可以回复状态关键词（如\"待发货\"），我按状态帮你筛。"
        ),
        candidates=candidates,
    )


def estimate_tokens(text: str) -> int:
    """用近似 token 估算服务成本治理和上下文预算展示。"""
    return max(1, len(text) // 2)
