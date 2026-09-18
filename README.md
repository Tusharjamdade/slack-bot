# Slack AI Agent (Google Workspace, Groq & pgvector RAG)

A modular, production-grade AI assistant inside Slack powered by LangChain and Groq LLMs, equipped with:
- **Long-Term Memory & RAG**: Amazon RDS PostgreSQL with **`pgvector`** for semantic search over past conversations, decisions, and notes.
- **Adaptive Task Routing**: Smart classifier that retrieves historical context only when needed, executing direct tool actions or answers with zero latency overhead.
- **Google Workspace Productivity**: Native tools for **Google Calendar**, **Gmail**, and **Google Keep**.
- **Cloud Native Deployment**: Fully containerized and configured for **AWS ECS Fargate** and **Docker Compose**.

---

## Architecture Overview

```
                          ┌──────────────────────────┐
                          │   Slack User / Channel   │
                          └─────────────┬────────────┘
                                        │ (Webhook)
                                        ▼
                          ┌──────────────────────────┐
                          │ FastAPI  /slack/events   │
                          └─────────────┬────────────┘
                                        │ (Background Task)
                  ┌─────────────────────┴─────────────────────┐
                  │                                           │
                  ▼                                           ▼
   ┌─────────────────────────────┐             ┌─────────────────────────────┐
   │ Persist User Message to DB  │             │   Adaptive Intent Router    │
   │  & pgvector Embeddings      │             │    (Groq LLaMA Classifier)  │
   └─────────────────────────────┘             └──────────────┬──────────────┘
                                                              │
         ┌──────────────────────────────┬─────────────────────┴──────────────────────┐
         ▼                              ▼                                            ▼
┌──────────────────┐          ┌────────────────────┐                       ┌───────────────────┐
│ Direct Workspace │          │     Direct QA      │                       │  History Recall   │
│ Tool Action      │          │     / Coding       │                       │  / Hybrid Task    │
│ (Calendar/Gmail) │          │ (Immediate Answer) │                       └─────────┬─────────┘
└────────┬─────────┘          └─────────┬──────────┘                                 │
         │                              │                                            ▼
         │                              │                                  ┌───────────────────┐
         │                              │                                  │  pgvector Cosine  │
         │                              │                                  │ Similarity Search │
         │                              │                                  └─────────┬─────────┘
         │                              │                                            │ (Top-K Chunks)
         ▼                              ▼                                            ▼
   ┌───────────────────────────────────────────────────────────────────────────────────────────┐
   │                                LangChain Agent Reasoning                                  │
   │                 (Tools: Calendar, Gmail, Keep, search_chat_history)                       │
   └────────────────────────────────────────────┬──────────────────────────────────────────────┘
                                                │
                                                ▼
                               ┌─────────────────────────────────┐
                               │ Persist Assistant Reply to DB   │
                               │   & pgvector Embeddings         │
                               └────────────────┬────────────────┘
                                                │
                                                ▼
                               ┌─────────────────────────────────┐
                               │     Send Slack Reply / Thread   │
                               └─────────────────────────────────┘
```

---

## Key Features

1. **Persistent Conversation Storage & Memory**:
   - Every user message and assistant reply is recorded in PostgreSQL.
   - Embeddings are generated using **FastEmbed** (`BAAI/bge-small-en-v1.5`, 384 dimensions) via ONNX on CPU. Fast (<10ms), zero external API cost, and pre-baked in Docker.
   - HNSW index on pgvector for high-speed cosine similarity searches.
2. **Adaptive Task Routing**:
   - **Direct Workspace Actions**: (e.g. *"Schedule a meeting tomorrow at 3pm"*, *"Check my unread emails"*) directly invoke the respective tools without vector retrieval overhead.
   - **Direct QA / Code**: (e.g. *"How do I implement binary search?"*) answers immediately.
   - **History Recall**: (e.g. *"What did we decide about the database yesterday?"*, *"What was that meeting link?"*) extracts clean search keywords, retrieves top-$k$ chunks from pgvector, and injects relevant context.
   - **In-Loop Recall Tool**: The agent also possesses `search_chat_history` in its active toolbelt for dynamic multi-step retrieval.
