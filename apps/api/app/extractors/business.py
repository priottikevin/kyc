"""Business and claim documents: Kbis, invoices, bank statements, expert reports, repair orders, claim letters.

Dense A4 pages with a "Label value" or "Label : value" layout: patterns on the joined text work better than
box geometry. Each extractor keeps a short list of required fields; the LLM (when configured) fills the rest.
"""

import re

from ..textutils import (
    AMOUNT, BIC, DATE_VALUE, EMAIL, VIN, digits_luhn, find_ibans, find_plate, format_phone, iban_ok, last_amount, norm, parse_amount,
    parse_date,
)
from .base import Ctx, Extraction
from .insurance import INSURERS

LEGAL_FORMS = r"\b(SARL|SAS|SASU|SA|EURL|SNC|SCI|SCM|SELARL|SCOP|EI|EIRL|GIE|ASSOCIATION)\b"
ROLES = r"^(GERANT|CO-?GERANT|PRESIDENT|DIRECTEUR GENERAL(?: DELEGUE)?|ADMINISTRATEUR|COMMISSAIRE AUX COMPTES[\w ]*|ASSOCIE[\w ]*)$"


def _search(pat: str, text: str, group: int = 1, flags: int = 0) -> str | None:
    m = re.search(pat, text, flags)
    return m.group(group).strip() if m else None


def _row_with(ctx: Ctx, pat: str) -> str | None:
    """Original-case row whose normalised text matches pat."""
    for r in ctx.row_texts:
        if re.search(pat, norm(r)):
            return r
    return None


def _insurer(ntext: str) -> str | None:
    for name in sorted(INSURERS, key=len, reverse=True):
        if re.search(r"\b" + re.escape(name) + r"\b", ntext):
            return name
    return None


def _vehicle(ctx: Ctx, ex: Extraction) -> None:
    ex.set("plate", find_plate(ctx.text), "pattern")
    m = VIN.search(norm(ctx.text))
    if m:
        ex.set("vin", m.group(0), "pattern")


def _amounts(ex: Extraction, ht: float | None, tva: float | None, ttc: float | None) -> None:
    ex.set("total_excl_tax", ht)
    ex.set("total_vat", tva)
    ex.set("total_incl_tax", ttc)
    if ht is not None and tva is not None and ttc is not None:
        ex.checks["totals_consistent"] = abs(ht + tva - ttc) < 0.05


# ---------------------------------------------------------------- Kbis


def kbis(ctx: Ctx) -> Extraction:
    ex = Extraction("kbis", required=["company_name", "siren", "legal_form", "head_office_address"])
    v = ctx.value_after
    ex.set("company_name", v(r"DENOMINATION OU RAISON SOCIALE|^DENOMINATION"))
    rcs = _row_with(ctx, r"IMMATRICULATION AU RCS|R\.C\.S\. ") or v(r"NUMERO D'IDENTIFICATION|^SIREN")
    if siren := digits_luhn(rcs, 9):
        ex.set("siren", siren)
        ex.checks["siren_luhn"] = True
    elif rcs and (m := re.search(r"\d{3}\s?\d{3}\s?\d{3}", rcs)):
        ex.set("siren", m.group(0).replace(" ", ""))
        ex.checks["siren_luhn"] = False
    if rcs:
        ex.set("rcs_city", _search(r"R\.?\s?C\.?\s?S\.?\s+([A-ZÀ-Ÿa-zà-ÿ][\w\- ]+)", rcs))
    ex.set("registration_date", parse_date(v(r"DATE D'IMMATRICULATION", DATE_VALUE) or ""))
    ex.set("legal_form", v(r"FORME JURIDIQUE"))
    capital = v(r"CAPITAL SOCIAL|^CAPITAL")
    ex.set("share_capital", capital)
    ex.set("euid", v(r"EUID", r"[A-Z]{2}\d{4}\.\S+"))
    ex.set("head_office_address", v(r"ADRESSE DU SIEGE|^SIEGE SOCIAL"))
    ex.set("activity", v(r"ACTIVITE\(S\) EXERCEE\(S\)|ACTIVITES? EXERCEES?|ACTIVITE PRINCIPALE|^ACTIVITE"))
    ex.set("activity_start_date", parse_date(v(r"DATE DE COMMENCEMENT", DATE_VALUE) or ""))
    ex.set("management_number", v(r"N[°O] DE GESTION", r"\w+"))
    ex.set("extract_date", parse_date(_search(r"A JOUR AU\s+(\d{1,2}\s+[A-Z]+\s+\d{4}|\d{2}/\d{2}/\d{4})", ctx.ntext) or ""))

    people, role = [], None
    for row in ctx.row_texts:
        n = norm(row).strip()
        if re.match(ROLES, n):
            role = row.strip()
            continue
        if role and n.startswith("NOM, PRENOM"):
            people.append({"role": role, "name": re.sub(r"^\S+,\s*\S+\s*", "", row).strip()})
            role = None
        elif re.match(r"^(PRECEDENT EXPLOITANT|RENSEIGNEMENTS)", n):
            role = None
    if people:
        ex.set("legal_representatives", people)
    return ex


