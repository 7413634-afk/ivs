"""Автоматичні тести розрахункових алгоритмів ІВС.  Запуск:  python -m unittest discover -s tests -v"""
import csv
import math
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import termopara as tc
from analiz import runs_above, settle_index, verdict, analyze
from dzherela import SimSource, Esp32Source, parse_line
from kalibrovka import fit_line, calibrate, simulate_readings, correct, REF_POINTS
from konfig import CHANNELS, AIR, PART_NAMES
from neviznachenist import components, combine, budget, K
from vymiriuvannia import radiation_error, Hardware, Channel, MEAS
from zapys import Recorder, run_cycle

P = {"setpoint": 200.0, "load_min": 50, "door_s": 120, "after_load_min": 40, "Лист": 9, "Труба": 5, "Опора": 1}


class TestTermopara(unittest.TestCase):
    def test_nist_table(self):
        # Значення з таблиці NIST ITS-90 для типу K, мВ
        for t, e in ((0, 0.000), (50, 2.023), (100, 4.096), (150, 6.138), (200, 8.138), (250, 10.153)):
            self.assertAlmostEqual(tc.emf(t), e, places=3)

    def test_inverse(self):
        for t in (-20, 0, 25, 190, 250, 400):
            self.assertAlmostEqual(tc.temp(tc.emf(t)), t, places=5)

    def test_class1(self):
        self.assertEqual(tc.CLASS1(200), 1.5)
        self.assertAlmostEqual(tc.CLASS1(500), 2.0)


class TestKalibrovka(unittest.TestCase):
    def test_fit_exact(self):
        x = [1, 2, 3, 4, 5]
        a, b = fit_line(x, [0.5 + 2 * xi for xi in x])
        self.assertAlmostEqual(a, 0.5)
        self.assertAlmostEqual(b, 2.0)

    def test_recovers_linear_error(self):
        ref = list(REF_POINTS)
        shown = [0.6 + 1.003 * T for T in ref]
        rd = {"ref": ref, "samples": {n: [[s, s + 0.01, s - 0.01] for s in shown] for n in CHANNELS}}
        cal = calibrate(rd, save=False)
        for n in CHANNELS:
            self.assertLess(cal[n]["max_resid"], 1e-6)
            self.assertAlmostEqual(correct(n, 0.6 + 1.003 * 190, cal), 190, places=4)  # a, b зберігаються з округленням

    def test_simulated_errors_within_class1_and_corrected(self):
        cal = calibrate(simulate_readings(), save=False)
        for n, r in cal.items():
            self.assertTrue(all(abs(e) <= 1.5 + 0.3 for e in r["errors"]), n)   # клас 1 + похибки MAX31856
            self.assertLess(r["max_resid"], 0.15, n)

    def test_reproducible(self):
        self.assertEqual(simulate_readings(), simulate_readings())


class TestNeviznachenist(unittest.TestCase):
    def test_combine(self):
        rows = [("a", "", "A", "", 0, 0.3, 9), ("b", "", "B", "", 0, 0.4, math.inf)]
        r = combine(rows)
        self.assertAlmostEqual(r["uc"], 0.5)
        self.assertAlmostEqual(r["U"], K * 0.5)
        self.assertAlmostEqual(r["nu_eff"], 0.5 ** 4 / (0.3 ** 4 / 9))

    def test_groups_have_specific_components(self):
        ch = {"n": 60, "s": [0.06] * 5, "s_pooled": 0.06, "s_res": 0.04}
        air = {r[0] for r in components(ch, "air")}
        metal = {r[0] for r in components(ch, "metal")}
        self.assertIn("u_вип", air)
        self.assertNotIn("u_вип", metal)
        self.assertIn("u_конт", metal)
        for sym in ("u_A", "u_ет", "u_кал", "u_ХС", "u_др", "u_q"):
            self.assertIn(sym, air & metal)

    def test_radiation_sign_and_size(self):
        self.assertLess(radiation_error(200, 190), 0)       # холодніші стінки -> датчик показує менше
        self.assertGreater(radiation_error(190, 200), 0)
        self.assertLess(abs(radiation_error(190, 180)), 3)

    def test_threshold_uses_U(self):
        cal = calibrate(simulate_readings(), save=False)
        b = budget("sim", cal, save=False)
        self.assertAlmostEqual(b["threshold"], 190 + b["metal"]["U"])
        self.assertGreater(b["air"]["U"], b["metal"]["U"])   # при прийнятих вхідних даних домінує випромінювання


