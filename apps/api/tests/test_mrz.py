from app.extractors.mrz import check_digit, find_mrz

from .synth import fr_cni, td1, td3


def test_check_digit():
    assert check_digit("L898902C3") == "6"
    assert check_digit("740812") == "2"
    assert check_digit("<<<<<<<<<<<<<<") == "0"


def test_icao_td3_specimen():
    r = find_mrz(["P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<", "L898902C36UTO7408122F1204159ZE184226B<<<<<10"])
    assert r.format == "TD3" and r.valid
    assert r.fields["surname"] == "ERIKSSON"
    assert r.fields["given_names"] == "ANNA MARIA"
    assert r.fields["birth_date"] == "1974-08-12"
    assert r.fields["document_number"] == "L898902C3"


def test_icao_td1_specimen():
    r = find_mrz(["I<UTOD231458907<<<<<<<<<<<<<<<", "7408122F1204159UTO<<<<<<<<<<<6", "ERIKSSON<<ANNA<MARIA<<<<<<<<<<"])
    assert r.format == "TD1" and r.valid
    assert r.fields["document_number"] == "D23145890"


def test_icao_td2_specimen():
    r = find_mrz(["I<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<", "D231458907UTO7408122F1204159<<<<<<<6"])
    assert r.format == "TD2" and r.valid


def test_ocr_noise_is_repaired():
    # O instead of 0 in dates, missing fillers, 'K' read inside a filler run
    r = find_mrz(["P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<", "L898902C36UTO74O8122F12O4159ZE184226B<<K<<10"])
    assert r.valid
    assert r.fields["birth_date"] == "1974-08-12"
    assert r.fields["expiry_date"] == "2012-04-15"


def test_corrupted_mrz_is_flagged():
    r = find_mrz(["P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<", "L898902C36UTO7408132F1204159ZE184226B<<<<<10"])
    assert not r.valid
    assert r.checks["birth_date"] is False


def test_french_old_cni():
    r = find_mrz(fr_cni("BERTHIER", "880692310285", "CORINNE", "651206", "F"))
    assert r.format == "FR_CNI" and r.valid
    assert r.fields["given_names"] == "CORINNE"
    assert r.fields["birth_date"] == "1965-12-06"


def test_generated_td1_td3_roundtrip():
    assert find_mrz(td1("ID", "FRA", "X4RTBPFW4", "880714", "F", "310211", "FRA", "MARTIN DUPRE", "ELODIE")).valid
    assert find_mrz(td3("FRA", "22AB12345", "FRA", "790302", "M", "320915", "LEFEVRE", "JEAN")).valid
