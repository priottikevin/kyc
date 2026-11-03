"""Claude fallback: used only when local rules can't produce a complete/validated result."""

import base64
import json
import logging
import os

import anthropic
import cv2
import numpy as np

from .config import Settings

log = logging.getLogger(__name__)

DOC_TYPES = ["id_card", "passport", "residence_permit", "driving_license", "vehicle_registration", "insurance_certificate", "business_card", "invoice", "bank_details", "proof_of_address", "other"]

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
country = pays émetteur du document (ISO alpha-3) ou chaîne vide."""

SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {"type": "string", "enum": DOC_TYPES},
        "country": {"type": "string"},
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                "required": ["key", "value"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["document_type", "country", "fields"],
    "additionalProperties": False,
}

LIST_KEYS = {"emails", "phones", "websites", "siren_siret", "categories"}


class LlmError(RuntimeError):
    pass


class LlmClient:
    def __init__(self, settings: Settings):
        self.s = settings
        if not (settings.anthropic_api_key or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise LlmError("ANTHROPIC_API_KEY absente")
        kw = {"timeout": settings.llm_timeout_s, "max_retries": 1}
        if settings.anthropic_api_key:
            kw["api_key"] = settings.anthropic_api_key
        self.client = anthropic.AsyncAnthropic(**kw)

    def _img_block(self, img: np.ndarray) -> dict:
        h, w = img.shape[:2]
        m = max(h, w)
        if m > self.s.llm_image_max_side:
            sc = self.s.llm_image_max_side / m
            img = cv2.resize(img, (int(w * sc), int(h * sc)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise LlmError("encodage image impossible")
        return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(buf.tobytes()).decode()}}

    async def extract(self, images: list[np.ndarray], ocr_text: str, hint_type: str | None, partial: dict) -> dict:
        content: list[dict] = [self._img_block(i) for i in images]
        parts = []
        if hint_type and hint_type != "unknown":
            parts.append(f"Type probable (détection locale) : {hint_type}")
        if partial:
            parts.append("Champs déjà extraits localement (à vérifier/corriger, compléter les manquants) :\n" + json.dumps(partial, ensure_ascii=False))
        parts.append("Texte OCR :\n" + (ocr_text[:12000] or "(vide)"))
        content.append({"type": "text", "text": "\n\n".join(parts)})

        try:
            resp = await self.client.messages.create(
                model=self.s.llm_model,
                max_tokens=4096,
                system=SYSTEM,
                output_config={"effort": self.s.llm_effort, "format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": content}],
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
        data = json.loads(text)
        fields: dict = {}
        for item in data.get("fields", []):
            k, v = item["key"].strip(), item["value"].strip()
            if not k or not v:
                continue
            fields[k] = [x.strip() for x in v.split(",") if x.strip()] if k in LIST_KEYS else v
        return {
            "document_type": data.get("document_type", "other"),
            "country": data.get("country", ""),
            "fields": fields,
            "usage": {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens},
        }
