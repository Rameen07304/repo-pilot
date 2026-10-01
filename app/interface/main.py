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
    proof,
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

# Every external origin the pages actually use, pinned. Grepped from the
# templates rather than remembered: the first attempt allowed the two
# script CDNs and silently broke the webfont, which loads from a third.
_CDN = "https://cdnjs.cloudflare.com https://cdn.jsdelivr.net"
_FONT_CSS = "https://fonts.googleapis.com"
_FONT_FILES = "https://fonts.gstatic.com"
_CSP = "; ".join(
    (
        "default-src 'self'",
        # 'unsafe-inline' is required by the generated documents; see the
        # middleware docstring. Nothing else may load script.
        f"script-src 'self' 'unsafe-inline' {_CDN}",
        f"style-src 'self' 'unsafe-inline' {_CDN} {_FONT_CSS}",
        # Diagrams and exported images are data: and blob: URLs we make.
        "img-src 'self' data: blob:",
        f"font-src 'self' data: {_FONT_FILES}",
        # The one that matters most: injected markup cannot phone home.
        "connect-src 'self'",
        # The document viewer frames blob: URLs produced by this origin.
        "frame-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)


@app.middleware("http")
async def security_headers(request, call_next):
    """
    Baseline response headers.

    This app ingests arbitrary public repositories and renders their file
    names and symbols, so it is worth being strict about the cheap things
    before it is hosted anywhere.

    The CSP allows inline script and style, which is weaker than a nonce
    policy and deliberate: the four generated documents are inline script
    and style by design, and a blob: document inherits the creating page's
    policy, so a stricter header here breaks them inside the viewer. What
    it still buys is the part that matters for a tool that renders
    arbitrary repository content — script, frames and connections can only
    come from this origin and the four pinned CDNs, so injected markup has
    nowhere to send anything.
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
    response.headers.setdefault("Content-Security-Policy", _CSP)
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
app.include_router(proof.router)

templates = Jinja2Templates(directory="app/interface/templates")
app.mount("/static", StaticFiles(directory="app/interface/static"), name="static")

# Appended to static asset URLs so browsers pick up new CSS/JS after a
# restart instead of serving a cached copy of the old interface.
STATIC_VERSION = str(int(time.time()))

# Routes in their own modules need these, and importing them back from
# this module would be circular. State is the seam that already exists.
app.state.templates = templates
app.state.static_version = STATIC_VERSION


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
