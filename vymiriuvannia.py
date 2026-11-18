"""Модель вимірювального каналу: термопара K (клас 1) -> плата Adafruit 3263 (MAX31856) -> ESP32.

Ланцюг для кожного з 12 каналів:
  1) методична похибка: випромінювання (повітря) або неідеальний контакт з поверхнею (метал);
  2) теплова інерція датчика;
  3) індивідуальне відхилення НСХ термопари (у межах допуску класу 1) + дрейф;
  4) MAX31856: ТЕРС гарячого спаю мінус ТЕРС холодного спаю, похибки підсилення і зміщення АЦП, шум;
  5) компенсація холодного спаю за внутрішнім датчиком MAX31856 (з його похибкою);
  6) лінеаризація, квантування 0,0078125 °C (19 біт).
Параметри «екземпляра» (заводські відхилення) задаються одним seed, тому калібрування і цикли
вимірювання стосуються одних і тих самих датчиків.
"""
import math
import random

import termopara as tc
from konfig import AIR, PART_NAMES, CHANNELS
from model_pechi import SIGMA

MEAS = {
    # MAX31856 (параметри з datasheet Maxim/Analog Devices; звірити з редакцією datasheet у дипломі)
    "q_tc": 0.0078125,        # роздільність ТП, °C
    "q_cj": 0.015625,         # роздільність датчика холодного спаю, °C
    "gain_err": 0.0015,       # межа відносної похибки перетворення напруги (±0,15 %)
    "offset_uV": 5.0,         # межа зміщення нуля, мкВ
    "noise": 0.06,            # СКВ шуму одного відліку (усереднення 4 відліки, фільтр 50 Гц), °C
    "cj_offset": 0.5,         # межа похибки датчика ХС при 25 °C, °C (datasheet: ±0,7 °C у −20…85 °C)
    "cj_tempco": 0.01,        # межа температурної залежності похибки ХС, °C/°C
    "cj_grad": 0.10,          # межа градієнта клема–датчик ХС, °C
    # Термопара K, клас 1 (IEC 60584-1)
    "tc_dev0": 0.8,           # межа відхилення при 0 °C, °C
    "tc_dev250": 1.4,         # межа відхилення при 250 °C, °C (≤ допуску 1,5 °C)
    "tc_nonlin": 0.05,        # межа нелінійної складової відхилення, °C
    "drift_year": 0.2,        # межа дрейфу за міжкалібрувальний інтервал (1 рік) при t ≤ 250 °C, °C
    # Методичні похибки
    "d_probe": 0.0015,        # діаметр термопари КТМС, м
    "v_air": 2.0,             # швидкість повітря біля датчика, м/с
    "eps_probe": 0.8,         # ступінь чорноти окисленої оболонки
    "tau_air": 9.0,           # постійна часу датчика повітря, с
    # Контактна ТП на металі: магнітний поверхневий зонд — спай у сталевій контактній площадці Ø d_pad,
    # притиснутій до неізольованої ділянки металу; оболонка КТМС і верх площадки омиваються повітрям.
    # Частка β = R_к / (R_к + R_пов) різниці «повітря − поверхня» потрапляє в показ.
    "d_pad": 0.010,           # діаметр контактної площадки, м
    "h_contact": 1000.0,      # контактна теплопровідність сухого контакту сталь–сталь (магнітне притискання), Вт/(м²·К)
    "k_metal": 50.0,          # теплопровідність сталі виробу (опір розтікання), Вт/(м·К)
    "h_pad": 25.0,            # тепловіддача від відкритого верху площадки до повітря, Вт/(м²·К)
    "k_sheath": 18.0,         # ефективна теплопровідність КТМС Ø1,5 мм (оболонка 12Х18Н10Т + MgO + жили), Вт/(м·К)
    "tau_metal": 4.0,         # постійна часу контактної ТП, с
    # Умови холодного спаю
    "T_cj_cal": 22.0,         # під час калібрування (лабораторія)
    "T_cj_work": 27.0,        # під час роботи (шафа ESP32 біля печі), повільно зростає на +3 °C
}


def h_probe(T_air, m=MEAS):
    """Коефіцієнт тепловіддачі до циліндричного датчика (Чурчилль–Бернштейн спрощено, Re < 1e4)."""
    Tk = T_air + 273.15
    nu = 1.458e-6 * Tk ** 1.5 / (Tk + 110.4) / (101325 / (287.05 * Tk))  # Сазерленд / густина
    k = 0.0241 * (Tk / 273.15) ** 0.81
    Re = m["v_air"] * m["d_probe"] / nu
    Pr = 0.70
    Nu = 0.3 + 0.62 * Re ** 0.5 * Pr ** (1 / 3) / (1 + (0.4 / Pr) ** (2 / 3)) ** 0.25
    return Nu * k / m["d_probe"]


def radiation_error(T_air, T_env, m=MEAS):
    """Похибка від променевого теплообміну датчика з оточенням (баланс енергії на датчику), °C."""
    h = h_probe(T_air, m)
    hr = 4 * m["eps_probe"] * SIGMA * (T_air + 273.15) ** 3
    return m["eps_probe"] * SIGMA * ((T_env + 273.15) ** 4 - (T_air + 273.15) ** 4) / (h + hr)


