from oven import PART_NAMES

PX = {1: (0.2, 0.88), 2: (0.8, 0.88), 3: (0.2, 0.12), 4: (0.8, 0.12),
      5: (0.2, 0.88), 6: (0.8, 0.88), 7: (0.2, 0.12), 8: (0.8, 0.12), 9: (0.5, 0.5)}
LEVELS = [("Низ · 0,2 м", [1, 2, 3, 4]), ("Центр · 1,0 м", [9]), ("Верх · 1,8 м", [5, 6, 7, 8])]
PART_COLORS = {"Лист": "#8b5cf6", "Труба": "#e8a317", "Опора": "#12a5a5"}


def temp_color(T):
    a = max(0.0, min(1.0, (T - 186) / 20))
    c1, c2, c3 = (28, 111, 184), (230, 220, 120), (217, 72, 15)
    A, B, t = (c1, c2, a * 2) if a < 0.5 else (c2, c3, (a - 0.5) * 2)
    return "#%02x%02x%02x" % tuple(round(v + (B[i] - v) * t) for i, v in enumerate(A))


def draw_plan(cv, temps, placement, col):
    cv.delete("all")
    pw, ph, gap, x0, y0 = 100, 190, 28, 14, 44
    for k, (title, points) in enumerate(LEVELS):
        x = x0 + k * (pw + gap)
        cv.create_text(x + pw / 2, 12, text=title, fill=col["muted"], font=("Times New Roman", 11))
        cv.create_rectangle(x, y0 - 9, x + pw, y0 - 3, fill=col["hot"], outline="")
        cv.create_text(x + pw / 2, y0 - 18, text="нагрівач", fill=col["hot"], font=("Times New Roman", 10))
        cv.create_rectangle(x, y0, x + pw, y0 + ph, outline=col["line"], width=2)
        cv.create_line(x + pw / 2, y0, x + pw / 2, y0 + ph, fill=col["muted"], dash=(3, 3))
        cv.create_rectangle(x, y0 + ph + 2, x + pw, y0 + ph + 8, fill=col["door"], outline="")
        cv.create_text(x + pw / 2, y0 + ph + 20, text="двері", fill=col["muted"], font=("Times New Roman", 10))
        for n in points:
            cx, cy = x + PX[n][0] * pw, y0 + PX[n][1] * ph
            T = temps[n - 1] if temps else None
            fill = col["soft"] if T is None else temp_color(T)
            cv.create_oval(cx - 15, cy - 15, cx + 15, cy + 15, fill=fill, outline=col["ink"])
            cv.create_text(cx, cy, text=f"П{n}", fill="#101418", font=("Times New Roman", 10, "bold"))
            if T is not None:
                ty = cy - 24 if PX[n][1] > 0.5 else cy + 25
                cv.create_text(cx, ty, text=f"{T:.1f}".replace(".", ","), fill=col["ink"], font=("Times New Roman", 11))
            here = [p for p in PART_NAMES if int(placement[p]) == n]
            for j, p in enumerate(here):
                sx = cx + 19 if PX[n][0] <= 0.5 else cx - 28
                sy = cy - 8 + j * 11
                cv.create_rectangle(sx, sy, sx + 9, sy + 9, fill=PART_COLORS[p], outline="")
