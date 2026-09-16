"""Built-in stock animations — every install starts with these two:

    donut   the classic tumbling torus (a1k0n's math), mono/tintable
    logo    the motionfetch "M" drawing itself in ascii (see README credits)

Anything else, you convert yourself from a video or image.
"""

import math
import os
import random

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


LOGO_PATH = os.path.join(os.path.dirname(__file__), "assets", "logo.txt")


def logo_art():
    """The app's ASCII "M" (Letter M Logo from logowik.com, see README)."""
    with open(LOGO_PATH) as f:
        return f.read().rstrip("\n").split("\n")


def logo(width=None, height=None, frames=96):
    """The M drawing itself: cells appear in a diagonal sweep with a
    dithered frontier, then the finished logo holds for the rest of the
    loop. width/height are fixed by the artwork and ignored."""
    art = logo_art()
    h, w = len(art), max(len(line) for line in art)
    art = [line.ljust(w) for line in art]
    reveal = min(48, max(8, frames * 2 // 3))
    hold = max(frames - reveal, 8)
    rng = random.Random(3)
    jitter = [[rng.uniform(0, 14) for _ in range(w)] for _ in range(h)]
    out = []
    for step in range(reveal):
        t = (step + 1) / reveal * (w + h + 14)
        lines = []
        for y in range(h):
            row = []
            for x in range(w):
                ch = art[y][x]
                row.append(ch if x + y + jitter[y][x] < t else " ")
            lines.append("".join(row))
        out.append(lines)
    out.extend([list(art)] * hold)
    return out


GENERATORS = {
    "donut": (donut, "mono"),
    "logo": (logo, "mono"),
}


def make(kind, width=48, height=20, frames=None):
    """Render one stock animation -> (frames, meta). Sizes are taken from
    the frames themselves, since some generators fix their own canvas."""
    func, mode = GENERATORS[kind]
    frames_list = func(width, height, frames) if frames else func(width, height)
    h = len(frames_list[-1])
    w = max(len(line) for line in frames_list[-1])
    meta = {
        "mode": mode, "style": "generated", "fps": 15,
        "width": w, "height": h, "source": f"builtin:{kind}",
    }
    return frames_list, meta


def ensure_stock():
    """First run: give the library its two stock animations.
    -> list of names created (empty when the library already has content)."""
    from . import convert, library

    if library.list_all():
        return []
    created = []
    for kind in GENERATORS:
        frames, meta = make(kind)
        convert.pad_frames(frames, meta["width"])
        library.save(kind, frames, meta, overwrite=True)
        created.append(kind)
    return created
