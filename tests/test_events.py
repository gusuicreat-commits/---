import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from starlette.requests import Request

from app import events
from app.api import contracts, export, records, rules, voice
from app.api import events as api
from app.voice import parser


@pytest.fixture(autouse=True)
def isolated_events(monkeypatch, tmp_path):
    current = events.EventBus()
    for module in (events, api, contracts, export, records, rules, voice):
        monkeypatch.setattr(module, "bus", current)
    monkeypatch.setattr(api, "_presence", {})
    monkeypatch.setenv("QC_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("QC_EXPORT_DIR", str(tmp_path / "exports"))
    return current


def test_buffer_sequence_and_beijing_time(monkeypatch):
    now = datetime(2026, 11, 10, 15, 16, 17, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(events, "now_bj", lambda: now)
    current = events.EventBus(maxlen=2)
    first = current.publish("focus", {"ht_code": None}, "phone")
    assert current.since(0) == [first]
    second = current.publish("focus", {}, "未知来源")
    third = current.publish("draft", {}, "screen")
    assert [first["id"], second["id"], third["id"]] == [1, 2, 3]
    assert re.fullmatch(r"[0-9a-f]{8}", events.BOOT)
    assert all(item["boot"] == events.BOOT for item in (first, second, third))
    assert first["ts"] == "2026-11-10 15:16:17"
    assert second["source"] is None
    assert current.since(0) == [second, third]
    assert current.since(1, limit=1) == [second]
    assert current.since(3) == []
    assert current.last_id() == 3


def test_threaded_publish_reaches_subscriber_in_order():
    async def check():
        current = events.EventBus()
        q = await current.subscribe()
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(current.publish, "draft", {"n": n}) for n in range(30)]
            for future in futures:
                future.result()
        received = [await asyncio.wait_for(q.get(), 1) for _ in range(30)]
        assert [item["id"] for item in received] == list(range(1, 31))
        current.unsubscribe(q)
        current.publish("focus", {})
        await asyncio.sleep(0)
        assert q.empty()
    asyncio.run(check())


@pytest.mark.parametrize("failure", ["schedule", "queue"])
def test_broken_subscriber_is_removed(monkeypatch, failure):
    async def check():
        current = events.EventBus()
        broken = await current.subscribe()
        healthy = await current.subscribe()

        def fail(*args):
            raise RuntimeError("模拟投递异常")

        if failure == "schedule":
            class BrokenLoop:
                call_soon_threadsafe = staticmethod(fail)
            current._subscribers[broken] = BrokenLoop()
        else:
            monkeypatch.setattr(broken, "put_nowait", fail)
        published = current.publish("focus", {})
        assert await asyncio.wait_for(healthy.get(), 1) == published
        assert broken not in current._subscribers
        current.unsubscribe(healthy)
    asyncio.run(check())


def test_parse_text_event(client):
    response = client.post("/api/voice/parse-text", json={
        "text": "直经一千二百五十", "form": "drum", "ht_code": "20261110-10", "source": "phone"})
    assert response.status_code == 200
    result = client.get("/api/events").json()
    assert result["boot"] == events.BOOT
    assert result["last_id"] == 1
    event = result["items"][0]
    assert event["type"] == "voice"
    assert event["source"] == "phone"
    assert event["data"] == {**response.json(), "form": "drum", "ht_code": "20261110-10"}


def test_audio_parse_event(client, monkeypatch):
    original = httpx.Client
    metadata = {"text": "厚度二十", "duration_s": 1, "elapsed_ms": 2,
                "denoise": None, "truncated": False}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=metadata))
    monkeypatch.setattr(voice.httpx, "Client", lambda **kw: original(transport=transport, **kw))
    response = client.post("/api/voice/parse", files={"audio": ("录音.wav", b"audio", "audio/wav")},
                           data={"form": "drum", "ht_code": "20261110-10", "source": "phone"})
    assert response.status_code == 200
    event = client.get("/api/events").json()["items"][0]
    assert event["source"] == "phone"
    assert event["data"] == {**response.json(), "form": "drum", "ht_code": "20261110-10"}
    assert event["data"]["asr"] == metadata


def test_record_event(client, contract, good_drum, isolated_events):
    after = isolated_events.last_id()
    response = client.post(f"/api/contracts/{contract}/records/drum?source=phone", json=good_drum)
    assert response.status_code == 201
    event, = client.get(f"/api/events?after={after}").json()["items"]
    assert event["type"] == "record"
    assert event["source"] == "phone"
    assert event["data"] == {**response.json(), "ht_code": contract, "kind": "drum"}
    assert event["data"]["validation"]["is_qualified"] == response.json()["validation"]["is_qualified"] == 1