class TestAnaliz(unittest.TestCase):
    def test_runs_above(self):
        xs = [1, 5, 5, 1, 5, 5, 5, None, 5]
        self.assertEqual(runs_above(xs, 4), [(1, 3), (4, 7), (8, 9)])

    def test_settle(self):
        s = [[0, 5, 9, 10, 10, 10], [0, 0, 10, 10, 11, 10]]
        self.assertEqual(settle_index(s, [10, 10], 1.0), 2)
        self.assertIsNone(settle_index([[0, 0, 0]], [10], 1.0))

    def test_verdict(self):
        self.assertEqual(verdict(11.9), "Не догрітий")
        self.assertEqual(verdict(12.0), "Норма")
        self.assertEqual(verdict(20.1), "Перетримка")

    def test_synthetic_file(self):
        """Δt, σ_t, максимальна неперервна витримка на штучних даних з відомою відповіддю."""
        d = tempfile.mkdtemp()
        path = os.path.join(d, "x.csv")
        n = 60 * 60
        rnd = random.Random(1)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["t_s"] + CHANNELS)
            for t in range(1, n + 1):
                air = [190 + i + (0.5 if t % 2 else -0.5) for i in range(9)]
                metal = [20.0] * 3
                if t >= 600:                                   # завантаження на 10 хв
                    metal = [195.0 if 600 + 300 <= t < 600 + 300 + 15 * 60 else 150.0] * 3
                w.writerow([t] + [f"{v:.3f}" for v in air + metal])
        r = analyze(path, use_cal=False, threshold=191.0, U_air=1.0,
                    meta={"mode": "sim", "params": {"load_min": 10}})
        self.assertAlmostEqual(r["dT"], 8.0, places=2)
        self.assertAlmostEqual(r["sigma_t"], 0.5, places=2)
        for p in PART_NAMES:
            self.assertAlmostEqual(r["parts"][p]["reach_min"], 5.0, places=1)
            self.assertAlmostEqual(r["parts"][p]["hold_min"], 15.0, places=1)
            self.assertEqual(r["parts"][p]["verdict"], "Норма")


class TestModel(unittest.TestCase):
    def test_reproducible(self):
        a, b = SimSource(P, seed=5), SimSource(P, seed=5)
        ra, rb = a.read_batch(300), b.read_batch(300)
        self.assertEqual([r.values for r in ra], [r.values for r in rb])

    def test_heats_to_setpoint_and_parts_ordered(self):
        d = tempfile.mkdtemp()
        rec = Recorder("sim", "c", d, {"params": P, "threshold": 190.6, "U_air": 1.5})
        path = run_cycle(SimSource(P, seed=3), rec)
        r = analyze(path, use_cal=False)
        self.assertTrue(185 < r["T_mean"] < 205)
        self.assertIsNotNone(r["heat_min"])
        self.assertLess(r["heat_min"], P["load_min"])
        reach = {p: r["parts"][p]["reach_min"] for p in PART_NAMES}
        self.assertLess(reach["Лист"], reach["Труба"])          # тонкий лист гріється швидше за трубу
        self.assertTrue(os.path.exists(path[:-4] + "_true.csv"))

    def test_measurement_error_bounded(self):
        hw = Hardware(2026)
        ch = Channel("П1", hw, random.Random(0))
        for T in (50, 190, 250):
            v, _ = ch.max31856(T, MEAS["T_cj_work"])
            self.assertLess(abs(v - T), 2.5)


class FakeSerial:
    def __init__(self, lines):
        self.lines = [l.encode() for l in lines]

    def readline(self):
        return self.lines.pop(0) if self.lines else b""

    def close(self):
        pass


class TestEsp32(unittest.TestCase):
    def line(self, ms, T, fault=0):
        return "D;%d;%s;%s;%X\n" % (ms, ";".join(f"{x:.4f}" for x in T), ";".join(["25.00"] * 12), fault)

    def test_parse(self):
        ms, T, cj = parse_line(self.line(1000, [190.0 + i for i in range(12)], fault=0b100))
        self.assertEqual(ms, 1000)
        self.assertIsNone(T[2])
        self.assertAlmostEqual(T[3], 193.0)
        self.assertEqual(len(cj), 12)
        self.assertIsNone(parse_line("# коментар"))
        self.assertIsNone(parse_line("D;1;2"))

    def test_source_rows(self):
        lines = ["# IVS start\n"] + [self.line(5000 + 1000 * k, [100.0] * 12) for k in range(3)]
        src = Esp32Source(P, "fake", serial_obj=FakeSerial(lines))
        rows = src.read_batch(10)
        self.assertEqual([r.t for r in rows], [1, 2, 3])
        self.assertEqual(rows[0].values, [100.0] * 12)
        self.assertIsNone(rows[0].ctrl)


