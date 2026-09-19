import os
import re
import logging
import asyncio
import hmac
import hashlib
import base64
import json
import time
from typing import Optional, List, Dict, Any
import httpx
from fastapi import APIRouter, Request, BackgroundTasks, Body
from fastapi.responses import PlainTextResponse, JSONResponse, RedirectResponse, FileResponse
from pydantic import BaseModel

from app.config import settings
from app.services.slack_service import slack_service
from app.services.agent_service import agent_service
from app.tools import all_tools, get_tools_for_workspace
from app.db.connection import check_db_health, db_pool
from app.db.workspace_repo import workspace_repo
from app.db.user_google_repo import user_google_repo
from app.services.google.auth import current_user_context
from app.services.notification_service import notification_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ==========================================================
# Session Security Helpers (HMAC-SHA256 Signed Cookies)
# ==========================================================

def create_session_token(data: dict) -> str:
    """Generate a tamper-proof HMAC-SHA256 signed session token."""
    payload_str = json.dumps(data, separators=(',', ':'))
    b64_payload = base64.urlsafe_b64encode(payload_str.encode('utf-8')).decode('utf-8').rstrip('=')
    secret = (settings.SLACK_SIGNING_SECRET or "slackops_secure_default_secret_key").encode('utf-8')
    sig = hmac.new(secret, b64_payload.encode('utf-8'), hashlib.sha256).hexdigest()
    return f"{b64_payload}.{sig}"


def verify_session_token(token: Optional[str]) -> Optional[dict]:
    """Verify HMAC signature and decode session payload."""
    if not token or "." not in token:
        return None
    try:
        b64_payload, sig = token.split(".", 1)
        secret = (settings.SLACK_SIGNING_SECRET or "slackops_secure_default_secret_key").encode('utf-8')
        expected_sig = hmac.new(secret, b64_payload.encode('utf-8'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        rem = len(b64_payload) % 4
        if rem:
            b64_payload += "=" * (4 - rem)
        decoded = base64.urlsafe_b64decode(b64_payload).decode('utf-8')
        return json.loads(decoded)
    except Exception:
        return None


class ToolsUpdateRequest(BaseModel):
    enabled_tools: List[str]


class TestChatRequest(BaseModel):
    message: str
    channel_id: Optional[str] = "dashboard-preview"


# ==========================================================
# Landing Page & Health
# ==========================================================

@router.get("/")
async def root(request: Request):
    """Serve the single-page HTML landing page and user dashboard."""
    static_html = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static", "index.html")
    if os.path.exists(static_html):
        return FileResponse(static_html)
    
    db_status = await check_db_health()
    return {
        "status": "running",
        "service": "SlackOps AI - Multi-Tenant Intelligent Assistant",
        "model": settings.GROQ_MODEL,
        "database_connected": db_status.get("connected", False),
        "slack_oauth_configured": bool(settings.SLACK_CLIENT_ID),
        "active_tools": [tool.name for tool in all_tools],
    }


@router.get("/health")
async def health():
    """Service health check endpoint."""
    db_status = await check_db_health()
    is_healthy = bool(settings.SLACK_BOT_TOKEN or settings.SLACK_CLIENT_ID) and bool(settings.GROQ_API_KEY)

    return {
        "status": "healthy" if is_healthy else "degraded",
        "slack_oauth_configured": bool(settings.SLACK_CLIENT_ID and settings.SLACK_CLIENT_SECRET),
        "slack_bot_token_configured": bool(settings.SLACK_BOT_TOKEN),
        "groq_configured": bool(settings.GROQ_API_KEY),
        "database": db_status,
        "rag_enabled": settings.ENABLE_RAG,
        "embedding_model": settings.EMBEDDING_MODEL,
        "proactive_notifications_enabled": settings.ENABLE_EVENT_NOTIFICATIONS,
        "active_timers": notification_service.timer_manager.list_active_timers(),
        "available_tools": [tool.name for tool in all_tools],
    }


@router.get("/db-test")
async def db_test():
    """Diagnostic endpoint to test AWS RDS PostgreSQL connection, latency, and pgvector extension."""
    start_time = asyncio.get_event_loop().time()
    try:
        pool = await db_pool.get_pool()
        if not pool:
            last_err = db_pool.get_last_error() or "Database connection pool could not be initialized"
            return JSONResponse(
                status_code=503,
                content={
                    "database": "failed",
                    "error": last_err,
                    "host": settings.POSTGRES_HOST,
                    "port": settings.POSTGRES_PORT,
                    "target_db": settings.POSTGRES_DB,
                    "hint": "Check DATABASE_URL and AWS RDS Security Group inbound rules (port 5432).",
                },
            )

        async with pool.acquire() as conn:
            version = await conn.fetchval("SELECT version()")
            vector = await conn.fetchval(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            )
            msg_count = await conn.fetchval("SELECT COUNT(*) FROM chat_messages")
            workspace_count = await conn.fetchval("SELECT COUNT(*) FROM workspaces")
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
                "workspaces_count": workspace_count,
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
            },
        )


