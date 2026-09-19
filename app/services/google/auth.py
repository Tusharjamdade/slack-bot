import os
import logging
from typing import Optional, List, Dict, Any
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from app.config import settings

logger = logging.getLogger(__name__)

# Standard Scopes for Google Calendar, Gmail, and User Profile
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]


from contextvars import ContextVar
from typing import Optional, List, Dict, Any, Tuple

# Context variable storing (team_id, user_id) for the active Slack/Web request
current_user_context: ContextVar[Tuple[Optional[str], Optional[str]]] = ContextVar(
    "current_user_context", default=(None, None)
)


def get_google_credentials(
    scopes: Optional[List[str]] = None,
    workspace_creds: Optional[Dict[str, Any]] = None,
    team_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Optional[Credentials]:
    """
    Retrieve and refresh Google OAuth2 credentials with strict per-user isolation.
    Resolution order:
    1. Direct workspace_creds argument if provided
    2. User-specific credentials from user_google_repo cache for (team_id, user_id)
    3. Workspace-level credentials from workspace_repo cache as fallback
    4. Local token.json / environment variables (development mode fallback)
    """
    effective_scopes = scopes or GOOGLE_SCOPES
    creds: Optional[Credentials] = None
    token_file = settings.GOOGLE_TOKEN_FILE

    # Resolve context team_id and user_id if not explicitly provided
    ctx_team, ctx_user = current_user_context.get()
    effective_team = team_id or ctx_team
    effective_user = user_id or ctx_user

    # 1. Direct workspace_creds argument
    if workspace_creds and isinstance(workspace_creds, dict):
        try:
            creds_data = dict(workspace_creds)
            creds_data.setdefault("client_id", settings.GOOGLE_CLIENT_ID)
            creds_data.setdefault("client_secret", settings.GOOGLE_CLIENT_SECRET)
            creds = Credentials.from_authorized_user_info(creds_data, effective_scopes)
        except Exception as e:
            logger.warning("Could not parse explicit Google credentials: %s", e)

    # 2. Strict Per-User Google Credentials (team_id, user_id)
    if not creds and effective_team and effective_user:
        try:
            from app.db.user_google_repo import user_google_repo
            user_acc = user_google_repo._cache.get((effective_team, effective_user))
            if user_acc and user_acc.get("google_credentials"):
                u_creds = dict(user_acc["google_credentials"])
                u_creds.setdefault("client_id", settings.GOOGLE_CLIENT_ID)
                u_creds.setdefault("client_secret", settings.GOOGLE_CLIENT_SECRET)
                creds = Credentials.from_authorized_user_info(u_creds, effective_scopes)
        except Exception as e:
            logger.debug("Could not resolve per-user Google credentials (%s, %s): %s",
                         effective_team, effective_user, e)

    # 3. Workspace-level fallback if user has not linked personal account
    if not creds and effective_team:
        try:
            from app.db.workspace_repo import workspace_repo
            ws_data = workspace_repo._cache.get(effective_team)
            if ws_data and ws_data.get("google_credentials"):
                w_creds = dict(ws_data["google_credentials"])
                w_creds.setdefault("client_id", settings.GOOGLE_CLIENT_ID)
                w_creds.setdefault("client_secret", settings.GOOGLE_CLIENT_SECRET)
                creds = Credentials.from_authorized_user_info(w_creds, effective_scopes)
        except Exception as e:
            logger.debug("Could not resolve workspace-level Google credentials for %s: %s", effective_team, e)

    # 3b. Try loading from active workspace cache in workspace_repo (any cached workspace in dev)
    if not creds:
        try:
            from app.db.workspace_repo import workspace_repo
            for ws_data in workspace_repo._cache.values():
                w_creds = ws_data.get("google_credentials")
                if w_creds and isinstance(w_creds, dict):
                    creds_data = dict(w_creds)
                    creds_data.setdefault("client_id", settings.GOOGLE_CLIENT_ID)
                    creds_data.setdefault("client_secret", settings.GOOGLE_CLIENT_SECRET)
                    creds = Credentials.from_authorized_user_info(creds_data, effective_scopes)
                    if creds:
                        break
        except Exception as e:
            logger.debug("Could not load credentials from workspace_repo cache: %s", e)

    # 2. Try loading from token.json
    if not creds and os.path.isfile(token_file):
        try:
            creds = Credentials.from_authorized_user_file(token_file, effective_scopes)
        except Exception as e:
            logger.warning("Could not read token file %s: %s", token_file, e)

    # 3. Try loading from environment variables if no file credentials found
    if not creds and settings.GOOGLE_REFRESH_TOKEN and settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET:
        try:
            creds = Credentials(
                None,
                refresh_token=settings.GOOGLE_REFRESH_TOKEN,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=settings.GOOGLE_CLIENT_ID,
                client_secret=settings.GOOGLE_CLIENT_SECRET,
                scopes=effective_scopes,
            )
        except Exception as e:
            logger.warning("Could not create credentials from environment variables: %s", e)

    # Refresh credentials if expired or not yet marked valid
    if creds and (creds.expired or not creds.valid) and creds.refresh_token:
        try:
            creds.refresh(Request())
            # Save refreshed credentials back to file if possible
            if os.path.isfile(token_file) and os.access(os.path.dirname(os.path.abspath(token_file)), os.W_OK):
                try:
                    with open(token_file, "w") as token:
                        token.write(creds.to_json())
                except Exception as save_err:
                    logger.debug("Failed saving refreshed token to %s: %s", token_file, save_err)
        except Exception as refresh_err:
            logger.error("Failed refreshing Google OAuth token: %s", refresh_err)
            return None

    return creds


def get_google_auth_link_for_current_user() -> str:
    """Return Google OAuth consent URL for the current user in current workspace."""
    ctx_team, ctx_user = current_user_context.get()
    state = f"{ctx_team or 'default'}:{ctx_user or 'user'}"
    return settings.get_google_auth_url(state=state)

