import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api.routes import router as api_router
from app.tools import all_tools

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
    logger.info(" Active Tools (%d): %s", len(all_tools), [t.name for t in all_tools])
    logger.info("==========================================")
    yield
    logger.info("Shutting down Slack AI Agent.")


def create_app() -> FastAPI:
    """Factory to create FastAPI application."""
    app = FastAPI(
        title="Slack AI Agent",
        description="Slack AI Bot powered by Groq and LangChain with Google Calendar, Gmail, and Google Keep tools.",
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
    return app


app = create_app()