3. **Workspace Productivity**:
   - 📅 **Google Calendar**: List, schedule, and search events.
   - ✉️ **Gmail**: Search inbox, read message details, fetch unread emails, send emails.
   - 📝 **Google Keep**: List notes, search checklists, create notes, append items.
4. **AWS ECS Fargate & RDS Ready**:
   - Multi-stage Dockerfile with non-root security.
   - Pre-warmed model weights for instant container boot.
   - Production ECS task definition with AWS Secrets Manager support.

---

## Project Structure

```text
├── app/
│   ├── api/routes.py              # FastAPI endpoints (/health, /slack/events)
│   ├── db/
│   │   ├── connection.py          # asyncpg connection pool with RDS SSL
│   │   ├── schema.py              # PostgreSQL + pgvector DDL & migrations
│   │   └── vector_store.py        # Message persistence & cosine similarity search
│   ├── services/
│   │   ├── rag/
│   │   │   ├── embedding_service.py # FastEmbed ONNX embedding generator
│   │   │   ├── intent_router.py     # Adaptive task classifier (direct vs recall)
│   │   │   └── rag_service.py       # RAG pipeline coordinator
│   │   ├── agent_service.py       # LangChain agent orchestrator with memory
│   │   ├── slack_service.py       # Slack messaging & deduplication
│   │   └── google/                # Auth, Calendar, Gmail & Keep services
│   ├── tools/                     # LangChain @tool definitions (incl. rag_tools)
│   ├── config.py                  # Pydantic Settings configuration
│   └── main.py                    # FastAPI app factory & lifespan manager
├── ecs/
│   ├── task-definition.json       # AWS ECS Fargate task definition template
│   ├── rds_setup.sql              # RDS PostgreSQL pgvector initialization script
│   ├── deploy_fargate.sh          # Bash deployment automation script
│   └── deploy_fargate.ps1         # PowerShell deployment automation script
├── Dockerfile & docker-compose.yml# Containerization & local stack
├── requirement.txt & pyproject.toml
└── main.py                        # Root runner entrypoint
```

---

## Local Development & Testing

### Option A: Local Docker Compose (Includes PostgreSQL with pgvector)

The fastest way to test the entire stack locally:

```bash
# 1. Clone or navigate to the directory
cp .env.example .env
# Fill in your SLACK_BOT_TOKEN and GROQ_API_KEY in .env

# 2. Start PostgreSQL (pgvector) and Slack Agent containers
docker compose up --build -d

# 3. View logs
docker compose logs -f slack-agent

# 4. Check health endpoint
curl http://localhost:8000/health
```

### Option B: Local Python Environment (WSL / Linux)

```bash
# 1. Create and activate virtual environment
uv venv
source .venv/bin/activate   # or .\.venv\Scripts\Activate.ps1 on Windows

# 2. Install dependencies
uv pip install -r requirement.txt

# 3. Configure environment
cp .env.example .env

# 4. Start app
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## AWS RDS PostgreSQL + pgvector Setup

1. **Create an RDS PostgreSQL Instance**:
   - Engine: **PostgreSQL 15.2+** or **16+**.
   - Instance Class: `db.t4g.micro` or `db.t4g.small` (for dev) / `db.r6g.large` (for production).
   - Storage: 20 GB+ gp3.
   - In the DB Parameter Group, ensure `rds.force_ssl = 1` (recommended).
2. **Connect & Initialize pgvector**:
   - Connect to RDS via psql:
     ```bash
     psql -h <your-rds-endpoint>.rds.amazonaws.com -U postgres -d postgres
     ```
   - Run the setup script in [`ecs/rds_setup.sql`](file:///d:/My%20Programs/ML/MachineLearning/slack/ecs/rds_setup.sql):
     ```sql
     CREATE DATABASE slackbot;
     \c slackbot
     CREATE EXTENSION IF NOT EXISTS vector;
     ```
   - The agent will automatically run table and HNSW index migrations upon first startup.

---

## AWS ECS Fargate Deployment

### 1. Store Secrets in AWS Secrets Manager

Store sensitive tokens in AWS Secrets Manager (e.g. secret name `slack-agent-secrets`):
- `SLACK_BOT_TOKEN`: `xoxb-...`
- `GROQ_API_KEY`: `gsk_...`
- `DATABASE_URL`: `postgresql://username:password@<rds-endpoint>:5432/slackbot`

