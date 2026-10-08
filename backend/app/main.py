from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.config import cfg
from app.database.db import init_db
from app.services.container import services
from playwright.async_api import Error as PlaywrightError

from app.utils.errors import AppError
from app.utils.logging_utils import setup_logging


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    init_db()
    yield
    await services.pw.stop()


app = FastAPI(title="WordPress Product Image Automation", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=list(cfg.cors_origins), allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type"],
)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, e: AppError):
    return JSONResponse(status_code=e.status, content={"error": {"code": e.code, "message": e.message, "details": e.details}})


@app.exception_handler(PlaywrightError)
async def playwright_error_handler(_: Request, e: PlaywrightError):
    msg = (str(e).strip().splitlines() or ["Browser error"])[0].replace("Page.goto: ", "")
    return JSONResponse(status_code=502, content={"error": {"code": "browser_error", "message": msg, "details": None}})


app.include_router(router)


@app.get("/health")
async def health():
    return {"ok": True}