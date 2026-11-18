"""Окончательна серія при уставці 200 °C і формування DATA_FOR_DIPLOMA.md.

  python dani_dyplom.py            # серія (калібрування, порожня камера, 3 цикли) + DATA_FOR_DIPLOMA.md
  python dani_dyplom.py --no-run   # лише DATA_FOR_DIPLOMA.md з уже збереженої серії

Усі числа читаються з файлів серії (umovy.json, neviznachenist.json, dani/*.csv, dani/*.json,
dani/kalibrovka_sim.json) та з аналізу її CSV; вручну нічого не підставляється.
"""
import argparse
import glob
import json
import math
import os
import subprocess
import sys

from analiz import analyze, STEADY_MIN, HEAT_TOL
from kalibrovka import REF_POINTS, CAL, load_calibration
from konfig import AIR, PART_NAMES, CHANNELS, PAINT, WHERE, CS_PINS, BASE, report_dir, read_json
from model_pechi import PHYS, derived, part_geometry, c_steel, SIGMA
from skhema import PX, LEVELS
from vymiriuvannia import MEAS, contact_beta, contact_resistances
from zvit import c

FINAL = "pidsumkova_demo_200"
SETPOINT = 200.0
OUT = os.path.join(BASE, "DATA_FOR_DIPLOMA.md")


def point_coords():
    """Координати точок, м: x — від лівої стінки (ширина 1,8 м), y — від дверей углиб (довжина 3,6 м),
    z — від поду. Беруться зі схеми skhema.PX / skhema.LEVELS."""
    z = {}
    for title, pts in LEVELS:
        h = float(title.split("·")[1].replace("м", "").replace(",", ".").strip())
        for n in pts:
            z[n] = h
    return {n: (PX[n][0] * PHYS["W"], (1 - PX[n][1]) * PHYS["L"], z[n]) for n in range(1, 10)}


def run_tests():
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=BASE,
                       capture_output=True, text=True)
    import re
    lines = [re.sub(r" in [0-9.]+s$", "", l) for l in r.stderr.strip().splitlines()
             if l.startswith("Ran ") or l in ("OK",) or l.startswith("FAILED")]
    return " · ".join(lines), r.returncode == 0


def git_rev():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=BASE, capture_output=True,
                              text=True).stdout.strip() or "—"
    except OSError:
        return "—"


def u_rows(b, group):
    return b["channels"][b[group]["worst"]]["rows"]


