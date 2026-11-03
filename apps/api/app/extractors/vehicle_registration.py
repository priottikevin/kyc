"""French vehicle registration certificate (certificat d'immatriculation / carte grise)."""

import re

from ..textutils import VIN, clean_value, find_plate, norm, parse_date
from .base import Ctx, Extraction

# Code -> output key. Order matters for regex alternation (longest first).
CODES = {
    "A": "plate",
    "B": "first_registration_date",
    "C.1": "holder_name",
    "C.3": "holder_address",
    "C.4.1": "co_owner",
    "C.4a": "holder_status",
    "D.1": "make",
    "D.2": "type_variant",
    "D.2.1": "national_type_code",
    "D.3": "commercial_name",
    "E": "vin",
    "F.1": "max_mass_kg",
    "F.2": "max_mass_in_service_kg",
    "F.3": "max_mass_combination_kg",
    "G": "mass_in_service_kg",
    "G.1": "empty_mass_kg",
    "H": "validity",
    "I": "certificate_date",
    "J": "category_eu",
    "J.1": "national_category",
    "J.2": "body_eu",
    "J.3": "body_national",
    "K": "type_approval",
    "P.1": "engine_capacity_cm3",
    "P.2": "max_power_kw",
    "P.3": "fuel",
    "P.6": "fiscal_power_cv",
    "Q": "power_weight_ratio",
    "S.1": "seats",
    "S.2": "standing_places",
    "U.1": "stationary_noise_db",
    "U.2": "engine_speed_rpm",
    "V.7": "co2_g_km",
    "V.9": "environmental_class",
    "X.1": "technical_inspection_date",
    "Y.1": "tax_regional",
    "Y.6": "tax_total",
    "Z.1": "mentions",
}
_alts = sorted(CODES, key=len, reverse=True) + ["1"]  # "I" is often read as "1"
CODE_RE = re.compile(r"^(" + "|".join(re.escape(c).replace(r"\.", r"\s?[.,]?\s?") for c in _alts) + r")(?:[.,:)]|\s)\s*(.*)$")
CODE_ONLY = re.compile(r"^(" + "|".join(re.escape(c).replace(r"\.", r"\s?[.,]?\s?") for c in _alts) + r")[.,:)]?$")

FUELS = {"ES": "essence", "GO": "gazole", "EL": "électrique", "EH": "hybride essence non rechargeable", "EE": "hybride essence rechargeable", "GH": "hybride gazole", "GL": "hybride gazole rechargeable", "FE": "superéthanol E85", "GP": "GPL", "EG": "bicarburation essence-GPL", "GN": "GNV"}


def _canon(code: str) -> str:
    c = re.sub(r"\s", "", code).replace(",", ".")
    if c == "1":
        return "I"
    if c not in CODES and len(c) >= 2 and "." not in c:
        c = c[0] + "." + ".".join(c[1:])
    return c


def extract(ctx: Ctx, doc_type: str) -> Extraction:
    ex = Extraction("vehicle_registration", required=["plate", "vin", "holder_name", "make", "first_registration_date"])
    found: dict[str, str] = {}
    val_x: dict[str, float] = {}  # x of the value column, to attach continuation lines
    val_row: dict[str, int] = {}
    code_row: dict[str, int] = {}  # row of a code printed alone (value on the next row, e.g. C.3 address)
    for ri, row in enumerate(ctx.rows):
        current: str | None = None
        for ln in row:
            t = ln.text.strip()
            m = CODE_ONLY.match(t)
            if m:
                current = _canon(m.group(1))
                found.setdefault(current, "")
                if len(row) == 1:
                    code_row[current] = ri
                continue
            m = CODE_RE.match(t)
            if m and m.group(2):
                current = _canon(m.group(1))
                if not found.get(current):
                    found[current] = m.group(2).strip()
                    val_x[current], val_row[current] = ln.x0, ri
                continue
            if current is None:
                # no code yet on this row: continuation of a multi-line holder name / address?
                for k in ("C.3", "C.1"):
                    if not found.get(k) and code_row.get(k) == ri - 1:
                        found[k] = t
                        val_x[k], val_row[k] = ln.x0, ri
                        break
                    if k in val_x and ri - val_row[k] == 1 and abs(ln.x0 - val_x[k]) < 3 * ln.h:
                        found[k] += ", " + t
                        val_row[k] = ri
                        break
                continue
            if not found.get(current):
                val_x[current], val_row[current] = ln.x0, ri
            found[current] = (found.get(current, "") + " " + t).strip()

    for code, val in found.items():
        key = CODES.get(code)
        val = clean_value(val)
        if not key or not val:
            continue
        if key.endswith("_date"):
            ex.set(key, parse_date(val))
        elif key == "plate":
            ex.set(key, find_plate(val) or val.upper())
        elif key == "vin":
            m = VIN.search(val.upper().replace(" ", ""))
            ex.set(key, m.group(0) if m else None)
        elif key == "fuel":
            code_f = val.upper().split()[0]
            ex.set(key, code_f)
            if code_f in FUELS:
                ex.set("fuel_label", FUELS[code_f])
        elif key == "holder_address":
            # postcodes: "9230O" -> "92300"
            ex.set(key, re.sub(r"(?<![\w])[0-9OQ]{5}(?![\w])", lambda m: m.group(0).replace("O", "0").replace("Q", "0")
                               if sum(c.isdigit() for c in m.group(0)) >= 3 else m.group(0), val))
        elif key.endswith(("_kg", "_cm3", "_kw", "_cv", "seats", "_g_km", "_db", "_rpm")) or key.startswith("tax_"):
            m = re.search(r"\d+(?:[.,]\d+)?", val)
            ex.set(key, m.group(0).replace(",", ".") if m else None)
        else:
            ex.set(key, val)

    # Global fallbacks: plate and VIN are pattern-identifiable anywhere
    if "plate" not in ex.fields and (p := find_plate(ctx.text)):
        ex.set("plate", p, "pattern")
    if "vin" not in ex.fields:
        m = VIN.search(norm(ctx.text))
        if m:
            ex.set("vin", m.group(0), "pattern")
    if "plate" in ex.fields and not find_plate(ex.fields["plate"]):
        ex.warnings.append("Format d'immatriculation non SIV")
    return ex
