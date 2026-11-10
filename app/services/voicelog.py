"""语音证据、复核纠错及解析提交的公共服务。"""

import json
import logging
import unicodedata
from pathlib import Path
from uuid import uuid4

from sqlalchemy import or_, select, update

from app.config import get_settings
from app.errors import Conflict, Invalid, NotFound
from app.events import bus
from app.htcode import normalize
from app.models import LearnedCorrection, VoiceLog
from app.services import records
from app.timeutil import now_bj, today_bj
from app.voice import parser


def _fold(text):
    return unicodedata.normalize("NFKC", text).casefold()


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def refresh_corrections(session):
    mapping = {row.wrong: row.right for row in session.scalars(select(LearnedCorrection))}
    parser.set_learned_corrections(mapping)
    return mapping


def correction_dict(row):
    return {"id": row.id, "wrong": row.wrong, "right": row.right, "source": row.source,
            "voice_log_id": row.voice_log_id, "created_at": _at(row.created_at), "hits": row.hits}


def _at(value):
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


def learned_list(session):
    return [correction_dict(row) for row in session.scalars(
        select(LearnedCorrection).order_by(LearnedCorrection.id.desc()))]


def as_dict(row, detail=False):
    result = json.loads(row.result)
    item = {key: getattr(row, key) for key in (
        "id", "ht_code", "form", "source", "duration_s", "elapsed_ms", "text", "corrected",
        "status", "record_kind", "record_id")}
    item.update(created_at=_at(row.created_at), has_audio=bool(row.audio_path),
                fields=result.get("fields", {}), rejected=result.get("rejected", []),
                message=result.get("message"),
                resolution=json.loads(row.resolution) if row.resolution else None)
    if detail:
        item["result"] = result
    return item


def get(session, voice_id):
    row = session.get(VoiceLog, voice_id)
    if row is None:
        raise NotFound("语音记录不存在")
    return row


def save_audio(content: bytes, filename: str | None):
    target = None
    try:
        suffix = Path(filename or "").suffix.lower()
        if suffix not in {".webm", ".ogg", ".m4a", ".mp4", ".wav", ".mp3"}:
            suffix = ".bin"
        relative = Path(now_bj().strftime("%Y-%m")) / (uuid4().hex + suffix)
        target = get_settings().audio_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return relative.as_posix()
    except Exception:
        logging.exception("录音保存失败")
        if target is not None:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                logging.exception("清理未保存完整的录音失败")
        return None


def parse(session, text, form, ht_code=None, source=None, *, asr=None,
          audio=None, filename=None, today=None, event_bus=None):
    """文本和音频识别结果共用此入口，先保存证据，再发布事件。"""
    if form not in {"contract", "drum", "travel_drive"}:
        raise Invalid("BAD_FORM", "表单类型不正确")
    publisher = event_bus if event_bus is not None else bus
    result = {**parser.parse_text(text, form, today or today_bj()), "asr": asr}
    review = not result["fields"] or (form != "contract" and bool(result["rejected"]))
    row = VoiceLog(ht_code=normalize(ht_code) if ht_code else None, form=form,
                   source=source if source in {"phone", "screen", "demo"} else None,
                   audio_path=save_audio(audio, filename) if audio is not None else None,
                   duration_s=asr.get("duration_s") if asr else None,
                   elapsed_ms=asr.get("elapsed_ms") if asr else None,
                   text=text, corrected=result["corrected"], result=_json(result),
                   status="review" if review else "ok")
    session.add(row)
    # 命中数按每次解析的原话统计一次，不重复计算同一句里的多次出现。
    for correction in session.scalars(select(LearnedCorrection)):
        if _fold(correction.wrong) in _fold(text):
            session.execute(update(LearnedCorrection).where(LearnedCorrection.id == correction.id)
                            .values(hits=LearnedCorrection.hits + 1))
    session.commit()
    response = {**result, "voice_id": row.id}
    publisher.publish("voice", {**response, "form": form, "ht_code": row.ht_code}, source)
    if review:
        publisher.publish("review", {"action": "new", "voice": as_dict(row)}, source)
    return response


def parse_voice_ids(raw):
    if not raw:
        return []
    values = raw.split(",") if isinstance(raw, str) else raw
    result = []
    for value in values[:50]:
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if 0 < number <= 2**63 - 1 and number not in result:
            result.append(number)
    return result


