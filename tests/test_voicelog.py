"""证据留存、复核学习及关联时间线的业务验收。"""

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select

from app import events
from app.api import voice
from app.models import LearnedCorrection, VoiceLog
from app.services import voicelog
from app.voice import parser


@pytest.fixture(autouse=True)
def evidence_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("QC_AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setenv("QC_BACKUP_DIR", str(tmp_path / "backups"))
    current = events.EventBus()
    monkeypatch.setattr(voice, "bus", current)
    monkeypatch.setattr(voicelog, "bus", current)
    monkeypatch.setattr(events, "bus", current)
    yield current
    parser.set_learned_corrections({})


def say(client, text="直径一千二百五十", form="drum", **kwargs):
    response = client.post("/api/voice/parse-text", json={"text": text, "form": form, **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("text,form,status", [
    ("直径一千二百五十", "drum", "ok"), ("厚度等一下再测", "drum", "review"),
    ("直径一千二，厚度等一下", "drum", "review"),
    ("客户某厂，合同号乱说", "contract", "ok"), ("乱说", "contract", "review")])
def test_parse_text_evidence(client, text, form, status):
    parsed = say(client, text, form)
    row = client.get(f"/api/voice/logs/{parsed['voice_id']}").json()
    assert row["text"] == text
    assert row["status"] == status
    assert row["has_audio"] is False
    assert row["duration_s"] is row["elapsed_ms"] is None
    assert row["result"] == {key: value for key, value in parsed.items() if key != "voice_id"}
    assert client.get("/api/voice/logs").json()["items"][0]["id"] == row["id"]
    response = client.get(f"/api/voice/logs/{row['id']}/audio")
    assert response.status_code == 404 and response.json()["error"]["code"] == "NO_AUDIO"
    assert client.get("/api/voice/logs/999999").status_code == 404


@pytest.mark.parametrize("suffix,media", [("webm", "audio/webm"), ("ogg", "audio/ogg"),
    ("m4a", "audio/mp4"), ("mp4", "audio/mp4"), ("wav", "audio/wav"),
    ("mp3", "audio/mpeg"), ("exe", "application/octet-stream")])
def test_audio_roundtrip(client, monkeypatch, tmp_path, suffix, media):
    original = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "text": "厚度二十", "duration_s": 1.25, "elapsed_ms": 12}))
    monkeypatch.setattr(voice.httpx, "Client", lambda **kw: original(transport=transport, **kw))
    content = b"\x00\x01\xffaudio"
    response = client.post("/api/voice/parse", files={"audio": (f"录音.{suffix}", content)},
                           data={"form": "drum", "source": "phone"})
    assert response.status_code == 200
    voice_id = response.json()["voice_id"]
    row = client.get(f"/api/voice/logs/{voice_id}").json()
    assert row["duration_s"] == 1.25 and row["elapsed_ms"] == 12
    files = list((tmp_path / "audio").rglob("*.*"))
    assert len(files) == 1 and files[0].read_bytes() == content
    assert files[0].suffix == (".bin" if suffix == "exe" else f".{suffix}")
    download = client.get(f"/api/voice/logs/{voice_id}/audio")
    assert download.content == content and download.headers["content-type"] == media
    files[0].unlink()
    assert client.get(f"/api/voice/logs/{voice_id}/audio").json()["error"]["code"] == "NO_AUDIO"


