# 小黄鱼二手电商交易平台客服 Agent
# Xiaohuangyu Customer Service Agent

> 面向小黄鱼二手电商交易平台的智能客服 Agent，覆盖商品查询、订单物流、退款/退货审批流程、活动规则问答等核心客服场景，集成 Tool 调用、RAG 知识检索、Workflow/HITL 人工审批、Trace 可观测性和成本治理能力。
>
> A customer service Agent for the Xiaohuangyu second-hand marketplace, covering product queries, order logistics, refund/return approval workflows, and promotion policy Q&A. Integrates Tool calls, RAG retrieval, Workflow/HITL human-in-the-loop approval, Trace observability, and cost governance.

📖 [English Version](README_EN.md)

---

## 📦 项目结构 / Project Structure

```
smallyellowfish/
├── backend/              # Python 客服 Agent (FastAPI)
│   ├── agents/           # Agent 编排层
│   ├── api/              # 路由层 (chat, resume, eval, trace)
│   ├── models/           # 大模型路由 + 回答层
│   ├── tools/            # 工具调用 (订单/物流/售后)
│   ├── rag/              # RAG 知识检索
│   ├── workflows/        # HITL 工作流
│   ├── safety/           # 规则断言校验
│   ├── knowledge/        # 30+ 知识文档
│   └── evals/            # 回归评测
├── ecommerce-backend/    # Java 电商网关 (Spring Boot)
│   ├── src/main/java/    # 业务代码
│   ├── frontend/         # 商城 + 管理后台前端
│   └── resources/        # 配置文件
├── frontend/             # 调试台前端 (Vite + React)
│   ├── src/              # 组件 (Chat, Eval, Trace)
│   └── public/           # 静态资源
├── screenshots/          # 截图
├── .env.example          # 环境变量模板 (MySQL/Token)
├── app.env.example       # API 配置模板 (大模型 Key)
├── docker-compose.yml    # Docker 编排
└── .gitignore
```

---

## ✨ 核心功能 / Core Features

### 🤖 智能客服 Agent (backend/)

| 功能 | 说明 |
|------|------|
| **意图路由** | 23 意图分支，买家/卖家角色门控双向拦截 |
| **Tool 调用** | 订单详情、物流查询、购物车、售后申请，实时事实不靠模型猜 |
| **RAG 知识检索** | 30+ 售后/活动/会员规则文档，支持 reranker 重排 |
| **Workflow/HITL** | 高风险退款审批 checkpoint + resume_token 幂等恢复 |
| **规则断言校验** | 工作流资格一致性、身份一致性、订单号保真 |
| **转人工标识** | Python → Java 网关 → 前端橙色胶囊完整链路 |
| **会话持久化** | SQLite 五类状态落盘 (chat/workflow/trace/cost/safety) |
| **Trace 可观测** | 请求级 Trace、成本统计、工具调用链 |
| **回归评测** | cases.yml 23 用例 + run_eval.py CLI |

### 🛒 电商网关 (ecommerce-backend/)

| 功能 | 说明 |
|------|------|
| **用户认证** | 登录/注册、角色 (buyer/seller)、会员等级 |
| **商品管理** | 商品 CRUD、上架/下架、分类 |
| **订单系统** | 创建订单、支付、发货、物流查询 |
| **售后流程** | 退款申请、退货审批、仲裁证据 |
| **客服桥接** | POST `/api/customer-service/chat` 调用 Python Agent |

### 🎨 前端 (frontend/)

| 功能 | 说明 |
|------|------|
| **调试台** | 聊天对话、Trace 可视化、Eval 回归评测 |
| **商城** | 商品列表、详情页、购物车、下单 |
| **管理后台** | 商品/订单/用户/售后管理 |

---

## 🏗️ 架构设计 / Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        用户 / User                               │
└──────────────┬──────────────────────────┬───────────────────────┘
               │                          │
     ┌─────────▼─────────┐      ┌────────▼────────┐
     │   调试台 Frontend   │      │  商城/管理后台   │
     │  (Vite + React)    │      │ (SPA + Admin)   │
     └─────────┬─────────┘      └────────┬────────
               │                          │
               └──────────┬───────────────┘
                          │
              ┌───────────▼───────────┐
              │   Java 电商网关        │
              │   (Spring Boot:8081)  │
              │  - 认证 / 商品 / 订单  │
              │  - 客服会话桥接        │
              └───────────┬───────────┘
                          │
              ┌───────────▼───────────┐
              │  Python 客服 Agent     │
              │  (FastAPI:8000)        │
              │  - 意图路由 (23 分支)  │
              │  - Tool / RAG / HITL  │
              │  - 规则断言 / 转人工   │
              └───────────┬───────────┘
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
   ┌────▼────┐      ┌────▼────┐      ┌────▼────┐
   │ 大模型   │      │ 业务后端 │      │ 知识库   │
   │ (DashScope)│    │ (MySQL) │      │ (Chroma) │
   └─────────┘      └─────────┘      └─────────
```

---

##  快速开始 / Quick Start

### 环境要求 / Prerequisites

- **Python** 3.10+
- **Java** 17+ / Maven 3.8+
- **Node.js** 18+
- **Docker** (可选，用于 MySQL + Chroma)

### 1. 配置环境变量 / Configure Environment

```bash
# 复制模板
cp .env.example .env
cp app.env.example app.env

