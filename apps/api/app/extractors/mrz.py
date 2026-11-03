"""ICAO 9303 MRZ parsing with OCR error correction and check-digit validation.

Supported: TD1 (3x30, ID cards / residence permits), TD2 (2x36), TD3 (2x44, passports),
and the French national ID card issued before 2021 (2x36, non-ICAO layout).
"""

import re
from dataclasses import dataclass, field
from datetime import date

_TO_DIGIT = str.maketrans({"O": "0", "Q": "0", "D": "0", "U": "0", "I": "1", "L": "1", "T": "1", "Z": "2", "S": "5", "B": "8", "G": "6", "A": "4", "<": "0"})
_TO_ALPHA = str.maketrans({"0": "O", "1": "I", "2": "Z", "4": "A", "5": "S", "6": "G", "8": "B"})


def char_value(c: str) -> int:
    if c.isdigit():
        return int(c)
    if "A" <= c <= "Z":
        return ord(c) - 55
    return 0


def check_digit(s: str) -> str:
    w = (7, 3, 1)
    return str(sum(char_value(c) * w[i % 3] for i, c in enumerate(s)) % 10)


def clean_line(s: str) -> str:
    s = s.upper().replace(" ", "")
    s = re.sub(r"[«‹<>{}\[\]()|]", "<", s)
    s = re.sub(r"[^A-Z0-9<]", "<", s)
    # filler '<' commonly misread as K/C/L/E inside filler runs
    for _ in range(2):
        s = re.sub(r"(?<=<)[KCLE](?=<)", "<", s)
    return s


def looks_like_mrz(s: str) -> bool:
    c = clean_line(s)
    if len(c) < 28:
        return False
    return c.count("<") >= 2 and sum(ch.isalnum() or ch == "<" for ch in s.replace(" ", "")) / max(len(s.replace(" ", "")), 1) > 0.9


def fit(s: str, n: int) -> str:
    """Adjust length by growing/shrinking the longest '<' run (OCR miscounts fillers)."""
    while len(s) != n:
        runs = [(m.end() - m.start(), m.start(), m.end()) for m in re.finditer(r"<+", s)]
        if len(s) > n:
            if not runs:
                return s[:n]
            ln, a, b = max(runs)
            s = s[:a] + s[a + 1:]
        else:
            if not runs:
                return s + "<" * (n - len(s))
            ln, a, b = max(runs)
            s = s[:a] + "<" + s[a:]
    return s


def digits(s: str) -> str:
    return s.translate(_TO_DIGIT)


def alpha(s: str) -> str:
    return s.translate(_TO_ALPHA)


def _date(yymmdd: str, future: bool) -> str | None:
    if not yymmdd.isdigit():
        return None
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:])
    cur = date.today().year % 100
    if future:
        y = 2000 + yy if yy < 70 else 1900 + yy
    else:
        y = 1900 + yy if yy > cur else 2000 + yy
    try:
        return date(y, mm, dd).isoformat()
    except ValueError:
        return None


def _names(s: str) -> tuple[str, str]:
    s = s.rstrip("<")
    parts = s.split("<<", 1)
    surname = parts[0].replace("<", " ").strip()
    given = parts[1].replace("<", " ").strip() if len(parts) > 1 else ""
    given = re.sub(r"\s+", " ", given)
    return surname, given


def _field_with_check(value: str, check: str) -> tuple[str, str, bool]:
    """Try to repair a numeric-ish field so that it matches its check digit."""
    check = digits(check)
    if check_digit(value) == check:
        return value, check, True
    # try the digit-coerced version (doc numbers may legitimately contain letters, so only if it validates)
    dv = digits(value)
    if check_digit(dv) == check:
        return dv, check, True
    return value, check, False


@dataclass
class MrzResult:
    format: str
    lines: list[str]
    fields: dict = field(default_factory=dict)
    checks: dict = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    @property
    def score(self) -> int:
        return sum(self.checks.values())


def _common(doc_code: str, state: str, number: str, nat: str, dob: str, sex: str, exp: str, names: str, optional: str = "") -> dict:
    surname, given = _names(names)
    sex = {"M": "M", "F": "F"}.get(sex, "X")
    f = {
        "document_code": doc_code.replace("<", ""),
        "issuing_country": alpha(state).replace("<", ""),
        "document_number": number.replace("<", ""),
        "surname": surname,
        "given_names": given,
        "nationality": alpha(nat).replace("<", ""),
        "birth_date": _date(dob, future=False),
        "sex": sex,
        "expiry_date": _date(exp, future=True),
    }
    opt = optional.replace("<", "")
    if opt:
        f["optional_data"] = opt
    return {k: v for k, v in f.items() if v}


def parse_td3(l1: str, l2: str) -> MrzResult:
    l1, l2 = fit(l1, 44), fit(l2, 44)
    num, c1, ok1 = _field_with_check(l2[0:9], l2[9])
    dob, c2, ok2 = _field_with_check(digits(l2[13:19]), l2[19])
    exp, c3, ok3 = _field_with_check(digits(l2[21:27]), l2[27])
    pers, pc = l2[28:42], l2[42]
    ok4 = check_digit(pers) == digits(pc)
    ok5 = check_digit(num + c1 + dob + c2 + exp + c3 + pers + pc) == digits(l2[43])
    f = _common(l1[0:2], l1[2:5], num, l2[10:13], dob, l2[20], exp, l1[5:44], pers)
    return MrzResult("TD3", [l1, l2], f, {"document_number": ok1, "birth_date": ok2, "expiry_date": ok3, "personal_number": ok4, "composite": ok5})


