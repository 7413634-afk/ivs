import csv
import os

import matplotlib
from matplotlib.figure import Figure

from analiz import analyze, load_csv, AIR, PART_NAMES, LIMIT
from kalibrovka import load_calibration, correct

matplotlib.rcParams["font.family"] = "serif"
matplotlib.rcParams["font.serif"] = ["Times New Roman", "DejaVu Serif"]
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zvit")


def c(x):
    return f"{x:.1f}".replace(".", ",")


def make_report(csv_path, use_cal=True, params=None):
    res = analyze(csv_path, use_cal)
    folder = os.path.join(OUT_DIR, os.path.splitext(os.path.basename(csv_path))[0])
    os.makedirs(folder, exist_ok=True)

    data = load_csv(csv_path)
    cal = load_calibration() if use_cal else {}
    t_min = [t / 60 for t in data["t_s"]]

    def fix(n):
        return [correct(n, T, cal) for T in data[n]]

    fig = Figure(figsize=(10, 5), dpi=150)
    ax = fig.add_subplot(111)
    for n in AIR:
        ax.plot(t_min, fix(n), lw=1, label=n)
    if any(v is not None for v in data.get("Регулятор", [])):
        ax.plot(t_min, data["Регулятор"], "k--", lw=1.4, label="Регулятор")
    ax.axhline(LIMIT, color="red", ls=":", lw=1, label="191,9 °C")
    ax.set(xlabel="Час, хв", ylabel="Температура, °C", title="Температура повітря в точках П1–П9")
    ax.grid(alpha=0.3)
    ax.legend(ncol=6, fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(os.path.join(folder, "1_povitria.png"))

    fig = Figure(figsize=(10, 5), dpi=150)
    ax = fig.add_subplot(111)
    for n in PART_NAMES:
        if n in data:
            ax.plot(t_min, fix(n), lw=2, label=n)
    ax.axhline(LIMIT, color="red", ls=":", lw=1, label="191,9 °C")
    if res["load_min"] is not None:
        ax.axvline(res["load_min"], color="grey", lw=1)
        ax.text(res["load_min"], 205, " завантаження", color="grey", fontsize=8, va="top")
    ax.set(xlabel="Час, хв", ylabel="Температура металу, °C", title="Нагрів виробів")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(os.path.join(folder, "2_vyroby.png"))

    fig = Figure(figsize=(9, 4.5), dpi=150)
    ax = fig.add_subplot(111)
    names = list(res["points"])
    means = [res["points"][n]["mean"] for n in names]
    lo = [m - res["points"][n]["min"] for n, m in zip(names, means)]
    hi = [res["points"][n]["max"] - m for n, m in zip(names, means)]
    colors = ["#c0392b" if n in res["cold_points"] else "#2e86c1" for n in names]
    ax.bar(names, means, color=colors, yerr=[lo, hi], capsize=4)
    ax.axhline(LIMIT, color="red", ls=":", lw=1.2, label="191,9 °C")
    mins = [res["points"][n]["min"] for n in names]
    maxs = [res["points"][n]["max"] for n in names]
    ax.set_ylim(min(min(mins), LIMIT) - 3, max(maxs) + 3)
    for i, m in enumerate(means):
        ax.text(i, m + hi[i] + 0.3, c(m), ha="center", fontsize=8)
    ax.set(ylabel="°C", title="Середня температура точок в усталеному режимі")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(folder, "3_tochky.png"))

    with open(os.path.join(folder, "povitria.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Точка", "Середня, °C", "Мін, °C", "Макс, °C", "Стан"])
        for n, p in res["points"].items():
            w.writerow([n, c(p["mean"]), c(p["min"]), c(p["max"]),
                        "нижче 191,9" if n in res["cold_points"] else "норма"])
        w.writerow([])
        w.writerow(["Середня температура, °C", c(res["T_mean"])])
        w.writerow(["Нерівномірність поля, °C", c(res["dT"])])
        if res["ctrl"] is not None:
            w.writerow(["Регулятор, °C", c(res["ctrl"])])
            w.writerow(["Відхилення регулятора, °C", f"{res['ctrl_dev']:+.1f}".replace(".", ",")])

    with open(os.path.join(folder, "vyroby.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Виріб", "Досяг 191,9 °C через, хв", "Час ≥ 191,9 °C, хв", "Макс, °C", "Стан"])
        for p, v in res["parts"].items():
            reach = c(v["reach_min"]) if v["reach_min"] is not None else "не досяг"
            w.writerow([p, reach, c(v["above_min"]), "—" if v["Tmax"] != v["Tmax"] else c(v["Tmax"]), v["verdict"]])

    if params:
        with open(os.path.join(folder, "parametry.csv"), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Параметр", "Значення"])
            w.writerow(["Уставка регулятора, °C", f"{params['setpoint']:g}"])
            w.writerow(["Завантаження виробів, хв", params["load_min"]])
            w.writerow(["Відкриття дверей, с", params["door_s"]])
            w.writerow(["Тривалість після завантаження, хв", params["after_load_min"]])
            for part in PART_NAMES:
                w.writerow([f"Розміщення: {part}", f"П{params[part]}"])

    return folder
