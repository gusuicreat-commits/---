"""使用真实业务服务执行可停止的演示剧本。"""

import json
import logging
import os
import threading
from datetime import date
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, or_, select

from app.config import ROOT, get_settings
from app.errors import AppError, Conflict, Invalid
from app.events import bus
from app.fields import table_by_kind
from app.models import Contract, MODEL_BY_TABLE, VoiceLog
from app.services import contracts, export, records, voicelog
from app.services.status import latest_records
from app.timeutil import today_bj


def load_script():
    path = Path(os.environ.get("QC_DEMO_PATH", "config/demo.json")).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _demo_contract_query():
    logs = select(VoiceLog.id).where(VoiceLog.ht_code == Contract.ht_code)
    other_logs = logs.where(or_(VoiceLog.source != "demo", VoiceLog.source.is_(None)))
    return select(Contract.ht_code).where(
        Contract.name == load_script()["name"], logs.exists(), ~other_logs.exists())


def find_demo_contracts(session, day: date | None = None) -> list[str]:
    """只识别有演示语音证据、且没有其他来源语音的同名合同。"""
    query = _demo_contract_query()
    if day is not None:
        query = query.where(Contract.date == day)
    return list(session.scalars(query.order_by(Contract.ht_code)))


def purge_demo_contract(session, ht_code) -> dict:
    """在同一事务内删除演示数据，提交成功后清理录音，保留导出留档。"""
    try:
        if session.scalar(_demo_contract_query().where(Contract.ht_code == ht_code)) is None:
            raise Invalid("NOT_DEMO", "不是演示合同，不能删除")
        audio_paths = session.scalars(select(VoiceLog.audio_path).where(
            VoiceLog.ht_code == ht_code, VoiceLog.audio_path.is_not(None))).all()
        record_count = sum(session.execute(delete(model).where(
            model.ht_code == ht_code)).rowcount for model in MODEL_BY_TABLE.values())
        voice_count = session.execute(delete(VoiceLog).where(VoiceLog.ht_code == ht_code)).rowcount
        session.execute(delete(Contract).where(Contract.ht_code == ht_code))
        session.commit()
    except Exception:
        session.rollback()
        raise
    for audio_path in audio_paths:
        try:
            root = get_settings().audio_dir.resolve()
            target = (root / audio_path).resolve()
            if not target.is_relative_to(root):
                raise ValueError("录音路径超出录音目录")
            target.unlink(missing_ok=True)
        except (OSError, ValueError):
            logging.exception("删除演示录音失败：%s", audio_path)
    return {"ht_code": ht_code, "records": record_count, "voice_logs": voice_count}


