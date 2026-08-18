# Xiaohuangyu Customer Service Agent

> A customer service Agent for the Xiaohuangyu second-hand marketplace, covering product queries, order logistics, refund/return approval workflows, and promotion policy Q&A. Integrates Tool calls, RAG retrieval, Workflow/HITL human-in-the-loop approval, Trace observability, and cost governance.

[🇨🇳 中文版](README.md)

---

## 📦 Project Structure

```
smallyellowfish/
├── backend/              # Python Customer Service Agent (FastAPI)
│   ├── agents/           # Agent orchestration layer
│   ├── api/              # Route layer (chat, resume, eval, trace)
│   ├── models/           # LLM router + answer layer
│   ├── tools/            # Tool calls (order/logistics/after-sale)
│   ├── rag/              # RAG knowledge retrieval
│   ├── workflows/        # HITL workflows
│   ├── safety/           # Assertion validation
│   ├── knowledge/        # 30+ knowledge documents
│   └── evals/            # Regression evaluation
├── ecommerce-backend/    # Java E-commerce Gateway (Spring Boot)
│   ├── src/main/java/    # Business code
│   ├── frontend/         # Shop + Admin frontend
│   └── resources/        # Configuration files
├── frontend/             # Debug Console Frontend (Vite + React)
│   ├── src/              # Components (Chat, Eval, Trace)
│   └── public/           # Static assets
├── screenshots/          # Screenshots
├── .env.example          # Environment variable template (MySQL/Token)
├── app.env.example       # API configuration template (LLM Key)
├── docker-compose.yml    # Docker orchestration
└── .gitignore
```

---

## ✨ Core Features

### 🤖 Customer Service Agent (backend/)

| Feature | Description |
|---------|-------------|
| **Intent Routing** | 23 intent branches, buyer/seller role gate with bidirectional interception |
| **Tool Calls** | Order details, logistics query, cart, after-sale request - real-time facts not guessed by model |
| **RAG Retrieval** | 30+ after-sale/promotion/member rule documents, supports reranker re-ranking |
| **Workflow/HITL** | High-risk refund approval checkpoint + resume_token idempotent recovery |
| **Assertion Validation** | Workflow eligibility consistency, identity consistency, order number authenticity |
| **Human Handoff** | Python → Java gateway → Frontend orange capsule complete chain |
| **Session Persistence** | SQLite five-state persistence (chat/workflow/trace/cost/safety) |
| **Trace Observability** | Request-level Trace, cost statistics, tool call chain |
| **Regression Eval** | cases.yml 23 test cases + run_eval.py CLI |

### 🛒 E-commerce Gateway (ecommerce-backend/)

| Feature | Description |
|---------|-------------|
| **User Authentication** | Login/register, roles (buyer/seller), member levels |
| **Product Management** | Product CRUD, listing/unlisting, categories |
| **Order System** | Create order, payment, shipping, logistics query |
| **After-sale Process** | Refund request, return approval, arbitration evidence |
| **Customer Service Bridge** | POST `/api/customer-service/chat` calls Python Agent |

### 🎨 Frontend (frontend/)

| Feature | Description |
|---------|-------------|
| **Debug Console** | Chat conversation, Trace visualization, Eval regression testing |
| **Shop** | Product list, detail page, cart, checkout |
| **Admin Panel** | Product/order/user/after-sale management |

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        User                                      │
└──────────────┬──────────────────────────┬───────────────────────┘
               │                          │
     ┌─────────▼─────────┐      ┌────────▼────────┐
     │   Debug Console    │      │  Shop/Admin      │
     │  (Vite + React)    │      │ (SPA + Admin)   │
     └──────────────────┘      └────────┬────────
               │                          │
               └──────────┬───────────────┘
                          │
              ┌───────────▼───────────┐
              │   Java E-commerce GW   │
              │   (Spring Boot:8081)  │
              │  - Auth / Products /  │
              │  - Orders              │
              │  - CS session bridge   │
              └───────────┬───────────┘
                          │
              ┌───────────▼───────────┐
              │  Python CS Agent       │
              │  (FastAPI:8000)        │
              │  - Intent routing (23) │
              │  - Tool / RAG / HITL  │
              │  - Assertions / Handoff│
              └───────────┬───────────┘
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
   ┌────▼────┐      ┌────▼────┐      ┌────▼────┐
   │  LLM     │      │  Backend │      │  Knowledge│
   │(DashScope)│    │ (MySQL) │      │ (Chroma)  │
   └─────────┘      └─────────┘      └─────────
```

---

## 🚀 Quick Start

### Prerequisites

- **Python** 3.10+
- **Java** 17+ / Maven 3.8+
- **Node.js** 18+
- **Docker** (optional, for MySQL + Chroma)

### 1. Configure Environment

```bash
# Copy templates
cp .env.example .env
cp app.env.example app.env

# Edit .env (MySQL / Token)
# Edit app.env (LLM API Key)
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

