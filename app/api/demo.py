"""一键演示的启动、停止、重置和进度接口。"""

import shutil
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import demo
from app.config import get_settings
from app.db import get_session
from app.errors import AppError, NotFound
from app.fields import load_fields
from app.models import DemoClip
from app.services import voicelog
from app.timeutil import now_bj

router = APIRouter(prefix="/api/demo")


class StartRequest(BaseModel):
    speed: float = Field(1.0, ge=0.25, le=4, allow_inf_nan=False)


class ClipRequest(BaseModel):
    voice_id: int
    step: int


@router.get("/script")
def script(session: Session = Depends(get_session)):
    current = demo.load_script()
    clips = {clip.step: demo.clip_dict(clip) for clip in session.scalars(select(DemoClip))}
    return {"name": current["name"], "steps": [
        {**{key: step[key] for key in ("say", "form", "focus", "submit", "export") if key in step},
         "index": index, "caption": step.get("caption", ""), "clip": clips.get(index)}
        for index, step in enumerate(current["steps"])]}


@router.post("/clips")
def bind_clip(body: ClipRequest, request: Request, session: Session = Depends(get_session)):
    with request.app.state.demo.edit_clips():
        steps = demo.load_script()["steps"]
        if not 0 <= body.step < len(steps) or "say" not in steps[body.step]:
            raise AppError(400, "BAD_DEMO_STEP", "请选择当前脚本中的台词步骤")
        voice = voicelog.get(session, body.voice_id)
        settings = get_settings()
        root = settings.audio_dir.resolve()
        try:
            source = (root / voice.audio_path).resolve() if voice.audio_path else None
            if source is None or not source.is_relative_to(root) or not source.is_file():
                raise AppError(400, "NO_AUDIO", "这条语音没有录音")
        except (OSError, ValueError):
            raise AppError(400, "NO_AUDIO", "这条语音没有录音") from None
        if voice.form != steps[body.step]["form"]:
            kinds = {meta["kind"]: meta["label"] for meta in load_fields().values()}
            kinds["contract"] = "合同"
            raise AppError(400, "BAD_DEMO_FORM",
                           f"这段录音是{kinds[voice.form]}，和第 {body.step + 1} 步不一致")
        target_root = settings.demo_audio_dir.resolve()
        target_root.mkdir(parents=True, exist_ok=True)
        target = target_root / (uuid4().hex + source.suffix)
        clip = session.scalar(select(DemoClip).where(DemoClip.step == body.step))
        old_path = clip.audio_path if clip else None
        try:
            shutil.copyfile(source, target)
            if clip is None:
                clip = DemoClip(step=body.step)
                session.add(clip)
            clip.form, clip.text = voice.form, voice.text
            clip.audio_path, clip.duration_s = target.name, voice.duration_s
            clip.source_voice_id, clip.created_at = voice.id, now_bj()
            session.commit()
        except Exception:
            session.rollback()
            target.unlink(missing_ok=True)
            raise
        if old_path:
            demo.delete_clip_audio(old_path)
        demo.bus.publish("demo", {"state": "clips"}, "demo")
        return demo.clip_dict(clip)


@router.delete("/clips/{step}", status_code=204)
def delete_clip(step: int, request: Request, session: Session = Depends(get_session)):
    with request.app.state.demo.edit_clips():
        clip = session.scalar(select(DemoClip).where(DemoClip.step == step))
        if clip is None:
            raise NotFound("演示录音片段不存在")
        audio_path = clip.audio_path
        session.delete(clip)
        session.commit()
        demo.delete_clip_audio(audio_path)
        demo.bus.publish("demo", {"state": "clips"}, "demo")
        return Response(status_code=204)


@router.get("/clips/{step}/audio")
def clip_audio(step: int, session: Session = Depends(get_session)):
    clip = session.scalar(select(DemoClip).where(DemoClip.step == step))
    if clip is None:
        raise NotFound("演示录音片段不存在")
    target = demo.clip_audio_path(clip.audio_path)
    media = {".webm": "audio/webm", ".ogg": "audio/ogg", ".m4a": "audio/mp4",
             ".mp4": "audio/mp4", ".wav": "audio/wav", ".mp3": "audio/mpeg"}
    return FileResponse(target, media_type=media.get(target.suffix.lower(), "application/octet-stream"))


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
