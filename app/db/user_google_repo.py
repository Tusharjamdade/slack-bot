import json
import logging
from typing import Optional, Dict, Any, Tuple
from app.db.connection import db_pool

logger = logging.getLogger(__name__)


class UserGoogleRepository:
    """Repository for per-user Google OAuth credentials strictly isolated by (team_id, user_id)."""

    def __init__(self):
        # In-memory cache: (team_id, user_id) -> record dict
        self._cache: Dict[Tuple[str, str], Dict[str, Any]] = {}

    async def get_user_google_account(
        self, team_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Retrieve Google account credentials for a specific user in a workspace."""
        if not team_id or not user_id:
            return None

        key = (team_id, user_id)
        if key in self._cache:
            return self._cache[key]

        pool = await db_pool.get_pool()
        if not pool:
            return None

        query = """
            SELECT team_id, user_id, google_user_email, google_credentials, updated_at
            FROM user_google_accounts
            WHERE team_id = $1 AND user_id = $2
        """
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(query, team_id, user_id)
                if row:
                    creds = row["google_credentials"]
                    if isinstance(creds, str):
                        try:
                            creds = json.loads(creds)
                        except Exception:
                            pass

                    record = {
                        "team_id": row["team_id"],
                        "user_id": row["user_id"],
                        "google_user_email": row["google_user_email"],
                        "google_credentials": creds,
                        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                    }
                    self._cache[key] = record
                    return record
        except Exception as e:
            logger.error("Failed to fetch user Google account (%s, %s): %s", team_id, user_id, e)

        return None

    async def save_user_google_credentials(
        self, team_id: str, user_id: str, email: str, credentials_dict: Dict[str, Any]
    ) -> bool:
        """Upsert Google OAuth credentials for a specific user in a workspace."""
        if not team_id or not user_id:
            return False

        key = (team_id, user_id)
        record = {
            "team_id": team_id,
            "user_id": user_id,
            "google_user_email": email,
            "google_credentials": credentials_dict,
        }
        self._cache[key] = record

        pool = await db_pool.get_pool()
        if not pool:
            return True

        creds_json = json.dumps(credentials_dict)
        query = """
            INSERT INTO user_google_accounts (team_id, user_id, google_user_email, google_credentials, updated_at)
            VALUES ($1, $2, $3, $4::jsonb, CURRENT_TIMESTAMP)
            ON CONFLICT (team_id, user_id)
            DO UPDATE SET
                google_user_email = EXCLUDED.google_user_email,
                google_credentials = EXCLUDED.google_credentials,
                updated_at = CURRENT_TIMESTAMP;
        """
        try:
            async with pool.acquire() as conn:
                await conn.execute(query, team_id, user_id, email, creds_json)
                logger.info("Successfully saved Google credentials for user %s in workspace %s (%s)",
                            user_id, team_id, email)
                return True
        except Exception as e:
            logger.error("Failed to save user Google credentials: %s", e)
            return False

    async def delete_user_google_credentials(self, team_id: str, user_id: str) -> bool:
        """Disconnect and remove Google OAuth credentials for a specific user."""
        key = (team_id, user_id)
        self._cache.pop(key, None)

        pool = await db_pool.get_pool()
        if not pool:
            return True

        query = "DELETE FROM user_google_accounts WHERE team_id = $1 AND user_id = $2"
        try:
            async with pool.acquire() as conn:
                await conn.execute(query, team_id, user_id)
                logger.info("Disconnected Google credentials for user %s in workspace %s", user_id, team_id)
                return True
        except Exception as e:
            logger.error("Failed to delete user Google credentials: %s", e)
            return False


user_google_repo = UserGoogleRepository()
__all__ = ["user_google_repo", "UserGoogleRepository"]
