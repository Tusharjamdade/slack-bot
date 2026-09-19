import base64
import logging
from email.mime.text import MIMEText
from typing import Optional, List, Dict, Any
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.services.google.auth import get_google_credentials, get_google_auth_link_for_current_user

logger = logging.getLogger(__name__)


class GmailService:
    """Service to interact with Gmail API with strict per-user isolation."""

    def __init__(self):
        pass

    def get_service(self):
        creds = get_google_credentials()
        if not creds:
            auth_link = get_google_auth_link_for_current_user()
            raise PermissionError(
                f"Your Gmail account is not connected yet. "
                f"Please connect your personal Google account to access your emails: {auth_link}"
            )
        return build("gmail", "v1", credentials=creds)

    def search_messages(self, query: str = "label:INBOX", max_results: int = 3) -> List[Dict[str, Any]]:
        """Search messages matching query and return list with compact summary details."""
        service = self.get_service()
        # Cap max_results to 5 to protect token limits
        effective_max = min(max_results, 5)
        results = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=effective_max,
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
            subject = (headers.get("Subject") or "No Subject")[:70]
            sender = (headers.get("From") or "Unknown")[:60]
            date_str = (headers.get("Date") or "")[:25]
            snippet = (msg_data.get("snippet") or "")[:100]

            messages.append({
                "id": msg_id,
                "subject": subject,
                "from": sender,
                "date": date_str,
                "snippet": snippet,
            })

        return messages

    def get_message_body(self, message_id: str) -> Dict[str, Any]:
        """Fetch message content for an email ID with compact token-efficient payload."""
        import re
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

        # Strip HTML tags if plain text wasn't found or contains HTML remnants
        clean_text = re.sub(r"<[^>]+>", " ", body_text)
        # Collapse whitespace and empty lines
        clean_text = re.sub(r"\s+", " ", clean_text).strip()

        if not clean_text:
            clean_text = msg.get("snippet", "")

        # Hard limit body to 1200 characters (~300 tokens) to guarantee safety under 8k TPM limits
        if len(clean_text) > 1200:
            clean_text = clean_text[:1200] + "... [truncated for brevity]"

        return {
            "id": message_id,
            "subject": (headers.get("Subject") or "No Subject")[:80],
            "from": (headers.get("From") or "Unknown")[:60],
            "date": (headers.get("Date") or "")[:30],
            "body": clean_text,
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
            "status": "sent",
            "to": to,
            "subject": subject,
        }

    def get_unread_messages(self, max_results: int = 3) -> List[Dict[str, Any]]:
        """Get recent unread messages from INBOX with compact payload."""
        return self.search_messages(query="is:unread label:INBOX", max_results=max_results)


gmail_service = GmailService()