def test_audio_save_failure_and_path_escape(client, session, monkeypatch, tmp_path):
    # 音频目录被普通文件占用，保存失败仍应保留解析结果与证据行。
    blocked = tmp_path / "blocked"
    blocked.write_text("占位", encoding="utf-8")
    monkeypatch.setenv("QC_AUDIO_DIR", str(blocked))
    result = voicelog.parse(session, "厚度二十", "drum", audio=b"audio", filename="a.wav")
    row = session.get(VoiceLog, result["voice_id"])
    assert row.audio_path is None and result["fields"] == {"thick": "20"}
    monkeypatch.setenv("QC_AUDIO_DIR", str(tmp_path / "audio"))
    for bad in ("../blocked", str(blocked)):
        row.audio_path = bad
        session.commit()
        response = client.get(f"/api/voice/logs/{row.id}/audio")
        assert response.status_code == 404 and response.json()["error"]["code"] == "NO_AUDIO"
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio" / "escape.wav").symlink_to(blocked)
    row.audio_path = "escape.wav"
    session.commit()
    assert client.get(f"/api/voice/logs/{row.id}/audio").status_code == 404


def test_record_links_and_timeline(client, session, contract, good_drum):
    first = say(client, ht_code=contract)["voice_id"]
    second = say(client)["voice_id"]
    foreign = say(client, ht_code="20261110-20")["voice_id"]
    now = datetime(2026, 11, 10, 8, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    session.get(VoiceLog, first).created_at = now
    session.commit()
    response = client.post(f"/api/contracts/{contract}/records/drum"
                           f"?voice_ids={first},{second},{foreign},x,-1,999999,{first}", json=good_drum)
    assert response.status_code == 201
    assert response.json()["voice_ids"] == [first, second]
    session.expire_all()
    assert session.get(VoiceLog, foreign).record_id is None
    timeline = client.get(f"/api/contracts/{contract}/timeline").json()["items"]
    record = next(item for item in timeline if item["type"] == "record")
    assert record["voice_ids"] == [first, second]
    assert record["record"] == response.json()["record"]
    assert [item["at"] for item in timeline] == sorted(item["at"] for item in timeline)
    assert [item["voice"]["id"] for item in timeline if item["type"] == "voice"] == [first, second]
    # 相同时间使用明确的类型优先级，语音必须排在记录前面。
    for row in session.scalars(select(VoiceLog).where(VoiceLog.id.in_([first, second]))):
        row.created_at = datetime.fromisoformat(record["at"])
    session.commit()
    assert [item["type"] for item in client.get(
        f"/api/contracts/{contract}/timeline").json()["items"]] == ["voice", "voice", "record"]
    assert client.get("/api/contracts/20261110-90/timeline").status_code == 404


def test_filters_and_id_limit(client, contract, good_drum):
    ids = [say(client, ht_code=contract)["voice_id"] for _ in range(52)]
    response = client.post(f"/api/contracts/{contract}/records/drum?voice_ids=" +
                           ",".join(map(str, ids)), json=good_drum)
    assert len(response.json()["voice_ids"]) == 50
    review = say(client, "厚度等一下", ht_code="20261110-20")["voice_id"]
    items = client.get(f"/api/voice/logs?ht_code={contract}&status=ok&limit=2&before_id={ids[-1]}").json()["items"]
    assert [row["id"] for row in items] == ids[-3:-1][::-1]
    assert client.get("/api/voice/logs?limit=201").status_code == 422
    assert client.get("/api/voice/logs?status=review").json()["items"][0]["id"] == review


def test_review_learn_runtime_hits_events(client, session, evidence_environment):
    parsed = say(client, "吃紧一千二", source="phone")
    voice_id = parsed["voice_id"]
    assert parsed["fields"] == {}
    assert any(item["type"] == "review" and item["data"]["action"] == "new"
               for item in evidence_environment.since(0))
    result = client.post(f"/api/review/{voice_id}/resolve", json={
        "action": "learn", "wrong": " 吃紧 ", "right": " 直径 "})
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["reparsed"]["fields"] == {"diameter": "1200"}
    assert data["voice"]["status"] == "resolved"
    assert data["voice"]["resolution"]["reparsed"] == data["reparsed"]
    assert data["correction"]["source"] == "复核"
    assert say(client, "吃紧一千二百五十")["fields"] == {"diameter": "1250"}
    config = client.get("/api/voice/config").json()
    assert config["corrections"]["吃紧"] == config["learned"]["吃紧"] == "直径"
    assert session.scalar(select(LearnedCorrection)).hits == 1
    repeated = client.post(f"/api/review/{voice_id}/resolve", json={"action": "ignore"})
    assert repeated.status_code == 409 and repeated.json()["error"]["code"] == "ALREADY_RESOLVED"
    assert any(item["type"] == "corrections" for item in evidence_environment.since(0))
    assert any(item["type"] == "review" and item["data"]["action"] == "resolved"
               for item in evidence_environment.since(0))


@pytest.mark.parametrize("wrong,right,message", [
    ("不存在", "直径", "原话里没有「不存在」"), ("", "直径", None),
    ("吃紧", "吃紧", None), ("吃紧", " ", None), ("字" * 17, "直径", None)])
def test_invalid_review_correction(client, wrong, right, message):
    voice_id = say(client, "吃紧一千二")["voice_id"]
    response = client.post(f"/api/review/{voice_id}/resolve", json={
        "action": "learn", "wrong": wrong, "right": right})
    assert response.status_code == 422 and response.json()["error"]["code"] == "BAD_CORRECTION"
    if message:
        assert response.json()["error"]["message"] == message
    assert client.get(f"/api/voice/logs/{voice_id}").json()["status"] == "review"


def test_ignore_and_manual_corrections(client, evidence_environment):
    voice_id = say(client, "厚度等一下")["voice_id"]
    ignored = client.post(f"/api/review/{voice_id}/resolve", json={"action": "ignore", "note": "等待复测"})
    assert ignored.json()["voice"]["resolution"] == {"note": "等待复测"}
    assert ignored.json()["voice"]["status"] == "ignored"
    assert client.get("/api/review").json()["items"] == []
    assert client.get("/api/review?status=ignored").json()["items"][0]["id"] == voice_id
    assert client.get("/api/review?status=all").json()["items"][0]["id"] == voice_id
    added = client.post("/api/corrections", json={"wrong": "吃紧", "right": "直径"}).json()
    assert added["source"] == "手动"
    assert say(client, "吃紧一千二")["fields"] == {"diameter": "1200"}
    updated = client.post("/api/corrections", json={"wrong": "吃紧", "right": "长度"}).json()
    assert updated["id"] == added["id"]
    assert say(client, "吃紧一千二")["fields"] == {"length": "1200"}
    assert client.delete(f"/api/corrections/{added['id']}").status_code == 204
    assert say(client, "吃紧一千二")["fields"] == {}
    assert client.get("/api/corrections").json()["learned"] == []
    assert evidence_environment.since(0)[-2]["type"] == "voice"


def test_builtin_override_nfkc_and_startup(client, session):
    original = dict(parser.load_voice_config()["corrections"])
    override = client.post("/api/corrections", json={"wrong": "后度", "right": "长度"}).json()
    assert say(client, "后度二十")["fields"] == {"length": "20"}
    client.delete(f"/api/corrections/{override['id']}")
    assert say(client, "后度二十")["fields"] == {"thick": "20"}
    voice_id = say(client, "ＡＢＣ一千二")["voice_id"]
    result = client.post(f"/api/review/{voice_id}/resolve", json={
        "action": "learn", "wrong": "abc", "right": "直径"})
    assert result.status_code == 200 and result.json()["reparsed"]["fields"] == {"diameter": "1200"}
    assert parser.load_voice_config()["corrections"] == original
    parser.set_learned_corrections({})
    from app.main import create_app
    from fastapi.testclient import TestClient
    with TestClient(create_app()) as reopened:
        assert say(reopened, "ABC一千二")["fields"] == {"diameter": "1200"}


def test_client_cannot_publish_demo_source(client):
    response = client.post("/api/events", json={"type": "focus", "source": "demo", "data": {}})
    assert response.status_code == 200 and response.json()["source"] is None
