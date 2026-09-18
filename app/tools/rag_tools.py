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
def search_chat_history(query: str, top_k: int = 5) -> str:
    """
    Search past Slack conversations, previous messages, decisions, and history
    using semantic vector search (RDS PostgreSQL + pgvector).
    
    Use this tool whenever you need to recall what was discussed earlier, find past information,
    or look up decisions made in previous chats.
    
    Args:
        query: Semantic search query describing what you are looking for (e.g. 'deployment plan', 'database schema').
        top_k: Number of most relevant conversation chunks to retrieve (default 5).
    """
    try:
        return _run_async_safe(rag_service.search_knowledge(query=query, top_k=top_k))
    except Exception as e:
        logger.error("Error executing search_chat_history tool: %s", e)
        return f"Error retrieving conversation history: {e}"


@tool
def get_recent_chat_history(limit: int = 15) -> str:
    """
    Retrieve chronological recent chat messages (past user questions and assistant responses)
    from PostgreSQL.
    
    Use this tool whenever the user asks:
    - 'what are my past questions?'
    - 'what questions did I ask you in past?'
    - 'what are my past conversations with you?'
    - 'what did we talk about earlier?'
    - 'show recent messages / recap conversation'
    
    Args:
        limit: Number of recent messages to retrieve (default 15).
    """
    try:
        return _run_async_safe(rag_service.get_recent_history_formatted(limit=limit))
    except Exception as e:
        logger.error("Error executing get_recent_chat_history tool: %s", e)
        return f"Error retrieving conversation history: {e}"


rag_tools = [search_chat_history, get_recent_chat_history]

__all__ = ["search_chat_history", "get_recent_chat_history", "rag_tools"]
