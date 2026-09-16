"""Convert images and videos into terminal frames.

Videos are decoded with ffmpeg (cropped and scaled in one pass, streamed as
raw RGB); images go through Pillow. Three rendering styles:

    blocks   truecolor half-blocks (2 pixels per cell) — best for video
    ascii    luminance ramp, plain text — tintable like the fastfetch donut
    braille  2x4 dot cells, plain text — highest detail, tintable

Every emitted line is padded to exactly `width` visible columns so the
player can compose an info box next to the animation without measuring.
"""

import json
import shutil
import subprocess

from PIL import Image, ImageOps

from .ansi import RESET, bg, fg

RAMP = " .:-=+*#%@"

# Half-width glyph budget per terminal cell: a cell is roughly twice as tall
# as it is wide, so one ascii char covers a 1x2 pixel area, a half-block
# covers 1x1 per half, and a braille cell covers 2x4.
STYLES = ("blocks", "ascii", "ascii-color", "braille", "image")

BRAILLE_BITS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))

# Bayer 4x4 matrix for ordered dithering: braille is 1-bit, and a fixed
# threshold turns most footage into an all-on or all-off screen.
BAYER4 = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))


class ConvertError(Exception):
    pass


def parse_crop(value, total):
    """'15%' or '120' -> pixels, clamped to [0, total)."""
    if not value:
        return 0
    value = str(value).strip()
    try:
        if value.endswith("%"):
            px = int(total * float(value[:-1]) / 100)
        else:
            px = int(value)
    except ValueError:
        raise ConvertError(f"bad crop value {value!r} (use pixels or e.g. 15%)")
    return max(0, min(px, total - 1))


def crop_box(w, h, crop):
    """crop: dict with top/bottom/left/right strings -> (x, y, cw, ch)."""
    top = parse_crop(crop.get("top"), h)
    bottom = parse_crop(crop.get("bottom"), h)
    left = parse_crop(crop.get("left"), w)
    right = parse_crop(crop.get("right"), w)
    cw, ch = w - left - right, h - top - bottom
    if cw < 8 or ch < 8:
        raise ConvertError("crop leaves almost nothing of the picture")
    return left, top, cw, ch


