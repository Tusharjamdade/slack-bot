from typing import Optional, List
from app.tools.calendar_tools import calendar_tools
from app.tools.gmail_tools import gmail_tools
from app.tools.rag_tools import rag_tools
from app.tools.timer_tools import timer_tools

all_tools = calendar_tools + gmail_tools + rag_tools + timer_tools


def get_tools_for_workspace(enabled_tools: Optional[List[str]] = None) -> list:
    """Return tool list filtered by the workspace's enabled tools."""
    if enabled_tools is None:
        return all_tools

    active = []
    # Always include memory/RAG and timer tools
    active.extend(rag_tools)
    active.extend(timer_tools)

    # Workspace specific productivity tools
    normalized_tools = [t.lower() for t in enabled_tools]
    if "calendar" in normalized_tools:
        active.extend(calendar_tools)
    if "email" in normalized_tools or "gmail" in normalized_tools:
        active.extend(gmail_tools)

    return active


__all__ = [
    "all_tools",
    "get_tools_for_workspace",
    "calendar_tools",
    "gmail_tools",
    "rag_tools",
    "timer_tools",
]
