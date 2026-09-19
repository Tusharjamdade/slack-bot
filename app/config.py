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

    # Slack Configuration (Single bot token fallback & Multi-workspace OAuth)
    SLACK_BOT_TOKEN: str = ""
    SLACK_CLIENT_ID: str = ""
    SLACK_CLIENT_SECRET: str = ""
    SLACK_SIGNING_SECRET: str = ""
    APP_BASE_URL: str = "http://localhost:8000"
    SLACK_REDIRECT_URI: Optional[str] = None
    SLACK_SCOPES: str = "chat:write,app_mentions:read,channels:history,im:history,groups:history,channels:join,channels:read,groups:read"
    DEFAULT_SLACK_CHANNEL: Optional[str] = None  # Fallback channel for proactive alerts

    # Event-Driven & Proactive Notifications Configuration
    ENABLE_EVENT_NOTIFICATIONS: bool = True
    EVENT_POLL_INTERVAL_GMAIL: int = 30       # Poll unread emails every 30 seconds
    EVENT_POLL_INTERVAL_CALENDAR: int = 60    # Poll upcoming meetings every 60 seconds
    MEETING_ALERT_WINDOW_MINUTES: int = 30    # Alert for meetings starting within next 30 minutes
    TIMEZONE: str = "Asia/Kolkata"

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
    GOOGLE_REDIRECT_URI: Optional[str] = None
    GOOGLE_REFRESH_TOKEN: Optional[str] = None

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
    RAG_TOP_K: int = 2
    RAG_SIMILARITY_THRESHOLD: float = 0.45
    SHORT_TERM_MEMORY_LIMIT: int = 4

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

    def get_slack_redirect_uri(self) -> str:
        """Get effective Slack OAuth redirect URI."""
        if self.SLACK_REDIRECT_URI:
            return self.SLACK_REDIRECT_URI
        base = self.APP_BASE_URL.rstrip("/")
        return f"{base}/slack/oauth/callback"

    def get_slack_install_url(self, state: str = "") -> str:
        """Construct official Slack OAuth v2 authorization redirect URL."""
        client_id = self.SLACK_CLIENT_ID
        redirect_uri = urllib.parse.quote(self.get_slack_redirect_uri(), safe="")
        scope = urllib.parse.quote(self.SLACK_SCOPES, safe="")
        url = f"https://slack.com/oauth/v2/authorize?client_id={client_id}&scope={scope}&redirect_uri={redirect_uri}"
        if state:
            url += f"&state={urllib.parse.quote(state, safe='')}"
        return url

    def get_google_redirect_uri(self) -> str:
        """Get effective Google OAuth redirect URI."""
        if self.GOOGLE_REDIRECT_URI:
            return self.GOOGLE_REDIRECT_URI
        base = self.APP_BASE_URL.rstrip("/")
        return f"{base}/auth/google/callback"

    def get_google_auth_url(self, state: str = "") -> str:
        """Construct Google OAuth consent URL for Calendar and Gmail."""
        client_id = self.GOOGLE_CLIENT_ID or ""
        redirect_uri = urllib.parse.quote(self.get_google_redirect_uri(), safe="")
        
        scopes_list = [
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/userinfo.email",
            "openid",
        ]

        scopes = urllib.parse.quote(" ".join(scopes_list), safe="")
        url = (
            f"https://accounts.google.com/o/oauth2/v2/auth"
            f"?client_id={client_id}"
            f"&response_type=code"
            f"&scope={scopes}"
            f"&redirect_uri={redirect_uri}"
            f"&access_type=offline"
            f"&prompt=consent"
        )
        if state:
            url += f"&state={urllib.parse.quote(state, safe='')}"
        return url

    def validate_required_settings(self) -> None:
        """Validate core required environment variables on startup."""
        missing = []
        if not self.SLACK_BOT_TOKEN and not self.SLACK_CLIENT_ID:
            missing.append("SLACK_BOT_TOKEN or SLACK_CLIENT_ID")
        if not self.GROQ_API_KEY:
            missing.append("GROQ_API_KEY")
        if self.ENABLE_RAG and not self.DATABASE_URL:
            missing.append("DATABASE_URL (PostgreSQL connection URL)")
        if missing:
            raise ValueError(f"Missing required environment variable(s): {', '.join(missing)}")


@lru_cache()
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()


settings = get_settings()