def contact_resistances(T_air=190.0, m=MEAS):
    """Термічні опори, К/Вт:
    R_к  = 1/(h_к·A_пл) + 1/(4·k_м·r_пл)              — контакт + розтікання в металі;
    R_р  = 1/√(h·P·k·A)                               — оболонка ТП як нескінченне ребро;
    R_пл = 1/(h_пл·A_пл)                              — відкритий верх площадки;
    R_пов = R_р ∥ R_пл."""
    d, dp = m["d_probe"], m["d_pad"]
    A_pad = math.pi * dp * dp / 4
    R_c = 1.0 / (m["h_contact"] * A_pad) + 1.0 / (4 * m["k_metal"] * dp / 2)
    R_fin = 1.0 / math.sqrt(h_probe(T_air, m) * math.pi * d * m["k_sheath"] * math.pi * d * d / 4)
    R_pad = 1.0 / (m["h_pad"] * A_pad)
    R_air = 1.0 / (1.0 / R_fin + 1.0 / R_pad)
    return {"R_c": R_c, "R_fin": R_fin, "R_pad": R_pad, "R_air": R_air}


def contact_beta(T_air=190.0, m=MEAS):
    r = contact_resistances(T_air, m)
    return r["R_c"] / (r["R_c"] + r["R_air"])


def contact_error(T_surface, T_air, m=MEAS):
    return contact_beta(T_air, m) * (T_air - T_surface)


class Hardware:
    """Заводські відхилення 12 датчиків і 12 мікросхем MAX31856 — фіксовані для екземпляра ІВС."""

    def __init__(self, seed=2026, m=MEAS):
        r = random.Random(seed)
        u = lambda a: r.uniform(-a, a)
        self.ch = {}
        for name in CHANNELS:
            self.ch[name] = {
                "e0": u(m["tc_dev0"]), "e250": u(m["tc_dev250"]), "enl": u(m["tc_nonlin"]),
                "drift": u(1.0),                       # частка межі дрейфу
                "gain": u(m["gain_err"]), "off_mV": u(m["offset_uV"]) / 1000,
                "cj0": u(m["cj_offset"]), "cj1": u(m["cj_tempco"]), "cjg": u(m["cj_grad"]),
            }


class Channel:
    def __init__(self, name, hw, rng, m=MEAS):
        self.name, self.e, self.rng, self.m = name, hw.ch[name], rng, m
        self.metal = name in PART_NAMES
        self.sensor = None                       # температура чутливого елемента (з інерцією)

    def tc_deviation(self, T, age_years=0.0):
        e = self.e
        lin = e["e0"] + (e["e250"] - e["e0"]) * T / 250.0
        nl = e["enl"] * math.sin(math.pi * T / 250.0)
        return lin + nl + e["drift"] * self.m["drift_year"] * age_years

    def max31856(self, T_junction, T_cj, age_years=0.0):
        """Показ MAX31856, °C, для температури гарячого спаю T_junction і холодного T_cj."""
        e, m = self.e, self.m
        T_hot = T_junction + self.tc_deviation(T_junction, age_years)
        T_cj_term = T_cj + e["cjg"]                                  # температура клем
        v = tc.emf(T_hot) - tc.emf(T_cj_term)                        # напруга на вході
        v = v * (1 + e["gain"]) + e["off_mV"]
        cj_meas = T_cj + e["cj0"] + e["cj1"] * (T_cj - 25.0)
        cj_meas = round(cj_meas / m["q_cj"]) * m["q_cj"]
        T = tc.temp(v + tc.emf(cj_meas), guess=T_junction)
        T += self.rng.gauss(0, m["noise"])
        return round(T / m["q_tc"]) * m["q_tc"], cj_meas

    def measure(self, T_true, T_cond, T_cj, dt=1.0, age_years=0.0):
        """T_true — дійсна температура об'єкта; T_cond — T оточення (повітря) або T повітря біля виробу (метал)."""
        m = self.m
        if self.metal:
            target, tau = T_true + contact_error(T_true, T_cond, m), m["tau_metal"]
        else:
            target, tau = T_true + radiation_error(T_true, T_cond, m), m["tau_air"]
        if self.sensor is None:
            self.sensor = target
        self.sensor += (target - self.sensor) * (1 - math.exp(-dt / tau))
        return self.max31856(self.sensor, T_cj, age_years)


class MeasuringSystem:
    """12 каналів ІВС, опитування раз на секунду."""

    def __init__(self, hw, seed=0, age_years=0.0, m=MEAS):
        self.rng = random.Random(seed)
        self.m = m
        self.age = age_years
        self.ch = {n: Channel(n, hw, self.rng, m) for n in CHANNELS}

    def T_cj(self, t):
        return self.m["T_cj_work"] + 3.0 * (1 - math.exp(-t / 3600.0))

    def read(self, state):
        """Повертає (показ, °C або None, для кожного каналу; температура ХС кожного каналу)."""
        t = state["t"]
        Tcj = self.T_cj(t)
        vals, cjs = [], []
        for i, n in enumerate(AIR):
            v, cj = self.ch[n].measure(state["air"][i], state["env"][i], Tcj, age_years=self.age)
            vals.append(v)
            cjs.append(cj)
        for n in PART_NAMES:
            st = state["parts"][n]
            if st is None:                       # виріб ще не завантажено: датчик у повітрі цеху
                self.ch[n].sensor = None
                v, cj = self.ch[n].max31856(state["t_ceh"], Tcj, self.age)
            else:
                v, cj = self.ch[n].measure(st[0], st[1], Tcj, age_years=self.age)
            vals.append(v)
            cjs.append(cj)
        return vals, cjs
