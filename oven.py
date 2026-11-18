"""Параметри режиму, що задаються в інтерфейсі (parametry.json). Модель печі — у model_pechi.py."""
import os

from konfig import AIR, PART_NAMES, CHANNELS, BASE, read_json, write_json  # noqa: F401 (реекспорт)
from model_pechi import T_CEH  # noqa: F401

PARAMS_FILE = os.path.join(BASE, "parametry.json")
DEFAULTS = {"setpoint": 200.0, "load_min": 50, "door_s": 120, "after_load_min": 40,
            "Лист": 9, "Труба": 5, "Опора": 1}
LIMITS = {"setpoint": (180, 220), "load_min": (20, 90), "door_s": (30, 300), "after_load_min": (20, 90)}


def load_params():
    p = dict(DEFAULTS)
    p.update(read_json(PARAMS_FILE, {}) or {})
    return p


def save_params(p):
    write_json(PARAMS_FILE, {k: v for k, v in p.items() if k in DEFAULTS})