def grid_size(src_w, src_h, width, style):
    """-> (cols, rows, px_w, px_h) for the given char width."""
    aspect = src_h / src_w
    cols = width
    if style == "braille":
        px_w = cols * 2
        px_h = max(4, round(px_w * aspect))
        rows = -(-px_h // 4)
        px_h = rows * 4
    elif style == "blocks":
        px_w = cols
        px_h = max(2, round(cols * aspect * 0.5) * 2)
        rows = px_h // 2
    elif style == "image":
        # real pixels via the kitty graphics protocol: keep a comfortable
        # resolution (~10x20 px per cell), the terminal scales to fit
        rows = max(1, round(cols * aspect * 0.5))
        px_w = min(cols * 10, 800)
        px_h = max(1, round(px_w * aspect))
    else:  # ascii
        px_w = cols
        rows = max(1, round(cols * aspect * 0.5))
        px_h = rows
    return cols, rows, px_w, px_h


# ── style renderers: PIL RGB image (already px_w x px_h) -> list of lines ──


def _render_blocks(img, cols, rows):
    px = img.load()
    lines = []
    for row in range(rows):
        parts, last = [], None
        for x in range(cols):
            top = px[x, row * 2]
            bot = px[x, row * 2 + 1]
            if (top, bot) != last:
                parts.append(fg(*top) + bg(*bot))
                last = (top, bot)
            parts.append("▀")  # upper half block
        lines.append("".join(parts) + RESET)
    return lines


def _luma(img):
    # stretch the contrast so mostly-bright or mostly-dark footage still
    # spreads over the whole ramp
    return ImageOps.autocontrast(img.convert("L"), cutoff=2)


def _render_ascii(img, cols, rows, gamma, invert, color_img=None):
    gray = _luma(img).load()
    cpx = color_img.load() if color_img else None
    lines = []
    for y in range(rows):
        parts, last = [], None
        for x in range(cols):
            v = gray[x, y] / 255
            if invert:
                v = 1 - v
            v = v**gamma
            ch = RAMP[min(int(v * len(RAMP)), len(RAMP) - 1)]
            if cpx:
                c = cpx[x, y]
                if c != last:
                    parts.append(fg(*c))
                    last = c
            parts.append(ch)
        lines.append("".join(parts) + (RESET if cpx else ""))
    return lines


def _render_braille(img, cols, rows, gamma, invert):
    gray = _luma(img).load()
    lines = []
    for row in range(rows):
        chars = []
        for col in range(cols):
            code = 0x2800
            for dy in range(4):
                for dx in range(2):
                    x, y = col * 2 + dx, row * 4 + dy
                    v = (gray[x, y] / 255) ** gamma
                    if invert:
                        v = 1 - v
                    if v > (BAYER4[y % 4][x % 4] + 0.5) / 16:
                        code |= BRAILLE_BITS[dy][dx]
            chars.append(chr(code))
        lines.append("".join(chars))
    return lines


def render_frame(img, style, cols, rows, gamma=1.0, invert=False):
    if style == "image":
        return img  # kept as real pixels; stored as PNG, drawn by kitty
    if style == "blocks":
        return _render_blocks(img, cols, rows)
    if style == "ascii":
        return _render_ascii(img, cols, rows, gamma, invert)
    if style == "ascii-color":
        return _render_ascii(img, cols, rows, gamma, invert, color_img=img)
    if style == "braille":
        return _render_braille(img, cols, rows, gamma, invert)
    raise ConvertError(f"unknown style {style!r}")


# ── sources ──


def probe_video(path):
    if not shutil.which("ffprobe"):
        raise ConvertError("ffmpeg/ffprobe not found — install ffmpeg")
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "json", path,
        ],
        capture_output=True, text=True,
    )
    try:
        stream = json.loads(out.stdout)["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except (ValueError, LookupError):
        raise ConvertError(f"could not read video {path!r}: {out.stderr.strip()}")


def video_frames(path, width, style, fps, crop, start=None, duration=None,
                 max_frames=400, gamma=1.0, invert=False):
    """Yield rendered frames (list of lines) from a video file."""
    src_w, src_h = probe_video(path)
    x, y, cw, ch = crop_box(src_w, src_h, crop)
    cols, rows, px_w, px_h = grid_size(cw, ch, width, style)

    filters = f"crop={cw}:{ch}:{x}:{y},fps={fps},scale={px_w}:{px_h}:flags=area"
    cmd = ["ffmpeg", "-v", "error"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", path]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-vf", filters, "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]

    frame_bytes = px_w * px_h * 3
    # DEVNULL: stopping early (max_frames) breaks ffmpeg's pipe, which is
    # expected and would otherwise spray harmless errors over the UI
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    try:
        count = 0
        while count < max_frames:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            img = Image.frombytes("RGB", (px_w, px_h), buf)
            yield render_frame(img, style, cols, rows, gamma, invert)
            count += 1
    finally:
        proc.stdout.close()
        proc.terminate()
        proc.wait()
    if count == 0:
        raise ConvertError("ffmpeg produced no frames — is this a video file?")


def image_frames(path, width, style, crop, gamma=1.0, invert=False):
    """-> single rendered frame from an image (animated GIFs: all frames)."""
    img = Image.open(path)
    frames = []
    try:
        n = getattr(img, "n_frames", 1)
    except Exception:
        n = 1
    for i in range(min(n, 400)):
        if n > 1:
            img.seek(i)
        rgb = img.convert("RGB")
        x, y, cw, ch = crop_box(rgb.width, rgb.height, crop)
        rgb = rgb.crop((x, y, x + cw, y + ch))
        cols, rows, px_w, px_h = grid_size(cw, ch, width, style)
        rgb = rgb.resize((px_w, px_h), Image.LANCZOS)
        frames.append(render_frame(rgb, style, cols, rows, gamma, invert))
    return frames


def pad_frames(frames, cols):
    """Make every line exactly `cols` visible columns (mono styles only add
    trailing spaces; color styles already emit full-width lines)."""
    from .ansi import visible_len

    if frames and not isinstance(frames[0], list):
        return frames  # image style: PIL frames, nothing to pad

    for frame in frames:
        for i, line in enumerate(frame):
            missing = cols - visible_len(line)
            if missing > 0:
                frame[i] = line + " " * missing
    return frames
