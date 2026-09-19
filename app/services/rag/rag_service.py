import logging
from typing import Dict, Any, List, Optional

from app.config import settings
from app.db.vector_store import vector_store
from app.services.rag.embedding_service import embedding_service
from app.services.rag.intent_router import intent_router

logger = logging.getLogger(__name__)


class RagService:
    """Orchestrator for chat persistence, embedding generation, and adaptive RAG retrieval."""

    async def store_user_turn(
        self,
        session_id: str,
        channel_id: str,
        user_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        team_id: Optional[str] = None,
    ) -> Optional[int]:
        """Store user message in database and index into pgvector with team_id isolation."""
        try:
            # 1. Save raw message
            msg_id = await vector_store.save_message(
                session_id=session_id,
                channel_id=channel_id,
                user_id=user_id,
                role="user",
                content=content,
                metadata=metadata,
                team_id=team_id,
            )

            # 2. Chunk and embed
            chunks = embedding_service.chunk_text(content)
            if chunks:
                embeddings = embedding_service.embed_documents(chunks)
                for chunk_text, emb in zip(chunks, embeddings):
                    await vector_store.save_embedding(
                        message_id=msg_id,
                        session_id=session_id,
                        channel_id=channel_id,
                        speaker_role="user",
                        chunk_text=chunk_text,
                        embedding=emb,
                        metadata=metadata,
                        team_id=team_id,
                    )
            return msg_id
        except Exception as e:
            logger.error("Failed to store and embed user turn: %s", e)
            return None

    async def store_assistant_turn(
        self,
        session_id: str,
        channel_id: str,
        user_id: str,
        content: str,
        tool_calls: Optional[Any] = None,
        metadata: Optional[Dict[str, Any]] = None,
        team_id: Optional[str] = None,
    ) -> Optional[int]:
        """Store assistant message in database and index into pgvector with team_id isolation."""
        try:
            # 1. Save assistant turn
            msg_id = await vector_store.save_message(
                session_id=session_id,
                channel_id=channel_id,
                user_id=user_id,
                role="assistant",
                content=content,
                tool_calls=tool_calls,
                metadata=metadata,
                team_id=team_id,
            )

            # 2. Chunk and embed assistant response
            chunks = embedding_service.chunk_text(content)
            if chunks:
                embeddings = embedding_service.embed_documents(chunks)
                for chunk_text, emb in zip(chunks, embeddings):
                    await vector_store.save_embedding(
                        message_id=msg_id,
                        session_id=session_id,
                        channel_id=channel_id,
                        speaker_role="assistant",
                        chunk_text=chunk_text,
                        embedding=emb,
                        metadata=metadata,
                        team_id=team_id,
                    )
            return msg_id
        except Exception as e:
            logger.error("Failed to store and embed assistant turn: %s", e)
            return None

    async def evaluate_and_retrieve(
        self,
        message: str,
        channel_id: Optional[str] = None,
        session_id: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Adaptive task routing:
        1. Classifies if user message requires previous conversation history / knowledge.
        2. If YES and is_chronological: returns chronological recent messages (for past questions, recaps).
        3. If YES and topic-specific: performs pgvector cosine similarity search, filters by threshold, and returns context.
        4. If NO: returns empty context immediately to avoid cluttering LLM prompt.
        """
        route_decision = intent_router.route_message(message)
        needs_history = route_decision.get("needs_history", False)
        is_chronological = route_decision.get("is_chronological", False)
        task_type = route_decision.get("task_type", "direct_qa")
        search_query = route_decision.get("search_query")

        logger.info(
            "Adaptive Intent Route -> Task: %s | Needs History: %s | Chronological: %s | Search Query: %s",
            task_type, needs_history, is_chronological, search_query
        )

        if not needs_history:
            return {
                "needs_history": False,
                "task_type": task_type,
                "context": "",
                "chunks": [],
                "search_query": None,
                "reasoning": route_decision.get("reasoning", ""),
            }

        # Case 1: Chronological history overview (past questions, conversation recap)
        if is_chronological:
            try:
                msgs = await vector_store.get_recent_messages(
                    session_id=session_id,
                    channel_id=channel_id,
                    limit=5,
                )
                filtered = [m for m in msgs if m.get("content", "").strip() != message.strip()]
                if not filtered:
                    return {
                        "needs_history": True,
                        "task_type": task_type,
                        "context": "=== RECENT CONVERSATION HISTORY ===\nNo prior messages recorded in this conversation.\n=== END OF CONVERSATION HISTORY ===",
                        "chunks": [],
                        "search_query": None,
                        "reasoning": "No prior conversation messages found",
                    }

                formatted_lines = [
                    "=== RECENT CONVERSATION HISTORY (CHRONOLOGICAL) ==="
                ]
                for m in filtered:
                    role = m.get("role", "unknown").upper()
                    date = str(m.get("created_at", ""))[:16]
                    cnt = m.get("content", "").strip()
                    if len(cnt) > 150:
                        cnt = cnt[:150] + "..."
                    formatted_lines.append(f"- [{date}] {role}: {cnt}")
                formatted_lines.append("=== END OF CONVERSATION HISTORY ===")
                formatted_context = "\n".join(formatted_lines)

                return {
                    "needs_history": True,
                    "task_type": task_type,
                    "context": formatted_context,
                    "chunks": [],
                    "search_query": None,
                    "reasoning": route_decision.get("reasoning", ""),
                }
            except Exception as e:
                logger.error("Error retrieving chronological history: %s", e)
                return {
                    "needs_history": False,
                    "task_type": task_type,
                    "context": "",
                    "chunks": [],
                    "search_query": None,
                    "reasoning": f"History retrieval error: {e}",
                }

        # Case 2: Specific topic retrieval via vector search
        target_query = search_query or message
        try:
            query_vector = embedding_service.embed_query(target_query)
            chunks = await vector_store.search_similar_chunks(
                query_embedding=query_vector,
                channel_id=None,  # Allow cross-channel memory recall within same workspace
                top_k=settings.RAG_TOP_K,
                threshold=settings.RAG_SIMILARITY_THRESHOLD,
                team_id=team_id,
            )

            if not chunks:
                logger.info("No past chunks matched similarity threshold (%.2f)", settings.RAG_SIMILARITY_THRESHOLD)
                return {
                    "needs_history": True,
                    "task_type": task_type,
                    "context": "",
                    "chunks": [],
                    "search_query": target_query,
                    "reasoning": "No relevant chunks passed the similarity threshold",
                }

            # Format chunks for prompt injection
            formatted_lines = [
                "=== RELEVANT PAST CONVERSATION ==="
            ]
            for i, chunk in enumerate(chunks, 1):
                role = chunk.get("speaker_role", "unknown")
                date = chunk.get("created_at", "")[:16]
                text = chunk.get("chunk_text", "").strip()
                if len(text) > 200:
                    text = text[:200] + "..."
                formatted_lines.append(f"[{i}] ({role.upper()} at {date}): {text}")
            formatted_lines.append("=== END OF KNOWLEDGE ===")
            formatted_context = "\n".join(formatted_lines)

            return {
                "needs_history": True,
                "task_type": task_type,
                "context": formatted_context,
                "chunks": chunks,
                "search_query": target_query,
                "reasoning": route_decision.get("reasoning", ""),
            }
        except Exception as e:
            logger.error("Error during adaptive RAG retrieval: %s", e)
            return {
                "needs_history": False,
                "task_type": task_type,
                "context": "",
                "chunks": [],
                "search_query": target_query,
                "reasoning": f"Retrieval error: {e}",
            }

    async def get_recent_history_formatted(
        self,
        session_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        limit: int = 5,
    ) -> str:
        """Fetch and format recent chronological history for explicit tool calls."""
        try:
            msgs = await vector_store.get_recent_messages(
                session_id=session_id,
                channel_id=channel_id,
                limit=limit,
            )
            if not msgs:
                return "No previous conversation history found."
            result_lines = [f"Recent conversation ({len(msgs)} turns):"]
            for m in msgs:
                role = m.get("role", "unknown").upper()
                date = str(m.get("created_at", ""))[:16]
                cnt = m.get("content", "").strip()
                if len(cnt) > 120:
                    cnt = cnt[:120] + "..."
                result_lines.append(f"- [{role} - {date}]: {cnt}")
            return "\n".join(result_lines)
        except Exception as e:
            logger.error("Failed to retrieve formatted history: %s", e)
            return f"Error retrieving conversation history: {e}"

    async def search_knowledge(
        self,
        query: str,
        top_k: int = 3,
        session_id: Optional[str] = None,
        channel_id: Optional[str] = None,
    ) -> str:
        """Explicit tool call function to search past conversation history."""
        import re
        is_overview = bool(re.search(
            r"\b(past|previous|recent)\s+(questions?|conversations?|messages?|chats?)\b|\bwhat\s+(questions?|did i ask)\b|\bchat history\b",
            query.lower(),
        ))
        if is_overview:
            return await self.get_recent_history_formatted(
                session_id=session_id,
                channel_id=channel_id,
                limit=top_k * 2,
            )

        try:
            query_vector = embedding_service.embed_query(query)
            chunks = await vector_store.search_similar_chunks(
                query_embedding=query_vector,
                channel_id=channel_id,
                top_k=top_k,
                threshold=0.40,
            )
            if not chunks:
                return f"No previous conversations or notes found matching '{query}'."

            result_lines = [f"Found {len(chunks)} relevant records:"]
            for i, c in enumerate(chunks, 1):
                role = c.get("speaker_role", "unknown")
                text = c.get("chunk_text", "").strip()
                if len(text) > 150:
                    text = text[:150] + "..."
                date = c.get("created_at", "")[:16]
                result_lines.append(f"{i}. [{role.upper()} - {date}]: {text}")
            return "\n".join(result_lines)
        except Exception as e:
            logger.error("Failed to execute search_knowledge tool: %s", e)
            return f"Error querying past knowledge: {e}"

    async def get_recent_history(
        self,
        session_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve recent chronological turns for conversational context."""
        return await vector_store.get_recent_messages(session_id=session_id, channel_id=channel_id, limit=limit)


rag_service = RagService()
