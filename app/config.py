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
