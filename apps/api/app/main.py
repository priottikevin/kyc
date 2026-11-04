import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from .classify import TYPES
from .config import get_settings
from .ingest import UnsupportedFile
from .pipeline import Options, Pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("kyc")
settings = get_settings()
state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    pipe = Pipeline(settings)
    pipe.warmup()  # load ONNX models before accepting traffic
    state["pipeline"] = pipe
    warm = asyncio.create_task(pipe.llm.keep_warm()) if pipe.llm else None
    log.info("Ready (OCR %s/%s, LLM %s)", settings.ocr_fast_model, settings.ocr_accurate_model, settings.llm_mode)
    yield
    if warm:
        warm.cancel()


app = FastAPI(title="KYC Extract API", version="1.0.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_list, allow_methods=["GET", "POST"], allow_headers=["*"])


def require_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    keys = settings.api_key_set
    if not keys:
        return
    if not x_api_key or not any(secrets.compare_digest(x_api_key, k) for k in keys):
        raise HTTPException(401, "Clé API invalide ou manquante (header X-API-Key)")


@app.get("/api/health")
async def health():
    pipe = state.get("pipeline")
    return {
        "status": "ok" if pipe else "starting",
        "auth": bool(settings.api_key_set),
        "llm": settings.llm_mode if pipe and pipe.llm else "off",
        "llm_model": settings.llm_model if pipe and pipe.llm else None,
    }


@app.get("/api/document-types")
async def document_types():
    return TYPES


@app.post("/api/extract", dependencies=[Depends(require_key)])
async def extract(
    file: Annotated[UploadFile, File(description="PDF ou image (JPEG, PNG, WEBP, TIFF)")],
    document_type: Annotated[str | None, Form(description="Forcer le type (sinon détection auto)")] = None,
    llm: Annotated[str | None, Form(description="off | fallback | always (défaut serveur)")] = None,
    include_text: Annotated[bool, Form()] = False,
):
    max_bytes = settings.max_upload_mb * 1024 * 1024
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(413, f"Fichier trop volumineux (max {settings.max_upload_mb} Mo)")
    if not data:
        raise HTTPException(400, "Fichier vide")
    if llm not in (None, "", "off", "fallback", "always"):
        raise HTTPException(422, "llm doit valoir off, fallback ou always")
    try:
        return await state["pipeline"].process(data, Options(document_type=document_type or None, llm=llm or None, include_text=include_text))
    except UnsupportedFile as e:
        raise HTTPException(415, str(e)) from e


if settings.static_dir and Path(settings.static_dir).is_dir():
    # Dev convenience: serve apps/web from the API (prod: nginx container)
    app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="web")
