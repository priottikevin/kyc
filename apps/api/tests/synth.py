"""Synthetic (fake) documents for tests - no real personal data."""

import io

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.extractors.mrz import check_digit


def _font(size: int, bold: bool = False):
    for name in (("arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else ("arial.ttf", "DejaVuSans.ttf")):
        for base in ("C:/Windows/Fonts/", "/usr/share/fonts/truetype/dejavu/", ""):
            try:
                return ImageFont.truetype(base + name, size)
            except OSError:
                pass
    return ImageFont.load_default(size=size)


def _mono(size: int):
    for p in ("C:/Windows/Fonts/consola.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def _finish(im: Image.Image, rotate: float = 0.8) -> bytes:
    im = im.rotate(rotate, fillcolor=(205, 205, 205)).filter(ImageFilter.GaussianBlur(0.7))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return buf.getvalue()


def _labels(d, rows, x, y, step=62):
    for k, v in rows:
        d.text((x, y), k, font=_font(20), fill=(70, 70, 70))
        d.text((x, y + 22), v, font=_font(30, True), fill=(0, 0, 0))
        y += step


def td1(code, state, num, dob, sex, exp, nat, surname, given):
    l1 = f"{code:<2}{state}{num:<9}{check_digit(num.ljust(9, '<'))}".ljust(30, "<").replace(" ", "<")
    l2 = f"{dob}{check_digit(dob)}{sex}{exp}{check_digit(exp)}{nat}".ljust(29, "<")
    l2 += check_digit(l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29])
    l3 = (surname.replace(" ", "<").replace("-", "<") + "<<" + given.replace(" ", "<")).ljust(30, "<")[:30]
    return [l1.replace(" ", "<"), l2, l3]


def td3(state, num, nat, dob, sex, exp, surname, given):
    l1 = ("P<" + state + surname.replace(" ", "<").replace("-", "<") + "<<" + given.replace(" ", "<")).ljust(44, "<")[:44]
    pers = "<" * 14
    l2 = f"{num:<9}".replace(" ", "<")
    l2 += check_digit(l2) + nat + dob + check_digit(dob) + sex + exp + check_digit(exp) + pers + check_digit(pers)
    l2 += check_digit(l2[0:10] + l2[13:20] + l2[21:43])
    return [l1, l2]


def fr_cni(surname, num12, given, dob, sex, dept="693", office="025"):
    l1 = ("IDFRA" + surname.replace(" ", "<").replace("-", "<")).ljust(30, "<")[:30] + dept + office
    l2 = num12 + check_digit(num12) + given.replace(" ", "<<").ljust(14, "<")[:14] + dob + check_digit(dob) + sex
    l2 += check_digit(l1 + l2)
    return [l1, l2]


def id_card_2021() -> bytes:
    im = Image.new("RGB", (1400, 880), (225, 232, 240))
    d = ImageDraw.Draw(im)
    d.text((40, 30), "RÉPUBLIQUE FRANÇAISE", font=_font(34, True), fill=(20, 40, 90))
    d.text((40, 75), "CARTE NATIONALE D'IDENTITÉ / IDENTITY CARD", font=_font(26), fill=(20, 40, 90))
    d.rectangle((40, 140, 340, 520), fill=(180, 190, 200))
    _labels(d, [("NOM / Surname", "MARTIN-DUPRÉ"), ("Prénoms / Given names", "Élodie, Françoise"), ("Sexe / Sex", "F"),
                ("Nationalité / Nationality", "FRA"), ("Date de naiss. / Date of birth", "14 07 1988"),
                ("Lieu de naissance / Place of birth", "LYON 3E (69)"), ("N° du document / Document No", "X4RTBPFW4"),
                ("Date d'expiration / Expiry date", "11 02 2031")], 380, 140)
    for i, l in enumerate(td1("ID", "FRA", "X4RTBPFW4", "880714", "F", "310211", "FRA", "MARTIN DUPRE", "ELODIE FRANCOISE")):
        d.text((50, 680 + i * 55), l, font=_mono(40), fill=(0, 0, 0))
    return _finish(im)


def id_card_old() -> bytes:
    im = Image.new("RGB", (1400, 900), (232, 225, 236))
    d = ImageDraw.Draw(im)
    d.text((300, 30), "RÉPUBLIQUE FRANÇAISE", font=_font(34, True), fill=(20, 40, 90))
    d.text((300, 75), "CARTE NATIONALE D'IDENTITÉ N° : 880692310285", font=_font(28), fill=(20, 40, 90))
    _labels(d, [("Nom :", "BERTHIER"), ("Prénom(s) :", "CORINNE"), ("Sexe : F   Né(e) le :", "06.12.1965"), ("à :", "PARIS 1ER (75)")], 380, 140, 72)
    for i, l in enumerate(fr_cni("BERTHIER", "880692310285", "CORINNE", "651206", "F")):
        d.text((50, 720 + i * 60), l, font=_mono(37), fill=(0, 0, 0))
    return _finish(im, -0.6)


def passport() -> bytes:
    im = Image.new("RGB", (1600, 1050), (240, 235, 225))
    d = ImageDraw.Draw(im)
    d.text((420, 30), "PASSEPORT / PASSPORT", font=_font(36, True), fill=(80, 20, 40))
    _labels(d, [("Type / Type", "P"), ("Nom / Surname (1)", "LEFÈVRE"), ("Prénoms / Given names (2)", "Jean-Baptiste"),
                ("Nationalité / Nationality (3)", "Française"), ("Date de naissance / Date of birth (4)", "02 03 1979"),
                ("Lieu de naissance / Place of birth", "NANTES"), ("Date d'expiration / Date of expiry", "15 09 2032"),
                ("Passeport N° / Passport No", "22AB12345")], 420, 100, 70)
    for i, l in enumerate(td3("FRA", "22AB12345", "FRA", "790302", "M", "320915", "LEFEVRE", "JEAN BAPTISTE")):
        d.text((40, 880 + i * 60), l, font=_mono(34), fill=(0, 0, 0))
    return _finish(im)


def driving_license() -> bytes:
    im = Image.new("RGB", (1400, 880), (235, 215, 225))
    d = ImageDraw.Draw(im)
    d.text((380, 30), "PERMIS DE CONDUIRE   RÉPUBLIQUE FRANÇAISE", font=_font(30, True), fill=(60, 20, 60))
    rows = ["1. DURAND", "2. THOMAS", "3. 21.05.1990 MARSEILLE (13)", "4a. 03.10.2016   4c. PRÉFECTURE DES BOUCHES-DU-RHÔNE",
            "4b. 03.10.2031", "5. 16AB12345", "9. AM/A1/B1/B"]
    for i, r in enumerate(rows):
        d.text((380, 120 + i * 80), r, font=_font(34, True), fill=(0, 0, 0))
    return _finish(im)


def vehicle_registration() -> bytes:
    im = Image.new("RGB", (1700, 1100), (230, 235, 225))
    d = ImageDraw.Draw(im)
    d.text((500, 25), "CERTIFICAT D'IMMATRICULATION", font=_font(36, True), fill=(30, 60, 30))
    left = [("A", "AB-123-CD"), ("B", "12/03/2019"), ("C.1", "MARTIN ELODIE"), ("C.3", "12 RUE DES LILAS"), ("", "69003 LYON"),
            ("D.1", "RENAULT"), ("D.2", "BJA1M2"), ("D.3", "CLIO"), ("E", "VF1RJA00012345678")]
    right = [("F.1", "1634"), ("F.2", "1634"), ("G", "1259"), ("J.1", "VP"), ("P.1", "999"), ("P.3", "ES"), ("P.6", "5"),
             ("S.1", "5"), ("I", "12/03/2019")]
    for i, (c, v) in enumerate(left):
        y = 110 + i * 70
        d.text((40, y), c, font=_font(28, True), fill=(0, 0, 0))
        d.text((140, y), v, font=_font(30), fill=(0, 0, 0))
    for i, (c, v) in enumerate(right):
        y = 110 + i * 70
        d.text((1000, y), c, font=_font(28, True), fill=(0, 0, 0))
        d.text((1100, y), v, font=_font(30), fill=(0, 0, 0))
    return _finish(im)


def insurance() -> bytes:
    im = Image.new("RGB", (1300, 1700), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.text((60, 50), "MAIF", font=_font(60, True), fill=(200, 0, 50))
    d.text((60, 160), "ATTESTATION D'ASSURANCE AUTOMOBILE", font=_font(40, True), fill=(0, 0, 0))
    lines = ["Assuré : Mme Élodie MARTIN", "Adresse : 12 rue des Lilas 69003 LYON", "N° de contrat : 1234567 B",
             "Période de validité : du 01/01/2025 au 31/12/2025", "Véhicule assuré", "Marque : RENAULT",
             "Modèle : CLIO", "Immatriculation : AB-123-CD", "Garanties : Tous risques"]
    for i, l in enumerate(lines):
        d.text((60, 280 + i * 70), l, font=_font(32), fill=(0, 0, 0))
    return _finish(im, 0.3)


def business_card() -> bytes:
    im = Image.new("RGB", (1050, 600), (250, 250, 248))
    d = ImageDraw.Draw(im)
    d.text((60, 60), "Sophie Lambert", font=_font(54, True), fill=(10, 10, 10))
    d.text((60, 140), "Directrice Commerciale", font=_font(30), fill=(90, 90, 90))
    d.text((60, 200), "ACME Solutions SAS", font=_font(30, True), fill=(30, 60, 150))
    for i, l in enumerate(["sophie.lambert@acme-solutions.fr", "+33 6 12 34 56 78", "www.acme-solutions.fr", "8 avenue Foch", "75008 Paris"]):
        d.text((60, 300 + i * 50), l, font=_font(28), fill=(30, 30, 30))
    return _finish(im, 0.0)


def native_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for l in ["ATTESTATION D'ASSURANCE AUTOMOBILE", "Assureur : MATMUT", "Souscripteur : Paul ROBERT",
              "N° de contrat : 987654321", "Valable du 15/02/2025 au 14/02/2026", "Immatriculation : GH-456-JK"]:
        page.insert_text((72, y), l, fontsize=12)
        y += 24
    return doc.tobytes()


ALL = {
    "id_card_2021": id_card_2021,
    "id_card_old": id_card_old,
    "passport": passport,
    "driving_license": driving_license,
    "vehicle_registration": vehicle_registration,
    "insurance": insurance,
    "business_card": business_card,
    "native_pdf": native_pdf,
}
