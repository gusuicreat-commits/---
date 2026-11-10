import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException

from app import backup
from app.api import contracts, export, records, rules, validation, voice
from app.config import get_settings
from app.db import get_engine, init_db
from app.errors import AppError
from app.fields import load_fields


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(application):
        try:
            backup.ensure_today()
        except Exception:
            logging.exception("每日数据库备份失败")
        yield

    application = FastAPI(title="产品参数校验系统", lifespan=lifespan)
    application.state.engine = get_engine()
    init_db(application.state.engine)

    @application.exception_handler(AppError)
    async def business_error(request: Request, exc: AppError):
        return JSONResponse(status_code=exc.status, content={
            "error": {"code": exc.code, "message": exc.message, **exc.extra}})

    @application.exception_handler(OperationalError)
    async def database_error(request: Request, exc: OperationalError):
        logging.exception("数据库暂时不可用")
        return JSONResponse(status_code=503, content={
            "error": {"code": "DB_UNAVAILABLE", "message": "系统暂时不可用"}})

    @application.exception_handler(RequestValidationError)
    async def request_error(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={
            "error": {"code": "BAD_REQUEST", "message": "请求格式不正确"}})

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={
            "error": {"code": "NOT_FOUND" if exc.status_code == 404 else "BAD_REQUEST",
                      "message": "资源不存在" if exc.status_code == 404 else "请求格式不正确"}})

    @application.get("/api/health")
    def health():
        return {"ok": True}

    @application.get("/api/fields")
    def fields():
        return load_fields()

    for router in (contracts.router, records.router, rules.router, validation.router,
                   export.router, voice.router):
        application.include_router(router)
    web_dir = get_settings().web_dir
    if web_dir.exists():
        application.mount("/", StaticFiles(directory=web_dir, html=True))
    return application


app = create_app()
