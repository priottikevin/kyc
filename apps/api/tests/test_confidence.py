"""Per-field confidence from per-character OCR scores, and parsing of the plain-text LLM answers (fake data only)."""

import asyncio

import numpy as np

from app.confidence import THRESHOLD, field_confidence, uncertain
from app.config import Settings
from app.llm import LlmClient
from app.ocr import Line, _chars


def L(text, y, scores=None, conf=0.99):
    return Line(text, conf, 0, y, 400, y + 30, scores if scores is not None else [0.999] * len(text))


def test_lowest_character_drives_the_score():
    # one doubtful character ("O" read for "0") hidden in a line whose average is high
    scores = [0.999] * 22
    scores[4] = 0.79
    lines = [L("92300 VILLE-SUR-TESTER", 0, scores), L("DUPONT", 50)]
    conf, where = field_confidence({"city": "92300 VILLE-SUR-TESTER", "surname": "DUPONT"}, {}, {}, lines)
    assert conf["city"] == {"score": 0.79, "level": "check"}
    assert conf["surname"]["level"] == "high" and conf["surname"]["score"] >= THRESHOLD
    assert where["city"] == [lines[0]]
    assert uncertain(conf) == ["city"]


def test_normalised_values_are_found_as_printed():
    lines = [L("Né le 01.02.1990", 0), L("Total TTC 1 234,50", 50), L("12 RUE DES ESSAIS", 100), L("75000 PARIS", 150)]
    conf, _ = field_confidence(
        {"birth_date": "1990-02-01", "total": 1234.5, "address": "12 RUE DES ESSAIS, 75000 PARIS", "derived": "essence"},
        {}, {}, lines,
    )
    assert conf["birth_date"]["level"] == "high"
    assert conf["total"]["level"] == "high"
    assert conf["address"]["level"] == "high"
    assert conf["derived"] == {"score": None, "level": "unknown"}


def test_checksums_and_text_layer():
    lines = [L("FR76 0000", 0, [0.5] * 9)]
    conf, _ = field_confidence({"iban": "FR760000", "surname": "X"}, {"surname": "mrz"}, {"iban": True}, lines)
    assert conf["iban"]["level"] == "verified" and conf["surname"]["level"] == "verified"
    native = [Line("Dénomination : SOCIETE TEST", 1.0, 0, 0, 100, 10, None)]
    conf, _ = field_confidence({"extract_date": "2026-01-04"}, {}, {}, native)
    assert conf["extract_date"]["level"] == "high"  # exact PDF text, even when reformatted


def test_char_scores_follow_strip():
    class Info:
        confs = [0.1, 0.9, 0.8, 0.2]

    assert _chars(" AB ", Info()) == ("AB", [0.9, 0.8])
    assert _chars("AB", Info())[1] is None  # misaligned -> no per-character scores


def _client(answer: str) -> LlmClient:
    c = LlmClient(Settings(anthropic_api_key="test"))

    async def fake_call(content, system, schema, max_tokens):
        assert schema is None  # plain text: a JSON schema adds latency
        return {"text": answer, "_usage": {"input_tokens": 1, "output_tokens": 1}}

    c._call = fake_call
    return c


def test_read_lines_parsing():
    img = np.full((20, 100, 3), 255, np.uint8)
    res = asyncio.run(_client("[L0] BANQUE TEST\n[L1]: 92300 VILLE\nbruit").read_lines([("L0", img, "BANOUE TEST"), ("L1", img, "9230O VILLE")]))
    assert res["fields"] == {"L0": "BANQUE TEST", "L1": "92300 VILLE"}


def test_extract_parsing():
    answer = "document_type: id_card\ncountry: FRA\nsurname: DUPONT\n- given_names: Jean, Paul\nemails: a@b.c, d@e.f\n"
    res = asyncio.run(_client(answer).extract([], "texte", None, {}))
    assert res["document_type"] == "id_card" and res["country"] == "FRA"
    assert res["fields"] == {"surname": "DUPONT", "given_names": "Jean, Paul", "emails": ["a@b.c", "d@e.f"]}
    assert asyncio.run(_client("document_type: inconnu\n").extract([], "", None, {}))["document_type"] == "other"