def parse_td2(l1: str, l2: str) -> MrzResult:
    l1, l2 = fit(l1, 36), fit(l2, 36)
    num, c1, ok1 = _field_with_check(l2[0:9], l2[9])
    dob, c2, ok2 = _field_with_check(digits(l2[13:19]), l2[19])
    exp, c3, ok3 = _field_with_check(digits(l2[21:27]), l2[27])
    opt = l2[28:35]
    ok5 = check_digit(num + c1 + dob + c2 + exp + c3 + opt) == digits(l2[35])
    f = _common(l1[0:2], l1[2:5], num, l2[10:13], dob, l2[20], exp, l1[5:36], opt)
    return MrzResult("TD2", [l1, l2], f, {"document_number": ok1, "birth_date": ok2, "expiry_date": ok3, "composite": ok5})


def parse_td1(l1: str, l2: str, l3: str) -> MrzResult:
    l1, l2, l3 = fit(l1, 30), fit(l2, 30), fit(l3, 30)
    opt1 = l1[15:30]
    if l1[14] == "<" and opt1.strip("<"):
        # long document number continues in optional field; its last char is the check digit
        ext = opt1.split("<", 1)[0]
        raw_num = l1[5:14] + ext[:-1]
        num, c1, ok1 = _field_with_check(raw_num, ext[-1])
    else:
        num, c1, ok1 = _field_with_check(l1[5:14], l1[14])
    dob, c2, ok2 = _field_with_check(digits(l2[0:6]), l2[6])
    exp, c3, ok3 = _field_with_check(digits(l2[8:14]), l2[14])
    opt2 = l2[18:29]
    long_num = l1[14] == "<" and bool(opt1.strip("<"))
    l1_part = l1[5:30] if long_num else num + c1 + opt1
    ok4 = check_digit(l1_part + dob + c2 + exp + c3 + opt2) == digits(l2[29])
    f = _common(l1[0:2], l1[2:5], num, l2[15:18], dob, l2[7], exp, l3, ("" if long_num else opt1) + opt2)
    return MrzResult("TD1", [l1, l2, l3], f, {"document_number": ok1, "birth_date": ok2, "expiry_date": ok3, "composite": ok4})


def parse_fr_cni(l1: str, l2: str) -> MrzResult:
    """French ID card (1988-2021): IDFRA + surname(25) + dept(3) + office(3) / number(12)+chk + given(14) + dob(6)+chk + sex + chk."""
    l1, l2 = fit(l1, 36), fit(l2, 36)
    num_raw = l2[0:12]
    num, c1, ok1 = _field_with_check(num_raw, l2[12])
    dob, c2, ok2 = _field_with_check(digits(l2[27:33]), l2[33])
    ok3 = check_digit(l1 + num + c1 + l2[13:27] + dob + c2 + l2[34]) == digits(l2[35])
    surname = l1[5:30].rstrip("<").replace("<", " ").strip()
    given = re.sub(r"\s+", ", ", l2[13:27].rstrip("<").replace("<<", " ").replace("<", "-").strip())
    sex = {"M": "M", "F": "F"}.get(l2[34], "X")
    f = {
        "document_code": "ID",
        "issuing_country": "FRA",
        "document_number": num,
        "surname": surname,
        "given_names": given,
        "nationality": "FRA",
        "birth_date": _date(dob, future=False),
        "sex": sex,
    }
    # Validity: 10 years (15 for cards issued 2004-2013 to adults) - not encoded; issue year/month is
    if num[:4].isdigit():
        f["issue_month"] = f"20{num[:2]}-{num[2:4]}" if int(num[:2]) < 50 else f"19{num[:2]}-{num[2:4]}"
    return MrzResult("FR_CNI", [l1, l2], {k: v for k, v in f.items() if v}, {"document_number": ok1, "birth_date": ok2, "composite": ok3})


def find_mrz(texts: list[str]) -> MrzResult | None:
    """texts: OCR rows (already joined left->right) in top->bottom order."""
    cand = [clean_line(t) for t in texts if looks_like_mrz(t)]
    if not cand:
        return None
    results: list[MrzResult] = []
    n = len(cand)
    for i in range(n):
        a = cand[i]
        if i + 2 < n and all(26 <= len(x) <= 34 for x in cand[i:i + 3]):
            results.append(parse_td1(cand[i], cand[i + 1], cand[i + 2]))
        if i + 1 < n:
            b = cand[i + 1]
            la, lb = len(a), len(b)
            if 40 <= la <= 48 and 40 <= lb <= 48:
                results.append(parse_td3(a, b))
            elif 32 <= la <= 40 and 32 <= lb <= 40:
                if a.startswith("IDFRA"):
                    results.append(parse_fr_cni(a, b))
                results.append(parse_td2(a, b))
    if not results:
        return None
    return max(results, key=lambda r: (r.valid, r.score, len(r.fields)))
