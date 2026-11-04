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
3. Base de données : PostgreSQL `kyc-db` dans le même projet Coolify, sur le réseau partagé `coolify` (voir `docker-compose.yml`). Le schéma est créé au démarrage de l'API.
4. Variables d'environnement : `DATABASE_URL`, `ADMIN_PASSWORD`, `ANTHROPIC_API_KEY` (+ options de `.env.example`). Redéployer après modification.
5. Ressources : 2 vCPU / 1,5 Go RAM conseillés. Augmenter `OCR_CONCURRENCY` avec les vCPU.

Local avec Docker : `docker compose -f docker-compose.yml -f docker-compose.local.yml up --build` → http://localhost:8080

## Administration

`https://kyc.priotti.fr/admin` (mot de passe : `ADMIN_PASSWORD`) : créer les entreprises clientes, leur générer ou révoquer des clés API sans redéploiement, désactiver un client et suivre ses appels et son coût IA par mois. Une clé n'est affichée qu'à sa création ; la base n'en garde qu'un hash. L'usage enregistré se limite aux métadonnées de l'appel (type de document, statut, durée, coût).

Tests avec base : définir `TEST_DATABASE_URL` vers un PostgreSQL jetable (les tables y sont supprimées), sinon ces tests sont ignorés.

## Notes

- **Aucun fichier stocké** : tout est traité en mémoire. Seules les découpes des lignes douteuses (ou les documents non compris) partent chez Anthropic ; mettre `LLM_MODE=off` pour un traitement 100 % local.
- L'extraction n'est pas une vérification KYC complète (détection de fraude, liveness, comparaison photo restent à faire).
- HEIC (iPhone) non supporté : convertir en JPEG côté client.
