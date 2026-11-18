"""Серія модельних експериментів (відтворювана, фіксовані seed):

  1) калібрування 12 каналів у точках 50…250 °C, коефіцієнти a, b;
  2) бюджет невизначеності окремо для повітря і металу, поріг витримки T_фарби + U_метал;
  3) цикл порожньої камери;
  4) три повторні цикли з виробами (різні реалізації шуму, T цеху, тривалість відкриття дверей, маса партії);
  5) аналіз, перевірка покриття U за дійсними значеннями моделі, CSV, графіки, підсумковий звіт.

Запуск:  python eksperyment.py [--setpoint 200] [--load-min 50] [--after-min 40]
"""
import argparse
import math
import os
from datetime import datetime

from analiz import analyze, coverage
from dzherela import SimSource
from kalibrovka import calibrate
from konfig import PAINT, PART_NAMES, data_dir, report_dir, write_json, cal_file
from neviznachenist import budget
from oven import load_params
from zapys import Recorder, run_cycle
from zvit import make_report, calibration_tables, budget_tables, series_tables, c

EMPTY_SEED = 100
# Відмінності повторних циклів (умови, які в цеху не повторюються точно)
REPEATS = [
    {"seed": 101, "t_ceh": 20.0, "door_ds": 0, "m_load": 1.00, "age_years": 0.05},
    {"seed": 102, "t_ceh": 18.5, "door_ds": 15, "m_load": 1.05, "age_years": 0.06},
    {"seed": 103, "t_ceh": 21.5, "door_ds": -10, "m_load": 0.95, "age_years": 0.07},
]


def run_series(params=None, out_name=None, log=print):
    params = dict(params or load_params())
    stamp = out_name or "seriia_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    ddir = os.path.join(data_dir("sim"), stamp)
    rdir = os.path.join(report_dir("sim"), stamp)
    os.makedirs(rdir, exist_ok=True)

    log("Калібрування 12 каналів…")
    cal = calibrate(mode="sim")
    from doslidzhennia import regime_budget          # бюджет для умов саме цього режиму
    b, _ = regime_budget(dict(params, after_load_min=max(params["after_load_min"], 70)), cal)
    thr, U_air = b["threshold"], b["air"]["U"]
    log(f"U повітря = {U_air:.2f} °C, U метал = {b['metal']['U']:.2f} °C, поріг = {thr:.2f} °C")
    calibration_tables(rdir, cal)
    budget_tables(rdir, b)
    common = {"threshold": thr, "U_air": U_air, "U_metal": b["metal"]["U"]}

    log("Цикл порожньої камери…")
    pe = dict(params, empty=True, empty_min=90)
    rec = Recorder("sim", "0_porozhnia", ddir, dict(common, params=pe, seed=EMPTY_SEED))
    path = run_cycle(SimSource(pe, seed=EMPTY_SEED), rec)
    r_empty = analyze(path)
    make_report(path, params=pe, res=r_empty, out=os.path.join(rdir, "0_porozhnia"))
    covs = [("Порожня камера", coverage(path, r_empty, b))]

    loaded = []
    for i, v in enumerate(REPEATS):
        log(f"Цикл з виробами {i + 1}/3…")
        p = dict(params, door_s=params["door_s"] + v["door_ds"])
        rec = Recorder("sim", f"{i + 1}_vyroby", ddir, dict(common, params=p, variation=v))
        src = SimSource(p, seed=v["seed"], t_ceh=v["t_ceh"], age_years=v["age_years"],
                        phys={"m_load": 150.0 * v["m_load"]})
        path = run_cycle(src, rec)
        r = analyze(path)
        make_report(path, params=p, res=r, out=os.path.join(rdir, f"{i + 1}_vyroby"))
        loaded.append(r)
        covs.append((f"З виробами {i + 1}", coverage(path, r, b)))

    series_tables(rdir, r_empty, loaded, covs)
    write_summary(rdir, params, cal, b, r_empty, loaded, covs)
    from dzherela import HW_SEED
    from doslidzhennia import jsonable
    write_json(os.path.join(rdir, "umovy.json"),
               {"source": "sim (фізична модель печі + модель 12 вимірювальних каналів)", "params": params,
                "hw_seed": HW_SEED, "empty_seed": EMPTY_SEED, "repeats": REPEATS})
    write_json(os.path.join(rdir, "neviznachenist.json"), jsonable(b))
    write_json(os.path.join(os.path.dirname(cal_file("sim")), "neviznachenist_sim.json"), jsonable(b))
    # повний комплект у одній папці: сирі ряди 1 Гц, метадані циклів, калібрування
    import shutil
    dani = os.path.join(rdir, "dani")
    os.makedirs(dani, exist_ok=True)
    for f in sorted(os.listdir(ddir)):
        shutil.copy2(os.path.join(ddir, f), dani)
    shutil.copy2(cal_file("sim"), os.path.join(dani, "kalibrovka_sim.json"))
    log("Готово: " + rdir)
    return rdir, {"budget": b, "empty": r_empty, "loaded": loaded, "coverage": covs}


