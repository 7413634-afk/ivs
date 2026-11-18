"""Дослідження режимів: уставка повітря 200, 210, 220 °C при режимі фарби 190 °C за металом, витримка 12–20 хв.

Для кожної уставки (розрахункові дані, режим "sim"):
  1) розрахункові умови режиму (ΔT повітря–поверхні для випромінювання, ΔT повітря–метал на початку витримки)
     -> бюджет невизначеності -> поріг T_фарби + U_метал (ітераційно, бо ΔT залежить від порогу);
  2) цикл порожньої камери: Δt, σ_t, прогрів;
  3) три повторні цикли з виробами з довгим перебуванням після завантаження (D_MAX), щоб визначити
     час досягнення порогу кожним виробом і витримку як функцію тривалості циклу D;
  4) вердикти для стандартного циклу (D з parametry.json) і вікно D, у якому всі вироби в нормі.
Критерій вибору основного режиму (визначено до розрахунку, без підгонки):
  К1 — найбільша кількість виробів, що можуть бути в нормі в усіх повторах при одному D;
  К2 — ширина вікна D для цього набору виробів;  К3 — нижча Tmax металу, менша енергія.
Запуск:  python doslidzhennia.py
"""
import json
import math
import os

from matplotlib.figure import Figure

from analiz import analyze, coverage, load_csv, mean, std, runs_above
from dzherela import SimSource
from kalibrovka import calibrate, load_calibration, correct
from konfig import AIR, PART_NAMES, PAINT, BASE, data_dir, report_dir, write_json
from model_pechi import Oven, PHYS, derived, part_geometry, c_steel
from neviznachenist import budget, UNC
from oven import load_params
from zapys import Recorder, run_cycle
from zvit import make_report, calibration_tables, budget_tables, table, c
from eksperyment import REPEATS, EMPTY_SEED

SETPOINTS = [200.0, 210.0, 220.0]
D_MAX = 70                 # хв після завантаження у розрахункових циклах
D_GRID = list(range(5, D_MAX + 1))
NAME = "doslidzhennia_ustavky"
ALT_PLACEMENT = {"Опора": 8}   # додатковий сценарій: опора в найгарячішій точці


def regime_stats(params, threshold, seed=101, steady_min=15):
    """Дійсні (модельні) умови режиму: T_nom, dT_env у сталому режимі, dT повітря–метал при досягненні порогу."""
    o = Oven(params, seed=seed)
    n = o.total_seconds()
    air_env, parts_hist, power = [], [], []
    for _ in range(n):
        o.step()
        st = o.true_state()
        power.append(st["power"])
        if o.t > n - steady_min * 60:
            air_env.append((st["air"], st["env"]))
        parts_hist.append({p: st["parts"][p] for p in PART_NAMES} if o.loaded else None)
    T_nom = mean([mean(a) for a, _ in air_env])
    dT_env = max(abs(x - e) for a, en in air_env for x, e in zip(a, en))
    eb = o.energy_balance()
    return {"T_nom": T_nom, "dT_env": dT_env, "parts_hist": parts_hist, "power": power, "energy": eb}


def dT_at_reach(stats, threshold):
    out = {}
    for p in PART_NAMES:
        for h in stats["parts_hist"]:
            if h and h[p][0] >= threshold:
                out[p] = h[p][1] - h[p][0]
                break
    return out


def regime_budget(params, cal, seed=101):
    """Ітерація: поріг -> ΔT при досягненні -> U_метал -> поріг."""
    stats = regime_stats(params, PAINT["T_metal"] + 0.6, seed)
    thr = PAINT["T_metal"] + 0.6
    for _ in range(5):
        dts = dT_at_reach(stats, thr)
        regime = {"T_nom": stats["T_nom"], "dT_env": stats["dT_env"],
                  "dT_air_metal": max(dts.values()) if dts else UNC["dT_air_metal"]}
        b = budget("sim", cal, save=False, regime=regime)
        if abs(b["threshold"] - thr) < 1e-3:
            break
        thr = b["threshold"]
    b["regime"] = dict(regime, dT_at_reach=dts)
    return b, stats


def metal_series(path, res):
    data = load_csv(path)
    cal = load_calibration("sim")
    t = data["t_s"]
    i0 = next(i for i, ti in enumerate(t) if ti >= res["load_min"] * 60)
    dt = (t[-1] - t[0]) / (len(t) - 1)
    return {p: [correct(p, x, cal) for x in data[p][i0:]] for p in PART_NAMES}, dt


