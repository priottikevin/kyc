# KYC Extract — guide d'intégration

Version du contrat : **1.3.0** · Contrat machine : [`openapi.json`](openapi.json) (servi aussi sur `/api/openapi.json`) · Doc interactive : `/api/docs`

Production : `https://kyc.priotti.fr`

## En bref

```bash
curl -H "X-API-Key: $KYC_API_KEY" -F file=@document.pdf https://kyc.priotti.fr/api/extract
```

Un fichier (PDF ou image) en entrée, un JSON en sortie : type de document, champs extraits, et pour chaque champ son origine et son niveau de confiance. Aucun fichier n'est conservé.

## Authentification

Header `X-API-Key: <clé>` sur `POST /api/extract`. Les clés se créent et se révoquent dans l'administration (`/admin`), une ou plusieurs par client. Celles de `API_KEYS` (séparées par des virgules) restent acceptées, sans suivi d'usage. Une erreur `503` signifie que la clé n'a pas pu être vérifiée : réessayer. `GET /api/health` et `GET /api/document-types` sont publics.

Appeler l'API **depuis un serveur**, pas depuis un navigateur : la clé serait visible dans le code client.

## Endpoints

### `POST /api/extract` — multipart/form-data

| Champ | Requis | Description |
|---|---|---|
| `file` | oui | PDF (2 premières pages lues), JPEG, PNG, WEBP, TIFF, BMP. Max 15 Mo. HEIC refusé : convertir en JPEG. |
| `document_type` | non | Forcer le type (clé de `/api/document-types`). Sinon détection automatique. |
| `llm` | non | `off` : 100 % local, rien ne quitte le serveur. `fallback` (défaut) : Claude seulement si nécessaire. `always` : Claude relit tout le document. |
| `include_text` | non | `true` pour recevoir le texte OCR brut dans `text`. |
| `include_positions` | non | `true` pour recevoir l'emplacement de chaque valeur dans `field_positions` (surligner les valeurs dans une visionneuse). |

### `GET /api/document-types`

```json
{ "id_card": "Carte nationale d'identité", "passport": "Passeport", "...": "..." }
```

### `GET /api/health`

```json
{ "status": "ok", "api_version": "1.3.0", "auth": true, "llm": "fallback", "llm_model": "claude-haiku-5-5" }
```

## Réponse

```json
{
  "api_version": "1.3.0",
  "document_type": "id_card",
  "document_label": "Carte nationale d'identité",
  "country": "FRA",
  "status": "verified",
  "fields": {
    "surname": "MARTIN",
    "given_names": "Élodie, Françoise",
    "birth_date": "1988-07-14",
    "document_number": "X4RTBPFW4",
    "birth_place": "LYON 3E (69)"
  },
  "field_sources": { "surname": "mrz+ocr", "birth_date": "mrz", "birth_place": "ocr" },
  "field_confidence": {
    "surname": { "score": 1.0, "level": "verified" },
    "birth_place": { "score": 0.991, "level": "high" }
  },
  "checks": { "mrz_document_number": true, "mrz_birth_date": true, "mrz_composite": true },
  "missing_fields": [],
  "warnings": [],
  "entities": {},
  "mrz": { "format": "TD1", "valid": true, "lines": ["…"] },
  "engine": {
    "input": "jpeg", "pages": 1, "ocr_passes": ["tiny"],
    "llm_used": false, "llm_kind": null, "llm_model": null, "llm_usage": null, "llm_cost_usd": null
  },
  "timings_ms": { "ingest": 7, "ocr_fast": 430, "total": 440 },
  "page_rotations": [0],
  "field_positions": {
    "surname": { "page": 1, "x": 408, "y": 241, "width": 138, "height": 68 }
  }
}
```

### `page_rotations` et `field_positions` — situer les valeurs

