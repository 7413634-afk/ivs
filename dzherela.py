"""Джерела даних ІВС. Розрахункова частина отримує однакові рядки Row незалежно від джерела.

  SimSource   — фізична модель печі + модель 12 вимірювальних каналів (режим "sim");
  Esp32Source — реальна ESP32 + 12 × MAX31856 через USB (режим "real").
"""
import time
from collections import namedtuple

from konfig import CHANNELS
from model_pechi import Oven, T_CEH
from vymiriuvannia import Hardware, MeasuringSystem

# t — с від початку; values — 12 показів, °C (None = обрив/помилка); ctrl — показ регулятора або None;
# true — дійсні температури (лише симуляція, для перевірки покриття U); cj — температури ХС
Row = namedtuple("Row", "t values ctrl true cj")

HW_SEED = 2026          # «екземпляр» ІВС: ті самі датчики під час калібрування і вимірювань


class SimSource:
    mode = "sim"

    def __init__(self, params, seed=1, t_ceh=T_CEH, age_years=0.02, phys=None):
        self.oven = Oven(params, seed=seed, t_ceh=t_ceh, phys=phys)
        self.meas = MeasuringSystem(Hardware(HW_SEED), seed=seed + 1000, age_years=age_years)
        self.finished = False
        self.total_min = self.oven.total_seconds() / 60

    def read_batch(self, steps):
        rows = []
        for _ in range(steps):
            if self.oven.t >= self.oven.total_seconds():
                self.finished = True
                break
            self.oven.step()
            st = self.oven.true_state()
            vals, cjs = self.meas.read(st)
            true = list(st["air"]) + [st["parts"][p][0] if st["parts"][p] else None for p in st["parts"]]
            rows.append(Row(st["t"], vals, round(st["ctrl"], 2), true, cjs))
        return rows

    def close(self):
        pass


def parse_line(line):
    """Рядок прошивки: D;<мс>;T1..T12;CJ1..CJ12;<маска помилок>. Повертає (мс, 12 T, 12 CJ) або None."""
    line = line.strip()
    if not line.startswith("D;"):
        return None
    f = line.split(";")
    if len(f) != 2 + 2 * len(CHANNELS) + 1:
        return None
    fault = int(f[-1], 16)

    def num(s):
        s = s.strip().replace(",", ".")
        return None if s in ("", "nan", "NaN") else float(s)

    T = [None if fault >> i & 1 else num(s) for i, s in enumerate(f[2:2 + len(CHANNELS)])]
    cj = [num(s) for s in f[2 + len(CHANNELS):-1]]
    return int(f[1]), T, cj


class Esp32Source:
    """Читання ESP32 через віртуальний COM-порт USB (pyserial). Регулятор печі не опитується."""
    mode = "real"

    def __init__(self, params, port, baud=115200, total_min=None, serial_obj=None):
        if serial_obj is None:
            import serial                         # pip install pyserial
            serial_obj = serial.Serial(port, baud, timeout=0.05)
        self.ser = serial_obj
        self.finished = False
        self.total_min = total_min or (params["load_min"] + params["after_load_min"])
        self.t0 = None
        self.t = 0

    def read_batch(self, steps):
        rows = []
        deadline = time.monotonic() + 0.2
        while len(rows) < steps and time.monotonic() < deadline:
            raw = self.ser.readline()
            if not raw:
                break
            p = parse_line(raw.decode("ascii", "replace"))
            if p is None:
                continue
            ms, T, cj = p
            if self.t0 is None:
                self.t0 = ms
            self.t = round((ms - self.t0) / 1000) + 1
            rows.append(Row(self.t, T, None, None, cj))
            if self.t >= self.total_min * 60:
                self.finished = True
                break
        return rows

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


def make_source(mode, params, **kw):
    if mode == "sim":
        return SimSource(params, **{k: v for k, v in kw.items() if k in ("seed", "t_ceh", "age_years", "phys")})
    if mode == "real":
        return Esp32Source(params, kw["port"], kw.get("baud", 115200))
    raise ValueError(mode)
