import httpx
from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import get_settings
from app.errors import AppError, Invalid
from app.timeutil import today_bj
from app.voice import parser

router = APIRouter(prefix="/api/voice")


def _check_form(form: str) -> None:
    if form not in {"contract", "drum", "travel_drive"}:
        raise Invalid("BAD_FORM", "表单类型不正确")


def _unavailable() -> AppError:
    return AppError(503, "VOICE_UNAVAILABLE", "语音服务不可用，请手动填写")


@router.get("/status")
def status():
    result = {"available": False, "loaded": False,
              "denoise_enabled": None, "frontend_available": None}
    try:
        with httpx.Client(timeout=2) as client:
            response = client.get(get_settings().asr_url.rstrip("/") + "/health")
        response.raise_for_status()
        body = response.json()
        result.update(available=True, loaded=bool(body.get("loaded", False)),
                      denoise_enabled=body.get("denoise_enabled"),
                      frontend_available=body.get("frontend_available"))
    except (httpx.HTTPError, ValueError, AttributeError):
        pass
    return result


class TextRequest(BaseModel):
    text: str
    form: str


@router.post("/parse-text")
def parse_text(body: TextRequest):
    _check_form(body.form)
    return {**parser.parse_text(body.text, body.form, today_bj()), "asr": None}


@router.post("/parse")
def parse(audio: UploadFile = File(...), form: str = Form(...)):
    _check_form(form)
    settings = get_settings()
    try:
        with httpx.Client(timeout=settings.asr_timeout) as client:
            response = client.post(settings.asr_url.rstrip("/") + "/transcribe",
                                   files={"audio": (audio.filename or "audio", audio.file.read(),
                                                    audio.content_type or "application/octet-stream")},
                                   data={"hotwords": " ".join(parser.hotwords())})
    except httpx.RequestError:
        raise _unavailable() from None
    try:
        body = response.json()
    except ValueError:
        raise _unavailable() from None
    if response.is_error:
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            return JSONResponse(status_code=response.status_code, content={"error": body["error"]})
        raise _unavailable()
    if not isinstance(body, dict) or not isinstance(body.get("text"), str):
        raise _unavailable()
    return {**parser.parse_text(body["text"], form, today_bj()),
            "asr": {key: body.get(key) for key in
                    ("text", "duration_s", "elapsed_ms", "denoise", "truncated")}}
