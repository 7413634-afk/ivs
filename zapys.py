"""Запис циклу вимірювання: CSV показів (1 Гц), для симуляції — CSV дійсних значень, JSON метаданих."""
import csv
import os
from datetime import datetime

from konfig import CHANNELS, CTRL, data_dir, write_json


def fmt(x):
    return "" if x is None else f"{x:.3f}".replace(".", ",")


class Recorder:
    def __init__(self, mode, name=None, folder=None, meta=None):
        folder = folder or data_dir(mode)
        os.makedirs(folder, exist_ok=True)
        name = name or "izmer_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = os.path.join(folder, name + ".csv")
        self.meta = dict(meta or {}, mode=mode, started=datetime.now().isoformat(timespec="seconds"))
        self.f = open(self.path, "w", newline="", encoding="utf-8-sig")
        self.w = csv.writer(self.f, delimiter=";")
        self.w.writerow(["t_s"] + CHANNELS + [CTRL])
        self.ft = self.wt = None
        if mode == "sim":
            self.ft = open(self.path[:-4] + "_true.csv", "w", newline="", encoding="utf-8-sig")
            self.wt = csv.writer(self.ft, delimiter=";")
            self.wt.writerow(["t_s"] + CHANNELS)

    def write(self, row):
        self.w.writerow([row.t] + [fmt(v) for v in row.values] + [fmt(row.ctrl)])
        if self.wt and row.true is not None:
            self.wt.writerow([row.t] + [fmt(v) for v in row.true])

    def close(self, **extra):
        for f in (self.f, self.ft):
            if f:
                f.close()
        self.f = self.ft = None
        self.meta.update(extra)
        write_json(self.path[:-4] + ".json", self.meta)
        return self.path


def run_cycle(source, recorder):
    """Повний цикл без інтерфейсу (серія експериментів, тести)."""
    while not source.finished:
        for row in source.read_batch(600):
            recorder.write(row)
    source.close()
    return recorder.close()
