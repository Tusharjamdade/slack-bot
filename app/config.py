from functools import lru_cache
from typing import Optional
import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Slack Configuration
    SLACK_BOT_TOKEN: str = ""

    # Groq Configuration
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # Server Configuration
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False

    # Google API Configuration
    GOOGLE_CREDENTIALS_FILE: str = "credentials.json"
    GOOGLE_TOKEN_FILE: str = "token.json"
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    GOOGLE_REFRESH_TOKEN: Optional[str] = None

    # Google Keep Configuration (for personal accounts using gkeepapi)
    GOOGLE_KEEP_USERNAME: Optional[str] = None
    GOOGLE_KEEP_PASSWORD: Optional[str] = None
    GOOGLE_KEEP_MASTER_TOKEN: Optional[str] = None

    # Database (PostgreSQL with pgvector - Local or AWS RDS)
    DATABASE_URL: Optional[str] = None
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "slackbot"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    DB_SSLMODE: str = "prefer"  # Set to "require" for AWS RDS
    DB_POOL_MIN_SIZE: int = 2
    DB_POOL_MAX_SIZE: int = 10

    # RAG and Embedding Configuration
    ENABLE_RAG: bool = True
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    EMBEDDING_DIM: int = 384
    RAG_TOP_K: int = 6
    RAG_SIMILARITY_THRESHOLD: float = 0.45
    SHORT_TERM_MEMORY_LIMIT: int = 12

    def get_database_dsn(self) -> str:
        """Return database DSN formatted for asyncpg."""
        if self.DATABASE_URL:
            # Handle postgres:// vs postgresql://
            url = self.DATABASE_URL
            if url.startswith("postgres://"):
                url = "postgresql://" + url[len("postgres://"):]
            return url
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@"
            f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    def validate_required_settings(self) -> None:
        """Validate core required environment variables on startup."""
        missing = []
        if not self.SLACK_BOT_TOKEN:
            missing.append("SLACK_BOT_TOKEN")
        if not self.GROQ_API_KEY:
            missing.append("GROQ_API_KEY")
        if missing:
            raise ValueError(f"Missing required environment variable(s): {', '.join(missing)}")


@lru_cache()
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()


settings = get_settings()
