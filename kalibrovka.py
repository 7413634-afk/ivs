"""Калібрування 12 каналів у точках 50, 100, 150, 200, 250 °C. Поправка: T' = a + b·T.

Симуляція: усі 12 термопар у блоці сухоблочного калібратора разом з еталонним Pt100,
у кожній точці n відліків з частотою 1 Гц. Реальні вимірювання: імпорт CSV
(T_еталон;П1;…;Опора — один рядок на відлік).
"""
import csv
import math
import random

from konfig import CHANNELS, cal_file, read_json, write_json
from vymiriuvannia import Hardware, Channel, MEAS

REF_POINTS = [50.0, 100.0, 150.0, 200.0, 250.0]
CAL = {
    "n_samples": 60,          # відліків у кожній точці (1 хв при 1 Гц)
    "U_ref": 0.10,            # розширена невизначеність еталона Pt100 + вимірювача, °C (k = 2)
    "block_nonunif": 0.05,    # межа неоднорідності поля блоку калібратора, °C
    "block_stab": 0.02,       # межа нестабільності калібратора за час відліку, °C
}


def mean(xs):
    return sum(xs) / len(xs)


def stdev(xs):
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def fit_line(x, y):
    """МНК: y = a + b·x."""
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    b = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / sum((xi - mx) ** 2 for xi in x)
    return my - b * mx, b


def simulate_readings(seed=7, hw_seed=None):
    """Модельне калібрування: повертає {"ref": [...], "samples": {канал: [[відліки точки 1], ...]}}."""
    from dzherela import HW_SEED
    hw = Hardware(hw_seed or HW_SEED)
    rng = random.Random(seed)
    ref_meas, samples = [], {n: [] for n in CHANNELS}
    chans = {n: Channel(n, hw, rng) for n in CHANNELS}
    for T in REF_POINTS:
        T_block = T + rng.uniform(-CAL["block_stab"], CAL["block_stab"])
        ref_meas.append(T_block + rng.gauss(0, CAL["U_ref"] / 2))
        for n in CHANNELS:
            T_well = T_block + rng.uniform(-CAL["block_nonunif"], CAL["block_nonunif"])
            samples[n].append([chans[n].max31856(T_well, MEAS["T_cj_cal"])[0] for _ in range(CAL["n_samples"])])
    return {"ref": ref_meas, "samples": samples}


def readings_from_csv(path):
    """Реальне калібрування: CSV «T_еталон;П1;…;Опора», відліки групуються за найближчою точкою REF_POINTS."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))
    head, rows = rows[0], rows[1:]
    idx = {h.strip(): i for i, h in enumerate(head)}
    groups = {T: [] for T in REF_POINTS}
    for r in rows:
        if not r or not r[0].strip():
            continue
        t_ref = float(r[0].replace(",", "."))
        groups[min(REF_POINTS, key=lambda T: abs(T - t_ref))].append(r)
    ref, samples = [], {n: [] for n in CHANNELS}
    for T in REF_POINTS:
        g = groups[T]
        if not g:
            raise ValueError(f"немає відліків для точки {T} °C")
        ref.append(mean([float(r[0].replace(",", ".")) for r in g]))
        for n in CHANNELS:
            samples[n].append([float(r[idx[n]].replace(",", ".")) for r in g])
    return {"ref": ref, "samples": samples}


def calibrate(readings=None, mode="sim", save=True):
    readings = readings or simulate_readings()
    ref = readings["ref"]
    result = {}
    for name in CHANNELS:
        pts = readings["samples"][name]
        shown = [mean(p) for p in pts]
        s_pts = [stdev(p) for p in pts]
        n = len(pts[0])
        a, b = fit_line(shown, ref)
        resid = [a + b * x - r for x, r in zip(shown, ref)]
        result[name] = {
            "a": round(a, 5), "b": round(b, 7),
            "shown": [round(x, 4) for x in shown],
            "errors": [round(x - r, 3) for x, r in zip(shown, ref)],
            "s": [round(s, 4) for s in s_pts],
            "s_pooled": round(math.sqrt(mean([s * s for s in s_pts])), 4),
            "n": n,
            "resid": [round(x, 4) for x in resid],
            "s_res": round(math.sqrt(sum(x * x for x in resid) / (len(ref) - 2)), 4),
            "max_resid": round(max(abs(x) for x in resid), 4),
        }
    data = {"mode": mode, "ref_points": REF_POINTS, "ref_measured": [round(x, 4) for x in ref],
            "formula": "T' = a + b*T", "conditions": CAL, "channels": result}
    if save:
        write_json(cal_file(mode), data)
    return result


def load_calibration(mode="sim", full=False):
    d = read_json(cal_file(mode))
    if d is None:
        return {}
    return d if full else d["channels"]


def correct(name, T, cal):
    if T is None or name not in cal:
        return T
    return cal[name]["a"] + cal[name]["b"] * T


if __name__ == "__main__":
    for name, r in calibrate().items():
        print(name, r["errors"], r["a"], r["b"], r["s_res"])
