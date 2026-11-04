"""Claude fallback: used only when local rules can't produce a complete/validated result."""

import asyncio
import base64
import json
import logging
import os
import re

import anthropic
import cv2
import httpx
import numpy as np

from .classify import TYPES
from .config import Settings

log = logging.getLogger(__name__)

# every locally known type, plus a few the LLM may recognise that rules don't handle yet
DOC_TYPES = [t for t in TYPES if t != "unknown"] + ["proof_of_address", "other"]

SYSTEM = """Tu extrais les données de documents (KYC) à partir d'une image et/ou du texte OCR fourni.
L'image fait foi : le texte OCR peut contenir des erreurs, sers-t'en seulement comme aide.
N'invente rien : n'inclus un champ que s'il est lisible sur le document. Recopie les valeurs telles qu'imprimées (accents compris), sans commentaire.
Dates au format AAAA-MM-JJ. Pays et nationalités en code ISO 3166 alpha-3 (FRA, DEU...). Sexe : M, F ou X.
Utilise de préférence ces clés selon le type :
- id_card / passport / residence_permit : surname, usage_name, given_names, sex, nationality, birth_date, birth_place, document_number, issue_date, expiry_date, issuing_authority, address, permit_type
- driving_license : surname, given_names, birth_date, birth_place, issue_date, expiry_date, issuing_authority, license_number, categories (séparées par des virgules)
- vehicle_registration : plate, first_registration_date, certificate_date, holder_name, holder_address, make, type_variant, commercial_name, vin, national_category, fuel, fiscal_power_cv, engine_capacity_cm3, max_power_kw, seats, co2_g_km
- insurance_certificate : insurer, policy_number, holder_name, holder_address, valid_from, valid_to, plate, vehicle_make, vehicle_model, vehicle_category, coverage
- business_card : full_name, job_title, company, emails, phones, websites, address, siren_siret (listes séparées par des virgules)
- autres : clés snake_case explicites en anglais.
Réponds uniquement par des lignes « clé: valeur », une par champ, sans autre texte.
Première ligne « document_type: » suivi d'un de : """ + ", ".join(DOC_TYPES) + """.
Deuxième ligne « country: » suivi du pays émetteur du document (ISO alpha-3), ou rien."""

LINES_SYSTEM = """Chaque image est une ligne extraite d'un document, précédée de sa clé et de la lecture d'un OCR qui peut contenir des erreurs.
Recopie exactement le texte imprimé sur l'image : mêmes caractères, accents, chiffres, ponctuation et espaces, sans reformater les dates ni les montants.
Si l'image est illisible, recopie la lecture OCR telle quelle. Aucun autre texte que les lignes demandées."""

LIST_KEYS = {"emails", "phones", "websites", "siren_siret", "categories"}


class LlmError(RuntimeError):
    pass


