import { useEffect, useMemo, useRef, useState } from "react";

import type {
  AgentCapabilityManifest,
  AgentEndpointKey,
  AgentFeatureKey,
  ChatResponse,
  ChatResumeDecision,
  ChatResumeResponse,
  ConversationMessage,
  DemoUser,
  DemoUserCreatePayload,
  EvalRunResponse,
  FeedbackSubmitResponse,
  ReasoningView,
  RuntimeOrderContextResponse,
  TraceEvent,
} from "../types/api";

const AGENT_BASE_URL = import.meta.env.VITE_AGENT_BASE_URL ?? "http://localhost:8000";
const ECOMMERCE_BASE_URL = import.meta.env.VITE_ECOMMERCE_BASE_URL ?? "http://localhost:8081";

/** 商城实时镜像槽：商城端（Java 网关 cs- 会话）的对话自动流入主对话区。 */
export const SHOP_USER_ID = "SHOP";
export const SHOP_USER_LABEL = "商城实时";

const DEFAULT_AGENT_CAPABILITIES: AgentCapabilityManifest = {
  schema_version: "agent_capabilities_v1",
  project: {
    id: "default-agent",
    title: "默认 Agent",
    summary: "目标 Agent 未提供能力声明时，调试后台只保留最小聊天入口。",
  },
  agent: {
    name: "小黄鱼二手电商交易平台客服 Agent",
    version: "default",
  },
  endpoints: {
    health: true,
    chat: true,
    chat_resume: false,
    trace: false,
    eval_run: false,
  },
  features: {
    chat: true,
    runtime_context: false,
    reasoning_summary: false,
    reasoning_content: false,
    structured_intent: false,
    rag_citations: false,
    tool_calls: false,
    workflow: false,
    human_approval: false,
    memory: false,
    hooks: false,
    trace: false,
    evaluation: false,
    cost_summary: false,
  },
};

const welcomeMessage: ConversationMessage = {
  role: "assistant",
  content: "欢迎来到小黄鱼二手电商交易平台客服 Agent 调试后台。请选择示例用户，或直接输入问题观察响应。",
};

const initialConversationMessages: Record<string, ConversationMessage[]> = {};

const initialUsers: DemoUser[] = [
  {
    profile: { userId: "U1001", nickname: "张三", role: "buyer", memberLevel: "gold", riskLevel: "low" },
    preferences: {
      userId: "U1001",
      preferredCategories: "耳机,充电器",
      preferredDelivery: "顺丰速运",
      budgetMin: 200,
      budgetMax: 800,
      invoiceRequired: true,
    },
  },
  {
    profile: { userId: "U1002", nickname: "李四", role: "seller", memberLevel: "silver", riskLevel: "low" },
    preferences: {
      userId: "U1002",
      preferredCategories: "音箱,户外数码",
      preferredDelivery: "普通快递",
      budgetMin: 100,
      budgetMax: 500,
      invoiceRequired: false,
    },
  },
  {
    profile: { userId: "U1003", nickname: "王五", role: "buyer", memberLevel: "normal", riskLevel: "medium" },
    preferences: {
      userId: "U1003",
      preferredCategories: "",
      preferredDelivery: "",
      budgetMin: null,
      budgetMax: null,
      invoiceRequired: false,
    },
  },
];

type UserConversationState = {
  sessionId: string;
  messages: ConversationMessage[];
  activeResponse?: ChatResponse;
  traceEvents: TraceEvent[];
  resumeResult?: ChatResumeResponse;
};

function createConversationState(userId: string): UserConversationState {
  return {
    sessionId: `session-${userId}-${Math.random().toString(36).slice(2, 10)}`,
    messages: initialConversationMessages[userId]?.map((message) => ({ ...message })) ?? [welcomeMessage],
    traceEvents: [],
  };
}

/** 商城实时镜像槽初始状态：无本地会话，等待 /sessions/recent 自动填充。 */
function createShopConversationState(): UserConversationState {
  return {
    sessionId: "",
    messages: [],
    traceEvents: [],
  };
}

