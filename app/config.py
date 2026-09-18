from functools import lru_cache
from typing import Optional, Any
import os
import urllib.parse
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
    # AWS Configuration
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_REGION: str = "ap-south-1"

    # Database (AWS RDS PostgreSQL with pgvector for RAG)
    DATABASE_URL: str = ""
    POSTGRES_HOST: str = ""
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = ""
    POSTGRES_USER: str = ""
    POSTGRES_PASSWORD: str = ""
    DB_SSLMODE: str = "require"  # AWS RDS requires SSL by default
    DB_POOL_MIN_SIZE: int = 2
    DB_POOL_MAX_SIZE: int = 10

    # RAG and Embedding Configuration
    ENABLE_RAG: bool = True
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    EMBEDDING_DIM: int = 384
    RAG_TOP_K: int = 6
    RAG_SIMILARITY_THRESHOLD: float = 0.45
    SHORT_TERM_MEMORY_LIMIT: int = 12

    def model_post_init(self, __context: Any) -> None:
        """Parse and synchronize DATABASE_URL with host, port, and AWS SSL settings."""
        if self.DATABASE_URL:
            raw_url = self.DATABASE_URL.strip().strip("'\"")
            if raw_url.startswith("postgres://"):
                raw_url = "postgresql://" + raw_url[len("postgres://"):]
            self.DATABASE_URL = raw_url

            try:
                parsed = urllib.parse.urlparse(raw_url)
                if parsed.hostname:
                    self.POSTGRES_HOST = parsed.hostname
                if parsed.port:
                    self.POSTGRES_PORT = parsed.port
                if parsed.path and parsed.path.lstrip("/"):
                    self.POSTGRES_DB = parsed.path.lstrip("/")
                if parsed.username:
                    self.POSTGRES_USER = parsed.username
                if parsed.password:
                    self.POSTGRES_PASSWORD = parsed.password

                # Check query params for sslmode
                if parsed.query:
                    q_params = urllib.parse.parse_qs(parsed.query)
                    ssl_param = q_params.get("sslmode", [None])[0] or q_params.get("ssl", [None])[0]
                    if ssl_param:
                        self.DB_SSLMODE = ssl_param

                # If connecting to AWS RDS, automatically default to require SSL unless explicitly disabled
                if parsed.hostname and "rds.amazonaws.com" in parsed.hostname.lower():
                    if self.DB_SSLMODE.lower() != "disable":
                        self.DB_SSLMODE = "require"
            except Exception:
                pass

    def get_database_dsn(self) -> str:
        """Return clean database DSN formatted for asyncpg connecting to AWS RDS."""
        if not self.DATABASE_URL:
            raise ValueError(
                "DATABASE_URL is not set. Please provide your AWS RDS PostgreSQL connection string in .env "
                "(e.g. postgresql://user:password@<rds-endpoint>:5432/<dbname>)"
            )

        url = self.DATABASE_URL.strip().strip("'\"")
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]

        # asyncpg does not accept sslmode query parameter in the connection URL
        try:
            parsed = urllib.parse.urlparse(url)
            if parsed.query:
                q = urllib.parse.parse_qs(parsed.query)
                q.pop("sslmode", None)
                q.pop("ssl", None)
                new_query = urllib.parse.urlencode(q, doseq=True)
                url = urllib.parse.urlunparse(parsed._replace(query=new_query))
        except Exception:
            pass
        return url

    def validate_required_settings(self) -> None:
        """Validate core required environment variables on startup."""
        missing = []
        if not self.SLACK_BOT_TOKEN:
            missing.append("SLACK_BOT_TOKEN")
        if not self.GROQ_API_KEY:
            missing.append("GROQ_API_KEY")
        if self.ENABLE_RAG and not self.DATABASE_URL:
            missing.append("DATABASE_URL (AWS RDS PostgreSQL connection URL)")
        if missing:
            raise ValueError(f"Missing required environment variable(s): {', '.join(missing)}")


@lru_cache()
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()


settings = get_settings()
