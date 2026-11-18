"""Фізична модель камерної електричної печі полімеризації та трьох виробів.

Зосереджені теплові ємності (метод теплового балансу), крок інтегрування 0,5 с:
  ТЕНи (C_h) -> повітря (C_a) -> внутрішня обшивка, стелажі (C_w) -> ізоляція -> цех;
  витяжна вентиляція і відкриті двері виносять тепло з повітря;
  партія виробів (C_load) і три контрольні вироби нагріваються конвекцією та випромінюванням.
Температурне поле: 9 точок = середня температура повітря + локальна поправка
  δ_i = α_i·ΔT_цирк − β_i·(T_a − T_цех) [+ двері] з інерцією τ_i і турбулентними пульсаціями.
"""
import math
import random

from konfig import AIR, PART_NAMES

SIGMA = 5.670374e-8
T_CEH = 20.0

PHYS = {
    # Камера 3,6 × 1,8 × 2,0 м
    "L": 3.6, "W": 1.8, "H": 2.0,
    # 12 ТЕНів × 2,5 кВт
    "n_ten": 12, "P_ten": 2500.0,
    "m_ten": 3.0,            # кг сталі одного ТЕНа з оребренням
    "hA_ten": 150.0,         # Вт/К, тепловіддача ТЕНів до повітря (примусовий обдув)
    # Обшивка 1,5 мм + стелажі + прогріваємий шар ізоляції
    "m_lining": 400.0, "m_racks": 100.0, "c_steel_lining": 480.0,
    "m_ins_eff": 60.0, "c_ins": 840.0,
    "h_wall": 25.0,          # Вт/(м²·К), повітря -> обшивка
    "lambda_ins": 0.06, "delta_ins": 0.10,   # мінвата 100 мм
    "G_exhaust": 0.015,      # кг/с, витяжна вентиляція
    "G_door": 0.40,          # кг/с, газообмін через відкриті двері
    "m_circ": 1.6,           # кг/с, циркуляційний вентилятор (~2 м³/с при 190 °C)
    "cp_air": 1010.0,
    # Партія виробів, що завантажується разом з контрольними
    "m_load": 150.0, "tau_load": 400.0,
    # Контрольні вироби: маса на одиницю тепловіддаючої поверхні, кг/м²
    "h_part": 20.0,          # Вт/(м²·К), конвекція при швидкості 1,5–3 м/с
    "eps_part": 0.85,        # порошкове покриття
    "part_mA": {"Лист": 7850 * 0.0015 / 2,                 # лист 1,5 мм, нагрів з двох боків
                "Труба": 7850 * 0.002 / (1 + 0.3 * 0.95),  # стінка 2 мм, внутрішня поверхня ~30 % ефективна
                "Опора": 7850 * 0.016 / 2},                # фланець 16 мм, нагрів з двох боків
    # Регулятор (ПІ), власна термопара біля нагрівачів
    "Kp": 0.10, "Ti": 300.0,
    "alpha_ctrl": 0.6, "beta_ctrl": 0.004, "ctrl_offset": 0.5,
}

# Локальні поправки 9 точок: вплив нагрівачів (α), холодних зон і дверей (β), відкриття дверей (γ), інерція τ, с
ALPHA = [-0.3, -0.3, 0.1, 0.1, 0.0, 0.0, 0.9, 1.0, 0.2]
BETA = [0.040, 0.034, 0.012, 0.010, 0.018, 0.015, 0.0, 0.0, 0.006]
GAMMA = [0.25, 0.25, 0.05, 0.05, 0.15, 0.15, 0.02, 0.02, 0.08]
TAU_PT = [60, 55, 80, 80, 50, 50, 30, 30, 45]
VIEW_HEATER = [0.0, 0.0, 0.01, 0.01, 0.0, 0.0, 0.03, 0.03, 0.005]   # кутовий коефіцієнт датчика на ТЕНи
TURB_SIGMA, TURB_TAU = 0.15, 20.0


def c_steel(T):
    """Питома теплоємність вуглецевої сталі, Дж/(кг·К) (апроксимація 20–250 °C)."""
    return 450.0 + 0.28 * T


def derived(ph=PHYS):
    A_in = 2 * (ph["L"] * ph["W"] + ph["L"] * ph["H"] + ph["W"] * ph["H"])
    V = ph["L"] * ph["W"] * ph["H"]
    return {
        "A_in": A_in, "V": V,
        "P_max": ph["n_ten"] * ph["P_ten"],
        "C_h": ph["n_ten"] * ph["m_ten"] * 500.0,
        "C_w": (ph["m_lining"] + ph["m_racks"]) * ph["c_steel_lining"] + ph["m_ins_eff"] * ph["c_ins"],
        "hA_w": ph["h_wall"] * A_in,
        "UA_ins": ph["lambda_ins"] / ph["delta_ins"] * A_in,
    }


