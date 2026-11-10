from sqlalchemy import select

from app.export import finish, qualified_text
from app.fields import load_fields
from app.models import ControlCabinetParam


def build(ws, session, ht_code) -> None:
    record = session.scalar(select(ControlCabinetParam).where(ControlCabinetParam.ht_code == ht_code)
                            .order_by(ControlCabinetParam.id.desc()).limit(1))
    fields = load_fields()["control_cabinet_param"]["fields"]
    ws.append([f"{field['label']}({field['unit']})" if field.get("unit") else field["label"]
               for field in fields] + ["是否合格", "录入时间"])
    if record is not None:
        ws.append([getattr(record, field["name"]) for field in fields] + [
            qualified_text(record.is_qualified), record.created_at.strftime("%Y-%m-%d %H:%M:%S")])
    finish(ws)