# ---------------------------------------------------------------- invoice


def invoice(ctx: Ctx) -> Extraction:
    ex = Extraction("invoice", required=["invoice_number", "invoice_date", "total_incl_tax"])
    t = ctx.ntext
    m = re.search(r"FACTURE\s*(?:N[°O]?|NUMERO|NO\.?|#)\s*[:.]?\s*([A-Z0-9][A-Z0-9\-]*(?:\s*/\s*\d+)?)", t)
    if m:
        ex.set("invoice_number", re.sub(r"\s+", "", m.group(1)))
        row = _row_with(ctx, re.escape(m.group(0)))
        if row:
            # first valid date of the header row ("26669" would also look like a date)
            ex.set("invoice_date", next((d for x in re.finditer(DATE_VALUE, row) if (d := parse_date(x.group(0)))), None))
    ex.set("invoice_date", parse_date(ctx.value_after(r"DATE (DE )?(LA )?FACTURE|FACTURE DU|DATE D'EMISSION", DATE_VALUE) or ""))
    ex.set("due_date", parse_date(ctx.value_after(r"ECHEANCE|DATE LIMITE|A REGLER AVANT|A PAYER AVANT", DATE_VALUE) or ""))

    # supplier = the company in the header (legal form), else the first box of the page
    first = ctx.rows[0][0].text if ctx.rows and ctx.rows[0] else None
    ex.set("supplier_name", next((l.text for r in ctx.rows[:4] for l in r if re.search(LEGAL_FORMS, norm(l.text))), None) or first)
    # customer = the address block that is not the supplier's: name, then street and postcode
    supplier = ex.fields.get("supplier_name")
    customer = next((b for b in ctx.address_blocks() if len(b) >= 3 and supplier not in b), None)
    if customer:
        ex.set("customer_name", customer[-3])
        ex.set("customer_address", ", ".join(customer[-2:]))
    siret = digits_luhn(_search(r"SIRET\s*[:.]?\s*([\d ]{14,20})", t), 14)
    ex.set("supplier_siret", siret)
    if siret:
        ex.checks["siret_luhn"] = True
    ex.set("vat_number", _search(r"TVA(?: INTRACOM\w*)?\s*[:.]?\s*(FR\s?[0-9A-Z]{2}\s?\d{3}\s?\d{3}\s?\d{3})", t))
    if "vat_number" in ex.fields:
        ex.fields["vat_number"] = ex.fields["vat_number"].replace(" ", "")

    def total(pat: str) -> float | None:
        for r in ctx.row_texts:
            if re.search(pat, norm(r)) and (a := last_amount(r)) is not None:
                return a
        return None

    _amounts(
        ex,
        total(r"TOTAL\s*H\.?\s?T\b|MONTANT H\.?T|TOTAL HORS TAXE"),
        total(r"TOTAL\s*T\.?V\.?A\b|MONTANT T\.?V\.?A"),
        total(r"TOTAL\s*T\.?\s?T\.?\s?C|NET A PAYER|MONTANT T\.?T\.?C|TOTAL TTC"),
    )
    ibans = find_ibans(ctx.text)
    if ibans:
        ex.set("iban", ibans[0])
        ex.checks["iban"] = iban_ok(ibans[0])
    _vehicle(ctx, ex)
    return ex


# ---------------------------------------------------------------- bank statement


