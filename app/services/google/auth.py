import os
import logging
from typing import Optional, List
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from app.config import settings

logger = logging.getLogger(__name__)

# Scopes for Google Calendar, Gmail, and Google Keep
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/keep",
]


def get_google_credentials(scopes: Optional[List[str]] = None) -> Optional[Credentials]:
    """
    Retrieve and refresh Google OAuth2 credentials.
    Looks for:
    1. Local token file (e.g. token.json)
    2. Environment variables (GOOGLE_REFRESH_TOKEN, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
    Returns Credentials instance or None if not configured.
    """
    effective_scopes = scopes or GOOGLE_SCOPES
    creds: Optional[Credentials] = None
    token_file = settings.GOOGLE_TOKEN_FILE

    # 1. Try loading from token.json
    if os.path.isfile(token_file):
        try:
            creds = Credentials.from_authorized_user_file(token_file, effective_scopes)
        except Exception as e:
            logger.warning("Could not read token file %s: %s", token_file, e)

    # 2. Try loading from environment variables if no file credentials found
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

    # Refresh credentials if expired
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            # Save refreshed credentials back to file if possible
            if os.path.exists(token_file) or os.access(os.path.dirname(os.path.abspath(token_file)), os.W_OK):
                try:
                    with open(token_file, "w") as token:
                        token.write(creds.to_json())
                except Exception as save_err:
                    logger.debug("Failed saving refreshed token to %s: %s", token_file, save_err)
        except Exception as refresh_err:
            logger.error("Failed refreshing Google OAuth token: %s", refresh_err)
            return None

    return creds
