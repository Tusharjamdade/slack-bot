import json
from typing import Optional
from langchain_core.tools import tool
from app.services.google.gmail_service import gmail_service


@tool
def search_emails(query: str = "label:INBOX", max_results: int = 5) -> str:
    """
    Search emails in Gmail using search terms or operators (e.g. 'is:unread', 'from:john', 'subject:report').
    Args:
        query: Gmail query string (default: 'label:INBOX').
        max_results: Max emails to return (default 5).
    """
    try:
        messages = gmail_service.search_messages(query=query, max_results=max_results)
        if not messages:
            return f"No emails found matching query '{query}'."
        return json.dumps(messages, indent=2)
    except PermissionError as e:
        return f"Gmail Authentication Required: {e}"
    except Exception as e:
        return f"Error searching emails: {e}"


@tool
def get_unread_emails(max_results: int = 5) -> str:
    """
    Fetch recent unread emails from the user's primary inbox.
    Args:
        max_results: Max unread emails to retrieve (default 5).
    """
    try:
        messages = gmail_service.get_unread_messages(max_results=max_results)
        if not messages:
            return "You have no unread emails in your inbox."
        return json.dumps(messages, indent=2)
    except PermissionError as e:
        return f"Gmail Authentication Required: {e}"
    except Exception as e:
        return f"Error fetching unread emails: {e}"


@tool
def read_email_content(message_id: str) -> str:
    """
    Get the full body and details of a specific email message using its ID.
    Args:
        message_id: The unique ID of the email message.
    """
    try:
        details = gmail_service.get_message_body(message_id=message_id)
        return json.dumps(details, indent=2)
    except PermissionError as e:
        return f"Gmail Authentication Required: {e}"
    except Exception as e:
        return f"Error reading email content: {e}"


@tool
def send_email(to: str, subject: str, body: str) -> str:
    """
    Send an email via the user's Gmail account.
    Args:
        to: Recipient email address.
        subject: Subject line of the email.
        body: Plain text message content.
    """
    try:
        result = gmail_service.send_message(to=to, subject=subject, body=body)
        return f"Email successfully sent to {to} with subject '{subject}'. Message ID: {result.get('id')}"
    except PermissionError as e:
        return f"Gmail Authentication Required: {e}"
    except Exception as e:
        return f"Error sending email: {e}"


gmail_tools = [search_emails, get_unread_emails, read_email_content, send_email]
