import json
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from app.db.connection import db_pool
from app.config import settings

logger = logging.getLogger(__name__)

DEFAULT_TOOLS = ["calendar", "email"]


class WorkspaceRepository:
    """Repository for Slack workspace installations, bot tokens, and tool configurations."""

    def __init__(self):
        # In-memory fallback cache to ensure local tests & resilient uptime work smoothly
        self._cache: Dict[str, Dict[str, Any]] = {}
        # Pre-seed with single-tenant bot token if present in settings
        if settings.SLACK_BOT_TOKEN:
            self._cache["default"] = {
                "team_id": "default",
                "team_name": "Default Workspace",
                "bot_token": settings.SLACK_BOT_TOKEN,
                "bot_user_id": None,
                "authed_user_id": None,
                "scope": settings.SLACK_SCOPES,
                "installed_at": datetime.now(timezone.utc).isoformat(),
                "enabled_tools": list(DEFAULT_TOOLS),
                "google_credentials": None,
                "google_user_email": None,
            }

    async def save_workspace(
        self,
        team_id: str,
        team_name: str,
        bot_token: str,
        bot_user_id: Optional[str] = None,
        authed_user_id: Optional[str] = None,
        scope: Optional[str] = None,
        enabled_tools: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Insert or update workspace in PostgreSQL and local cache."""
        tools = enabled_tools if enabled_tools is not None else list(DEFAULT_TOOLS)
        installed_at_iso = datetime.now(timezone.utc).isoformat()

        record = {
            "team_id": team_id,
            "team_name": team_name,
            "bot_token": bot_token,
            "bot_user_id": bot_user_id,
            "authed_user_id": authed_user_id,
            "scope": scope,
            "installed_at": installed_at_iso,
            "enabled_tools": tools,
            "google_credentials": self._cache.get(team_id, {}).get("google_credentials"),
            "google_user_email": self._cache.get(team_id, {}).get("google_user_email"),
        }
        self._cache[team_id] = record

        try:
            pool = await db_pool.get_pool()
            if pool:
                async with pool.acquire() as conn:
                    query = """
                    INSERT INTO workspaces (
                        team_id, team_name, bot_token, bot_user_id, authed_user_id, scope, enabled_tools
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (team_id) DO UPDATE SET
                        team_name = EXCLUDED.team_name,
                        bot_token = EXCLUDED.bot_token,
                        bot_user_id = COALESCE(EXCLUDED.bot_user_id, workspaces.bot_user_id),
                        authed_user_id = COALESCE(EXCLUDED.authed_user_id, workspaces.authed_user_id),
                        scope = COALESCE(EXCLUDED.scope, workspaces.scope),
                        enabled_tools = COALESCE(EXCLUDED.enabled_tools, workspaces.enabled_tools);
                    """
                    await conn.execute(
                        query,
                        team_id,
                        team_name,
                        bot_token,
                        bot_user_id,
                        authed_user_id,
                        scope,
                        json.dumps(tools),
                    )
                    logger.info("Saved workspace %s (%s) to PostgreSQL.", team_name, team_id)
        except Exception as e:
            logger.warning("Could not persist workspace to PostgreSQL (cached in-memory): %s", e)

        return record

    async def get_workspace(self, team_id: str) -> Optional[Dict[str, Any]]:
        """Fetch workspace details by team_id (checking DB then cache)."""
        if not team_id:
            return None

        # 1. Try DB lookup
        try:
            pool = await db_pool.get_pool()
            if pool:
                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        """
                        SELECT team_id, team_name, bot_token, bot_user_id, authed_user_id,
                               scope, installed_at, enabled_tools, google_credentials, google_user_email
                        FROM workspaces
                        WHERE team_id = $1
                        """,
                        team_id,
                    )
                    if row:
                        tools = row["enabled_tools"]
                        if isinstance(tools, str):
                            try:
                                tools = json.loads(tools)
                            except Exception:
                                tools = list(DEFAULT_TOOLS)
                        creds = row["google_credentials"]
                        if isinstance(creds, str):
                            try:
                                creds = json.loads(creds)
                            except Exception:
                                pass

                        record = {
                            "team_id": row["team_id"],
                            "team_name": row["team_name"],
                            "bot_token": row["bot_token"],
                            "bot_user_id": row["bot_user_id"],
                            "authed_user_id": row["authed_user_id"],
                            "scope": row["scope"],
                            "installed_at": row["installed_at"].isoformat() if row["installed_at"] else None,
                            "enabled_tools": tools or list(DEFAULT_TOOLS),
                            "google_credentials": creds,
                            "google_user_email": row["google_user_email"],
                        }
                        self._cache[team_id] = record
                        return record
        except Exception as e:
            logger.debug("Database lookup for workspace %s failed: %s", team_id, e)

        # 2. Return from cache or fallback
        if team_id in self._cache:
            return self._cache[team_id]

        # Single-tenant default fallback if bot token is configured
        if settings.SLACK_BOT_TOKEN:
            return {
                "team_id": team_id,
                "team_name": "Workspace",
                "bot_token": settings.SLACK_BOT_TOKEN,
                "enabled_tools": list(DEFAULT_TOOLS),
                "google_credentials": None,
                "google_user_email": None,
            }

        return None

    async def list_workspaces(self) -> List[Dict[str, Any]]:
        """List all installed workspaces."""
        results: Dict[str, Dict[str, Any]] = dict(self._cache)

        try:
            pool = await db_pool.get_pool()
            if pool:
                async with pool.acquire() as conn:
                    rows = await conn.fetch(
                        """
                        SELECT team_id, team_name, bot_token, bot_user_id, authed_user_id,
                               scope, installed_at, enabled_tools, google_credentials, google_user_email
                        FROM workspaces
                        ORDER BY installed_at DESC
                        """
                    )
                    for row in rows:
                        tools = row["enabled_tools"]
                        if isinstance(tools, str):
                            try:
                                tools = json.loads(tools)
                            except Exception:
                                tools = list(DEFAULT_TOOLS)
                        creds = row["google_credentials"]
                        if isinstance(creds, str):
                            try:
                                creds = json.loads(creds)
                            except Exception:
                                pass

                        record = {
                            "team_id": row["team_id"],
                            "team_name": row["team_name"],
                            "bot_token": row["bot_token"],
                            "bot_user_id": row["bot_user_id"],
                            "authed_user_id": row["authed_user_id"],
                            "scope": row["scope"],
                            "installed_at": row["installed_at"].isoformat() if row["installed_at"] else None,
                            "enabled_tools": tools or list(DEFAULT_TOOLS),
                            "google_credentials": creds,
                            "google_user_email": row["google_user_email"],
                        }
                        results[row["team_id"]] = record
        except Exception as e:
            logger.debug("Database list_workspaces error: %s", e)

        return list(results.values())

    async def update_workspace_tools(self, team_id: str, enabled_tools: List[str]) -> bool:
        """Update active tools for a workspace."""
        if team_id in self._cache:
            self._cache[team_id]["enabled_tools"] = enabled_tools

        try:
            pool = await db_pool.get_pool()
            if pool:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE workspaces SET enabled_tools = $1 WHERE team_id = $2",
                        json.dumps(enabled_tools),
                        team_id,
                    )
                    return True
        except Exception as e:
            logger.warning("Could not update workspace tools in PostgreSQL: %s", e)

        return team_id in self._cache

    async def update_workspace_google_credentials(
        self,
        team_id: str,
        credentials_dict: Dict[str, Any],
        email: Optional[str] = None,
    ) -> bool:
        """Store Google OAuth credentials & user email for a workspace."""
        if team_id in self._cache:
            self._cache[team_id]["google_credentials"] = credentials_dict
            self._cache[team_id]["google_user_email"] = email
            # Auto-enable google tools if not already
            current_tools = set(self._cache[team_id].get("enabled_tools", []))
            current_tools.update(["calendar", "email"])
            self._cache[team_id]["enabled_tools"] = list(current_tools)

        try:
            pool = await db_pool.get_pool()
            if pool:
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE workspaces
                        SET google_credentials = $1,
                            google_user_email = $2
                        WHERE team_id = $3
                        """,
                        json.dumps(credentials_dict),
                        email,
                        team_id,
                    )
                    return True
        except Exception as e:
            logger.warning("Could not update Google credentials in PostgreSQL: %s", e)

        return team_id in self._cache

    async def disconnect_workspace_google(self, team_id: str) -> bool:
        """Disconnect and clear Google credentials for a workspace."""
        if team_id in self._cache:
            self._cache[team_id]["google_credentials"] = None
            self._cache[team_id]["google_user_email"] = None

        try:
            pool = await db_pool.get_pool()
            if pool:
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE workspaces
                        SET google_credentials = NULL,
                            google_user_email = NULL
                        WHERE team_id = $1
                        """,
                        team_id,
                    )
                    return True
        except Exception as e:
            logger.warning("Could not disconnect Google credentials in PostgreSQL: %s", e)

        return team_id in self._cache


workspace_repo = WorkspaceRepository()
