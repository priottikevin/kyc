import asyncio

import cv2
import numpy as np

from app.pipeline import Options

from . import synth


def _run(pipeline, data: bytes, **kw) -> dict:
    return asyncio.run(pipeline.process(data, Options(llm="off", include_positions=True, **kw)))


def _rotate(data: bytes, code) -> bytes:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    return cv2.imencode(".jpg", cv2.rotate(img, code))[1].tobytes()


def test_positions_on_upright_photo(pipeline):
    r = _run(pipeline, synth.vehicle_registration())
    assert r["page_rotations"] == [0]
    pos = r["field_positions"]
    assert {"plate", "vin"} <= set(pos)
    for p in pos.values():
        assert p["page"] == 1
        assert 0 <= p["x"] <= 1000 and 0 <= p["y"] <= 1000
        assert p["width"] > 0 and p["height"] > 0
    # the box surrounds the value, not the whole line with its label
    assert pos["plate"]["width"] < 400


def test_positions_follow_a_sideways_photo(pipeline):
    """Photo taken sideways: boxes are given in the photo as sent, plus the rotation to read it upright."""
    upright = _run(pipeline, synth.vehicle_registration())["field_positions"]["vin"]
    r = _run(pipeline, _rotate(synth.vehicle_registration(), cv2.ROTATE_90_CLOCKWISE))
    assert r["page_rotations"] == [270]
    p = r["field_positions"]["vin"]
    # turning the page 90° clockwise maps (x, y, w, h) to (1000 - y - h, x, h, w)
    expected = (1000 - upright["y"] - upright["height"], upright["x"], upright["height"], upright["width"])
    got = (p["x"], p["y"], p["width"], p["height"])
    assert all(abs(a - b) <= 30 for a, b in zip(got, expected)), (got, expected)


def test_positions_upside_down(pipeline):
    upright = _run(pipeline, synth.insurance())["field_positions"]["policy_number"]
    r = _run(pipeline, _rotate(synth.insurance(), cv2.ROTATE_180))
    assert r["page_rotations"] == [180]
    p = r["field_positions"]["policy_number"]
    expected = (1000 - upright["x"] - upright["width"], 1000 - upright["y"] - upright["height"])
    assert abs(p["x"] - expected[0]) <= 30 and abs(p["y"] - expected[1]) <= 30, (p, expected)


def test_positions_on_native_pdf(pipeline):
    r = _run(pipeline, synth.native_pdf())
    assert r["page_rotations"] == [0]
    assert r["field_positions"]["insurer"]["page"] == 1


def test_positions_only_on_request(pipeline):
    r = asyncio.run(pipeline.process(synth.passport(), Options(llm="off")))
    assert "field_positions" not in r
    assert r["page_rotations"] == [0]
