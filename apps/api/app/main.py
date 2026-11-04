import asyncio
import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from . import admin
from .classify import TYPES
from .config import get_settings
from .ingest import UnsupportedFile
from .pipeline import Options, Pipeline
from .schemas import API_VERSION, ErrorResponse, ExtractResponse, Health
from .store import Caller, Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("kyc")
settings = get_settings()
state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.store = Store(settings.database_url) if settings.database_url else None
    if app.state.store:
        await asyncio.to_thread(app.state.store.open)
    pipe = Pipeline(settings)
    pipe.warmup()  # load ONNX models before accepting traffic
    state["pipeline"] = pipe
    warm = asyncio.create_task(pipe.llm.keep_warm()) if pipe.llm else None
    log.info("Ready (OCR %s/%s, LLM %s)", settings.ocr_fast_model, settings.ocr_accurate_model, settings.llm_mode)
    yield
    if warm:
        warm.cancel()
    if app.state.store:
        await asyncio.to_thread(app.state.store.close)


DESCRIPTION = """Extraction de données de documents (identité, véhicule, assurance, entreprise, factures, sinistres).

OCR local d'abord ; Claude n'est appelé que pour relire les valeurs douteuses ou les documents non compris.
Authentification : header `X-API-Key`. Aucun fichier n'est conservé.

Pour décider automatiquement : accepter si `status` = `verified`, ou `complete` sans champ de
`field_confidence` au niveau `check`/`unknown` ; sinon faire vérifier les champs signalés."""

ERRORS = {
    401: {"model": ErrorResponse, "description": "Clé API invalide ou manquante"},
    413: {"model": ErrorResponse, "description": "Fichier trop volumineux"},
    415: {"model": ErrorResponse, "description": "Format non supporté (HEIC, PDF chiffré...)"},
    422: {"description": "Paramètre invalide"},
    503: {"model": ErrorResponse, "description": "Vérification de la clé momentanément impossible, réessayer"},
}

app = FastAPI(title="KYC Extract API", version=API_VERSION, description=DESCRIPTION, lifespan=lifespan,
              docs_url="/api/docs", redoc_url="/api/redoc", openapi_url="/api/openapi.json")
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_list, allow_methods=["GET", "POST"], allow_headers=["*"])


app.state.store = None
app.add_exception_handler(psycopg.Error, admin.database_error)
app.include_router(admin.public)
app.include_router(admin.router)


def auth_enabled() -> bool:
    return bool(settings.api_key_set or app.state.store)


def require_key(x_api_key: Annotated[str | None, Header()] = None) -> Caller | None:
    """Returns the client to meter, or None for API_KEYS callers, which have no client. Sync: runs in the threadpool."""
    if not auth_enabled():
        return None
    if x_api_key:
        if any(secrets.compare_digest(x_api_key.encode(), k.encode()) for k in settings.api_key_set):
            return None
        caller = app.state.store.find_key(x_api_key) if app.state.store else None
        if caller:
            return caller
    raise HTTPException(401, "Clé API invalide ou manquante (header X-API-Key)")


@app.get("/api/health", response_model=Health)
async def health():
    pipe = state.get("pipeline")
    return {
        "status": "ok" if pipe else "starting",
        "auth": auth_enabled(),
        "llm": settings.llm_mode if pipe and pipe.llm else "off",
        "llm_model": settings.llm_model if pipe and pipe.llm else None,
    }


@app.get("/api/document-types", response_model=dict[str, str], summary="Types reconnus (clé -> libellé)")
async def document_types():
    return TYPES


@app.post("/api/extract", response_model=ExtractResponse, responses=ERRORS, summary="Extraire les données d'un document")
async def extract(
    caller: Annotated[Caller | None, Depends(require_key)],
    file: Annotated[UploadFile, File(description="PDF ou image (JPEG, PNG, WEBP, TIFF)")],
    document_type: Annotated[str | None, Form(description="Forcer le type (clé de /api/document-types), sinon détection auto")] = None,
    llm: Annotated[str | None, Form(description="off = 100 % local ; fallback (défaut) = LLM seulement si nécessaire ; always")] = None,
    include_text: Annotated[bool, Form(description="Renvoyer le texte OCR brut dans `text`")] = False,
    include_positions: Annotated[bool, Form(description="Renvoyer l'emplacement de chaque valeur dans `field_positions`")] = False,
):
    started = time.perf_counter()
    http_status, result = 500, {}
    try:
        max_bytes = settings.max_upload_mb * 1024 * 1024
        data = await file.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise HTTPException(413, f"Fichier trop volumineux (max {settings.max_upload_mb} Mo)")
        if not data:
            raise HTTPException(400, "Fichier vide")
        if llm not in (None, "", "off", "fallback", "always"):
            raise HTTPException(422, "llm doit valoir off, fallback ou always")
        try:
            result = await state["pipeline"].process(data, Options(document_type=document_type or None, llm=llm or None,
                                                                   include_text=include_text, include_positions=include_positions))
        except UnsupportedFile as e:
            raise HTTPException(415, str(e)) from e
        http_status = 200
        return result
    except HTTPException as e:
        http_status = e.status_code
        raise
    finally:
        if caller:
            engine = result.get("engine") or {}
            await asyncio.to_thread(app.state.store.record_usage, caller, http_status,
                                    round((time.perf_counter() - started) * 1000), result.get("document_type"),
                                    bool(engine.get("llm_used")), engine.get("llm_cost_usd"))


if settings.static_dir and Path(settings.static_dir).is_dir():
    # Dev convenience: serve apps/web from the API (prod: nginx container)
    app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="web")
