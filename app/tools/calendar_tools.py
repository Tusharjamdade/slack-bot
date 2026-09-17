import json
from typing import Optional, List
from langchain_core.tools import tool
from app.services.google.calendar_service import calendar_service


@tool
def list_calendar_events(max_results: int = 10, time_min: Optional[str] = None) -> str:
    """
    List upcoming events from the user's primary Google Calendar.
    Args:
        max_results: Maximum number of events to return (default 10).
        time_min: Optional ISO 8601 start time (e.g. '2026-09-17T00:00:00Z'). Defaults to current time.
    """
    try:
        events = calendar_service.list_events(max_results=max_results, time_min=time_min)
        if not events:
            return "No upcoming events found on your Google Calendar."
        return json.dumps(events, indent=2)
    except PermissionError as e:
        return f"Google Calendar Authentication Required: {e}"
    except Exception as e:
        return f"Error retrieving calendar events: {e}"


@tool
def create_calendar_event(
    summary: str,
    start_time: str,
    end_time: str,
    description: str = "",
    location: str = "",
    attendees: Optional[List[str]] = None,
) -> str:
    """
    Schedule a new event on Google Calendar.
    Args:
        summary: Title/name of the event.
        start_time: Start datetime in ISO 8601 format (e.g. '2026-09-18T15:00:00+05:30').
        end_time: End datetime in ISO 8601 format (e.g. '2026-09-18T16:00:00+05:30').
        description: Optional notes or agenda for the event.
        location: Optional physical address or video call link.
        attendees: Optional list of guest email addresses.
    """
    try:
        event = calendar_service.create_event(
            summary=summary,
            start_time=start_time,
            end_time=end_time,
            description=description,
            location=location,
            attendees=attendees,
        )
        return f"Successfully created event '{event.get('summary')}' from {event.get('start')} to {event.get('end')}. Event Link: {event.get('link')}"
    except PermissionError as e:
        return f"Google Calendar Authentication Required: {e}"
    except Exception as e:
        return f"Error creating calendar event: {e}"


@tool
def search_calendar_events(query: str, max_results: int = 5) -> str:
    """
    Search for Google Calendar events by keyword or person name.
    Args:
        query: Keyword or phrase to search for.
        max_results: Max results to return (default 5).
    """
    try:
        events = calendar_service.search_events(query=query, max_results=max_results)
        if not events:
            return f"No events found matching '{query}'."
        return json.dumps(events, indent=2)
    except PermissionError as e:
        return f"Google Calendar Authentication Required: {e}"
    except Exception as e:
        return f"Error searching calendar events: {e}"


calendar_tools = [list_calendar_events, create_calendar_event, search_calendar_events]
