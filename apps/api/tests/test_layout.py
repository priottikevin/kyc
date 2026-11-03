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
