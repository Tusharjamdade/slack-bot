# Slack AI Agent (Google Workspace, Groq & AWS RDS pgvector RAG)

A production-grade AI assistant inside Slack powered by LangChain, Groq LLMs, and AWS cloud infrastructure:
- **Long-Term Memory & RAG**: Amazon RDS PostgreSQL with **`pgvector`** for semantic search over conversations and decisions.
- **Adaptive Task Routing**: Smart classifier that routes directly to tools, direct QA, or vector retrieval with zero overhead.
- **Google Workspace**: Native tools for **Google Calendar**, **Gmail**, and **Google Keep**.
- **Automated CI/CD**: Auto-builds and pushes multi-stage Docker images to **Amazon ECR** via GitHub Actions on every commit.

---

## Architecture Flow

```
[Slack User] ──► [FastAPI /slack/events] ──► [Adaptive Intent Router]
                                                    │
         ┌────────────────────────┬─────────────────┴────────────────────────┐
         ▼                        ▼                                          ▼
[Direct Tool Action]      [Direct QA / Code]                         [pgvector RAG Recall]
 (Calendar/Gmail/Keep)      (Instant Answer)                          (FastEmbed BGE Chunks)
         │                        │                                          │
         └────────────────────────┼──────────────────────────────────────────┘
                                  ▼
                     [LangChain Agent Execution]
                                  │
                                  ▼
                   [AWS RDS PostgreSQL Persistence]
                                  │
                                  ▼
                    [Slack Response / Thread Reply]
```

---

## Core Capabilities

1. **Event-Driven Proactive Notifications**:
   - 📧 **Gmail Incoming Mail Alert**: Automatically detects newly received emails and notifies your Slack channel with sender, subject, and snippet preview.
   - 📅 **Calendar 30-Minute Meeting Alerts**: Polls upcoming events and alerts you in Slack when a meeting is starting within the next 30 minutes (including join links).
   - ⏰ **Conversational Timers & Reminders**: Ask the bot to set timers (e.g., *"set a timer for 15 minutes for deployment"* or *"remind me in 30 minutes"*), and it will proactively ping your chat when the countdown expires.
2. **pgvector Long-Term Memory**: Automatically embeds conversations with ONNX-accelerated **FastEmbed** (`BAAI/bge-small-en-v1.5`, 384-dim, CPU-native) and performs cosine similarity search via HNSW indexes.
3. **Adaptive Router & High Performance**:
   - Asynchronous, connection-pooled Slack dispatch (`httpx.AsyncClient`) avoiding event loop blockage.
   - Pre-compiled sanitization pipelines and dynamic real-time temporal grounding.
4. **Google Workspace Automation**:
   - 📅 **Calendar**: View, create, and search schedule events.
   - ✉️ **Gmail**: List unread emails, search inbox, read messages, and send replies.
   - 📝 **Google Keep**: Create, list, search, and append items to notes.
5. **Resilient Database Layer**: Async connection pool (`asyncpg`) supporting AWS RDS SSL modes (`require`), auto-reconnect, and health probes.

---

## Environment Setup (`.env`)

Create a `.env` file in the project root:

```env
SLACK_BOT_TOKEN=xoxb-your-slack-bot-token
GROQ_API_KEY=gsk_your_groq_api_key
GROQ_MODEL=openai/gpt-oss-20b

# AWS RDS PostgreSQL with pgvector (URL-encode special characters in password!)
DATABASE_URL=postgresql://postgres:<ENCODED_PASSWORD>@<rds-endpoint>.rds.amazonaws.com:5432/postgres

# AWS Credentials for ECR CI/CD
AWS_ACCESS_KEY_ID=your-aws-access-key-id
AWS_SECRET_ACCESS_KEY=your-aws-secret-access-key
AWS_REGION=ap-south-1

GOOGLE_CREDENTIALS_FILE=credentials.json
GOOGLE_TOKEN_FILE=token.json
```

> **Note**: If your database password contains characters like `#`, `!`, or `@`, URL-encode them (e.g. via `urllib.parse.quote(password, safe="")`).

---

## AWS RDS PostgreSQL + pgvector Setup

1. **Create RDS Instance**: PostgreSQL 15+ in AWS RDS (e.g. `db.t4g.micro` or `db.t4g.small`).
2. **Connectivity Settings**:
   - **Publicly Accessible**: Set to **Yes** if connecting from local environments or external servers.
   - **VPC Security Group**: Add an Inbound Rule for **PostgreSQL (Port 5432)** from `0.0.0.0/0` or your IP/ECS Security Group.
3. **Enable pgvector**:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```
   *Tables and HNSW vector indexes are auto-migrated by the app on startup.*

---

## Local Development & Docker

### Option 1: Docker Compose
```bash
# Build and run container locally
docker compose up --build -d

# Check real-time logs
docker compose logs -f slack-agent
```

### Option 2: Local Python (uv / virtualenv)
```bash
uv venv && source .venv/bin/activate    # Windows: .\.venv\Scripts\Activate.ps1
uv pip install -r requirement.txt
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## API & Health Endpoints

- **`GET /health`**: Returns system status, registered tools, and database connection latency.
- **`GET /db-test`**: Deep diagnostic testing PostgreSQL version, `pgvector` extension status, latency, and row counts.
- **`POST /slack/events`**: Slack Events API webhook receiver.

```bash
# Verify database connection and pgvector
curl http://localhost:8000/db-test
```

Sample `/db-test` output:
```json
{
  "database": "connected",
  "latency_ms": 102.8,
  "postgres": "PostgreSQL 16.3 on aarch64-unknown-linux-gnu...",
  "pgvector": "0.7.0",
  "counts": { "conversations": 4, "messages": 12 }
}
```

---

## Automated CI/CD (GitHub Actions)

The workflow at `.github/workflows/deploy-ecr.yml` triggers on push to `master`/`main`:
1. Authenticates with AWS using `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`.
2. Logs in to **Amazon ECR** in `ap-south-1`.
3. Builds the multi-stage Docker image and pre-bakes FastEmbed model weights.
4. Pushes tags `latest` and `<commit-sha>` to the ECR repository `slack-ai-agent`.

---

## Slack App Configuration

1. Create an app at [api.slack.com/apps](https://api.slack.com/apps) with Bot Scopes:
   `chat:write`, `app_mentions:read`, `channels:history`, `im:history`.
2. Set **Event Subscriptions** Request URL: `https://<your-domain>/slack/events`.
3. Subscribe to events: `app_mention`, `message.im`, `message.channels`.