def hold_within(xs, thr, dt_s, D_min):
    n = int(round(D_min * 60 / dt_s))
    runs = runs_above(xs[:n], thr)
    return max(((b - a) * dt_s / 60 for a, b in runs), default=0.0)


def verdict(h):
    return "Не догрітий" if h < PAINT["hold_min"] else ("Перетримка" if h > PAINT["hold_max"] else "Норма")


def windows(holds_by_D, parts):
    """Значення D, за яких усі вироби з parts у нормі в усіх повторах."""
    return [D for D in D_GRID if all(PAINT["hold_min"] <= h <= PAINT["hold_max"]
                                     for p in parts for h in holds_by_D[D][p])]


def best_set(holds_by_D):
    """К1: найбільший набір виробів, для якого існує спільне вікно D."""
    from itertools import combinations
    for k in (3, 2, 1):
        best = None
        for s in combinations(PART_NAMES, k):
            w = windows(holds_by_D, s)
            if w and (best is None or len(w) > len(best[1])):
                best = (list(s), w)
        if best:
            return best
    return ([], [])


def run_setpoint(sp, base, cal, ddir, rdir, placement=None, tag=None, log=print):
    tag = tag or f"{int(sp)}"
    p_long = dict(base, setpoint=sp, after_load_min=D_MAX, **(placement or {}))
    b, stats = regime_budget(p_long, cal)
    thr = b["threshold"]
    log(f"  уставка {sp:g}: U повітря {b['air']['U']:.2f}, U метал {b['metal']['U']:.2f}, поріг {thr:.2f} °C")
    common = {"threshold": thr, "U_air": b["air"]["U"], "U_metal": b["metal"]["U"], "regime": b["regime"]}
    sub_d, sub_r = os.path.join(ddir, tag), os.path.join(rdir, tag)
    os.makedirs(sub_r, exist_ok=True)

    r_empty = None
    if placement is None:
        pe = dict(base, setpoint=sp, empty=True, empty_min=90)
        path = run_cycle(SimSource(pe, seed=EMPTY_SEED), Recorder("sim", "0_porozhnia", sub_d, dict(common, params=pe)))
        r_empty = analyze(path)
        r_empty["coverage"] = coverage(path, r_empty, b)
        make_report(path, params=pe, res=r_empty, out=os.path.join(sub_r, "0_porozhnia"))

    reps = []
    for i, v in enumerate(REPEATS):
        p = dict(p_long, door_s=base["door_s"] + v["door_ds"])
        src = SimSource(p, seed=v["seed"], t_ceh=v["t_ceh"], age_years=v["age_years"],
                        phys={"m_load": PHYS["m_load"] * v["m_load"]})
        path = run_cycle(src, Recorder("sim", f"{i + 1}_vyroby", sub_d, dict(common, params=p, variation=v)))
        r = analyze(path)
        r["coverage"] = coverage(path, r, b)
        r["energy"] = src.oven.energy_balance()
        series, dt = metal_series(path, r)
        r["hold_D"] = {D: {pp: hold_within(series[pp], thr, dt, D) for pp in PART_NAMES} for D in D_GRID}
        r["Tmax_D"] = {D: {pp: max(x for x in series[pp][:int(D * 60 / dt)] if x is not None) for pp in PART_NAMES}
                       for D in D_GRID}
        if i == 0:
            make_report(path, params=p, res=r, out=os.path.join(sub_r, "1_vyroby"))
        reps.append(r)

    holds_by_D = {D: {pp: [r["hold_D"][D][pp] for r in reps] for pp in PART_NAMES} for D in D_GRID}
    parts_ok, win = best_set(holds_by_D)
    D_std = base["after_load_min"]
    load_s = base["load_min"] * 60
    E_std = sum(stats["power"][:int(load_s + D_std * 60)]) / 3.6e6
    return {"setpoint": sp, "tag": tag, "placement": {pp: p_long[pp] for pp in PART_NAMES},
            "budget": b, "threshold": thr, "empty": r_empty, "reps": reps, "holds_by_D": holds_by_D,
            "best_parts": parts_ok, "window": win, "window_all": windows(holds_by_D, PART_NAMES),
            "D_std": D_std, "E_std_kWh": E_std, "P_steady_kW": mean(stats["power"][-900:]) / 1000,
            "energy_check": stats["energy"]}


