"""FastAPI 路由层，只负责接入 Agent、Resume、Trace、Eval 和 Feedback。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agents.customer_service_agent import CustomerServiceAgent
from api.schemas import *
from config.settings import CASES_PATH, load_agent_capabilities
from evals.runner import EvalRunner
from feedback.attribution import FailureAttributor, build_backfilled_case
from observability.trace import trace_store
from state.chat_snapshots import list_recent_sessions, list_session_messages, record_chat_snapshot
from state.session_state import BACKFILLED_CASES, FEEDBACK_RECORDS


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时恢复持久化会话状态并预热 RAG 知识索引。

    在线模式下预热 RAG：知识索引由真实 Embedding API 构建，首次构建约 1-2 分钟。
    若不预热，商城网关（读超时 45s）在用户首个客服请求上会降级为"客服服务暂时繁忙"。
    持久化恢复：把 SQLite 中的会话记忆/对话快照/消息计数/checkpoint/trace 回填内存，
    支持重启后的断线恢复与多轮审计。
    """
    try:
        from context.builder import load_session_memories
        from state.chat_snapshots import load_chat_snapshots
        from state.persistence import init as init_persistence
        from state.session_state import load_persisted_state
        from observability.trace import load_persisted_traces
        init_persistence()
        load_session_memories()
        load_chat_snapshots()
        load_persisted_state()
        load_persisted_traces()
        print("[lifespan] 持久化会话状态恢复完成", flush=True)
    except Exception as exc:
        print(f"[lifespan] 持久化状态恢复失败，以内存模式运行: {exc}", flush=True)
    try:
        from rag.hybrid_retrieval import get_knowledge_index
        await asyncio.to_thread(get_knowledge_index)
        print("[lifespan] RAG 知识索引预热完成", flush=True)
    except Exception as exc:  # 预热失败不阻塞启动，首个请求将按需构建。
        print(f"[lifespan] RAG 索引预热失败，首个请求将按需构建: {exc}", flush=True)
    yield


agent = CustomerServiceAgent()
eval_runner = EvalRunner(agent, CASES_PATH)
eval_runner.backfilled_cases = BACKFILLED_CASES
failure_attributor = FailureAttributor()
app = FastAPI(title="XiaoHuangYu Customer Service Agent", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    """提供当前版本健康检查。"""
    return {"status": "ok", "version": "v1"}


@app.get("/capabilities")
def capabilities() -> dict[str, Any]:
    """返回当前版本能力清单。"""
    return load_agent_capabilities()


def _backfill_session_history(request: ChatRequest) -> ChatRequest:
    """每轮对话发生前，从服务端会话快照回填完整历史。

    服务端 record_chat_snapshot 已按会话保存每轮 user/assistant 原文，
    这里保证"所有对话在发生之前都走一遍上下文"，不依赖前端是否传历史。
    快照为权威来源：非空时覆盖前端传入的 history_messages（前端可能是子集）。
    """
    try:
        messages = list_session_messages(request.session_id)
    except Exception:
        return request
    history = [
        HistoryMessage(role=message["role"], content=message["content"])
        for message in messages
        if message.get("role") in ("user", "assistant") and message.get("content")
    ]
    if history and len(history) >= len(request.history_messages):
        request.history_messages = history
    return request


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """处理一次大促场景验证聊天请求。"""
    request = _backfill_session_history(request)
    response = agent.chat(request)
    record_chat_snapshot(request, response)
    return response


@app.get("/sessions/recent")
def sessions_recent(limit: int = 10) -> list[dict[str, Any]]:
    """返回最近会话元信息（联动面板轮询发现新商城会话）。"""
    return list_recent_sessions(limit)


@app.get("/sessions/{session_id}/messages")
def session_messages(session_id: str) -> list[dict[str, Any]]:
    """返回指定会话的完整对话消息（含每轮完整 ChatResponse）。"""
    return list_session_messages(session_id)


@app.post("/chat/resume", response_model=ChatResumeResponse)
def chat_resume(request: ChatResumeRequest) -> ChatResumeResponse:
    """恢复一个暂停在 HITL 节点的售后 workflow。"""
    return agent.resume(request)


@app.get("/sessions/{session_id}/trace", response_model=list[TraceEvent])
def session_trace(session_id: str) -> list[TraceEvent]:
    """返回指定会话的公开 Trace。"""
    return trace_store.list(session_id)


@app.post("/eval/run", response_model=EvalRunResponse)
def run_eval(request: EvalRunRequest) -> EvalRunResponse:
    """运行大促场景验证回归评测。"""
    return eval_runner.run(case_id=request.case_id)


@app.post("/feedback/submit", response_model=FeedbackSubmitResponse)
def submit_feedback(request: FeedbackRequest) -> FeedbackSubmitResponse:
    """提交反馈、生成归因并回填成临时回归 case。"""
    eval_report = eval_runner.run(case_id=request.case_id) if request.case_id else None
    eval_result = eval_report.results[0] if eval_report and eval_report.results else None
    events = trace_store.list(request.session_id)
    attributions = failure_attributor.attribute(feedback=request, trace_events=events, eval_result=eval_result)
    base_case = next((case for case in eval_runner.load_cases() if case["case_id"] == request.case_id), None)
    backfilled_case = build_backfilled_case(request, attributions, base_case)
    BACKFILLED_CASES.append(backfilled_case)
    record = FeedbackRecord(
        feedback_id=f"fb-{len(FEEDBACK_RECORDS) + 1:03d}",
        session_id=request.session_id,
        case_id=request.case_id,
        rating=request.rating,
        user_comment=request.user_comment,
        trace_event_names=[event.event_type for event in events],
        eval_failure_categories=eval_result.failure_categories if eval_result else [],
        attributions=attributions,
        backfilled_case=backfilled_case,
    )
    FEEDBACK_RECORDS.append(record)
    return FeedbackSubmitResponse(record=record, eval_report=eval_report)