def write_summary(folder, params, cal, b, empty, loaded, covs):
    L = [f"# Підсумок модельної серії\n",
         f"Фарба: {PAINT['name']}; режим {c(PAINT['T_metal'], 0)} °C за металом, витримка "
         f"{c(PAINT['hold_min'], 0)}–{c(PAINT['hold_max'], 0)} хв.  ",
         f"Уставка {params['setpoint']:g} °C, завантаження на {params['load_min']} хв, "
         f"цикл після завантаження {params['after_load_min']} хв. Розміщення: "
         + ", ".join(f"{p} — П{params[p]}" for p in PART_NAMES) + ".\n",
         "## Невизначеність (k = 2)\n",
         f"- Повітря: u_c = {c(b['air']['uc'], 3)} °C, **U = {c(b['air']['U'], 2)} °C** (канал {b['air']['worst']})",
         f"- Метал: u_c = {c(b['metal']['uc'], 3)} °C, **U = {c(b['metal']['U'], 2)} °C** (канал {b['metal']['worst']})",
         f"- Поріг початку витримки: {c(PAINT['T_metal'], 0)} + {c(b['metal']['U'], 2)} = **{c(b['threshold'], 2)} °C**\n",
         "## Порожня камера\n",
         f"- Δt = {c(empty['dT'], 2)} °C ({empty['hottest']} − {empty['coldest']}), σ_t = {c(empty['sigma_t'], 3)} °C, "
         f"прогрів {c(empty['heat_min'])} хв\n",
         "## Цикли з виробами\n",
         "| Цикл | Δt, °C | σ_t, °C | Відновлення, хв | " + " | ".join(f"{p}: витримка, хв" for p in PART_NAMES) + " |",
         "|---|---|---|---|" + "---|" * len(PART_NAMES)]
    for i, r in enumerate(loaded):
        L.append(f"| {i + 1} | {c(r['dT'], 2)} | {c(r['sigma_t'], 3)} | {c(r['recover_min'])} | "
                 + " | ".join(f"{c(r['parts'][p]['hold_min'])} ({r['parts'][p]['verdict'].lower()})" for p in PART_NAMES) + " |")
    L.append("\n## Перевірка покриття U (лише симуляція)\n")
    for name, cv in covs:
        if cv:
            L.append(f"- {name}: " + "; ".join(
                f"{'повітря' if g == 'air' else 'метал'} — макс |похибка| {c(v['max_abs'], 2)} °C, у межах U {c(100 * v['within_U'], 1)} %"
                for g, v in cv.items()))
    L.append("\nТаблиці: kalibrovka.csv, neviznachenist_*.csv, pole_seriia.csv, vyroby_seriia.csv, pokryttia_U.csv; "
             "графіки 4_kalibrovka.png, 5_neviznachenist.png, 6_vytrymka_seriia.png; окремі цикли — у підпапках.")
    with open(os.path.join(folder, "pidsumok.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--setpoint", type=float)
    ap.add_argument("--load-min", type=int)
    ap.add_argument("--after-min", type=int)
    ap.add_argument("--name", help="назва папки серії (за замовчуванням з датою)")
    a = ap.parse_args()
    p = load_params()
    for k, v in (("setpoint", a.setpoint), ("load_min", a.load_min), ("after_load_min", a.after_min)):
        if v is not None:
            p[k] = v
    run_series(p, a.name)
