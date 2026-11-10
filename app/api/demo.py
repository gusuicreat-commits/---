"""一键演示的启动、停止、重置和进度接口。"""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/demo")


class StartRequest(BaseModel):
    speed: float = Field(1.0, ge=0.25, le=4, allow_inf_nan=False)


@router.post("/start")
def start(body: StartRequest, request: Request):
    return request.app.state.demo.start(body.speed)


@router.post("/stop")
def stop(request: Request):
    return request.app.state.demo.stop()


@router.post("/reset")
def reset(request: Request):
    return request.app.state.demo.reset()


@router.get("/status")
def status(request: Request):
    return request.app.state.demo.status()
