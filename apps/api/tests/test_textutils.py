from app.textutils import entities, iban_ok, luhn_ok, parse_date


def test_parse_date():
    assert parse_date("14 07 1988") == "1988-07-14"
    assert parse_date("14.07.1988") == "1988-07-14"
    assert parse_date("le 3 juillet 2021") == "2021-07-03"
    assert parse_date("31/02/2020") is None


def test_validators():
    assert iban_ok("FR76 3000 6000 0112 3456 7890 189")
    assert not iban_ok("FR76 3000 6000 0112 3456 7890 188")
    assert luhn_ok("73282932000074")  # SIRET


def test_entities():
    e = entities("Tel 06 12 34 56 78 - jean@ex.fr - FR76 3000 6000 0112 3456 7890 189 - AB-123-CD")
    assert e["emails"] == ["jean@ex.fr"]
    assert e["phones"] == ["06 12 34 56 78"]
    assert e["ibans"] == ["FR7630006000011234567890189"]
    assert e["plates"] == ["AB-123-CD"]
