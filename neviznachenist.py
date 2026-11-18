"""Бюджет невизначеності вимірювання температури (GUM / ДСТУ-Н РМГ 43): окремо для повітря і металу.

Для кожного каналу:  u_c = √(Σ u_i²), коефіцієнти чутливості c_i = 1, кореляції відсутні;
U = k·u_c, k = 2. Ефективне число ступенів свободи — за Велчем–Саттертуейтом.
Складові типу A і залишкова похибка калібрування беруться з файлу калібрування режиму (sim/real),
складові типу B — з UNC (обґрунтування у коментарях).
"""
import math

from konfig import AIR, PART_NAMES, CHANNELS, PAINT, BASE, read_json, write_json
from kalibrovka import CAL, load_calibration
from vymiriuvannia import MEAS, radiation_error, contact_beta
import os

K = 2.0
UNC = {
    "n_avg": 1,               # кількість відліків, що усереднюються перед порівнянням з порогом
    "T_nom": 190.0,           # робоча температура, для якої складається бюджет, °C
    "dT_env": 10.0,           # межа |T повітря − T поверхонь, які бачить датчик| у сталому режимі, К
                              # (≈ просторова неоднорідність Δt ≈ 12 °C: біля ТЕНів повітря гарячіше обшивки, біля дверей — холодніше)
    "dT_air_metal": 10.0,     # межа різниці «повітря − поверхня» на початку витримки, К
    "q_record": 0.01,         # роздільність запису у CSV, °C
}
SQ3 = math.sqrt(3)


def components(cal_ch, group, unc=UNC, m=MEAS, cal_cond=CAL):
    """unc може містити розрахункові для режиму T_nom, dT_env, dT_air_metal (див. doslidzhennia.regime_stats)."""
    """Список складових для одного каналу: (позначення, назва, тип, розподіл, межа/значення, u, ν)."""
    n = cal_ch["n"]
    npts = len(cal_ch["s"])
    s = cal_ch["s_pooled"] / math.sqrt(unc["n_avg"])
    dTcj = abs(m["T_cj_work"] + 3.0 - m["T_cj_cal"])
    a_cj = m["cj_tempco"] * dTcj + m["cj_grad"]
    q = max(m["q_tc"], unc["q_record"])
    u_ref = math.sqrt((cal_cond["U_ref"] / 2) ** 2 + (cal_cond["block_nonunif"] / SQ3) ** 2
                      + (cal_cond["block_stab"] / SQ3) ** 2)
    rows = [
        ("u_A", "Випадкова складова (тип A)", "A", "нормальний", s, s, npts * (n - 1)),
        ("u_ет", "Еталон Pt100 і калібратор", "B", "нормальний/рівномірний", cal_cond["U_ref"], u_ref, math.inf),
        ("u_кал", "Залишкова похибка після калібрування", "A", "нормальний", cal_ch["s_res"], cal_ch["s_res"], npts - 2),
        ("u_ХС", "Компенсація холодного спаю", "B", "рівномірний", a_cj, a_cj / SQ3, math.inf),
        ("u_др", "Дрейф термопари за міжкалібрувальний інтервал", "B", "рівномірний", m["drift_year"],
         m["drift_year"] / SQ3, math.inf),
        ("u_q", "Роздільність перетворення і запису", "B", "рівномірний", q / 2, q / (2 * SQ3), math.inf),
    ]
    if group == "air":
        T = unc["T_nom"]
        a = abs(radiation_error(T, T - unc["dT_env"], m))
        rows.append(("u_вип", "Теплове випромінювання (датчики повітря)", "B", "рівномірний", a, a / SQ3, math.inf))
    else:
        a = contact_beta(unc["T_nom"], m) * unc["dT_air_metal"]
        rows.append(("u_конт", "Контакт термопари з поверхнею металу", "B", "рівномірний", a, a / SQ3, math.inf))
    return rows


def combine(rows, k=K):
    uc = math.sqrt(sum(r[5] ** 2 for r in rows))
    denom = sum(r[5] ** 4 / r[6] for r in rows if r[6] != math.inf and r[6] > 0)
    nu = uc ** 4 / denom if denom else math.inf
    return {"uc": uc, "U": k * uc, "k": k, "nu_eff": nu}


def budget(mode="sim", cal=None, save=True, regime=None):
    """regime — розрахункові умови режиму {T_nom, dT_env, dT_air_metal}; без нього — значення UNC."""
    unc = dict(UNC, **(regime or {}))
    cal = cal if cal is not None else load_calibration(mode)
    if not cal:
        raise RuntimeError("немає файлу калібрування для режиму " + mode + " — спочатку виконайте калібрування")
    out = {"mode": mode, "k": K, "inputs": {"UNC": unc, "MEAS": MEAS, "CAL": CAL}, "channels": {}}
    for name in CHANNELS:
        group = "metal" if name in PART_NAMES else "air"
        rows = components(cal[name], group, unc)
        out["channels"][name] = dict(combine(rows), group=group,
                                     rows=[dict(zip(("sym", "name", "type", "dist", "limit", "u", "nu"), r))
                                           for r in rows])
    for group, names in (("air", AIR), ("metal", PART_NAMES)):
        worst = max(names, key=lambda n: out["channels"][n]["U"])
        out[group] = {"U": out["channels"][worst]["U"], "uc": out["channels"][worst]["uc"],
                      "nu_eff": out["channels"][worst]["nu_eff"], "worst": worst}
    out["threshold"] = threshold_from(out)
    if save:
        write_json(os.path.join(BASE, f"neviznachenist_{mode}.json"), _jsonable(out))
    return out


def threshold_from(b):
    """Поріг початку витримки за температурою металу: T_фарби + U_метал (гарантована відповідність)."""
    return PAINT["T_metal"] + b["metal"]["U"]


def _jsonable(o):
    if isinstance(o, float) and math.isinf(o):
        return "inf"
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_jsonable(v) for v in o]
    return o


def load_budget(mode="sim"):
    """Бюджет з файлу; якщо його немає, але є калібрування, — розраховується."""
    try:
        return budget(mode, save=True)
    except RuntimeError:
        return None


def current_threshold(mode="sim"):
    b = load_budget(mode)
    if b is None:
        return None, None
    return b["threshold"], b


if __name__ == "__main__":
    b = budget()
    for g in ("air", "metal"):
        print(g, {k: round(v, 3) if isinstance(v, float) else v for k, v in b[g].items()})
    print("Поріг витримки:", round(b["threshold"], 2))
    for r in b["channels"][b["metal"]["worst"]]["rows"]:
        print(f"  {r['sym']:7s} {r['u']:.4f}  {r['name']}")
