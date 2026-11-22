# BeamCraft - Grbl v1.1 machine connection (serial / network)
# Copyright (c) 2026  Tarfu Air - GPL-3.0-or-later
"""
Connection to a Grbl v1.1 controller (see the Grbl v1.1 Interface and
Commands wiki pages):

* streaming with the character-counting protocol: several lines are sent in
  advance as long as the unacknowledged characters fit in Grbl's 128-byte
  serial receive buffer, so the 16-block planner buffer never starves;
* status reports polled with '?' at 5 Hz (state, position, feed, power,
  overrides);
* realtime commands: feed hold '!', cycle start '~', soft reset 0x18,
  jog cancel 0x85, feed / spindle (laser power) overrides 0x90..0x9D;
* jogging ($J=), homing ($H), alarm unlock ($X), work zero (G10 L20),
  framing (jog around the job bounds with the laser off).

No GUI code here: events are delivered through a thread-safe queue
(`events`) that the GUI polls.  Each event is a tuple (kind, payload):
    ("line", str)          raw line received (console)
    ("sent", str)          line sent (console)
    ("status", dict)       parsed status report
    ("alarm", str)  ("error", str)  ("welcome", str)  ("msg", str)
    ("progress", (acked, total))
    ("job", "started" | "done" | "stopped" | "paused-error")
    ("pointer", True | False)  aiming dot on / off
    ("disconnected", reason)
"""

import queue
import re
import threading
import time
from collections import deque

try:
    import serial
    from serial.tools import list_ports
except ImportError:   # pragma: no cover
    serial = None
    list_ports = None

RX_BUFFER = 128          # Grbl serial receive buffer (bytes)
POLL_HZ = 5.0            # status query rate recommended by the Grbl wiki

RT_RESET = b"\x18"
RT_STATUS = b"?"
RT_HOLD = b"!"
RT_RESUME = b"~"
RT_JOG_CANCEL = b"\x85"
RT_FEED = {"reset": b"\x90", "+10": b"\x91", "-10": b"\x92", "+1": b"\x93", "-1": b"\x94"}
RT_POWER = {"reset": b"\x99", "+10": b"\x9a", "-10": b"\x9b", "+1": b"\x9c", "-1": b"\x9d"}

ERRORS = {
    1: "Expected command letter", 2: "Bad number format", 3: "Invalid $ statement",
    4: "Negative value", 5: "Homing not enabled", 6: "Step pulse too short",
    7: "EEPROM read fail", 8: "Not idle", 9: "G-code locked (alarm or jog)",
    10: "Soft limits need homing", 11: "Line too long", 12: "Step rate too high",
    13: "Safety door open", 14: "Build info / startup line too long",
    15: "Jog target exceeds travel", 16: "Invalid jog command", 17: "Laser mode needs PWM",
    20: "Unsupported command", 21: "Modal group violation", 22: "Undefined feed rate",
    23: "Integer value required", 24: "Two G-codes need axis words",
    25: "Repeated word", 26: "No axis words", 27: "Invalid line number",
    28: "Missing value word", 29: "G59.x not supported", 30: "G53 needs G0/G1",
    31: "Unused axis words", 32: "G2/G3 need an in-plane axis word",
    33: "Invalid motion target (arc)", 34: "Arc radius error", 35: "G2/G3 need an offset",
    36: "Unused value words", 37: "G43.1 needs an axis", 38: "Tool number too large",
}
ALARMS = {
    1: "Hard limit triggered", 2: "Soft limit: target out of travel",
    3: "Reset while in motion: position lost, re-home", 4: "Probe fail (initial state)",
    5: "Probe fail (no contact)", 6: "Homing fail: reset during cycle",
    7: "Homing fail: door opened", 8: "Homing fail: pull-off did not clear switch",
    9: "Homing fail: switch not found",
}


def list_serial_ports():
    if list_ports is None:
        return []
    return [p.device for p in list_ports.comports()]


