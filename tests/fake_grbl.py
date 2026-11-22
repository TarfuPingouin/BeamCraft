"""A tiny fake Grbl v1.1 on a pseudo-terminal, for tests (Linux / macOS)."""

import os
import re
import threading
import time


class FakeGrbl:
    def __init__(self, exec_time=0.002, fail_on=None):
        self.master, slave = os.openpty()
        self.port = os.ttyname(slave)
        self._slave = slave
        self.exec_time = exec_time
        self.fail_on = fail_on or set()
        self.received = []
        self.max_buffer = 0
        self.state = "Idle"
        self.power = 0.0
        self.motion = 0
        self.pos = [0.0, 0.0]
        self.wco = [0.0, 0.0]
        self.realtime = []
        self._rx = b""
        self._queue = []
        self._stop = False
        self._lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._exec, daemon=True).start()

    def _send(self, txt):
        os.write(self.master, (txt + "\r\n").encode())

    def _read(self):
        while not self._stop:
            try:
                data = os.read(self.master, 1024)
            except OSError:
                return
            for b in data:
                ch = bytes([b])
                if ch == b"?":
                    self._send(f"<{self.state}|MPos:{self.pos[0]:.3f},{self.pos[1]:.3f},0.000|FS:1000,{self.power if self.motion else 0:g}"
                               f"|WCO:{self.wco[0]:.3f},{self.wco[1]:.3f},0.000>")
                elif ch in (b"!", b"~", b"\x18", b"\x85") or b >= 0x80:
                    self.realtime.append(ch)
                    if ch == b"!":
                        self.state = "Hold:0"
                    elif ch == b"~":
                        self.state = "Run" if self._queue else "Idle"
                    elif ch == b"\x18":
                        with self._lock:
                            self._queue.clear()
                            self._rx = b""
                        self.state = "Idle"
                        self._send("Grbl 1.1h ['$' for help]")
                else:
                    with self._lock:
                        self._rx += ch
                        while b"\n" in self._rx:
                            line, self._rx = self._rx.split(b"\n", 1)
                            self._queue.append(line.decode().strip())
                        # bytes received but not yet acknowledged (= Grbl RX buffer use)
                        used = len(self._rx) + sum(len(q) + 1 for q in self._queue)
                        self.max_buffer = max(self.max_buffer, used)

    def _exec(self):
        while not self._stop:
            if self.state.startswith("Hold"):
                time.sleep(0.005)
                continue
            with self._lock:
                line = self._queue.pop(0) if self._queue else None
            if line is None:
                if self.state == "Run":
                    self.state = "Idle"
                time.sleep(0.002)
                continue
            if not line:
                continue
            self.state = "Run"
            time.sleep(self.exec_time)
            self.received.append(line)
            if line == "$$":
                for k, v in ((11, 0.01), (12, 0.002), (30, 1000), (32, 1), (110, 5000), (111, 4000),
                             (120, 800), (121, 600)):
                    self._send(f"${k}={v}")
                self._send("ok")
                continue
            if line in self.fail_on:
                self._send("error:20")
                continue
            if line.startswith("G10"):
                self.wco = list(self.pos)
            sm = re.search(r"S(\d*\.?\d+)", line)
            if sm:
                self.power = float(sm.group(1))
            gm = re.findall(r"G([0-3])(?![0-9.])", line)
            if gm:
                self.motion = int(gm[-1])
            if line.startswith("$J"):
                self.motion = 0
            m = re.findall(r"([XY])(-?\d*\.?\d+)", line)
            rel = "G91" in line
            for ax, v in m:
                i = 0 if ax == "X" else 1
                if rel:
                    self.pos[i] += float(v)
                else:
                    self.pos[i] = float(v) + self.wco[i]
            self._send("ok")

    def close(self):
        self._stop = True
        try:
            os.close(self.master)
            os.close(self._slave)
        except OSError:
            pass
