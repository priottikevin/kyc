"""Back-office API behind /admin: client companies, their API keys and usage.

One administrator, identified by ADMIN_PASSWORD. The session is a signed, expiring HttpOnly cookie;
it is signed with a key derived from the password, so changing the password ends every session.
These routes stay out of the public OpenAPI contract.
"""
import hashlib
import hmac
import logging
import secrets
import time
from collections import deque
from typing import Annotated

import psycopg
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import get_settings
from .store import Store

log = logging.getLogger("kyc.admin")
settings = get_settings()

COOKIE = "kyc_admin"
SESSION_SECONDS = 12 * 3600
MAX_FAILURES = 10
FAILURE_WINDOW_SECONDS = 300
LOCAL_HOSTS = {"localhost", "127.0.0.1", "testserver"}

# Global rather than per IP: X-Forwarded-For can be forged by the client here.
failures: deque[float] = deque()


class Login(BaseModel):
    password: str = Field(max_length=256)


class NewClient(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    contact_email: str | None = Field(None, max_length=320)


class ClientChanges(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    contact_email: str | None = Field(None, max_length=320)
    active: bool | None = None


class NewKey(BaseModel):
    name: str = Field(min_length=1, max_length=200)


def _sign(expires: int) -> str:
    signing_key = hashlib.sha256(b"kyc-admin-session:" + settings.admin_password.encode()).digest()
    return hmac.new(signing_key, str(expires).encode(), hashlib.sha256).hexdigest()


def session_token(now: float | None = None) -> str:
    expires = int(now if now is not None else time.time()) + SESSION_SECONDS
    return f"{expires}.{_sign(expires)}"


def session_valid(token: str | None) -> bool:
    if not token or not settings.admin_password:
        return False
    expires, _, signature = token.partition(".")
    if not expires.isdecimal() or int(expires) < time.time():
        return False
    return secrets.compare_digest(signature.encode(), _sign(int(expires)).encode())


def require_admin(kyc_admin: Annotated[str | None, Cookie()] = None) -> None:
    if not session_valid(kyc_admin):
        raise HTTPException(401, "Session administrateur requise")


def get_store(request: Request) -> Store:
    store = request.app.state.store
    if store is None:
        raise HTTPException(503, "Base de données non configurée (DATABASE_URL)")
    return store


StoreDep = Annotated[Store, Depends(get_store)]

public = APIRouter(prefix="/api/admin", include_in_schema=False)
router = APIRouter(prefix="/api/admin", include_in_schema=False, dependencies=[Depends(require_admin)])


@public.post("/login")
def login(body: Login, request: Request, response: Response):
    if not settings.admin_password:
        raise HTTPException(503, "Administration désactivée (ADMIN_PASSWORD absent)")
    now = time.time()
    while failures and failures[0] < now - FAILURE_WINDOW_SECONDS:
        failures.popleft()
    if len(failures) >= MAX_FAILURES:
        raise HTTPException(429, "Trop de tentatives, réessayez dans quelques minutes")
    if not secrets.compare_digest(body.password.encode(), settings.admin_password.encode()):
        failures.append(now)
        log.warning("Failed admin login")
        raise HTTPException(401, "Mot de passe incorrect")
    response.set_cookie(COOKIE, session_token(now), max_age=SESSION_SECONDS, path="/api/admin", httponly=True,
                        samesite="strict", secure=request.url.hostname not in LOCAL_HOSTS)
    return {"ok": True}


@public.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/api/admin")
    return {"ok": True}


@router.get("/session")
def session():
    return {"ok": True}


@router.get("/clients")
def list_clients(store: StoreDep):
    return store.list_clients()


@router.post("/clients", status_code=201)
def create_client(body: NewClient, store: StoreDep):
    return store.create_client(body.name.strip(), (body.contact_email or "").strip() or None)


@router.patch("/clients/{client_id}")
def update_client(client_id: int, body: ClientChanges, store: StoreDep):
    client = store.update_client(client_id, body.model_dump(exclude_unset=True))
    if not client:
        raise HTTPException(404, "Client introuvable")
    return client


@router.get("/clients/{client_id}/keys")
def list_keys(client_id: int, store: StoreDep):
    return store.list_keys(client_id)


@router.post("/clients/{client_id}/keys", status_code=201)
def create_key(client_id: int, body: NewKey, store: StoreDep):
    key = store.create_key(client_id, body.name.strip())
    if not key:
        raise HTTPException(404, "Client introuvable")
    return key


@router.delete("/keys/{key_id}")
def revoke_key(key_id: int, store: StoreDep):
    key = store.revoke_key(key_id)
    if not key:
        raise HTTPException(404, "Clé introuvable")
    return key


@router.get("/clients/{client_id}/usage")
def usage(client_id: int, store: StoreDep):
    return store.monthly_usage(client_id)


def database_error(_: Request, exc: psycopg.Error) -> JSONResponse:
    log.error("Database error: %s", exc)
    return JSONResponse({"detail": "Base de données indisponible"}, status_code=503)
