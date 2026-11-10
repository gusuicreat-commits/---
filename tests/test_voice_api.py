from datetime import date

import httpx
import pytest

from app.api import voice
from app.voice import parser


@pytest.fixture(autouse=True)
def voice_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("QC_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("QC_ASR_URL", "http://识别服务.test:8601/")


def mock_asr(monkeypatch, handler):
    original = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(voice.httpx, "Client", lambda **kwargs: original(transport=transport, **kwargs))


def upload(client, form="drum"):
    return client.post("/api/voice/parse", files={"audio": ("录音.wav", "音频".encode(), "audio/wav")},
                       data={"form": form})


def test_parse_text(client):
    response = client.post("/api/voice/parse-text", json={"text": "直径一千二百五十，光段三百", "form": "drum"})
    assert response.status_code == 200
    assert response.json()["fields"] == {"diameter": "1250", "light": "300"}
    assert response.json()["asr"] is None


@pytest.mark.parametrize("endpoint", ["parse", "parse-text"])
def test_bad_form(client, endpoint):
    response = (upload(client, "不支持的表单") if endpoint == "parse" else
                client.post("/api/voice/parse-text", json={"text": "直径1", "form": "bad"}))
    assert response.status_code == 422
    assert response.json()["error"] == {"code": "BAD_FORM", "message": "表单类型不正确"}


def test_parse_forwards_audio_and_metadata(client, monkeypatch):
    monkeypatch.setenv("QC_ASR_TIMEOUT", "17")
    metadata = {"text": "厚度十二点五毫米", "duration_s": 2.5, "elapsed_ms": 20,
                "denoise": {"applied": False}, "truncated": False}

    def handler(request):
        assert request.url.path == "/transcribe"
        assert request.extensions["timeout"]["read"] == 17
        assert b'name="audio"' in request.content
        assert "音频".encode() in request.content
        assert " ".join(parser.hotwords()).encode() in request.content
        return httpx.Response(200, json=metadata)

    mock_asr(monkeypatch, handler)
    response = upload(client)
    assert response.status_code == 200
    assert response.json()["fields"] == {"thick": "12.5"}
    assert response.json()["asr"] == metadata


@pytest.mark.parametrize("exception", [httpx.ConnectError, httpx.ReadTimeout])
def test_voice_unavailable(client, monkeypatch, exception):
    def handler(request):
        raise exception("模拟连接失败", request=request)

    mock_asr(monkeypatch, handler)
    response = upload(client)
    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "VOICE_UNAVAILABLE", "message": "语音服务不可用，请手动填写"}


@pytest.mark.parametrize("status,code", [(422, "BAD_AUDIO"), (503, "ASR_NOT_READY"), (500, "ASR_FAILED")])
def test_upstream_errors_pass_through(client, monkeypatch, status, code):
    payload = {"error": {"code": code, "message": "识别错误", "detail": "保留额外信息"}}
    mock_asr(monkeypatch, lambda request: httpx.Response(status, json=payload))
    response = upload(client)
    assert response.status_code == status
    assert response.json() == payload


@pytest.mark.parametrize("status,body", [(500, {}), (200, {}), (200, []), (200, {"text": None})])
def test_invalid_upstream_response(client, monkeypatch, status, body):
    mock_asr(monkeypatch, lambda request: httpx.Response(status, json=body))
    assert upload(client).status_code == 503


def test_status_unavailable(client, monkeypatch):
    def handler(request):
        assert request.extensions["timeout"]["read"] == 2
        raise httpx.ConnectError("模拟连接失败", request=request)

    mock_asr(monkeypatch, handler)
    assert client.get("/api/voice/status").json() == {
        "available": False, "loaded": False, "denoise_enabled": None, "frontend_available": None}


@pytest.mark.parametrize("loaded", [True, False])
def test_status_available(client, monkeypatch, loaded):
    mock_asr(monkeypatch, lambda request: httpx.Response(200, json={
        "loaded": loaded, "ok": loaded, "denoise_enabled": True, "frontend_available": False}))
    assert client.get("/api/voice/status").json() == {
        "available": True, "loaded": loaded, "denoise_enabled": True, "frontend_available": False}


@pytest.mark.parametrize("form,text", [
    ("drum", "长度一点几米吧"), ("travel_drive", "噪声八十多分贝"), ("drum", "光段三百来毫米")])
def test_ambiguous_values_rejected(client, form, text):
    response = client.post("/api/voice/parse-text", json={"text": text, "form": form})
    assert response.status_code == 200
    assert response.json()["fields"] == {}
    assert response.json()["rejected"][0]["reason"] == "数值说法无法确定"


def test_exact_value_still_accepted():
    assert parser.parse_text("光段三百", "drum", date(2026, 11, 10))["fields"] == {"light": "300"}


def test_parse_text_uses_beijing_date(client, monkeypatch):
    monkeypatch.setattr(voice, "today_bj", lambda: date(2027, 1, 1))
    response = client.post("/api/voice/parse-text", json={"text": "日期昨天，合同号二零二七四八二七", "form": "contract"})
    assert response.json()["fields"] == {"date": "2026-12-31", "ht_code": "20274827"}