class Oven:
    """Стан печі. step() — 1 с модельного часу. true_state() — дійсні температури для вимірювального каналу."""

    DT = 0.5

    def __init__(self, params, seed=0, t_ceh=T_CEH, phys=None):
        self.p = params
        self.ph = dict(PHYS, **(phys or {}))
        self.d = derived(self.ph)
        self.rng = random.Random(seed)
        self.t_ceh = t_ceh
        self.t = 0
        self.T_h = self.T_a = self.T_w = t_ceh
        self.T_load = t_ceh
        self.local = [t_ceh] * 9
        self.turb = [0.0] * 9
        self.parts = {p: t_ceh for p in PART_NAMES}
        self.loaded = False
        self.integral = 0.0
        self.power = 0.0
        self.ctrl_true = t_ceh
        self.with_load = not params.get("empty", False)

    # --- режим ---
    def total_seconds(self):
        if self.p.get("empty"):
            return int(self.p.get("empty_min", 90) * 60)
        return int((self.p["load_min"] + self.p["after_load_min"]) * 60)

    def door_open(self):
        if not self.with_load:
            return False
        start = self.p["load_min"] * 60
        return start <= self.t < start + self.p["door_s"]

    # --- фізика ---
    def _rho_air(self):
        return 101325 / (287.05 * (self.T_a + 273.15))

    def _part_rate(self, name, T_air_loc):
        T = self.parts[name]
        Tk, Twk = T + 273.15, self.T_w + 273.15
        q = self.ph["h_part"] * (T_air_loc - T) + self.ph["eps_part"] * SIGMA * (Twk ** 4 - Tk ** 4)
        return q / (self.ph["part_mA"][name] * c_steel(T))

    def _substep(self, dt, door):
        ph, d = self.ph, self.d
        cp = ph["cp_air"]
        C_a = self._rho_air() * d["V"] * cp
        Q_ha = ph["hA_ten"] * (self.T_h - self.T_a)
        Q_aw = d["hA_w"] * (self.T_a - self.T_w)
        Q_ex = ph["G_exhaust"] * cp * (self.T_a - self.t_ceh)
        Q_door = ph["G_door"] * cp * (self.T_a - self.t_ceh) if door else 0.0
        Q_load = 0.0
        if self.loaded:
            C_load = ph["m_load"] * c_steel(self.T_load)
            Q_load = C_load / ph["tau_load"] * (self.T_a - self.T_load)
            self.T_load += Q_load / C_load * dt
        self.T_h += (self.power - Q_ha) / d["C_h"] * dt
        self.T_a += (Q_ha - Q_aw - Q_ex - Q_door - Q_load) / C_a * dt
        self.T_w += (Q_aw - d["UA_ins"] * (self.T_w - self.t_ceh)) / d["C_w"] * dt

        dT_circ = Q_ha / (ph["m_circ"] * cp)
        rise = self.T_a - self.t_ceh
        for i in range(9):
            target = self.T_a + ALPHA[i] * dT_circ - BETA[i] * rise - (GAMMA[i] * rise if door else 0.0)
            self.local[i] += (target - self.local[i]) * dt / TAU_PT[i]
        self.ctrl_true = self.T_a + ph["alpha_ctrl"] * dT_circ - ph["beta_ctrl"] * rise

        if self.loaded:
            for name in PART_NAMES:
                T_loc = self.air_point(int(self.p[name]) - 1)
                self.parts[name] += self._part_rate(name, T_loc) * dt

    def _control(self):
        ph = self.ph
        err = self.p["setpoint"] - (self.ctrl_true + ph["ctrl_offset"])
        u = ph["Kp"] * (err + self.integral / ph["Ti"])
        if 0.0 < u < 1.0:                 # анти-насичення інтегратора
            self.integral += err
        u = min(max(u, 0.0), 1.0)
        self.power = u * self.d["P_max"]

    def air_point(self, i):
        return self.local[i] + self.turb[i]

    def step(self):
        door = self.door_open()
        if door and not self.loaded:
            self.loaded = True
            self.parts = {p: self.t_ceh for p in PART_NAMES}
            self.T_load = self.t_ceh
        self._control()
        n = int(round(1.0 / self.DT))
        for _ in range(n):
            self._substep(self.DT, door)
        k = math.exp(-1.0 / TURB_TAU)
        for i in range(9):
            self.turb[i] = k * self.turb[i] + TURB_SIGMA * math.sqrt(1 - k * k) * self.rng.gauss(0, 1)
        self.t += 1

    def true_state(self):
        """Дійсні значення вимірюваних величин і умови вимірювання для кожного каналу."""
        air = [self.air_point(i) for i in range(9)]
        heat_env = self.T_h
        env = [self.T_w + VIEW_HEATER[i] * (heat_env - self.T_w) for i in range(9)]
        parts = {}
        for name in PART_NAMES:
            parts[name] = (self.parts[name], air[int(self.p[name]) - 1]) if self.loaded else None
        return {"t": self.t, "air": air, "env": env, "parts": parts,
                "ctrl": self.ctrl_true + self.ph["ctrl_offset"], "power": self.power,
                "T_a": self.T_a, "T_w": self.T_w, "T_h": self.T_h, "door": self.door_open(), "t_ceh": self.t_ceh}