# 编辑 .env (MySQL / Token)
# 编辑 app.env (大模型 API Key)
```

**`.env.example`**
```bash
MYSQL_DATABASE=agent_demo
MYSQL_ROOT_PASSWORD=root
AGENT_SERVICE_AUTH_TOKEN=your-secure-token-here
```

**`app.env.example`**
```bash
AGENT_OPENAI_API_KEY=sk-your-api-key-here
AGENT_OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AGENT_OPENAI_MODEL=qwen3.7-max-2026-05-17
```

### 2. 启动服务 / Start Services

#### 方式一：Docker Compose (推荐) / Docker Compose (Recommended)

```bash
docker compose up -d
```

#### 方式二：本地启动 / Local Start

```bash
# 1. 启动 Python Agent
cd backend
python main.py

# 2. 启动 Java 电商网关
cd ecommerce-backend
./mvnw spring-boot:run

# 3. 启动调试台前端
cd frontend
npm install
npm run dev
```

### 3. 访问服务 / Access Services

| 服务 | 地址 | 说明 |
|------|------|------|
| 调试台 | http://localhost:5173 | Chat + Eval + Trace |
| Agent API | http://localhost:8000 | FastAPI (Swagger: /docs) |
| 电商网关 | http://localhost:8081 | Spring Boot (Swagger: /swagger-ui.html) |
| 商城 | http://localhost:8081/shop | 商品浏览 + 下单 |
| 管理后台 | http://localhost:8081/admin | 商品/订单/用户管理 |

---

## 🧪 评测与验证 / Evaluation & Verification

### 回归评测 / Regression Eval

```bash
# 运行 23 条用例
cd backend
python run_eval.py

# 或调用 API
curl -s -X POST http://127.0.0.1:8000/eval/run
```

**预期结果**：`passed=23`, `failed=0`

### 手动测试 / Manual Testing

**场景 1：物流查询**
```json
{
  "session_id": "test-logistics",
  "runtime_user_id": "U1001",
  "runtime_role": "buyer",
  "user_message": "查一下 SO20260602103000009-a1000009 的物流"
}
```
✅ 预期：调用 `get_order_detail` + `get_order_logistics`，返回"已发货，运输中"

**场景 2：退款引导**
```json
{
  "session_id": "test-refund",
  "runtime_user_id": "U1001",
  "runtime_role": "buyer",
  "user_message": "订单 SO20260601090000008-a1000008 还没发货，我想退款"
}
```
✅ 预期：先调用工具核实发货状态，再给出退款指引

**场景 3：角色门控**
```json
{
  "session_id": "test-gate",
  "runtime_user_id": "U1001",
  "runtime_role": "buyer",
  "user_message": "我卖出的商品怎么管理"
}
```
✅ 预期：返回角色门控拦截话术，引导买家使用买家功能

---

## 📊 技术栈 / Tech Stack

| 组件 | 技术 |
|------|------|
| **客服 Agent** | Python 3.10+, FastAPI, LangChain, ChromaDB |
| **大模型** | 阿里云 DashScope (Qwen3.7-Max) |
| **电商网关** | Java 17, Spring Boot, MyBatis-Plus |
| **数据库** | MySQL 8.0 |
| **向量库** | ChromaDB 1.0.8 |
| **前端** | React 18, Vite, TypeScript |
| **部署** | Docker Compose |

---

## 🔒 安全与隐私 / Security & Privacy

### 敏感文件排除 / Sensitive Files Excluded

以下文件已通过 `.gitignore` 排除，**不会提交到仓库**：

- `app.env` / `.env` — 真实 API Key、MySQL 密码、Auth Token
- `backend/data/session_store.db*` — SQLite 会话数据
- `backend/.venv/` — Python 虚拟环境
- `ecommerce-backend/target/` — Java 编译产物
- `ecommerce-backend/frontend/dist/` — 前端构建产物
- `ecommerce-backend/docker/m2-cache.tar.gz` — Maven 缓存 (151MB)

### 生产部署建议 / Production Recommendations

1. **替换演示值**：使用 `.env.example` / `app.env.example` 作为模板，填入真实配置
2. **撤销 API Key**：如果本地 `app.env` 已存在真实 Key，建议在 DashScope 控制台撤销并重建
3. **高强度密码**：MySQL 密码、Auth Token 使用随机字符串
4. **HTTPS**：生产环境启用 HTTPS，配置 TLS 证书

---

##  贡献指南 / Contributing

欢迎提交 Issue 和 Pull Request！

### 开发流程 / Development Flow

```bash
# 1. Fork 仓库
# 2. 克隆本地
git clone https://github.com/your-username/smallyellowfish.git
cd smallyellowfish

# 3. 创建特性分支
git checkout -b feature/your-feature

# 4. 提交更改
git commit -m "feat: 添加 xxx 功能"

# 5. 推送到远程
git push origin feature/your-feature

# 6. 创建 Pull Request
```

### 代码规范 / Code Style

- **Python**：遵循 PEP 8，使用 `ruff` 格式化
- **Java**：遵循 Google Java Style
- **前端**：TypeScript strict mode，ESLint + Prettier

---

## 📄 许可证 / License

本仓库采用 [Apache License 2.0](LICENSE) 许可证。

---

## 👥 作者 / Authors

- **xiaoxiaoma0201** — 核心开发

---

## 📧 联系方式 / Contact

- GitHub: [@xiaoxiaoma0201](https://github.com/xiaoxiaoma0201)
- Email: 1187030516@qq.com

---

## 🙏 致谢 / Acknowledgments

- 阿里云 DashScope — 大模型服务
- LangChain — Agent 框架
- ChromaDB — 向量数据库
- Spring Boot — Java 后端框架

---

*最后更新 / Last Updated: 2026-08-17*
