"""Public API contract (published as OpenAPI at /api/openapi.json).

Changes stay backward compatible within a major version: fields may be added, never removed or retyped.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

API_VERSION = "1.1.0"

DocumentType = Literal[
    "id_card", "passport", "residence_permit", "driving_license", "vehicle_registration", "insurance_certificate",
    "business_card", "kbis", "invoice", "bank_statement", "expert_report", "repair_order", "insurance_claim",
    "proof_of_address", "other", "unknown",
]
Status = Literal["verified", "complete", "partial"]
ConfidenceLevel = Literal["verified", "high", "confirmed", "llm", "check", "unknown"]


class FieldConfidence(BaseModel):
    score: float | None = Field(
        description="Score de lecture du caractère le plus faible de la valeur (0-1). "
        "null si la valeur est reformatée/déduite ou lue par le LLM."
    )
    level: ConfidenceLevel = Field(
        description="verified = prouvé par checksum (MRZ, SIREN, SIRET, IBAN, totaux) ; "
        "high = tous les caractères ≥ 0,97 ; confirmed = caractère douteux mais le LLM a relu la ligne à l'identique ; "
        "llm = valeur corrigée/ajoutée par le LLM ; check = au moins un caractère < 0,97 ; "
        "unknown = valeur non retrouvée telle qu'imprimée (reformatée ou déduite). "
        "À faire vérifier par un humain : check et unknown."
    )


class Mrz(BaseModel):
    format: str = Field(description="TD1, TD2, TD3, FR_CNI (ancien modèle) ou EU_DL")
    lines: list[str]
    valid: bool = Field(description="Toutes les sommes de contrôle ICAO sont correctes")


class LlmUsage(BaseModel):
    input_tokens: int
    output_tokens: int


class Engine(BaseModel):
    input: str = Field(description="pdf, jpeg, png, webp, tiff, bmp")
    pages: int
    ocr_passes: list[str] = Field(description="native (texte PDF), tiny, small, small:names (relecture ciblée)")
    llm_used: bool
    llm_kind: Literal["lines", "document"] | None = Field(
        None, description="lines = relecture des seules lignes douteuses ; document = lecture du document complet"
    )
    llm_model: str | None = None
    llm_usage: LlmUsage | None = None
    llm_cost_usd: float | None = Field(None, description="Coût IA estimé de cet appel (tarif configuré, en USD)")
    classification_scores: dict[str, int] = Field(default_factory=dict)


class ExtractResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "document_type": "id_card", "document_label": "Carte nationale d'identité", "country": "FRA",
        "status": "verified",
        "fields": {"surname": "MARTIN", "given_names": "Élodie, Françoise", "birth_date": "1988-07-14",
                   "document_number": "X4RTBPFW4", "expiry_date": "2031-02-11", "birth_place": "LYON 3E (69)"},
        "field_sources": {"surname": "mrz+ocr", "birth_date": "mrz", "birth_place": "ocr"},
        "field_confidence": {"surname": {"score": 1.0, "level": "verified"},
                             "birth_place": {"score": 0.991, "level": "high"}},
        "checks": {"mrz_document_number": True, "mrz_birth_date": True},
        "missing_fields": [], "warnings": [], "entities": {},
        "engine": {"input": "jpeg", "pages": 1, "ocr_passes": ["tiny"], "llm_used": False},
        "timings_ms": {"ingest": 7, "ocr_fast": 430, "total": 440},
    }})

    api_version: str = API_VERSION
    document_type: DocumentType
    document_label: str = Field(description="Libellé français du type")
    country: str | None = Field(None, description="Pays émetteur ISO 3166 alpha-3 si connu")
    status: Status = Field(
        description="verified = champs obligatoires présents et checksums OK ; complete = champs obligatoires présents ; "
        "partial = il manque des champs obligatoires (voir missing_fields)"
    )
    fields: dict[str, Any] = Field(
        description="Valeurs extraites. Dates AAAA-MM-JJ, montants en nombre (euros), listes pour emails/phones/"
        "categories, legal_representatives = [{role, name}]. Les clés dépendent du type (voir la documentation)."
    )
    field_sources: dict[str, str] = Field(
        description="Origine de chaque valeur : mrz, mrz+ocr, ocr, pattern, heuristic, ocr+llm, llm"
    )
    field_confidence: dict[str, FieldConfidence] = Field(default_factory=dict)
    checks: dict[str, bool] = Field(description="Sommes de contrôle effectuées et leur résultat")
    missing_fields: list[str]
    warnings: list[str] = Field(description="Messages lisibles (corrections LLM, MRZ absente...) - ne pas parser")
    entities: dict[str, Any] = Field(default_factory=dict, description="Emails, téléphones, IBAN... trouvés dans le texte")
    mrz: Mrz | None = None
    engine: Engine
    timings_ms: dict[str, int]
    text: str | None = Field(None, description="Texte OCR brut (si include_text=true)")


class Health(BaseModel):
    status: Literal["ok", "starting"]
    api_version: str = API_VERSION
    auth: bool = Field(description="Une clé X-API-Key est exigée")
    llm: str = Field(description="off | fallback | always")
    llm_model: str | None


class ErrorResponse(BaseModel):
    detail: str