def run_script(session_factory, steps, ht_code, run_id=None, *, speed=1.0,
               stop_event=None, on_step=None, event_bus=None):
    """可同步调用；调用者负责先创建演示合同，执行器独立管理数据库会话。"""
    publisher = event_bus if event_bus is not None else bus
    run_id = run_id or uuid4().hex
    stop_event = stop_event if stop_event is not None else threading.Event()
    drafts, voice_ids = {}, {}
    common = {"run_id": run_id, "ht_code": ht_code}
    try:
        with session_factory() as session:
            for index, step in enumerate(steps):
                if stop_event.is_set():
                    publisher.publish("demo", {"state": "stopped", **common}, "demo")
                    return
                if on_step:
                    on_step(index)
                publisher.publish("demo", {"state": "step", **common, "index": index,
                                           "total": len(steps), "caption": step.get("caption", ""),
                                           "step": step}, "demo")
                if "say" in step:
                    form = step["form"]
                    result = voicelog.parse(session, step["say"], form, ht_code, "demo",
                                            event_bus=publisher)
                    drafts.setdefault(form, {}).update(result["fields"])
                    voice_ids.setdefault(form, []).append(result["voice_id"])
                    publisher.publish("draft", {"ht_code": ht_code, "form": form,
                                                "fields": dict(drafts[form])}, "demo")
                elif "submit" in step:
                    form = step["submit"]
                    latest = latest_records(session, ht_code)[form]
                    previous = records.as_dict(table_by_kind(form), latest) if latest else {}
                    payload = {**previous, **drafts.get(form, {})}
                    result = voicelog.submit_record(session, form, ht_code, payload,
                                                     voice_ids.get(form, []), "demo", publisher)
                    if result["record"]["is_qualified"] == 1:
                        drafts.pop(form, None)
                        voice_ids.pop(form, None)
                elif "focus" in step:
                    publisher.publish("focus", {"ht_code": ht_code, "form": step["focus"]}, "demo")
                elif step.get("export"):
                    try:
                        result = export.export_one(session, ht_code)
                    except AppError as exc:
                        if exc.code != "NOT_QUALIFIED":
                            raise
                        publisher.publish("demo", {"state": "export_failed", **common,
                                                   "message": exc.message}, "demo")
                    else:
                        publisher.publish("export", {"action": "one", **{key: result[key] for key in
                                                            ("ht_code", "file", "sheets")}}, "demo")
                else:
                    raise ValueError("演示步骤类型不正确")
                if stop_event.wait(max(0, step.get("wait_ms", 0)) / 1000 / speed):
                    publisher.publish("demo", {"state": "stopped", **common}, "demo")
                    return
        publisher.publish("demo", {"state": "done", **common}, "demo")
    except Exception as exc:
        logging.exception("演示执行失败")
        publisher.publish("demo", {"state": "error", **common, "message": str(exc)}, "demo")


class DemoRunner:
    def __init__(self, session_factory):
        self.session_factory = session_factory
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._status = {"running": False, "run_id": None, "ht_code": None, "index": -1,
                        "total": 0, "recycled": None}

    def status(self):
        with self._lock:
            return dict(self._status)

    def start(self, speed=1.0):
        with self._lock:
            if self._status["running"]:
                raise Conflict("DEMO_RUNNING", "演示正在进行")
            script = load_script()
            recycled = None
            with self.session_factory() as session:
                code = contracts.suggestion(session)
                if code is None:
                    candidates = find_demo_contracts(session, today_bj())
                    if not candidates:
                        raise Conflict("NO_CONTRACT_CODE", "今天的合同号已用完")
                    code = recycled = candidates[0]
                    purge_demo_contract(session, code)
                    bus.publish("contract", {"action": "deleted", "contract": {"ht_code": code}}, "demo")
                contract = contracts.create(session, code, script["name"])
            bus.publish("contract", {"action": "created", "contract": contract}, "demo")
            run_id = uuid4().hex
            self._stop = threading.Event()
            self._status = {"running": True, "run_id": run_id, "ht_code": code,
                            "index": -1, "total": len(script["steps"]), "recycled": recycled}
            self._thread = threading.Thread(target=self._run,
                                            args=(script["steps"], code, run_id, speed, self._stop),
                                            daemon=True)
            self._thread.start()
            return {key: self._status[key] for key in ("run_id", "ht_code", "total", "recycled")}

    def reset(self):
        with self._lock:
            if self._status["running"]:
                raise Conflict("DEMO_RUNNING", "演示正在进行")
            deleted = []
            with self.session_factory() as session:
                for code in find_demo_contracts(session):
                    purge_demo_contract(session, code)
                    deleted.append(code)
                    bus.publish("contract", {"action": "deleted", "contract": {"ht_code": code}}, "demo")
            return {"deleted": deleted}

    def _run(self, steps, code, run_id, speed, stop):
        def progress(index):
            with self._lock:
                self._status["index"] = index
        try:
            run_script(self.session_factory, steps, code, run_id, speed=speed,
                       stop_event=stop, on_step=progress)
        finally:
            with self._lock:
                self._status["running"] = False

    def stop(self):
        with self._lock:
            self._stop.set()
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join()
        return {"running": False}
