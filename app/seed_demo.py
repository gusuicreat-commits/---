import json

from sqlalchemy import select

from app.config import ROOT
from app.db import SessionLocal, init_db
from app.models import Contract, Rule
from app.services import contracts, records, rules
from app.timeutil import today_bj


def main():
    print("写入的是样例数据")
    init_db()
    with SessionLocal() as session:
        current_rules = session.scalars(select(Rule)).all()
        if all(rule.min is None and rule.max is None for rule in current_rules):
            profile = json.loads((ROOT / "config/showcase.json").read_text(encoding="utf-8"))
            examples = {(item["table"], item["field"]): (item["min"], item["max"])
                        for item in profile["rules"]}
            for rule in current_rules:
                if (rule.table_name, rule.name) in examples:
                    rules.update_rule(session, rule.id, *examples[rule.table_name, rule.name])
        prefix = today_bj().strftime("%Y%m%d")
        for seq, customer in [(10, "甲"), (20, "乙"), (30, "丙")]:
            code = f"{prefix}-{seq}"
            if session.scalar(select(Contract.id).where(Contract.ht_code == code)) is not None:
                continue
            contracts.create(session, code, f"样例客户{customer}")
            if seq == 30:
                continue
            records.submit(session, "drum", code, {"diameter": 1250 if seq == 10 else 1262,
                           "length": 3000, "thick": 20, "light": 310})
            records.submit(session, "travel_drive", code,
                           {"reducer_nameplate": "样例减速机", "motor_nameplate": "样例电机", "noise": 80})

            records.submit(session, "control_cabinet", code,
                           {"insulation_resistance": 500, "control_wire_torque": 1.2, "electrical_clearance": 8})
            records.submit(session, "travel_paint", code,
                           {"primer_thickness_ratio": "98", "total_thickness_ratio": 99, "adhesion_loss_ratio": 1})


if __name__ == "__main__":
    main()