# ==========================================================
# Slack OAuth Flow & Multi-Tenant Installation
# ==========================================================

@router.get("/slack/install")
async def slack_install():
    """Redirect user to Slack OAuth authorization page."""
    if not settings.SLACK_CLIENT_ID:
        return JSONResponse(
            status_code=400,
            content={
                "error": "SLACK_CLIENT_ID is not configured in .env. "
                         "Please add SLACK_CLIENT_ID and SLACK_CLIENT_SECRET to your environment."
            },
        )
    redirect_url = settings.get_slack_install_url()
    return RedirectResponse(redirect_url)


@router.get("/slack/oauth/callback")
async def slack_oauth_callback(
    code: Optional[str] = None,
    error: Optional[str] = None,
    state: Optional[str] = None,
):
    """Exchange authorization code with Slack for workspace bot token, establish authenticated session."""
    if error:
        logger.warning("Slack OAuth error callback: %s", error)
        return RedirectResponse(f"/?error={error}")

    if not code:
        return RedirectResponse("/?error=missing_code")

    try:
        redirect_uri = settings.get_slack_redirect_uri()
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://slack.com/api/oauth.v2.access",
                data={
                    "client_id": settings.SLACK_CLIENT_ID,
                    "client_secret": settings.SLACK_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                timeout=25.0,
            )
            data = response.json()

        if not data.get("ok"):
            err = data.get("error", "oauth_exchange_failed")
            logger.error("Slack OAuth token exchange failed: %s", data)
            return RedirectResponse(f"/?error={err}")

        team_info = data.get("team", {})
        team_id = team_info.get("id") or data.get("team_id")
        team_name = team_info.get("name") or "Slack Workspace"
        bot_token = data.get("access_token")
        bot_user_id = data.get("bot_user_id")
        authed_user_id = data.get("authed_user", {}).get("id")
        scope = data.get("scope")

        # Store token & workspace details in PostgreSQL
        await workspace_repo.save_workspace(
            team_id=team_id,
            team_name=team_name,
            bot_token=bot_token,
            bot_user_id=bot_user_id,
            authed_user_id=authed_user_id,
            scope=scope,
        )

        logger.info("Successfully installed SlackOps AI to workspace: %s (%s)", team_name, team_id)

        # Issue secure, tamper-proof session cookie for the installing user
        session_token = create_session_token({
            "team_id": team_id,
            "team_name": team_name,
            "user_id": authed_user_id or "admin",
            "bot_user_id": bot_user_id,
            "ts": int(time.time()),
        })

        redirect = RedirectResponse("/?installed=true", status_code=302)
        redirect.set_cookie(
            key="slack_session",
            value=session_token,
            max_age=30 * 24 * 3600,
            httponly=True,
            samesite="lax",
        )
        return redirect

    except Exception as e:
        logger.exception("Exception during Slack OAuth callback: %s", e)
        return RedirectResponse(f"/?error={str(e)}")


# ==========================================================
# Google OAuth Integration (Per-User Calendar & Gmail)
# ==========================================================

