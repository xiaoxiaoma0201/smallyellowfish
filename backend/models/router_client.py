"""真实模型路由客户端。"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from pydantic import BaseModel, Field

from api.schemas import Intent
from config.settings import api_key_is_missing, load_app_env, openai_base_url, openai_model_name
from prompts.loader import PromptManager, prompt_manager


class ModelRouteResult(BaseModel):
    """模型路由结果。不可用或输出不可信时回退到规则路由。"""

    intent: Intent
    used_model: bool = False
    model_name: str | None = None
    fallback_reason: str | None = None
    framework: str | None = None
    prompt_fragments: list[dict[str, Any]] = Field(default_factory=list)


class RouteModelClient:
    """用真实大模型判断本轮应该进入哪条受控路径。"""

    def __init__(self, manager: PromptManager = prompt_manager) -> None:
        self.prompt_manager = manager

    def can_call_model(self) -> bool:
        """检查当前运行环境是否能真实调用聊天模型。"""
        load_app_env()
        if os.getenv("AGENT_DISABLE_LLM") == "1":
            return False
        if api_key_is_missing(os.getenv("AGENT_OPENAI_API_KEY")):
            return False
        try:
            self._chat_model_class()
        except ImportError:
            return False
        return True

    def structured_query(
        self,
        system_prompt: str,
        user_message: str,
        *,
        timeout: int = 30,
    ) -> str | None:
        """通用结构化理解：让模型基于给定上下文输出短答案（如指代消解）。

        供规则无法覆盖的自然语言理解场景使用（例如用户说"没发货的啊"而非
        订单号）。超时较短（默认 30s），任何异常返回 None 由调用方兜底。
        """
        if not self.can_call_model():
            return None
        try:
            model = self._create_chat_model(temperature=0, timeout=timeout)
            content = self._invoke_chain(model, user_message, system_prompt)
            return content.strip() or None
        except Exception:
            return None

    def plan_intent(self, user_message: str, *, fallback_intent: Intent) -> ModelRouteResult:
        """优先用真实模型判断本轮意图，失败时回退到规则分类。"""
        if not self.can_call_model():
            return ModelRouteResult(intent=fallback_intent, fallback_reason="model_config_missing")
        try:
            model = self._create_chat_model(temperature=0)
            fragments = self.prompt_manager.select_fragments({"phase": "route"})
            system_prompt = self.prompt_manager.render_system_prompt(fragments)
            content = self._invoke_chain(model, user_message, system_prompt)
            prompt_fragments = self.prompt_manager.selection_summary(fragments, phase="route")
            intent = self._extract_intent(content)
            if intent is None:
                return ModelRouteResult(
                    intent=fallback_intent,
                    model_name=openai_model_name(),
                    fallback_reason="invalid_model_route",
                    prompt_fragments=prompt_fragments,
                )
            return ModelRouteResult(
                intent=intent,
                used_model=True,
                model_name=openai_model_name(),
                framework="langchain_runnable_sequence",
                prompt_fragments=prompt_fragments,
            )
        except Exception as exc:
            return ModelRouteResult(
                intent=fallback_intent,
                model_name=openai_model_name(),
                fallback_reason=exc.__class__.__name__,
            )

    @staticmethod
    def _extract_intent(content: str) -> Intent | None:
        allowed = {
            "general_chat",
            "order_query",
            "refund_status_query",
            "refund_request",
            "return_request",
            "faq_query",
            "promotion_query",
            "product_query",
            "recommend_products",
            "low_confidence_query",
            "degradation_request",
            "security_request",
            "platform_rule_query",
            "buyer_service_query",
            "seller_service_query",
            "seller_products_query",
            "seller_orders_query",
            "cart_query",
            "dispute_query",
            "risk_prevention_query",
            "fulfillment_consult_query",
            "unknown",
        }
        text = content.strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.S)
            if match is None:
                return None
            try:
                payload = json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
        intent = payload.get("intent")
        # "unknown" 视为模型未能判定意图，返回 None 让编排层回退到规则意图，
        # 避免 unknow 意图绕过 RAG/工具路径变成自由发挥回答。
        if intent == "unknown":
            return None
        return intent if intent in allowed else None

    @staticmethod
    def _chat_model_class() -> Any:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI

    def _create_chat_model(self, *, temperature: float, timeout: int = 120) -> Any:
        load_app_env()
        chat_model_class = self._chat_model_class()
        return chat_model_class(
            model=openai_model_name(),
            api_key=os.getenv("AGENT_OPENAI_API_KEY"),
            base_url=openai_base_url(),
            temperature=temperature,
            timeout=timeout,
            max_retries=0,
        )

    @staticmethod
    def _invoke_chain(model: Any, user_message: str, system_prompt: str) -> str:
        """用 LangChain RunnableSequence 执行可替换 Prompt、模型和输出解析。"""
        from langchain_core.messages import SystemMessage
        from langchain_core.output_parsers import StrOutputParser
        from langchain_core.prompts import ChatPromptTemplate

        prompt = ChatPromptTemplate.from_messages(
            [
                SystemMessage(content=system_prompt),
                ("human", "{user_message}"),
            ]
        )
        chain = prompt | model | StrOutputParser()
        return str(chain.invoke({"user_message": user_message}))
