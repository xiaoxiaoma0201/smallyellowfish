﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿import { FormEvent, useEffect, useMemo, useState } from "react";

import { EvaluationPanel } from "./components/EvaluationPanel";
import { MessageList } from "./components/MessageList";
import { SampleQuestions } from "./components/SampleQuestions";
import { TracePanel } from "./components/TracePanel";
import { getDemoScenarioSet } from "./data/demoScenarios";
import { SHOP_USER_ID, SHOP_USER_LABEL, useAgentChat } from "./hooks/useAgentChat";
import type { AgentCapabilityManifest, AgentFeatureKey, ChatResponse, DemoUser, DemoUserCreatePayload, ReasoningView } from "./types/api";

type LearningToggles = {
  tools: boolean;
  rag: boolean;
  reasoning: boolean;
};

export default function App() {
  const {
    users,
    selectedUser,
    selectedUserId,
    messages,
    isLoading,
    isResuming,
    isEvaluating,
    isSubmittingFeedback,
    isCreatingUser,
    activeResponse,
    traceEvents,
    evalReport,
    evalError,
    feedbackReport,
    feedbackError,
    resumeResult,
    agentBaseUrl,
    agentCapabilities,
    capabilitiesError,
    selectUser,
    createUser,
    sendMessage,
    resumeWorkflow,
    runEval,
    submitFeedback,
    shopWatching,
    toggleShopWatching,
  } = useAgentChat();
  const scenarioSet = useMemo(
    () => getDemoScenarioSet(selectedUserId, agentCapabilities.project?.number),
    [selectedUserId, agentCapabilities.project?.number],
  );
  const [draft, setDraft] = useState(scenarioSet.defaultQuestion);
  const [toggles, setToggles] = useState<LearningToggles>({
    tools: false,
    rag: false,
    reasoning: false,
  });
  const capabilityAvailability = {
    tools: isFeatureEnabled(agentCapabilities, "tool_calls"),
    rag: isFeatureEnabled(agentCapabilities, "rag_citations"),
    reasoning: isFeatureEnabled(agentCapabilities, "reasoning_content"),
  };
  const reasoningView: ReasoningView =
    toggles.reasoning && capabilityAvailability.reasoning
      ? "detailed"
      : (toggles.tools && capabilityAvailability.tools) || (toggles.rag && capabilityAvailability.rag)
        ? "summary"
        : "off";

  useEffect(() => {
    setDraft(scenarioSet.defaultQuestion);
  }, [scenarioSet.defaultQuestion]);

  useEffect(() => {
    setToggles((current) => ({
      tools: current.tools && learningAvailability.tools,
      rag: current.rag && learningAvailability.rag,
      reasoning: current.reasoning && learningAvailability.reasoning,
    }));
  }, [learningAvailability.tools, learningAvailability.rag, learningAvailability.reasoning]);

  function toggleLearning(key: keyof LearningToggles) {
    if (!learningAvailability[key]) {
      return;
    }
    setToggles((current) => ({ ...current, [key]: !current[key] }));
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (isLoading || !draft.trim()) {
      return;
    }
    await sendMessage(draft.trim(), reasoningView);
  }

  async function handleClarificationCandidateSelect(candidateValue: string) {
    if (isLoading || !candidateValue.trim()) {
      return;
    }
    const followUp = buildClarificationFollowUp(candidateValue.trim(), activeResponse);
    setDraft(followUp);
    await sendMessage(followUp, reasoningView);
  }

  return (
    <main className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">E-commerce Agent Debug Console</p>
          <h1>小黄鱼二手电商交易平台客服 Agent 调试后台</h1>
        </div>
        <p>围绕小黄鱼二手电商交易平台的客户服务，观察工具、RAG、工作流、HITL、Hooks、成本和评测信号如何支撑 Agent 回答。</p>
      </header>

      <AgentTargetBanner baseUrl={agentBaseUrl} capabilities={agentCapabilities} error={capabilitiesError} />

      <UserSwitcher
        users={users}
        selectedUserId={selectedUserId}
        selectedUser={selectedUser}
        isCreatingUser={isCreatingUser}
        shopWatching={shopWatching}
        onToggleShopWatching={toggleShopWatching}
        onSelectUser={selectUser}
        onCreateUser={createUser}
      />

      <section className="top-grid" aria-label="对话与调试">
        <MessageList messages={messages} isLoading={isLoading} />
        <TracePanel
          response={activeResponse}
          traces={traceEvents}
          selectedReasoningView={reasoningView}
          toggles={toggles}
          resumeResult={resumeResult}
          isResuming={isResuming}
          isChatLoading={isLoading}
          capabilities={agentCapabilities}
          onClarificationCandidateSelect={handleClarificationCandidateSelect}
          onResumeWorkflow={resumeWorkflow}
        />
      </section>

      <section className="bottom-grid" aria-label="输入与示例">
        <form className="panel composer-panel" onSubmit={handleSubmit}>
          <div className="panel-header">
            <span>输入用户问题</span>
            <small>默认就是跑通效果；按需打开学习开关后发送给 Agent</small>
          </div>

          <textarea
            id="question"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            disabled={isLoading}
            rows={4}
            placeholder="例如：SO20260601090000008-a1000008 还没发货，我现在能退款吗？"
          />

          <div className="composer-actions">
            <div className="switch-group">
              <div className="learning-switch" aria-label="Learning toggles">
                <button
                  className={toggles.tools ? "active" : ""}
                  disabled={!learningAvailability.tools}
                  onClick={() => toggleLearning("tools")}
                  type="button"
                  aria-pressed={toggles.tools}
                >
                  <span>工具调用</span>
                  <small>{learningAvailability.tools ? "显示 Action、Observation 和 Tool Trace" : "当前版本未开放"}</small>
                </button>
                <button
                  className={toggles.rag ? "active" : ""}
                  disabled={!learningAvailability.rag}
                  onClick={() => toggleLearning("rag")}
                  type="button"
                  aria-pressed={toggles.rag}
                >
                  <span>RAG 引用</span>
                  <small>{learningAvailability.rag ? "突出知识库命中和 citations" : "当前版本未开放"}</small>
                </button>
                <button
                  className={toggles.reasoning ? "active" : ""}
                  disabled={!learningAvailability.reasoning}
                  onClick={() => toggleLearning("reasoning")}
                  type="button"
                  aria-pressed={toggles.reasoning}
                >
                  <span>Reasoning</span>
                  <small>{learningAvailability.reasoning ? "展示 reasoning_content 和执行链路" : "当前版本未开放"}</small>
                </button>
              </div>
            </div>
            <button className="submit-button" disabled={isLoading} type="submit">
              {isLoading ? "处理中..." : "发送给 Agent"}
            </button>
          </div>
        </form>

        <SampleQuestions scenarioSet={scenarioSet} disabled={isLoading} onSelect={setDraft} />
      </section>

      <EvaluationPanel
        report={evalReport}
        error={evalError}
        feedbackReport={feedbackReport}
        feedbackError={feedbackError}
        isEvaluating={isEvaluating}
        isSubmittingFeedback={isSubmittingFeedback}
        capabilities={agentCapabilities}
        onRunEval={runEval}
        onSubmitFeedback={submitFeedback}
      />
    </main>
  );
}

