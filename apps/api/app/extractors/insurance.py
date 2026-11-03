"""Insurance certificates (attestation d'assurance, carte verte / certificat international, mémo véhicule)."""

import re

from ..textutils import DATE_NUM, find_plate, parse_date
from .base import Ctx, Extraction

INSURERS = [
    "AXA", "ALLIANZ", "MAIF", "MACIF", "MATMUT", "MAAF", "GMF", "MMA", "GROUPAMA", "GENERALI", "AVIVA", "ABEILLE",
    "PACIFICA", "CREDIT AGRICOLE ASSURANCES", "SOGESSUR", "BPCE ASSURANCES", "SURAVENIR", "SWISS LIFE", "THELEM",
    "AREAS", "LA BANQUE POSTALE ASSURANCES", "DIRECT ASSURANCE", "AMV", "OLIVIER ASSURANCE", "LEOCARE", "LOVYS",
    "ACHEEL", "L'OLIVIER", "HISCOX", "ZURICH", "CHUBB", "LA MUTUELLE GENERALE", "MUTUELLE DE POITIERS", "AESIO",
    "HARMONIE MUTUELLE", "MGEN", "MALAKOFF HUMANIS", "APRIL", "ALLIANZ IARD", "MACSF", "LEOCARE", "ASSU 2000",
    "EUROFIL", "AMAGUIZ", "IDMACIF", "GAN", "CNP", "AG2R", "SMABTP", "WAKAM", "ORNIKAR",
]


def extract(ctx: Ctx, doc_type: str) -> Extraction:
    ex = Extraction("insurance_certificate", required=["insurer", "policy_number", "holder_name", "valid_from", "valid_to"])
    v = ctx.value_after
    t = ctx.ntext

    for name in sorted(INSURERS, key=len, reverse=True):
        if re.search(r"\b" + re.escape(name) + r"\b", t):
            ex.set("insurer", name)
            break
    ex.set("insurer", v(r"\bASSUREUR\b|COMPAGNIE|SOCIETE D'ASSURANCE|NAME OF INSURER"))
    ex.set("policy_number", v(r"N[°O]? ?(DE )?(CONTRAT|POLICE|SOCIETAIRE|ADHERENT)|CONTRAT N|POLICY N|N[°O] D'ASSURE", r"[A-Z0-9][A-Z0-9 /\-]{4,}"))
    ex.set("holder_name", v(r"\bASSURE\b|SOUSCRIPTEUR|TITULAIRE|POLICYHOLDER|NOM DE L'ASSURE|INSURED", exclude=r"^(ADRESSE|N[°O]|DATE)"))
    ex.set("holder_address", v(r"ADRESSE", exclude=r"^(N[°O]|DATE|TEL)"))

    # Validity period: "du 01/01/2025 au 31/12/2025", or labelled dates
    m = re.search(r"\bDU\s+(\d{1,2}[./\- ]\d{1,2}[./\- ]\d{2,4})\s*(?:\S+\s+)?AU\s+(\d{1,2}[./\- ]\d{1,2}[./\- ]\d{2,4})", t)
    if not m:
        m = re.search(r"VALID\w*\s+FROM\s+(\S+)\s+TO\s+(\S+)", t)
    if m:
        ex.set("valid_from", parse_date(m.group(1)))
        ex.set("valid_to", parse_date(m.group(2)))
    ex.set("valid_from", parse_date(v(r"DATE D'EFFET|PRISE D'EFFET|VALABLE DU|DEBUT|EFFECTIVE DATE") or ""))
    ex.set("valid_to", parse_date(v(r"DATE D'ECHEANCE|ECHEANCE|VALABLE JUSQU|EXPIRE|AU\b|FIN DE VALIDITE") or ""))
    if "valid_from" not in ex.fields or "valid_to" not in ex.fields:
        # A single row holding exactly two dates is very likely the validity period
        for row in ctx.row_texts:
            ds = [parse_date(x.group(0)) for x in DATE_NUM.finditer(row)]
            ds = [d for d in ds if d]
            if len(ds) == 2 and ds[0] < ds[1]:
                ex.set("valid_from", ds[0], "pattern")
                ex.set("valid_to", ds[1], "pattern")
                break

    ex.set("plate", v(r"IMMATRICULATION|IMMAT|REGISTRATION", r"[A-Z]{2}[\s\-]?\d{3}[\s\-]?[A-Z]{2}") or find_plate(ctx.text))
    if "plate" in ex.fields:
        ex.fields["plate"] = find_plate(ex.fields["plate"]) or ex.fields["plate"]
    ex.set("vehicle_make", v(r"MARQUE|MAKE OF VEHICLE|\bMAKE\b", exclude=r"^(MODELE|TYPE|IMMAT)"))
    ex.set("vehicle_model", v(r"MODELE|\bMODEL\b", exclude=r"^(IMMAT|N[°O])"))
    ex.set("vehicle_category", v(r"CATEGORIE|CATEGORY"))
    ex.set("insurer_code", v(r"CODE (ASSUREUR|SOCIETE)|INSURER CODE|CODE FVA", r"\b\d{3,6}\b"))
    return ex
