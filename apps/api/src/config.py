import sys
import logging
import os
import re
from typing import ClassVar
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from src.catalog import DEFAULT_CATALOG_SUBJECTS, DEFAULT_GER_SUBJECTS
from src.terms import TermCode

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None if os.environ.get("APP_ENV") == "test" else ".env",
        extra="ignore",
    )

    DATABASE_URL:       str
    SUPABASE_URL:       str
    SUPABASE_ANON_KEY:  str
    CURRENT_TERM:       TermCode = "202690"
    CATALOG_SUBJECTS:    str = DEFAULT_CATALOG_SUBJECTS
    GER_SUBJECTS:        str = DEFAULT_GER_SUBJECTS
    APP_ENV:            str = "development"
    CORS_ORIGINS:       str = "http://localhost:3000"
    SENTRY_DSN:         str = ""
    LOG_LEVEL:          str = "INFO"

    @field_validator("CATALOG_SUBJECTS", "GER_SUBJECTS")
    @classmethod
    def normalize_subjects(cls, value: str) -> str:
        subjects = [subject.strip() for subject in value.split(",")]
        if any(not re.fullmatch(r"[A-Za-z]{2,5}", subject) for subject in subjects):
            raise ValueError("Use comma-separated subject codes of 2–5 ASCII letters; empty entries are not allowed.")
        return ",".join(dict.fromkeys(subject.upper() for subject in subjects))

    @property
    def catalog_subjects(self) -> list[str]:
        return self.CATALOG_SUBJECTS.split(",")

    @property
    def ger_subjects(self) -> list[str]:
        return self.GER_SUBJECTS.split(",")

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
