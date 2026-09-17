import logging
from collections import OrderedDict
from typing import Optional, Dict, Any
import requests

from app.config import settings

logger = logging.getLogger(__name__)


class SlackService:
    """Service to handle Slack API communications and event deduplication."""

    def __init__(self, max_dedup_cache: int = 1000):
        self.api_url = "https://slack.com/api/chat.postMessage"
        self._processed_events: OrderedDict[str, bool] = OrderedDict()
        self.max_dedup_cache = max_dedup_cache

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

    def send_message(
        self,
        channel_id: str,
        text: str,
        thread_ts: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a message to a Slack channel or thread."""
        headers = {
            "Authorization": f"Bearer {settings.SLACK_BOT_TOKEN}",
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
            logger.error("Failed to send Slack message: %s", e)
            return {"ok": False, "error": str(e)}


slack_service = SlackService()
