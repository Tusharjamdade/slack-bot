import re
import logging
from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import PlainTextResponse, JSONResponse

from app.config import settings
from app.services.slack_service import slack_service
from app.services.agent_service import agent_service
from app.tools import all_tools

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/")
async def root():
    """Root health check and status."""
    return {
        "status": "running",
        "service": "Slack AI Agent with Google Workspace",
        "model": settings.GROQ_MODEL,
        "tools_count": len(all_tools),
    }


@router.get("/health")
async def health():
    """Service health check endpoint."""
    return {
        "status": "healthy",
        "slack_configured": bool(settings.SLACK_BOT_TOKEN),
        "groq_configured": bool(settings.GROQ_API_KEY),
        "available_tools": [tool.name for tool in all_tools],
    }


def handle_slack_message(channel_id: str, text: str):
    """Background task to execute AI agent and post response directly to the chat."""
    logger.info("Processing message for channel %s: %s", channel_id, text)
    try:
        reply = agent_service.process_message(text)
        # Reply directly in the channel chat (no thread creation)
        slack_service.send_message(channel_id=channel_id, text=reply)
    except Exception as e:
        logger.exception("Error processing Slack message in background: %s", e)
        slack_service.send_message(
            channel_id=channel_id,
            text="Sorry, an unexpected error occurred while processing your request.",
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
    if event.get("bot_id"):
        return {"ok": True}

    # Handle direct messages or channel messages
    if inner_type in ("message", "app_mention"):
        channel_id = event.get("channel")
        text = event.get("text", "")

        # Strip bot mention tag if present (e.g. <@U1234567>)
        clean_text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()

        if channel_id and clean_text:
            # Post directly to the chat without creating a thread
            background_tasks.add_task(handle_slack_message, channel_id, clean_text)

    return {"ok": True}