def sensitivity_h(sp, base, thr, values=(12.0, 20.0, 30.0)):
    """Чутливість часу досягнення порогу (дійсні T моделі) до коефіцієнта тепловіддачі виробів h."""
    out = {}
    for h in values:
        p = dict(base, setpoint=sp, after_load_min=D_MAX)
        o = Oven(p, seed=101, phys={"h_part": h})
        reach = {}
        while o.t < o.total_seconds():
            o.step()
            if o.loaded:
                for name in PART_NAMES:
                    if name not in reach and o.parts[name] >= thr:
                        reach[name] = (o.t - base["load_min"] * 60) / 60
        out[h] = {name: reach.get(name) for name in PART_NAMES}
    return out


def choose(results):
    def key(r):
        Tm = max(max(rep["Tmax_D"][r["window"][0]][p] for rep in r["reps"]) for p in r["best_parts"]) if r["window"] else 999
        return (-len(r["best_parts"]), -len(r["window"]), Tm, r["E_std_kWh"])
    return min(results, key=key)


# ---------------- таблиці, графіки ----------------

def mm(xs):
    xs = [x for x in xs if x is not None]
    return (mean(xs), std(xs) if len(xs) > 1 else 0.0) if xs else (None, None)


def write_tables(rdir, results, cal):
    rows = [["Уставка, °C", "U повітря, °C", "U метал, °C", "Поріг, °C", "ΔT_пов–пов. (вип.), К", "ΔT пов–метал при порозі, К",
             "Δt порожня, °C", "σ_t порожня, °C", "Прогрів, хв", "Δt з виробами, °C", "σ_t з виробами, °C",
             "Відновлення, хв", "P сталий, кВт", f"E циклу (до D_ст), кВт·год",
             "Вироби в нормі при D_ст", "Найбільший набір у нормі", "Вікно D, хв", "Вікно D для всіх трьох, хв"]]
    for r in results:
        b, e = r["budget"], r["empty"]
        dts = [rep["dT"] for rep in r["reps"]]
        sig = [rep["sigma_t"] for rep in r["reps"]]
        rec = [rep["recover_min"] for rep in r["reps"]]
        std_ok = [p for p in PART_NAMES if all(verdict(rep["hold_D"][r["D_std"]][p]) == "Норма" for rep in r["reps"])]
        rows.append([c(r["setpoint"], 0), c(b["air"]["U"], 2), c(b["metal"]["U"], 2), c(r["threshold"], 2),
                     c(b["regime"]["dT_env"], 1), c(b["regime"]["dT_air_metal"], 1),
                     c(e["dT"], 2) if e else "—", c(e["sigma_t"], 3) if e else "—", c(e["heat_min"]) if e else "—",
                     c(mean(dts), 2), c(mean(sig), 3), c(mm(rec)[0]), c(r["P_steady_kW"], 1), c(r["E_std_kWh"], 1),
                     ", ".join(std_ok) or "жоден", ", ".join(r["best_parts"]) or "жоден",
                     f"{r['window'][0]}–{r['window'][-1]}" if r["window"] else "немає",
                     f"{r['window_all'][0]}–{r['window_all'][-1]}" if r["window_all"] else "немає"])
    table(os.path.join(rdir, "porivniannia_rezhymiv.csv"), rows)

    rows = [["Уставка, °C", "Виріб", "Точка", "Досягнення порогу, хв (сер. ± s)", "Tmax при D_ст, °C",
             "Витримка при D_ст, хв (повтори)", "Стан при D_ст", "Вікно D для виробу, хв (усі повтори)",
             "Tmax при D_max, °C"]]
    for r in results:
        for p in PART_NAMES:
            reach = [rep["parts"][p]["reach_min"] for rep in r["reps"]]
            m, s = mm(reach)
            holds = [rep["hold_D"][r["D_std"]][p] for rep in r["reps"]]
            w = windows(r["holds_by_D"], [p])
            vs = sorted(set(verdict(h) for h in holds))
            rows.append([c(r["setpoint"], 0), p, f"П{r['placement'][p]}",
                         "не досягає" if m is None else f"{c(m)} ± {c(s)}" + ("" if None not in reach else " (не в усіх)"),
                         c(max(rep["Tmax_D"][r["D_std"]][p] for rep in r["reps"])),
                         " / ".join(c(h) for h in holds), " / ".join(vs),
                         f"{w[0]}–{w[-1]}" if w else "немає", c(max(rep["Tmax_D"][D_MAX][p] for rep in r["reps"]))])
    table(os.path.join(rdir, "vyroby_po_rezhymah.csv"), rows)

    rows = [["D, хв"] + [f"{p} @ {int(r['setpoint'])} °C" for r in results for p in PART_NAMES]]
    for D in D_GRID:
        rows.append([D] + [c(mean(r["holds_by_D"][D][p])) for r in results for p in PART_NAMES])
    table(os.path.join(rdir, "vytrymka_vid_D.csv"), rows)

    rows = [["Складова"] + [f"{g} @ {int(r['setpoint'])} °C" for r in results for g in ("повітря", "метал")]]
    syms = None
    for r in results:
        b = r["budget"]
        for g in ("air", "metal"):
            rr = b["channels"][b[g]["worst"]]["rows"]
            syms = syms or {}
            for x in rr:
                syms.setdefault(x["sym"] + " — " + x["name"], {})[(r["setpoint"], g)] = x["u"]
    for k, v in syms.items():
        rows.append([k] + [c(v.get((r["setpoint"], g)), 4) if (r["setpoint"], g) in v else "—"
                           for r in results for g in ("air", "metal")])
    rows.append(["U (k = 2)"] + [c(r["budget"][g]["U"], 3) for r in results for g in ("air", "metal")])
    table(os.path.join(rdir, "neviznachenist_po_rezhymah.csv"), rows)

    rows = [["Цикл", "Група", "Макс |похибка|, °C", "U, °C", "У межах U, %"]]
    for r in results:
        for i, rep in enumerate(([r["empty"]] if r["empty"] else []) + r["reps"]):
            name = f"{int(r['setpoint'])} °C, " + ("порожня" if rep.get("empty") else f"повтор {i if r['empty'] else i + 1}")
            for g, v in (rep.get("coverage") or {}).items():
                rows.append([name, "повітря" if g == "air" else "метал", c(v["max_abs"], 2), c(v["U"], 2),
                             c(100 * v["within_U"], 1)])
    table(os.path.join(rdir, "pokryttia_U.csv"), rows)


