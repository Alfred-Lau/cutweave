"""UI 静态目录挂载测试。"""
from fastapi.testclient import TestClient

from app.main import app


def test_ui_index_served():
    with TestClient(app) as c:
        r = c.get("/ui/")
        assert r.status_code == 200
        assert "CutWeave" in r.text


def test_ui_assets_served():
    with TestClient(app) as c:
        assert c.get("/ui/app.js").status_code == 200
        assert c.get("/ui/style.css").status_code == 200
