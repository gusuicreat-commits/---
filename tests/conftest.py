import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_engine


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("QC_DB_PATH", str(tmp_path / "qc.sqlite3"))
    monkeypatch.setenv("QC_WEB_DIR", str(tmp_path / "不存在的页面目录"))
    engine = get_engine()
    yield
    engine.dispose()


@pytest.fixture
def client(isolated_db):
    from app.main import create_app
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def session(client):
    with Session(client.app.state.engine) as session:
        yield session


@pytest.fixture
def configured(client):
    limits = {"diameter": (1240, 1260), "length": (2990, 3010),
              "thick": (18, 22), "light": (300, 320), "noise": (0, 85), "insulation_resistance": (1, 1000), "control_wire_torque": (0.5, 2.5),
                        "electrical_clearance": (8, 20), "primer_thickness_ratio": (95, 100),
                        "total_thickness_ratio": (95, 100), "adhesion_loss_ratio": (0, 5),}
    for rule in client.get("/api/rules").json()["items"]:
        lower, upper = limits[rule["name"]]
        assert client.put(f"/api/rules/{rule['id']}", json={"min": lower, "max": upper}).status_code == 200
    return client


@pytest.fixture
def contract(configured):
    code = "20261110-10"
    assert configured.post("/api/contracts", json={"ht_code": code, "name": "测试客户"}).status_code == 201
    return code


@pytest.fixture
def good_drum():
    return {"diameter": "1250", "length": "3000", "thick": "20", "light": "310"}


@pytest.fixture
def complete_new_parameters(client):
    """旧两类业务用例显式补齐新增必检项，使用测试阈值。"""
    def complete(code):
        for kind, values in {
            "control_cabinet": {"insulation_resistance": 500, "control_wire_torque": 1.2, "electrical_clearance": 8},
            "travel_paint": {"primer_thickness_ratio": "98", "total_thickness_ratio": 99, "adhesion_loss_ratio": 1},
        }.items():
            response = client.post(f"/api/contracts/{code}/records/{kind}", json=values)
            assert response.status_code == 201
            assert response.json()["record"]["is_qualified"] == 1
    return complete