function AgentTargetBanner({
  baseUrl,
  capabilities,
  error,
}: {
  baseUrl: string;
  capabilities: AgentCapabilityManifest;
  error?: string;
}) {
  const projectTitle = capabilities.project?.title || capabilities.agent?.version || "目标 Agent";
  const projectId = capabilities.project?.id || capabilities.agent?.version || "unknown";
  const enabledFeatures = Object.entries(capabilities.features ?? {}).filter(([, enabled]) => enabled).length;
  const totalFeatures = Object.keys(capabilities.features ?? {}).length;

  return (
    <section className="capability-banner" aria-label="当前 Agent 能力配置">
      <div>
        <strong>{projectTitle}</strong>
        <span>
          {projectId} · {baseUrl}
        </span>
      </div>
      <div className="capability-banner-meta">
        <span>{totalFeatures ? `${enabledFeatures}/${totalFeatures} 项能力开放` : "未声明能力"}</span>
        {error ? <span className="capability-warning">{error}</span> : null}
      </div>
    </section>
  );
}

function isFeatureEnabled(capabilities: AgentCapabilityManifest, feature: AgentFeatureKey) {
  return capabilities.features?.[feature] === true;
}

function buildClarificationFollowUp(candidateValue: string, response?: ChatResponse) {
  const clarificationField = stringValue((response?.clarification as Record<string, unknown> | undefined)?.clarification_field);
  const intent = response?.intent ?? stringValue(response?.session_state.intent);
  if (clarificationField === "sku") {
    return `查 ${candidateValue} 的库存和价格`;
  }
  if (intent === "refund_status_query" || intent?.includes("refund")) {
    return `查 ${candidateValue} 的退款进度`;
  }
  if (intent === "order_query" || intent?.includes("order")) {
    return `查 ${candidateValue} 的物流到哪了`;
  }
  return `我选择 ${candidateValue}`;
}