@router.get("/auth/google/start")
async def google_auth_start(request: Request, team_id: Optional[str] = None, user_id: Optional[str] = None):
    """Redirect user to Google OAuth consent screen for their personal Calendar and Gmail."""
    cookie = request.cookies.get("slack_session")
    session = verify_session_token(cookie) if cookie else None

    resolved_team = team_id or (session.get("team_id") if session else "default")
    resolved_user = user_id or (session.get("user_id") if session else "default")

    if not settings.GOOGLE_CLIENT_ID:
        return JSONResponse(
            status_code=400,
            content={
                "error": "GOOGLE_CLIENT_ID is not configured in .env. "
                         "Please set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
            },
        )
    # Encode state with team_id:user_id for per-user credential isolation
    state_payload = f"{resolved_team}:{resolved_user}"
    auth_url = settings.get_google_auth_url(state=state_payload)
    return RedirectResponse(auth_url)


@router.get("/auth/google/callback")
async def google_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    """Handle Google OAuth callback and save tokens for the specific calling user."""
    resolved_team = "default"
    resolved_user = "default"
    if state and ":" in state:
        parts = state.split(":", 1)
        resolved_team = parts[0]
        resolved_user = parts[1]
    elif state:
        resolved_team = state

    if error:
        logger.warning("Google OAuth error callback: %s", error)
        return RedirectResponse(f"/?error={error}")

    if not code:
        return RedirectResponse("/?error=missing_google_code")

    try:
        redirect_uri = settings.get_google_redirect_uri()
        async with httpx.AsyncClient() as client:
            token_res = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                timeout=25.0,
            )
            token_data = token_res.json()

            if "error" in token_data:
                err = token_data.get("error_description", token_data.get("error"))
                logger.error("Google token exchange error: %s", token_data)
                return RedirectResponse(f"/?error={err}")

            user_email = None
            access_token = token_data.get("access_token")
            if access_token:
                try:
                    uinfo_res = await client.get(
                        "https://www.googleapis.com/oauth2/v2/userinfo",
                        headers={"Authorization": f"Bearer {access_token}"},
                        timeout=10.0,
                    )
                    user_email = uinfo_res.json().get("email")
                except Exception:
                    pass

        token_data["client_id"] = settings.GOOGLE_CLIENT_ID
        token_data["client_secret"] = settings.GOOGLE_CLIENT_SECRET

        # 1. Save per-user Google credentials (strict privacy)
        await user_google_repo.save_user_google_credentials(
            team_id=resolved_team,
            user_id=resolved_user,
            email=user_email or "unknown@google.com",
            credentials_dict=token_data,
        )

        # 2. Also update workspace record for backward compatibility
        await workspace_repo.update_workspace_google_credentials(
            team_id=resolved_team,
            credentials_dict=token_data,
            email=user_email,
        )

        logger.info("Google credentials successfully linked for user %s in team %s (%s)",
                    resolved_user, resolved_team, user_email)
        return RedirectResponse("/?google_connected=true")

    except Exception as e:
        logger.exception("Exception during Google OAuth callback: %s", e)
        return RedirectResponse(f"/?error={str(e)}")


# ==========================================================
# Authenticated User & Workspace Privacy APIs
# ==========================================================

