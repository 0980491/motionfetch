"""Render animations to GIF or PNG previews (for READMEs, sharing, etc.).

Draws each frame with a monospace TTF onto a little terminal-window mockup.
Only the SGR codes motionfetch itself emits are interpreted (reset, bold,
truecolor fg/bg).
"""

import glob
import os
import re

from PIL import Image, ImageDraw, ImageFont

from . import ansi

_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")

BG = (17, 17, 27)
FG = (205, 214, 244)
CHROME = (30, 30, 46)
DOTS = ((243, 139, 168), (249, 226, 175), (166, 227, 161))

FONT_CANDIDATES = [
    "JetBrainsMono-Regular.ttf",
    "JetBrainsMono-Medium.ttf",
    "DejaVuSansMono.ttf",
    "LiberationMono-Regular.ttf",
]
FONT_DIRS = [
    "/usr/share/fonts/TTF",
    "/usr/share/fonts/truetype/*",
    "/usr/share/fonts/*",
    os.path.expanduser("~/.local/share/fonts"),
]


def find_font(size):
    for name in FONT_CANDIDATES:
        for pattern in FONT_DIRS:
            for hit in glob.glob(os.path.join(pattern, name)):
                return ImageFont.truetype(hit, size)
    return ImageFont.load_default()


def parse_line(line, base_fg):
    """ANSI line -> list of (char, fg, bg_or_None) cells."""
    cells = []
    cur_fg, cur_bg, bold = base_fg, None, False
    pos = 0
    for m in _SGR_RE.finditer(line):
        for ch in line[pos : m.start()]:
            cells.append((ch, cur_fg, cur_bg))
        pos = m.end()
        params = [int(p) for p in m.group(1).split(";") if p] or [0]
        i = 0
        while i < len(params):
            p = params[i]
            if p == 0:
                cur_fg, cur_bg, bold = base_fg, None, False
            elif p == 1:
                bold = True
            elif p in (38, 48) and params[i : i + 2][1:] == [2]:
                rgb = tuple(params[i + 2 : i + 5])
                if len(rgb) == 3:
                    if p == 38:
                        cur_fg = rgb
                    else:
                        cur_bg = rgb
                i += 4
            i += 1
    for ch in line[pos:]:
        cells.append((ch, cur_fg, cur_bg))
    return cells


def render_frame_image(frame, meta, font, cell_w, cell_h, tint=None):
    pad, bar = 14, 28
    w = meta["width"] * cell_w + pad * 2
    h = meta["height"] * cell_h + pad * 2 + bar
    img = Image.new("RGB", (w, h), BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, w, bar), fill=CHROME)
    for i, color in enumerate(DOTS):
        cx = 16 + i * 20
        draw.ellipse((cx - 6, bar // 2 - 6, cx + 6, bar // 2 + 6), fill=color)

    base_fg = FG
    if meta.get("mode") == "mono":
        rgb = ansi.parse_hex(tint) if tint else None
        base_fg = rgb or ansi.accent_color()

    for row, line in enumerate(frame):
        y = bar + pad + row * cell_h
        for col, (ch, fg, bg) in enumerate(parse_line(line, base_fg)):
            x = pad + col * cell_w
            if bg:
                draw.rectangle((x, y, x + cell_w, y + cell_h), fill=bg)
            if ch not in (" ", ""):
                draw.text((x, y), ch, font=font, fill=fg)
    return img


def export(meta, frames, out_path, fps=None, font_size=15, tint=None,
           max_frames=None):
    font = find_font(font_size)
    box = font.getbbox("█")
    cell_w = max(box[2] - box[0], 1)
    cell_h = max(box[3], 1)
    if max_frames:
        frames = frames[:max_frames]
    if os.path.splitext(out_path)[1].lower() == ".png":
        frames = frames[:1]

    images = [
        render_frame_image(f, meta, font, cell_w, cell_h, tint) for f in frames
    ]
    ext = os.path.splitext(out_path)[1].lower()
    if ext == ".png" or len(images) == 1:
        images[0].save(out_path)
    elif ext == ".gif":
        fps = fps or meta.get("fps", 15)
        quantized = [im.quantize(colors=128, dither=Image.NONE) for im in images]
        quantized[0].save(
            out_path,
            save_all=True,
            append_images=quantized[1:],
            duration=int(1000 / fps),
            loop=0,
            optimize=True,
        )
    else:
        raise ValueError(f"unsupported export format {ext!r} (use .gif or .png)")
    return out_path, len(images)