def submit_record(session, kind, ht_code, payload, voice_ids=None, source=None, event_bus=None):
    result = records.submit(session, kind, ht_code, payload)
    code = result["record"]["ht_code"]
    ids = parse_voice_ids(voice_ids)
    found = {row.id: row for row in session.scalars(select(VoiceLog).where(
        VoiceLog.id.in_(ids), or_(VoiceLog.ht_code == code, VoiceLog.ht_code.is_(None))))}
    linked = []
    for voice_id in ids:
        if voice_id in found:
            row = found[voice_id]
            row.record_kind, row.record_id = kind, result["record"]["id"]
            linked.append(voice_id)
    session.commit()
    result["voice_ids"] = linked
    publisher = event_bus if event_bus is not None else bus
    publisher.publish("record", {**result, "kind": kind, "ht_code": code}, source)
    return result


def validate_correction(wrong, right):
    wrong, right = wrong.strip(), right.strip()
    if not (1 <= len(wrong) <= 16 and 1 <= len(right) <= 16):
        raise Invalid("BAD_CORRECTION", "纠错词必须为 1～16 个字")
    if _fold(wrong) == _fold(right):
        raise Invalid("BAD_CORRECTION", "纠错前后不能相同")
    return wrong, right


def _upsert(session, wrong, right, source, voice_id=None):
    row = next((row for row in session.scalars(select(LearnedCorrection))
                if _fold(row.wrong) == _fold(wrong)), None)
    if row is None:
        row = LearnedCorrection(wrong=wrong, right=right, source=source, voice_log_id=voice_id)
        session.add(row)
    else:
        row.right, row.source, row.voice_log_id = right, source, voice_id
    session.flush()
    return row


def publish_corrections(session, source=None):
    refresh_corrections(session)
    bus.publish("corrections", {"learned": learned_list(session)}, source)


def add_correction(session, wrong, right):
    wrong, right = validate_correction(wrong, right)
    row = _upsert(session, wrong, right, "手动")
    session.commit()
    publish_corrections(session)
    return correction_dict(row)


def delete_correction(session, correction_id):
    row = session.get(LearnedCorrection, correction_id)
    if row is None:
        raise NotFound("纠错条目不存在")
    session.delete(row)
    session.commit()
    publish_corrections(session)


def resolve(session, voice_id, action, wrong=None, right=None, note=None):
    row = get(session, voice_id)
    if row.status != "review":
        raise Conflict("ALREADY_RESOLVED", "这条已经处理过了")
    correction, reparsed = None, None
    if action == "learn":
        wrong, right = validate_correction(wrong or "", right or "")
        if _fold(wrong) not in _fold(row.text):
            raise Invalid("BAD_CORRECTION", f"原话里没有「{wrong}」")
    elif action != "ignore":
        raise Invalid("BAD_CORRECTION", "复核操作不正确")
    # 条件更新保证同一条复核只有一个请求能处理成功。
    changed = session.execute(update(VoiceLog).where(
        VoiceLog.id == voice_id, VoiceLog.status == "review").values(
            status="resolved" if action == "learn" else "ignored", resolved_at=now_bj()))
    if not changed.rowcount:
        session.rollback()
        raise Conflict("ALREADY_RESOLVED", "这条已经处理过了")
    try:
        if action == "learn":
            correction_row = _upsert(session, wrong, right, "复核", row.id)
            mapping = {item.wrong: item.right for item in session.scalars(select(LearnedCorrection))}
            # 在事务成功前临时使用新映射；解析失败会恢复已提交的纠错表。
            parser.set_learned_corrections(mapping)
            reparsed = parser.parse_text(row.text, row.form, today_bj())
            row.corrected, row.result = reparsed["corrected"], _json(reparsed)
            row.resolution = _json({"wrong": wrong, "right": right, "reparsed": reparsed})
            session.flush()
            correction = correction_dict(correction_row)
        else:
            row.resolution = _json({"note": note})
        session.commit()
    except Exception:
        session.rollback()
        refresh_corrections(session)
        raise
    if action == "learn":
        publish_corrections(session, row.source)
    voice = as_dict(row)
    bus.publish("review", {"action": row.status, "voice": voice, "correction": correction}, row.source)
    return {"voice": voice, "reparsed": reparsed, "correction": correction}
