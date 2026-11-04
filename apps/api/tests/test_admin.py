"""Back office and database-backed keys. Needs a disposable PostgreSQL: set TEST_DATABASE_URL."""
import os
import time

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import admin, main

from . import synth

DB_URL = os.environ.get("TEST_DATABASE_URL")
PASSWORD = "test-admin-password"
needs_db = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def no_db(monkeypatch, pipeline):
    admin.failures.clear()
    monkeypatch.setattr(main, "Pipeline", lambda _settings: pipeline)  # skip reloading the OCR models for each test
    monkeypatch.setattr(main.settings, "admin_password", PASSWORD)
    monkeypatch.setattr(main.settings, "database_url", "")
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def api(monkeypatch, pipeline):
    admin.failures.clear()
    monkeypatch.setattr(main, "Pipeline", lambda _settings: pipeline)
    with psycopg.connect(DB_URL, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS usage_events, api_keys, clients")
    monkeypatch.setattr(main.settings, "admin_password", PASSWORD)
    monkeypatch.setattr(main.settings, "database_url", DB_URL)
    with TestClient(main.app) as c:
        yield c


def log_in(c):
    assert c.post("/api/admin/login", json={"password": PASSWORD}).status_code == 200


def new_key(c, name="Tribu"):
    client = c.post("/api/admin/clients", json={"name": name, "contact_email": "contact@example.com"}).json()
    return client, c.post(f"/api/admin/clients/{client['id']}/keys", json={"name": "production"}).json()


def extract(c, key=None, content=None):
    files = {"file": ("p.jpg", content if content is not None else synth.passport(), "image/jpeg")}
    return c.post("/api/extract", files=files, data={"llm": "off"}, headers={"X-API-Key": key} if key else {})


def test_login_required_and_session_checked(no_db):
    c = no_db
    assert c.get("/api/admin/session").status_code == 401
    assert c.post("/api/admin/login", json={"password": "wrong"}).status_code == 401
    log_in(c)
    assert c.get("/api/admin/session").status_code == 200
    c.post("/api/admin/logout")
    assert c.get("/api/admin/session").status_code == 401


def test_forged_or_expired_sessions_are_rejected(no_db):
    expires = int(time.time()) + 3600
    assert not admin.session_valid(f"{expires}.{'0' * 64}")
    assert not admin.session_valid(f"{expires + 1}.{admin._sign(expires)}")
    assert not admin.session_valid("not-a-token")
    assert not admin.session_valid(admin.session_token(time.time() - admin.SESSION_SECONDS - 1))
    assert admin.session_valid(admin.session_token())


def test_no_password_means_no_admin(no_db, monkeypatch):
    token = admin.session_token()
    monkeypatch.setattr(main.settings, "admin_password", "")
    assert no_db.post("/api/admin/login", json={"password": ""}).status_code == 503
    assert not admin.session_valid(token)


def test_login_is_throttled(no_db):
    for _ in range(admin.MAX_FAILURES):
        assert no_db.post("/api/admin/login", json={"password": "wrong"}).status_code == 401
    assert no_db.post("/api/admin/login", json={"password": PASSWORD}).status_code == 429


def test_session_cookie_flags(no_db):
    cookie = no_db.post("/api/admin/login", json={"password": PASSWORD}).headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/api/admin" in cookie


def test_secure_cookie_outside_localhost(no_db):
    r = no_db.post("/api/admin/login", json={"password": PASSWORD}, headers={"Host": "kyc.priotti.fr"})
    assert "secure" in r.headers["set-cookie"].lower()


def test_admin_stays_out_of_public_contract(no_db):
    assert not [p for p in no_db.get("/api/openapi.json").json()["paths"] if "admin" in p]


def test_without_database_admin_data_is_unavailable(no_db):
    log_in(no_db)
    assert no_db.get("/api/admin/clients").status_code == 503


@needs_db
def test_client_key_lifecycle(api):
    log_in(api)
    client, key = new_key(api)
    assert key["key"].startswith("kyc_live_") and key["key"].startswith(key["prefix"])
    assert extract(api, key["key"]).status_code == 200

    listed = api.get(f"/api/admin/clients/{client['id']}/keys").json()
    assert len(listed) == 1 and "key" not in listed[0] and "key_hash" not in listed[0]
    assert listed[0]["last_used_at"]

    assert api.delete(f"/api/admin/keys/{key['id']}").status_code == 200
    assert extract(api, key["key"]).status_code == 401


@needs_db
def test_plain_key_is_never_stored(api):
    log_in(api)
    _, key = new_key(api)
    with psycopg.connect(DB_URL) as conn:
        row = conn.execute("SELECT * FROM api_keys").fetchone()
    assert key["key"] not in [str(v) for v in row]


@needs_db
def test_deactivating_a_client_blocks_its_keys(api):
    log_in(api)
    client, key = new_key(api)
    api.patch(f"/api/admin/clients/{client['id']}", json={"active": False})
    assert extract(api, key["key"]).status_code == 401
    api.patch(f"/api/admin/clients/{client['id']}", json={"active": True})
    assert extract(api, key["key"]).status_code == 200


@needs_db
def test_database_requires_a_key_even_without_api_keys(api):
    assert api.get("/api/health").json()["auth"] is True
    assert extract(api).status_code == 401
    assert extract(api, "kyc_live_unknown").status_code == 401


@needs_db
def test_api_keys_env_still_works_and_is_not_metered(api, monkeypatch):
    monkeypatch.setattr(main.settings, "api_keys", "legacy-key")
    assert extract(api, "legacy-key").status_code == 200
    log_in(api)
    assert api.get("/api/admin/clients").json() == []


@needs_db
def test_usage_is_counted_per_client(api):
    log_in(api)
    client, key = new_key(api)
    other, _ = new_key(api, "Autre")
    assert extract(api, key["key"]).status_code == 200
    assert extract(api, key["key"], b"not an image").status_code == 415

    month = api.get(f"/api/admin/clients/{client['id']}/usage").json()
    assert len(month) == 1 and month[0]["calls"] == 2 and month[0]["successes"] == 1
    assert api.get(f"/api/admin/clients/{other['id']}/usage").json() == []
    rows = {c["name"]: c for c in api.get("/api/admin/clients").json()}
    assert rows["Tribu"]["calls"] == 2 and rows["Autre"]["calls"] == 0 and rows["Tribu"]["active_keys"] == 1


@needs_db
def test_usage_keeps_no_document_data(api):
    log_in(api)
    _, key = new_key(api)
    extract(api, key["key"])
    with psycopg.connect(DB_URL) as conn:
        columns = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'usage_events'").fetchall()}
    assert columns == {"id", "client_id", "key_id", "created_at", "http_status", "document_type",
                       "llm_used", "llm_cost_usd", "duration_ms"}


@needs_db
def test_database_outage_fails_closed(api, monkeypatch):
    def down(_key):
        raise psycopg.OperationalError("down")

    monkeypatch.setattr(main.app.state.store, "find_key", down)
    assert TestClient(main.app, raise_server_exceptions=False).post(
        "/api/extract", files={"file": ("p.jpg", synth.passport(), "image/jpeg")}, headers={"X-API-Key": "kyc_live_x"}
    ).status_code == 503


@needs_db
def test_unknown_ids_return_404(api):
    log_in(api)
    assert api.patch("/api/admin/clients/999", json={"active": False}).status_code == 404
    assert api.post("/api/admin/clients/999/keys", json={"name": "x"}).status_code == 404
    assert api.delete("/api/admin/keys/999").status_code == 404
