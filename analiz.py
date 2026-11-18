"""Аналіз циклу: просторова неоднорідність Δt, часова нестабільність σ_t, час прогріву і відновлення,
досягнення порогу та максимальна неперервна витримка виробів. Розрахунок не залежить від джерела даних."""
import csv
import glob
import math
import os

from konfig import AIR, PART_NAMES, PAINT, CTRL, read_json
from kalibrovka import load_calibration, correct

STEADY_MIN = 15          # вікно сталого режиму — останні 15 хв циклу
HEAT_TOL = 2.0           # прогрів/відновлення: усі точки в межах ±2 °C від сталих середніх
DEFAULT_U_METAL = 1.9    # лише для старих файлів без бюджету невизначеності


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


def meta_path(csv_path):
    return os.path.splitext(csv_path)[0] + ".json"


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def std(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def runs_above(xs, limit):
    """Неперервні інтервали (початок, кінець) індексів, де xs ≥ limit."""
    out, start = [], None
    for i, x in enumerate(xs):
        if x is not None and x >= limit:
            if start is None:
                start = i
        elif start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(xs)))
    return out


def settle_index(series, targets, tol, i0=0, i1=None):
    """Перший індекс ≥ i0, після якого всі ряди до i1 лишаються в межах ±tol від своїх targets."""
    i1 = len(series[0]) if i1 is None else i1
    last_bad = i0 - 1
    for i in range(i0, i1):
        if any(s[i] is None or abs(s[i] - tg) > tol for s, tg in zip(series, targets)):
            last_bad = i
    k = last_bad + 1
    return k if k < i1 else None


def verdict(hold, lo=PAINT["hold_min"], hi=PAINT["hold_max"]):
    if hold < lo:
        return "Не догрітий"
    if hold > hi:
        return "Перетримка"
    return "Норма"


def newest_file(folder):
    files = sorted(glob.glob(os.path.join(folder, "*.csv")), key=os.path.getmtime)
    files = [f for f in files if not f.endswith("_true.csv")]
    return files[-1] if files else None


