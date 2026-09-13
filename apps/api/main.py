import logging
import os
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import Depends, FastAPI, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.config import settings
from src.dependencies import get_db
from src.schemas.status import DegradedHealthResponse, HealthResponse, ScraperStatusResponse, VersionResponse
from src.services.meeting_integrity import IncompleteMeetingData

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)

if settings.SENTRY_DSN:
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.APP_ENV,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            SqlalchemyIntegration(),
        ],
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
        send_default_pii=False,
    )
    logger.info("Sentry initialized.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_for_production()

    engine = create_async_engine(
        settings.DATABASE_URL,
        pool_size=3,
        max_overflow=2,
        pool_pre_ping=True,
    )
    app.state.engine = engine
    app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("Database connectivity verified.")
    except Exception as e:
        logger.critical(f"Cannot connect to database at startup: {e}")
        if settings.APP_ENV == "production":
            import sys
            sys.exit(1)

    yield

    await engine.dispose()
    logger.info("Database engine disposed.")


app = FastAPI(title="NJIT Schedule Builder API", lifespan=lifespan)

CORS_ORIGINS = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(IncompleteMeetingData)
async def incomplete_meeting_data(request: Request, error: IncompleteMeetingData):
    return JSONResponse(status_code=503, content={"detail": str(error)})


@app.get("/health", response_model=HealthResponse, responses={503: {"model": DegradedHealthResponse}})
async def health(request: Request):
    try:
        engine = request.app.state.engine
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT COUNT(*) FROM sections LIMIT 1"))
            section_count = result.scalar()
        return {"status": "ok", "db": "connected", "sections": section_count, "env": settings.APP_ENV}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return Response(
            content='{"status": "degraded", "error": "db_unreachable"}',
            status_code=503,
            media_type="application/json",
        )


@app.get("/api/scraper/status", response_model=ScraperStatusResponse)
async def scraper_status(db: AsyncSession = Depends(get_db)):
    """
    Returns the last Banner scrape timestamp and status.
    Used by the frontend to show a staleness warning when seat data is old.
    """
    result = await db.execute(
        text("""
            SELECT status, finished_at, sections_upserted, error_message
            FROM scraper_runs
            WHERE scraper = 'banner'
            ORDER BY started_at DESC
            LIMIT 1
        """)
    )
    row = result.mappings().first()

    if not row:
        return {"last_scrape": None, "status": "never_run", "sections_upserted": None, "error_message": None}

    return {
        "last_scrape":       row["finished_at"].isoformat() if row["finished_at"] else None,
        "status":            row["status"],
        "sections_upserted": row["sections_upserted"],
        "error_message":     row["error_message"],
    }


@app.get("/api/version", response_model=VersionResponse)
async def version():
    return {
        "version": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "unknown")[:8],
        "env": settings.APP_ENV,
        "term": settings.CURRENT_TERM,
    }


# Routers — imported after `limiter` is defined above so the circular import
# (`from main import limiter` inside the router) resolves correctly.
from src.routers.schedule import router as schedule_router  # noqa: E402
from src.routers.plan import router as plan_router  # noqa: E402
from src.routers.courses import router as courses_router  # noqa: E402
app.include_router(schedule_router)
app.include_router(plan_router)
app.include_router(courses_router)
