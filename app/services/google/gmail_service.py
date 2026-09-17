import base64
import logging
from email.mime.text import MIMEText
from typing import Optional, List, Dict, Any
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.services.google.auth import get_google_credentials

logger = logging.getLogger(__name__)


class GmailService:
    """Service to interact with Gmail API."""

    def __init__(self):
        self._service = None

    def get_service(self):
        creds = get_google_credentials()
        if not creds:
            raise PermissionError(
                "Gmail is not authenticated. Please run 'python scripts/setup_google_auth.py' "
                "or configure GOOGLE_TOKEN_FILE / GOOGLE_REFRESH_TOKEN in .env."
            )
        if not self._service or creds.expired:
            self._service = build("gmail", "v1", credentials=creds)
        return self._service

    def search_messages(self, query: str = "label:INBOX", max_results: int = 5) -> List[Dict[str, Any]]:
        """Search messages matching query and return list with summary details."""
        service = self.get_service()
        results = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=max_results,
        ).execute()

        messages_meta = results.get("messages", [])
        messages = []

        for meta in messages_meta:
            msg_id = meta["id"]
            msg_data = service.users().messages().get(
                userId="me",
                id=msg_id,
                format="metadata",
                metadataHeaders=["Subject", "From", "Date"],
            ).execute()

            headers = {h["name"]: h["value"] for h in msg_data.get("payload", {}).get("headers", [])}
            messages.append({
                "id": msg_id,
                "thread_id": msg_data.get("threadId"),
                "subject": headers.get("Subject", "No Subject"),
                "from": headers.get("From", "Unknown"),
                "date": headers.get("Date", ""),
                "snippet": msg_data.get("snippet", ""),
            })

        return messages

    def get_message_body(self, message_id: str) -> Dict[str, Any]:
        """Fetch full message content for an email ID."""
        service = self.get_service()
        msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        payload = msg.get("payload", {})
        headers = {h["name"]: h["value"] for h in payload.get("headers", [])}

        body_text = ""
        if "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain" and "data" in part.get("body", {}):
                    data = part["body"]["data"]
                    body_text += base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        elif "body" in payload and "data" in payload["body"]:
            data = payload["body"]["data"]
            body_text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        return {
            "id": message_id,
            "subject": headers.get("Subject", "No Subject"),
            "from": headers.get("From", "Unknown"),
            "to": headers.get("To", ""),
            "date": headers.get("Date", ""),
            "snippet": msg.get("snippet", ""),
            "body": body_text.strip() or msg.get("snippet", ""),
        }

    def send_message(self, to: str, subject: str, body: str) -> Dict[str, Any]:
        """Send an email to a recipient."""
        service = self.get_service()
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        sent = service.users().messages().send(
            userId="me",
            body={"raw": raw},
        ).execute()

        return {
            "id": sent.get("id"),
            "thread_id": sent.get("threadId"),
            "status": "sent",
            "to": to,
            "subject": subject,
        }

    def get_unread_messages(self, max_results: int = 5) -> List[Dict[str, Any]]:
        """Get recent unread messages from INBOX."""
        return self.search_messages(query="is:unread label:INBOX", max_results=max_results)


gmail_service = GmailService()
