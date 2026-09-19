import logging
from collections import OrderedDict
from typing import Optional, Dict, Any
import httpx
import requests

from app.config import settings

logger = logging.getLogger(__name__)


class SlackService:
    """Service to handle Slack API communications, event deduplication, and proactive notifications."""

    def __init__(self, max_dedup_cache: int = 1000):
        self.api_url = "https://slack.com/api/chat.postMessage"
        self._processed_events: OrderedDict[str, bool] = OrderedDict()
        self.max_dedup_cache = max_dedup_cache
        self._async_client: Optional[httpx.AsyncClient] = None
        self.last_active_channel_id: Optional[str] = None

    async def get_async_client(self) -> httpx.AsyncClient:
        """Return cached or newly initialized async httpx client with connection pooling."""
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
            )
        return self._async_client

    async def close(self) -> None:
        """Close underlying async HTTP client on application shutdown."""
        if self._async_client and not self._async_client.is_closed:
            await self._async_client.aclose()
            self._async_client = None

    def record_active_channel(self, channel_id: str) -> None:
        """Record the latest active channel ID from incoming user interactions."""
        if channel_id:
            self.last_active_channel_id = channel_id

    def get_target_channel(self, fallback_channel: Optional[str] = None) -> Optional[str]:
        """Determine target channel for proactive alerts: fallback -> DEFAULT_SLACK_CHANNEL -> last active."""
        if fallback_channel:
            return fallback_channel
        if settings.DEFAULT_SLACK_CHANNEL:
            return settings.DEFAULT_SLACK_CHANNEL
        return self.last_active_channel_id

    def is_duplicate_event(self, event_id: Optional[str]) -> bool:
        """Check if an event ID was already processed and record it."""
        if not event_id:
            return False

        if event_id in self._processed_events:
            return True

        self._processed_events[event_id] = True
        # Evict oldest events if cache exceeds limit
        if len(self._processed_events) > self.max_dedup_cache:
            self._processed_events.popitem(last=False)

        return False

    def verify_slack_signature(self, timestamp: str, signature: str, body: bytes) -> bool:
        """Verify Slack request signature with HMAC SHA256 using SLACK_SIGNING_SECRET."""
        signing_secret = settings.SLACK_SIGNING_SECRET.strip() if settings.SLACK_SIGNING_SECRET else ""
        # If secret is unset or a placeholder, skip verification in development mode
        if not signing_secret or signing_secret.startswith("your-") or "placeholder" in signing_secret.lower():
            logger.debug("Skipping Slack signature verification (SLACK_SIGNING_SECRET is empty or placeholder).")
            return True

        import hmac
        import hashlib
        import time

        try:
            # Check for replay attacks (within 5 minutes)
            if abs(time.time() - float(timestamp)) > 60 * 5:
                logger.warning("Slack event timestamp is older than 5 minutes.")
                return False

            sig_basestring = f"v0:{timestamp}:".encode("utf-8") + body
            computed = "v0=" + hmac.new(
                signing_secret.encode("utf-8"),
                sig_basestring,
                hashlib.sha256,
            ).hexdigest()
            return hmac.compare_digest(computed, signature)
        except Exception as e:
            logger.error("Error verifying Slack signature: %s", e)
            return False

    async def send_message_async(
        self,
        channel_id: str,
        text: str,
        thread_ts: Optional[str] = None,
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a message to a Slack channel or thread asynchronously using workspace token or default."""
        auth_token = token or settings.SLACK_BOT_TOKEN
        headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {
            "channel": channel_id,
            "text": text,
        }
        if thread_ts:
            payload["thread_ts"] = thread_ts

        try:
            client = await self.get_async_client()
            response = await client.post(
                self.api_url,
                headers=headers,
                json=payload,
            )
            result = response.json()

            if not result.get("ok"):
                logger.error("Slack API error response: %s", result)
            else:
                logger.info("Slack message successfully sent to channel %s", channel_id)

            return result
        except Exception as e:
            logger.error("Failed to send Slack message asynchronously: %s", e)
            return {"ok": False, "error": str(e)}

    async def join_channel_async(self, channel_id: str, token: Optional[str] = None) -> Dict[str, Any]:
        """Join a public Slack channel so the bot can be added to any channel in the workspace."""
        auth_token = token or settings.SLACK_BOT_TOKEN
        if not auth_token or not channel_id or channel_id.startswith("D"):
            return {"ok": True}

        client = await self.get_async_client()
        try:
            res = await client.post(
                "https://slack.com/api/conversations.join",
                headers={"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"},
                json={"channel": channel_id},
            )
            data = res.json()
            if data.get("ok"):
                logger.info("Bot successfully joined channel %s", channel_id)
            return data
        except Exception as e:
            logger.debug("Could not join channel %s: %s", channel_id, e)
            return {"ok": False, "error": str(e)}

    def send_message(
        self,
        channel_id: str,
        text: str,
        thread_ts: Optional[str] = None,
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Synchronous wrapper for sending a message to a Slack channel or thread."""
        auth_token = token or settings.SLACK_BOT_TOKEN
        headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {
            "channel": channel_id,
            "text": text,
        }
        if thread_ts:
            payload["thread_ts"] = thread_ts

        try:
            response = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=30,
            )
            result = response.json()

            if not result.get("ok"):
                logger.error("Slack API error response: %s", result)
            else:
                logger.info("Slack message successfully sent to channel %s", channel_id)

            return result
        except Exception as e:
            logger.error("Failed to send Slack message synchronously: %s", e)
            return {"ok": False, "error": str(e)}

    async def post_proactive_alert(
        self,
        text: str,
        channel_id: Optional[str] = None,
        thread_ts: Optional[str] = None,
    ) -> bool:
        """Send a proactive alert (email, meeting, timer) to the target channel."""
        target_channel = self.get_target_channel(channel_id)
        if not target_channel:
            logger.warning(
                "Cannot send proactive notification: No target channel found. "
                "Configure DEFAULT_SLACK_CHANNEL in .env or send a message to the bot first."
            )
            return False

        res = await self.send_message_async(target_channel, text, thread_ts=thread_ts)
        return bool(res.get("ok"))


slack_service = SlackService()
