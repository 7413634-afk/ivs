"""Звіти: графіки PNG і таблиці CSV для окремого циклу та для серії експериментів."""
import csv
import math
import os

import matplotlib
from matplotlib.figure import Figure

from analiz import analyze, load_csv, mean, std
from konfig import AIR, PART_NAMES, CHANNELS, CTRL, PAINT, WHERE, report_dir
from kalibrovka import load_calibration, correct, REF_POINTS

matplotlib.rcParams["font.family"] = "serif"
matplotlib.rcParams["font.serif"] = ["Times New Roman", "DejaVu Serif"]
OUT_DIR = report_dir("sim")      # сумісність зі старим інтерфейсом


def c(x, d=1):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:.{d}f}".replace(".", ",")


def table(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f, delimiter=";").writerows(rows)


def make_report(csv_path, use_cal=True, params=None, res=None, out=None):
    res = res or analyze(csv_path, use_cal)
    mode = res["mode"]
    folder = out or os.path.join(report_dir(mode), os.path.splitext(os.path.basename(csv_path))[0])
    os.makedirs(folder, exist_ok=True)
    data = load_csv(csv_path)
    cal = load_calibration(mode) if use_cal else {}
    t_min = [t / 60 for t in data["t_s"]]
    thr = res["threshold"]
    thr_txt = c(thr, 2) + " °C"

    def fix(n):
        return [correct(n, T, cal) for T in data[n]]

    fig = Figure(figsize=(10, 5), dpi=150)
    ax = fig.add_subplot(111)
    for n in AIR:
        ax.plot(t_min, fix(n), lw=1, label=n)
    if any(v is not None for v in data.get(CTRL, [])):
        ax.plot(t_min, data[CTRL], "k--", lw=1.4, label="Регулятор")
    ax.axhline(res["air_limit"], color="red", ls=":", lw=1, label=c(res["air_limit"], 2) + " °C")
    ax.set(xlabel="Час, хв", ylabel="Температура, °C", title="Температура повітря в точках П1–П9")
    ax.grid(alpha=0.3)
    ax.legend(ncol=6, fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(os.path.join(folder, "1_povitria.png"))

    if not res["empty"]:
        fig = Figure(figsize=(10, 5), dpi=150)
        ax = fig.add_subplot(111)
        for n in PART_NAMES:
            if n in data:
                ax.plot(t_min, fix(n), lw=2, label=n)
        ax.axhline(thr, color="red", ls=":", lw=1, label="поріг " + thr_txt)
        if res["load_min"] is not None:
            ax.axvline(res["load_min"], color="grey", lw=1)
            ax.text(res["load_min"], thr + 10, " завантаження", color="grey", fontsize=8, va="top")
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
    ax.axhline(res["air_limit"], color="red", ls=":", lw=1.2, label=c(res["air_limit"], 2) + " °C")
    ax.set_ylim(min(min(m - l for m, l in zip(means, lo)), res["air_limit"]) - 3, max(m + h for m, h in zip(means, hi)) + 3)
    for i, m in enumerate(means):
        ax.text(i, m + hi[i] + 0.3, c(m), ha="center", fontsize=8)
    ax.set(ylabel="°C", title=f"Середня температура точок у сталому режимі (Δt = {c(res['dT'])} °C)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(folder, "3_tochky.png"))

    rows = [["Точка", "Розташування", "Середня, °C", "Мін, °C", "Макс, °C", "σ_t, °C", "Стан"]]
    for n, p in res["points"].items():
        rows.append([n, WHERE[AIR.index(n)], c(p["mean"], 2), c(p["min"], 2), c(p["max"], 2), c(p["sigma"], 3),
                     "нижче " + c(res["air_limit"], 2) if n in res["cold_points"] else "норма"])
    table(os.path.join(folder, "povitria.csv"), rows)

    rows = [["Показник", "Значення"],
            ["Середня температура повітря, °C", c(res["T_mean"], 2)],
            ["Просторова неоднорідність Δt (середні), °C", c(res["dT"], 2)],
            ["Найбільший миттєвий розкид, °C", c(res["dT_inst"], 2)],
            ["Часова нестабільність σ_t (макс.), °C", c(res["sigma_t"], 3) + f" ({res['sigma_t_point']})"],
            ["Час прогріву (±2 °C від сталих значень), хв", c(res["heat_min"])],
            ["Відновлення після завантаження, хв", c(res["recover_min"])],
            ["U повітря (k = 2), °C", c(res["U_air"], 2)],
            ["Поріг початку витримки, °C", c(thr, 2)]]
    if res["ctrl"] is not None:
        rows += [["Регулятор, °C", c(res["ctrl"], 2)], ["Відхилення регулятора від середньої, °C", c(res["ctrl_dev"], 2)]]
    table(os.path.join(folder, "pokaznyky.csv"), rows)

    if res["parts"]:
        rows = [["Виріб", f"Досяг {thr_txt} через, хв", "Макс. неперервна витримка, хв", "Сумарно вище порогу, хв",
                 "Макс, °C", f"Стан (норма {c(PAINT['hold_min'], 0)}–{c(PAINT['hold_max'], 0)} хв)"]]
        for p, v in res["parts"].items():
            rows.append([p, c(v["reach_min"]) if v["reach_min"] is not None else "не досяг", c(v["hold_min"]),
                         c(v["above_min"]), c(v["Tmax"]), v["verdict"]])
        table(os.path.join(folder, "vyroby.csv"), rows)

    if params:
        rows = [["Параметр", "Значення"], ["Уставка регулятора, °C", f"{params['setpoint']:g}"]]
        if params.get("empty"):
            rows.append(["Тривалість циклу (порожня камера), хв", params.get("empty_min", 90)])
        else:
            rows += [["Завантаження виробів, хв", params["load_min"]], ["Відкриття дверей, с", params["door_s"]],
                     ["Тривалість після завантаження, хв", params["after_load_min"]]]
            rows += [[f"Розміщення: {part}", f"П{params[part]}"] for part in PART_NAMES]
        table(os.path.join(folder, "parametry.csv"), rows)
    return folder


# ---------- серія ----------

def calibration_tables(folder, cal):
    rows = [["Канал"] + [f"Δ при {int(T)} °C до, °C" for T in REF_POINTS]
            + [f"Δ при {int(T)} °C після, °C" for T in REF_POINTS] + ["a, °C", "b", "s (1 відлік), °C", "s_зал, °C"]]
    for n in CHANNELS:
        r = cal[n]
        rows.append([n] + [c(e, 3) for e in r["errors"]] + [c(e, 3) for e in r["resid"]]
                    + [c(r["a"], 4), c(r["b"], 6), c(r["s_pooled"], 4), c(r["s_res"], 4)])
    table(os.path.join(folder, "kalibrovka.csv"), rows)

    fig = Figure(figsize=(10, 4.5), dpi=150)
    ax1, ax2 = fig.add_subplot(121), fig.add_subplot(122)
    for n in CHANNELS:
        ax1.plot(REF_POINTS, cal[n]["errors"], marker="o", lw=1, label=n)
        ax2.plot(REF_POINTS, cal[n]["resid"], marker="o", lw=1)
    ax1.set(title="Похибка каналів до калібрування", xlabel="T еталона, °C", ylabel="°C")
    ax2.set(title="Залишкова похибка після поправки", xlabel="T еталона, °C")
    for ax in (ax1, ax2):
        ax.grid(alpha=0.3)
    ax1.legend(fontsize=6, ncol=3)
    fig.tight_layout()
    fig.savefig(os.path.join(folder, "4_kalibrovka.png"))


def budget_tables(folder, b):
    for group, title in (("air", "povitria"), ("metal", "metal")):
        ch = b["channels"][b[group]["worst"]]
        rows = [["Позначення", "Джерело", "Тип", "Розподіл", "Межа / оцінка, °C", "u_i, °C", "Внесок u_i², %", "ν_i"]]
        tot = sum(r["u"] ** 2 for r in ch["rows"])
        for r in ch["rows"]:
            rows.append([r["sym"], r["name"], r["type"], r["dist"], c(r["limit"], 4), c(r["u"], 4),
                         c(100 * r["u"] ** 2 / tot, 1), "∞" if r["nu"] == math.inf else int(r["nu"])])
        rows += [[], ["u_c, °C", c(ch["uc"], 4)], ["k", c(b["k"], 0)], ["U, °C", c(ch["U"], 3)],
                 ["ν_eff", "∞" if ch["nu_eff"] == math.inf else int(ch["nu_eff"])],
                 ["Канал з найбільшою U", b[group]["worst"]]]
        table(os.path.join(folder, f"neviznachenist_{title}.csv"), rows)
    rows = [["Канал", "Група", "u_c, °C", "U (k = 2), °C"]]
    for n in CHANNELS:
        ch = b["channels"][n]
        rows.append([n, "повітря" if ch["group"] == "air" else "метал", c(ch["uc"], 4), c(ch["U"], 3)])
    table(os.path.join(folder, "neviznachenist_kanaly.csv"), rows)

    fig = Figure(figsize=(10, 4), dpi=150)
    for k, group in enumerate(("air", "metal")):
        ax = fig.add_subplot(1, 2, k + 1)
        ch = b["channels"][b[group]["worst"]]
        ax.barh([r["sym"] for r in ch["rows"]], [r["u"] for r in ch["rows"]], color="#2e86c1")
        ax.invert_yaxis()
        ax.set(title=f"{'Повітря' if group == 'air' else 'Метал'} ({b[group]['worst']}): U = {c(ch['U'], 2)} °C",
               xlabel="u_i, °C")
        ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(folder, "5_neviznachenist.png"))


def series_tables(folder, empty, loaded, covs):
    rows = [["Цикл", "Середня, °C", "Δt, °C", "Миттєвий розкид, °C", "σ_t, °C", "Прогрів, хв", "Відновлення, хв",
             "Найхолодніша", "Найгарячіша"]]
    for name, r in [("Порожня камера", empty)] + [(f"З виробами {i + 1}", r) for i, r in enumerate(loaded)]:
        rows.append([name, c(r["T_mean"], 2), c(r["dT"], 2), c(r["dT_inst"], 2), c(r["sigma_t"], 3),
                     c(r["heat_min"]), c(r["recover_min"]), r["coldest"], r["hottest"]])
    table(os.path.join(folder, "pole_seriia.csv"), rows)

    rows = [["Виріб", "Показник"] + [f"Цикл {i + 1}" for i in range(len(loaded))] + ["Середнє", "s (повторюваність)"]]
    for p in PART_NAMES:
        for key, title in (("reach_min", "Досягнення порогу, хв"), ("hold_min", "Макс. неперервна витримка, хв"),
                           ("Tmax", "Макс. температура, °C")):
            xs = [r["parts"][p][key] for r in loaded]
            ok = [x for x in xs if x is not None]
            rows.append([p, title] + [c(x) for x in xs] + [c(mean(ok)) if ok else "—", c(std(ok), 2) if len(ok) > 1 else "—"])
        rows.append([p, "Стан"] + [r["parts"][p]["verdict"] for r in loaded] + ["", ""])
    table(os.path.join(folder, "vyroby_seriia.csv"), rows)

    rows = [["Цикл", "Група", "Відліків", "Середня похибка, °C", "СКВ, °C", "Макс |похибка|, °C", "U, °C", "Частка в межах U, %"]]
    for name, cv in covs:
        for g in ("air", "metal"):
            if cv and g in cv:
                v = cv[g]
                rows.append([name, "повітря" if g == "air" else "метал", v["n"], c(v["mean"], 3), c(v["std"], 3),
                             c(v["max_abs"], 3), c(v["U"], 3), c(100 * v["within_U"], 1)])
    table(os.path.join(folder, "pokryttia_U.csv"), rows)

    fig = Figure(figsize=(10, 4.5), dpi=150)
    ax = fig.add_subplot(111)
    w = 0.25
    for k, p in enumerate(PART_NAMES):
        xs = [r["parts"][p]["hold_min"] for r in loaded]
        ax.bar([i + (k - 1) * w for i in range(len(loaded))], xs, width=w, label=p)
    ax.axhspan(PAINT["hold_min"], PAINT["hold_max"], color="#2b8a3e", alpha=0.12, label="норма витримки")
    ax.set_xticks(range(len(loaded)), [f"Цикл {i + 1}" for i in range(len(loaded))])
    ax.set(ylabel="хв", title="Максимальна неперервна витримка вище порогу")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(folder, "6_vytrymka_seriia.png"))
