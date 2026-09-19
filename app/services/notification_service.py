import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any, Set

from app.config import settings
from app.services.slack_service import slack_service
from app.services.google.calendar_service import calendar_service
from app.services.google.gmail_service import gmail_service

logger = logging.getLogger(__name__)


def format_duration(seconds: float) -> str:
    """Format seconds into human-readable duration."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    rem_seconds = seconds % 60
    if minutes < 60:
        return f"{minutes}m {rem_seconds}s" if rem_seconds else f"{minutes}m"
    hours = minutes // 60
    rem_minutes = minutes % 60
    return f"{hours}h {rem_minutes}m"


class TimerItem:
    def __init__(
        self,
        timer_id: str,
        label: str,
        duration_seconds: float,
        channel_id: Optional[str],
        thread_ts: Optional[str],
    ):
        self.timer_id = timer_id
        self.label = label
        self.duration_seconds = duration_seconds
        self.channel_id = channel_id
        self.thread_ts = thread_ts
        self.created_at = datetime.now(timezone.utc)
        self.target_time = self.created_at + timedelta(seconds=duration_seconds)
        self.status = "pending"  # "pending", "completed", "cancelled"
        self.task: Optional[asyncio.Task] = None

    def to_dict(self) -> Dict[str, Any]:
        remaining = max(0, int((self.target_time - datetime.now(timezone.utc)).total_seconds()))
        return {
            "timer_id": self.timer_id,
            "label": self.label,
            "duration": format_duration(self.duration_seconds),
            "remaining": format_duration(remaining),
            "remaining_seconds": remaining,
            "target_time_utc": self.target_time.isoformat(),
            "status": self.status,
            "channel_id": self.channel_id,
        }


import threading


class TimerManager:
    """Manages conversational timers and reminders with proactive Slack alerts."""

    def __init__(self):
        self._timers: Dict[str, TimerItem] = {}
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

    def set_main_loop(self, loop: asyncio.AbstractEventLoop):
        """Set reference to the primary running asyncio event loop."""
        self._main_loop = loop

    def _schedule_timer_task(self, timer: TimerItem):
        """Safely schedule timer countdown task across async loops and worker threads."""
        coro = self._run_timer(timer)

        # 1. Check if there is an active running event loop in the current thread
        try:
            loop = asyncio.get_running_loop()
            timer.task = loop.create_task(coro)
            return timer.task
        except RuntimeError:
            pass

        # 2. Check if the main application loop was registered and is running
        if self._main_loop and self._main_loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(coro, self._main_loop)
                timer.task = future
                return future
            except Exception as e:
                logger.warning("Could not schedule timer on main event loop: %s", e)

        # 3. Fallback: run countdown in a dedicated daemon thread with its own loop
        def _thread_worker():
            worker_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(worker_loop)
            try:
                worker_loop.run_until_complete(coro)
            finally:
                worker_loop.close()

        thread = threading.Thread(target=_thread_worker, daemon=True)
        thread.start()
        timer.task = thread
        return thread

    def set_timer(
        self,
        duration_seconds: float,
        label: str = "Timer",
        channel_id: Optional[str] = None,
        thread_ts: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create and start a new countdown timer."""
        timer_id = str(uuid.uuid4())[:8]
        timer = TimerItem(
            timer_id=timer_id,
            label=label or "Timer",
            duration_seconds=duration_seconds,
            channel_id=channel_id,
            thread_ts=thread_ts,
        )
        self._timers[timer_id] = timer

        # Schedule countdown task thread-safely
        self._schedule_timer_task(timer)
        logger.info("Created timer '%s' (%s) for %s seconds", timer_id, label, duration_seconds)
        return timer.to_dict()

    async def _run_timer(self, timer: TimerItem):
        """Asynchronously sleep and fire alert when countdown expires."""
        try:
            await asyncio.sleep(timer.duration_seconds)
            if timer.status == "pending":
                timer.status = "completed"
                logger.info("Timer '%s' (%s) expired. Posting alert to Slack.", timer.timer_id, timer.label)

                alert_text = (
                    f"⏰ *Timer Alert!*\n"
                    f"• *Reminder:* {timer.label}\n"
                    f"• *Duration:* {format_duration(timer.duration_seconds)}\n"
                    f"• *Status:* Completed"
                )
                await slack_service.post_proactive_alert(
                    text=alert_text,
                    channel_id=timer.channel_id,
                    thread_ts=timer.thread_ts,
                )
        except asyncio.CancelledError:
            timer.status = "cancelled"
            logger.info("Timer '%s' was cancelled.", timer.timer_id)
        except Exception as e:
            logger.exception("Error executing timer '%s': %s", timer.timer_id, e)

    def cancel_timer(self, timer_id: str) -> bool:
        """Cancel a pending timer."""
        timer = self._timers.get(timer_id)
        if not timer or timer.status != "pending":
            return False

        timer.status = "cancelled"
        if timer.task:
            if hasattr(timer.task, "cancel"):
                timer.task.cancel()
        return True

    def list_active_timers(self) -> List[Dict[str, Any]]:
        """Return list of active pending timers."""
        return [
            timer.to_dict()
            for timer in self._timers.values()
            if timer.status == "pending"
        ]


