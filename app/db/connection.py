import ssl
import logging
import asyncio
from typing import Optional, Dict, Any
import asyncpg
from pgvector.asyncpg import register_vector

from app.config import settings

logger = logging.getLogger(__name__)


class DatabasePool:
    """Async connection pool manager for PostgreSQL with pgvector support."""

    def __init__(self):
        self._pool: Optional[asyncpg.Pool] = None
        self._is_initialized = False

    async def _init_connection(self, conn: asyncpg.Connection):
        """Configure each new connection from pool to support pgvector."""
        try:
            await register_vector(conn)
        except Exception as e:
            logger.warning("Could not register pgvector on connection (extension may not exist yet): %s", e)

    async def get_pool(self) -> Optional[asyncpg.Pool]:
        """Return the active connection pool or initialize if not ready."""
        if not self._pool:
            await self.initialize()
        return self._pool

    async def initialize(self) -> bool:
        """Initialize connection pool with RDS SSL settings and pgvector registration."""
        if self._pool:
            return True

        dsn = settings.get_database_dsn()
        ssl_mode = settings.DB_SSLMODE.lower()
        ssl_context = None

        if ssl_mode in ("require", "verify-ca", "verify-full"):
            ssl_context = ssl.create_default_context()
            if ssl_mode == "require":
                # For RDS without custom CA bundle validation
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

        try:
            logger.info("Connecting to PostgreSQL at %s:%s (db=%s, ssl=%s)...",
                        settings.POSTGRES_HOST, settings.POSTGRES_PORT, settings.POSTGRES_DB, ssl_mode)
            self._pool = await asyncpg.create_pool(
                dsn=dsn,
                min_size=settings.DB_POOL_MIN_SIZE,
                max_size=settings.DB_POOL_MAX_SIZE,
                init=self._init_connection,
                ssl=ssl_context,
                command_timeout=60,
            )
            self._is_initialized = True
            logger.info("Successfully connected to PostgreSQL connection pool.")
            return True
        except Exception as e:
            logger.error("Failed to connect to PostgreSQL database: %s", e)
            self._pool = None
            self._is_initialized = False
            return False

    async def close(self):
        """Close connection pool gracefully."""
        if self._pool:
            logger.info("Closing PostgreSQL connection pool...")
            await self._pool.close()
            self._pool = None
            self._is_initialized = False

    async def check_health(self) -> Dict[str, Any]:
        """Health check verifying database connection and pgvector extension."""
        health = {
            "connected": False,
            "pgvector_installed": False,
            "latency_ms": None,
            "error": None,
        }
        if not self._pool:
            health["error"] = "Database pool not initialized"
            return health

        start_time = asyncio.get_event_loop().time()
        try:
            async with self._pool.acquire() as conn:
                # 1. Ping
                await conn.fetchval("SELECT 1")
                elapsed = (asyncio.get_event_loop().time() - start_time) * 1000
                health["connected"] = True
                health["latency_ms"] = round(elapsed, 2)

                # 2. Check pgvector extension
                ext = await conn.fetchval("SELECT extname FROM pg_extension WHERE extname = 'vector'")
                health["pgvector_installed"] = bool(ext)
        except Exception as e:
            health["error"] = str(e)
            logger.error("Database health check failed: %s", e)

        return health


db_pool = DatabasePool()


async def init_db() -> bool:
    """Convenience helper to initialize database pool and create schema."""
    success = await db_pool.initialize()
    if success:
        from app.db.schema import create_schema
        await create_schema()
    return success


async def close_db():
    """Convenience helper to close database pool."""
    await db_pool.close()


async def check_db_health() -> Dict[str, Any]:
    """Check database health status."""
    return await db_pool.check_health()
