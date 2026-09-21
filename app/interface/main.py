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
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.interface.dependencies import build_container
from app.interface.routes import ask, code_health, diagram, impact, ingest, investigate_stream, onboarding, source

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

app.include_router(ingest.router)
app.include_router(ask.router)
app.include_router(investigate_stream.router)
app.include_router(diagram.router)
app.include_router(onboarding.router)
app.include_router(source.router)
app.include_router(impact.router)
app.include_router(code_health.router)

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