function stringValue(value: unknown) {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function UserSwitcher({
  users,
  selectedUserId,
  selectedUser,
  isCreatingUser,
  shopWatching,
  onToggleShopWatching,
  onSelectUser,
  onCreateUser,
}: {
  users: DemoUser[];
  selectedUserId: string;
  selectedUser: DemoUser;
  isCreatingUser: boolean;
  shopWatching: boolean;
  onToggleShopWatching: () => void;
  onSelectUser: (userId: string) => void;
  onCreateUser: (payload: DemoUserCreatePayload) => Promise<void>;
}) {
  const [isAdding, setIsAdding] = useState(false);
  const [form, setForm] = useState<DemoUserCreatePayload>({
    userId: "",
    nickname: "",
    role: "buyer",
    memberLevel: "normal",
    riskLevel: "low",
    preferredCategories: "",
    preferredDelivery: "",
    budgetMin: undefined,
    budgetMax: undefined,
    invoiceRequired: false,
  });
  const selectedPreferences = selectedUser.preferences;
  const roleLabel = (role: string | undefined) =>
    role === "buyer" ? "买家" : role === "seller" ? "卖家" : role ?? "";
  const memberLabel = (memberLevel: string) =>
    memberLevel === "gold" ? "金卡会员" : memberLevel === "silver" ? "银卡会员" : "普通会员";

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    if (!form.userId.trim() || !form.nickname.trim()) {
      return;
    }
    await onCreateUser({
      ...form,
      userId: form.userId.trim(),
      nickname: form.nickname.trim(),
      preferredCategories: form.preferredCategories?.trim(),
      preferredDelivery: form.preferredDelivery?.trim(),
    });
    setIsAdding(false);
    setForm({
      userId: "",
      nickname: "",
      role: "buyer",
      memberLevel: "normal",
      riskLevel: "low",
      preferredCategories: "",
      preferredDelivery: "",
      budgetMin: undefined,
      budgetMax: undefined,
      invoiceRequired: false,
    });
  }

  return (
    <section className="panel user-panel" aria-label="调试用户">
      <div className="panel-header">
        <span>调试用户</span>
        <small>切换用户会切换 Runtime Context、聊天历史和 trace</small>
      </div>
      <div className="user-layout">
        <div className="user-tabs">
          <div
            role="button"
            tabIndex={0}
            className={selectedUserId === SHOP_USER_ID ? "active shop-user" : "shop-user"}
            onClick={() => onSelectUser(SHOP_USER_ID)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                onSelectUser(SHOP_USER_ID);
              }
            }}
          >
            <strong>{SHOP_USER_LABEL}</strong>
            <small>
              {shopWatching ? "监听中 · 商城对话自动同步" : "已暂停 · 点击开启监听"}
            </small>
            <button
              type="button"
              className={`shop-toggle ${shopWatching ? "active" : ""}`}
              onClick={(event) => {
                event.stopPropagation();
                onToggleShopWatching();
              }}
            >
              {shopWatching ? "暂停监听" : "开启监听"}
            </button>
          </div>
          {users.map((user) => (
            <button
              key={user.profile.userId}
              type="button"
              className={user.profile.userId === selectedUserId ? "active" : ""}
              onClick={() => onSelectUser(user.profile.userId)}
            >
              <strong>{user.profile.nickname}</strong>
              <small>
                {user.profile.userId} · {roleLabel(user.profile.role) || "未设角色"} ·{" "}
                {memberLabel(user.profile.memberLevel)} · risk {user.profile.riskLevel}
              </small>
            </button>
          ))}
          <button type="button" className={isAdding ? "active add-user" : "add-user"} onClick={() => setIsAdding((value) => !value)}>
            <strong>新增用户</strong>
            <small>写入模拟电商后端</small>
          </button>
        </div>
        <div className="user-summary">
          {selectedUserId === SHOP_USER_ID ? (
            <>
              <strong>{SHOP_USER_LABEL} · 商城用户会话</strong>
              <span>
                {shopWatching
                  ? "正在监听商城客服会话：商城端的对话、工具调用、RAG 引用、Reasoning 会实时同步到本对话区与右侧调试面板。"
                  : "监听已暂停。点击「开启监听」恢复商城对话自动同步。"}
              </span>
            </>
          ) : (
            <>
              <strong>
                {selectedUser.profile.nickname} / {selectedUser.profile.userId}
              </strong>
              <span>
                角色：{roleLabel(selectedUser.profile.role) || "未设置"} · 会员：
                {memberLabel(selectedUser.profile.memberLevel)} · 偏好：
                {selectedPreferences.preferredCategories || "未设置"} · 配送：
                {selectedPreferences.preferredDelivery || "未设置"} · 预算：
                {formatBudget(selectedPreferences.budgetMin, selectedPreferences.budgetMax)}
              </span>
            </>
          )}
        </div>
      </div>
      {isAdding ? (
        <form className="new-user-form" onSubmit={handleCreate}>
          <input
            value={form.userId}
            onChange={(event) => setForm((current) => ({ ...current, userId: event.target.value }))}
            placeholder="用户 ID，例如 U2001"
          />
          <input
            value={form.nickname}
            onChange={(event) => setForm((current) => ({ ...current, nickname: event.target.value }))}
            placeholder="昵称"
          />
          <select value={form.role ?? "buyer"} onChange={(event) => setForm((current) => ({ ...current, role: event.target.value }))}>
            <option value="buyer">买家</option>
            <option value="seller">卖家</option>
          </select>
          <select value={form.memberLevel} onChange={(event) => setForm((current) => ({ ...current, memberLevel: event.target.value }))}>
            <option value="normal">普通会员</option>
            <option value="silver">银卡会员</option>
            <option value="gold">金卡会员</option>
          </select>
          <select value={form.riskLevel} onChange={(event) => setForm((current) => ({ ...current, riskLevel: event.target.value }))}>
            <option value="low">low</option>
            <option value="medium">medium</option>
            <option value="high">high</option>
          </select>
          <input
            value={form.preferredCategories}
            onChange={(event) => setForm((current) => ({ ...current, preferredCategories: event.target.value }))}
            placeholder="偏好品类"
          />
          <input
            value={form.preferredDelivery}
            onChange={(event) => setForm((current) => ({ ...current, preferredDelivery: event.target.value }))}
            placeholder="配送偏好"
          />
          <input
            type="number"
            value={form.budgetMin ?? ""}
            onChange={(event) =>
              setForm((current) => ({ ...current, budgetMin: event.target.value ? Number(event.target.value) : undefined }))
            }
            placeholder="预算下限"
          />
          <input
            type="number"
            value={form.budgetMax ?? ""}
            onChange={(event) =>
              setForm((current) => ({ ...current, budgetMax: event.target.value ? Number(event.target.value) : undefined }))
            }
            placeholder="预算上限"
          />
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={Boolean(form.invoiceRequired)}
              onChange={(event) => setForm((current) => ({ ...current, invoiceRequired: event.target.checked }))}
            />
            <span>偏好电子发票</span>
          </label>
          <button type="submit" disabled={isCreatingUser}>
            {isCreatingUser ? "创建中..." : "创建用户"}
          </button>
        </form>
      ) : null}
    </section>
  );
}

function formatBudget(min?: number | null, max?: number | null) {
  if (typeof min === "number" && typeof max === "number") {
    return `${min}-${max}`;
  }
  if (typeof min === "number") {
    return `${min}+`;
  }
  if (typeof max === "number") {
    return `<=${max}`;
  }
  return "未设置";
}
