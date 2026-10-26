#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 dmitri sashchenko
"""filepile — консольный проводник, в котором папка выглядит как куча хлама.

Каждый файл — предмет в куче: папки — коробки, картинки — картины в рамах,
архивы — мешки, логи — брёвна. Чем больше файл, тем крупнее предмет.
←↑↓→ — выбрать, enter — открыть, backspace — наверх, q — выход.
"""
import argparse
import functools
import math
import os
import random
import select
import shlex
import shutil
import stat
import subprocess
import sys
import termios
import time
import tty
import unicodedata
import zlib

__version__ = "0.1.0"

WHITE = (255, 255, 255)
DARK = (30, 28, 34)
GLASS = (18, 22, 30)
PAPER = (236, 232, 218)
METAL = (172, 176, 184)
SOIL, SOIL_DARK = (78, 60, 46), (56, 44, 36)
DUST = (150, 140, 128)
LABEL = "\x1b[0;1;38;2;255;255;255;48;2;24;22;28m"

MAX_ITEMS = 250     # больше в кучу не валим: и смотреть не на что, и раскладка тормозит
FALL = 0.35         # за сколько секунд предмет долетает до кучи
TOSS = 0.9          # сколько летит подброшенный при встряске предмет, если взлетел на высоту окна
SINK = 1            # предметы чуть вдавлены друг в друга — так куча выглядит слежавшейся

# пропорции предмета (ширина, высота) от базового размера
SHAPES = {
    "box": (1.0, 0.75),
    "paper": (0.8, 0.62),
    "picture": (1.0, 0.8),
    "cassette": (1.0, 0.62),
    "tv": (0.95, 0.95),
    "sack": (0.85, 1.0),
    "floppy": (0.8, 0.8),
    "can": (0.6, 0.85),
    "battery": (1.1, 0.45),
    "log": (1.3, 0.45),
    "tire": (1.0, 0.55),
}

EXT = {}
for _kind, _exts in (
    ("picture", "png jpg jpeg gif webp bmp svg ico tif tiff heic avif xcf psd kra"),
    ("cassette", "mp3 flac ogg oga wav m4a opus aac mid midi"),
    ("tv", "mp4 mkv webm avi mov m4v wmv flv mpg mpeg"),
    ("sack", "zip tar gz tgz bz2 xz zst 7z rar deb rpm iso img apk jar whl"),
    ("floppy", "py js ts jsx tsx c h cc cpp hpp rs go sh bash zsh fish lua rb pl php "
               "java kt swift qml css scss html vue el vim nix"),
    ("paper", "txt md rst pdf doc docx odt rtf tex org epub djvu"),
    ("log", "log out err"),
    ("can", "json jsonc yaml yml toml ini conf cfg csv tsv xml env lock sql db sqlite "
            "sqlite3 desktop service"),
):
    for _e in _exts.split():
        EXT[_e] = _kind
NAMES = {"makefile": "floppy", "dockerfile": "floppy", "readme": "paper",
         "license": "paper", "copying": "paper", "changelog": "paper"}

COLORS = {
    "box": [(176, 128, 78), (160, 116, 70), (190, 146, 92)],
    "paper": [PAPER, (240, 226, 180), (220, 228, 236)],
    "picture": [(150, 104, 58), (200, 160, 70), (64, 56, 54)],          # рама
    "cassette": [(44, 44, 52), (210, 200, 180), (60, 90, 150)],
    "tv": [(120, 84, 56), (150, 150, 156), (72, 72, 80)],
    "sack": [(196, 170, 120), (170, 150, 110), (150, 160, 110)],
    "floppy": [(40, 40, 48), (52, 86, 160), (170, 52, 52), (70, 130, 80), (210, 180, 60)],
    "can": [(212, 80, 72), (64, 140, 200), (226, 190, 70), (110, 170, 80)],   # этикетка
    "battery": [(226, 150, 50), (200, 60, 60), (60, 150, 90), (90, 110, 200)],
    "log": [(120, 82, 52), (100, 70, 46)],
    "tire": [(34, 34, 38)],
}

MONTHS = "янв фев мар апр мая июн июл авг сен окт ноя дек".split()

# русская раскладка: те же физические клавиши
RU = {"й": "q", "р": "h", "о": "j", "л": "k", "д": "l", "у": "e", "ы": "s", "к": "r",
      "а": "f", "щ": "o", "ю": ".", "Ё": "~"}


def mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def shaded(c, x, y, w, h):
    """Край предмета: сверху-слева светлее, снизу-справа темнее."""
    if x == w - 1 or y == h - 1:
        return mix(c, DARK, 0.35)
    if x == 0 or y == 0:
        return mix(c, WHITE, 0.15)
    return c


def disc(px, cx, cy, r, color):
    n = int(r) + 1
    for dy in range(-n, n + 1):
        for dx in range(-n, n + 1):
            if dx * dx + dy * dy <= r * r:
                px[(round(cx + dx), round(cy + dy))] = color


# ----------------------------------------------------------------- предметы
# Каждый рисует себя в px в координатах 0..w-1 x 0..h-1, y растёт вниз.
# Всё, что вылезло за рамку, потом обрезается.

