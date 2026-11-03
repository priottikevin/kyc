"""ID card / passport / residence permit: MRZ (checksummed) + visual zone for accents & extra fields."""

import re

from ..textutils import DATE_VALUE, norm, parse_date, strip_accents
from .base import Ctx, Extraction
from .mrz import find_mrz

# "Surname" is often misread ("Sumame", "Surmame")
LABELS = r"(NOM|SURNAME|SU[RNM]{1,3}AME|PRENOM|GIVEN|SEXE|SEX|NATIONALIT|DATE|LIEU|PLACE|N[°O0] |DOCUMENT|EXPIR|AUTORIT|SIGNATURE|TAILLE|HEIGHT|ADRESSE|ADDRESS|DELIVR|ISSU|TYPE|CODE|PASSEPORT|PASSPORT|CARTE|REPUBLIQUE|NOM D'USAGE)"

REQUIRED = ["surname", "given_names", "birth_date", "document_number"]


def _mrz_key(s: str) -> str:
    return re.sub(r"[^A-Z]", "", norm(s))


def extract(ctx: Ctx, doc_type: str) -> Extraction:
    ex = Extraction(doc_type, required=list(REQUIRED))
    mrz = find_mrz(ctx.row_texts)

    if mrz:
        for k, v in mrz.fields.items():
            ex.set(k, v, "mrz")
        ex.checks.update({f"mrz_{k}": v for k, v in mrz.checks.items()})
        ex.extra["mrz"] = {"format": mrz.format, "lines": mrz.lines, "valid": mrz.valid}
        code = mrz.fields.get("document_code", "")
        if code.startswith("P"):
            ex.document_type = "passport"
        elif code[:2] in ("IR", "AR", "RT", "RP") or "TITRE DE SEJOUR" in ctx.ntext or "RESIDENCE PERMIT" in ctx.ntext:
            ex.document_type = "residence_permit"
        elif code.startswith(("I", "A", "C")) and doc_type not in ("passport", "residence_permit"):
            ex.document_type = "id_card"
        if mrz.format != "FR_CNI":
            ex.required.append("expiry_date")
    else:
        ex.warnings.append("MRZ non détectée")
        ex.required.append("expiry_date")

    _viz(ctx, ex)
    return ex


def _viz(ctx: Ctx, ex: Extraction) -> None:
    """Visual inspection zone. Prefer VIZ spelling (accents, hyphens) when it matches the MRZ."""
    v = ctx.value_after
    # "NOM / Surname" may come out glued or garbled: "NOMI Sumame", "NOMISumame"
    surname = v(r"\bNOM\b(?! D'USAGE)|^NOM(?!S\b)(?! D'USAGE)|SURNAME|SU[RNM]{1,3}AME",
                r"[A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ' \-]+", exclude=LABELS)
    usage = v(r"NOM D'USAGE|ALTERNATIVE NAME|EPOUSE|EP\.", r"[A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ' \-]+", exclude=LABELS)
    given = v(r"PRENOM|GIVEN NAME", r"[A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ' ,\-]+", exclude=LABELS)
    birth_place = v(r"LIEU ?DE NAISS|PLACE OF BIRTH", exclude=r"^(DATE|SEXE|SEX|NATIONAL|\d)")
    birth = v(r"DATE DE NAISS|DATE OF BIRTH|NE\(E\) LE|NE LE", DATE_VALUE)
    expiry = v(r"EXPIR|VALABLE JUSQU|DATE OF EXPIRY|VALID UNTIL", DATE_VALUE)
    issue = v(r"DELIVR|DATE OF ISSUE|EMIS LE", DATE_VALUE)
    docnum = v(r"N[°O0]? ?DU DOCUMENT|DOCUMENT N|PASSPORT N|CARD N|N[°O] ?DE (CARTE|TITRE)", r"\b[A-Z0-9]{8,12}\b")
    address = v(r"ADRESSE|ADDRESS|DOMICILE", exclude=LABELS)
    authority = v(r"AUTORITE|AUTHORITY|DELIVRE PAR|PREFECTURE", exclude=r"^(DATE|SIGNATURE)")

    mrz_sn = ex.fields.get("surname") if ex.field_sources.get("surname") == "mrz" else None
    if surname:
        if mrz_sn and _mrz_key(surname) == _mrz_key(mrz_sn):
            ex.set("surname", surname.upper() if surname.isupper() else surname, "mrz+ocr", overwrite=True)
        elif not mrz_sn:
            ex.set("surname", surname)
    mrz_gn = ex.fields.get("given_names") if ex.field_sources.get("given_names") == "mrz" else None
    if given:
        g = re.sub(r"\s*,\s*", ", ", given.strip(" ,"))
        if mrz_gn and _mrz_key(g) == _mrz_key(mrz_gn):
            ex.set("given_names", g, "mrz+ocr", overwrite=True)
        elif mrz_gn and _mrz_key(g).startswith(_mrz_key(mrz_gn)):
            # MRZ truncates long names
            ex.set("given_names", g, "mrz+ocr", overwrite=True)
        elif not mrz_gn:
            ex.set("given_names", g)
    ex.set("usage_name", usage)
    ex.set("birth_place", birth_place)
    if birth and (d := parse_date(birth)):
        if "birth_date" in ex.fields and ex.fields["birth_date"] != d:
            ex.warnings.append(f"Date de naissance VIZ ({d}) différente de la MRZ")
        ex.set("birth_date", d)
    if expiry and (d := parse_date(expiry)):
        ex.set("expiry_date", d)
    if issue and (d := parse_date(issue)):
        ex.set("issue_date", d)
    if docnum:
        ex.set("document_number", docnum)
    ex.set("address", address)
    ex.set("issuing_authority", authority)
    if "sex" not in ex.fields:
        s = v(r"\bSEXE\b|\bSEX\b", r"\b[MF]\b")
        ex.set("sex", s)
    if "nationality" not in ex.fields:
        nat = v(r"NATIONALIT", r"\b[A-Z]{3}\b|FRANCAISE|FRANÇAISE")
        if nat:
            ex.set("nationality", "FRA" if strip_accents(nat).upper().startswith("FRANCAISE") else nat)
