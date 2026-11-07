"""Text frames -> transparent PNGs sized to the terminal's own cells, for
the greeter (greet.py), which hands them to kitty to animate by itself.

Unlike export.py this draws no window chrome and no background: empty cells
stay transparent, so the terminal's own background (and its opacity) shows
through and the animation looks like text rather than a picture of text.

Each distinct character is drawn once into a cell-sized alpha mask; a frame
is then one numpy gather over those masks plus the per-cell colours, which
keeps a 120-frame render around a second. Block elements and braille are
drawn geometrically instead of from the font — kitty draws them that way
too, and a font's █ rarely fills the cell exactly, which would leave seams
between rows.
"""

import os
import shutil

import numpy as np
from PIL import Image, ImageDraw

from . import ansi, export

SUPERSAMPLE = 4


def _font(cw, ch):
    """The bundled-font search from export.py, scaled so one advance is
    one cell wide and the line still fits the cell's height."""
    probe = export.find_font(100)
    adv = probe.getlength("M") / 100 or 0.6
    asc, desc = probe.getmetrics()
    tall = (asc + desc) / 100 or 1.2
    size = max(4, int(min(cw / adv, ch / tall)))
    return export.find_font(size)


def _geometric(char, cw, ch):
    """Mask for block elements and braille, or None for anything else."""
    o = ord(char)
    m = np.zeros((ch, cw), np.float32)
    if char == "█":
        m[:] = 1
    elif char == "▀":
        m[: ch // 2] = 1
    elif char == "▄":
        m[ch // 2:] = 1
    elif char == "▌":
        m[:, : cw // 2] = 1
    elif char == "▐":
        m[:, cw // 2:] = 1
    elif 0x2800 <= o <= 0x28FF:
        bits = o - 0x2800
        # dot n -> (column, row) in the 2x4 grid
        dots = ((0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2), (0, 3), (1, 3))
        s = SUPERSAMPLE
        img = Image.new("L", (cw * s, ch * s), 0)
        d = ImageDraw.Draw(img)
        r = min(cw / 2, ch / 4) * 0.36 * s
        for n, (cx, cy) in enumerate(dots):
            if bits & (1 << n):
                x = (cx + 0.5) * cw / 2 * s
                y = (cy + 0.5) * ch / 4 * s
                d.ellipse((x - r, y - r, x + r, y + r), fill=255)
        img = img.resize((cw, ch), Image.BOX)
        m = np.asarray(img, np.float32) / 255
    else:
        return None
    return m


class Atlas:
    """char -> index into a stack of (ch, cw) alpha masks, built lazily."""

    def __init__(self, cw, ch):
        self.cw, self.ch = cw, ch
        self.font = None
        self.index = {" ": 0}
        self.masks = [np.zeros((ch, cw), np.float32)]

    def _glyph(self, char):
        if self.font is None:
            self.font = _font(self.cw, self.ch)
        asc, desc = self.font.getmetrics()
        img = Image.new("L", (self.cw, self.ch), 0)
        top = (self.ch - (asc + desc)) // 2
        ImageDraw.Draw(img).text((0, top + asc), char, font=self.font,
                                 fill=255, anchor="ls")
        return np.asarray(img, np.float32) / 255

    def get(self, char):
        i = self.index.get(char)
        if i is None:
            m = _geometric(char, self.cw, self.ch)
            self.masks.append(m if m is not None else self._glyph(char))
            i = self.index[char] = len(self.masks) - 1
        return i

    def stack(self):
        return np.stack(self.masks)


def render_frame(lines, meta, atlas, base_fg):
    cols, rows = meta["width"], meta["height"]
    cw, ch = atlas.cw, atlas.ch
    idx = np.zeros((rows, cols), np.int32)
    fg = np.zeros((rows, cols, 3), np.float32)
    bg = np.zeros((rows, cols, 3), np.float32)
    has_bg = np.zeros((rows, cols), bool)
    for y, line in enumerate(lines[:rows]):
        for x, (c, f, b) in enumerate(export.parse_line(line, base_fg)[:cols]):
            idx[y, x] = atlas.get(c)
            fg[y, x] = f
            if b is not None:
                bg[y, x] = b
                has_bg[y, x] = True

    masks = atlas.stack()
    # (rows, cols, ch, cw) -> (rows*ch, cols*cw)
    alpha = masks[idx].transpose(0, 2, 1, 3).reshape(rows * ch, cols * cw)

    def grow(a):
        return np.repeat(np.repeat(a, ch, axis=0), cw, axis=1)

    fg_px, bg_px, bg_on = grow(fg), grow(bg), grow(has_bg)
    a = alpha[..., None]
    rgb = np.where(bg_on[..., None], fg_px * a + bg_px * (1 - a), fg_px)
    out_a = np.where(bg_on, 1.0, alpha)
    rgba = np.dstack([rgb, out_a * 255]).clip(0, 255).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def render(meta, frames, cw, ch, tint, out_dir):
    """Write frames/NNNN.png into out_dir, atomically (a `done` marker last,
    and the whole directory renamed into place)."""
    base_fg = ansi.parse_hex(tint) if tint else None
    base_fg = base_fg or export.FG
    atlas = Atlas(cw, ch)
    # Per-process scratch: two terminals opened together both miss the
    # cache and render the same thing; whichever finishes second just
    # throws its copy away.
    tmp = f"{out_dir}.new{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    for i, lines in enumerate(frames):
        img = render_frame(lines, meta, atlas, base_fg)
        img.save(os.path.join(tmp, f"{i:04d}.png"), compress_level=1)
    open(os.path.join(tmp, "done"), "w").close()
    if os.path.isfile(os.path.join(out_dir, "done")):
        shutil.rmtree(tmp, ignore_errors=True)
        return
    shutil.rmtree(out_dir, ignore_errors=True)
    try:
        os.rename(tmp, out_dir)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
