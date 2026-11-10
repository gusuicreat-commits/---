from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.dec import Dec2
from app.timeutil import now_bj


class Base(DeclarativeBase):
    pass


class Contract(Base):
    __tablename__ = "contract"
    id: Mapped[int] = mapped_column(primary_key=True)
    ht_code: Mapped[str] = mapped_column(String(11), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)


class ParameterMixin:
    id: Mapped[int] = mapped_column(primary_key=True)
    ht_code: Mapped[str] = mapped_column(String(11), index=True)
    is_qualified: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: now_bj().replace(tzinfo=None))


class DrumParam(ParameterMixin, Base):
    __tablename__ = "drum_param"
    diameter: Mapped[Decimal | None] = mapped_column(Dec2())
    length: Mapped[Decimal | None] = mapped_column(Dec2())
    thick: Mapped[Decimal | None] = mapped_column(Dec2())
    light: Mapped[Decimal | None] = mapped_column(Dec2())


class TravelDriveParam(ParameterMixin, Base):
    __tablename__ = "travel_drive_param"
    reducer_nameplate: Mapped[str | None] = mapped_column(String(255))
    motor_nameplate: Mapped[str | None] = mapped_column(String(255))
    noise: Mapped[Decimal | None] = mapped_column(Dec2())


class Rule(Base):
    __tablename__ = "rule"
    __table_args__ = (UniqueConstraint("table_name", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    table_name: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(64))
    min: Mapped[Decimal | None] = mapped_column(Dec2())
    max: Mapped[Decimal | None] = mapped_column(Dec2())


class ControlCabinetParam(ParameterMixin, Base):
    __tablename__ = "control_cabinet_param"
    insulation_resistance: Mapped[Decimal | None] = mapped_column(Dec2())
    control_wire_torque: Mapped[Decimal | None] = mapped_column(Dec2())
    electrical_clearance: Mapped[Decimal | None] = mapped_column(Dec2())


class TravelPaintParam(ParameterMixin, Base):
    __tablename__ = "travel_paint_param"
    primer_thickness_ratio: Mapped[str | None] = mapped_column(String(255))
    total_thickness_ratio: Mapped[Decimal | None] = mapped_column(Dec2())
    adhesion_loss_ratio: Mapped[Decimal | None] = mapped_column(Dec2())


MODEL_BY_TABLE = {
    "drum_param": DrumParam, "travel_drive_param": TravelDriveParam,
    "control_cabinet_param": ControlCabinetParam, "travel_paint_param": TravelPaintParam,
}


class VoiceLog(Base):
    __tablename__ = "voice_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bj)
    ht_code: Mapped[str | None] = mapped_column(String(11), index=True)
    form: Mapped[str] = mapped_column(String(32))
    source: Mapped[str | None] = mapped_column(String(16))
    audio_path: Mapped[str | None] = mapped_column(String(255))
    duration_s: Mapped[float | None] = mapped_column(Float)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    corrected: Mapped[str] = mapped_column(Text)
    result: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    record_kind: Mapped[str | None] = mapped_column(String(32))
    record_id: Mapped[int | None] = mapped_column(Integer)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolution: Mapped[str | None] = mapped_column(Text)


class DemoClip(Base):
    __tablename__ = "demo_clip"
    id: Mapped[int] = mapped_column(primary_key=True)
    step: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    form: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    audio_path: Mapped[str] = mapped_column(String(255))
    duration_s: Mapped[float | None] = mapped_column(Float)
    source_voice_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bj)


class LearnedCorrection(Base):
    __tablename__ = "learned_correction"
    id: Mapped[int] = mapped_column(primary_key=True)
    wrong: Mapped[str] = mapped_column(String(32), unique=True)
    right: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(32))
    voice_log_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bj)
    hits: Mapped[int] = mapped_column(Integer, default=0)
