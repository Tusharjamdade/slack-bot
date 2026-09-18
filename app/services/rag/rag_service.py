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
    ) -> Optional[int]:
        """Store user message in database and index into pgvector."""
        try:
            # 1. Save raw message
            msg_id = await vector_store.save_message(
                session_id=session_id,
                channel_id=channel_id,
                user_id=user_id,
                role="user",
                content=content,
                metadata=metadata,
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
    ) -> Optional[int]:
        """Store assistant message in database and index into pgvector."""
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
    ) -> Dict[str, Any]:
        """
        Adaptive task routing:
        1. Classifies if user message requires previous conversation history / knowledge.
        2. If YES: performs pgvector cosine similarity search, filters by threshold, and returns context.
        3. If NO: returns empty context immediately to avoid cluttering LLM prompt.
        """
        route_decision = intent_router.route_message(message)
        needs_history = route_decision.get("needs_history", False)
        task_type = route_decision.get("task_type", "direct_qa")
        search_query = route_decision.get("search_query")

        logger.info(
            "Adaptive Intent Route -> Task: %s | Needs History: %s | Search Query: %s",
            task_type, needs_history, search_query
        )

        if not needs_history or not search_query:
            return {
                "needs_history": False,
                "task_type": task_type,
                "context": "",
                "chunks": [],
                "search_query": None,
                "reasoning": route_decision.get("reasoning", ""),
            }

        # Retrieve matching chunks
        try:
            query_vector = embedding_service.embed_query(search_query)
            # Search workspace history (across channels or current channel)
            chunks = await vector_store.search_similar_chunks(
                query_embedding=query_vector,
                channel_id=None,  # Allow cross-channel memory recall
                top_k=settings.RAG_TOP_K,
                threshold=settings.RAG_SIMILARITY_THRESHOLD,
            )

            if not chunks:
                logger.info("No past chunks matched the similarity threshold (%.2f)", settings.RAG_SIMILARITY_THRESHOLD)
                return {
                    "needs_history": True,
                    "task_type": task_type,
                    "context": "",
                    "chunks": [],
                    "search_query": search_query,
                    "reasoning": "No relevant chunks passed the similarity threshold",
                }

            # Format chunks for prompt injection
            formatted_lines = [
                "=== RELEVANT PAST CONVERSATION & KNOWLEDGE (RETRIEVED VIA RDS PGVECTOR) ==="
            ]
            for i, chunk in enumerate(chunks, 1):
                role = chunk.get("speaker_role", "unknown")
                date = chunk.get("created_at", "")[:19]
                text = chunk.get("chunk_text", "").strip()
                sim = round(chunk.get("similarity", 0.0), 3)
                formatted_lines.append(f"[{i}] ({role.upper()} at {date}, similarity: {sim}):\n{text}\n")
            formatted_lines.append("=== END OF RETRIEVED KNOWLEDGE ===")
            formatted_context = "\n".join(formatted_lines)

            return {
                "needs_history": True,
                "task_type": task_type,
                "context": formatted_context,
                "chunks": chunks,
                "search_query": search_query,
                "reasoning": route_decision.get("reasoning", ""),
            }
        except Exception as e:
            logger.error("Error during adaptive RAG retrieval: %s", e)
            return {
                "needs_history": False,
                "task_type": task_type,
                "context": "",
                "chunks": [],
                "search_query": search_query,
                "reasoning": f"Retrieval error: {e}",
            }

    async def search_knowledge(self, query: str, top_k: int = 5) -> str:
        """Explicit tool call function to search past conversation history."""
        try:
            query_vector = embedding_service.embed_query(query)
            chunks = await vector_store.search_similar_chunks(
                query_embedding=query_vector,
                top_k=top_k,
                threshold=0.50,  # Slightly lower threshold for explicit search
            )
            if not chunks:
                return f"No previous conversations or notes found matching '{query}'."

            result_lines = [f"Found {len(chunks)} relevant past conversation records:"]
            for i, c in enumerate(chunks, 1):
                role = c.get("speaker_role", "unknown")
                text = c.get("chunk_text", "").strip()
                date = c.get("created_at", "")[:19]
                result_lines.append(f"{i}. [{role.upper()} - {date}]: {text}")
            return "\n\n".join(result_lines)
        except Exception as e:
            logger.error("Failed to execute search_knowledge tool: %s", e)
            return f"Error querying past knowledge: {e}"

    async def get_recent_history(self, session_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Retrieve recent chronological turns for conversational context."""
        return await vector_store.get_recent_messages(session_id=session_id, limit=limit)


rag_service = RagService()
