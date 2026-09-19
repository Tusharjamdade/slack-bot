import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from langchain_core.tools import tool

from app.services.rag.rag_service import rag_service

logger = logging.getLogger(__name__)


def _run_async_safe(coro):
    """Safely run an async coroutine from a sync tool context."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)


@tool
def search_chat_history(query: str, top_k: int = 3) -> str:
    """
    Search past Slack conversations, previous messages, and decisions using semantic vector search.
    Args:
        query: Search query describing what you are looking for.
        top_k: Number of relevant conversation chunks to retrieve (default 3).
    """
    try:
        return _run_async_safe(rag_service.search_knowledge(query=query, top_k=min(top_k, 3)))
    except Exception as e:
        logger.error("Error executing search_chat_history tool: %s", e)
        return f"Error retrieving conversation history: {e}"


@tool
def get_recent_chat_history(limit: int = 5) -> str:
    """
    Retrieve chronological recent chat messages (past user questions and assistant responses).
    Args:
        limit: Number of recent messages to retrieve (default 5).
    """
    try:
        return _run_async_safe(rag_service.get_recent_history_formatted(limit=min(limit, 5)))
    except Exception as e:
        logger.error("Error executing get_recent_chat_history tool: %s", e)
        return f"Error retrieving conversation history: {e}"


rag_tools = [search_chat_history, get_recent_chat_history]

__all__ = ["search_chat_history", "get_recent_chat_history", "rag_tools"]
