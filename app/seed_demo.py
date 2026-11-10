from sqlalchemy import select

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
            examples = {"diameter": (1240, 1260), "length": (2990, 3010),
                        "thick": (18, 22), "light": (300, 320), "noise": (0, 85)}
            for rule in current_rules:
                if rule.name in examples:
                    rules.update_rule(session, rule.id, *examples[rule.name])
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


if __name__ == "__main__":
    main()
