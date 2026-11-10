"""通过接口批量录入 18 个样例合同（全部虚构，客户名带「样例」）。

用法：先启动主程序，然后
    .venv/bin/python tools/seed_batch.py [http://localhost:8600]
走真实接口，所以每条记录都按当前规则判定；规则需先配置好（seed_demo 或规则页）。
已存在的合同号会跳过。录入时间是运行时刻（接口不支持回填时间）。
"""
import random
import sys
from collections import Counter

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8600").rstrip("/") + "/api"
random.seed(1110)
c = httpx.Client(base_url=BASE, timeout=10)

NAMES = ["东岳重型机械（样例）", "中原港口装卸（样例）", "北辰钢结构（样例）", "江淮物流装备（样例）", "西岭水电工程（样例）",
         "南湖造船配套（样例）", "青石矿山机械（样例）", "远航冶金设备（样例）", "泰和建材（样例）", "长风电力检修（样例）",
         "金桥仓储（样例）", "星河新能源（样例）", "云海化工设备（样例）", "汇通轨道交通（样例）", "凌云环保工程（样例）",
         "宏远造纸机械（样例）", "安泰汽车零部件（样例）", "鼎盛预制构件（样例）"]


def drum(bad=None):
    v = {"diameter": f"{random.uniform(1243, 1258):.1f}", "length": f"{random.uniform(2993, 3008):.1f}",
         "thick": f"{random.uniform(18.6, 21.6):.2f}", "light": f"{random.uniform(303, 318):.0f}"}
    if bad == "diameter": v["diameter"] = random.choice(["1262.5", "1237", "1261"])
    if bad == "thick": v["thick"] = random.choice(["17.6", "22.4", "12.5"])
    if bad == "light": v["light"] = random.choice(["296", "324"])
    if bad == "length": v["length"] = "3013"
    return v


def travel(bad=False):
    return {"reducer_nameplate": random.choice(["ZQ500-31.5", "ZQ650-40.17", "QJS280-50", "ZSC600-31.5"]) + " 样例",
            "motor_nameplate": random.choice(["YZR160M2-6 7.5kW", "YZR180L-6 15kW", "YZP200L-6 22kW"]) + " 样例",
            "noise": str(random.choice([86, 88.5, 91]) if bad else random.randint(72, 84))}


# (日期, 序号, 卷筒计划, 大车传动计划)；计划："ok" / "bad:字段" / "retest:字段"（先不合格再合格）/ None（不录）
PLAN = [
    ("20261103", 10, "ok", "ok"), ("20261103", 20, "bad:diameter", "ok"), ("20261104", 10, "ok", "ok"),
    ("20261104", 20, "retest:thick", "ok"), ("20261104", 30, "ok", "bad"), ("20261105", 10, "ok", "ok"),
    ("20261105", 20, "ok", None), ("20261106", 10, "retest:light", "retest"), ("20261106", 20, "ok", "ok"),
    ("20261106", 30, "bad:thick", "ok"), ("20261107", 10, "ok", "ok"), ("20261107", 20, None, None),
    ("20261108", 10, "ok", "ok"), ("20261108", 20, "retest:diameter", "ok"), ("20261109", 10, "bad:length", "bad"),
    ("20261109", 20, "ok", "ok"), ("20261109", 30, "ok", None), ("20261110", 10, "ok", "ok"),
]


def post(path, body):
    r = c.post(path, json=body)
    r.raise_for_status()


existing = {x["ht_code"] for x in c.get("/contracts").json()["items"]}
made = 0
for (d, s, pd, pt), name in zip(PLAN, NAMES):
    ht = f"{d}-{s}"
    if ht in existing:
        print("跳过已存在", ht)
        continue
    post("/contracts", {"ht_code": ht, "name": name, "date": f"{d[:4]}-{d[4:6]}-{d[6:]}"})
    made += 1
    if pd:
        kind, _, f = pd.partition(":")
        if kind == "retest":
            post(f"/contracts/{ht}/records/drum", drum(f))
            post(f"/contracts/{ht}/records/drum", drum())
        else:
            post(f"/contracts/{ht}/records/drum", drum(f if kind == "bad" else None))
    if pt:
        if pt == "retest":
            post(f"/contracts/{ht}/records/travel_drive", travel(True))
        post(f"/contracts/{ht}/records/travel_drive", travel(pt == "bad"))

items = c.get("/contracts").json()["items"]
print(f"新建 {made} 个合同；现在共 {len(items)} 个：", dict(Counter(x["status"] for x in items)))
