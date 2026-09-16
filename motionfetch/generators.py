"""Built-in animation generators — no video required.

    donut    the classic tumbling torus (a1k0n's math), mono/tintable
    matrix   digital rain with bright heads and fading tails, green
    cube     rotating wireframe cube, mono/tintable
"""

import math
import random

from .ansi import RESET, fg

DONUT_RAMP = ".,-~:;=!*#$@"


def donut(width=48, height=20, frames=200):
    """Torus rotating on two axes; one full A revolution per loop."""
    out = []
    ta, tb = 2 * math.pi, 4 * math.pi
    for f in range(frames):
        a = ta * f / frames
        b = tb * f / frames
        z = [0.0] * (width * height)
        chars = [" "] * (width * height)
        ca, sa, cb, sb = math.cos(a), math.sin(a), math.cos(b), math.sin(b)
        for tj in range(0, 628, 7):
            j = tj / 100
            ct, st = math.cos(j), math.sin(j)
            for ti in range(0, 628, 2):
                i = ti / 100
                sp, cp = math.sin(i), math.cos(i)
                h = ct + 2
                d = 1 / (sp * h * sa + st * ca + 5)
                t = sp * h * ca - st * sa
                x = int(width / 2 + (width * 0.6) * d * (cp * h * cb - t * sb))
                y = int(height / 2 + (height * 0.45) * d * (cp * h * sb + t * cb))
                o = x + width * y
                n = int(
                    8
                    * (
                        (st * sa - sp * ct * ca) * cb
                        - sp * ct * sa
                        - st * ca
                        - cp * ct * sb
                    )
                )
                if 0 <= y < height and 0 <= x < width and d > z[o]:
                    z[o] = d
                    chars[o] = DONUT_RAMP[n if n > 0 else 0]
        out.append(
            ["".join(chars[k * width : (k + 1) * width]) for k in range(height)]
        )
    return out


GLYPHS = "01" + "abcdefghijklmnopqrstuvwxyz" + "<>[]{}#$%&*+=;:/\\|"


def matrix(width=48, height=20, frames=200, seed=7):
    """Digital rain. Color frames: white-green heads, tails fading to dark."""
    rng = random.Random(seed)
    cols = []
    for _ in range(width):
        cols.append(
            {
                "y": rng.uniform(-height, 0),
                "speed": rng.uniform(0.3, 1.0),
                "tail": rng.randint(4, height - 2),
                "glyphs": [rng.choice(GLYPHS) for _ in range(height)],
            }
        )
    out = []
    # warm-up: let the rain fall for a while before recording, so the first
    # frame is not an empty screen
    for step in range(40 + frames):
        record = step >= 40
        grid = [[" ", None] for _ in range(width * height)]
        for x, c in enumerate(cols):
            c["y"] += c["speed"]
            if c["y"] - c["tail"] > height:
                c["y"] = rng.uniform(-height / 2, 0)
                c["speed"] = rng.uniform(0.3, 1.0)
                c["tail"] = rng.randint(4, height - 2)
            if rng.random() < 0.2:
                c["glyphs"][rng.randrange(height)] = rng.choice(GLYPHS)
            head = int(c["y"])
            for k in range(c["tail"]):
                y = head - k
                if not 0 <= y < height:
                    continue
                fade = 1 - k / c["tail"]
                if k == 0:
                    color = (200, 255, 200)
                else:
                    g = int(80 + 175 * fade)
                    color = (0, g, int(g * 0.35))
                grid[x + y * width] = [c["glyphs"][y % height], color]
        if not record:
            continue
        lines = []
        for y in range(height):
            parts, last = [], None
            for x in range(width):
                ch, color = grid[x + y * width]
                if color and color != last:
                    parts.append(fg(*color))
                    last = color
                parts.append(ch)
            lines.append("".join(parts) + RESET)
        out.append(lines)
    return out


CUBE_VERTS = [
    (x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)
]
CUBE_EDGES = [
    (a, b)
    for a in range(8)
    for b in range(a + 1, 8)
    if bin(a ^ b).count("1") == 1
]


def cube(width=48, height=20, frames=120):
    """Wireframe cube tumbling on two axes."""
    out = []
    for f in range(frames):
        a = 2 * math.pi * f / frames
        b = 4 * math.pi * f / frames
        ca, sa, cb, sb = math.cos(a), math.sin(a), math.cos(b), math.sin(b)
        pts = []
        for x, y, z in CUBE_VERTS:
            # rotate around Y then X
            x, z = x * cb + z * sb, -x * sb + z * cb
            y, z = y * ca - z * sa, y * sa + z * ca
            d = 1 / (z + 4)
            pts.append(
                (
                    width / 2 + width * 1.1 * d * x,
                    height / 2 + height * 1.05 * d * y,
                    d,
                )
            )
        grid = [" "] * (width * height)
        for i, j in CUBE_EDGES:
            x0, y0, d0 = pts[i]
            x1, y1, d1 = pts[j]
            steps = int(max(abs(x1 - x0), abs(y1 - y0)) * 2) + 1
            for s in range(steps + 1):
                t = s / steps
                x = int(x0 + (x1 - x0) * t)
                y = int(y0 + (y1 - y0) * t)
                d = d0 + (d1 - d0) * t
                if 0 <= x < width and 0 <= y < height:
                    grid[x + y * width] = "#" if d > 0.26 else "+"
        for x, y, d in pts:
            xi, yi = int(x), int(y)
            if 0 <= xi < width and 0 <= yi < height:
                grid[xi + yi * width] = "@"
        out.append(
            ["".join(grid[k * width : (k + 1) * width]) for k in range(height)]
        )
    return out


GENERATORS = {
    "donut": (donut, "mono"),
    "matrix": (matrix, "color"),
    "cube": (cube, "mono"),
}