`page_rotations` (toujours présent) : pour chaque page lue, l'angle (0, 90, 180 ou 270) dont il faut la tourner **dans le sens horaire** pour la lire à l'endroit (photo prise de côté, scan à l'envers).

`field_positions` (avec `include_positions=true`) : pour chaque valeur retrouvée telle qu'imprimée, le rectangle qui l'entoure :

- `page` : 1 pour la première ;
- `x`, `y` (coin en haut à gauche), `width`, `height` : en **millièmes** de la largeur et de la hauteur de la page ;
- repère : la page **telle qu'envoyée**, avant toute rotation (image avec son orientation EXIF appliquée, page PDF telle qu'affichée). Pour l'afficher sur la page redressée, appliquer au rectangle la même rotation que `page_rotations`.

Absent pour une valeur reformatée ou déduite (ex. `fuel_label`), une valeur de moins de 3 caractères (sexe, code), ou une valeur lue par Claude qui ne figure pas telle quelle dans le texte OCR. Une valeur sur plusieurs lignes (adresse) reçoit le rectangle qui englobe ses lignes.

### `status`

| Valeur | Signification |
|---|---|
| `verified` | Champs obligatoires présents **et** sommes de contrôle correctes (MRZ, SIREN, SIRET, IBAN, HT + TVA = TTC). |
| `complete` | Champs obligatoires présents, pas de somme de contrôle disponible sur ce document. |
| `partial` | Il manque des champs obligatoires : voir `missing_fields`. |

### `field_confidence` — fiabilité de chaque valeur

L'OCR note chaque caractère lu. Le `score` d'un champ est celui de **son caractère le plus faible** : un seul caractère douteux rend la valeur douteuse.