def bank_statement(ctx: Ctx) -> Extraction:
    ex = Extraction("bank_statement", required=["iban"])
    t = ctx.ntext
    ibans = find_ibans(ctx.text)
    if ibans:
        ex.set("iban", ibans[0])
        ex.checks["iban"] = True
    if m := BIC.search(t):
        ex.set("bic", m.group(0))
    ex.set("bank_name", _search(r"\b(BANQUE\s+[A-Z][A-Z' \-]{2,40}?|CREDIT [A-Z]+(?: [A-Z]+)?|SOCIETE GENERALE|BNP PARIBAS|LCL|"
                                r"CAISSE D'EPARGNE[A-Z ]*|BOURSORAMA|LA BANQUE POSTALE|HSBC|QONTO|SHINE)\b", t, 1))
    ex.set("statement_period", _search(r"RELEVE (?:MENSUEL|DE COMPTES?|DE COMPTE COURANT)\s*(?:DU|DE|AU)?\s*"
                                       r"([A-Z]+\s+\d{4}|\d{2}[./]\d{2}[./]\d{4}(?:\s*AU\s*\d{2}[./]\d{2}[./]\d{4})?)", t))
    ex.set("statement_date", parse_date(_search(r"(?:^|\n)\s*LE\s+(\d{1,2}\s+[A-Z]+\s+\d{4})", t) or ""))
    holder = next((l.text for r in ctx.rows[:12] for l in r
                   if re.search(LEGAL_FORMS + r"|^(M\.|MME|MR|MONSIEUR|MADAME)\b", norm(l.text)) and "BANQUE" not in norm(l.text)), None)
    ex.set("account_holder", holder)
    if m := re.search(r"NOUVEAU SOLDE[^\n]*?(" + AMOUNT + r")", ctx.text.upper()):
        ex.set("closing_balance", parse_amount(m.group(1)))
    return ex


# ---------------------------------------------------------------- expert report


def expert_report(ctx: Ctx) -> Extraction:
    ex = Extraction("expert_report", required=["report_number", "claim_number"])
    t = ctx.ntext
    ex.set("report_number", _search(r"N[°O]\s*(?:DE\s+)?RAPPORT\s*[:.]?\s*([A-Z0-9]{5,})", t) or _search(r"\bREF\s*:\s*([A-Z0-9]{6,})", t))
    ex.set("report_date", parse_date(_search(r"RAPPORT D.EXPERTISE DU\s*([\d/. ]{6,10})", t) or ""))
    ex.set("claim_number", _search(r"N[°O]\s*(?:DE\s+)?SINISTRE\s*[:.]?\s*([A-Z0-9]{5,})", t))
    ex.set("claim_date", parse_date(_search(r"DATE (?:DU )?SINISTRE\s*\W*\s*(\d{2}/\d{2}/\d{2,4})", t) or ""))
    ex.set("policy_number", _search(r"N[°O]\s*POLICE[/\w]*\s*[:.]?\s*([A-Z0-9][\w/\-]{3,})", t))
    ex.set("insurer", _search(r"NOM SOCIETE\s*[:.]?\s*([A-Z][\w.\-]+(?: [A-Z][\w.\-]+)?)", t) or _insurer(t))
    ex.set("expert_name", _search(r"\bEXPERT\s*:\s*([A-Z][A-Z\-]+(?: [A-Z][A-Z\-]+){1,3}?)\s+N[°O]", t))
    ex.set("insured_name", _search(r"^ASSURE\s*:\s*(.+)$", t, flags=re.M))
    ex.set("insured_address", ctx.address_of(r"^ASSURE\s*:"))
    ex.set("conclusion", _search(r"VEHICULE\s+(?:TECHNIQUEMENT\s+)?((?:ECONOMIQUEMENT\s+|NON\s+)?(?:IR)?REPARABLE)", t))
    _amounts(
        ex,
        parse_amount(_search(r"TOTAL\s*H\.?T\.?\s*\W*\s*(" + AMOUNT + ")", t)),
        parse_amount(_search(r"TVA\s*\W*\s*(" + AMOUNT + ")", t)),
        parse_amount(_search(r"TOTAL\s*T\.?T\.?C\.?\s*\W*\s*(" + AMOUNT + ")", t)),
    )
    ex.set("repairer_invoice_number", _search(r"N[°O]\s*FACTURE[\w ]*?\s*[:.]\s*([\w/]+)", t))
    _vehicle(ctx, ex)
    return ex


# ---------------------------------------------------------------- repair order


