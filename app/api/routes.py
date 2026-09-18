import re
import logging
import asyncio
from typing import Optional
from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import PlainTextResponse, JSONResponse

from app.config import settings
from app.services.slack_service import slack_service
from app.services.agent_service import agent_service
from app.tools import all_tools
from app.db.connection import check_db_health, db_pool

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/")
async def root():
    """Root health check and status."""
    db_status = await check_db_health()
    return {
        "status": "running",
        "service": "Slack AI Agent with Google Workspace & pgvector RAG",
        "model": settings.GROQ_MODEL,
        "rag_enabled": settings.ENABLE_RAG,
        "database_connected": db_status.get("connected", False),
        "tools_count": len(all_tools),
    }


@router.get("/health")
async def health():
    """Service health check endpoint."""
    db_status = await check_db_health()
    is_healthy = bool(settings.SLACK_BOT_TOKEN) and bool(settings.GROQ_API_KEY)
    
    return {
        "status": "healthy" if is_healthy else "degraded",
        "slack_configured": bool(settings.SLACK_BOT_TOKEN),
        "groq_configured": bool(settings.GROQ_API_KEY),
        "database": db_status,
        "rag_enabled": settings.ENABLE_RAG,
        "embedding_model": settings.EMBEDDING_MODEL,
        "available_tools": [tool.name for tool in all_tools],
    }


@router.get("/db-test")
async def db_test():
    """Diagnostic endpoint to test AWS RDS PostgreSQL connection, latency, and pgvector extension."""
    start_time = asyncio.get_event_loop().time()
    try:
        pool = await db_pool.get_pool()
        if not pool:
            return JSONResponse(
                status_code=503,
                content={
                    "database": "failed",
                    "error": "Database connection pool could not be initialized",
                    "host": settings.POSTGRES_HOST,
                    "port": settings.POSTGRES_PORT,
                    "target_db": settings.POSTGRES_DB,
                    "hint": "Check DATABASE_URL, AWS RDS Security Group inbound rules (port 5432), and Public Accessibility.",
                },
            )

        async with pool.acquire() as conn:
            version = await conn.fetchval("SELECT version()")
            vector = await conn.fetchval(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            )
            # Table counts
            msg_count = await conn.fetchval("SELECT COUNT(*) FROM chat_messages")
            emb_count = await conn.fetchval("SELECT COUNT(*) FROM chat_embeddings")
            elapsed_ms = round((asyncio.get_event_loop().time() - start_time) * 1000, 2)

            return {
                "database": "connected",
                "latency_ms": elapsed_ms,
                "host": settings.POSTGRES_HOST,
                "port": settings.POSTGRES_PORT,
                "target_db": settings.POSTGRES_DB,
                "ssl_mode": settings.DB_SSLMODE,
                "postgres_version": version,
                "pgvector_installed": bool(vector),
                "pgvector_version": vector,
                "chat_messages_count": msg_count,
                "chat_embeddings_count": emb_count,
            }

    except Exception as e:
        logger.error("DB test endpoint error: %s", e)
        return JSONResponse(
            status_code=500,
            content={
                "database": "failed",
                "error": str(e),
                "error_type": type(e).__name__,
                "host": settings.POSTGRES_HOST,
                "port": settings.POSTGRES_PORT,
                "target_db": settings.POSTGRES_DB,
                "hint": "Ensure your AWS RDS Security Group allows inbound traffic on port 5432 and Publicly Accessible is enabled if connecting externally.",
            },
        )


async def handle_slack_message(
    channel_id: str,
    text: str,
    user_id: str = "user",
    thread_ts: Optional[str] = None,
    event_id: Optional[str] = None,
):
    """Background task to execute AI agent with RAG and post response directly to the chat."""
    logger.info("Processing message for channel %s (user: %s, thread: %s): %s",
                channel_id, user_id, thread_ts, text)
    try:
        reply = await agent_service.process_message(
            message=text,
            channel_id=channel_id,
            user_id=user_id,
            thread_ts=thread_ts,
            event_id=event_id,
        )
        # Reply in thread if incoming was in thread, else reply in channel
        slack_service.send_message(channel_id=channel_id, text=reply, thread_ts=thread_ts)
    except Exception as e:
        logger.exception("Error processing Slack message in background: %s", e)
        slack_service.send_message(
            channel_id=channel_id,
            text="Sorry, an unexpected error occurred while processing your request.",
            thread_ts=thread_ts,
        )


@router.post("/slack/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks):
    """Slack Events API webhook endpoint."""
    try:
        data = await request.json()
    except Exception as e:
        logger.error("Invalid JSON received at /slack/events: %s", e)
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    event_type = data.get("type")

    # 1. Handle Slack URL Verification Challenge
    if event_type == "url_verification":
        challenge = data.get("challenge", "")
        logger.info("Slack URL verification challenge answered.")
        return PlainTextResponse(content=challenge, status_code=200)

    # 2. Prevent duplicate event processing from Slack retries
    event_id = data.get("event_id")
    if slack_service.is_duplicate_event(event_id):
        logger.info("Ignoring duplicate Slack event: %s", event_id)
        return {"ok": True}

    # 3. Parse inner event
    event = data.get("event", {})
    inner_type = event.get("type")

    # Ignore messages sent by bots to avoid loops
    if event.get("bot_id") or event.get("subtype") == "bot_message":
        return {"ok": True}

    # Handle direct messages or channel messages
    if inner_type in ("message", "app_mention"):
        channel_id = event.get("channel")
        text = event.get("text", "")
        user_id = event.get("user", "unknown_user")
        thread_ts = event.get("thread_ts")  # Preserves thread context if present

        # Strip bot mention tag if present (e.g. <@U1234567>)
        clean_text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()

        if channel_id and clean_text:
            # Post directly or into thread using background task
            background_tasks.add_task(
                handle_slack_message,
                channel_id=channel_id,
                text=clean_text,
                user_id=user_id,
                thread_ts=thread_ts,
                event_id=event_id,
            )

    return {"ok": True}