if __name__ == "__main__":
    unittest.main()


class TestFizyka(unittest.TestCase):
    def test_energy_balance_closes(self):
        from model_pechi import Oven
        o = Oven(P, seed=1)
        for _ in range(o.total_seconds()):
            o.step()
        eb = o.energy_balance()
        self.assertLess(abs(eb["rel"]), 1e-9)
        self.assertGreater(eb["losses"], 0)

    def test_steady_power_equals_losses(self):
        from model_pechi import Oven, PHYS, derived
        o = Oven(dict(P, empty=True, empty_min=150), seed=1)
        for _ in range(o.total_seconds()):
            o.step()
        d = derived()
        loss = d["UA_ins"] * (o.T_w - 20) + PHYS["G_exhaust"] * PHYS["cp_air"] * (o.T_a - 20)
        self.assertAlmostEqual(o.power, loss, delta=0.03 * loss)
        self.assertLess(o.T_w, o.T_a)                      # обшивка холодніша за повітря

    def test_part_geometry(self):
        from model_pechi import part_geometry
        g = part_geometry()
        self.assertAlmostEqual(g["Лист"][0], 7850 * 0.5 * 0.5 * 0.0015, places=6)
        self.assertAlmostEqual(g["Труба"][0], 7850 * (0.04 ** 2 - 0.036 ** 2), places=6)
        self.assertAlmostEqual(g["Опора"][0], 7850 * 0.2 * 0.2 * 0.016, places=6)
        mA = {p: m / A for p, (m, A) in g.items()}
        self.assertLess(mA["Лист"], mA["Труба"])
        self.assertLess(mA["Труба"], mA["Опора"])

    def test_contact_beta_physics(self):
        from vymiriuvannia import contact_beta, contact_resistances
        r = contact_resistances(190)
        b = contact_beta(190)
        self.assertAlmostEqual(b, r["R_c"] / (r["R_c"] + r["R_air"]))
        self.assertTrue(0 < b < 1)
        # кращий контакт або більша площадка -> менший β
        self.assertLess(contact_beta(190, dict(MEAS, h_contact=3000)), b)
        self.assertLess(contact_beta(190, dict(MEAS, d_pad=0.015)), b)
        # оболонка без площадки -> датчик переважно показує повітря
        self.assertGreater(contact_beta(190, dict(MEAS, d_pad=0.0011, h_contact=2000, h_pad=1e-9)), 0.5)


class TestDoslidzhennia(unittest.TestCase):
    def test_hold_within(self):
        from doslidzhennia import hold_within
        xs = [100] * 60 + [195] * 1200 + [100] * 60          # 20 хв вище порогу, починаючи з 1-ї хв
        self.assertAlmostEqual(hold_within(xs, 191, 1.0, 11), 10.0)
        self.assertAlmostEqual(hold_within(xs, 191, 1.0, 30), 20.0)

    def test_windows_and_best_set(self):
        import doslidzhennia as ds
        # лист досягає порогу на 5-й, труба на 10-й, опора на 40-й хв і далі тримається
        reach = {"Лист": 5, "Труба": 10, "Опора": 40}
        holds = {D: {p: [max(0, D - reach[p])] * 3 for p in PART_NAMES} for D in ds.D_GRID}
        self.assertEqual(ds.windows(holds, ["Лист"]), list(range(17, 26)))
        self.assertEqual(ds.windows(holds, PART_NAMES), [])
        parts, win = ds.best_set(holds)
        self.assertEqual(parts, ["Лист", "Труба"])
        self.assertEqual(win, list(range(22, 26)))

    def test_regime_threshold_consistent(self):
        from doslidzhennia import regime_budget
        cal = calibrate(simulate_readings(), save=False)
        b, _ = regime_budget(dict(P, setpoint=210, after_load_min=70), cal)
        self.assertAlmostEqual(b["threshold"], 190 + b["metal"]["U"], places=6)
        self.assertGreater(b["regime"]["dT_air_metal"], 0)
