"""Спільні константи ІВС: канали, шляхи, режим фарби, розділення симуляції та реальних вимірювань."""
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))

AIR = ["П1", "П2", "П3", "П4", "П5", "П6", "П7", "П8", "П9"]
PART_NAMES = ["Лист", "Труба", "Опора"]
CHANNELS = AIR + PART_NAMES          # 12 вимірювальних каналів
CTRL = "Регулятор"

# Плата Adafruit 3263 (MAX31856) кожного каналу -> CS на ESP32 (спільна SPI: SCK 18, MISO 19, MOSI 23)
CS_PINS = dict(zip(CHANNELS, [4, 5, 13, 14, 15, 16, 17, 21, 22, 25, 26, 27]))

WHERE = ["двері, низ, ліво", "двері, низ, право", "глибина, низ, ліво", "глибина, низ, право",
         "двері, верх, ліво", "двері, верх, право", "нагрівач, верх, ліво", "нагрівач, верх, право", "центр"]
PART_DESC = {"Лист": "лист 1,5 мм", "Труба": "труба 40×40×2 мм", "Опора": "опора, фланець 16 мм"}

# Режим полімеризації WEG Politherm 26 SM Red Intemperie 80897 BR (за температурою МЕТАЛУ).
# Значення прийняті в дипломі; перед захистом звірити з паспортом (TDS) виробника.
PAINT = {"name": "WEG Politherm 26 SM Red Intemperie 80897 BR",
         "T_metal": 190.0, "hold_min": 12.0, "hold_max": 20.0}

MODES = ("sim", "real")              # результати симуляції та реальних вимірювань зберігаються окремо


def data_dir(mode):
    return os.path.join(BASE, "data", mode)


def report_dir(mode):
    return os.path.join(BASE, "zvit", mode)


def cal_file(mode):
    return os.path.join(BASE, f"kalibrovka_{mode}.json")


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