### 2. Deploy with Automated Script

#### Linux / macOS:
```bash
chmod +x ecs/deploy_fargate.sh
export AWS_REGION="us-east-1"
export ECS_CLUSTER_NAME="slack-agent-cluster"
export ECS_SERVICE_NAME="slack-ai-agent-service"
./ecs/deploy_fargate.sh
```

#### Windows PowerShell:
```powershell
.\ecs\deploy_fargate.ps1 -AwsRegion "us-east-1" -EcsClusterName "slack-agent-cluster" -EcsServiceName "slack-ai-agent-service"
```

The script will automatically:
1. Create or locate the Amazon ECR repository.
2. Authenticate Docker with ECR.
3. Build the Linux/amd64 multi-stage Docker image (pre-baking the FastEmbed model).
4. Push the image to ECR.
5. Register the new task definition revision from [`ecs/task-definition.json`](file:///d:/My%20Programs/ML/MachineLearning/slack/ecs/task-definition.json).
6. Trigger a zero-downtime rolling update on your ECS Fargate service and wait for stability.

---

## Automated CI/CD (GitHub Actions)

A GitHub Actions workflow is provided in [`.github/workflows/deploy-ecr.yml`](file:///d:/My%20Programs/ML/MachineLearning/slack/.github/workflows/deploy-ecr.yml) that automatically builds and pushes the Docker image to Amazon ECR upon each commit to `master` or `main`.

### Setup GitHub Secrets

In your GitHub repository, navigate to **Settings > Secrets and variables > Actions** and add the following repository secrets:

| Secret Name | Description | Example |
| :--- | :--- | :--- |
| `AWS_ACCESS_KEY_ID` | IAM User Access Key with ECR push permissions | `AKIAIOSFODNN7EXAMPLE` |
| `AWS_SECRET_ACCESS_KEY` | IAM User Secret Access Key | `wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY` |
| `AWS_REGION` | *(Optional)* Target AWS Region | `us-east-1` (default if omitted) |
| `ECR_REPOSITORY` | *(Optional)* ECR Repository Name | `slack-ai-agent` (default if omitted) |

### IAM Permissions Required for ECR Push

Ensure your IAM user or role has the `AmazonEC2ContainerRegistryPowerUser` policy attached, or the following minimum permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage",
        "ecr:PutImage",
        "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload",
        "ecr:DescribeRepositories",
        "ecr:CreateRepository"
      ],
      "Resource": "*"
    }
  ]
}
```

Every `git push` to `master` will trigger the workflow, tagging the image with both the **Git commit SHA** and **`latest`** in Amazon ECR.

---

## Slack App Configuration

1. Create a Slack App at [api.slack.com/apps](https://api.slack.com/apps).
2. Under **OAuth & Permissions**, add Bot Scopes:
   - `chat:write`, `app_mentions:read`, `channels:history`, `im:history`, `groups:history`
3. Install the app to your workspace and copy the Bot Token (`xoxb-...`) to your environment / Secrets Manager.
4. Set **Event Subscriptions** Request URL to your ALB / API Gateway or public URL:
   `https://<your-load-balancer-domain>/slack/events`
5. Subscribe to Bot Events: `message.channels`, `message.im`, and `app_mention`.

---

## Health Check & Verification

Hit the `/health` endpoint to verify database connectivity, pgvector status, and registered tools:

```bash
curl http://localhost:8000/health
```

Sample Response:
```json
{
  "status": "healthy",
  "slack_configured": true,
  "groq_configured": true,
  "database": {
    "connected": true,
    "pgvector_installed": true,
    "latency_ms": 1.42,
    "error": null
  },
  "rag_enabled": true,
  "embedding_model": "BAAI/bge-small-en-v1.5",
  "available_tools": [
    "list_calendar_events",
    "create_calendar_event",
    "search_calendar_events",
    "search_emails",
    "get_unread_emails",
    "read_email_content",
    "send_email",
    "list_keep_notes",
    "create_keep_note",
    "append_to_keep_note",
    "search_chat_history"
  ]
}
```
