"""事件查询、实时推送与客户端在线状态。"""

import asyncio
import json
import threading
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.errors import Invalid
from app.events import BOOT, bus
from app.timeutil import now_bj

router = APIRouter(prefix="/api")
_presence = {}
_presence_lock = threading.Lock()
PING_INTERVAL = 15


@router.get("/events")
def get_events(after: int = 0, limit: int = Query(200, ge=1, le=500)):
    return {"boot": BOOT, "last_id": bus.last_id(), "items": bus.since(after, limit)}


def _sse(event):
    return f"id: {event['id']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


async def event_stream(after, request):
    # 先订阅再读取缓冲；用游标去重，避免补发和实时推送交界处丢事件。
    q = await bus.subscribe()
    try:
        yield "event: hello\ndata: " + json.dumps({"boot": BOOT, "last_id": bus.last_id()}) + "\n\n"
        for event in bus.since(after, limit=1000):
            if await request.is_disconnected():
                return
            after = event["id"]
            yield _sse(event)
        while not await request.is_disconnected():
            try:
                event = await asyncio.wait_for(q.get(), timeout=PING_INTERVAL)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if event["id"] > after:
                after = event["id"]
                yield _sse(event)
    finally:
        bus.unsubscribe(q)


@router.get("/events/stream")
async def stream(request: Request, after: int = 0):
    try:
        after = max(after, int(request.headers.get("Last-Event-ID", "0")))
    except ValueError:
        pass
    return StreamingResponse(event_stream(after, request), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class ClientEvent(BaseModel):
    type: Any = None
    source: str | None = None
    data: Any = None


@router.post("/events")
def post_event(body: ClientEvent):
    if body.type not in ("focus", "draft"):
        raise Invalid("BAD_EVENT", "事件类型不支持")
    try:
        valid = isinstance(body.data, dict) and len(
            json.dumps(body.data, ensure_ascii=False, allow_nan=False).encode("utf-8")) <= 8192
    except (TypeError, ValueError, OverflowError):
        valid = False
    if not valid:
        raise Invalid("BAD_EVENT", "事件内容不合法")
    return bus.publish(body.type, body.data,
                       body.source if body.source in ("phone", "screen") else None)


class Presence(BaseModel):
    role: Literal["phone", "screen"]
    client: str = Field(min_length=1, max_length=40)


@router.post("/presence")
def post_presence(body: Presence):
    with _presence_lock:
        _presence[body.role, body.client] = now_bj().timestamp()
    return {"ok": True}


@router.get("/presence")
def get_presence():
    now = now_bj().timestamp()
    result = {}
    with _presence_lock:
        for role in ("phone", "screen"):
            ages = [max(0, now - seen) for (kind, _), seen in _presence.items() if kind == role]
            clients = sum(age <= 10 for age in ages)
            result[role] = {"online": clients > 0, "clients": clients,
                            "last_seen_s": min(ages) if ages else None}
    return result
