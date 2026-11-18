import csv
import glob
import os
import shutil
import tkinter as tk
from datetime import datetime
from tkinter import ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib
from matplotlib.figure import Figure

from oven import AIR, PART_NAMES, CHANNELS, LIMITS, load_params, save_params
from dzherela import DemoSource
from kalibrovka import calibrate, load_calibration, correct, REF_POINTS
from analiz import analyze
from zvit import make_report, OUT_DIR
from skhema import draw_plan, PART_COLORS

LIMIT = 191.9
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
KEEP = 5
STEP = 20
WHERE = ["двері, низ, ліво", "двері, низ, право", "глибина, низ, ліво", "глибина, низ, право",
         "двері, верх, ліво", "двері, верх, право", "нагрівач, верх, ліво", "нагрівач, верх, право", "центр"]
PART_DESC = {"Лист": "лист 1,5 мм", "Труба": "труба 40×40×2 мм", "Опора": "опора, фланець 16 мм"}

COL = {"bg": "#eef1f3", "panel": "#ffffff", "ink": "#15202b", "muted": "#5b6b78", "line": "#d3dae0",
       "hot": "#d9480f", "cold": "#1c6fb8", "ok": "#2b8a3e", "warn": "#c92a2a", "soft": "#f5f7f9",
       "door": "#8fb8dc", "center": "#2b8a3e", "other": "#c9d1d8"}
FONT = "Times New Roman"
F_BODY = (FONT, 12)
F_SMALL = (FONT, 11)
F_H1 = (FONT, 15, "bold")
F_H2 = (FONT, 13, "bold")
F_MONO = (FONT, 12)
F_EYEBROW = (FONT, 10, "bold")
matplotlib.rcParams["font.family"] = "serif"
matplotlib.rcParams["font.serif"] = [FONT, "DejaVu Serif"]
KEY_AIR = {0: COL["cold"], 8: COL["center"], 7: COL["hot"]}


def fmt(x):
    return f"{x:.2f}".replace(".", ",")


def c1(x):
    return f"{x:.1f}".replace(".", ",")


def cleanup(keep):
    files = sorted(glob.glob(os.path.join(DATA_DIR, "izmer_*.csv")))
    for f in files[:-keep] if keep else files:
        os.remove(f)
    names = {os.path.splitext(os.path.basename(f))[0] for f in glob.glob(os.path.join(DATA_DIR, "izmer_*.csv"))}
    if os.path.isdir(OUT_DIR):
        for d in os.listdir(OUT_DIR):
            if d not in names:
                shutil.rmtree(os.path.join(OUT_DIR, d), ignore_errors=True)


def card(parent, **kw):
    return tk.Frame(parent, bg=COL["panel"], highlightthickness=1, highlightbackground=COL["line"], **kw)


def label(parent, text, font=F_BODY, fg=None, bg=None, **kw):
    return tk.Label(parent, text=text, font=font, fg=fg or COL["ink"], bg=bg or COL["panel"], **kw)


