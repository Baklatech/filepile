#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Рисует packaging/filepile.svg из той же раскладки кучи, что и сама программа."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import filepile as f  # noqa: E402

# маленькая куча с предметами разных видов
JUNK = [("box", True, 40), ("tv.mkv", False, 10 ** 8), ("sack.zip", False, 3 * 10 ** 7),
        ("tire", False, 4 * 10 ** 5), ("pic.png", False, 2 * 10 ** 6),
        ("floppy.py", False, 6000), ("can.json", False, 900), ("paper.md", False, 300)]

items = []
for name, is_dir, size in JUNK:
    e = {"name": name, "path": name, "link": False, "broken": False, "dir": is_dir,
         "size": size, "mtime": 0, "exec": False}
    items.append(f.dress(e, 40 if is_dir else None))
pile = f.layout(items, 80, 200, 5)

px = {}
for it in pile:
    top = it["b"] + it["h"] - 1
    for (dx, dy), c in it["spr"].items():
        px[(it["x"] + dx, -(top - dy))] = c
x0, x1 = min(x for x, _ in px), max(x for x, _ in px)
y0, y1 = min(y for _, y in px), max(y for _, y in px)
size = max(x1 - x0, y1 - y0) + 1 + 4            # квадрат с полями по 2 пикселя
ox = (size - (x1 - x0 + 1)) // 2 - x0
oy = size - 2 - 1 - y1                          # куча стоит на нижнем поле

rects = []
for y in range(y0, y1 + 1):
    x = x0
    while x <= x1:
        c = px.get((x, y))
        if c is None:
            x += 1
            continue
        run = 1
        while px.get((x + run, y)) == c:        # соседние пиксели одного цвета — один rect
            run += 1
        rects.append('<rect x="%d" y="%d" width="%d" height="1" fill="#%02x%02x%02x"/>'
                     % ((x + ox, y + oy, run) + c))
        x += run

out = os.path.join(HERE, "filepile.svg")
with open(out, "w") as fh:
    fh.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
             'shape-rendering="crispEdges">\n%s\n</svg>\n' % (size, size, "\n".join(rects)))
print(out, "%dx%d" % (size, size), len(rects), "rects")