def plots(rdir, results):
    cols = {"Лист": "#8b5cf6", "Труба": "#e8a317", "Опора": "#12a5a5"}
    fig = Figure(figsize=(12, 4), dpi=150)
    for k, r in enumerate(results):
        ax = fig.add_subplot(1, len(results), k + 1)
        for p in PART_NAMES:
            ys = [mean(r["holds_by_D"][D][p]) for D in D_GRID]
            ax.plot(D_GRID, ys, color=cols[p], lw=2, label=p)
        ax.axhspan(PAINT["hold_min"], PAINT["hold_max"], color="#2b8a3e", alpha=0.12)
        if r["window_all"]:
            ax.axvspan(r["window_all"][0], r["window_all"][-1], color="#2b8a3e", alpha=0.25)
        ax.axvline(r["D_std"], color="grey", ls="--", lw=1)
        ax.set(title=f"Уставка {r['setpoint']:g} °C, поріг {c(r['threshold'], 2)} °C", xlabel="D — час після завантаження, хв",
               ylabel="Неперервна витримка, хв" if k == 0 else None, ylim=(0, 50))
        ax.grid(alpha=0.3)
        if k == 0:
            ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(os.path.join(rdir, "7_vytrymka_vid_D.png"))

    fig = Figure(figsize=(12, 4), dpi=150)
    for k, r in enumerate(results):
        ax = fig.add_subplot(1, len(results), k + 1)
        rep = r["reps"][0]
        path = os.path.join(data_dir("sim"), NAME, r["tag"], "1_vyroby.csv")
        series, dt = metal_series(path, rep)
        tt = [i * dt / 60 for i in range(len(series["Лист"]))]
        for p in PART_NAMES:
            ax.plot(tt, series[p], color=cols[p], lw=1.6, label=p)
        ax.axhline(r["threshold"], color="red", ls=":", lw=1)
        ax.axvline(r["D_std"], color="grey", ls="--", lw=1)
        ax.set(title=f"Уставка {r['setpoint']:g} °C", xlabel="Час після завантаження, хв",
               ylabel="T металу, °C" if k == 0 else None, ylim=(100, 225))
        ax.grid(alpha=0.3)
        if k == 0:
            ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(os.path.join(rdir, "8_nagriv_po_rezhymah.png"))

    fig = Figure(figsize=(9, 4), dpi=150)
    ax = fig.add_subplot(111)
    x = range(len(results))
    ax.bar([i - 0.2 for i in x], [r["budget"]["air"]["U"] for r in results], 0.4, label="U повітря")
    ax.bar([i + 0.2 for i in x], [r["budget"]["metal"]["U"] for r in results], 0.4, label="U метал")
    ax.axhline(1.4, color="#1c6fb8", ls=":", lw=1, label="диплом: повітря 1,4")
    ax.axhline(2.0, color="#d9480f", ls=":", lw=1, label="диплом: метал 2,0")
    ax.set_xticks(list(x), [f"{r['setpoint']:g} °C" for r in results])
    ax.set(ylabel="U (k = 2), °C", title="Розширена невизначеність за режимами")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(rdir, "9_U_po_rezhymah.png"))


