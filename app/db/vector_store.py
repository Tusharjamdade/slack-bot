import json
import logging
from typing import List, Dict, Any, Optional
import numpy as np

from app.config import settings
from app.db.connection import db_pool

logger = logging.getLogger(__name__)


class VectorStore:
    """PostgreSQL + pgvector store for chat history and semantic retrieval."""

    async def ensure_conversation(
        self,
        session_id: str,
        channel_id: str,
        thread_ts: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Create or update conversation session record."""
        pool = await db_pool.get_pool()
        if not pool:
            return

        meta_json = json.dumps(metadata or {})
        query = """
        INSERT INTO conversations (session_id, channel_id, thread_ts, metadata, updated_at)
        VALUES ($1, $2, $3, $4::jsonb, CURRENT_TIMESTAMP)
        ON CONFLICT (session_id) 
        DO UPDATE SET updated_at = CURRENT_TIMESTAMP;
        """
        try:
            async with pool.acquire() as conn:
                await conn.execute(query, session_id, channel_id, thread_ts, meta_json)
        except Exception as e:
            logger.error("Failed to ensure conversation record: %s", e)

    async def save_message(
        self,
        session_id: str,
        channel_id: str,
        user_id: str,
        role: str,
        content: str,
        tool_calls: Optional[Any] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """Save raw chat message turn to PostgreSQL."""
        pool = await db_pool.get_pool()
        if not pool:
            return None

        # Ensure conversation exists
        await self.ensure_conversation(session_id=session_id, channel_id=channel_id)

        meta_json = json.dumps(metadata or {})
        tool_calls_json = json.dumps(tool_calls) if tool_calls is not None else None

        query = """
        INSERT INTO chat_messages (session_id, channel_id, user_id, role, content, tool_calls, metadata)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb)
        RETURNING id;
        """
        try:
            async with pool.acquire() as conn:
                message_id = await conn.fetchval(
                    query, session_id, channel_id, user_id, role, content, tool_calls_json, meta_json
                )
                return message_id
        except Exception as e:
            logger.error("Failed to save chat message: %s", e)
            return None

    async def save_embedding(
        self,
        message_id: Optional[int],
        session_id: str,
        channel_id: str,
        speaker_role: str,
        chunk_text: str,
        embedding: List[float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """Save vector chunk to chat_embeddings table."""
        pool = await db_pool.get_pool()
        if not pool:
            return None

        meta_json = json.dumps(metadata or {})
        # Ensure embedding is numpy array or list compatible with pgvector
        vec = np.array(embedding, dtype=np.float32)

        query = """
        INSERT INTO chat_embeddings (message_id, session_id, channel_id, speaker_role, chunk_text, embedding, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
        RETURNING id;
        """
        try:
            async with pool.acquire() as conn:
                embed_id = await conn.fetchval(
                    query, message_id, session_id, channel_id, speaker_role, chunk_text, vec, meta_json
                )
                return embed_id
        except Exception as e:
            logger.error("Failed to save vector embedding: %s", e)
            return None

    async def search_similar_chunks(
        self,
        query_embedding: List[float],
        channel_id: Optional[str] = None,
        top_k: Optional[int] = None,
        threshold: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        Cosine similarity search across conversation history using pgvector.
        Uses 1 - (embedding <=> query) as similarity score.
        """
        pool = await db_pool.get_pool()
        if not pool:
            return []

        k = top_k or settings.RAG_TOP_K
        sim_threshold = threshold if threshold is not None else settings.RAG_SIMILARITY_THRESHOLD
        vec = np.array(query_embedding, dtype=np.float32)

        # Cosine distance operator is <=>
        query = """
        SELECT 
            id,
            message_id,
            session_id,
            channel_id,
            speaker_role,
            chunk_text,
            metadata,
            created_at,
            1 - (embedding <=> $1) AS similarity
        FROM chat_embeddings
        WHERE ($2::varchar IS NULL OR channel_id = $2)
          AND (1 - (embedding <=> $1)) >= $3
        ORDER BY embedding <=> $1 ASC
        LIMIT $4;
        """
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(query, vec, channel_id, sim_threshold, k)
                results = []
                for row in rows:
                    results.append({
                        "id": row["id"],
                        "message_id": row["message_id"],
                        "session_id": row["session_id"],
                        "channel_id": row["channel_id"],
                        "speaker_role": row["speaker_role"],
                        "chunk_text": row["chunk_text"],
                        "metadata": json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"],
                        "created_at": str(row["created_at"]),
                        "similarity": float(row["similarity"]),
                    })
                return results
        except Exception as e:
            logger.error("Failed to perform vector similarity search: %s", e)
            return []

    async def get_recent_messages(
        self,
        session_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch chronological recent messages for conversational context or history review."""
        pool = await db_pool.get_pool()
        if not pool:
            return []

        msg_limit = limit or settings.SHORT_TERM_MEMORY_LIMIT
        query = """
        SELECT id, session_id, channel_id, user_id, role, content, created_at
        FROM (
            SELECT id, session_id, channel_id, user_id, role, content, created_at
            FROM chat_messages
            WHERE ($1::varchar IS NULL OR session_id = $1)
              AND ($2::varchar IS NULL OR channel_id = $2)
            ORDER BY created_at DESC
            LIMIT $3
        ) sub
        ORDER BY created_at ASC;
        """
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(query, session_id, channel_id, msg_limit)
                return [
                    {
                        "id": row["id"],
                        "session_id": row["session_id"],
                        "channel_id": row["channel_id"],
                        "user_id": row["user_id"],
                        "role": row["role"],
                        "content": row["content"],
                        "created_at": str(row["created_at"]),
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.error("Failed to retrieve recent messages: %s", e)
            return []


vector_store = VectorStore()
