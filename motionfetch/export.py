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


def find_font(size, braille=False):
    if braille:
        # most coding fonts lack braille glyphs and PIL does no fallback:
        # ask fontconfig for a monospace font that actually covers them
        import subprocess

        try:
            hit = subprocess.run(
                ["fc-match", "-f", "%{file}", "monospace:charset=2847"],
                capture_output=True, text=True,
            ).stdout.strip()
            if hit:
                return ImageFont.truetype(hit, size)
        except OSError:
            pass
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


def _color_runs(cells):
    """Group consecutive cells sharing fg/bg into (text, fg, bg, start)."""
    runs = []
    for i, (ch, fg, bg) in enumerate(cells):
        if runs and runs[-1][1] == fg and runs[-1][2] == bg:
            runs[-1][0].append(ch)
        else:
            runs.append([[ch], fg, bg, i])
    return [("".join(chs), fg, bg, start) for chs, fg, bg, start in runs]


def _blocks_pixels(frame, meta):
    """A blocks frame is literally 2 pixels per cell — rebuild that tiny
    image and let the caller scale it. Orders of magnitude faster than
    drawing every '▀' as a glyph."""
    cols, rows = meta["width"], meta["height"]
    img = Image.new("RGB", (cols, rows * 2), BG)
    px = img.load()
    for y, line in enumerate(frame[:rows]):
        for x, (ch, fg, bg) in enumerate(parse_line(line, FG)[:cols]):
            if ch == "▀":
                px[x, y * 2] = fg
                px[x, y * 2 + 1] = bg or BG
            elif bg:
                px[x, y * 2] = bg
                px[x, y * 2 + 1] = bg
    return img


def render_frame_image(frame, meta, font, cell_w, cell_h, tint=None,
                       chrome=True):
    pad, bar = 14, 28 if chrome else 0
    w = meta["width"] * cell_w + pad * 2
    h = meta["height"] * cell_h + pad * 2 + bar
    img = Image.new("RGB", (w, h), BG)
    draw = ImageDraw.Draw(img)
    if chrome:
        draw.rectangle((0, 0, w, bar), fill=CHROME)
        for i, color in enumerate(DOTS):
            cx = 16 + i * 20
            draw.ellipse(
                (cx - 6, bar // 2 - 6, cx + 6, bar // 2 + 6), fill=color
            )

    if meta.get("style") == "blocks":
        pixels = _blocks_pixels(frame, meta).resize(
            (meta["width"] * cell_w, meta["height"] * cell_h),
            Image.NEAREST,
        )
        img.paste(pixels, (pad, bar + pad))
        return img

    base_fg = FG
    if meta.get("mode") == "mono":
        rgb = ansi.parse_hex(tint) if tint else None
        base_fg = rgb or ansi.accent_color()

    for row, line in enumerate(frame):
        y = bar + pad + row * cell_h
        for text, fg, bg, start in _color_runs(parse_line(line, base_fg)):
            x = pad + start * cell_w
            if bg:
                draw.rectangle(
                    (x, y, x + len(text) * cell_w, y + cell_h), fill=bg
                )
            if text.strip():
                draw.text((x, y), text, font=font, fill=fg)
    return img


def render_pixel_frame(png_bytes, meta, cell_w, cell_h):
    """Image-style frames: paste the real pixels into the window mockup."""
    import io

    pad, bar = 14, 28
    w = meta["width"] * cell_w + pad * 2
    h = meta["height"] * cell_h + pad * 2 + bar
    img = Image.new("RGB", (w, h), BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, w, bar), fill=CHROME)
    for i, color in enumerate(DOTS):
        cx = 16 + i * 20
        draw.ellipse((cx - 6, bar // 2 - 6, cx + 6, bar // 2 + 6), fill=color)
    pic = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    pic = pic.resize((meta["width"] * cell_w, meta["height"] * cell_h))
    img.paste(pic, (pad, bar + pad))
    return img


def export(meta, frames, out_path, fps=None, font_size=15, tint=None,
           max_frames=None):
    font = find_font(font_size, braille=meta.get("style") == "braille")
    # advance width, not ink width: text is drawn a whole run at a time,
    # so the per-cell step must match the font's own character advance
    cell_w = max(round(font.getlength("█")), 1)
    cell_h = max(font.getbbox("█")[3], 1)
    if max_frames:
        frames = frames[:max_frames]
    if os.path.splitext(out_path)[1].lower() == ".png":
        frames = frames[:1]

    if meta.get("mode") == "image":
        images = [render_pixel_frame(f, meta, cell_w, cell_h) for f in frames]
    else:
        images = [
            render_frame_image(f, meta, font, cell_w, cell_h, tint)
            for f in frames
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
