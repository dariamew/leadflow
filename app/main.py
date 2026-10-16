import asyncio
import logging
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.administration import invitation_router
from app.administration import router as users_router
from app.api import health_router, router
from app.auth import router as auth_router
from app.config import Settings
from app.db import make_database
from app.integrations.bitrix import BitrixClient, BitrixError
from app.monitor import run_monitor
from app.monitor_api import router as monitor_router
from app.qualification.extractors import ExtractionError, LLMExtractor, RulesExtractor
from app.qualification.pipeline import QualificationPipeline
from app.source_settings import router as source_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        # httpx INFO request logs contain full incoming-webhook URLs.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        engine, session_factory = make_database(settings.database_url)
        async with httpx.AsyncClient(follow_redirects=False) as http:
            application.state.settings = settings
            application.state.engine = engine
            application.state.session_factory = session_factory
            application.state.password_slots = asyncio.Semaphore(2)
            application.state.bitrix = BitrixClient(
                http,
                settings.bitrix_webhook_url.get_secret_value(),
                settings.bitrix_timeout_seconds,
            )
            extractor = (
                RulesExtractor()
                if settings.extractor == "rules"
                else LLMExtractor(
                    http,
                    settings.llm_base_url,
                    settings.llm_api_key.get_secret_value(),
                    settings.llm_model,
                    settings.llm_timeout_seconds,
                )
            )
            application.state.pipeline = QualificationPipeline(
                extractor,
                settings.priority_budget_threshold_rub,
            )
            monitor_task = (
                asyncio.create_task(run_monitor(application))
                if settings.monitor_worker_enabled
                else None
            )
            try:
                yield
            finally:
                if monitor_task:
                    monitor_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await monitor_task
                await engine.dispose()

    application = FastAPI(
        title="Lead Qualifier",
        version="0.1.0",
        description="Квалификация заявок с цитатами и подтверждением записи в Битрикс24.",
        lifespan=lifespan,
    )
    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(invitation_router)
    application.include_router(users_router)
    application.include_router(monitor_router)
    application.include_router(source_router)
    application.include_router(router)

    @application.middleware("http")
    async def private_api_responses(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(BitrixError)
    async def bitrix_error(request: Request, error: BitrixError):
        return JSONResponse(
            status_code=503 if error.code == "NOT_CONFIGURED" else 502,
            content={
                "detail": "Bitrix24 request failed",
                "code": error.code,
                "outcome_unknown": error.outcome_unknown,
            },
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        if request.url.path.startswith("/api/v1/auth/"):
            # Validation errors must never echo password input.
            return JSONResponse(status_code=422, content={"detail": "Invalid login input"})
        return await request_validation_exception_handler(request, error)

    @application.exception_handler(ExtractionError)
    async def extraction_error(request: Request, error: ExtractionError):
        return JSONResponse(status_code=502, content={"detail": str(error)})

    @application.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError):
        # Do not return SQL statements, parameters, connection strings or personal data.
        return JSONResponse(status_code=503, content={"detail": "Database operation failed"})

    return application


app = create_app()