### 2. Start Services

#### Option 1: Docker Compose (Recommended)

```bash
docker compose up -d
```

#### Option 2: Local Start

```bash
# 1. Start Python Agent
cd backend
python main.py

# 2. Start Java E-commerce Gateway
cd ecommerce-backend
./mvnw spring-boot:run

# 3. Start Debug Console Frontend
cd frontend
npm install
npm run dev
```

### 3. Access Services

| Service | URL | Description |
|---------|-----|-------------|
| Debug Console | http://localhost:5173 | Chat + Eval + Trace |
| Agent API | http://localhost:8000 | FastAPI (Swagger: /docs) |
| E-commerce GW | http://localhost:8081 | Spring Boot (Swagger: /swagger-ui.html) |
| Shop | http://localhost:8081/shop | Browse products + checkout |
| Admin Panel | http://localhost:8081/admin | Product/order/user management |

---

## 🧪 Evaluation & Verification

### Regression Eval

```bash
# Run 23 test cases
cd backend
python run_eval.py

# Or call API
curl -s -X POST http://127.0.0.1:8000/eval/run
```

**Expected**: `passed=23`, `failed=0`

### Manual Testing

**Scenario 1: Logistics Query**
```json
{
  "session_id": "test-logistics",
  "runtime_user_id": "U1001",
  "runtime_role": "buyer",
  "user_message": "Check logistics for SO20260602103000009-a1000009"
}
```
✅ Expected: Calls `get_order_detail` + `get_order_logistics`, returns "Shipped, in transit"

**Scenario 2: Refund Guidance**
```json
{
  "session_id": "test-refund",
  "runtime_user_id": "U1001",
  "runtime_role": "buyer",
  "user_message": "Order SO20260601090000008-a1000008 hasn't shipped, I want a refund"
}
```
✅ Expected: First calls tool to verify shipping status, then provides refund guidance

**Scenario 3: Role Gate**
```json
{
  "session_id": "test-gate",
  "runtime_user_id": "U1001",
  "runtime_role": "buyer",
  "user_message": "How do I manage my sold products"
}
```
✅ Expected: Returns role gate interception message, guides buyer to use buyer features

---

## 📊 Tech Stack

| Component | Technology |
|-----------|------------|
| **CS Agent** | Python 3.10+, FastAPI, LangChain, ChromaDB |
| **LLM** | Alibaba Cloud DashScope (Qwen3.7-Max) |
| **E-commerce GW** | Java 17, Spring Boot, MyBatis-Plus |
| **Database** | MySQL 8.0 |
| **Vector DB** | ChromaDB 1.0.8 |
| **Frontend** | React 18, Vite, TypeScript |
| **Deployment** | Docker Compose |

---

## 🔒 Security & Privacy

### Sensitive Files Excluded

The following files are excluded by `.gitignore` and **will not be committed**:

- `app.env` / `.env` — Real API Key, MySQL password, Auth Token
- `backend/data/session_store.db*` — SQLite session data
- `backend/.venv/` — Python virtual environment
- `ecommerce-backend/target/` — Java compiled artifacts
- `ecommerce-backend/frontend/dist/` — Frontend build artifacts
- `ecommerce-backend/docker/m2-cache.tar.gz` — Maven cache (151MB)

### Production Recommendations

1. **Replace demo values**: Use `.env.example` / `app.env.example` as templates, fill in real configurations
2. **Revoke API Key**: If local `app.env` contains a real Key, consider revoking and regenerating it in DashScope console
3. **Strong passwords**: Use random strings for MySQL password and Auth Token
4. **HTTPS**: Enable HTTPS in production, configure TLS certificate

---

---

## 🤝 Contributing

Welcome to submit Issues and Pull Requests!

### Development Flow

```bash
# 1. Fork the repository
# 2. Clone locally
git clone https://github.com/your-username/smallyellowfish.git
cd smallyellowfish

# 3. Create feature branch
git checkout -b feature/your-feature

# 4. Commit changes
git commit -m "feat: add xxx feature"

# 5. Push to remote
git push origin feature/your-feature

# 6. Create Pull Request
```

### Code Style

- **Python**: Follow PEP 8, use `ruff` for formatting
- **Java**: Follow Google Java Style
- **Frontend**: TypeScript strict mode, ESLint + Prettier

---

## 📄 License

This repository is licensed under the [Apache License 2.0](LICENSE).

---

## 👥 Authors

- **xiaoxiaoma0201** — Core developer

---

## 📧 Contact

- GitHub: [@xiaoxiaoma0201](https://github.com/xiaoxiaoma0201)
- Email: 1187030516@qq.com

---

## 🙏 Acknowledgments

- Alibaba Cloud DashScope — LLM service
- LangChain — Agent framework
- ChromaDB — Vector database
- Spring Boot — Java backend framework

---

*Last Updated: 2026-08-17*
