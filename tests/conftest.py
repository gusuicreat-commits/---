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
              "thick": (18, 22), "light": (300, 320), "noise": (0, 85)}
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