def test_contract_and_rule_events(client):
    code = "20261110-10"
    created = client.post("/api/contracts?source=phone", json={"ht_code": code, "name": "客户"})
    updated = client.patch(f"/api/contracts/{code}?source=screen", json={"name": "新客户"})
    deleted = client.delete(f"/api/contracts/{code}")
    assert (created.status_code, updated.status_code, deleted.status_code) == (201, 200, 204)
    rule_id = client.get("/api/rules").json()["items"][0]["id"]
    rule = client.put(f"/api/rules/{rule_id}?source=screen", json={"min": 1, "max": 2})
    assert rule.status_code == 200
    items = client.get("/api/events").json()["items"]
    assert [item["type"] for item in items] == ["contract"] * 3 + ["rules"]
    assert [item["source"] for item in items] == ["phone", "screen", None, "screen"]
    for item, action, contract in zip(items, ("created", "updated", "deleted"),
                                      (created.json(), updated.json(), {"ht_code": code})):
        assert item["data"] == {"action": action, "contract": contract}
    assert items[-1]["data"] == {"rule": rule.json()}


def test_export_events(client, contract, good_drum, isolated_events):
    client.post(f"/api/contracts/{contract}/records/drum", json=good_drum)
    client.post(f"/api/contracts/{contract}/records/travel_drive", json={
        "reducer_nameplate": "ZQ500", "motor_nameplate": "YZR160", "noise": "80"})
    client.post("/api/contracts", json={"ht_code": "20261110-20", "name": "未录全"})
    after = isolated_events.last_id()
    one = client.post(f"/api/contracts/{contract}/export?source=phone")
    batch = client.post("/api/export/all?source=screen")
    assert one.status_code == batch.status_code == 200
    items = client.get(f"/api/events?after={after}").json()["items"]
    assert len(items) == 2
    assert [item["type"] for item in items] == ["export", "export"]
    assert [item["source"] for item in items] == ["phone", "screen"]
    assert items[0]["data"] == {"action": "one", **{key: one.json()[key] for key in
                                                    ("ht_code", "file", "sheets")}}
    assert items[1]["data"] == {"action": "batch", "succeeded": 1, "failed": 0, "skipped": 1}


@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/contracts", {"ht_code": "错误", "name": "客户"}),
    ("patch", "/api/contracts/20261110-10", {"name": "客户"}),
    ("delete", "/api/contracts/20261110-10", None),
    ("post", "/api/contracts/20261110-10/records/drum", {}),
    ("post", "/api/contracts/20261110-10/export", None),
    ("put", "/api/rules/999999", {"min": 1, "max": 2}),
    ("post", "/api/voice/parse-text", {"text": "直径一", "form": "错误"}),
])
def test_failed_business_request_has_no_event(client, isolated_events, method, path, body):
    after = isolated_events.last_id()
    response = client.request(method, path, **({"json": body} if body is not None else {}))
    assert response.status_code in (404, 422)
    assert isolated_events.since(after) == []


@pytest.mark.parametrize("failure", ["buffer", "delivery"])
def test_event_failure_does_not_fail_business(client, isolated_events, monkeypatch, failure):
    def fail(*args):
        raise RuntimeError("模拟内部事件故障")

    if failure == "buffer":
        class BrokenBuffer:
            append = staticmethod(fail)
        monkeypatch.setattr(isolated_events, "_events", BrokenBuffer())
    else:
        class BrokenLoop:
            call_soon_threadsafe = staticmethod(fail)
        isolated_events._subscribers[object()] = BrokenLoop()
    assert client.post("/api/contracts", json={"ht_code": "20261110-10", "name": "客户"}).status_code == 201
    assert client.post("/api/voice/parse-text", json={"text": "直径一", "form": "drum"}).status_code == 200


@pytest.mark.parametrize("kind,data", [
    ("focus", {"ht_code": None, "form": "contract"}),
    ("draft", {"ht_code": "20261110-10", "form": "drum", "fields": {"diameter": "1250"}}),
])
def test_client_events(client, kind, data):
    response = client.post("/api/events", json={"type": kind, "source": "phone", "data": data})
    assert response.status_code == 200
    assert response.json()["type"] == kind
    assert response.json()["data"] == data
    assert response.json()["source"] == "phone"


@pytest.mark.parametrize("kind,data,message", [
    ("record", {}, "事件类型不支持"),
    ([], {}, "事件类型不支持"),
    ("focus", [], "事件内容不合法"),
    ("draft", None, "事件内容不合法"),
    ("draft", {"text": "字" * 3000}, "事件内容不合法"),
])
def test_invalid_client_events(client, kind, data, message):
    response = client.post("/api/events", json={"type": kind, "source": "screen", "data": data})
    assert response.status_code == 422
    assert response.json()["error"] == {"code": "BAD_EVENT", "message": message}
    assert client.get("/api/events").json()["items"] == []


