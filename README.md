# Slack AI Agent (Google Workspace & Groq)

A modular, production-ready AI assistant inside Slack powered by LangChain and Groq LLMs, equipped with native integrations for **Google Calendar**, **Gmail**, and **Google Keep**.

---

## Features

- **Modular Architecture**: Clean separation between API routes, background workers, agent reasoning, and external integrations.
- **Productivity Superpowers**:
  - 📅 **Google Calendar**: Check schedules, list meetings, create events, and search appointments.
  - ✉️ **Gmail**: Search inbox, view message details, fetch unread emails, and send messages.
  - 📝 **Google Keep**: List notes, search checklists, create new notes, and append items.
- **Slack Integration**: URL verification, automatic retry deduplication, threaded responses, and mention parsing.
- **Deployment Ready**: Runs natively in WSL with `uv` or in isolated containers via Docker & Docker Compose.

---

## Project Structure

```text
├── app/
│   ├── api/routes.py              # FastAPI endpoints (/health, /slack/events)
│   ├── services/
│   │   ├── agent_service.py       # LangChain Tool-Calling Agent orchestrator
│   │   ├── slack_service.py       # Slack messaging & deduplication
│   │   └── google/                # Auth, Calendar, Gmail & Keep services
│   ├── tools/                     # LangChain @tool definitions
│   ├── config.py                  # Pydantic Settings configuration
│   └── main.py                    # FastAPI application factory
├── scripts/setup_google_auth.py   # One-click Google OAuth setup CLI
├── Dockerfile & docker-compose.yml# Containerization
└── main.py                        # Root runner entrypoint
```

---

## Getting Started (WSL & `uv`)

This project is optimized for fast local execution inside **WSL (Windows Subsystem for Linux)** using `uv`:

```bash
# 1. Clone or navigate to the project directory in WSL
cd /mnt/d/My\ Programs/ML/MachineLearning/slack

# 2. Create and activate a Python virtual environment
uv venv
source .venv/bin/activate

# 3. Sync dependencies from pyproject.toml
uv sync

# 4. Configure environment variables
cp .env.example .env
# Edit .env and fill in SLACK_BOT_TOKEN and GROQ_API_KEY
```

---

## Google Workspace Setup (Calendar, Gmail & Keep)

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a project.
2. Enable **Google Calendar API**, **Gmail API**, and **Google Keep API** under **APIs & Services > Library**.
3. Under **OAuth consent screen**, set user type to **External** and add your Google email to **Test users**.
4. Under **Credentials**, click **Create Credentials > OAuth client ID > Desktop app**.
5. Download the JSON and save it as `credentials.json` in the root folder.
6. Run the interactive authentication helper to generate `token.json`:
   ```bash
   uv run python scripts/setup_google_auth.py
   ```
*(For personal `@gmail.com` Keep access, you can alternatively set `GOOGLE_KEEP_USERNAME` and `GOOGLE_KEEP_PASSWORD` App Password in `.env`)*

---

## Slack App Configuration

1. Create a Slack App at [api.slack.com/apps](https://api.slack.com/apps).
2. Under **OAuth & Permissions**, add the following **Bot Token Scopes**:
   - `chat:write`, `app_mentions:read`, `channels:history`, `im:history`, `groups:history`
3. Install the app to your workspace and copy the **Bot User OAuth Token** (`xoxb-...`) to `SLACK_BOT_TOKEN` in `.env`.
4. Expose your local port `8000` via tunnel (e.g. ngrok: `ngrok http 8000`) and set **Event Subscriptions** Request URL to:
   `https://<your-domain>/slack/events`
5. Subscribe to bot events: `message.channels`, `message.im`, and `app_mention`.

---

## Running the Application

### Option 1: Run with `uv` (WSL)
```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Option 2: Run with Docker Compose
```bash
docker compose up --build -d
```

---

## Verification & Health Check

Test that your server is running and tools are loaded:
```bash
curl http://localhost:8000/health
```
You will receive:
```json
{"status":"healthy","slack_configured":true,"groq_configured":true,"available_tools":["list_calendar_events","create_calendar_event","search_calendar_events","search_emails","get_unread_emails","read_email_content","send_email","list_keep_notes","create_keep_note","append_to_keep_note"]}
```