| `level` | Signification | À faire |
|---|---|---|
| `verified` | Prouvé par une somme de contrôle | rien |
| `high` | Tous les caractères lus à ≥ 0,97 | rien |
| `confirmed` | Un caractère était douteux, Claude a relu la ligne et lit la même chose | rien |
| `llm` | Valeur corrigée ou ajoutée par Claude (l'ancienne valeur est dans `warnings`) | rien, ou contrôle si critique |
| `check` | Au moins un caractère < 0,97 et pas de confirmation | **vérification humaine** |
| `unknown` | Valeur reformatée ou déduite (ex. `fuel_label` déduit du code), pas de score direct | **vérification humaine** si critique |

Le score n'est pas une probabilité d'exactitude : un caractère juste peut avoir un score bas. Mesuré sur nos documents de test : aucune erreur détectée sur les lignes dont tous les caractères sont ≥ 0,97 (0 sur 379), 15 à 80 % de lectures divergentes en dessous.

### Règle de décision recommandée

```python
def decision(r: dict) -> str:
    doubtful = [k for k, c in r["field_confidence"].items() if c["level"] in ("check", "unknown")]
    if r["status"] == "verified" and not doubtful:
        return "accept"
    if r["status"] == "complete" and not doubtful:
        return "accept"
    return "review"   # afficher missing_fields + doubtful à un opérateur
```

Pour un contrôle strict, ne considérer que les champs utiles à votre métier (ex. ignorer un `unknown` sur `fuel_label`).

### Formats des valeurs

- Dates : `AAAA-MM-JJ` (`issue_month` : `AAAA-MM`).
- Montants : nombres en euros (`1593.96`).
- Listes : `emails`, `phones`, `websites`, `siren_siret`, `categories`.
- `given_names` : prénoms séparés par `", "`. Adresses sur plusieurs lignes : lignes séparées par `", "`.
- `legal_representatives` (Kbis) : `[{"role": "Gérant", "name": "…"}]`.
- Pays et nationalités : ISO 3166 alpha-3 (`FRA`).
- `sex` : `M`, `F` ou `X`.

`field_sources` : `mrz` (zone MRZ, checksums), `mrz+ocr` (MRZ confirmée par la zone visuelle, accents conservés), `ocr`, `pattern`, `heuristic`, `ocr+llm` (OCR et Claude d'accord), `llm`.

`entities` : emails, téléphones, sites web, IBAN valides… trouvés n'importe où dans le texte (clés présentes seulement si non vides).

`warnings` sont des messages lisibles destinés à un humain : ne pas les parser.

## Champs par type

Les champs **en gras** sont obligatoires (`missing_fields` les liste s'ils manquent). Les autres sont renvoyés s'ils sont lus. Un document lu par Claude (`llm_kind: "document"`) peut contenir des clés supplémentaires en `snake_case`.

| Type | Champs |
|---|---|
| `id_card`, `passport`, `residence_permit` | **surname**, **given_names**, **birth_date**, **document_number**, expiry_date (obligatoire si présent sur le modèle), usage_name, sex, nationality, birth_place, issue_date, issuing_authority, address, height, document_code, issuing_country, optional_data, issue_month |
| `driving_license` | **surname**, **given_names**, **birth_date**, **license_number**, birth_place, issue_date, expiry_date, issuing_authority, address, categories |
| `vehicle_registration` | **plate**, **vin**, **holder_name**, **make**, **first_registration_date**, holder_address, holder_status, co_owner, type_variant, national_type_code, commercial_name, max_mass_kg, max_mass_in_service_kg, max_mass_combination_kg, mass_in_service_kg, empty_mass_kg, validity, certificate_date, category_eu, national_category, body_eu, body_national, type_approval, engine_capacity_cm3, max_power_kw, fuel, fuel_label, fiscal_power_cv, power_weight_ratio, seats, standing_places, stationary_noise_db, engine_speed_rpm, co2_g_km, environmental_class, technical_inspection_date, tax_regional, tax_total, mentions |
| `insurance_certificate` | **insurer**, **policy_number**, **holder_name**, **valid_from**, **valid_to** (non exigé sur un « mémo véhicule assuré »), holder_address, plate, vehicle_make, vehicle_model, vehicle_category, insurer_code |
| `business_card` | **full_name**, job_title, company, emails, phones, websites, address, siren_siret |
| `kbis` | **company_name**, **siren**, **legal_form**, **head_office_address**, rcs_city, registration_date, share_capital, euid, activity, activity_start_date, management_number, extract_date, legal_representatives |
| `invoice` | **invoice_number**, **invoice_date**, **total_incl_tax**, due_date, supplier_name, supplier_siret, vat_number, total_excl_tax, total_vat, iban, plate, vin |
| `bank_statement` | **iban**, bic, bank_name, statement_period, statement_date, account_holder, closing_balance |
| `expert_report` | **report_number**, **claim_number**, report_date, claim_date, policy_number, insurer, expert_name, conclusion, total_excl_tax, total_vat, total_incl_tax, repairer_invoice_number, plate, vin |
| `repair_order` | **order_number**, **plate**, opened_date, work_type, vin, vehicle_make, vehicle_model, customer_name, customer_address, customer_phone, customer_mobile, customer_email, insurer, work_description |
| `insurance_claim` | **claim_number**, **claim_date**, contract_number, insurer, subject, insurer_reference, liability, plate |
| `unknown` | Document non reconnu : avec `llm=fallback`, Claude le lit (`document_type` peut alors valoir `other` ou `proof_of_address`). |

## Erreurs

| Code | Cas |
|---|---|
| 400 | Fichier vide |
| 401 | Clé API absente ou invalide |
| 413 | Fichier > 15 Mo |
| 415 | Format non supporté : HEIC, fichier qui n'est ni une image ni un PDF, image ou PDF illisible, PDF vide |
| 422 | Paramètre invalide (ex. `llm=maybe`) |
| 5xx | Erreur serveur : réessayer une fois |

Corps : `{"detail": "message"}`. Une indisponibilité de Claude **n'est pas** une erreur HTTP : la réponse locale est renvoyée avec un message dans `warnings`.

## Exemples

### Python (httpx)

```python
import os, httpx

def extract(path: str) -> dict:
    with open(path, "rb") as f:
        r = httpx.post(
            "https://kyc.priotti.fr/api/extract",
            headers={"X-API-Key": os.environ["KYC_API_KEY"]},
            files={"file": (os.path.basename(path), f)},
            timeout=30,
        )
    r.raise_for_status()
    return r.json()

r = extract("cni.jpg")
print(r["document_type"], r["status"], r["fields"])
```

### TypeScript / Node 18+

```ts
import { readFile } from "node:fs/promises";

export async function extract(path: string, name: string) {
  const form = new FormData();
  form.append("file", new Blob([await readFile(path)]), name);
  const res = await fetch("https://kyc.priotti.fr/api/extract", {
    method: "POST",
    headers: { "X-API-Key": process.env.KYC_API_KEY! },
    body: form,
    signal: AbortSignal.timeout(30_000),
  });
  if (!res.ok) throw new Error(`KYC ${res.status}: ${(await res.json()).detail}`);
  return res.json();
}
```

Types TypeScript générés depuis le contrat :

```bash
npx openapi-typescript https://kyc.priotti.fr/api/openapi.json -o src/kyc-api.d.ts
```

### PHP

```php
$ch = curl_init("https://kyc.priotti.fr/api/extract");
curl_setopt_array($ch, [
    CURLOPT_POST => true,
    CURLOPT_HTTPHEADER => ["X-API-Key: " . getenv("KYC_API_KEY")],
    CURLOPT_POSTFIELDS => ["file" => new CURLFile("cni.jpg")],
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_TIMEOUT => 30,
]);
$r = json_decode(curl_exec($ch), true);
```

Client complet dans n'importe quel langage : `openapi-generator-cli generate -i https://kyc.priotti.fr/api/openapi.json -g <python|typescript-fetch|php|java|csharp…>`.

## Performances et limites

| Cas | Temps serveur mesuré |
|---|---|
| PDF avec texte natif (Kbis) | < 0,1 s |
| CNI (photo ou PDF), carte grise | 0,3 – 0,8 s sans IA |
| A4 dense d'1 à 2 pages (facture, rapport) | 1 – 2,5 s sans IA |
| Relecture IA des lignes douteuses | + 0,8 – 1,5 s |
| Lecture IA du document complet | + 1 – 2,5 s |

- Prévoir un timeout client de 30 s.
- Traitement en parallèle limité par `OCR_CONCURRENCY` côté serveur : les requêtes supplémentaires attendent leur tour.
- Seules les 2 premières pages d'un PDF sont lues.

## Coût IA

Claude Haiku 5.5 : 0,10 $ par million de jetons en entrée (images comprises), 0,50 $ en sortie. Chaque réponse indique le coût réel de son appel dans `engine.llm_cost_usd`. Mesures sur nos documents de test :

| Cas | Jetons entrée / sortie par document | Coût par document |
|---|---|---|
| Pas d'IA (checksums OK, tout ≥ 0,97) | 0 | 0 € |
| Relecture des lignes douteuses | 230 – 770 / 14 – 230 | 0,00003 – 0,00017 € |
| Lecture du document complet | 1 600 – 4 200 / 120 – 700 | 0,0002 – 0,0007 € |

## Données personnelles

- Rien n'est stocké : fichier et résultats restent en mémoire le temps de la requête.
- Avec `llm=fallback`, seules des **découpes des lignes douteuses** (ou le document entier s'il n'est pas compris) partent chez Anthropic. `llm=off` garantit un traitement 100 % local.
- L'extraction n'est pas une vérification KYC complète : pas de détection de fraude, de liveness ni de comparaison de photo.

## Versions

`api_version` (réponses et `/api/health`) suit le versionnage sémantique. En 1.x, des champs peuvent être **ajoutés** ; aucun n'est supprimé ni ne change de type. Ignorez les clés inconnues.

| Version | Changements |
|---|---|
| 1.2.0 | `page_rotations` ; `field_positions` sur demande (`include_positions`) |
| 1.3.0 | `repair_order` : adresse, téléphone, portable et mail du client |
| 1.1.0 | `field_confidence`, `api_version`, `engine.llm_kind`, `engine.llm_cost_usd` ; 6 nouveaux types (kbis, invoice, bank_statement, expert_report, repair_order, insurance_claim) ; Claude ne relit que les lignes douteuses |
| 1.0.0 | Version initiale |
