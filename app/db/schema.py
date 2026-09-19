import logging
from app.config import settings
from app.db.connection import db_pool

logger = logging.getLogger(__name__)

SCHEMA_SQL = f"""
-- 1. Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Conversations table to track sessions / channels / threads
CREATE TABLE IF NOT EXISTS conversations (
    session_id VARCHAR(255) PRIMARY KEY,
    channel_id VARCHAR(100) NOT NULL,
    thread_ts VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{{}}'::jsonb
);

-- 3. Chat messages table to store all raw user and assistant turns
CREATE TABLE IF NOT EXISTS chat_messages (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL REFERENCES conversations(session_id) ON DELETE CASCADE,
    channel_id VARCHAR(100) NOT NULL,
    user_id VARCHAR(100) NOT NULL,
    role VARCHAR(50) NOT NULL,
    content TEXT NOT NULL,
    tool_calls JSONB,
    metadata JSONB DEFAULT '{{}}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 4. Chat embeddings table storing chunks with pgvector
CREATE TABLE IF NOT EXISTS chat_embeddings (
    id BIGSERIAL PRIMARY KEY,
    message_id BIGINT REFERENCES chat_messages(id) ON DELETE CASCADE,
    session_id VARCHAR(255) NOT NULL,
    channel_id VARCHAR(100) NOT NULL,
    speaker_role VARCHAR(50) NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector({settings.EMBEDDING_DIM}) NOT NULL,
    metadata JSONB DEFAULT '{{}}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 5. B-tree indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_conversations_channel ON conversations (channel_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages (session_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_chat_messages_channel ON chat_messages (channel_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_chat_embeddings_channel ON chat_embeddings (channel_id);
CREATE INDEX IF NOT EXISTS idx_chat_embeddings_session ON chat_embeddings (session_id);

-- 6. HNSW vector index for high-performance cosine similarity search
CREATE INDEX IF NOT EXISTS idx_chat_embeddings_hnsw ON chat_embeddings 
USING hnsw (embedding vector_cosine_ops);

-- 7. Multi-workspace Slack installations and tool integrations
CREATE TABLE IF NOT EXISTS workspaces (
    team_id TEXT PRIMARY KEY,
    team_name TEXT NOT NULL,
    bot_token TEXT NOT NULL,
    bot_user_id TEXT,
    authed_user_id TEXT,
    scope TEXT,
    installed_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    enabled_tools JSONB DEFAULT '["calendar", "email"]'::jsonb,
    google_credentials JSONB DEFAULT NULL,
    google_user_email TEXT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_workspaces_installed_at ON workspaces (installed_at DESC);

-- 8. Per-user Google Account OAuth credentials (strictly isolated by team_id and user_id)
CREATE TABLE IF NOT EXISTS user_google_accounts (
    team_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    google_user_email TEXT NOT NULL,
    google_credentials JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (team_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_user_google_accounts_lookup ON user_google_accounts (team_id, user_id);
"""


async def create_schema() -> bool:
    """Execute schema creation statements against PostgreSQL database."""
    pool = await db_pool.get_pool()
    if not pool:
        logger.warning("Database pool not available. Schema migration skipped.")
        return False

    try:
        logger.info("Running database migrations (pgvector schema)...")
        async with pool.acquire() as conn:
            # Enable extension first in separate statement (safe on AWS RDS if extension already installed)
            try:
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            except Exception as ext_err:
                logger.warning("Could not execute 'CREATE EXTENSION vector' (may already exist or requires rds_superuser): %s", ext_err)
            
            # Register vector in connection
            from pgvector.asyncpg import register_vector
            await register_vector(conn)
            
            # Create tables and indexes
            await conn.execute(SCHEMA_SQL)

            # Safe column additions for multi-tenant workspace isolation
            await conn.execute("""
                ALTER TABLE conversations ADD COLUMN IF NOT EXISTS team_id VARCHAR(100);
                ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS team_id VARCHAR(100);
                ALTER TABLE chat_embeddings ADD COLUMN IF NOT EXISTS team_id VARCHAR(100);
                CREATE INDEX IF NOT EXISTS idx_conversations_team ON conversations (team_id);
                CREATE INDEX IF NOT EXISTS idx_chat_messages_team ON chat_messages (team_id);
                CREATE INDEX IF NOT EXISTS idx_chat_embeddings_team ON chat_embeddings (team_id);
            """)
        logger.info("Database schema initialized successfully with pgvector and user isolation support.")
        return True
    except Exception as e:
        logger.error("Failed to initialize database schema: %s", e)
        return False