class App:
    def __init__(self, root):
        self.root = root
        root.title("ІВС температурного поля печі полімеризації")
        root.configure(bg=COL["bg"])
        root.minsize(1180, 760)
        self.running = False
        self.file = None
        self.path = None
        self.done_path = None
        self.run_params = None
        self.cal = {}
        self.last_air = None
        self.setup_style()

        bar = tk.Frame(root, bg=COL["bg"])
        bar.pack(fill="x", padx=18, pady=(12, 8))
        self.btn_start = ttk.Button(bar, text="Старт", style="Accent.TButton", command=self.start)
        self.btn_stop = ttk.Button(bar, text="Стоп", command=self.stop, state="disabled")
        self.btn_cal = ttk.Button(bar, text="Калібрування", command=self.run_calibration)
        self.btn_rep = ttk.Button(bar, text="Звіт", command=self.run_report, state="disabled")
        for b in (self.btn_start, self.btn_stop, self.btn_cal, self.btn_rep):
            b.pack(side="left", padx=(0, 6))
        self.status = label(bar, "Готово", F_MONO, COL["muted"], COL["bg"])
        self.status.pack(side="left", padx=10)

        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        self.tab_meas = tk.Frame(self.nb, bg=COL["bg"])
        self.tab_res = tk.Frame(self.nb, bg=COL["bg"])
        self.nb.add(self.tab_meas, text="Вимірювання")
        self.nb.add(self.tab_res, text="Результат аналізу")
        self.build_measure()
        self.build_results()

        self.redraw_plan()
        self.prepare_plot()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    def setup_style(self):
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", font=F_BODY, background=COL["panel"], foreground=COL["ink"])
        st.configure("TButton", padding=(12, 5), background=COL["soft"], bordercolor=COL["line"])
        st.map("TButton", background=[("active", COL["line"])])
        st.configure("Accent.TButton", background=COL["hot"], foreground="#ffffff", bordercolor=COL["hot"])
        st.map("Accent.TButton", background=[("disabled", COL["line"]), ("active", "#b93c0b")],
               foreground=[("disabled", COL["muted"])])
        st.configure("TCheckbutton", background=COL["bg"])
        st.configure("TNotebook", background=COL["bg"], borderwidth=0)
        st.configure("TNotebook.Tab", padding=(14, 6), background=COL["soft"])
        st.map("TNotebook.Tab", background=[("selected", COL["panel"])])
        st.configure("Treeview", rowheight=26, font=F_MONO, background=COL["panel"], fieldbackground=COL["panel"])
        st.configure("Treeview.Heading", font=(FONT, 11, "bold"), background=COL["panel"], foreground=COL["muted"],
                     relief="flat", borderwidth=0, padding=(4, 4))
        st.map("Treeview.Heading", background=[("active", COL["panel"]), ("pressed", COL["panel"])],
               relief=[("active", "flat"), ("pressed", "flat")])
        st.map("Treeview", background=[("selected", COL["panel"])], foreground=[("selected", COL["ink"])])
        st.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        st.configure("Horizontal.TScale", background=COL["panel"])

    def build_measure(self):
        t = self.tab_meas
        left = tk.Frame(t, bg=COL["bg"])
        left.pack(side="left", fill="y", padx=(0, 12), pady=10)

        ctl = card(left, padx=14, pady=12)
        ctl.pack(fill="x")
        p = load_params()
        self.sp_var = tk.IntVar(value=int(p["setpoint"]))
        self.load_var = tk.IntVar(value=int(p["load_min"]))
        self.dur_var = tk.IntVar(value=int(p["after_load_min"]))
        self.slider(ctl, "Уставка регулятора", self.sp_var, *LIMITS["setpoint"], "°C")
        self.slider(ctl, "Завантаження виробів", self.load_var, *LIMITS["load_min"], "хв")
        self.slider(ctl, "Тривалість після завантаження", self.dur_var, *LIMITS["after_load_min"], "хв")

        label(ctl, "РОЗМІЩЕННЯ ВИРОБІВ", F_EYEBROW, COL["muted"]).pack(anchor="w", pady=(10, 4))
        row = tk.Frame(ctl, bg=COL["panel"])
        row.pack(fill="x")
        self.place = {}
        for i, part in enumerate(PART_NAMES):
            f = tk.Frame(row, bg=COL["panel"])
            f.grid(row=0, column=i, sticky="w", padx=(0, 10))
            h = tk.Frame(f, bg=COL["panel"])
            h.pack(anchor="w")
            dot = tk.Canvas(h, width=10, height=10, bg=COL["panel"], highlightthickness=0)
            dot.pack(side="left")
            dot.create_oval(1, 1, 9, 9, fill=PART_COLORS[part], outline="")
            label(h, part).pack(side="left", padx=(4, 0))
            v = tk.StringVar(value=f"П{p[part]}")
            cb = ttk.Combobox(f, textvariable=v, values=AIR, width=6, state="readonly")
            cb.pack(anchor="w", pady=(2, 0))
            cb.bind("<<ComboboxSelected>>", lambda e: self.params_changed())
            self.place[part] = v

        plan = card(left, padx=10, pady=10)
        plan.pack(fill="x", pady=(10, 0))
        label(plan, "СХЕМА ПЕЧІ · ВИГЛЯД ЗВЕРХУ", F_EYEBROW, COL["muted"]).pack(anchor="w")
        self.plan = tk.Canvas(plan, width=380, height=270, bg=COL["panel"], highlightthickness=0)
        self.plan.pack()
        self.part_labels = {}
        pl = tk.Frame(plan, bg=COL["panel"])
        pl.pack(fill="x", pady=(4, 0))
        for i, part in enumerate(PART_NAMES):
            lab = label(pl, "", F_MONO, PART_COLORS[part])
            lab.grid(row=0, column=i, sticky="w", padx=(0, 14))
            self.part_labels[part] = lab
        self.ctrl_lab = label(plan, "Регулятор: —", F_MONO, COL["muted"])
        self.ctrl_lab.pack(anchor="w", pady=(2, 0))

        right = card(t, padx=8, pady=8)
        right.pack(side="left", fill="both", expand=True, pady=10)
        self.fig = Figure(figsize=(8, 6), dpi=90, facecolor=COL["panel"])
        self.ax1 = self.fig.add_subplot(211)
        self.ax2 = self.fig.add_subplot(212, sharex=self.ax1)
        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    def slider(self, parent, text, var, lo, hi, unit):
        r = tk.Frame(parent, bg=COL["panel"])
        r.pack(fill="x", pady=(6, 0))
        label(r, text).pack(side="left")
        val = label(r, f"{var.get()} {unit}", F_MONO)
        val.pack(side="right")

        def moved(x):
            var.set(int(round(float(x))))
            val.config(text=f"{var.get()} {unit}")

        s = ttk.Scale(parent, from_=lo, to=hi, orient="horizontal", command=moved, length=330)
        s.set(var.get())
        s.pack(fill="x", pady=(2, 0))
        s.bind("<ButtonRelease-1>", lambda e: self.params_changed())
        return val

    def build_results(self):
        t = self.tab_res
        self.res_empty = label(t, "Результат з'явиться після завершення циклу вимірювання.", F_BODY,
                               COL["muted"], COL["bg"])
        self.res_empty.pack(anchor="w", pady=20)
        self.res_box = tk.Frame(t, bg=COL["bg"])

        self.tiles = tk.Frame(self.res_box, bg=COL["bg"])
        self.tiles.pack(fill="x", pady=(10, 10))
        self.tile_vals, self.tile_subs = {}, {}
        names = ["Регулятор", "Середня в печі", "Нерівномірність поля", "Найхолодніша точка", "Найгарячіша точка"]
        for i, name in enumerate(names):
            c = card(self.tiles, padx=14, pady=10)
            c.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 10, 0))
            self.tiles.grid_columnconfigure(i, weight=1, uniform="tile")
            label(c, name, F_SMALL, COL["muted"]).pack(anchor="w")
            v = label(c, "—", (FONT, 20, "bold"))
            v.pack(anchor="w")
            sub = label(c, "", F_SMALL, COL["muted"])
            sub.pack(anchor="w")
            self.tile_vals[name], self.tile_subs[name] = v, sub

        row = tk.Frame(self.res_box, bg=COL["bg"])
        row.pack(fill="both", expand=True)
        lp = card(row, padx=14, pady=12)
        lp.pack(side="left", fill="both", expand=True, padx=(0, 10))
        label(lp, "Повітря в печі", F_H2).pack(anchor="w")
        self.chart = Figure(figsize=(6, 2.4), dpi=90, facecolor=COL["panel"])
        self.chart_ax = self.chart.add_subplot(111)
        self.chart_canvas = FigureCanvasTkAgg(self.chart, master=lp)
        self.chart_canvas.get_tk_widget().pack(fill="x", pady=(4, 10))
        cols = ("p", "w", "m", "mn", "mx", "s")
        self.t1 = ttk.Treeview(lp, columns=cols, show="headings", height=9, selectmode="none")
        for c, h, w in zip(cols, ("Точка", "Розташування", "Середня, °C", "Мін, °C", "Макс, °C", "Стан"),
                           (60, 180, 105, 85, 85, 120)):
            self.t1.heading(c, text=h)
            self.t1.column(c, width=w, minwidth=w, anchor="w" if c in ("p", "w") else "center")
        self.t1.pack(fill="x")

        rp = card(row, padx=14, pady=12)
        rp.pack(side="left", fill="both", expand=True)
        label(rp, "Вироби", F_H2).pack(anchor="w")
        label(rp, "Норма: 15–25 хв при температурі металу не нижче 191,9 °C", F_SMALL, COL["muted"]).pack(
            anchor="w", pady=(0, 10))
        self.parts_box = tk.Frame(rp, bg=COL["panel"])
        self.parts_box.pack(fill="x", pady=(0, 14))
        cols = ("v", "pt", "r", "a", "mx", "s")
        self.t2 = ttk.Treeview(rp, columns=cols, show="headings", height=3, selectmode="none")
        for c, h, w in zip(cols, ("Виріб", "Точка", "Досяг 191,9 °C, хв", "Час ≥ 191,9 °C, хв", "Макс, °C", "Стан"),
                           (75, 60, 160, 155, 90, 125)):
            self.t2.heading(c, text=h)
            self.t2.column(c, width=w, minwidth=w, anchor="w" if c == "v" else "center")
        self.t2.pack(fill="x")
        self.param_lab = label(rp, "", F_SMALL, COL["muted"], justify="left")
        self.param_lab.pack(anchor="w", side="bottom")

    def current_params(self):
        p = load_params()
        p["setpoint"] = float(self.sp_var.get())
        p["load_min"] = int(self.load_var.get())
        p["after_load_min"] = int(self.dur_var.get())
        for part in PART_NAMES:
            p[part] = int(self.place[part].get()[1:])
        return p

    def params_changed(self):
        if self.running:
            return
        save_params(self.current_params())
        self.last_air = None
        self.redraw_plan()
        self.prepare_plot()
        self.status.config(text="Параметри збережено")

    def redraw_plan(self, parts=None, ctrl=None):
        placement = {p: self.place[p].get()[1:] for p in PART_NAMES}
        draw_plan(self.plan, self.last_air, placement, COL)
        for part in PART_NAMES:
            v = "—" if parts is None else c1(parts[part]) + " °C"
            self.part_labels[part].config(text=f"■ {part} · {self.place[part].get()}  {v}")
        self.ctrl_lab.config(text="Регулятор: " + ("—" if ctrl is None else c1(ctrl) + " °C"))

    def prepare_plot(self):
        p = self.current_params()
        total = p["load_min"] + p["after_load_min"]
        ymax = (int(p["setpoint"] + 15) // 25 + 1) * 25
        self.t_min, self.ctrl = [], []
        self.data = [[] for _ in CHANNELS]
        for ax in (self.ax1, self.ax2):
            ax.clear()
            ax.set_facecolor(COL["panel"])
            ax.set_xlim(0, total)
            ax.set_ylim(0, ymax)
            ax.grid(color=COL["line"], lw=0.8)
            ax.tick_params(colors=COL["muted"], labelsize=8)
            for s in ax.spines.values():
                s.set_color(COL["line"])
        self.ax1.set_title("Повітря в печі, °C", loc="left", fontsize=9, color=COL["muted"])
        self.ax2.set_title("Температура металу виробів, °C", loc="left", fontsize=9, color=COL["muted"])
        self.ax2.set_xlabel("Час, хв", fontsize=8, color=COL["muted"])
        self.lines = []
        for i, n in enumerate(AIR):
            if i in KEY_AIR:
                self.lines.append(self.ax1.plot([], [], lw=1.8, color=KEY_AIR[i], zorder=3)[0])
            else:
                self.lines.append(self.ax1.plot([], [], lw=1, color=COL["other"], zorder=2)[0])
        for part in PART_NAMES:
            self.lines.append(self.ax2.plot([], [], lw=2.2, color=PART_COLORS[part])[0])
        self.ctrl_line, = self.ax1.plot([], [], ls="--", lw=1.3, color=COL["ink"], zorder=4)
        self.ax2.axhline(LIMIT, color=COL["warn"], ls=":", lw=1.2)
        self.ax2.axvline(p["load_min"], color=COL["muted"], lw=0.8)
        self.ax2.text(p["load_min"] + 0.6, ymax * 0.93, "завантаження", fontsize=8, color=COL["muted"])
        h = [self.lines[0], self.lines[8], self.lines[7], self.lines[1], self.ctrl_line]
        self.ax1.legend(h, ["П1", "П9", "П8", "П2–П7", "регулятор"],
                        fontsize=7, ncol=5, loc="lower right", frameon=False)
        self.ax2.legend(self.lines[9:] + [self.ax2.lines[-2]],
                        [f"{part} · {self.place[part].get()}" for part in PART_NAMES] + ["191,9 °C"],
                        fontsize=7, ncol=4, loc="lower right", frameon=False)
        self.fig.tight_layout()
        self.canvas.draw()

    def begin(self):
        self.run_params = self.current_params()
        save_params(self.run_params)
        self.source = DemoSource(self.run_params)
        os.makedirs(DATA_DIR, exist_ok=True)
        cleanup(KEEP - 1)
        self.path = os.path.join(DATA_DIR, "izmer_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv")
        self.file = open(self.path, "w", newline="", encoding="utf-8-sig")
        self.writer = csv.writer(self.file, delimiter=";")
        self.writer.writerow(["t_s"] + CHANNELS + ["Регулятор"])
        self.cal = load_calibration()
        self.last_air = None
        self.prepare_plot()
        self.done_path = None
        self.running = True
        for b in (self.btn_start, self.btn_cal, self.btn_rep):
            b.config(state="disabled")
        self.btn_stop.config(state="normal")

    def start(self):
        self.begin()
        self.tick()

    def write(self, row):
        t, values, ctrl = row
        self.writer.writerow([t] + [fmt(v) for v in values] + [fmt(ctrl)])

    def collect(self, t, values, ctrl):
        values = [correct(n, v, self.cal) for n, v in zip(CHANNELS, values)]
        self.t_min.append(t / 60)
        self.ctrl.append(ctrl)
        loaded = t / 60 >= self.run_params["load_min"]
        for k, v in enumerate(values):
            self.data[k].append(v if k < 9 or loaded else float("nan"))
        return values

    def show(self, t, values, ctrl):
        values = [correct(n, v, self.cal) for n, v in zip(CHANNELS, values)]
        for k, line in enumerate(self.lines):
            line.set_data(self.t_min, self.data[k])
        self.ctrl_line.set_data(self.t_min, self.ctrl)
        self.canvas.draw_idle()
        self.last_air = values[:9]
        loaded = t / 60 >= self.run_params["load_min"]
        parts = {p: values[9 + i] for i, p in enumerate(PART_NAMES)} if loaded else None
        self.redraw_plan(parts, ctrl)
        mode = "з поправками" if self.cal else "без поправок"
        self.status.config(text=f"Вимірювання: {t / 60:.0f} / {self.source.total_min:.0f} хв, {mode}")

    def tick(self):
        if not self.running:
            return
        rows = self.source.read_batch(STEP)
        for row in rows:
            self.write(row)
            if row[0] % 10 == 0:
                self.collect(*row)
        if rows:
            self.show(*rows[-1])
        if self.source.finished:
            self.finish()
            return
        self.root.after(100, self.tick)

    def finish(self):
        self.done_path = self.path
        self.stop("Цикл завершено")
        self.btn_rep.config(state="normal")
        self.show_results()
        self.nb.select(self.tab_res)

    def stop(self, text="Вимірювання зупинено"):
        self.running = False
        if self.file:
            self.file.close()
            self.file = None
        for b in (self.btn_start, self.btn_cal):
            b.config(state="normal")
        self.btn_stop.config(state="disabled")
        if self.path:
            text += ". Дані: " + os.path.basename(self.path)
        self.status.config(text=text)

    def show_results(self):
        r = analyze(self.done_path, True)
        p = self.run_params
        self.res_empty.pack_forget()
        self.res_box.pack(fill="both", expand=True)
        pts = r["points"]
        self.tile_vals["Регулятор"].config(text=c1(r["ctrl"]) + " °C")
        self.tile_subs["Регулятор"].config(text=f"{r['ctrl_dev']:+.1f} °C від середньої".replace(".", ","))
        self.tile_vals["Середня в печі"].config(text=c1(r["T_mean"]) + " °C")
        self.tile_subs["Середня в печі"].config(text="за 9 точками")
        self.tile_vals["Нерівномірність поля"].config(text=c1(r["dT"]) + " °C")
        self.tile_subs["Нерівномірність поля"].config(text=f"{r['hottest']} − {r['coldest']}")
        cold, hot = r["coldest"], r["hottest"]
        self.tile_vals["Найхолодніша точка"].config(text=f"{cold} · {c1(pts[cold]['mean'])} °C", fg=COL["cold"])
        self.tile_subs["Найхолодніша точка"].config(text=WHERE[AIR.index(cold)])
        self.tile_vals["Найгарячіша точка"].config(text=f"{hot} · {c1(pts[hot]['mean'])} °C", fg=COL["hot"])
        self.tile_subs["Найгарячіша точка"].config(text=WHERE[AIR.index(hot)])

        self.t1.delete(*self.t1.get_children())
        for i, (n, pt) in enumerate(pts.items()):
            bad = n in r["cold_points"]
            self.t1.insert("", "end", values=(n, WHERE[i], c1(pt["mean"]), c1(pt["min"]), c1(pt["max"]),
                                              "нижче 191,9" if bad else "норма"))
        self.t2.delete(*self.t2.get_children())
        for part, v in r["parts"].items():
            reach = c1(v["reach_min"]) if v["reach_min"] is not None else "не досяг"
            self.t2.insert("", "end", values=(part, f"П{p[part]}", reach, c1(v["above_min"]),
                                              c1(v["Tmax"]), v["verdict"].lower()))

        for w in self.parts_box.winfo_children():
            w.destroy()
        for part, v in r["parts"].items():
            self.hold_bar(self.parts_box, part, v["above_min"], v["verdict"])

        self.param_lab.config(
            text=f"Уставка {p['setpoint']:g} °C · завантаження на {p['load_min']} хв · цикл {p['after_load_min']} хв · "
                 + ("поправки застосовано" if r["calibrated"] else "без поправок"))

        ax = self.chart_ax
        ax.clear()
        names = list(pts)
        means = [pts[n]["mean"] for n in names]
        colors = [COL["warn"] if n in r["cold_points"] else COL["cold"] for n in names]
        ax.bar(names, means, color=colors, width=0.62)
        lo, hi = min(min(pt["min"] for pt in pts.values()), LIMIT) - 3, max(pt["max"] for pt in pts.values()) + 2.5
        ax.set_ylim(lo, hi)
        ax.axhline(LIMIT, color=COL["warn"], ls="--", lw=1)
        for i, m in enumerate(means):
            ax.text(i, m + 0.3, c1(m), ha="center", va="bottom", fontsize=8, color=COL["ink"])
        ax.set_facecolor(COL["panel"])
        ax.tick_params(colors=COL["muted"], labelsize=8, length=0)
        ax.grid(axis="y", color=COL["line"], lw=0.7)
        ax.set_axisbelow(True)
        for side, sp in ax.spines.items():
            sp.set_visible(side == "bottom")
            sp.set_color(COL["line"])
        self.chart.tight_layout()
        self.chart_canvas.draw()

    def hold_bar(self, parent, part, minutes, verdict):
        color = {"Норма": COL["ok"], "Не догрітий": COL["warn"], "Перетримка": COL["hot"]}[verdict]
        f = tk.Frame(parent, bg=COL["panel"])
        f.pack(fill="x", pady=3)
        label(f, part, F_BODY, width=6, anchor="w").pack(side="left")
        W, H, scale = 340, 30, 45.0
        cv = tk.Canvas(f, width=W, height=H, bg=COL["panel"], highlightthickness=0)
        cv.pack(side="left", padx=(4, 10))
        x = lambda m: 2 + min(m, scale) / scale * (W - 4)
        cv.create_rectangle(x(0), 2, x(scale), 18, fill=COL["soft"], outline="")
        cv.create_rectangle(x(15), 2, x(25), 18, fill="#dff0e3", outline="")
        cv.create_rectangle(x(0), 6, max(x(0) + 2, x(minutes)), 14, fill=color, outline="")
        for m in (0, 15, 25, 45):
            cv.create_text(x(m), 25, text=str(m), fill=COL["muted"], font=(FONT, 8),
                           anchor="w" if m == 0 else "e" if m == 45 else "center")
        label(f, f"{c1(minutes)} хв · {verdict.lower()}", (FONT, 12, "bold"), color).pack(side="left")

    def run_calibration(self):
        if self.running:
            return
        result = calibrate()
        win = tk.Toplevel(self.root)
        win.title("Калібрування каналів")
        win.configure(bg=COL["bg"])
        c = card(win, padx=10, pady=10)
        c.pack(padx=12, pady=12)
        cols = ["ch"] + [f"e{int(T)}" for T in REF_POINTS] + ["a", "b", "res"]
        heads = ["Канал"] + [f"Похибка {int(T)} °C" for T in REF_POINTS] + ["a, °C", "b", "Залишкова, °C"]
        tree = ttk.Treeview(c, columns=cols, show="headings", height=len(result), selectmode="none")
        for col, h in zip(cols, heads):
            tree.heading(col, text=h)
            tree.column(col, width=70 if col == "ch" else 120, anchor="center")
        f = lambda x, s: format(x, s).replace(".", ",")
        for name, r in result.items():
            tree.insert("", "end", values=[name] + [f(e, "+.2f") for e in r["errors"]]
                        + [f(r["a"], "+.3f"), f(r["b"], ".5f"), f(r["max_resid"], ".3f")])
        tree.pack()
        label(c, "Еталон: Pt100, U = 0,1 °C", F_SMALL, COL["muted"]).pack(
            anchor="w", pady=(6, 0))
        self.status.config(text="Поправки збережено: kalibrovka.json")

    def run_report(self):
        if self.running or not self.done_path:
            return
        folder = make_report(self.done_path, True, self.run_params)
        self.status.config(text="Звіт збережено: zvit\\" + os.path.basename(folder))
        if hasattr(os, "startfile"):
            os.startfile(folder)

    def on_close(self):
        self.stop()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
