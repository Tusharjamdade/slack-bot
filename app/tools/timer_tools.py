import json
from typing import Optional
from langchain_core.tools import tool

from app.services.notification_service import notification_service, format_duration
from app.services.slack_service import slack_service


@tool
def set_timer(duration_minutes: float, label: str = "Reminder") -> str:
    """
    Set a countdown timer or reminder that will alert the user in Slack when it expires.
    Args:
        duration_minutes: The duration in minutes (e.g. 10 for 10 minutes, 0.5 for 30 seconds, 60 for 1 hour).
        label: A description of what the timer is for (e.g. 'Call John', 'Check deployment', 'Take medicine').
    """
    try:
        if duration_minutes <= 0:
            return "Duration must be greater than 0 minutes."

        duration_seconds = duration_minutes * 60.0
        # Automatically tie to the last active channel or default channel
        target_channel = slack_service.get_target_channel()
        timer_info = notification_service.timer_manager.set_timer(
            duration_seconds=duration_seconds,
            label=label,
            channel_id=target_channel,
        )
        dur_str = format_duration(duration_seconds)
        return f"Timer successfully set for {dur_str} with label '{label}' (Timer ID: {timer_info['timer_id']}). You will be alerted in Slack when it expires."
    except Exception as e:
        return f"Error setting timer: {e}"


@tool
def list_active_timers() -> str:
    """
    List all currently running countdown timers and their remaining time.
    """
    try:
        active = notification_service.timer_manager.list_active_timers()
        if not active:
            return "There are no active timers running right now."
        return json.dumps(active, indent=2)
    except Exception as e:
        return f"Error listing active timers: {e}"


@tool
def cancel_timer(timer_id: str) -> str:
    """
    Cancel an active timer using its Timer ID.
    Args:
        timer_id: The 8-character ID of the timer to cancel.
    """
    try:
        success = notification_service.timer_manager.cancel_timer(timer_id.strip())
        if success:
            return f"Timer {timer_id} was successfully cancelled."
        return f"No active timer found with ID '{timer_id}'."
    except Exception as e:
        return f"Error cancelling timer: {e}"


timer_tools = [set_timer, list_active_timers, cancel_timer]