async function loadDemoRuntimeContext(userId: string): Promise<Record<string, unknown>> {
  try {
    const response = await fetch(
      `${ECOMMERCE_BASE_URL}/api/debug/users/${encodeURIComponent(userId)}/order-context`,
    );
    if (!response.ok) {
      return {
        currentPage: "AGENT_WORKBENCH",
        currentUserOrders: [],
        currentUserOrdersTruncated: true,
      };
    }
    const envelope = (await response.json()) as { data?: RuntimeOrderContextResponse };
    if (!envelope.data || !Array.isArray(envelope.data.orders)) {
      return {
        currentPage: "AGENT_WORKBENCH",
        currentUserOrders: [],
        currentUserOrdersTruncated: true,
      };
    }
    return {
      currentPage: "AGENT_WORKBENCH",
      currentUserOrders: envelope.data.orders,
      currentUserOrdersTruncated: envelope.data.truncated === true,
    };
  } catch {
    return {
      currentPage: "AGENT_WORKBENCH",
      currentUserOrders: [],
      currentUserOrdersTruncated: true,
    };
  }
}

export function useAgentChat() {
  const [users, setUsers] = useState<DemoUser[]>(initialUsers);
  const [selectedUserId, setSelectedUserId] = useState(initialUsers[0].profile.userId);
  const [conversationByUser, setConversationByUser] = useState<Record<string, UserConversationState>>(() => ({
    ...Object.fromEntries(
      initialUsers.map((user) => [user.profile.userId, createConversationState(user.profile.userId)]),
    ),
    [SHOP_USER_ID]: createShopConversationState(),
  }));
  const [shopWatching, setShopWatching] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const shopPollRef = useRef<{ sessionId: string; turnCount: number } | undefined>(undefined);
  const shopMetaRef = useRef<{ userId?: string; nickname?: string }>({});
  const [isResuming, setIsResuming] = useState(false);
  const [isEvaluating, setIsEvaluating] = useState(false);
  const [isSubmittingFeedback, setIsSubmittingFeedback] = useState(false);
  const [isCreatingUser, setIsCreatingUser] = useState(false);
  const [evalReport, setEvalReport] = useState<EvalRunResponse | undefined>();
  const [evalError, setEvalError] = useState<string | undefined>();
  const [feedbackReport, setFeedbackReport] = useState<FeedbackSubmitResponse | undefined>();
  const [feedbackError, setFeedbackError] = useState<string | undefined>();
  const [agentCapabilities, setAgentCapabilities] = useState<AgentCapabilityManifest>(DEFAULT_AGENT_CAPABILITIES);
  const [capabilitiesError, setCapabilitiesError] = useState<string | undefined>();
  const isSendingRef = useRef(false);

  const selectedUser = useMemo(
    () => users.find((user) => user.profile.userId === selectedUserId) ?? users[0],
    [selectedUserId, users],
  );
  const activeConversation = conversationByUser[selectedUserId] ?? createConversationState(selectedUserId);

  useEffect(() => {
    let isActive = true;

    async function loadAgentCapabilities() {
      try {
        const response = await fetch(`${AGENT_BASE_URL}/capabilities`);
        if (!response.ok) {
          if (isActive) {
            setAgentCapabilities(DEFAULT_AGENT_CAPABILITIES);
            setCapabilitiesError("目标 Agent 未提供 /capabilities，调试后台只保留最小聊天入口。");
          }
          return;
        }

        const payload = (await response.json()) as AgentCapabilityManifest;
        if (isActive) {
          setAgentCapabilities(payload);
          setCapabilitiesError(undefined);
        }
      } catch {
        if (isActive) {
          setAgentCapabilities(DEFAULT_AGENT_CAPABILITIES);
          setCapabilitiesError("暂时无法读取 /capabilities，调试后台只保留最小聊天入口。");
        }
      }
    }

    void loadAgentCapabilities();

    return () => {
      isActive = false;
    };
  }, []);

  // 商城实时联动：轮询最近会话，发现商城（cs-）新对话后自动镜像进主对话区并切到"商城实时"。
  useEffect(() => {
    if (!shopWatching) {
      return;
    }
    let cancelled = false;

    async function pollShopSession() {
      try {
        const recentResponse = await fetch(`${AGENT_BASE_URL}/sessions/recent?limit=8`);
        if (!recentResponse.ok) {
          return;
        }
        const sessions = (await recentResponse.json()) as Array<{
          session_id: string;
          turn_count: number;
          user_id?: string;
          nickname?: string;
          last_message?: string;
        }>;
        const shopSession = sessions.find((session) => session.session_id?.startsWith("cs-"));
        if (!shopSession) {
          return;
        }
        const last = shopPollRef.current;
        if (last && last.sessionId === shopSession.session_id && last.turnCount === shopSession.turn_count) {
          return;
        }
        const messagesResponse = await fetch(
          `${AGENT_BASE_URL}/sessions/${encodeURIComponent(shopSession.session_id)}/messages`,
        );
        const traceResponse = await fetch(
          `${AGENT_BASE_URL}/sessions/${encodeURIComponent(shopSession.session_id)}/trace`,
        );
        if (!messagesResponse.ok) {
          return;
        }
        const messages = (await messagesResponse.json()) as ConversationMessage[];
        const traceEvents = traceResponse.ok ? ((await traceResponse.json()) as TraceEvent[]) : [];
        const assistantMessages = messages.filter((message) => message.role === "assistant" && message.response);
        const activeResponse = assistantMessages[assistantMessages.length - 1]?.response as ChatResponse | undefined;
        if (cancelled) {
          return;
        }
        setConversationByUser((current) => ({
          ...persistConversations({
            ...current,
            [SHOP_USER_ID]: {
              sessionId: shopSession.session_id,
              messages,
              traceEvents,
              activeResponse,
            },
          }),
        }));
        shopPollRef.current = { sessionId: shopSession.session_id, turnCount: shopSession.turn_count };
        shopMetaRef.current = { userId: shopSession.user_id, nickname: shopSession.nickname };
        // 商城有新的对话时自动切到"商城实时"，让后台像真实客服台一样同步展示。
        setSelectedUserId(SHOP_USER_ID);
      } catch {
        // 后端未就绪时静默跳过，下个周期重试。
      }
    }

    void pollShopSession();
    const timer = setInterval(() => void pollShopSession(), 2500);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [shopWatching]);

  function updateActiveConversation(updater: (current: UserConversationState) => UserConversationState) {
    setConversationByUser((current) => {
      const existing = current[selectedUserId] ?? createConversationState(selectedUserId);
      const next = { ...current, [selectedUserId]: updater(existing) };
      return next;
    });
  }

  function selectUser(userId: string) {
    setSelectedUserId(userId);
    setConversationByUser((current) =>
      current[userId] ? current : persistConversations({ ...current, [userId]: createConversationState(userId) }),
    );
  }

  async function createUser(payload: DemoUserCreatePayload) {
    setIsCreatingUser(true);
    try {
      const response = await fetch(`${ECOMMERCE_BASE_URL}/api/users/demo`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const errorPayload = (await response.json().catch(() => undefined)) as { message?: string; detail?: string } | undefined;
        throw new Error(formatErrorDetail(errorPayload?.message ?? errorPayload?.detail));
      }
      const envelope = (await response.json()) as { data: DemoUser };
      const created = envelope.data;
      setUsers((current) => [...current.filter((user) => user.profile.userId !== created.profile.userId), created]);
      setConversationByUser((current) => ({
        ...persistConversations({
          ...current,
          [created.profile.userId]: createConversationState(created.profile.userId),
        }),
      }));
      setSelectedUserId(created.profile.userId);
    } finally {
      setIsCreatingUser(false);
    }
  }

  async function sendMessage(userMessage: string, reasoningView: ReasoningView) {
    if (isSendingRef.current) {
      return;
    }
    if (!isEndpointEnabled(agentCapabilities, "chat")) {
      updateActiveConversation((current) => ({
        ...current,
        messages: [...current.messages, { role: "assistant", content: "当前 Agent 版本未开放 /chat。" }],
      }));
      return;
    }

    isSendingRef.current = true;
    setIsLoading(true);
    const userId = selectedUserId;
    const sessionId = activeConversation.sessionId;
    const effectiveReasoningView =
      reasoningView === "detailed" && !isFeatureEnabled(agentCapabilities, "reasoning_content")
        ? "summary"
        : reasoningView;
    updateActiveConversation((current) => ({
      ...current,
      messages: [...current.messages, { role: "user", content: userMessage }],
    }));

    try {
      // 商城实时槽使用商城会话身份（默认兜底 U1001 测试账号），其余用户使用演示资料。
      const isShopSlot = userId === SHOP_USER_ID;
      const shopMeta = shopMetaRef.current;
      const runtimeUserId = isShopSlot ? shopMeta.userId || "U1001" : userId;
      const runtimeNickname = isShopSlot ? shopMeta.nickname || "商城用户" : selectedUser.profile.nickname;
      const runtimeRole = isShopSlot ? "buyer" : selectedUser.profile.role;
      const runtimeMemberLevel = isShopSlot ? "gold" : selectedUser.profile.memberLevel;
      const runtimeRiskLevel = isShopSlot ? "low" : selectedUser.profile.riskLevel;
      // 调试后台用安全摘要模拟可信运行时注入；生产商城仍由登录态客服网关构造 Runtime Context。
      const runtimeContext = isFeatureEnabled(agentCapabilities, "runtime_context")
        ? await loadDemoRuntimeContext(runtimeUserId)
        : undefined;
      const chatResponse = await fetch(`${AGENT_BASE_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          runtime_user_id: runtimeUserId,
          runtime_nickname: runtimeNickname,
          runtime_role: runtimeRole,
          runtime_member_level: runtimeMemberLevel,
          runtime_risk_level: runtimeRiskLevel,
          user_message: userMessage,
          history_messages: activeConversation.messages.map((message) => ({
            role: message.role,
            content: message.content,
          })),
          reasoning_view: effectiveReasoningView,
          debug: true,
          runtime_context: runtimeContext,
        }),
      });

      if (!chatResponse.ok) {
        const errorPayload = (await chatResponse.json().catch(() => undefined)) as { detail?: string } | undefined;
        throw new Error(formatErrorDetail(errorPayload?.detail));
      }

      const payload = (await chatResponse.json()) as ChatResponse;
      setConversationByUser((current) => {
        const existing = current[userId] ?? createConversationState(userId);
        return persistConversations({
          ...current,
          [userId]: {
            ...existing,
            messages: [...existing.messages, { role: "assistant", content: payload.answer, response: payload }],
            activeResponse: payload,
            resumeResult: undefined,
          },
        });
      });

      await refreshTrace(userId, sessionId);
    } catch (error) {
      setConversationByUser((current) => {
        const existing = current[userId] ?? createConversationState(userId);
        return persistConversations({
          ...current,
          [userId]: {
            ...existing,
            messages: [
              ...existing.messages,
              { role: "assistant", content: error instanceof Error ? error.message : "Agent 请求失败" },
            ],
          },
        });
      });
    } finally {
      isSendingRef.current = false;
      setIsLoading(false);
    }
  }

  async function resumeWorkflow(decision: ChatResumeDecision, reviewerNote: string) {
    const userId = selectedUserId;
    const conversation = conversationByUser[userId];
    const workflow = conversation?.activeResponse?.session_state.workflow;
    if (!isEndpointEnabled(agentCapabilities, "chat_resume") || !isFeatureEnabled(agentCapabilities, "human_approval")) {
      updateActiveConversation((current) => ({
        ...current,
        messages: [...current.messages, { role: "assistant", content: "当前 Agent 版本未开放人工确认恢复。" }],
      }));
      return;
    }
    if (!conversation || !workflow?.workflow_id || !workflow.resume_token) {
      return;
    }

    setIsResuming(true);
    try {
      const resumeResponse = await fetch(`${AGENT_BASE_URL}/chat/resume`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: conversation.sessionId,
          workflow_id: workflow.workflow_id,
          resume_token: workflow.resume_token,
          reviewer_id: "demo-reviewer-001",
          reviewer_role: "after_sale_manager",
          decision,
          reviewer_note: reviewerNote,
        }),
      });

      if (!resumeResponse.ok) {
        const errorPayload = (await resumeResponse.json().catch(() => undefined)) as { detail?: string } | undefined;
        throw new Error(formatErrorDetail(errorPayload?.detail));
      }

      const payload = (await resumeResponse.json()) as ChatResumeResponse;
      setConversationByUser((current) => {
        const existing = current[userId] ?? createConversationState(userId);
        const activeResponse =
          existing.activeResponse && payload.session_state
            ? {
                ...existing.activeResponse,
                session_state: { ...existing.activeResponse.session_state, ...payload.session_state },
              }
            : existing.activeResponse;
        return persistConversations({
          ...current,
          [userId]: {
            ...existing,
            activeResponse,
            resumeResult: payload,
            messages: payload.answer
              ? [...existing.messages, { role: "assistant", content: payload.answer }]
              : existing.messages,
          },
        });
      });
      await refreshTrace(userId, conversation.sessionId);
    } catch (error) {
      updateActiveConversation((current) => ({
        ...current,
        messages: [
          ...current.messages,
          { role: "assistant", content: error instanceof Error ? error.message : "审批恢复请求失败" },
        ],
      }));
    } finally {
      setIsResuming(false);
    }
  }

  async function runEval() {
    if (!isEndpointEnabled(agentCapabilities, "eval_run") || !isFeatureEnabled(agentCapabilities, "evaluation")) {
      setEvalReport(undefined);
      setEvalError("当前 Agent 版本未开放 Evaluation。");
      return;
    }

    setIsEvaluating(true);
    setEvalError(undefined);
    try {
      const evalResponse = await fetch(`${AGENT_BASE_URL}/eval/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });

      if (!evalResponse.ok) {
        const errorPayload = (await evalResponse.json().catch(() => undefined)) as { detail?: string } | undefined;
        throw new Error(formatErrorDetail(errorPayload?.detail));
      }

      setEvalReport((await evalResponse.json()) as EvalRunResponse);
    } catch (error) {
      setEvalError(error instanceof Error ? error.message : "评测运行失败");
    } finally {
      setIsEvaluating(false);
    }
  }

  async function submitFeedback() {
    if (!isFeatureEnabled(agentCapabilities, "feedback_submit")) {
      setFeedbackReport(undefined);
      setFeedbackError("当前 Agent 版本未开放反馈归因。");
      return;
    }

    const userId = selectedUserId;
    const conversation = conversationByUser[userId] ?? createConversationState(userId);
    const observedAnswer =
      conversation.activeResponse?.answer ??
      [...conversation.messages].reverse().find((message) => message.role === "assistant")?.content ??
      "本轮没有可绑定的 Agent 回答。";

    setIsSubmittingFeedback(true);
    setFeedbackError(undefined);
    try {
      const feedbackResponse = await fetch(`${AGENT_BASE_URL}/feedback/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: conversation.sessionId,
          case_id: "unshipped-refund-hitl",
          rating: "negative",
          user_comment: "SO20260601090000008-a1000008 退款时，用户差评说客服没有说明人工审批。",
          observed_answer: observedAnswer,
        }),
      });

      if (!feedbackResponse.ok) {
        const errorPayload = (await feedbackResponse.json().catch(() => undefined)) as { detail?: string } | undefined;
        throw new Error(formatErrorDetail(errorPayload?.detail));
      }

      setFeedbackReport((await feedbackResponse.json()) as FeedbackSubmitResponse);
    } catch (error) {
      setFeedbackError(error instanceof Error ? error.message : "反馈提交失败");
    } finally {
      setIsSubmittingFeedback(false);
    }
  }

  async function refreshTrace(userId: string, sessionId: string) {
    if (!isEndpointEnabled(agentCapabilities, "trace") || !isFeatureEnabled(agentCapabilities, "trace")) {
      setConversationByUser((current) => {
        const existing = current[userId] ?? createConversationState(userId);
        return persistConversations({ ...current, [userId]: { ...existing, traceEvents: [] } });
      });
      return;
    }

    const traceResponse = await fetch(`${AGENT_BASE_URL}/sessions/${sessionId}/trace`);
    if (traceResponse.ok) {
      const traces = (await traceResponse.json()) as TraceEvent[];
      setConversationByUser((current) => {
        const existing = current[userId] ?? createConversationState(userId);
        return persistConversations({ ...current, [userId]: { ...existing, traceEvents: traces } });
      });
    }
  }

  return {
    users,
    selectedUser,
    selectedUserId,
    messages: activeConversation.messages,
    isLoading,
    isResuming,
    isEvaluating,
    isSubmittingFeedback,
    isCreatingUser,
    activeResponse: activeConversation.activeResponse,
    traceEvents: activeConversation.traceEvents,
    shopWatching,
    toggleShopWatching: () => setShopWatching((value) => !value),
    evalReport,
    evalError,
    feedbackReport,
    feedbackError,
    resumeResult: activeConversation.resumeResult,
    agentBaseUrl: AGENT_BASE_URL,
    agentCapabilities,
    capabilitiesError,
    selectUser,
    createUser,
    sendMessage,
    resumeWorkflow,
    runEval,
    submitFeedback,
  };
}

function persistConversations(conversations: Record<string, UserConversationState>) {
  return conversations;
}

function isEndpointEnabled(capabilities: AgentCapabilityManifest, endpoint: AgentEndpointKey) {
  return capabilities.endpoints?.[endpoint] === true;
}

function isFeatureEnabled(capabilities: AgentCapabilityManifest, feature: AgentFeatureKey) {
  return capabilities.features?.[feature] === true;
}

function formatErrorDetail(detail: unknown) {
  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }
  if (detail) {
    return JSON.stringify(detail);
  }
  return "Agent 请求失败";
}
