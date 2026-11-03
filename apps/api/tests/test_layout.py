"""Regression: real phone photo of a 2021 CNI recto (tilted, no MRZ), boxes as returned by the fast OCR.

Geometry and OCR noise are kept from the real capture; personal values are replaced by fake ones.
"""

from app import extractors
from app.extractors import Ctx
from app.ocr import Line
from app.textutils import parse_date

# (x0, x1, y0, y1, text) - fast "tiny" pass
CNI_RECTO_TINY = [
    (186, 558, 45, 106, "RÉPUBLIQUE FRANÇAISE"),
    (186, 554, 77, 122, "CARTE NATIONALE D'IDENTITÉ /IDENTITY CARD"),
    (295, 369, 104, 128, "NOMISumame"),
    (294, 394, 117, 150, "MARTINL"),
    (292, 397, 160, 185, "Prénoms/ Givennames"),
    (290, 417, 174, 207, "Zoé,Anne"),
    (287, 489, 215, 247, "SEXE(Sex NATIONALITEI NaBonalty"),
    (502, 666, 230, 257, "DATE DE NAISS7 Date Ofbith"),
    (288, 315, 233, 254, "F"),
    (347, 397, 236, 259, "FRA"),
    (505, 619, 245, 276, "03 05 1985"),
    (287, 472, 251, 280, "LIEUDE NAISSANCE / Place ofbith"),
    (287, 565, 266, 306, "LYON 3E ARRONDISSEMENT"),
    (281, 460, 323, 350, "N* DU DOCUMENT/ DOCument No."),
    (496, 639, 334, 364, "DATE D'EXPIR/Expiry date"),
    (278, 480, 335, 377, "X4RT9ZQ12"),
    (496, 614, 352, 384, "14 112031"),
    (496, 623, 386, 426, "123456"),
]

# accurate "small" pass: glued bilingual labels, dates without separators
CNI_RECTO_SMALL_DIFF = {
    "NOMISumame": "NOMI Sumame",
    "SEXE(Sex NATIONALITEI NaBonalty": "SEXE /Sex NATIONALITE/ Nationality DATE DE NAISS / Date of birth",
    "03 05 1985": "03051985",
    "14 112031": "14112031",
}


def _lines(rows, diff=None):
    out = []
    for x0, x1, y0, y1, t in rows:
        t = (diff or {}).get(t, t)
        out.append(Line(t, 0.95, x0, y0, x1, y1))
    return out


EXPECTED = {
    "given_names": "Zoé, Anne",
    "birth_date": "1985-05-03",
    "expiry_date": "2031-11-14",
    "document_number": "X4RT9ZQ12",
    "sex": "F",
    "nationality": "FRA",
    "birth_place": "LYON 3E ARRONDISSEMENT",
}


def test_cni_recto_photo_fast_pass():
    ex = extractors.run("id_card", Ctx(_lines(CNI_RECTO_TINY)))
    assert ex.fields["surname"] == "MARTINL"  # fast-model misread, fixed by the targeted re-read in the pipeline
    for k, v in EXPECTED.items():
        assert ex.fields.get(k) == v, (k, ex.fields)
    assert ex.complete


def test_cni_recto_photo_accurate_pass():
    rows = [r for r in CNI_RECTO_TINY if r[4] != "DATE DE NAISS7 Date Ofbith"]
    lines = _lines(rows, CNI_RECTO_SMALL_DIFF)
    # merged label box spans the whole row in the accurate pass
    lines = [Line(l.text, l.conf, l.x0, l.y0, 666 if l.text.startswith("SEXE") else l.x1, l.y1) for l in lines]
    ex = extractors.run("id_card", Ctx(lines))
    assert ex.fields["surname"] == "MARTINL"
    for k, v in EXPECTED.items():
        assert ex.fields.get(k) == v, (k, ex.fields)


def test_dates_without_separators():
    assert parse_date("12091980") == "1980-09-12"
    assert parse_date("28 072032") == "2032-07-28"
    assert parse_date("420005") is None


# CNI 2021 verso from an A4 scan (fast pass after margin trim), fake values
CNI_VERSO_TINY = [
    (412, 556, 615, 633, "DATE DE DELIVRANCE/ Date ofisSUP"),
    (330, 390, 619, 636, "TALLE/ Heigh"),
    (327, 393, 630, 653, "1,82 m"),
    (411, 514, 630, 650, "02 032021"),
    (627, 712, 642, 669, "01032031"),  # hologram date, right of the address label
    (329, 418, 650, 670, "ADRESSEI AddiresS"),
    (329, 433, 662, 683, "4 RUE DE LA REPUBLIQUE"),
    (329, 493, 673, 697, "69002 LYON"),
    (331, 386, 693, 710, "FRANCE"),
    (284, 487, 801, 827, "RÉPUBLIQUE FRANÇAISE"),
]


def test_cni_verso_address_block_and_height():
    ex = extractors.run("id_card", Ctx(_lines(CNI_VERSO_TINY)))
    assert ex.fields["address"] == "4 RUE DE LA REPUBLIQUE, 69002 LYON, FRANCE"
    assert ex.fields["height"] == "1,82 m"
    assert ex.fields["issue_date"] == "2021-03-02"


def test_trim_margins_crops_blank_page():
    import numpy as np

    from app.ingest import trim_margins

    page = np.full((2000, 1400, 3), 255, np.uint8)
    page[200:500, 150:700] = 90  # the card
    page[1900, 10] = 0  # scanner speck
    out = trim_margins(page)
    assert out.shape[0] < 500 and out.shape[1] < 700
    photo = np.full((500, 700, 3), 120, np.uint8)  # non-white background: untouched
    assert trim_margins(photo).shape == photo.shape