class LlmClient:
    def __init__(self, settings: Settings):
        self.s = settings
        if not (settings.anthropic_api_key or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise LlmError("ANTHROPIC_API_KEY absente")
        kw = {
            "timeout": settings.llm_timeout_s,
            "max_retries": 1,
            # keep the TLS connection open between sparse requests (SDK default: closed after 5 s idle)
            "http_client": anthropic.DefaultAsyncHttpxClient(
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=120)
            ),
        }
        if settings.anthropic_api_key:
            kw["api_key"] = settings.anthropic_api_key
        self.client = anthropic.AsyncAnthropic(**kw)

    async def keep_warm(self, every_s: float = 25.0) -> None:
        """Free metadata call at regular intervals so a request never pays a new TLS handshake (~0.5 s)."""
        while True:
            try:
                await self.client.models.retrieve(self.s.llm_model)
            except Exception as e:  # noqa: BLE001 - best effort
                log.debug("LLM keep-warm failed: %s", e)
            await asyncio.sleep(every_s)

    def _img_block(self, img: np.ndarray, max_side: int | None = None) -> dict:
        h, w = img.shape[:2]
        m = max(h, w)
        limit = max_side or self.s.llm_image_max_side
        if m > limit:
            sc = limit / m
            img = cv2.resize(img, (int(w * sc), int(h * sc)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise LlmError("encodage image impossible")
        return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(buf.tobytes()).decode()}}

    async def _call(self, content: list[dict], system: str, schema: dict | None, max_tokens: int) -> dict:
        """schema=None: plain text answer (a JSON schema costs ~0.5-1 s of extra latency)."""
        kw = {}
        if not self.s.llm_thinking:
            # reading a document needs no reasoning; disabled thinking is accepted up to effort "high"
            kw["thinking"] = {"type": "disabled"}
        try:
            resp = await self.client.messages.create(
                model=self.s.llm_model,
                max_tokens=max_tokens,
                system=system,
                output_config={"effort": self.s.llm_effort, **({"format": {"type": "json_schema", "schema": schema}} if schema else {})},
                messages=[{"role": "user", "content": content}],
                **kw,
            )
        except anthropic.AuthenticationError as e:
            raise LlmError("clé Anthropic invalide ou absente") from e
        except anthropic.RateLimitError as e:
            raise LlmError("limite de débit Anthropic atteinte") from e
        except anthropic.APITimeoutError as e:
            raise LlmError("délai dépassé côté LLM") from e
        except anthropic.APIStatusError as e:
            raise LlmError(f"erreur API Anthropic {e.status_code}") from e
        except anthropic.APIConnectionError as e:
            raise LlmError("connexion à l'API Anthropic impossible") from e

        if resp.stop_reason == "refusal":
            raise LlmError("requête refusée par le modèle")
        if resp.stop_reason == "max_tokens":
            raise LlmError("réponse LLM tronquée")
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if not text:
            raise LlmError("réponse LLM vide")
        data = json.loads(text) if schema else {"text": text}
        data["_usage"] = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
        return data

    async def read_lines(self, crops: list[tuple[str, np.ndarray, str]]) -> dict:
        """Targeted check: re-read only the doubtful values from crops of their lines.

        crops: (field key, image crop of the line(s) holding the value, value read by the OCR).
        Returns {"fields": {key: value as printed}, "usage": {...}}.
        """
        content: list[dict] = []
        for key, img, ocr_value in crops:
            content.append({"type": "text", "text": f"[{key}] lecture OCR : {ocr_value}"})
            content.append(self._img_block(img, max_side=800))
        content.append({"type": "text", "text": "Réponds une ligne par clé, au format : [clé] texte imprimé"})
        data = await self._call(content, LINES_SYSTEM, None, 1024)
        fields = {}
        for row in data["text"].splitlines():
            m = re.match(r"\s*\[(\w+)\]\s*:?\s*(.*)$", row)
            if m and m.group(2).strip():
                fields[m.group(1)] = m.group(2).strip()
        return {"fields": fields, "usage": data["_usage"]}

    async def extract(self, images: list[np.ndarray], ocr_text: str, hint_type: str | None, partial: dict) -> dict:
        content: list[dict] = [self._img_block(i) for i in images]
        parts = []
        if hint_type and hint_type != "unknown":
            parts.append(f"Type probable (détection locale) : {hint_type}")
        if partial:
            parts.append("Champs déjà extraits localement (à vérifier/corriger, compléter les manquants) :\n" + json.dumps(partial, ensure_ascii=False))
        parts.append("Texte OCR :\n" + (ocr_text[:12000] or "(vide)"))
        content.append({"type": "text", "text": "\n\n".join(parts)})

        # plain "key: value" lines rather than a JSON schema: same content, ~0.5-1 s faster
        data = await self._call(content, SYSTEM, None, 4096)
        fields: dict = {}
        meta = {"document_type": "other", "country": ""}
        for row in data["text"].splitlines():
            m = re.match(r"\s*[-*]?\s*([A-Za-z_][\w]*)\s*:\s*(.*)$", row)
            if not m:
                continue
            k, v = m.group(1).strip().lower(), m.group(2).strip().strip('"')
            if k in meta:
                meta[k] = v
            elif v:
                fields[k] = [x.strip() for x in v.split(",") if x.strip()] if k in LIST_KEYS else v
        if meta["document_type"] not in DOC_TYPES:
            meta["document_type"] = "other"
        return {
            "document_type": meta["document_type"],
            "country": meta["country"],
            "fields": fields,
            "usage": data["_usage"],
        }
