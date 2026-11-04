"""Business documents and MRZ false positives, from OCR rows of real documents with fake values."""

from app import extractors
from app.classify import classify
from app.extractors import Ctx
from app.extractors.mrz import find_mrz
from app.ocr import Line


def ctx_from(text: str) -> Ctx:
    """One box per row, stacked top to bottom (layout-free documents)."""
    lines = []
    for i, row in enumerate(t for t in text.strip().splitlines() if t.strip()):
        lines.append(Line(row.strip(), 0.95, 50, 40 * i, 50 + 12 * len(row), 40 * i + 25))
    return Ctx(lines)


def run(text: str):
    ctx = ctx_from(text)
    doc_type, _ = classify(ctx.ntext, ctx.row_texts)
    return doc_type, extractors.run(doc_type, ctx)


BANK_ROWS = """
Le 30 juin 2026
RELEVÉ MENSUEL JUIN 2026
Banque Exemple
GARAGE DUPONT SARL
FR08 30004 00001 0001234567890-BNPAFRPPXXX
Ag Date Libellé et Référence Valeur Débit EUR Crédit EUR
93 22.06 AXA FRANCE IARD SA/Virement SEPA 000001889 23.06
36 25.06 DIRECTION GENERALE/3303239272080100019111 25.06
93 25.06 MMA IARD/POUR LE SINISTRE:M0004M10943 26.06
NOUVEAU SOLDE AU 30.06.2026:
"""


def test_bank_statement_rows_are_not_an_mrz():
    ctx = ctx_from(BANK_ROWS)
    assert find_mrz(ctx.row_texts) is None
    doc_type, ex = run(BANK_ROWS)
    assert doc_type == "bank_statement"
    assert ex.fields["iban"] == "FR0830004000010001234567890"
    assert ex.fields["bic"] == "BNPAFRPPXXX"
    assert ex.fields["statement_period"] == "JUIN 2026"
    assert ex.fields["account_holder"] == "GARAGE DUPONT SARL"
    assert "closing_balance" not in ex.fields  # "30.06.2026" is a date, not an amount


def test_kbis():
    doc_type, ex = run("""
Extrait Kbis
EXTRAIT D'IMMATRICULATION PRINCIPALE AU REGISTRE DU COMMERCE ET DES SOCIETES
à jour au 4 octobre 2026
Immatriculation au RCS, numéro 732 829 320 R.C.S. Lyon
Date d'immatriculation 21/10/1993
Dénomination ou raison sociale GARAGE DUPONT
Forme juridique Société par actions simplifiée
Capital social 10 000,00 Euros
Adresse du siège 1 Rue de la Paix 69002 Lyon
Gérant
Nom, prénoms DUPONT Jean Paul
Activité(s) exercée(s) Réparation automobile
Précédent exploitant
Nom, prénoms MARTIN Paul
""")
    assert doc_type == "kbis"
    assert ex.fields["siren"] == "732829320" and ex.checks["siren_luhn"]
    assert ex.fields["company_name"] == "GARAGE DUPONT"
    assert ex.fields["rcs_city"] == "Lyon"
    assert ex.fields["activity"] == "Réparation automobile"
    assert ex.fields["legal_representatives"] == [{"role": "Gérant", "name": "DUPONT Jean Paul"}]
    assert ex.complete


def test_invoice_totals_and_date_after_number():
    doc_type, ex = run("""
SARL GARAGE DUPONT Facture N° 26669 / 1 Le 22/05/2026
Siret : 73282932000074 N° RCS: 502Z
N°TVA: FR44732829320
DESIGNATION Qté Pu H.T Prix Net HT
PARE-BRISE 1.00 776.50 776.50
TOTAL H.T. 20.00 = 1328.30 €
TOTAL TVA 20.00 = 265.66 €
TVA SUR ENCAISSEMENTS TOTAL T.T.C. = 1593.96 €
RIB:FR0830004000010001234567890
Article 1 - PORTEE JURIDIQUE DE L'ORDRE DE REPARATION
""")
    assert doc_type == "invoice"
    assert ex.fields["invoice_number"] == "26669/1"
    assert ex.fields["invoice_date"] == "2026-05-22"
    assert (ex.fields["total_excl_tax"], ex.fields["total_vat"], ex.fields["total_incl_tax"]) == (1328.3, 265.66, 1593.96)
    assert ex.checks == {"siret_luhn": True, "totals_consistent": True, "iban": True}


REPAIR_ORDER_ROWS = """
ORDRE DE REPARATION
N° OR BRIS DE GLACE 34061 Ouvert le 07/05/2026
Immatriculation GJ-868-JC Marque TOYOTA
Client
Nom DUPONT Date de Rdv Heure
Adresse Date d'entrée Date Début Travaux
10 RUE DES LILAS
77420 CHAMPS SUR MARNE Date fin travaux Date de Sortie
Tel 01 23 45 67 89 Fax 01 98 76 54 32 Date d'expertise Expert
Portable 06 12 34 56 78
Mail client@exemple.fr
Expert Assurance
Nom Nom MAIF
Adresse Adresse 200 AVENUE SALVADOR ALLENDE
Tel Fax Tel 09 72 72 15 15 Fax
"""


def test_repair_order_customer_contact():
    doc_type, ex = run(REPAIR_ORDER_ROWS)
    assert doc_type == "repair_order"
    assert ex.fields["customer_address"] == "10 RUE DES LILAS, 77420 CHAMPS SUR MARNE"
    assert ex.fields["customer_phone"] == "01 23 45 67 89"
    assert ex.fields["customer_mobile"] == "06 12 34 56 78"
    assert ex.fields["customer_email"] == "client@exemple.fr"


def test_address_blocks_tie_a_column_together():
    # two columns: the right one is broken up by the rows of the left one
    lines = [
        Line("Souscripteur du contrat", 0.95, 500, 0, 700, 25),
        Line("La garantie du", 0.95, 50, 45, 200, 70),
        Line("FCBA", 0.95, 500, 45, 560, 70),
        Line("10 RUE GALILEE", 0.95, 500, 90, 680, 115),
        Line("77420 CHAMPS SUR MARNE", 0.95, 500, 135, 760, 160),
        Line("33300 BORDEAUX", 0.95, 50, 135, 240, 160),
    ]
    assert Ctx(lines).address_of(r"^FCBA$") == "10 RUE GALILEE, 77420 CHAMPS SUR MARNE"


def test_invoice_customer_is_the_block_of_the_recipient():
    lines = [
        Line("SARL CARROSSERIE DUPONT", 0.95, 50, 0, 300, 25),
        Line("Facture N° 26669 / 1 Le 22/05/2026", 0.95, 500, 0, 800, 25),
        Line("26 RUE DES LILAS", 0.95, 50, 40, 250, 65),
        Line("33300 BORDEAUX", 0.95, 50, 80, 240, 105),
        Line("FCBA", 0.95, 800, 200, 860, 225),
        Line("10 RUE GALILEE", 0.95, 800, 250, 980, 275),
        Line("77420 CHAMPS SUR MARNE", 0.95, 800, 300, 1060, 325),
    ]
    ex = extractors.run("invoice", Ctx(lines))
    assert ex.fields["customer_name"] == "FCBA"
    assert ex.fields["customer_address"] == "10 RUE GALILEE, 77420 CHAMPS SUR MARNE"
