import csv
import glob
import os

from kalibrovka import load_calibration, correct

AIR = ["П1", "П2", "П3", "П4", "П5", "П6", "П7", "П8", "П9"]
PART_NAMES = ["Лист", "Труба", "Опора"]

PAINT_T = 190.0
U = 1.9
LIMIT = PAINT_T + U
HOLD_MIN, HOLD_MAX = 15.0, 25.0
STEADY_MIN = 15
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def to_float(s):
    s = (s or "").strip().replace(",", ".")
    if s == "" or s.lower() == "nan":
        return None
    return float(s)


def load_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))
    header, rows = rows[0], rows[1:]
    data = {name: [] for name in header}
    for r in rows:
        for name, cell in zip(header, r):
            data[name].append(to_float(cell))
    return data


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def newest_file():
    files = sorted(glob.glob(os.path.join(DATA_DIR, "*.csv")), key=os.path.getmtime)
    return files[-1] if files else None


def analyze(path, use_cal=True):
    data = load_csv(path)
    t = data["t_s"]
    cal = load_calibration() if use_cal else {}
    for name in AIR + PART_NAMES:
        if name in data:
            data[name] = [correct(name, T, cal) for T in data[name]]

    res = {"file": os.path.basename(path), "calibrated": bool(cal), "duration_min": t[-1] / 60}

    idx = [i for i, ti in enumerate(t) if ti >= t[-1] - STEADY_MIN * 60]
    points = {}
    for name in AIR:
        xs = [data[name][i] for i in idx if data[name][i] is not None]
        if xs:
            points[name] = {"mean": mean(xs), "min": min(xs), "max": max(xs)}
    res["points"] = points
    means = {n: p["mean"] for n, p in points.items()}
    res["T_mean"] = mean(list(means.values()))
    res["coldest"] = min(means, key=means.get)
    res["hottest"] = max(means, key=means.get)
    res["dT"] = means[res["hottest"]] - means[res["coldest"]]
    res["cold_points"] = [n for n, p in points.items() if p["min"] < LIMIT]
    ctrl = mean([data["Регулятор"][i] for i in idx]) if "Регулятор" in data else None
    res["ctrl"] = ctrl
    res["ctrl_dev"] = (ctrl - res["T_mean"]) if ctrl is not None else None

    load_i = None
    for i in range(len(t)):
        if any(data[p][i] is not None and data[p][0] is not None and data[p][i] > data[p][0] + 5
               for p in PART_NAMES if p in data):
            load_i = i
            break
    res["load_min"] = t[load_i] / 60 if load_i is not None else None

    parts = {}
    dt_s = (t[-1] - t[0]) / (len(t) - 1)
    for p in PART_NAMES:
        if p not in data or load_i is None:
            continue
        xs = data[p][load_i:]
        reach = next((k for k, x in enumerate(xs) if x is not None and x >= LIMIT), None)
        above_min = sum(1 for x in xs if x is not None and x >= LIMIT) * dt_s / 60
        if above_min < HOLD_MIN:
            verdict = "Не догрітий"
        elif above_min > HOLD_MAX:
            verdict = "Перетримка"
        else:
            verdict = "Норма"
        parts[p] = {"reach_min": reach * dt_s / 60 if reach is not None else None,
                    "above_min": above_min,
                    "Tmax": max((x for x in xs if x is not None), default=float("nan")),
                    "verdict": verdict}
    res["parts"] = parts
    res["ok"] = all(v["verdict"] == "Норма" for v in parts.values()) and not res["cold_points"]
    return res