def test_source_normalization_and_absent_metadata(client):
    response = client.post("/api/voice/parse-text", json={
        "text": "厚度二十", "form": "drum", "source": "未知"})
    assert response.status_code == 200
    event = client.get("/api/events").json()["items"][0]
    assert event["source"] is None
    assert event["data"]["ht_code"] is None
    response = client.post("/api/events", json={"type": "focus", "source": "未知", "data": {}})
    assert response.json()["source"] is None


def test_event_query_limits(client, isolated_events):
    for _ in range(510):
        isolated_events.publish("focus", {})
    assert len(client.get("/api/events").json()["items"]) == 200
    result = client.get("/api/events?after=5&limit=500").json()
    assert len(result["items"]) == 500
    assert result["items"][0]["id"] == 6
    assert result["last_id"] == 510
    assert client.get("/api/events?limit=501").status_code == 422


def test_presence_expiration_and_multiple_clients(client, monkeypatch):
    now = datetime(2026, 11, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(api, "now_bj", lambda: now)
    assert client.get("/api/presence").json() == {
        role: {"online": False, "clients": 0, "last_seen_s": None} for role in ("phone", "screen")}
    for device in ("甲", "甲", "乙"):
        assert client.post("/api/presence", json={"role": "phone", "client": device}).status_code == 200
    assert client.get("/api/presence").json()["phone"] == {
        "online": True, "clients": 2, "last_seen_s": 0.0}
    now += timedelta(seconds=10)
    assert client.get("/api/presence").json()["phone"]["online"] is True
    now += timedelta(seconds=1)
    assert client.get("/api/presence").json()["phone"] == {
        "online": False, "clients": 0, "last_seen_s": 11.0}
    client.post("/api/presence", json={"role": "screen", "client": "甲"})
    assert client.get("/api/presence").json()["screen"]["online"] is True


@pytest.mark.parametrize("body", [{"role": "other", "client": "甲"},
                                  {"role": "phone", "client": "a" * 41}])
def test_invalid_presence(client, body):
    assert client.post("/api/presence", json=body).status_code == 422


class ConnectedRequest:
    disconnected = False

    async def is_disconnected(self):
        return self.disconnected


def test_sse_hello_backfill_live_and_cleanup(isolated_events):
    async def check():
        isolated_events.publish("focus", {})
        second = isolated_events.publish("draft", {})
        request = ConnectedRequest()
        stream = api.event_stream(1, request)
        try:
            hello = await anext(stream)
            assert hello == "event: hello\ndata: " + json.dumps({"boot": events.BOOT, "last_id": 2}) + "\n\n"
            # hello 后发布的事件同时进入缓冲和队列，不能重复推送。
            third = isolated_events.publish("focus", {})
            assert await anext(stream) == api._sse(second)
            assert await anext(stream) == api._sse(third)
            fourth = isolated_events.publish("draft", {})
            assert await asyncio.wait_for(anext(stream), 1) == api._sse(fourth)
            request.disconnected = True
            with pytest.raises(StopAsyncIteration):
                await anext(stream)
        finally:
            await stream.aclose()
        assert not isolated_events._subscribers
    asyncio.run(check())


def test_sse_ping_and_cancel_cleanup(isolated_events, monkeypatch):
    async def check():
        monkeypatch.setattr(api, "PING_INTERVAL", 0.01)
        stream = api.event_stream(0, ConnectedRequest())
        await anext(stream)
        assert await asyncio.wait_for(anext(stream), 1) == ": ping\n\n"
        monkeypatch.setattr(api, "PING_INTERVAL", 15)
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await stream.aclose()
        assert not isolated_events._subscribers
    asyncio.run(check())


@pytest.mark.parametrize("after,header,expected", [(1, "2", 3), (3, "1", 4), (1, "无效", 2)])
def test_sse_route_headers_and_last_event_id(isolated_events, after, header, expected):
    async def check():
        for _ in range(4):
            isolated_events.publish("focus", {})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        request = Request({"type": "http", "headers": [(b"last-event-id", header.encode())]}, receive)
        response = await api.stream(request, after)
        assert response.media_type == "text/event-stream"
        assert response.headers["Cache-Control"] == "no-cache"
        assert response.headers["X-Accel-Buffering"] == "no"
        try:
            assert (await anext(response.body_iterator)).startswith("event: hello\n")
            assert (await anext(response.body_iterator)).startswith(f"id: {expected}\n")
        finally:
            await response.body_iterator.aclose()
        assert not isolated_events._subscribers
    asyncio.run(check())


def test_voice_config(client):
    response = client.get("/api/voice/config")
    assert response.status_code == 200
    config = response.json()
    assert set(config) == {"forms", "corrections", "hotwords", "retract_words", "learned"}
    assert set(config["forms"]) >= {"drum", "travel_drive", "contract"}
    base = parser.load_voice_config()
    assert {k: config[k] for k in ("forms", "hotwords", "retract_words")} == {k: base[k] for k in ("forms", "hotwords", "retract_words")}
    assert config["corrections"] == {**base["corrections"], **config["learned"]}
