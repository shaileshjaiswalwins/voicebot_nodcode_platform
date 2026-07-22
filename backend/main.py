import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError

from .analysis_prompts import seed_default_analysis_prompts
from .auth import ensure_default_admin
from .routers import analytics, audit, auth, bots, campaigns, dialer_webhooks, evals, library, phone_numbers, pricing, runtime, settings, testcall, transcripts

_log = logging.getLogger("voicebot_admin")

app = FastAPI(title="No-Code Voice AI Platform Admin API")

origins = [o.strip() for o in os.getenv("DASHBOARD_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(bots.router)
app.include_router(settings.router)
app.include_router(runtime.router)
app.include_router(campaigns.router)
app.include_router(library.router)
app.include_router(transcripts.router)
app.include_router(analytics.router)
app.include_router(testcall.router)
app.include_router(evals.router)
app.include_router(phone_numbers.router)
app.include_router(audit.router)
app.include_router(pricing.router)
app.include_router(dialer_webhooks.router)


@app.exception_handler(PyMongoError)
async def _mongo_error_handler(_: Request, exc: PyMongoError) -> JSONResponse:
    """A database-layer failure mid-request (dropped connection, replica-set failover,
    query timeout) previously surfaced as a bare 'Internal Server Error' with no detail —
    callers couldn't tell a DB blip from a real bug. Now returns a specific 503 so the
    frontend can show 'try again' instead of a generic crash message."""
    _log.error("Database error handling request: %s", exc, exc_info=exc)
    return JSONResponse(status_code=503, content={"detail": "Database is temporarily unreachable. Please try again in a moment."})


@app.exception_handler(Exception)
async def _unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    _log.error("Unhandled error handling request: %s", exc, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "An unexpected server error occurred. It has been logged."})


@app.on_event("startup")
def on_startup() -> None:
    ensure_default_admin()
    seed_default_analysis_prompts()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
