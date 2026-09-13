import sys
import logging
import os
from typing import ClassVar
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None if os.environ.get("APP_ENV") == "test" else ".env",
        extra="ignore",
    )

    DATABASE_URL:       str
    SUPABASE_URL:       str
    SUPABASE_ANON_KEY:  str
    CURRENT_TERM:       str = "202690"
    APP_ENV:            str = "development"
    CORS_ORIGINS:       str = "http://localhost:3000"
    SENTRY_DSN:         str = ""
    LOG_LEVEL:          str = "INFO"

    REQUIRED_IN_PRODUCTION: ClassVar[list[str]] = [
        "DATABASE_URL",
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "CORS_ORIGINS",
        "CURRENT_TERM",
    ]

    def validate_for_production(self) -> None:
        errors = [
            field
            for field in self.REQUIRED_IN_PRODUCTION
            if not getattr(self, field, None) or str(getattr(self, field)).startswith("CHANGE_ME")
        ]

        if errors:
            msg = (
                f"FATAL: Missing required environment variables: {errors}. "
                f"Set these in Railway (production) or .env (development) before starting."
            )
            if self.APP_ENV == "production":
                logger.critical(msg)
                sys.exit(1)
            else:
                logger.warning(msg)

        if not self.SENTRY_DSN:
            logger.warning(
                "SENTRY_DSN is not configured — error tracking is disabled. "
                "Set SENTRY_DSN in Railway env vars for production observability."
            )


settings = Settings()
