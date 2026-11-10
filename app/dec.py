from decimal import Decimal, InvalidOperation, localcontext
import unicodedata

from sqlalchemy import Integer
from sqlalchemy.types import TypeDecorator

from app.errors import BadNumber, TooManyDecimals


class Dec2(TypeDecorator):
    impl = Integer
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else int(value * 100)

    def process_result_value(self, value, dialect):
        return None if value is None else (Decimal(value) / 100).quantize(Decimal("0.01"))


def to_dec2(raw) -> Decimal | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = unicodedata.normalize("NFKC", raw.strip())
        if not raw:
            return None
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float, Decimal)):
        raise BadNumber()
    try:
        value = Decimal(str(raw)) if not isinstance(raw, Decimal) else raw
    except (InvalidOperation, ValueError):
        raise BadNumber() from None
    if not value.is_finite():
        raise BadNumber()
    if value.as_tuple().exponent < -2:
        raise TooManyDecimals()
    with localcontext() as context:
        context.prec = max(28, len(value.as_tuple().digits) + max(value.adjusted(), 0) + 3)
        return value.quantize(Decimal("0.01"))


def fmt(d: Decimal | None) -> str:
    if d is None:
        return ""
    value = format(d, "f")
    return value.rstrip("0").rstrip(".") if "." in value else value
