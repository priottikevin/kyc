from fastapi.testclient import TestClient

from app import main

from . import synth


def client():
    return TestClient(main.app)


def test_health_and_extract():
    with client() as c:
        assert c.get("/api/health").json()["status"] == "ok"
        r = c.post("/api/extract", files={"file": ("cni.jpg", synth.id_card_2021(), "image/jpeg")}, data={"llm": "off"})
        assert r.status_code == 200
        body = r.json()
        assert body["document_type"] == "id_card"
        assert body["mrz"]["valid"] is True


def test_rejects_bad_files():
    with client() as c:
        assert c.post("/api/extract", files={"file": ("x.txt", b"hello world", "text/plain")}).status_code == 415
        assert c.post("/api/extract", files={"file": ("x.jpg", b"", "image/jpeg")}).status_code == 400
        assert c.post("/api/extract", files={"file": ("x.jpg", synth.passport(), "image/jpeg")}, data={"llm": "maybe"}).status_code == 422


def test_api_key(monkeypatch):
    monkeypatch.setattr(main.settings, "api_keys", "secret1,secret2")
    with client() as c:
        f = {"file": ("p.jpg", synth.passport(), "image/jpeg")}
        assert c.post("/api/extract", files=f, data={"llm": "off"}).status_code == 401
        assert c.post("/api/extract", files=f, data={"llm": "off"}, headers={"X-API-Key": "nope"}).status_code == 401
        assert c.post("/api/extract", files=f, data={"llm": "off"}, headers={"X-API-Key": "secret2"}).status_code == 200
