import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

pytestmark = pytest.mark.skipif(not hasattr(os, "openpty"), reason="needs a pseudo-terminal")

from beamcraft_core.machine import GrblConnection, parse_status, clean_gcode  # noqa: E402
from fake_grbl import FakeGrbl  # noqa: E402


def drain(conn, until, timeout=10):
    t0 = time.time()
    seen = []
    while time.time() - t0 < timeout:
        try:
            ev = conn.events.get(timeout=0.05)
        except Exception:
            continue
        seen.append(ev)
        if until(ev):
            return seen
    raise AssertionError(f"timeout, events: {seen[-10:]}")


@pytest.fixture
def link():
    g = FakeGrbl()
    c = GrblConnection()
    c.connect(g.port)
    yield g, c
    c.disconnect()
    g.close()


def test_parse_status():
    st = parse_status("<Run|MPos:10.000,5.500,0.000|FS:1200,300|Ov:100,100,90>")
    assert st["state"] == "Run" and st["MPos"][1] == 5.5 and st["power"] == 300 and st["Ov"][2] == 90


def test_clean_gcode():
    assert clean_gcode(["; c", "G1 X1 (x) ; y", "", "M2"]) == ["G1 X1", "M2"]


def test_status_and_jog(link):
    g, c = link
    drain(c, lambda e: e[0] == "status")
    c.jog(5, -2, 1000)
    drain(c, lambda e: e[0] == "status" and abs(e[1]["MPos"][0] - 5) < 1e-6)
    c.set_zero()
    drain(c, lambda e: e[0] == "status" and abs(e[1]["WPos"][0]) < 1e-6 and abs(e[1]["MPos"][0] - 5) < 1e-6)


def test_stream_character_counting(link):
    g, c = link
    lines = [f"G1 X{i * 0.1:.3f} Y{(i % 7) * 0.2:.3f} S300 F1500" for i in range(600)]
    c.start_job(["; header", "G21 G90"] + lines + ["M2"])
    drain(c, lambda e: e == ("job", "done"), timeout=30)
    assert g.received[-1] == "M2" and len([x for x in g.received if x.startswith("G1")]) == 600
    assert g.max_buffer <= 128                    # never overflowed Grbl's RX buffer
    assert g.max_buffer > 60                      # and several lines were in flight


def test_error_pauses_streaming(link):
    g, c = link
    g.fail_on = {"G1 X5"}
    c.start_job(["G1 X1", "G1 X5"] + [f"G1 X{i}" for i in range(10, 400)])
    drain(c, lambda e: e == ("job", "paused-error"))
    n = len(g.received)
    time.sleep(0.3)
    assert len(g.received) - n < 15               # only what was already in Grbl's buffer
    c.resume()
    drain(c, lambda e: e == ("job", "done"), timeout=30)


def test_stop_sends_hold_then_reset(link):
    g, c = link
    g.exec_time = 0.01
    c.start_job([f"G1 X{i}" for i in range(500)])
    drain(c, lambda e: e[0] == "progress" and e[1][0] > 5)
    c.stop_job()
    drain(c, lambda e: e[0] == "welcome")
    assert g.realtime.index(b"!") < g.realtime.index(b"\x18")


def test_overrides(link):
    g, c = link
    c.feed_override("+10")
    c.power_override("-10")
    time.sleep(0.2)
    assert b"\x91" in g.realtime and b"\x9b" in g.realtime


def test_pointer_moves_then_fires_low_power_then_off(link):
    g, c = link
    drain(c, lambda e: e[0] == "status")
    c.pointer_at(12.5, 7.0, 10.0, feed=2000, timeout=0.6)
    drain(c, lambda e: e == ("pointer", True))
    time.sleep(0.15)       # let the fake Grbl execute the last lines
    assert any(l.startswith("$J=G90") and "X12.500" in l for l in g.received)
    i_g1 = g.received.index("G90 G1 F1000")
    assert g.received[i_g1 + 1] == "M3 S10.0"
    drain(c, lambda e: e == ("pointer", False), timeout=5)   # timeout switches it off
    time.sleep(0.2)
    assert g.received[-1] == "M5 S0"


def test_pointer_off_on_jog(link):
    g, c = link
    drain(c, lambda e: e[0] == "status")
    c.pointer_at(1, 1, 5.0, timeout=30)
    drain(c, lambda e: e == ("pointer", True))
    c.jog(1, 0, 1000)
    drain(c, lambda e: e == ("pointer", False))
    time.sleep(0.2)
    assert "M5 S0" in g.received