def write(rdir):
    rel = os.path.relpath(rdir, BASE)
    umovy = read_json(os.path.join(rdir, "umovy.json"))
    b = read_json(os.path.join(rdir, "neviznachenist.json"))
    cal_full = read_json(os.path.join(rdir, "dani", "kalibrovka_sim.json"))
    cal = cal_full["channels"]
    if load_calibration("sim") != cal:
        raise RuntimeError("kalibrovka_sim.json у корені не збігається з калібруванням серії — перезапустіть серію")
    params = umovy["params"]
    files = sorted(f for f in glob.glob(os.path.join(rdir, "dani", "*.csv")) if not f.endswith("_true.csv"))
    res = {os.path.basename(f)[:-4]: analyze(f) for f in files}
    meta = {os.path.basename(f)[:-4]: read_json(f[:-4] + ".json") for f in files}
    empty = res["0_porozhnia"]
    cycles = [res[k] for k in sorted(res) if k != "0_porozhnia"]
    tests, tests_ok = run_tests()
    d = derived()
    geo = part_geometry()

    L = []
    w = L.append
    w("# Дані для дипломної роботи — ІВС температурного поля печі полімеризації")
    w("")
    w("> **Джерело даних: розрахункові (модель, режим `sim`).** " + umovy["source"] + ". "
      "Файл згенеровано автоматично командою `python dani_dyplom.py` з окончательної серії; вручну не редагувати. "
      "Після вимірювань на реальній печі ті самі таблиці отримують з `data/real/` тими самими функціями.")
    w("")
    w(f"- Папка серії: `{rel}/` (графіки, таблиці CSV), сирі ряди 1 Гц і метадані: `{rel}/dani/`")
    w(f"- Відтворюваність: екземпляр датчиків HW_SEED = {umovy['hw_seed']}, порожня камера seed = {umovy['empty_seed']}, "
      "цикли з виробами seed = " + ", ".join(str(r["seed"]) for r in umovy["repeats"]) + f"; версія програми (git) `{git_rev()}`.")
    w("- Повторний запуск дає побайтно ті самі CSV.")
    w("- Дослідження уставок 210 і 220 °C — окремо: `zvit/sim/doslidzhennia_ustavky/`, `results_for_diploma.md`; "
      "в основні таблиці цього файлу не входять.")
    w("")

    # 1
    w("## 1. Вихідні параметри")
    w("")
    w("### 1.1 Режим")
    w("")
    w("| Параметр | Значення |")
    w("|---|---|")
    w(f"| Уставка регулятора повітря | {c(params['setpoint'], 0)} °C |")
    w(f"| Технологічний поріг металу (фарба) | {c(PAINT['T_metal'], 0)} °C |")
    w(f"| Витримка | {c(PAINT['hold_min'], 0)}–{c(PAINT['hold_max'], 0)} хв |")
    w(f"| Фарба | {PAINT['name']} |")
    w(f"| Завантаження виробів | на {params['load_min']}-й хв |")
    w(f"| Відкриття дверей | {params['door_s']} с (повтори: " + ", ".join(f"{params['door_s'] + r['door_ds']}" for r in umovy["repeats"]) + " с) |")
    w(f"| Тривалість після завантаження | {params['after_load_min']} хв |")
    w(f"| Порожня камера | 90 хв |")
    w(f"| Частота реєстрації | 1 Гц |")
    w("")
    w("### 1.2 Піч")
    w("")
    w("| Параметр | Значення |")
    w("|---|---|")
    rows = [
        ("Камера", f"{c(PHYS['L'])} × {c(PHYS['W'])} × {c(PHYS['H'])} м, V = {c(d['V'], 2)} м³, A_вн = {c(d['A_in'], 2)} м²"),
        ("Нагрівачі", f"{PHYS['n_ten']} ТЕНів × {c(PHYS['P_ten'] / 1000)} кВт = {c(d['P_max'] / 1000, 0)} кВт; C_h = {c(d['C_h'] / 1000, 0)} кДж/К; hA = {c(PHYS['hA_ten'], 0)} Вт/К"),
        ("Циркуляція", f"{c(PHYS['m_circ'])} кг/с"),
        ("Обшивка, стелажі, ізоляція", f"C_w = {c(d['C_w'] / 1000, 0)} кДж/К; h_w = {c(PHYS['h_wall'], 0)} Вт/(м²·К)"),
        ("Ізоляція", f"λ = {c(PHYS['lambda_ins'], 2)} Вт/(м·К), δ = {c(PHYS['delta_ins'] * 1000, 0)} мм, UA = {c(d['UA_ins'], 1)} Вт/К"),
        ("Витяжка / двері", f"{c(PHYS['G_exhaust'], 3)} кг/с / {c(PHYS['G_door'], 2)} кг/с"),
        ("Партія виробів", f"{c(PHYS['m_load'], 0)} кг (повтори ×" + ", ×".join(c(r['m_load'], 2) for r in umovy["repeats"]) + f"), τ = {c(PHYS['tau_load'], 0)} с"),
        ("Регулятор", f"ПІ, K_p = {c(PHYS['Kp'], 2)} 1/К, T_i = {c(PHYS['Ti'], 0)} с"),
        ("Температура цеху", ", ".join(f"{c(r['t_ceh'])}" for r in umovy["repeats"]) + " °C (повтори); 20 °C (порожня)"),
    ]
    for k, v in rows:
        w(f"| {k} | {v} |")
    w("")
    w("### 1.3 Вироби")
    w("")
    h_r = 4 * PHYS["eps_part"] * SIGMA * 463.15 ** 3
    dims = {"Лист": PHYS["sheet"], "Труба": PHYS["tube"], "Опора": PHYS["flange"]}
    desc = {"Лист": "лист {0:g}×{1:g}×{2:g} мм", "Труба": "труба {0:g}×{0:g}×{1:g} мм, L = {2:g} мм",
            "Опора": "фланець {0:g}×{1:g}×{2:g} мм"}
    w("| Виріб | Зразок | Точка | m, кг | A, м² | m/A, кг/м² | τ при 190 °C, с |")
    w("|---|---|---|---|---|---|---|")
    for p in PART_NAMES:
        m, A = geo[p]
        w(f"| {p} | {desc[p].format(*[x * 1000 for x in dims[p]])} | П{params[p]} | {c(m, 2)} | {c(A, 3)} | {c(m / A, 1)} | "
          f"{c(m / A * c_steel(190) / (PHYS['h_part'] + h_r), 0)} |")
    w(f"\nТепловіддача виробів: h = {c(PHYS['h_part'], 0)} Вт/(м²·К) + випромінювання ε = {c(PHYS['eps_part'], 2)} "
      f"(h_вип = {c(h_r, 1)} Вт/(м²·К) при 190 °C); c сталі = 450 + 0,28·T Дж/(кг·К).")
    w("")

    # 2
    w("## 2. Вимірювальні точки")
    w("")
    w("Початок координат — лівий нижній кут біля дверей: x — по ширині (0…1,8 м), y — від дверей углиб (0…3,6 м), z — висота від поду.")
    w("")
    w("| Канал | Призначення | x, м | y, м | z, м | Розташування | CS ESP32 |")
    w("|---|---|---|---|---|---|---|")
    pc = point_coords()
    for i, n in enumerate(AIR):
        x, y, z = pc[i + 1]
        w(f"| {n} | повітря | {c(x, 2)} | {c(y, 2)} | {c(z, 2)} | {WHERE[i]} | GPIO{CS_PINS[n]} |")
    for p in PART_NAMES:
        x, y, z = pc[params[p]]
        w(f"| {p} | метал (контактна ТП) | {c(x, 2)} | {c(y, 2)} | {c(z, 2)} | біля П{params[p]} | GPIO{CS_PINS[p]} |")
    w("")
    w("Датчики: термопари K клас 1 (КТМС Ø1,5 мм), Adafruit 3263 (MAX31856), ESP32, SPI: SCK GPIO18, MISO GPIO19, MOSI GPIO23.")
    w("")

    # 3
    w("## 3. Калібрування")
    w("")
    cond = cal_full["conditions"]
    w("| Параметр | Значення |")
    w("|---|---|")
    w(f"| Точки | {', '.join(c(T, 0) for T in cal_full['ref_points'])} °C |")
    w(f"| Еталон | Pt100 + вимірювач, U = {c(cond['U_ref'], 2)} °C (k = 2) |")
    w(f"| Калібратор | неоднорідність ±{c(cond['block_nonunif'], 2)} °C, нестабільність ±{c(cond['block_stab'], 2)} °C |")
    w(f"| Відліків у точці | {cond['n_samples']} (1 Гц) |")
    w(f"| T холодного спаю | {c(MEAS['T_cj_cal'], 0)} °C |")
    w(f"| Модель поправки | {cal_full['formula']}, МНК |")
    w("")
    w("| Канал | " + " | ".join(f"Δ{int(T)} до, °C" for T in REF_POINTS) + " | a, °C | b | s₁, °C | s_зал, °C | макс. залишок, °C |")
    w("|---|" + "---|" * (len(REF_POINTS) + 5))
    for n in CHANNELS:
        r = cal[n]
        w(f"| {n} | " + " | ".join(c(e, 3) for e in r["errors"]) + f" | {c(r['a'], 5)} | {c(r['b'], 7)} | "
          f"{c(r['s_pooled'], 4)} | {c(r['s_res'], 4)} | {c(r['max_resid'], 4)} |")
    w("")
    w(f"Таблиця: `{rel}/kalibrovka.csv`, графік: `{rel}/4_kalibrovka.png`, коефіцієнти: `{rel}/dani/kalibrovka_sim.json`.")
    w("")

    # 4
    w("## 4. Бюджет невизначеності")
    w("")
    reg = b["inputs"]["UNC"]
    w(f"Умови режиму з моделі: T_nom = {c(reg['T_nom'], 1)} °C, ΔT повітря–поверхні (випромінювання) = {c(reg['dT_env'], 1)} К, "
      f"ΔT повітря–метал при досягненні порогу = {c(reg['dT_air_metal'], 1)} К. Контактна ТП: β = {c(contact_beta(190), 3)} "
      f"(площадка Ø{MEAS['d_pad'] * 1000:.0f} мм, h_к = {c(MEAS['h_contact'], 0)} Вт/(м²·К)).")
    w("")
    for g, title in (("air", "Повітря"), ("metal", "Метал")):
        ch = b["channels"][b[g]["worst"]]
        tot = sum(x["u"] ** 2 for x in ch["rows"])
        w(f"### 4.{1 if g == 'air' else 2} {title} (канал з найбільшою U — {b[g]['worst']})")
        w("")
        w("| Позн. | Складова | Тип | Розподіл | Межа / оцінка, °C | u_i, °C | Внесок, % | ν_i |")
        w("|---|---|---|---|---|---|---|---|")
        for x in ch["rows"]:
            nu = "∞" if x["nu"] in ("inf", math.inf) else str(int(float(x["nu"])))
            w(f"| {x['sym']} | {x['name']} | {x['type']} | {x['dist']} | {c(x['limit'], 4)} | {c(x['u'], 4)} | "
              f"{c(100 * x['u'] ** 2 / tot, 1)} | {nu} |")
        nu_eff = ch["nu_eff"]
        nu_eff = "∞" if nu_eff in ("inf", math.inf) else str(int(float(nu_eff)))
        w(f"| **u_c** | | | | | **{c(ch['uc'], 4)}** | 100 | ν_eff = {nu_eff} |")
        w(f"| **U (k = {c(b['k'], 0)})** | | | | | **{c(ch['U'], 3)}** | | |")
        w("")
    w("### 4.3 Підсумок")
    w("")
    w("| Величина | Значення |")
    w("|---|---|")
    w(f"| U повітря (k = 2) | **{c(b['air']['U'], 2)} °C** |")
    w(f"| U металу (k = 2) | **{c(b['metal']['U'], 2)} °C** |")
    w(f"| Поріг початку витримки T_пор = {c(PAINT['T_metal'], 0)} + U_метал | **{c(b['threshold'], 2)} °C** |")
    w("")
    w("U за каналами:")
    w("")
    w("| " + " | ".join(CHANNELS) + " |")
    w("|" + "---|" * len(CHANNELS))
    w("| " + " | ".join(c(b["channels"][n]["U"], 2) for n in CHANNELS) + " |")
    w("")
    w(f"Таблиці: `{rel}/neviznachenist_povitria.csv`, `{rel}/neviznachenist_metal.csv`, `{rel}/neviznachenist_kanaly.csv`; "
      f"графік `{rel}/5_neviznachenist.png`; повний бюджет `{rel}/neviznachenist.json`.")
    w("")

    # 5
    w("## 5. Порожня камера")
    w("")
    w("| Показник | Значення |")
    w("|---|---|")
    w(f"| Середня температура повітря (останні {STEADY_MIN} хв) | {c(empty['T_mean'], 2)} °C |")
    w(f"| Просторова неоднорідність Δt | **{c(empty['dT'], 2)} °C** ({empty['hottest']} − {empty['coldest']}) |")
    w(f"| Найбільший миттєвий розкид | {c(empty['dT_inst'], 2)} °C |")
    w(f"| Часова нестабільність σ_t (макс.) | **{c(empty['sigma_t'], 3)} °C** ({empty['sigma_t_point']}) |")
    w(f"| Час прогріву (±{c(HEAT_TOL, 0)} °C від сталих значень) | **{c(empty['heat_min'])} хв** |")
    w(f"| Регулятор / відхилення від середньої | {c(empty['ctrl'], 2)} / {c(empty['ctrl_dev'], 2)} °C |")
    w("")
    w("| Точка | Розташування | Середня, °C | Мін, °C | Макс, °C | σ_t, °C |")
    w("|---|---|---|---|---|---|")
    for i, n in enumerate(AIR):
        p = empty["points"][n]
        w(f"| {n} | {WHERE[i]} | {c(p['mean'], 2)} | {c(p['min'], 2)} | {c(p['max'], 2)} | {c(p['sigma'], 3)} |")
    w("")
    w(f"Файли: `{rel}/0_porozhnia/` (`pokaznyky.csv`, `povitria.csv`, `1_povitria.png`, `3_tochky.png`); "
      f"дані `{rel}/dani/0_porozhnia.csv`.")
    w("")

    # 6
    w("## 6. Цикли з виробами")
    w("")
    w(f"Поріг витримки {c(b['threshold'], 2)} °C, норма {c(PAINT['hold_min'], 0)}–{c(PAINT['hold_max'], 0)} хв неперервно; "
      "час відлічується від завантаження.")
    w("")
    w("### 6.1 Поле повітря")
    w("")
    w("| Цикл | Середня, °C | Δt, °C | σ_t, °C | Прогрів камери, хв | Відновлення після завантаження, хв |")
    w("|---|---|---|---|---|---|")
    for i, r in enumerate(cycles):
        w(f"| {i + 1} | {c(r['T_mean'], 2)} | {c(r['dT'], 2)} | {c(r['sigma_t'], 3)} | {c(r['heat_min'])} | {c(r['recover_min'])} |")
    w("")
    for i, r in enumerate(cycles):
        v = meta[f"{i + 1}_vyroby"]["variation"]
        w(f"### 6.{i + 2} Цикл {i + 1} (seed {v['seed']}, T цеху {c(v['t_ceh'])} °C, двері "
          f"{params['door_s'] + v['door_ds']} с, партія ×{c(v['m_load'], 2)})")
        w("")
        w("| Виріб | Точка | Прогрів до порогу, хв | Неперервна витримка, хв | Tmax, °C | Результат |")
        w("|---|---|---|---|---|---|")
        for p in PART_NAMES:
            x = r["parts"][p]
            w(f"| {p} | П{params[p]} | {c(x['reach_min']) if x['reach_min'] is not None else 'не досягає'} | "
              f"{c(x['hold_min'])} | {c(x['Tmax'])} | {x['verdict']} |")
        w("")
    w("### 6.5 Зведення трьох циклів")
    w("")
    w("| Виріб | Прогрів до порогу, хв (1 / 2 / 3) | Витримка, хв (1 / 2 / 3) | Tmax, °C (1 / 2 / 3) | Результат |")
    w("|---|---|---|---|---|")
    for p in PART_NAMES:
        xs = [r["parts"][p] for r in cycles]
        w(f"| {p} | " + " / ".join(c(x["reach_min"]) if x["reach_min"] is not None else "—" for x in xs) + " | "
          + " / ".join(c(x["hold_min"]) for x in xs) + " | " + " / ".join(c(x["Tmax"]) for x in xs) + " | "
          + " / ".join(x["verdict"] for x in xs) + " |")
    ok_all = all(r["ok"] for r in cycles)
    w("")
    w(f"**Перевірка режиму:** " + ("усі вироби в нормі в усіх циклах." if ok_all else
      "режим не забезпечує норму витримки для всіх виробів — " + "; ".join(
          f"{p}: " + ", ".join(sorted(set(r['parts'][p]['verdict'] for r in cycles))) for p in PART_NAMES) + "."))
    w("")
    w(f"Файли: `{rel}/vyroby_seriia.csv`, `{rel}/pole_seriia.csv`, `{rel}/6_vytrymka_seriia.png`, "
      f"`{rel}/N_vyroby/` (`vyroby.csv`, `pokaznyky.csv`, `povitria.csv`, `1_povitria.png`, `2_vyroby.png`, `3_tochky.png`); "
      f"дані `{rel}/dani/N_vyroby.csv`, метадані `{rel}/dani/N_vyroby.json`.")
    w("")
    cov = os.path.join(rdir, "pokryttia_U.csv")
    if os.path.exists(cov):
        w(f"Перевірка покриття U за дійсними температурами моделі: `{rel}/pokryttia_U.csv`.")
        w("")

    # 7
    w("## 7. Формули")
    w("")
    w("**Теплова модель (зосереджені ємності, Δτ = 0,5 с):**")
    w("")
    w("- C_h·dT_h/dτ = P − (hA)_ТЕН·(T_h − T_a)")
    w("- C_a·dT_a/dτ = (hA)_ТЕН·(T_h − T_a) − h_w·A·(T_a − T_w) − ṁ_вит·c_p·(T_a − T_ц) − ṁ_дв·c_p·(T_a − T_ц)·[двері] − Q_партія − Q_вироби,конв")
    w("- C_w·dT_w/dτ = h_w·A·(T_a − T_w) − UA_із·(T_w − T_ц) − Q_вироби,вип")
    w("- m·c(T)·dT/dτ = A·[h·(T_пов,лок − T) + ε·σ·(T_w⁴ − T⁴)] — виріб")
    w("- T_i = T_a + α_i·ΔT_цирк − β_i·(T_a − T_ц) − γ_i·(T_a − T_ц)·[двері] — локальна температура точки (з інерцією τ_i)")
    w("- u = K_p·(e + ∫e dτ/T_i), 0 ≤ u ≤ 1, P = u·P_max — регулятор")
    w("")
    w("**Вимірювальний канал:**")
    w("")
    w("- Повітря: T_д = T_пов + ε·σ·(T_оточ⁴ − T_пов⁴)/(h_д + 4εσT³)")
    rr = contact_resistances(190)
    w(f"- Метал: T_д = T_s + β·(T_пов − T_s), β = R_к/(R_к + R_пов) = {c(rr['R_c'], 1)}/({c(rr['R_c'], 1)} + {c(rr['R_air'], 1)}) = {c(contact_beta(190), 3)}")
    w("- MAX31856: U = [E(T_д + δ_ТП) − E(T_ХС)]·(1 + δ_k) + U_0; T = E⁻¹(U + E(T_ХС,вим)) + шум; крок 0,0078125 °C; E — НСХ K (NIST ITS-90)")
    w("")
    w("**Калібрування:** T' = a + b·T; b = Σ(x − x̄)(y − ȳ)/Σ(x − x̄)², a = ȳ − b·x̄; s_зал = √(Σr²/(n − 2)).")
    w("")
    w("**Невизначеність:** u_c = √(Σu_i²); U = k·u_c, k = 2; ν_eff = u_c⁴/Σ(u_i⁴/ν_i); рівномірний розподіл u = a/√3; "
      "u_вип: a = |ε·σ·((T − ΔT_ок)⁴ − T⁴)|/(h_д + 4εσT³); u_конт: a = β·ΔT_пов–м; u_ХС: a = k_ХС·ΔT_ХС + a_град; "
      "u_ет = √((U_ет/2)² + (a_н/√3)² + (a_с/√3)²).")
    w("")
    w("**Поріг витримки:** T_пор = T_фарби + U_метал.")
    w("")
    w(f"**Показники:** Δt = max_i T̄_i − min_i T̄_i (середні за останні {STEADY_MIN} хв, 9 точок повітря); "
      f"σ_t = max_i √(Σ(T_ij − T̄_i)²/(n − 1)) у тому ж вікні; час прогріву — момент, після якого всі 9 точок лишаються в межах "
      f"±{c(HEAT_TOL, 0)} °C від своїх сталих середніх (для циклів — до завантаження); відновлення — те саме після завантаження; "
      "прогрів виробу — час від завантаження до першого T ≥ T_пор; неперервна витримка — найдовший інтервал T ≥ T_пор; "
      f"результат: < {c(PAINT['hold_min'], 0)} хв — «Не догрітий», > {c(PAINT['hold_max'], 0)} хв — «Перетримка», інакше «Норма».")
    w("")

    # 8
    w("## 8. Зміни програми та тести")
    w("")
    w("- Фізична модель печі (`model_pechi.py`): ТЕНи, повітря, обшивка, ізоляція, витяжка, двері, партія; вироби за геометрією зразків; енергетичний баланс.")
    w("- Модель вимірювального каналу (`vymiriuvannia.py`, `termopara.py`): ТП K клас 1, MAX31856, холодний спай, випромінювання, контакт, інерція, шум.")
    w("- Калібрування в 5 точках 50–250 °C з окремими файлами для симуляції й реальних вимірювань (`kalibrovka.py`).")
    w("- Бюджет невизначеності окремо для повітря й металу з умовами режиму з моделі; поріг T_фарби + U_метал замість фіксованих 191,9 °C (`neviznachenist.py`).")
    w("- Аналіз Δt, σ_t, прогріву, відновлення, неперервної витримки (`analiz.py`); звіти CSV/PNG (`zvit.py`).")
    w("- Джерела `SimSource` / `Esp32Source` з однаковим форматом даних, прошивка ESP32 (`dzherela.py`, `esp32/`).")
    w("- Серія експериментів з фіксованими seed і повним комплектом даних в одній папці (`eksperyment.py`, `dani_dyplom.py`).")
    w("- Інтерфейс: вибір джерела, кнопка «Серія», час відкриття дверей, поріг з невизначеності; усі 12 каналів на графіках і схемі.")
    w("- Окреме дослідження уставок 210/220 °C (`doslidzhennia.py`).")
    w("")
    w(f"**Автоматичні тести** (`python -m unittest discover -s tests`): {tests}" + ("" if tests_ok else " — **ПОМИЛКА**"))
    w("")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return OUT


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-run", action="store_true")
    a = ap.parse_args()
    rdir = os.path.join(report_dir("sim"), FINAL)
    if not a.no_run:
        from eksperyment import run_series
        from oven import load_params
        run_series(dict(load_params(), setpoint=SETPOINT), FINAL)
    print("Записано:", write(rdir))
