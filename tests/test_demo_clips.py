"""演示录音片段的绑定、播放、运行和长期保存验收。"""

import io
import threading
import time
import wave
from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

from app import demo, events
from app.config import ROOT, get_settings
from app.models import DemoClip, VoiceLog
from app.services import voicelog
from app.timeutil import today_bj


@pytest.fixture(autouse=True)
def clip_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("QC_AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setenv("QC_DEMO_AUDIO_DIR", str(tmp_path / "demo-audio"))
    monkeypatch.setenv("QC_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("QC_EXPORT_DIR", str(tmp_path / "exports"))
    publisher = events.EventBus()
    monkeypatch.setattr(demo, "bus", publisher)
    return publisher


@pytest.fixture
def recording(session):
    def create(*, form="drum", text="直径一千二百五十，长度三千", duration=0.02,
               suffix=".wav", audio=True, ht_code=None, source="phone"):
        content = io.BytesIO()
        with wave.open(content, "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(16000)
            stream.writeframes(b"\x00\x00" * 320)
        result = voicelog.parse(
            session, text, form, ht_code, source,
            audio=content.getvalue() if audio else None,
            filename="真实录音" + suffix, asr={"duration_s": duration})
        return session.get(VoiceLog, result["voice_id"])
    return create


def bind(client, voice, step=1):
    response = client.post("/api/demo/clips", json={"voice_id": voice.id, "step": step})
    assert response.status_code == 200, response.text
    return response.json()


def clip_row(session, step=1):
    session.expire_all()
    return session.scalar(select(DemoClip).where(DemoClip.step == step))


def test_demo_audio_settings_are_independent_and_cwd_free(monkeypatch, tmp_path):
    monkeypatch.delenv("QC_DEMO_AUDIO_DIR")
    monkeypatch.chdir(tmp_path)
    settings = get_settings()
    assert settings.demo_audio_dir == ROOT / "data" / "demo-audio"
    assert settings.audio_dir != settings.demo_audio_dir
    monkeypatch.setenv("QC_DEMO_AUDIO_DIR", "relative-demo")
    assert get_settings().demo_audio_dir == ROOT / "relative-demo"


def test_script_matches_current_steps_and_bound_clip(client, session, recording):
    current = demo.load_script()
    response = client.get("/api/demo/script")
    assert response.status_code == 200
    expected = {"name": current["name"], "steps": [
        {**{key: step[key] for key in ("say", "form", "focus", "submit", "export") if key in step},
         "index": index, "caption": step.get("caption", ""), "clip": None}
        for index, step in enumerate(current["steps"])]}
    assert response.json() == expected
    voice = recording()
    clip = bind(client, voice)
    assert set(clip) == {"text", "duration_s", "source_voice_id", "audio_url", "created_at"}
    assert clip == demo.clip_dict(clip_row(session))
    assert clip["text"] == voice.text
    assert clip["source_voice_id"] == voice.id
    assert clip["duration_s"] == voice.duration_s
    assert clip["created_at"].startswith(f"{today_bj():%Y-%m-%d}")
    expected["steps"][1]["clip"] = clip
    assert client.get("/api/demo/script").json() == expected


def test_binding_copies_original_and_snapshots_asr_text(client, session, recording, clip_environment):
    voice = recording()
    original = get_settings().audio_dir / voice.audio_path
    content = original.read_bytes()
    bind(client, voice)
    clip = clip_row(session)
    copy = demo.clip_audio_path(clip.audio_path)
    assert copy != original and not Path(clip.audio_path).is_absolute()
    assert copy.suffix == original.suffix
    assert len(copy.stem) == 32
    assert copy.read_bytes() == content
    voice.text, voice.corrected = "后来修改的原话", "后来纠正的文字"
    session.commit()
    original.unlink()
    session.delete(voice)
    session.commit()
    assert client.get("/api/demo/script").json()["steps"][1]["clip"]["text"] == "直径一千二百五十，长度三千"
    assert client.get("/api/demo/clips/1/audio").content == content
    assert clip_environment.since(0)[-1]["data"] == {"state": "clips"}


@pytest.mark.parametrize("step", [-1, 0, 5, 6, 8, 11, 14, 17, 18, 10000])
def test_bind_requires_current_say_step(client, recording, step):
    response = client.post("/api/demo/clips", json={"voice_id": recording().id, "step": step})
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "请选择当前脚本中的台词步骤"


def test_binding_checks_current_script(client, recording, monkeypatch):
    voice = recording()
    monkeypatch.setattr(demo, "load_script", lambda: {"name": "变更后的脚本", "steps": []})
    assert client.post("/api/demo/clips", json={"voice_id": voice.id, "step": 1}).status_code == 400


def test_bind_missing_voice(client):
    response = client.post("/api/demo/clips", json={"voice_id": 99999, "step": 1})
    assert response.status_code == 404
    assert response.json()["error"]["message"] == "语音记录不存在"


@pytest.mark.parametrize("mode", ["none", "missing", "outside", "symlink"])
def test_bind_requires_safe_existing_recording(client, session, recording, tmp_path, mode):
    voice = recording(audio=mode != "none")
    if mode == "missing":
        (get_settings().audio_dir / voice.audio_path).unlink()
    elif mode in {"outside", "symlink"}:
        outside = tmp_path / "外部.wav"
        outside.write_bytes(b"outside")
        if mode == "outside":
            voice.audio_path = "../外部.wav"
        else:
            link = get_settings().audio_dir / "外部链接.wav"
            link.symlink_to(outside)
            voice.audio_path = link.name
        session.commit()
    response = client.post("/api/demo/clips", json={"voice_id": voice.id, "step": 1})
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "这条语音没有录音"


@pytest.mark.parametrize("form,step,label", [
    ("drum", 7, "卷筒"), ("travel_drive", 1, "大车传动"), ("contract", 1, "合同")])
def test_bind_form_must_match(client, recording, form, step, label):
    voice = recording(form=form)
    response = client.post("/api/demo/clips", json={"voice_id": voice.id, "step": step})
    assert response.status_code == 400
    assert response.json()["error"]["message"] == f"这段录音是{label}，和第 {step + 1} 步不一致"


def test_replace_upserts_one_row_and_deletes_old_copy(client, session, recording, clip_environment):
    first, second = recording(), recording(text="直径一千二百五十二")
    bind(client, first)
    old = clip_row(session)
    old_id, old_path = old.id, demo.clip_audio_path(old.audio_path)
    clip = bind(client, second)
    row = clip_row(session)
    assert row.id == old_id
    assert row.text == second.text and clip["source_voice_id"] == second.id
    assert not old_path.exists()
    assert demo.clip_audio_path(row.audio_path).exists()
    assert len(session.scalars(select(DemoClip)).all()) == 1
    assert (get_settings().audio_dir / first.audio_path).exists()
    assert len(clip_environment.since(0)) == 2


def test_delete_removes_row_and_copy_only(client, session, recording, clip_environment):
    voice = recording()
    bind(client, voice)
    copy = demo.clip_audio_path(clip_row(session).audio_path)
    assert client.delete("/api/demo/clips/1").status_code == 204
    assert clip_row(session) is None and not copy.exists()
    assert (get_settings().audio_dir / voice.audio_path).exists()
    assert client.get("/api/demo/script").json()["steps"][1]["clip"] is None
    assert client.get("/api/demo/clips/1/audio").status_code == 404
    assert client.delete("/api/demo/clips/1").status_code == 404
    assert [item["data"] for item in clip_environment.since(0)] == [{"state": "clips"}] * 2


def test_delete_with_missing_file(client, session, recording):
    bind(client, recording())
    demo.clip_audio_path(clip_row(session).audio_path).unlink()
    assert client.delete("/api/demo/clips/1").status_code == 204


@pytest.mark.parametrize("method", ["post", "delete"])
def test_clip_edits_rejected_while_running(client, session, recording, monkeypatch, method):
    voice = recording()
    bind(client, voice)
    old_path = demo.clip_audio_path(clip_row(session).audio_path)
    monkeypatch.setattr(demo, "load_script", lambda: {"name": "演示客户（样例）", "steps": [
        {"focus": "drum", "wait_ms": 60000}]})
    assert client.post("/api/demo/start", json={}).status_code == 200
    try:
        response = (client.post("/api/demo/clips", json={"voice_id": voice.id, "step": 1})
                    if method == "post" else client.delete("/api/demo/clips/1"))
        assert response.status_code == 409
        assert response.json()["error"]["message"] == "演示进行中，停止后再改"
        assert old_path.exists() and clip_row(session) is not None
        assert client.get("/api/demo/clips/1/audio").status_code == 200
    finally:
        client.post("/api/demo/stop")
    assert client.delete("/api/demo/clips/1").status_code == 204


@pytest.mark.parametrize("suffix,media", [
    (".wav", "audio/wav"), (".webm", "audio/webm"), (".ogg", "audio/ogg"),
    (".m4a", "audio/mp4"), (".mp4", "audio/mp4"), (".mp3", "audio/mpeg"),
    (".bin", "application/octet-stream")])
def test_audio_media_type(client, recording, suffix, media):
    voice = recording(suffix=suffix)
    bind(client, voice)
    response = client.get("/api/demo/clips/1/audio")
    assert response.status_code == 200
    assert response.headers["content-type"] == media
    assert response.content == (get_settings().audio_dir / voice.audio_path).read_bytes()


@pytest.mark.parametrize("mode", ["missing", "relative", "absolute", "symlink"])
def test_audio_missing_and_path_escape(client, session, recording, tmp_path, mode):
    assert client.get("/api/demo/clips/999/audio").status_code == 404
    bind(client, recording())
    clip = clip_row(session)
    original = demo.clip_audio_path(clip.audio_path)
    outside = tmp_path / "禁止读取.wav"
    outside.write_bytes(b"outside")
    if mode == "missing":
        original.unlink()
    elif mode == "relative":
        clip.audio_path = "../禁止读取.wav"
    elif mode == "absolute":
        clip.audio_path = str(outside)
    else:
        original.unlink()
        original.symlink_to(outside)
    session.commit()
    assert client.get("/api/demo/clips/1/audio").status_code == 404
    assert client.delete("/api/demo/clips/1").status_code == 204
    assert outside.read_bytes() == b"outside"


@pytest.mark.parametrize("speed", [0.25, 4])
def test_runner_plays_before_parse_and_waits_original_duration(
        client, session, recording, clip_environment, speed):
    voice = recording()
    bind(client, voice)
    script = demo.load_script()
    steps = [{**step, "wait_ms": 0} for step in script["steps"][:2]]
    started = time.monotonic()
    demo.run_script(sessionmaker(bind=session.get_bind()), steps, None, "录音演示", speed=speed)
    elapsed = time.monotonic() - started
    assert elapsed >= voice.duration_s + 0.3
    logs = session.scalars(select(VoiceLog).where(VoiceLog.source == "demo")).all()
    assert len(logs) == 1
    result = logs[0]
    assert result.text == voice.text and result.text != steps[1]["say"]
    assert result.duration_s == voice.duration_s
    original = get_settings().audio_dir / voice.audio_path
    evidence = get_settings().audio_dir / result.audio_path
    assert evidence != original
    assert evidence.read_bytes() == original.read_bytes()
    assert client.get(f"/api/voice/logs/{result.id}/audio").content == original.read_bytes()
    items = clip_environment.since(0)
    audio = next(item for item in items if item["data"].get("state") == "audio")
    parsed = next(item for item in items if item["type"] == "voice")
    assert audio["id"] < parsed["id"]
    assert audio["data"] == {"state": "audio", "run_id": "录音演示", "ht_code": None,
                             "index": 1, "audio_url": "/api/demo/clips/1/audio", "duration_s": 0.02}
    assert parsed["data"]["demo_clip"] == 1
    assert parsed["data"]["fields"] == {"diameter": "1250", "length": "3000"}
    assert items[-1]["data"]["state"] == "done"


@pytest.mark.parametrize("duration,expected", [(None, 3.3), (0, 0.3), (0.02, 0.32)])
def test_clip_wait_defaults_and_following_wait_is_scaled(
        client, session, recording, clip_environment, duration, expected):
    bind(client, recording(duration=duration), step=1)

    class WaitRecorder:
        def __init__(self):
            self.waits = []

        def is_set(self):
            return False

        def wait(self, seconds):
            self.waits.append(seconds)
            return False

    stop = WaitRecorder()
    steps = [{"focus": "drum"}, {"say": "原台词", "form": "drum", "wait_ms": 800}]
    demo.run_script(sessionmaker(bind=session.get_bind()), steps, None, speed=4, stop_event=stop)
    assert stop.waits == pytest.approx([0, expected, 0.2])


def test_stop_interrupts_clip_wait_without_parsing(client, session, recording, clip_environment):
    bind(client, recording(duration=60))
    playing = threading.Event()

    class PlaybackBus:
        def publish(self, type, data, source=None):
            result = clip_environment.publish(type, data, source)
            if data.get("state") == "audio":
                playing.set()
            return result

    stop = threading.Event()
    thread = threading.Thread(target=demo.run_script, args=(
        sessionmaker(bind=session.get_bind()), demo.load_script()["steps"][:2], None),
        kwargs={"stop_event": stop, "speed": 4, "event_bus": PlaybackBus()})
    thread.start()
    try:
        assert playing.wait(2)
    finally:
        stop.set()
        thread.join(timeout=2)
    assert not thread.is_alive()
    assert session.scalars(select(VoiceLog).where(VoiceLog.source == "demo")).all() == []
    assert clip_environment.since(0)[-1]["data"]["state"] == "stopped"


@pytest.mark.parametrize("cleanup", ["purge", "reset"])
def test_recycle_removes_evidence_copy_and_preserves_playable_clip(
        configured, session, recording, monkeypatch, cleanup):
    code = f"{today_bj():%Y%m%d}-10"
    assert configured.post("/api/contracts", json={
        "ht_code": code, "name": demo.load_script()["name"]}).status_code == 201
    voice = recording(ht_code=code, source="demo", text="直径一千二百五十，长度三千，厚度二十，光段三百")
    bind(configured, voice)
    copy = demo.clip_audio_path(clip_row(session).audio_path)
    original = get_settings().audio_dir / voice.audio_path
    steps = [{**step, "wait_ms": 0} for step in demo.load_script()["steps"]]
    demo.run_script(sessionmaker(bind=session.get_bind()), steps, code, speed=4)
    session.expire_all()
    logs = session.scalars(select(VoiceLog).where(VoiceLog.ht_code == code)).all()
    evidence = [get_settings().audio_dir / row.audio_path for row in logs if row.audio_path]
    assert len(evidence) == 2 and original in evidence
    if cleanup == "purge":
        assert demo.purge_demo_contract(session, code)["voice_logs"] == 9
    else:
        assert configured.post("/api/demo/reset").json() == {"deleted": [code]}
    assert all(not path.exists() for path in evidence)
    assert copy.is_file()
    assert configured.get("/api/demo/clips/1/audio").content == copy.read_bytes()
    assert configured.get("/api/demo/script").json()["steps"][1]["clip"]["source_voice_id"] == voice.id
    assert clip_row(session) is not None


def test_purge_protects_demo_directory_even_for_bad_voice_path(
        client, session, recording, monkeypatch, tmp_path):
    nested = tmp_path / "audio" / "长期演示录音"
    monkeypatch.setenv("QC_DEMO_AUDIO_DIR", str(nested))
    code = f"{today_bj():%Y%m%d}-10"
    client.post("/api/contracts", json={"ht_code": code, "name": demo.load_script()["name"]})
    voice = recording(source="demo", ht_code=code)
    bind(client, voice)
    copy = demo.clip_audio_path(clip_row(session).audio_path)
    voice.audio_path = copy.relative_to(get_settings().audio_dir).as_posix()
    session.commit()
    demo.purge_demo_contract(session, code)
    assert copy.exists()
    assert client.get("/api/demo/clips/1/audio").content == copy.read_bytes()


def test_failed_upsert_preserves_old_row_and_copy(client, session, recording):
    first, second = recording(), recording(text="长度三千")
    bind(client, first)
    old_path = demo.clip_audio_path(clip_row(session).audio_path)
    engine = session.get_bind()

    def fail_update(connection, cursor, statement, parameters, context, many):
        if statement.startswith("UPDATE demo_clip "):
            raise RuntimeError("模拟片段更新失败")

    event.listen(engine, "before_cursor_execute", fail_update)
    try:
        with pytest.raises(RuntimeError, match="模拟片段更新失败"):
            client.post("/api/demo/clips", json={"voice_id": second.id, "step": 1})
    finally:
        event.remove(engine, "before_cursor_execute", fail_update)
    assert clip_row(session).source_voice_id == first.id
    assert list(get_settings().demo_audio_dir.iterdir()) == [old_path]


def test_unbound_say_remains_text_only(client, session, clip_environment):
    demo.run_script(sessionmaker(bind=session.get_bind()),
                    [{"say": "长度三千", "form": "drum"}], None)
    row = session.scalar(select(VoiceLog))
    assert row.text == "长度三千" and row.audio_path is None
    items = clip_environment.since(0)
    assert not any(item["data"].get("state") == "audio" for item in items)
    assert "demo_clip" not in next(item["data"] for item in items if item["type"] == "voice")
