from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Integer, String, UniqueConstraint
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


MODEL_BY_TABLE = {"drum_param": DrumParam, "travel_drive_param": TravelDriveParam}
