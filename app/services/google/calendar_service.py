import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.services.google.auth import get_google_credentials, get_google_auth_link_for_current_user

logger = logging.getLogger(__name__)


class CalendarService:
    """Service to interact with Google Calendar API with per-user isolation."""

    def __init__(self):
        pass

    def get_service(self):
        creds = get_google_credentials()
        if not creds:
            auth_link = get_google_auth_link_for_current_user()
            raise PermissionError(
                f"Your Google Calendar account is not connected yet. "
                f"Please connect your personal Google account to access your calendar: {auth_link}"
            )
        return build("calendar", "v3", credentials=creds)

    def list_events(
        self,
        max_results: int = 4,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List upcoming events from the primary calendar with compact payload."""
        service = self.get_service()
        if not time_min:
            time_min = datetime.now(timezone.utc).isoformat()

        effective_max = min(max_results, 6)
        kwargs: Dict[str, Any] = {
            "calendarId": "primary",
            "timeMin": time_min,
            "maxResults": effective_max,
            "singleEvents": True,
            "orderBy": "startTime",
        }
        if time_max:
            kwargs["timeMax"] = time_max

        events_result = service.events().list(**kwargs).execute()
        items = events_result.get("items", [])

        events = []
        for item in items:
            start = item.get("start", {}).get("dateTime", item.get("start", {}).get("date"))
            end = item.get("end", {}).get("dateTime", item.get("end", {}).get("date"))
            desc = (item.get("description") or "")[:80]
            events.append({
                "id": item.get("id"),
                "summary": (item.get("summary") or "Untitled Event")[:60],
                "start": start,
                "end": end,
                "location": (item.get("location") or "")[:50],
                "description": desc,
            })
        return events

    def create_event(
        self,
        summary: str,
        start_time: str,
        end_time: str,
        description: str = "",
        location: str = "",
        attendees: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a new event in the primary calendar. Times should be ISO 8601 strings."""
        service = self.get_service()

        body: Dict[str, Any] = {
            "summary": summary,
            "location": location,
            "description": description,
            "start": {"dateTime": start_time},
            "end": {"dateTime": end_time},
        }

        if attendees:
            body["attendees"] = [{"email": email.strip()} for email in attendees]

        created_event = service.events().insert(calendarId="primary", body=body).execute()
        return {
            "id": created_event.get("id"),
            "summary": created_event.get("summary"),
            "start": created_event.get("start", {}).get("dateTime"),
            "end": created_event.get("end", {}).get("dateTime"),
            "link": created_event.get("htmlLink"),
            "status": created_event.get("status"),
        }

    def delete_event(self, event_id: str) -> bool:
        """Delete an event by ID."""
        service = self.get_service()
        service.events().delete(calendarId="primary", eventId=event_id).execute()
        return True

    def search_events(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        """Search events matching query string."""
        service = self.get_service()
        events_result = service.events().list(
            calendarId="primary",
            q=query,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        items = events_result.get("items", [])
        return [
            {
                "id": item.get("id"),
                "summary": item.get("summary", "No Title"),
                "start": item.get("start", {}).get("dateTime", item.get("start", {}).get("date")),
                "end": item.get("end", {}).get("dateTime", item.get("end", {}).get("date")),
                "location": item.get("location", ""),
            }
            for item in items
        ]


calendar_service = CalendarService()
