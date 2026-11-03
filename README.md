# KYC Extract

API d'extraction de données de documents (CNI, passeport, titre de séjour, permis, carte grise, attestation d'assurance, carte de visite…) avec une petite interface de dépôt de fichier.

**Principe : local d'abord, IA en dernier recours.**

```
fichier ─► ingest (PDF natif = texte direct, sinon image)
        ─► OCR rapide (PP-OCRv6 tiny, ~0,3-0,7 s CPU)
        ─► classification + règles par type (MRZ + checksums ICAO, codes carte grise, champs permis UE…)
        ─► si champ obligatoire manquant / checksum KO : OCR précis (PP-OCRv6 small, ~1,5 s)
        ─► si toujours incomplet, doc inconnu ou carte de visite : Claude Haiku (image + texte OCR, JSON structuré)
```

Les champs validés par checksum MRZ ne peuvent pas être écrasés par le LLM (divergence signalée dans `warnings`).

## Structure (monorepo)

```
apps/
  api/   FastAPI + RapidOCR (onnxruntime, CPU) + PyMuPDF + Anthropic SDK
  web/   interface statique (HTML/CSS/JS, sans build) servie par nginx, proxy /api -> api
docker-compose.yml        déploiement (Coolify)
docker-compose.local.yml  exposition locale sur :8080
```

## Dev local

```powershell
cd apps/api
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
copy ..\..\.env.example ..\..\.env   # renseigner ANTHROPIC_API_KEY si besoin
.venv\Scripts\uvicorn app.main:app --reload --port 8000
```

Avec `STATIC_DIR=../web` (déjà dans `.env.example`), l'interface est servie sur http://localhost:8000 et la doc OpenAPI sur http://localhost:8000/api/docs. Les modèles OCR (~30 Mo) sont téléchargés au premier démarrage.

Tests : `.venv\Scripts\python -m pytest` (documents synthétiques générés à la volée, LLM simulé).

## API

`POST /api/extract` (multipart)

| champ | description |
|---|---|
| `file` | PDF ou image (JPEG, PNG, WEBP, TIFF, BMP) - max `MAX_UPLOAD_MB` |
| `document_type` | optionnel : forcer le type (`id_card`, `passport`, `residence_permit`, `driving_license`, `vehicle_registration`, `insurance_certificate`, `business_card`) |
| `llm` | optionnel : `off`, `fallback`, `always` |
| `include_text` | optionnel : renvoyer le texte OCR brut |

Header `X-API-Key` si `API_KEYS` est défini.

```bash
curl -H "X-API-Key: $KEY" -F file=@cni.jpg https://kyc.example.com/api/extract
```

Réponse (extrait) :

```json
{
  "document_type": "id_card",
  "status": "verified",
  "fields": { "surname": "MARTIN-DUPRÉ", "given_names": "Élodie, Françoise", "birth_date": "1988-07-14",
              "document_number": "X4RTBPFW4", "expiry_date": "2031-02-11", "birth_place": "LYON 3E (69)" },
  "field_sources": { "surname": "mrz+ocr", "birth_date": "mrz", "birth_place": "ocr" },
  "checks": { "mrz_document_number": true, "mrz_birth_date": true, "mrz_expiry_date": true, "mrz_composite": true },
  "mrz": { "format": "TD1", "valid": true, "lines": ["…"] },
  "missing_fields": [], "warnings": [],
  "engine": { "ocr_passes": ["tiny"], "llm_used": false },
  "timings_ms": { "ingest": 7, "ocr_fast": 496, "total": 503 }
}
```

`status` : `verified` (complet + checksums OK), `complete` (champs obligatoires présents), `partial`.
`field_sources` : `mrz`, `mrz+ocr` (MRZ confirmée par la zone visuelle, orthographe accentuée), `ocr`, `pattern`, `heuristic`, `llm`.

`GET /api/health` · `GET /api/document-types`

## Déploiement Coolify

1. Nouvelle ressource → **Docker Compose** → ce dépôt, fichier `docker-compose.yml`.
2. Domaine : sur le service **web** uniquement (l'API reste privée sur le réseau compose).
3. Variables d'environnement : `API_KEYS`, `ANTHROPIC_API_KEY` (+ options de `.env.example`).
4. Ressources : 2 vCPU / 1,5 Go RAM conseillés. Augmenter `OCR_CONCURRENCY` avec les vCPU.

Local avec Docker : `docker compose -f docker-compose.yml -f docker-compose.local.yml up --build` → http://localhost:8080

## Notes

- **Aucun fichier stocké** : tout est traité en mémoire. Seuls les cas envoyés au LLM quittent le serveur ; mettre `LLM_MODE=off` pour un traitement 100 % local.
- L'extraction n'est pas une vérification KYC complète (détection de fraude, liveness, comparaison photo restent à faire).
- HEIC (iPhone) non supporté : convertir en JPEG côté client.