def draw_box(px, w, h, c, rnd):
    if rnd.random() < 0.4:      # открытая, клапаны торчат в стороны
        top = 2
        fw = max(2, w // 3)
        flap = mix(c, WHITE, 0.25)
        for x in list(range(fw)) + list(range(w - fw, w)):
            px[(x, 1)] = flap
            if x < fw // 2 or x >= w - fw // 2:
                px[(x, 0)] = flap
        for y in range(top, h):
            for x in range(w):
                px[(x, y)] = shaded(c, x, y - top, w, h - top)
        for x in range(1, w - 1):      # внутри темно
            px[(x, top)] = mix(c, DARK, 0.6)
        return
    lid = max(1, h // 5)
    for y in range(h):
        for x in range(w):
            px[(x, y)] = mix(c, WHITE, 0.15) if y < lid and x < w - 1 else shaded(c, x, y, w, h)
    tape = (222, 204, 156)
    for y in range(0, max(lid + 1, h // 3)):
        for x in range(w // 2 - max(0, w // 12), w // 2 + max(1, w // 12)):
            px[(x, y)] = tape
    if rnd.random() < 0.35 and h >= 5:     # «не кантовать»
        px[(2, h - 3)] = px[(3, h - 3)] = (200, 60, 50)


def draw_paper(px, w, h, c, rnd):
    cx, cy, rx, ry = (w - 1) / 2, (h - 1) / 2, w / 2, h / 2
    bumps = [rnd.uniform(0.8, 1.0) for _ in range(9)]
    shadow = mix(c, DARK, 0.15)
    for y in range(h):
        for x in range(w):
            u, v = (x - cx) / rx, (y - cy) / ry
            k = (math.atan2(v, u) + math.pi) / (2 * math.pi) * 9
            i, f = int(k) % 9, k - int(k)
            if math.hypot(u, v) <= bumps[i] * (1 - f) + bumps[(i + 1) % 9] * f:
                px[(x, y)] = shadow if v > 0.35 else c
    crease = mix(c, DARK, 0.3)
    for _ in range(max(1, w // 4)):     # складки — короткие ломаные
        x, y = rnd.randrange(w), rnd.randrange(h)
        for _ in range(rnd.randint(2, 4)):
            if (x, y) in px:
                px[(x, y)] = crease
            x += rnd.choice((-1, 1))
            y += rnd.choice((-1, 0, 1))


def draw_picture(px, w, h, c, rnd):
    fr = 1 if w < 10 else 2
    night = rnd.random() < 0.3
    sky_t, sky_b = ((28, 34, 70), (60, 70, 120)) if night else ((110, 160, 226), (190, 214, 240))
    hill = rnd.choice(((90, 150, 70), (130, 170, 80), (70, 120, 90)))
    if night:
        hill = mix(hill, DARK, 0.5)
    sun = (236, 236, 220) if night else (250, 210, 90)
    sx, phase = rnd.choice((fr + 2, w - fr - 3)), rnd.uniform(0, 6)
    for y in range(h):
        for x in range(w):
            if x < fr or y < fr or x >= w - fr or y >= h - fr:
                px[(x, y)] = mix(c, WHITE, 0.2) if x < fr or y < fr else mix(c, DARK, 0.3)
                continue
            t = (y - fr) / max(1, h - 2 * fr - 1)
            if y > (h - 1) * 0.62 + 1.3 * math.sin(x * 0.5 + phase):
                px[(x, y)] = hill
            elif math.hypot(x - sx, y - fr - 1.5) <= max(1.0, w / 11):
                px[(x, y)] = sun
            else:
                px[(x, y)] = mix(sky_t, sky_b, t)


def draw_cassette(px, w, h, c, rnd):
    label = rnd.choice(((230, 220, 190), (240, 150, 60), (220, 90, 90), (120, 190, 220)))
    for y in range(h):
        for x in range(w):
            px[(x, y)] = shaded(c, x, y, w, h)
    for y in range(1, max(2, h * 2 // 3)):
        for x in range(2, w - 2):
            px[(x, y)] = label
    for x in range(3, w - 3):       # строчка от руки на наклейке
        px[(x, 1 + (h > 6))] = mix(label, DARK, 0.4) if x % 3 else label
    wy0, wy1 = round(h * 0.38), max(round(h * 0.38) + 1, round(h * 0.6))
    for y in range(wy0, wy1):
        for x in range(round(w * 0.22), round(w * 0.78)):
            px[(x, y)] = GLASS
    reel = (200, 200, 204)
    for rx in (round(w * 0.3), round(w * 0.7) - 1):
        px[(rx, wy0)] = reel
        if wy1 - wy0 > 1:
            px[(rx, wy0 + 1)] = mix(reel, DARK, 0.4)
    for x in range(round(w * 0.25), round(w * 0.75)):     # выступ снизу
        px[(x, h - 1)] = mix(c, WHITE, 0.2)


def draw_tv(px, w, h, c, rnd):
    ant = max(2, h // 4)
    legs = 1 if h >= 9 else 0
    top, bot = ant, h - 1 - legs
    cx = w // 2
    for i in range(1, ant + 1):      # рогатая антенна
        px[(cx - i, ant - i)] = px[(cx + i, ant - i)] = (170, 170, 176)
    for y in range(top, bot + 1):
        for x in range(w):
            px[(x, y)] = shaded(c, x, y - top, w, bot - top + 1)
    knobs = max(2, w // 4)
    sx0, sx1, sy0, sy1 = 1, w - knobs - 1, top + 1, bot - 1
    snow = rnd.random() < 0.5        # ловит только рябь
    for y in range(sy0, sy1 + 1):
        for x in range(sx0, sx1 + 1):
            if (x in (sx0, sx1)) and (y in (sy0, sy1)):
                continue        # скруглённые углы экрана
            if snow:
                g = rnd.randint(70, 200)
                px[(x, y)] = (g, g, g)
            else:
                px[(x, y)] = (78, 100, 112)
    if not snow:
        px[(sx0 + 1, sy0 + 1)] = (200, 220, 220)    # блик
    kx = w - 1 - knobs // 2 - (knobs > 2)
    for ky in (top + 2, top + 4):
        if ky < bot:
            px[(kx, ky)] = DARK
    if legs:
        for x in (1, 2, w - 3, w - 2):
            px[(x, h - 1)] = DARK


def draw_sack(px, w, h, c, rnd):
    neck = max(2, h // 5)
    mid = (w - 1) / 2
    for y in range(h):
        if y < neck - 1:
            hw = w * (0.12 + 0.12 * (1 - y / max(1, neck - 1)))   # хвостик над завязкой
        elif y == neck - 1:
            hw = w * 0.1
        else:
            s = (y - neck) / max(1, h - neck - 1)
            hw = w / 2 * (0.3 + 0.7 * math.sin(min(1.0, s * 1.5) * math.pi / 2))
        for x in range(w):
            if abs(x - mid) <= hw:
                px[(x, y)] = mix(c, DARK, 0.3) if x > mid + hw - 1 or y == h - 1 else c
    for x in range(w):
        if (x, neck - 1) in px:
            px[(x, neck - 1)] = (110, 80, 54)   # верёвка
    if rnd.random() < 0.4 and h >= 7:           # заплатка
        pc = mix(c, (90, 120, 160), 0.5)
        px0, py0 = round(mid + rnd.choice((-2, 1))), round(h * 0.65)
        for dx in range(2):
            for dy in range(2):
                if (px0 + dx, py0 + dy) in px:
                    px[(px0 + dx, py0 + dy)] = pc


def draw_floppy(px, w, h, c, rnd):
    for y in range(h):
        for x in range(w):
            if y == 0 and x == w - 1:
                continue        # срезанный уголок
            px[(x, y)] = shaded(c, x, y, w, h)
    for y in range(0, max(2, round(h * 0.38))):
        for x in range(round(w * 0.25), round(w * 0.72)):
            px[(x, y)] = METAL
        if y:
            px[(round(w * 0.58), y)] = DARK     # окошко шторки
    for y in range(round(h * 0.52), h - 1):
        for x in range(round(w * 0.15), round(w * 0.85)):
            px[(x, y)] = mix(PAPER, DARK, 0.25) if (y - round(h * 0.52)) % 2 and 0 < x - round(w * 0.15) else PAPER


def draw_can(px, w, h, c, rnd):
    crushed = rnd.random() < 0.3
    l0, l1 = round(h * 0.25), round(h * 0.8)
    dent = rnd.randrange(l0, max(l0 + 1, l1)) if crushed else -1
    for y in range(h):
        for x in range(w):
            if crushed and abs(y - dent) <= 1 and x in (0, w - 1):
                continue    # мятая
            base = c if l0 <= y < l1 else METAL
            if y == (l0 + l1) // 2:
                base = mix(base, WHITE, 0.5)
            u = (x + 0.5) / w
            col = mix(base, DARK, 0.5 * (1 - math.sin(math.pi * u)))
            if abs(u - 0.32) < 0.5 / w + 0.05:
                col = mix(col, WHITE, 0.35)
            px[(x, y)] = col
    for x in range(1, w - 1):
        px[(x, 0)] = mix(METAL, WHITE, 0.3)


def draw_battery(px, w, h, c, rnd):
    nub = 1 if w < 10 else 2
    body = w - nub
    for y in range(h):
        v = (y + 0.5) / h
        k = 0.5 * (1 - math.sin(math.pi * v))
        for x in range(body):
            base = (58, 58, 64) if x < body * 0.3 else c
            col = mix(base, DARK, k)
            if abs(v - 0.3) < 0.5 / h + 0.04:
                col = mix(col, WHITE, 0.35)
            px[(x, y)] = col
        if h * 0.3 <= y < h * 0.7 or h <= 3:
            for x in range(body, w):
                px[(x, y)] = METAL
    if h >= 5:      # плюсик
        pxp, py = body - 3, h // 2
        for dx, dy in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1)):
            px[(pxp + dx, py + dy)] = mix(c, WHITE, 0.6)


def draw_log(px, w, h, c, rnd):
    r = (h - 1) / 2
    ex = max(1.0, r * 0.6)
    ecx = w - 1 - ex
    end, ring = (222, 184, 124), (176, 132, 84)
    off = [rnd.randrange(6) for _ in range(h)]
    for y in range(h):
        for x in range(w):
            u, v = (x - ecx) / ex, (y - r) / (r + 0.5)
            if u * u + v * v <= 1:      # торец с годовыми кольцами
                d = math.hypot(u, v)
                px[(x, y)] = DARK if d < 0.2 else ring if int(d * 3) % 2 else end
            elif x <= ecx:
                if x == 0 and y in (0, h - 1):
                    continue
                col = mix(c, DARK, 0.4) if (x + off[y]) % 6 == 0 else c
                if y == 0:
                    col = mix(col, WHITE, 0.15)
                elif y == h - 1:
                    col = mix(col, DARK, 0.3)
                px[(x, y)] = col


def draw_tire(px, w, h, c, rnd):
    cx, cy = (w - 1) / 2, (h - 1) / 2
    for y in range(h):
        for x in range(w):
            u, v = (x - cx) / (w / 2), (y - cy) / (h / 2)
            r = math.hypot(u, v)
            if not 0.5 <= r <= 1:
                continue    # в дырке видно то, что лежит за покрышкой
            col = c
            if r > 0.8 and int((math.atan2(v, u) + math.pi) * 5) % 2:
                col = mix(c, WHITE, 0.1)     # протектор
            if r < 0.62:
                col = mix(c, WHITE, 0.3)     # борт
            if v < -0.55 and r > 0.8:
                col = mix(col, WHITE, 0.15)
            px[(x, y)] = col


DRAW = {"box": draw_box, "paper": draw_paper, "picture": draw_picture,
        "cassette": draw_cassette, "tv": draw_tv, "sack": draw_sack, "floppy": draw_floppy,
        "can": draw_can, "battery": draw_battery, "log": draw_log, "tire": draw_tire}


@functools.lru_cache(maxsize=4096)
def sprite(kind, w, h, color, seed, dim):
    """Пиксели предмета и его верхний/нижний край по столбцам."""
    px = {}
    DRAW[kind](px, w, h, color, random.Random(seed))
    spr = {}
    for (x, y), c in px.items():
        if 0 <= x < w and 0 <= y < h:
            # скрытые файлы — пыльные и выцветшие
            spr[(x, y)] = mix(mix(c, (128, 128, 128), 0.5), DARK, 0.3) if dim else c
    top, bot = {}, {}
    for x, y in spr:
        top[x] = min(top.get(x, y), y)
        bot[x] = max(bot.get(x, y), y)
    return spr, top, bot


# --------------------------------------------------------------------- куча

def classify(name, is_dir, is_exec):
    if is_dir:
        return "box"
    low = name.lower()
    if low.split(".")[0] in NAMES:
        return NAMES[low.split(".")[0]]
    stem = low.lstrip(".")
    ext = stem.rsplit(".", 1)[-1] if "." in stem else ""
    if ext in EXT:
        return EXT[ext]
    return "battery" if is_exec else "tire"


def scan(path):
    out = []
    with os.scandir(path) as it:
        for e in it:
            try:
                st, broken = e.stat(), False
            except OSError:
                try:
                    st, broken = e.stat(follow_symlinks=False), True
                except OSError:
                    continue
            out.append({"name": e.name, "path": e.path, "link": e.is_symlink(),
                        "broken": broken, "dir": not broken and stat.S_ISDIR(st.st_mode),
                        "size": st.st_size, "mtime": st.st_mtime,
                        "exec": stat.S_ISREG(st.st_mode) and bool(st.st_mode & 0o111)})
    return out


def count_entries(path, cap=999):
    n = 0
    try:
        with os.scandir(path) as it:
            for _ in it:
                n += 1
                if n >= cap:
                    break
    except OSError:
        return None
    return n


def dress(e, count):
    """Превращает запись о файле в предмет: вид, размер, цвет."""
    kind = "tire" if e["broken"] else classify(e["name"], e["dir"], e["exec"])
    if e["dir"]:
        weight = 0.3 if count is None else clamp(math.log2(count + 1) / 8)
    else:
        weight = clamp((math.log10(e["size"] + 1) - 1.5) / 7)
    seed = zlib.crc32(e["name"].encode("utf-8", "surrogateescape"))
    s = (7 + 15 * weight) * random.Random(seed).uniform(0.9, 1.1)
    kw, kh = SHAPES[kind]
    colors = COLORS[kind]
    return dict(e, kind=kind, count=count, weight=weight, seed=seed,
                w0=max(5, round(s * kw)), h0=max(4, round(s * kh)),
                color=colors[seed % len(colors)], dim=e["name"].startswith("."))


def layout(items, width, height, seed):
    """Сбрасывает предметы в кучу по одному, крупные первыми.

    Предмет падает около середины и сползает: если под его серединой нет
    опоры — в сторону свеса, если рядом ощутимо ниже — туда. Так получается
    горка, а не башня и не ровный слой. Высоты считаются вверх от земли.
    Возвращает предметы в порядке падения — он же порядок отрисовки.
    """
    rnd = random.Random(seed)
    hm = [0] * width      # высота кучи над каждым столбцом
    order = sorted(items, key=lambda it: (-it["weight"], it["name"]))
    # большую кучу мельчим, чтобы она в основном влезала в окно высотой height
    area = sum(it["w0"] * it["h0"] for it in order) or 1
    k = min(1.0, math.sqrt(width * height * 0.4 / area))
    for i, it in enumerate(order):
        w, h = min(max(4, round(it["w0"] * k)), width), max(3, round(it["h0"] * k))
        spr, top, bot = sprite(it["kind"], w, h, it["color"], it["seed"], it["dim"])

        def lift(x, c):
            return hm[x + c] - (h - 1 - bot[c]) - SINK

        def rest(x):
            return max(0, max(lift(x, c) for c in bot))

        x = round(width / 2 - w / 2 + rnd.gauss(0, width * 0.12))
        x = min(max(x, 0), width - w)
        b = rest(x)
        for _ in range(2 * width):
            nx = x
            if b > 0:
                # опора — столбцы, которыми предмет лежит на куче
                props = [c for c in bot if lift(x, c) >= b]
                lo, hi = min(props), max(props)
                if hi < w * 0.35:
                    nx = x + 1      # свисает вправо — сползает вправо
                elif lo > w * 0.65:
                    nx = x - 1
            if nx == x:
                # скатывается, если в стороне ниже, чем позволяет крутизна склона
                best = (b, 0)
                for dx in sorted({2, 4, max(2, w // 2), max(2, w - 2)}):
                    for mx in (x - dx, x + dx):
                        if 0 <= mx <= width - w:
                            score = rest(mx) + dx * 0.4 + rnd.random() * 0.1
                            if score < best[0]:
                                best = (score, mx - x)
                if not best[1]:
                    break
                nx = x + best[1]
            nx = min(max(nx, 0), width - w)
            if nx == x:
                break
            x, b = nx, rest(nx)
        it.update(x=x, b=b, w=w, h=h, spr=spr, seq=i)
        for c, d in top.items():
            hm[x + c] = max(hm[x + c], b + h - d)
    return order


def center(it):
    return it["x"] + it["w"] / 2, it["b"] + it["h"] / 2


def neighbor(pile, sel, d):
    """Ближайший предмет в направлении d — по куче ходим глазами, а не по списку."""
    dx, dy = {"left": (-1, 0), "right": (1, 0), "up": (0, 1), "down": (0, -1)}[d]
    sx, sy = center(sel)
    best, score = sel, None
    for it in pile:
        if it is sel:
            continue
        ox, oy = center(it)
        ox, oy = ox - sx, oy - sy
        along = ox * dx + oy * dy
        if along <= 0:
            continue
        s = along + 2.5 * abs(ox * dy + oy * dx)
        if score is None or s < score:
            best, score = it, s
    return best


# ------------------------------------------------------------------ текст

def clean(s):
    """Имя файла может содержать что угодно, в том числе escape-последовательности."""
    return "".join(ch if ch.isprintable() else "?" for ch in s)


def width(s):
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def cut(s, n, left=False):
    """Обрезает строку до n знакомест, ставя «…» с конца или с начала."""
    if width(s) <= n:
        return s
    if n <= 1:
        return "…"[:n]
    chars = list(reversed(s)) if left else list(s)
    out, used = [], 1
    for ch in chars:
        used += width(ch)
        if used > n:
            break
        out.append(ch)
    return "…" + "".join(reversed(out)) if left else "".join(out) + "…"


def human(n):
    for unit in "BKMGT":
        if n < 1024 or unit == "T":
            if unit == "B":
                return "%dB" % n
            return ("%.1f%s" if n < 10 else "%d%s") % (n, unit)
        n /= 1024


def when(ts):
    t, now = time.localtime(ts), time.localtime()
    tail = "%02d:%02d" % (t.tm_hour, t.tm_min) if t.tm_year == now.tm_year else str(t.tm_year)
    return "%d %s %s" % (t.tm_mday, MONTHS[t.tm_mon - 1], tail)


def pretty_path(path):
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def describe(it):
    if it["dir"]:
        n = it["count"]
        size = "нет доступа" if n is None else "999+ шт." if n >= 999 else "%d шт." % n
    elif it["broken"]:
        size = "битая ссылка"
    else:
        size = human(it["size"])
    parts = [size, when(it["mtime"])]
    if it["link"]:
        try:
            parts.append("→ " + clean(os.readlink(it["path"])))
        except OSError:
            pass
    return " · ".join(parts)


# ---------------------------------------------------------------- проводник

class Pile:
    """Текущая папка, предметы в куче, выбор и анимация падения."""

    def __init__(self, path, hidden=False):
        self.path = path
        self.hidden = hidden
        self.query = ""
        self.searching = False
        self.shake = 0
        self.size = None
        self.entries = []
        self.counts = {}
        self.items = []     # предметы в порядке Tab: коробки первыми, по имени
        self.pile = []      # в порядке падения и отрисовки
        self.total = 0
        self.sel = None
        self.cam = -2       # высота нижней строки пикселей экрана; -2 — видно землю
        self.t0 = -1e9
        self.toss_from = None   # где лежали предметы до встряски: путь -> (x, b)
        self.note = ""
        self.note_until = 0.0
        self.hint_until = 0.0
        self.view_h = 0
        self.last_click = (None, 0.0)
        self.animate = False

    def say(self, text, secs=3):
        self.note, self.note_until = text, time.monotonic() + secs

    def open(self, path, select=None, animate=True):
        try:
            entries = scan(path)
        except OSError as e:
            self.say("не открыть %s: %s" % (clean(pretty_path(path)), e.strerror or e))
            return False
        self.path, self.entries, self.counts = path, entries, {}
        self.query, self.searching, self.cam = "", False, -2
        self.refilter(select, animate)
        return True

    def refilter(self, select=None, animate=False):
        if select is None and self.sel:
            select = self.sel["name"]
        q = self.query.lower()
        found = [e for e in self.entries
                 if (self.hidden or not e["name"].startswith(".")) and q in e["name"].lower()]
        self.total = len(found)
        if len(found) > MAX_ITEMS:   # коробки важнее, из файлов — самые крупные
            found = sorted(found, key=lambda e: (not e["dir"], -e["size"]))[:MAX_ITEMS]
        items = []
        for e in found:
            if e["dir"] and e["path"] not in self.counts:
                self.counts[e["path"]] = count_entries(e["path"])
            items.append(dress(e, self.counts.get(e["path"])))
        self.items = sorted(items, key=lambda it: (not it["dir"], it["name"].lower()))
        self.sel = next((it for it in self.items if it["name"] == select),
                        self.items[0] if self.items else None)
        self.size = None    # раскладка — при следующем кадре, под размер окна
        self.animate = animate

    def relayout(self, cols, height):
        seed = zlib.crc32(self.path.encode("utf-8", "surrogateescape")) + self.shake
        self.pile = layout(self.items, cols, height, seed)
        self.size = (cols, height)
        if self.animate:
            self.t0 = time.monotonic()
            self.animate = False
            stagger = min(0.035, 0.8 / max(1, len(self.pile)))
            rnd = random.Random(seed)
            frm = self.toss_from or {}
            for it in self.pile:
                if it["path"] in frm:
                    # подброшен из того места, где лежал: чем тяжелее, тем ниже взлетает
                    it["from"] = frm[it["path"]]
                    it["peak"] = height * rnd.uniform(0.2, 0.55) * (1.1 - 0.5 * it["weight"])
                    it["t_start"] = self.t0 + rnd.uniform(0, 0.08)
                    it["t_land"] = it["t_start"] + TOSS * math.sqrt(it["peak"] / height) + 0.15
                else:
                    it["from"] = None
                    it["t_start"] = self.t0 + it["seq"] * stagger
                    it["t_land"] = it["t_start"] + FALL
        self.toss_from = None

    def falling(self, now):
        return any(now < it.get("t_land", 0) + 0.2 for it in self.pile)

    def landed(self, it, now):
        return now - it.get("t_land", 0)

    def follow(self, H):
        """Двигаем камеру так, чтобы выбранный предмет и подпись над ним были видны."""
        it = self.sel
        if it:
            top, bottom = it["b"] + it["h"] + 3, it["b"]
            if top > self.cam + H:
                self.cam = top - H
            if bottom < self.cam + 2:
                self.cam = bottom - 2
        self.cam = max(-2, self.cam)

    def item_at(self, col, row):
        """Верхний предмет под знакоместом — по настоящим пикселям, не по рамке."""
        for it in reversed(self.pile):      # позже нарисован — лежит сверху
            top = it["b"] + it["h"] - 1
            for r in (2 * row, 2 * row + 1):
                hgt = self.cam + self.view_h - 1 - r
                if (col - it["x"], top - hgt) in it["spr"]:
                    return it
        return None

    def parent(self):
        up = os.path.dirname(self.path)
        if up != self.path:
            self.open(up, select=os.path.basename(self.path))


HINT = "enter открыть · ⌫ наверх · / поиск · . скрытые · e редактор · s встряхнуть · q выход"


def compose(px, texts, cols, crows):
    """Полублочная картинка cols x crows знакомест; texts — строки поверх: (ряд, столбец, текст, sgr)."""
    over = {}
    for row, col, text, sg in texts:
        if not 0 <= row < crows:
            continue
        for ch in text:
            w = width(ch)
            if 0 <= col and col + w <= cols:
                over[(row, col)] = (ch, sg)
                if w == 2:
                    over[(row, col + 1)] = ("", sg)
            col += w
    lines = []
    for cr in range(crows):
        line, last = [], None
        for c in range(cols):
            if (cr, c) in over:
                ch, sg = over[(cr, c)]
            else:
                t, b = px.get((c, 2 * cr)), px.get((c, 2 * cr + 1))
                if t is None and b is None:
                    ch, sg = " ", "\x1b[0m"
                elif b is None:
                    ch, sg = "▀", "\x1b[0;38;2;%d;%d;%dm" % t
                elif t is None:
                    ch, sg = "▄", "\x1b[0;38;2;%d;%d;%dm" % b
                else:
                    ch, sg = "▀", "\x1b[0;38;2;%d;%d;%d;48;2;%d;%d;%dm" % (t + b)
            if sg != last:
                line.append(sg)
                last = sg
            line.append(ch)
        line.append("\x1b[0m")
        lines.append("".join(line))
    return lines


def build_frame(p, cols, rows, now):
    reserve = 2 if rows >= 6 else 0
    crows = rows - reserve
    H = 2 * crows
    if p.size != (cols, H):
        p.relayout(cols, H)
    p.follow(H)
    p.view_h = H
    falling = p.falling(now)
    pulse = int(now * 2.2) % 2 == 0

    def put(x, hgt, c):
        r = H - 1 - (hgt - p.cam)
        if 0 <= r < H and 0 <= x < cols:
            px[(x, r)] = c

    px = {}
    for hgt in (-1, -2):        # земля
        for x in range(cols):
            put(x, hgt, SOIL_DARK if (x * 7 + hgt * 3) % 5 == 0 else SOIL)
    for it in p.pile:
        x, b = it["x"], it["b"]
        if falling and p.landed(it, now) < 0:
            start, land = it["t_start"], it["t_land"]
            u = clamp((now - start) / (land - start))
            if it["from"]:      # летит по дуге со старого места на новое
                x0, b0 = it["from"]
                x = round(x0 + (x - x0) * u)
                b = round(b0 + (b - b0) * u + it["peak"] * 4 * u * (1 - u))
            elif now < start:
                continue        # ещё не начал падать
            else:
                b += round(max(4, p.cam + H + 2 - b) * (1 - u) ** 2)
        lit = it is p.sel and pulse and not falling
        top = b + it["h"] - 1
        for (dx, dy), c in it["spr"].items():
            put(x + dx, top - dy, mix(c, WHITE, 0.4) if lit else c)
        if falling and 0 <= p.landed(it, now) < 0.18:      # пыль при приземлении
            for dx, dh in ((-1, 0), (-2, 1), (it["w"], 0), (it["w"] + 1, 1)):
                put(x + dx, b + dh, DUST)

    texts = []
    if not p.pile:
        msg = "ничего не нашлось" if p.query else "пусто — даже хлама нет"
        texts.append((crows // 2, (cols - width(msg)) // 2, msg, "\x1b[0;2m"))
    elif p.sel and not falling:     # подпись над выбранным предметом
        it = p.sel
        name = " %s%s " % (clean(it["name"]), "/" if it["dir"] else "")
        name = cut(name, cols)
        row = (H - 1 - (it["b"] + it["h"] - 1 - p.cam)) // 2 - 1
        col = it["x"] + it["w"] // 2 - width(name) // 2
        texts.append((max(0, row), min(max(0, col), cols - width(name)), name, LABEL))
    lines = compose(px, texts, cols, crows)

    if reserve:
        lines.append(status_line(p, cols))
        if p.searching:
            hint = "/" + p.query + "▏"
        elif now < p.note_until:
            hint = p.note
        elif now < p.hint_until:      # подсказка — только при запуске и после непонятной клавиши
            hint = HINT
        else:
            hint = ""
        lines.append("\x1b[2m" + cut(hint, cols) + "\x1b[0m")
    return lines


def status_line(p, cols):
    """Путь › имя   размер · вид · дата"""
    where = pretty_path(p.path)
    if p.query and not p.searching:
        where += " /" + p.query
    if p.total > len(p.items):
        where += " [в куче %d из %d]" % (len(p.items), p.total)
    where = clean(where)
    if not p.sel:
        return "\x1b[2m" + cut(where, cols, left=True) + "\x1b[0m"
    name = clean(p.sel["name"]) + ("/" if p.sel["dir"] else "")
    info = describe(p.sel)
    room = cols - width(info) - 3
    name = cut(name, max(1, room // 2 if width(where) > room // 2 else room - width(where) - 3))
    where = cut(where, max(0, room - width(name) - 3), left=True)
    left = where + " › " + name
    gap = max(1, cols - width(left) - width(info))
    out = "\x1b[2m%s › \x1b[0;1m%s\x1b[0m" % (where, name) + " " * gap + "\x1b[2m" + info + "\x1b[0m"
    return out if width(left) + gap + width(info) <= cols else "\x1b[0;1m" + cut(name, cols) + "\x1b[0m"


# -------------------------------------------------------------------- ввод

def read_keys(fd):
    """Клавиши строками, щелчки мыши — кортежами ("click", столбец, ряд) от нуля."""
    data = os.read(fd, 1024).decode("utf-8", "ignore")
    seqs = {"A": "up", "B": "down", "C": "right", "D": "left", "Z": "btab"}
    keys, i = [], 0
    while i < len(data):
        ch = data[i]
        if data.startswith("\x1b[<", i):    # мышь в режиме SGR: ESC [ < кнопка ; x ; y M|m
            j = i + 3
            while j < len(data) and data[j] not in "Mm":
                j += 1
            try:
                btn, x, y = map(int, data[i + 3:j].split(";"))
            except ValueError:
                btn = -1
            if j < len(data) and data[j] == "M" and btn == 0:   # нажатие левой, без колеса и модификаторов
                keys.append(("click", x - 1, y - 1))
            i = j + 1
            continue
        if ch == "\x1b":
            if i + 2 < len(data) and data[i + 1] in "[O" and data[i + 2] in seqs:
                keys.append(seqs[data[i + 2]])
                i += 3
                continue
            if i + 1 == len(data):
                keys.append("esc")
            # прочие escape-последовательности пропускаем целиком
            i += 1
            while i < len(data) and not data[i].isalpha() and data[i] != "~":
                i += 1
            i += 1
            continue
        keys.append({"\x7f": "back", "\x08": "back", "\t": "tab", "\r": "enter",
                     "\n": "enter"}.get(ch, ch))
        i += 1
    return keys


def open_file(p, it):
    opener = shutil.which("xdg-open") or shutil.which("open")
    if not opener:
        p.say("нечем открыть: нет xdg-open")
        return
    subprocess.Popen([opener, it["path"]], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def activate(p, it):
    if it["dir"]:
        p.open(it["path"])
    else:
        open_file(p, it)


def click(p, col, row):
    """Щелчок выделяет предмет, второй по нему же вскоре — открывает."""
    it = p.item_at(col, row)
    if it is None:
        return
    now = time.monotonic()
    prev, t = p.last_click
    p.searching = False
    p.sel = it
    if prev is it and now - t < 0.4:
        p.last_click = (None, 0.0)
        activate(p, it)
    else:
        p.last_click = (it, now)


def handle_key(key, p):
    """Возвращает "quit", "edit" или None."""
    if key == "\x03":
        return "quit"
    if isinstance(key, tuple):
        click(p, key[1], key[2])
        return None
    if p.searching:     # тут раскладку не переводим: имена бывают и русские
        if key == "enter":
            p.searching = False
        elif key == "esc":
            p.searching, p.query = False, ""
            p.refilter()
        elif key == "back":
            p.query = p.query[:-1]
            p.refilter()
        elif len(key) == 1 and key.isprintable():
            p.query += key
            p.refilter()
        return None
    key = RU.get(key, key)
    sel = p.sel
    if key == "q":
        return "quit"
    if key in ("left", "right", "up", "down") and sel:
        p.sel = neighbor(p.pile, sel, key)
    elif key in ("h", "j", "k", "l") and sel:
        p.sel = neighbor(p.pile, sel, {"h": "left", "j": "down", "k": "up", "l": "right"}[key])
    elif key in ("tab", "btab") and sel:
        i = p.items.index(sel) + (1 if key == "tab" else -1)
        p.sel = p.items[i % len(p.items)]
    elif key in ("enter", "o") and sel:
        activate(p, sel)
    elif key == "e" and sel:
        if sel["dir"]:
            p.say("внутри что-то есть...")
        else:
            return "edit"
    elif key in ("back", "-"):
        p.parent()
    elif key == "~":
        p.open(os.path.expanduser("~"))
    elif key == ".":
        p.hidden = not p.hidden
        p.refilter(animate=True)
    elif key in ("/", "f"):
        p.searching = True
    elif key == "esc" and p.query:
        p.query = ""
        p.refilter()
    elif key == "s":
        p.shake += 1
        p.toss_from = {it["path"]: (it["x"], it["b"]) for it in p.pile}
        p.refilter(animate=True)
    elif key == "r":
        p.open(p.path, select=sel["name"] if sel else None, animate=False)
    elif key not in ("left", "right", "up", "down", "h", "j", "k", "l", "tab", "btab", "esc"):
        p.hint_until = time.monotonic() + 6
    return None


def edit(path):
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    try:
        subprocess.call(shlex.split(editor) + [path])
    except OSError as e:
        return "не запустить %s: %s" % (editor, e.strerror)
    return None


# --------------------------------------------------------------------- цикл

def main():
    ap = argparse.ArgumentParser(description="filepile — проводник-куча хлама")
    ap.add_argument("path", nargs="?", default=".", help="с какой папки начать")
    ap.add_argument("-a", "--all", action="store_true", help="сразу показывать скрытые")
    ap.add_argument("--cwd-file", metavar="FILE",
                    help="при выходе записать сюда папку, где остановились (для cd)")
    ap.add_argument("--once", action="store_true", help="напечатать кадр и выйти")
    ap.add_argument("--version", action="version", version="filepile " + __version__)
    args = ap.parse_args()

    p = Pile(os.path.abspath(args.path), hidden=args.all)
    if not p.open(p.path, animate=False):
        sys.exit("filepile: " + p.note)

    if args.once or not sys.stdin.isatty() or not sys.stdout.isatty():
        cols, rows = shutil.get_terminal_size((80, 30))
        lines = build_frame(p, cols, rows, 0.0)
        print("\n".join(lines))
        return

    p.refilter(animate=True)     # при запуске куча тоже падает
    p.hint_until = time.monotonic() + 6
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    last = None

    def enter_screen():
        sys.stdout.write("\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1006h")   # + мышь
        tty.setcbreak(fd)

    def leave_screen():
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\x1b[?1006l\x1b[?1000l\x1b[0m\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()

    enter_screen()
    try:
        running = True
        while running:
            now = time.monotonic()
            cols, rows = shutil.get_terminal_size()
            lines = build_frame(p, cols, rows, now)
            frame = "\x1b[H" + "\r\n".join(l + "\x1b[K" for l in lines)
            if frame != last:       # не перерисовываем зря
                sys.stdout.write(frame)
                sys.stdout.flush()
                last = frame
            if select.select([fd], [], [], 1 / 30 if p.falling(now) else 0.1)[0]:
                for key in read_keys(fd):
                    action = handle_key(key, p)
                    if action == "quit":
                        running = False
                        break
                    if action == "edit":
                        leave_screen()
                        err = edit(p.sel["path"])
                        enter_screen()
                        last = None
                        p.open(p.path, select=p.sel["name"], animate=False)
                        if err:
                            p.say(err, 6)
    except KeyboardInterrupt:
        pass
    finally:
        leave_screen()
    if args.cwd_file:
        try:
            with open(args.cwd_file, "w") as f:
                f.write(p.path)
        except OSError as e:
            sys.exit("filepile: не записать %s: %s" % (args.cwd_file, e.strerror))


if __name__ == "__main__":
    main()
