"""
FastAPI application entry point. Wires the composition root
(dependencies.py) into app.state at startup via lifespan, mounts
routes, and serves the frontend.
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv

# Deliberately before the imports below: they pull in the composition
# root, and every setting it reads must already be in the environment.
load_dotenv()

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402

from app.interface.dependencies import build_container  # noqa: E402
from app.interface.routes import (  # noqa: E402
    agent_tooling,
    architecture,
    architecture_delta,
    ask,
    code_health,
    diagram,
    handbook,
    impact,
    ingest,
    investigate_stream,
    onboarding,
    sequence,
    source,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Building application container (LLM client, vector store, cache, etc)...")
    app.state.container = build_container()
    logger.info("Application container ready.")
    yield
    logger.info("Shutting down.")


app = FastAPI(title="Repo Pilot", lifespan=lifespan)

@app.middleware("http")
async def security_headers(request, call_next):
    """
    Baseline response headers.

    This app ingests arbitrary public repositories and renders their file
    names and symbols, so it is worth being strict about the cheap things
    before it is hosted anywhere.

    No Content-Security-Policy yet, deliberately rather than by oversight.
    A blob: document can inherit the creating page's CSP, and the four
    generated documents depend on inline <script> and inline <style> to
    stay self-contained with no network. A policy strict enough to be
    worth having would break them in the viewer, so it needs its own pass
    with nonces threaded through both renderers.
    """
    response = await call_next(request)
    # The generated documents are served as text/html on purpose; never
    # let a browser second-guess a content type it was given.
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    # Nothing here is meant to be framed by another site. The document
    # viewer frames a blob: URL, not this app, so this costs nothing.
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # No feature here needs a camera, a microphone or a location.
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=(), interest-cohort=()"
    )
    return response


app.include_router(ingest.router)
app.include_router(ask.router)
app.include_router(investigate_stream.router)
app.include_router(diagram.router)
app.include_router(onboarding.router)
app.include_router(source.router)
app.include_router(impact.router)
app.include_router(code_health.router)
app.include_router(handbook.router)
app.include_router(agent_tooling.router)
app.include_router(architecture.router)
app.include_router(architecture_delta.router)
app.include_router(sequence.router)

templates = Jinja2Templates(directory="app/interface/templates")
app.mount("/static", StaticFiles(directory="app/interface/static"), name="static")

# Appended to static asset URLs so browsers pick up new CSS/JS after a
# restart instead of serving a cached copy of the old interface.
STATIC_VERSION = str(int(time.time()))


@app.get("/")
async def landing(request: Request):
    return templates.TemplateResponse(request, "landing.html", {"static_version": STATIC_VERSION})


@app.get("/app")
async def app_page(request: Request):
    return templates.TemplateResponse(request, "app.html", {"static_version": STATIC_VERSION})


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s", request.url.path)
    return JSONResponse(status_code=500, content={"error": "internal_error", "detail": "Something went wrong."})
