import asyncio

import pytest

from app.pipeline import Options

from . import synth

EXPECTED = {
    "id_card_2021": ("id_card", "verified", {"surname": "MARTIN-DUPRÉ", "given_names": "Élodie, Françoise", "birth_date": "1988-07-14", "document_number": "X4RTBPFW4", "expiry_date": "2031-02-11", "birth_place": "LYON 3E (69)"}),
    "id_card_old": ("id_card", "verified", {"surname": "BERTHIER", "given_names": "CORINNE", "birth_date": "1965-12-06", "document_number": "880692310285"}),
    "passport": ("passport", "verified", {"given_names": "Jean-Baptiste", "birth_date": "1979-03-02", "document_number": "22AB12345", "expiry_date": "2032-09-15"}),
    "driving_license": ("driving_license", "complete", {"surname": "DURAND", "given_names": "THOMAS", "birth_date": "1990-05-21", "license_number": "16AB12345", "expiry_date": "2031-10-03", "categories": ["AM", "A1", "B1", "B"]}),
    "vehicle_registration": ("vehicle_registration", "complete", {"plate": "AB-123-CD", "vin": "VF1RJA00012345678", "make": "RENAULT", "commercial_name": "CLIO", "holder_name": "MARTIN ELODIE", "holder_address": "12 RUE DES LILAS, 69003 LYON", "engine_capacity_cm3": "999", "first_registration_date": "2019-03-12"}),
    "insurance": ("insurance_certificate", "complete", {"insurer": "MAIF", "policy_number": "1234567 B", "valid_from": "2025-01-01", "valid_to": "2025-12-31", "plate": "AB-123-CD"}),
    "business_card": ("business_card", "complete", {"full_name": "Sophie Lambert", "emails": ["sophie.lambert@acme-solutions.fr"]}),
    "native_pdf": ("insurance_certificate", "complete", {"insurer": "MATMUT", "holder_name": "Paul ROBERT", "valid_to": "2026-02-14"}),
}


@pytest.mark.parametrize("name", list(EXPECTED))
def test_synthetic_documents(pipeline, name):
    doc_type, status, fields = EXPECTED[name]
    r = asyncio.run(pipeline.process(synth.ALL[name](), Options(llm="off")))
    assert r["document_type"] == doc_type
    assert r["status"] == status, r
    for k, v in fields.items():
        assert r["fields"].get(k) == v, (k, r["fields"])


def test_forced_type(pipeline):
    r = asyncio.run(pipeline.process(synth.passport(), Options(document_type="passport", llm="off")))
    assert r["document_type"] == "passport" and r["status"] == "verified"


class FakeLlm:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def extract(self, images, text, hint, partial):
        self.calls += 1
        return {**self.result, "usage": {"input_tokens": 1, "output_tokens": 1}}


def test_llm_not_called_when_rules_complete(pipeline):
    fake = FakeLlm({"document_type": "passport", "country": "FRA", "fields": {}})
    old, pipeline.llm = pipeline.llm, fake
    try:
        r = asyncio.run(pipeline.process(synth.passport(), Options(llm="fallback")))
    finally:
        pipeline.llm = old
    assert fake.calls == 0 and not r["engine"]["llm_used"]


def test_llm_cannot_override_checksum_validated_fields(pipeline):
    fake = FakeLlm({"document_type": "passport", "country": "FRA", "fields": {"birth_date": "1980-01-01", "issuing_authority": "Préfecture de Loire-Atlantique"}})
    old, pipeline.llm = pipeline.llm, fake
    try:
        r = asyncio.run(pipeline.process(synth.passport(), Options(llm="always")))
    finally:
        pipeline.llm = old
    assert fake.calls == 1
    assert r["fields"]["birth_date"] == "1979-03-02"  # MRZ wins
    assert r["fields"]["issuing_authority"] == "Préfecture de Loire-Atlantique"
    assert r["field_sources"]["issuing_authority"] == "llm"
    assert any("birth_date" in w for w in r["warnings"])


def test_llm_called_for_business_cards(pipeline):
    fake = FakeLlm({"document_type": "business_card", "country": "FRA", "fields": {"full_name": "Sophie Lambert", "company": "ACME Solutions SAS"}})
    old, pipeline.llm = pipeline.llm, fake
    try:
        r = asyncio.run(pipeline.process(synth.business_card(), Options(llm="fallback")))
    finally:
        pipeline.llm = old
    assert fake.calls == 1
    assert r["fields"]["company"] == "ACME Solutions SAS"
    assert r["engine"]["ocr_passes"] == ["tiny"]  # no second OCR pass when the LLM sees the image