def _customer_contact(ex: Extraction, text: str) -> None:
    """Address, phones and email of the "Client" block; the labels share their rows with the appointment dates."""
    block = _search(r"^CLIENT[ \t]*$(.*?)(?=^EXPERT\b|\Z)", text, flags=re.M | re.S)
    if not block:
        return
    address = _search(r"^ADRESSE\b(.*?)(?=^TEL\b|\Z)", block, flags=re.M | re.S) or ""
    rows = [re.sub(r"\s*\bDATE\b.*$", "", row).strip() for row in address.splitlines()]
    ex.set("customer_address", ", ".join(row for row in rows if row) or None)
    number = r"\s*:?\s*((?:\+33|0)[\d .\-]{8,16}\d)"
    phone = _search(r"^TEL\b" + number, block, flags=re.M)
    mobile = _search(r"^PORTABLE\b" + number, block, flags=re.M)
    ex.set("customer_phone", format_phone(phone) if phone else None)
    ex.set("customer_mobile", format_phone(mobile) if mobile else None)
    email = EMAIL.search(block.lower())
    ex.set("customer_email", email.group(0) if email else None)


def repair_order(ctx: Ctx) -> Extraction:
    ex = Extraction("repair_order", required=["order_number", "plate"])
    t = ctx.ntext
    v = ctx.value_after
    ex.set("order_number", _search(r"N[°O]\s*(?:D')?O\.?R\.?\b[^0-9\n]*(\d{3,})", t))
    ex.set("opened_date", parse_date(_search(r"OUVERT LE\s*(\d{2}/\d{2}/\d{2,4})", t) or ""))
    ex.set("work_type", _search(r"N[°O]\s*O\.?R\.?\s+([A-Z][A-Z ]+?)\s+\d", t))
    _vehicle(ctx, ex)
    ex.set("vehicle_make", v(r"^MARQUE$|\bMARQUE\b", exclude=r"^(MODELE|TYPE|IMMAT)"))
    ex.set("vehicle_model", v(r"^MODELE$|\bMODELE\b", exclude=r"^(IMMAT|N[°O]|DATE)"))
    ex.set("customer_name", v(r"^NOM$", exclude=r"^(NOM|ADRESSE|DATE)"))
    _customer_contact(ex, t)
    ex.set("insurer", _insurer(t))
    ex.set("work_description", v(r"DESCRIPTION SOMMAIRE DES TRAVAUX|TRAVAUX A EFFECTUER|NATURE DES TRAVAUX"))
    return ex


# ---------------------------------------------------------------- claim letters / insurer correspondence


def insurance_claim(ctx: Ctx) -> Extraction:
    ex = Extraction("insurance_claim", required=["claim_number", "claim_date"])
    t = ctx.ntext
    ex.set("claim_number", _search(r"SINISTRE\s+DU\s+\S+\s*-\s*N[°O]\s*([A-Z0-9]{5,})", t)
           or _search(r"N[°O]\s*(?:DE\s+)?SINISTRE\s*[:.]?\s*([A-Z0-9]{5,})", t))
    ex.set("claim_date", parse_date(_search(r"(?:DATE DU SINISTRE|SINISTRE DU)\s*[:.]?\s*(\d{2}/\d{2}/\d{2,4})", t) or ""))
    ex.set("contract_number", _search(r"CONTRAT\s*N[°O]\s*[:.]?\s*([A-Z0-9]{4,})", t) or _search(r"VOTRE CONTRAT[^\n]*\n?[^\n]*?N[°O]\s*:?\s*([A-Z0-9]{4,})", t))
    ex.set("insurer", _insurer(t))
    ex.set("subject", _search(r"OBJET\s*:\s*([^\n]+)", ctx.text, flags=re.I))
    ex.set("insurer_reference", _search(r"N/\s*REF[^\n:]*:\s*\n?\s*([A-Z0-9]{6,})", t))
    ex.set("liability", _search(r"\b(NON RESPONSABLE|PARTIELLEMENT RESPONSABLE|ENTIEREMENT RESPONSABLE|RESPONSABILITE PARTAGEE)\b", t))
    _vehicle(ctx, ex)
    return ex


EXTRACTORS = {
    "kbis": kbis,
    "invoice": invoice,
    "bank_statement": bank_statement,
    "expert_report": expert_report,
    "repair_order": repair_order,
    "insurance_claim": insurance_claim,
}
