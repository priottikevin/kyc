# KYC Extract

API d'extraction de données de documents (CNI, passeport, titre de séjour, permis, carte grise, attestation d'assurance, carte de visite, Kbis, facture, relevé bancaire, rapport d'expertise, ordre de réparation, courrier de sinistre) avec une petite interface de dépôt de fichier.

**Intégrer l'API dans un projet : [docs/API.md](docs/API.md)** (contrat OpenAPI : [docs/openapi.json](docs/openapi.json)).

**Principe : local d'abord, IA en dernier recours.**

```
fichier ─► ingest (PDF natif = texte direct, sinon image)
        ─► OCR rapide (PP-OCRv6 tiny, ~0,3-0,7 s CPU)
        ─► classification + règles par type (MRZ + checksums ICAO, codes carte grise, champs permis UE…)
        ─► si champ obligatoire manquant / checksum KO : OCR précis (PP-OCRv6 small, ~1,5 s)
        ─► score de confiance par champ (caractère le plus faible de la valeur)
        ─► valeurs douteuses (< 0,97) : Claude Haiku relit seulement les lignes concernées (~1 s)
        ─► si toujours incomplet ou doc inconnu : Claude Haiku lit le document complet
```

Les champs validés par checksum (MRZ, SIREN, IBAN, totaux) ne peuvent pas être écrasés par le LLM (divergence signalée dans `warnings`).

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

Référence complète, exemples Python / TypeScript / PHP, champs par type, confiance et coûts : **[docs/API.md](docs/API.md)**.

```bash
curl -H "X-API-Key: $KEY" -F file=@cni.jpg https://kyc.priotti.fr/api/extract
```

`POST /api/extract` · `GET /api/health` · `GET /api/document-types` · `GET /api/openapi.json` · `GET /api/docs`

Après une modification des modèles de réponse (`apps/api/app/schemas.py`), régénérer le contrat :

```powershell
cd apps/api
.venv\Scripts\python -c "import json; from app.main import app; json.dump(app.openapi(), open('../../docs/openapi.json','w',encoding='utf-8'), ensure_ascii=False, indent=2)"
```

## Déploiement Coolify

1. Nouvelle ressource → **Docker Compose** → ce dépôt, fichier `docker-compose.yml`.
2. Domaine : sur le service **web** uniquement (l'API reste privée sur le réseau compose).
3. Variables d'environnement : `API_KEYS` (une clé par projet client), `ANTHROPIC_API_KEY` (+ options de `.env.example`). Redéployer après modification.
4. Ressources : 2 vCPU / 1,5 Go RAM conseillés. Augmenter `OCR_CONCURRENCY` avec les vCPU.

Local avec Docker : `docker compose -f docker-compose.yml -f docker-compose.local.yml up --build` → http://localhost:8080

## Notes

- **Aucun fichier stocké** : tout est traité en mémoire. Seules les découpes des lignes douteuses (ou les documents non compris) partent chez Anthropic ; mettre `LLM_MODE=off` pour un traitement 100 % local.
- L'extraction n'est pas une vérification KYC complète (détection de fraude, liveness, comparaison photo restent à faire).
- HEIC (iPhone) non supporté : convertir en JPEG côté client.