def parse_status(text):
    """'<Idle|MPos:1,2,3|FS:0,0|WCO:..|Ov:..>' -> dict."""
    body = text.strip().lstrip("<").rstrip(">")
    parts = body.split("|")
    st = {"state": parts[0]}
    for p in parts[1:]:
        if ":" not in p:
            continue
        k, v = p.split(":", 1)
        vals = v.split(",")
        try:
            if k in ("MPos", "WPos", "WCO"):
                st[k] = tuple(float(x) for x in vals)
            elif k == "FS":
                st["feed"], st["power"] = float(vals[0]), float(vals[1])
            elif k == "F":
                st["feed"] = float(vals[0])
            elif k == "Ov":
                st["Ov"] = tuple(int(float(x)) for x in vals)
            elif k == "Bf":
                st["Bf"] = tuple(int(x) for x in vals)
            else:
                st[k] = v
        except ValueError:
            st[k] = v
    return st


def clean_gcode(lines):
    """Strips comments, blank lines and spaces (fewer bytes to stream)."""
    out = []
    for raw in lines:
        txt = re.sub(r"\(.*?\)", "", raw.split(";", 1)[0]).strip()
        if txt:
            out.append(txt)
    return out


class GrblConnection:
    def __init__(self):
        self.ser = None
        self.events = queue.Queue()
        self._lock = threading.Lock()          # serial writes
        self._cv = threading.Condition()       # buffer accounting
        self._pending = deque()                # lengths of unacknowledged lines
        self._used = 0
        self._stop = threading.Event()
        self._threads = []
        self.wco = (0.0, 0.0, 0.0)
        self.status = {}
        # job
        self._job = None
        self._job_idx = 0
        self._job_acked = 0
        self._job_paused = False
        self._job_thread = None
        self.last_error = None
        # aiming pointer
        self._pointer_seq = 0
        self.pointer_active = False

    # ------------------------------------------------------------ link ---
    @property
    def connected(self):
        return self.ser is not None

    def connect(self, port, baud=115200, timeout=0.1):
        """port: 'COM3', '/dev/ttyUSB0', or a pyserial URL ('socket://192.168.1.20:23')."""
        if serial is None:
            raise RuntimeError("pyserial is not installed (pip install pyserial).")
        if "://" in port:
            self.ser = serial.serial_for_url(port, baudrate=baud, timeout=timeout)
        else:
            self.ser = serial.Serial(port, baudrate=baud, timeout=timeout)
        self._stop.clear()
        self._reset_counters()
        for target in (self._reader, self._poller):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self._threads.append(t)
        # wake up Grbl (some boards reset on connect, others need a newline)
        time.sleep(0.05)
        self._write(b"\r\n\r\n")

    def disconnect(self, reason="closed"):
        if self.pointer_active and self.ser is not None:
            try:
                self._write(b"M5 S0\n")
            except Exception:
                pass
        self.pointer_active = False
        self._pointer_seq += 1
        self._stop.set()
        self.stop_job(reset=False, notify=False)
        ser, self.ser = self.ser, None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        with self._cv:
            self._cv.notify_all()
        self.events.put(("disconnected", reason))

    def _write(self, data):
        ser = self.ser
        if ser is None:
            return False
        try:
            with self._lock:
                ser.write(data)
            return True
        except Exception as e:  # cable unplugged...
            self.disconnect(f"write error: {e}")
            return False

    def _reset_counters(self):
        with self._cv:
            self._pending.clear()
            self._used = 0
            self._cv.notify_all()

    # --------------------------------------------------------- threads ---
    def _poller(self):
        period = 1.0 / POLL_HZ
        while not self._stop.is_set():
            self._write(RT_STATUS)
            self._stop.wait(period)

    def _reader(self):
        buf = b""
        while not self._stop.is_set():
            ser = self.ser
            if ser is None:
                return
            try:
                chunk = ser.read(256)
            except Exception as e:
                if not self._stop.is_set():
                    self.disconnect(f"read error: {e}")
                return
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("ascii", "replace").strip()
                if line:
                    self._handle(line)

    def _ack(self, ok=True):
        with self._cv:
            if self._pending:
                n, is_job = self._pending.popleft()
                self._used -= n
                if is_job:
                    self._job_acked += 1
            self._cv.notify_all()

    def _handle(self, line):
        if line.startswith("<"):
            st = parse_status(line)
            if "WCO" in st:
                self.wco = st["WCO"]
            if "MPos" in st:
                st["WPos"] = tuple(m - w for m, w in zip(st["MPos"], self.wco))
            elif "WPos" in st:
                st["MPos"] = tuple(p + w for p, w in zip(st["WPos"], self.wco))
            self.status = st
            self.events.put(("status", st))
            return
        self.events.put(("line", line))
        if line == "ok":
            self._ack(True)
        elif line.startswith("error:"):
            code = line[6:]
            try:
                desc = ERRORS.get(int(code), "")
            except ValueError:
                desc = ""
            self.last_error = f"{line} {desc}".strip()
            self._ack(False)
            self.events.put(("error", self.last_error))
            if self._job is not None and not self._job_paused:
                # stop feeding new lines; the user decides to resume or stop
                self._job_paused = True
                self.events.put(("job", "paused-error"))
        elif line.startswith("ALARM:"):
            try:
                desc = ALARMS.get(int(line[6:]), "")
            except ValueError:
                desc = ""
            self.events.put(("alarm", f"{line} {desc}".strip()))
            if self._job is not None:
                self._abort_job("stopped")
        elif line.startswith("Grbl"):
            # welcome message: Grbl was reset, its buffers are empty
            self._reset_counters()
            if self._job is not None:
                self._abort_job("stopped")
            self.events.put(("welcome", line))
        elif line.startswith("[MSG:"):
            self.events.put(("msg", line[5:].rstrip("]")))

    # ---------------------------------------------------------- sending ---
    def send_line(self, line, is_job=False, wait=True):
        """Character-counting send of one line. Blocks while Grbl's RX buffer is full."""
        data = (line.strip() + "\n").encode("ascii", "replace")
        n = len(data)
        if n >= RX_BUFFER:
            raise ValueError(f"line too long for Grbl ({n} bytes)")
        with self._cv:
            while self._used + n > RX_BUFFER - 1:
                if self._stop.is_set() or self.ser is None or (is_job and self._job is None):
                    return False
                if not wait:
                    return False
                self._cv.wait(0.2)
            self._pending.append((n, is_job))
            self._used += n
        ok = self._write(data)
        if ok and not is_job:
            self.events.put(("sent", line.strip()))
        return ok

    def command(self, line):
        """Console / button command (refused while a job is streaming)."""
        if self.job_running:
            raise RuntimeError("A job is running.")
        return self.send_line(line)

    def realtime(self, byte):
        return self._write(byte)

    # ------------------------------------------------------- shortcuts ---
    def home(self):
        return self.command("$H")

    def unlock(self):
        return self.command("$X")

    def set_zero(self):
        return self.command("G10 L20 P1 X0 Y0")

    def goto_zero(self, feed=3000):
        return self.command(f"$J=G90 G21 X0 Y0 F{feed:g}")

    def jog(self, dx=0.0, dy=0.0, feed=1000):
        self.pointer_off()
        words = []
        if dx:
            words.append(f"X{dx:.3f}")
        if dy:
            words.append(f"Y{dy:.3f}")
        if not words:
            return False
        return self.command(f"$J=G91 G21 {' '.join(words)} F{feed:g}")

    def jog_cancel(self):
        return self.realtime(RT_JOG_CANCEL)

    def frame(self, bounds, feed=3000):
        """Jog around the job rectangle (work coordinates), laser off."""
        x0, y0, x1, y1 = bounds
        for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)):
            self.command(f"$J=G90 G21 X{x:.3f} Y{y:.3f} F{feed:g}")

    def feed_override(self, which):
        return self.realtime(RT_FEED[which])

    def power_override(self, which):
        return self.realtime(RT_POWER[which])

    # --------------------------------------------------------- pointer ---
    def pointer_at(self, x, y, s_value, feed=3000.0, timeout=20.0):
        """
        Moves to (x, y) (work coordinates) with the laser off, then lights a
        low-power dot to check the position: M3 (constant power) is used
        because M4 turns the laser off when the machine does not move, and
        G1 is the active motion mode (Grbl only fires in G1/G2/G3).
        The dot is switched off after `timeout` seconds, by pointer_off(),
        by any job / reset, or if the machine leaves the Idle state.
        """
        if not self.connected:
            raise RuntimeError("Not connected.")
        if self.job_running:
            raise RuntimeError("A job is running.")
        st = self.status.get("state", "")
        if st.startswith(("Alarm", "Door")):
            raise RuntimeError(f"The laser is in {st} state: unlock it first.")
        self.pointer_off()
        self._pointer_seq += 1
        seq = self._pointer_seq
        self.command(f"$J=G90 G21 X{x:.3f} Y{y:.3f} F{feed:g}")

        def run():
            t0 = time.monotonic()
            seen_motion = False
            while time.monotonic() - t0 < 300:
                if seq != self._pointer_seq or not self.connected:
                    return
                state = self.status.get("state", "")
                if state.startswith(("Jog", "Run")):
                    seen_motion = True
                if state.startswith("Idle") and (seen_motion or time.monotonic() - t0 > 0.6):
                    break
                if state.startswith(("Alarm", "Door")):
                    return
                time.sleep(0.05)
            if seq != self._pointer_seq or not self.connected or self.job_running:
                return
            try:
                self.send_line("G90 G1 F1000")          # G1 modal, no motion
                self.send_line(f"M3 S{s_value:.1f}")    # constant low power: the dot
            except Exception:
                return
            self.pointer_active = True
            self.events.put(("pointer", True))
            t1 = time.monotonic()
            while time.monotonic() - t1 < timeout:
                if seq != self._pointer_seq or not self.connected:
                    return
                state = self.status.get("state", "")
                if not state.startswith("Idle"):
                    break
                time.sleep(0.1)
            if seq == self._pointer_seq:
                self.pointer_off()
        threading.Thread(target=run, daemon=True).start()

    def pointer_off(self):
        self._pointer_seq += 1
        if self.pointer_active:
            self.pointer_active = False
            if self.connected:
                try:
                    self.send_line("M5 S0")
                except Exception:
                    pass
            self.events.put(("pointer", False))

    # ------------------------------------------------------------- job ---
    @property
    def job_running(self):
        return self._job is not None

    def start_job(self, gcode_lines):
        if not self.connected:
            raise RuntimeError("Not connected.")
        if self.job_running:
            raise RuntimeError("A job is already running.")
        self.pointer_off()
        lines = clean_gcode(gcode_lines)
        self._job = lines
        self._job_idx = 0
        self._job_acked = 0
        self._job_paused = False
        self.last_error = None
        self.events.put(("job", "started"))
        self._job_thread = threading.Thread(target=self._streamer, daemon=True)
        self._job_thread.start()

    def _streamer(self):
        total = len(self._job or [])
        last_report = 0.0
        while self._job is not None and not self._stop.is_set():
            if self._job_paused:
                time.sleep(0.05)
                continue
            if self._job_idx < total:
                line = self._job[self._job_idx]
                if not self.send_line(line, is_job=True):
                    if self._job is None:
                        break
                    continue
                self._job_idx += 1
            else:
                # everything sent: wait for the last acknowledgements and Idle
                with self._cv:
                    self._cv.wait(0.1)
                if self._job_acked >= total and self.status.get("state", "").startswith("Idle"):
                    time.sleep(0.25)
                    if self.status.get("state", "").startswith("Idle"):
                        self._job = None
                        self.events.put(("progress", (total, total)))
                        self.events.put(("job", "done"))
                        return
            now = time.monotonic()
            if now - last_report > 0.2:
                self.events.put(("progress", (self._job_acked, total)))
                last_report = now

    def pause(self):
        """Feed hold (the laser stops with the motion)."""
        return self.realtime(RT_HOLD)

    def resume(self):
        """Cycle start; also continues streaming after an error pause."""
        self._job_paused = False
        return self.realtime(RT_RESUME)

    def _abort_job(self, why):
        self._job = None
        self._job_paused = False
        with self._cv:
            self._cv.notify_all()
        self.events.put(("job", why))

    def stop_job(self, reset=True, notify=True):
        """Stops streaming. With reset, sends a soft reset (laser off, motion stopped)."""
        self._pointer_seq += 1
        self.pointer_active = False
        was = self._job is not None
        self._job = None
        self._job_paused = False
        with self._cv:
            self._cv.notify_all()
        if reset and self.connected:
            # feed hold first: a reset while moving loses the position
            # (ALARM:3); wait for the machine to stop, then reset.
            def _halt():
                self.realtime(RT_HOLD)
                t0 = time.monotonic()
                while time.monotonic() - t0 < 3.0:
                    st = self.status.get("state", "")
                    if st.startswith("Hold:0") or st.startswith("Idle"):
                        break
                    time.sleep(0.05)
                self.realtime(RT_RESET)
                self._reset_counters()
            threading.Thread(target=_halt, daemon=True).start()
        if was and notify:
            self.events.put(("job", "stopped"))

    @property
    def progress(self):
        total = len(self._job) if self._job else 0
        return self._job_acked, total