def jsonable(o):
    if isinstance(o, float) and (math.isinf(o) or math.isnan(o)):
        return str(o)
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    return o


def run(log=print):
    base = load_params()
    ddir, rdir = os.path.join(data_dir("sim"), NAME), os.path.join(report_dir("sim"), NAME)
    os.makedirs(rdir, exist_ok=True)
    log("Калібрування…")
    cal = calibrate(mode="sim")
    calibration_tables(rdir, cal)
    results = []
    for sp in SETPOINTS:
        results.append(run_setpoint(sp, base, cal, ddir, rdir, log=log))
    main = choose(results)
    log(f"Основний режим за критерієм: {main['setpoint']:g} °C")
    budget_tables(rdir, main["budget"])
    write_json(os.path.join(rdir, "neviznachenist_osnovnyi.json"), jsonable(main["budget"]))
    log("Додатковий сценарій: опора в П8…")
    alt = run_setpoint(main["setpoint"], base, cal, ddir, rdir, placement=ALT_PLACEMENT,
                       tag=f"{int(main['setpoint'])}_opora_P8", log=log)
    log("Чутливість до h…")
    sens = {r["setpoint"]: sensitivity_h(r["setpoint"], base, r["threshold"]) for r in results}
    rows = [["Уставка, °C", "h, Вт/(м²·К)"] + [f"{p}: досягнення порогу, хв" for p in PART_NAMES] + ["Розрив Опора − Лист, хв"]]
    for sp, d in sens.items():
        for h, rr in d.items():
            gap = (rr["Опора"] - rr["Лист"]) if rr["Опора"] is not None and rr["Лист"] is not None else None
            rows.append([c(sp, 0), c(h, 0)] + [c(rr[p]) if rr[p] is not None else "не досягає" for p in PART_NAMES] + [c(gap)])
    table(os.path.join(rdir, "chutlyvist_h.csv"), rows)
    write_tables(rdir, results, cal)
    write_tables_alt(rdir, alt)
    plots(rdir, results)
    out = {"base_params": base, "setpoints": SETPOINTS, "D_max": D_MAX, "main": main["setpoint"],
           "results": results, "alt": alt, "sens_h": sens}
    write_json(os.path.join(rdir, "rezultaty.json"), jsonable(strip(out)))
    import dyplom
    dyplom.write(os.path.join(BASE, "results_for_diploma.md"), out, cal, rdir)
    log("Готово: " + rdir)
    return out


def write_tables_alt(rdir, alt):
    rows = [["Виріб", "Точка", "Досягнення порогу, хв", "Витримка при D_ст, хв", "Вікно D, хв"]]
    for p in PART_NAMES:
        reach = mm([rep["parts"][p]["reach_min"] for rep in alt["reps"]])[0]
        w = windows(alt["holds_by_D"], [p])
        rows.append([p, f"П{alt['placement'][p]}", c(reach), " / ".join(c(rep["hold_D"][alt["D_std"]][p]) for rep in alt["reps"]),
                     f"{w[0]}–{w[-1]}" if w else "немає"])
    rows.append([])
    rows.append(["Вікно D для всіх трьох", f"{alt['window_all'][0]}–{alt['window_all'][-1]}" if alt["window_all"] else "немає"])
    table(os.path.join(rdir, "dodatkovo_opora_P8.csv"), rows)


def strip(out):
    """Без важких рядів для JSON."""
    def s(r):
        r = dict(r)
        r["reps"] = [{k: v for k, v in rep.items() if k not in ("Tmax_D",)} for rep in r["reps"]]
        return r
    return dict(out, results=[s(r) for r in out["results"]], alt=s(out["alt"]))


if __name__ == "__main__":
    run()
