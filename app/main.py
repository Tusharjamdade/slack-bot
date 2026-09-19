import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api.routes import router as api_router
from app.tools import all_tools
from app.db.connection import init_db, close_db
from app.services.rag.embedding_service import embedding_service
from app.services.slack_service import slack_service
from app.services.notification_service import notification_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("slack_ai_agent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    logger.info("==========================================")
    logger.info(" Starting Slack AI Agent")
    logger.info(" Model: %s", settings.GROQ_MODEL)
    logger.info(" Slack token configured: %s", bool(settings.SLACK_BOT_TOKEN))
    logger.info(" Groq API key configured: %s", bool(settings.GROQ_API_KEY))
    logger.info(" RAG enabled: %s (Model: %s)", settings.ENABLE_RAG, settings.EMBEDDING_MODEL)
    logger.info(" Proactive Notifications enabled: %s", settings.ENABLE_EVENT_NOTIFICATIONS)
    logger.info(" Active Tools (%d): %s", len(all_tools), [t.name for t in all_tools])
    logger.info("==========================================")

    # 1. Initialize AWS PostgreSQL + pgvector connection pool and schema
    try:
        db_ok = await init_db()
        if db_ok:
            logger.info("AWS PostgreSQL (pgvector) connection & schema ready.")
        else:
            logger.warning("AWS PostgreSQL connection deferred or not reachable at startup.")
    except Exception as e:
        logger.warning("Could not initialize AWS PostgreSQL on startup: %s", e)

    # 2. Pre-warm embedding model in non-blocking background thread if RAG is enabled
    if settings.ENABLE_RAG:
        async def _prewarm():
            try:
                logger.info("Pre-warming embedding model in background thread...")
                await asyncio.to_thread(embedding_service._get_model)
                logger.info("Embedding model pre-warmed successfully.")
            except Exception as e:
                logger.warning("Embedding model pre-warming deferred: %s", e)

        asyncio.create_task(_prewarm())

    # 3. Start background event notification service (Email, Calendar 30m alert, Timers)
    if settings.ENABLE_EVENT_NOTIFICATIONS:
        try:
            notification_service.start()
            logger.info("Proactive event notification service active.")
        except Exception as e:
            logger.warning("Could not start event notification service: %s", e)

    yield

    # Shutdown
    logger.info("Shutting down Slack AI Agent...")
    try:
        notification_service.stop()
    except Exception as e:
        logger.error("Error stopping notification service: %s", e)

    try:
        await slack_service.close()
    except Exception as e:
        logger.error("Error closing Slack client: %s", e)

    try:
        await close_db()
    except Exception as e:
        logger.error("Error closing database pool: %s", e)
    logger.info("Shutdown complete.")


def create_app() -> FastAPI:
    """Factory to create FastAPI application."""
    app = FastAPI(
        title="Slack AI Agent",
        description="Slack AI Bot powered by Groq and LangChain with Google Calendar and Gmail tools.",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)

    import os
    from fastapi.staticfiles import StaticFiles
    static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    return app


app = create_app()