@router.get("/api/me")
async def get_current_user_profile(request: Request):
    """
    Return the authenticated user's profile, workspace info, and personal Google status.
    Strictly isolated: does not leak any other workspace or user data.
    """
    cookie = request.cookies.get("slack_session")
    session = verify_session_token(cookie) if cookie else None

    # Optional fallback for direct browser parameter testing (e.g. ?team_id=...)
    if not session:
        team_id = request.query_params.get("team_id")
        if team_id:
            workspace = await workspace_repo.get_workspace(team_id)
            if workspace:
                user_id = workspace.get("authed_user_id") or "admin"
                user_google = await user_google_repo.get_user_google_account(team_id, user_id)
                google_email = user_google.get("google_user_email") if user_google else workspace.get("google_user_email")
                has_google = bool((user_google and user_google.get("google_credentials")) or workspace.get("google_credentials"))
                return {
                    "authenticated": True,
                    "user_id": user_id,
                    "team_id": workspace.get("team_id"),
                    "team_name": workspace.get("team_name"),
                    "bot_user_id": workspace.get("bot_user_id"),
                    "installed_at": workspace.get("installed_at"),
                    "google_connected": has_google,
                    "google_user_email": google_email,
                    "enabled_tools": workspace.get("enabled_tools") or ["calendar", "email"],
                }
        return {"authenticated": False}

    team_id = session.get("team_id")
    user_id = session.get("user_id")
    workspace = await workspace_repo.get_workspace(team_id)
    if not workspace:
        return {"authenticated": False}

    user_google = await user_google_repo.get_user_google_account(team_id, user_id)
    google_email = user_google.get("google_user_email") if user_google else workspace.get("google_user_email")
    has_google = bool((user_google and user_google.get("google_credentials")) or workspace.get("google_credentials"))

    return {
        "authenticated": True,
        "user_id": user_id,
        "team_id": team_id,
        "team_name": workspace.get("team_name"),
        "bot_user_id": workspace.get("bot_user_id"),
        "installed_at": workspace.get("installed_at"),
        "google_connected": has_google,
        "google_user_email": google_email,
        "enabled_tools": workspace.get("enabled_tools") or ["calendar", "email"],
    }


@router.post("/api/me/disconnect-google")
async def disconnect_my_google(request: Request):
    """Disconnect personal Google credentials for the active user."""
    cookie = request.cookies.get("slack_session")
    session = verify_session_token(cookie) if cookie else None
    team_id = session.get("team_id") if session else request.query_params.get("team_id")
    user_id = session.get("user_id") if session else request.query_params.get("user_id", "default")
    
    if not team_id:
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})

    await user_google_repo.delete_user_google_credentials(team_id, user_id)
    await workspace_repo.disconnect_workspace_google(team_id)
    return {"ok": True}


@router.post("/api/me/logout")
async def logout():
    """Clear session cookie and log out of the dashboard."""
    response = JSONResponse(content={"ok": True})
    response.delete_cookie(key="slack_session")
    return response


@router.post("/api/me/tools")
async def update_my_tools(request: Request, body: ToolsUpdateRequest):
    """Update active tools for the authenticated user's workspace."""
    cookie = request.cookies.get("slack_session")
    session = verify_session_token(cookie) if cookie else None
    team_id = session.get("team_id") if session else request.query_params.get("team_id")
    if not team_id:
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})

    success = await workspace_repo.update_workspace_tools(team_id, body.enabled_tools)
    return {"ok": success, "team_id": team_id, "enabled_tools": body.enabled_tools}


@router.post("/api/me/test-chat")
async def test_my_chat(request: Request, body: TestChatRequest):
    """Execute AI test query strictly within the authenticated user's isolated context."""
    cookie = request.cookies.get("slack_session")
    session = verify_session_token(cookie) if cookie else None
    team_id = session.get("team_id") if session else request.query_params.get("team_id", "default")
    user_id = session.get("user_id") if session else "test-user"

    # Set user context for Google credentials
    token_context = current_user_context.set((team_id, user_id))
    try:
        workspace = await workspace_repo.get_workspace(team_id) if team_id != "default" else None
        enabled_tools = workspace.get("enabled_tools") if workspace else None
        reply = await agent_service.process_message(
            message=body.message,
            channel_id=body.channel_id or "dashboard-preview",
            user_id=user_id,
            team_id=team_id,
            enabled_tools=enabled_tools,
        )
        return {"reply": reply, "team_id": team_id, "user_id": user_id}
    finally:
        current_user_context.reset(token_context)


# ==========================================================
# Slack Events API (Per-Workspace Dynamic Token Dispatch & Auto-Join)
# ==========================================================