class CalendarWatcher:
    """Monitors Google Calendar and alerts user when a meeting is starting within 30 minutes."""

    def __init__(self):
        self._alerted_event_ids: Set[str] = set()

    def _parse_iso_datetime(self, dt_str: str) -> Optional[datetime]:
        """Parse ISO datetime string into UTC datetime object."""
        if not dt_str:
            return None
        try:
            # Handle date-only (all-day events)
            if len(dt_str) == 10:
                dt = datetime.strptime(dt_str, "%Y-%m-%d")
                return dt.replace(tzinfo=timezone.utc)
            # Handle full ISO format
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    async def check_upcoming_meetings(self):
        """Check for events starting in the next 30 minutes and alert if not already notified."""
        try:
            now_utc = datetime.now(timezone.utc)
            time_min = now_utc.isoformat()
            time_max = (now_utc + timedelta(minutes=settings.MEETING_ALERT_WINDOW_MINUTES)).isoformat()

            # Execute blocking Google API in worker thread
            events = await asyncio.to_thread(
                calendar_service.list_events,
                max_results=10,
                time_min=time_min,
                time_max=time_max,
            )

            if not events:
                return

            for event in events:
                event_id = event.get("id")
                if not event_id or event_id in self._alerted_event_ids:
                    continue

                start_str = event.get("start", "")
                start_dt = self._parse_iso_datetime(start_str)
                if not start_dt:
                    continue

                diff_seconds = (start_dt - now_utc).total_seconds()
                # If event starts between now and the configured window (e.g. 30 mins)
                if 0 <= diff_seconds <= (settings.MEETING_ALERT_WINDOW_MINUTES * 60 + 30):
                    mins_remaining = max(1, int(diff_seconds // 60))
                    summary = event.get("summary", "Upcoming Meeting")
                    link = event.get("link", "")
                    location = event.get("location", "")

                    loc_text = ""
                    if location:
                        loc_text = f"\n• *Location:* {location}"
                    if link and link != location:
                        loc_text += f"\n• *Link:* {link}"

                    alert_text = (
                        f"📅 *Upcoming Meeting Reminder (in {mins_remaining} min{'s' if mins_remaining != 1 else ''})*\n"
                        f"• *Title:* *{summary}*\n"
                        f"• *Start Time:* {start_str}{loc_text}"
                    )

                    success = await slack_service.post_proactive_alert(text=alert_text)
                    if success:
                        logger.info("Sent meeting alert for '%s' (starts in %d mins)", summary, mins_remaining)
                        self._alerted_event_ids.add(event_id)

            # Housekeeping: prune old events from alerted set if cache gets large
            if len(self._alerted_event_ids) > 200:
                self._alerted_event_ids.clear()

        except PermissionError:
            # Google auth not configured or token expired, skip silently without spamming error logs
            pass
        except Exception as e:
            logger.warning("Error checking upcoming calendar events: %s", e)


class GmailWatcher:
    """Monitors Gmail and alerts user when a new email arrives in the inbox."""

    def __init__(self):
        self._seen_email_ids: Set[str] = set()
        self._is_initialized = False

    async def check_new_emails(self):
        """Check for unread emails in inbox and notify on newly received ones."""
        try:
            # Execute blocking Google API in worker thread
            messages = await asyncio.to_thread(gmail_service.get_unread_messages, max_results=10)
            if not messages:
                self._is_initialized = True
                return

            current_ids = {msg["id"] for msg in messages if "id" in msg}

            # On startup, seed seen set so we don't alert on existing unread emails
            if not self._is_initialized:
                self._seen_email_ids.update(current_ids)
                self._is_initialized = True
                logger.info("Gmail watcher initialized with %d existing unread emails.", len(current_ids))
                return

            # Discover brand new emails
            new_ids = current_ids - self._seen_email_ids
            if not new_ids:
                return

            for msg in messages:
                msg_id = msg.get("id")
                if msg_id in new_ids:
                    sender = msg.get("from", "Unknown")
                    subject = msg.get("subject", "No Subject")
                    snippet = msg.get("snippet", "")
                    date_str = msg.get("date", "")

                    alert_text = (
                        f"📧 *New Email Received*\n"
                        f"• *From:* {sender}\n"
                        f"• *Subject:* *{subject}*\n"
                        f"• *Date:* {date_str}\n"
                        f"• *Snippet:* {snippet[:180]}{'...' if len(snippet) > 180 else ''}"
                    )

                    success = await slack_service.post_proactive_alert(text=alert_text)
                    if success:
                        logger.info("Sent new email notification for '%s' from %s", subject, sender)
                        self._seen_email_ids.add(msg_id)

            # Limit size of cache
            if len(self._seen_email_ids) > 500:
                self._seen_email_ids = current_ids

        except PermissionError:
            # Gmail not authenticated, skip silently
            pass
        except Exception as e:
            logger.warning("Error checking Gmail for new emails: %s", e)


class EventNotificationService:
    """Coordinates background event loops for proactive Slack notifications."""

    def __init__(self):
        self.timer_manager = TimerManager()
        self.calendar_watcher = CalendarWatcher()
        self.gmail_watcher = GmailWatcher()
        self._running = False
        self._background_tasks: List[asyncio.Task] = []

    def start(self):
        """Start background polling tasks."""
        if not settings.ENABLE_EVENT_NOTIFICATIONS:
            logger.info("Event-driven notifications are disabled via ENABLE_EVENT_NOTIFICATIONS=False.")
            return

        if self._running:
            return

        self._running = True
        try:
            current_loop = asyncio.get_running_loop()
            self.timer_manager.set_main_loop(current_loop)
        except Exception:
            pass

        self._background_tasks.append(asyncio.create_task(self._calendar_loop()))
        self._background_tasks.append(asyncio.create_task(self._gmail_loop()))
        logger.info(
            "EventNotificationService started (Calendar check: every %ds, Gmail check: every %ds).",
            settings.EVENT_POLL_INTERVAL_CALENDAR,
            settings.EVENT_POLL_INTERVAL_GMAIL,
        )

    async def _calendar_loop(self):
        """Periodic loop to check calendar meetings."""
        while self._running:
            try:
                await self.calendar_watcher.check_upcoming_meetings()
            except Exception as e:
                logger.warning("Calendar loop iteration error: %s", e)
            await asyncio.sleep(settings.EVENT_POLL_INTERVAL_CALENDAR)

    async def _gmail_loop(self):
        """Periodic loop to check incoming emails."""
        while self._running:
            try:
                await self.gmail_watcher.check_new_emails()
            except Exception as e:
                logger.warning("Gmail loop iteration error: %s", e)
            await asyncio.sleep(settings.EVENT_POLL_INTERVAL_GMAIL)

    def stop(self):
        """Stop background polling tasks."""
        self._running = False
        for task in self._background_tasks:
            if not task.done():
                task.cancel()
        self._background_tasks.clear()
        logger.info("EventNotificationService stopped.")


notification_service = EventNotificationService()