def analyze(path, use_cal=True, mode=None, threshold=None, U_air=None, meta=None):
    meta = meta or read_json(meta_path(path), {}) or {}
    mode = mode or meta.get("mode", "sim")
    params = meta.get("params", {})
    if threshold is None:
        threshold = meta.get("threshold", PAINT["T_metal"] + DEFAULT_U_METAL)
    if U_air is None:
        U_air = meta.get("U_air", DEFAULT_U_METAL)
    data = load_csv(path)
    t = data["t_s"]
    cal = load_calibration(mode) if use_cal else {}
    for name in AIR + PART_NAMES:
        if name in data:
            data[name] = [correct(name, T, cal) for T in data[name]]
    dt_s = (t[-1] - t[0]) / (len(t) - 1)
    empty = bool(params.get("empty")) or not any(p in data for p in PART_NAMES)

    res = {"file": os.path.basename(path), "mode": mode, "calibrated": bool(cal), "empty": empty,
           "duration_min": t[-1] / 60, "threshold": threshold, "U_air": U_air,
           "air_limit": PAINT["T_metal"] + U_air}

    idx = [i for i, ti in enumerate(t) if ti > t[-1] - STEADY_MIN * 60]
    points = {}
    for name in AIR:
        xs = [data[name][i] for i in idx]
        if any(x is not None for x in xs):
            vals = [x for x in xs if x is not None]
            points[name] = {"mean": mean(vals), "min": min(vals), "max": max(vals), "sigma": std(vals)}
    res["points"] = points
    means = {n: p["mean"] for n, p in points.items()}
    res["T_mean"] = mean(list(means.values()))
    res["coldest"] = min(means, key=means.get)
    res["hottest"] = max(means, key=means.get)
    res["dT"] = means[res["hottest"]] - means[res["coldest"]]
    spreads = [max(v) - min(v) for v in ([data[n][i] for n in points] for i in idx) if None not in v]
    res["dT_inst"] = max(spreads) if spreads else None
    res["sigma_t"] = max(p["sigma"] for p in points.values())
    res["sigma_t_point"] = max(points, key=lambda n: points[n]["sigma"])
    res["cold_points"] = [n for n, p in points.items() if p["min"] < res["air_limit"]]
    ctrl = mean([data[CTRL][i] for i in idx]) if CTRL in data else None
    res["ctrl"] = ctrl
    res["ctrl_dev"] = (ctrl - res["T_mean"]) if ctrl is not None else None

    # момент завантаження
    load_i = None
    if not empty:
        if "load_min" in params:
            load_i = next((i for i, ti in enumerate(t) if ti >= params["load_min"] * 60), None)
        else:
            for i in range(len(t)):
                if any(data[p][i] is not None and data[p][0] is not None and data[p][i] > data[p][0] + 5
                       for p in PART_NAMES if p in data):
                    load_i = i
                    break
    res["load_min"] = t[load_i] / 60 if load_i is not None else None

    series = [data[n] for n in points]
    targets = [means[n] for n in points]
    k = settle_index(series, targets, HEAT_TOL, 0, load_i)
    res["heat_min"] = t[k] / 60 if k is not None else None
    res["recover_min"] = None
    if load_i is not None:
        k = settle_index(series, targets, HEAT_TOL, load_i)
        res["recover_min"] = (t[k] - t[load_i]) / 60 if k is not None else None

    parts = {}
    for p in PART_NAMES:
        if p not in data or load_i is None:
            continue
        xs = data[p][load_i:]
        runs = runs_above(xs, threshold)
        hold = max(((b - a) * dt_s / 60 for a, b in runs), default=0.0)
        vals = [x for x in xs if x is not None]
        parts[p] = {"reach_min": runs[0][0] * dt_s / 60 if runs else None,
                    "hold_min": hold,
                    "above_min": sum(b - a for a, b in runs) * dt_s / 60,
                    "Tmax": max(vals) if vals else float("nan"),
                    "verdict": verdict(hold)}
    res["parts"] = parts
    res["ok"] = (not empty and bool(parts) and all(v["verdict"] == "Норма" for v in parts.values())
                 and not res["cold_points"])
    return res


def coverage(path, res, budget, use_cal=True):
    """Лише для симуляції: похибка (виправлений показ − дійсне значення) порівнюється з U.
    Повітря — у вікні сталого режиму, метал — від досягнення порогу до кінця циклу."""
    true_path = os.path.splitext(path)[0] + "_true.csv"
    if not os.path.exists(true_path) or budget is None:
        return None
    data, true = load_csv(path), load_csv(true_path)
    cal = load_calibration(res["mode"]) if use_cal else {}
    t = data["t_s"]
    idx_air = [i for i, ti in enumerate(t) if ti > t[-1] - STEADY_MIN * 60]
    out = {}
    for group, names in (("air", AIR), ("metal", PART_NAMES)):
        errs = []
        for n in names:
            if n not in true:
                continue
            if group == "air":
                ii = idx_air
            else:
                if n not in res["parts"] or res["parts"][n]["reach_min"] is None:
                    continue
                i0 = next(i for i, ti in enumerate(t) if ti >= (res["load_min"] + res["parts"][n]["reach_min"]) * 60)
                ii = range(i0, len(t))
            for i in ii:
                m, tr = correct(n, data[n][i], cal), true[n][i]
                if m is not None and tr is not None:
                    errs.append(m - tr)
        if errs:
            U = budget[group]["U"]
            out[group] = {"n": len(errs), "mean": mean(errs), "std": std(errs),
                          "max_abs": max(abs(e) for e in errs), "U": U,
                          "within_U": sum(abs(e) <= U for e in errs) / len(errs)}
    return out
