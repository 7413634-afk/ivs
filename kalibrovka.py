import json
import os
import random

from oven import CHANNELS, SENSOR_ERR

REF_POINTS = [0.0, 100.0, 150.0, 200.0, 250.0]
CAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kalibrovka.json")


def demo_readings():
    rng = random.Random(7)
    out = {}
    for k, name in enumerate(CHANNELS):
        a, b = SENSOR_ERR[k]
        out[name] = [round(a + b * T + rng.gauss(0, 0.15 / 60 ** 0.5), 3) for T in REF_POINTS]
    return out


def fit_line(x, y):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    b = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / sum((xi - mx) ** 2 for xi in x)
    return my - b * mx, b


def calibrate(readings=None):
    readings = readings or demo_readings()
    result = {}
    for name in CHANNELS:
        shown = readings[name]
        a, b = fit_line(shown, REF_POINTS)
        corrected = [a + b * T for T in shown]
        result[name] = {
            "a": round(a, 4),
            "b": round(b, 6),
            "errors": [round(s - r, 2) for s, r in zip(shown, REF_POINTS)],
            "max_resid": round(max(abs(c - r) for c, r in zip(corrected, REF_POINTS)), 3),
        }
    with open(CAL_FILE, "w", encoding="utf-8") as f:
        json.dump({"ref_points": REF_POINTS, "formula": "T' = a + b*T", "channels": result},
                  f, ensure_ascii=False, indent=2)
    return result


def load_calibration():
    if not os.path.exists(CAL_FILE):
        return {}
    with open(CAL_FILE, encoding="utf-8") as f:
        return json.load(f)["channels"]


def correct(name, T, cal):
    if T is None or name not in cal:
        return T
    return cal[name]["a"] + cal[name]["b"] * T


if __name__ == "__main__":
    for name, r in calibrate().items():
        print(name, r["errors"], r["a"], r["b"], r["max_resid"])
