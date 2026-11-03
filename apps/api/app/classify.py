"""Fast keyword/pattern-based document classification on OCR text."""

import re

from .extractors.mrz import looks_like_mrz
from .textutils import EMAIL, PHONE

TYPES = {
    "id_card": "Carte nationale d'identité",
    "passport": "Passeport",
    "residence_permit": "Titre de séjour",
    "driving_license": "Permis de conduire",
    "vehicle_registration": "Certificat d'immatriculation (carte grise)",
    "insurance_certificate": "Attestation d'assurance",
    "business_card": "Carte de visite",
    "kbis": "Extrait Kbis",
    "invoice": "Facture",
    "bank_statement": "Relevé bancaire / RIB",
    "expert_report": "Rapport d'expertise",
    "repair_order": "Ordre de réparation",
    "insurance_claim": "Courrier de sinistre",
    "unknown": "Document non reconnu",
}

KEYWORDS: dict[str, list[tuple[str, int]]] = {
    "passport": [(r"PASSEPORT", 5), (r"PASSPORT", 5), (r"REISEPASS|PASAPORTE|PASSAPORTO", 5)],
    "id_card": [(r"CARTE NATIONALE D.IDENTITE", 6), (r"IDENTITY CARD", 4), (r"CARTE D.IDENTITE", 4), (r"PERSONALAUSWEIS|DOCUMENTO DE IDENTIDAD|CARTA D.IDENTITA", 5)],
    "residence_permit": [(r"TITRE DE SEJOUR", 7), (r"RESIDENCE PERMIT", 6), (r"CARTE DE RESIDENT", 6), (r"AUFENTHALTSTITEL|PERMESSO DI SOGGIORNO", 6)],
    "driving_license": [(r"PERMIS DE CONDUIRE", 7), (r"DRIVING LICEN[CS]E", 6), (r"FUHRERSCHEIN|PERMISO DE CONDUCCION|PATENTE DI GUIDA", 6), (r"(^|\n)\s*4\s?[AB][.,]?\s", 2), (r"(^|\n)\s*9[.,]\s", 1)],
    "vehicle_registration": [(r"CERTIFICAT D.IMMATRICULATION", 7), (r"CARTE GRISE", 6), (r"(^|\s)D\s?\.\s?1(\s|$)", 2), (r"(^|\s)C\s?\.\s?1(\s|$)", 2), (r"(^|\s)J\s?\.\s?1(\s|$)", 2), (r"(^|\s)P\s?\.\s?3(\s|$)", 2), (r"(^|\s)F\s?\.\s?1(\s|$)", 1)],
    "insurance_certificate": [(r"ATTESTATION D.ASSURANCE", 7), (r"CERTIFICAT INTERNATIONAL D.ASSURANCE", 7), (r"CARTE VERTE|GREEN CARD", 5), (r"MEMO VEHICULE ASSURE", 7), (r"PRESOMPTION D.ASSURANCE", 6), (r"(^|\n)MEMO\b", 3), (r"NUMERO DE SOCIETAIRE", 2),(r"ASSUREUR|INSURER", 3), (r"SOUSCRIPTEUR|POLICYHOLDER", 2), (r"N[°O]? ?(DE )?CONTRAT|POLICE N", 2), (r"ASSURANCE", 2), (r"RESPONSABILITE CIVILE", 2)],
    # Business documents: a strong header keyword each (they all mention "assurance", "facture", "sinistre"...)
    "kbis": [(r"EXTRAIT KBIS", 10), (r"REGISTRE DU COMMERCE ET DES SOCIETES", 6), (r"GREFFE DU TRIBUNAL", 3), (r"FORME JURIDIQUE", 2)],
    "invoice": [(r"(^|\n|\s)FACTURE\s*(N[°O]?|NUMERO|NO\.?|#)\s*[:.]?\s*\w", 8), (r"TOTAL\s*T\.?\s?T\.?\s?C", 3), (r"TOTAL\s*H\.?\s?T\b", 2), (r"NET A PAYER", 3)],
    "bank_statement": [(r"RELEVE (MENSUEL|DE COMPTE|D'IDENTITE BANCAIRE)", 9), (r"(NOUVEAU|ANCIEN) SOLDE", 4), (r"SOLDE (CREDITEUR|DEBITEUR)", 3), (r"\bDEBIT\b.*\bCREDIT\b", 2)],
    "expert_report": [(r"RAPPORT D.EXPERTISE", 12), (r"\bEXPERT\b", 1), (r"V\.?R\.?A\.?D\.?E", 3)],
    "repair_order": [(r"(^|\n)\s*ORDRE DE REPARATION\s*($|\n)", 9), (r"N[°O]\s*O\.?R\.?\b", 3), (r"BON POUR ACCORD", 2)],
    "insurance_claim": [(r"VOTRE SINISTRE", 5), (r"SINISTRE DU\s+\d", 4), (r"DATE DU SINISTRE", 3), (r"DECLARATION D[EU] SINISTRE", 5), (r"OBJET\s*:.*(SINISTRE|DOMMAGE)", 4)],
}


def classify(ntext: str, rows: list[str]) -> tuple[str, dict[str, int]]:
    scores = {k: 0 for k in KEYWORDS}
    for t, pats in KEYWORDS.items():
        for p, w in pats:
            if re.search(p, ntext):
                scores[t] += w

    mrz = [r for r in rows if looks_like_mrz(r)]
    if mrz:
        first = re.sub(r"\s", "", mrz[0].upper())
        if first.startswith("P"):
            scores["passport"] += 8
        elif first[:2] in ("IR", "AR", "RT", "RP"):
            scores["residence_permit"] += 6
        elif first[:1] in ("I", "A", "C"):
            scores["id_card"] += 6
        if first.startswith("D1") or first.startswith("D<"):
            scores["driving_license"] += 6

    best = max(scores, key=scores.get)
    if scores[best] >= 4:
        return best, scores
    # Business card: few lines with contact details
    if len(rows) <= 25 and (EMAIL.search(ntext.lower()) or PHONE.search(ntext)):
        return "business_card", scores
    return "unknown", scores