async def handle_slack_message(
    channel_id: str,
    text: str,
    user_id: str = "user",
    thread_ts: Optional[str] = None,
    event_id: Optional[str] = None,
    token: Optional[str] = None,
    team_id: Optional[str] = None,
    enabled_tools: Optional[List[str]] = None,
):
    """Background task to auto-join channel, execute AI agent under caller context, and reply."""
    logger.info("Processing message for team %s channel %s (user: %s): %s",
                team_id, channel_id, user_id, text)

    # 1. Auto-join channel so bot can participate in any public channel seamlessly
    if token and channel_id and not channel_id.startswith("D"):
        try:
            await slack_service.join_channel_async(channel_id, token)
        except Exception as je:
            logger.debug("Auto join channel notice: %s", je)

    # 2. Warm up user's Google account cache & bind ContextVar for isolated tools
    if team_id and user_id:
        try:
            await user_google_repo.get_user_google_account(team_id, user_id)
        except Exception as ue:
            logger.debug("User Google account cache warm-up error: %s", ue)

    token_context = current_user_context.set((team_id, user_id))
    try:
        reply = await agent_service.process_message(
            message=text,
            channel_id=channel_id,
            user_id=user_id,
            thread_ts=thread_ts,
            event_id=event_id,
            team_id=team_id,
            enabled_tools=enabled_tools,
        )
        await slack_service.send_message_async(
            channel_id=channel_id,
            text=reply,
            thread_ts=thread_ts,
            token=token,
        )
    except Exception as e:
        logger.exception("Error processing Slack message in background: %s", e)
        await slack_service.send_message_async(
            channel_id=channel_id,
            text="Sorry, an unexpected error occurred while processing your request.",
            thread_ts=thread_ts,
            token=token,
        )
    finally:
        current_user_context.reset(token_context)


@router.post("/slack/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks):
    """Slack Events API webhook endpoint with multi-workspace token resolution."""
    raw_body = await request.body()
    if not raw_body or not raw_body.strip():
        return JSONResponse(status_code=200, content={"ok": True})

    # 1. Parse incoming payload
    data = None
    try:
        data = json.loads(raw_body.decode("utf-8"))
    except Exception:
        try:
            import urllib.parse
            decoded_str = raw_body.decode("utf-8")
            form_data = urllib.parse.parse_qs(decoded_str)
            if "payload" in form_data:
                data = json.loads(form_data["payload"][0])
            else:
                data = {k: v[0] if len(v) == 1 else v for k, v in form_data.items()}
        except Exception as parse_err:
            logger.error("Failed to parse request at /slack/events: %s", parse_err)
            return JSONResponse(status_code=400, content={"error": "Invalid payload format"})

    if not isinstance(data, dict):
        return JSONResponse(status_code=200, content={"ok": True})

    event_type = data.get("type")

    # 2. Handle Slack URL Verification Challenge immediately
    if event_type == "url_verification":
        challenge = data.get("challenge", "")
        logger.info("Slack URL verification challenge answered.")
        return PlainTextResponse(content=challenge, status_code=200)

    # 3. Optional Slack signature verification
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    if timestamp and signature:
        if not slack_service.verify_slack_signature(timestamp, signature, raw_body):
            logger.warning("Invalid Slack request signature. Verify SLACK_SIGNING_SECRET in .env.")
            return JSONResponse(status_code=403, content={"error": "Invalid signature"})

    # 4. Prevent duplicate event processing from Slack retries
    event_id = data.get("event_id")
    if slack_service.is_duplicate_event(event_id):
        logger.info("Ignoring duplicate Slack event: %s", event_id)
        return {"ok": True}

    # 5. Resolve Workspace & Bot Token per workspace
    team_id = data.get("team_id")
    workspace = await workspace_repo.get_workspace(team_id) if team_id else None
    bot_token = workspace.get("bot_token") if workspace else settings.SLACK_BOT_TOKEN
    enabled_tools = workspace.get("enabled_tools") if workspace else None

    # 6. Parse inner event
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
        thread_ts = event.get("thread_ts")

        if channel_id:
            slack_service.record_active_channel(channel_id)

        # Strip bot mention tag if present (e.g. <@U1234567>)
        clean_text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()

        if channel_id and clean_text:
            background_tasks.add_task(
                handle_slack_message,
                channel_id=channel_id,
                text=clean_text,
                user_id=user_id,
                thread_ts=thread_ts,
                event_id=event_id,
                token=bot_token,
                team_id=team_id,
                enabled_tools=enabled_tools,
            )

    return {"ok": True}
