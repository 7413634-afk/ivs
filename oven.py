import json
import os
import random

T_CEH = 20.0
PARAMS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parametry.json")
DEFAULTS = {"setpoint": 200.0, "load_min": 35, "door_s": 120, "after_load_min": 40,
            "Лист": 9, "Труба": 6, "Опора": 1}
LIMITS = {"setpoint": (180, 220), "load_min": (20, 60), "door_s": (30, 300), "after_load_min": (30, 90)}

AIR = ["П1", "П2", "П3", "П4", "П5", "П6", "П7", "П8", "П9"]
PART_NAMES = ["Лист", "Труба", "Опора"]
CHANNELS = AIR + PART_NAMES

OFF_EMPTY = [-7.0, -6.0, -2.0, -1.5, -3.0, -2.5, 3.0, 4.0, -1.0]
OFF_LOAD = [-2.5, -2.0, -1.5, -1.0, -0.5, -0.5, 0.5, 0.5, -2.0]
TAU_AIR = [120, 105, 75, 70, 75, 70, 50, 45, 60]
TAU_DOOR = [120, 130, 450, 460, 160, 170, 520, 540, 300]

PART_TAU = {"Лист": 64.0, "Труба": 171.0, "Опора": 513.0}

SENSOR_ERR = [(0.6, 1.0020), (-0.4, 0.9990), (0.3, 1.0030), (-0.8, 1.0010),
              (0.5, 0.9985), (-0.2, 1.0025), (0.9, 0.9980), (-0.5, 1.0015),
              (0.1, 1.0005), (0.7, 0.9975), (-0.6, 1.0020), (0.4, 0.9995)]
NOISE = 0.15


def load_params():
    p = dict(DEFAULTS)
    if os.path.exists(PARAMS_FILE):
        with open(PARAMS_FILE, encoding="utf-8") as f:
            p.update(json.load(f))
    return p


def save_params(p):
    with open(PARAMS_FILE, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=2)


class DemoOven:

    def __init__(self, params=None, seed=None):
        self.p = params or load_params()
        self.rng = random.Random(seed)
        self.t = 0
        self.ctrl = T_CEH
        self.air = [T_CEH] * 9
        self.parts = {p: T_CEH for p in PART_NAMES}
        self.load_t = T_CEH
        self.integral = 0.0
        self.loaded = False

    def total_seconds(self):
        return (self.p["load_min"] + self.p["after_load_min"]) * 60

    def door_open(self):
        start = self.p["load_min"] * 60
        return start <= self.t < start + self.p["door_s"]

    def step(self):
        door = self.door_open()
        if door and not self.loaded:
            self.loaded = True

        err = self.p["setpoint"] - self.ctrl
        power = 0.08 * err + self.integral
        if 0.0 < power < 1.0:
            self.integral += 0.08 / 600 * err
        power = min(max(power, 0.0), 1.0)

        d = 0.15 * power - (self.ctrl - T_CEH) / 3600
        if door:
            d -= (self.ctrl - T_CEH) / 1200
        if self.loaded:
            d -= 0.25 * (self.ctrl - self.load_t) / 600
            self.load_t += (self.ctrl - self.load_t) / 600
        self.ctrl += d

        for i in range(9):
            target = self.ctrl + OFF_EMPTY[i] + (OFF_LOAD[i] if self.loaded else 0.0)
            self.air[i] += (target - self.air[i]) / TAU_AIR[i]
            if door:
                self.air[i] -= (self.air[i] - T_CEH) / TAU_DOOR[i]

        if self.loaded:
            for p in PART_NAMES:
                near = self.air[int(self.p[p]) - 1]
                self.parts[p] += (near - self.parts[p]) / PART_TAU[p]

        self.t += 1

    def read(self):
        true = self.air + [self.parts[p] for p in PART_NAMES]
        vals = []
        for k, T in enumerate(true):
            a, b = SENSOR_ERR[k]
            vals.append(round(a + b * T + self.rng.gauss(0, NOISE), 2))
        return self.t, vals, round(self.ctrl, 1)
