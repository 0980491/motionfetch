"""Convert images and videos into terminal frames.

Videos are decoded with ffmpeg (cropped, retimed and scaled in one pass,
streamed as raw RGB); images go through Pillow. The per-pixel math runs on
numpy. Styles:

    blocks   truecolor half-blocks (2 pixels per cell) — best for video
    dots     a dot-matrix display: braille dots (2x4 per cell), colored
    ascii    luminance ramp, plain text — tintable like the fastfetch donut
    braille  2x4 dot cells, plain text — highest detail, tintable
    image    the real pixels, drawn by the kitty graphics protocol

A background color can be keyed out (`key=(rgb, tolerance%)`): matching
pixels become empty cells — or true transparency in the image style.

Every emitted text line is padded to exactly `width` visible columns so the
player can compose an info box next to the animation without measuring.
"""

import json
import shutil
import subprocess

import numpy as np
from PIL import Image

from .ansi import RESET, bg, fg

RAMP = " .:-=+*#%@"

STYLES = ("blocks", "dots", "ascii", "ascii-color", "braille", "image")

# Bayer 4x4 matrix for ordered dithering: braille/dots are 1-bit, and a
# fixed threshold turns most footage into an all-on or all-off screen.
BAYER4 = np.array(
    ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5)),
    dtype=np.float32,
)

# braille dot weights by (dy, dx), added to U+2800
BRAILLE_W = np.array(((1, 8), (2, 16), (4, 32), (64, 128)), dtype=np.int32)

MAX_COLOR_DIST = 441.7  # sqrt(3 * 255^2)


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
    if style in ("braille", "dots"):
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


# ── numpy helpers ──


def _keep_mask(arr, key):
    """arr (h,w,3) uint8 -> bool array of pixels to KEEP, or None."""
    if not key or key[0] is None:
        return None
    rgb, tol = key
    diff = arr.astype(np.int16) - np.asarray(rgb, dtype=np.int16)
    dist = np.sqrt((diff.astype(np.float32) ** 2).sum(axis=-1))
    return dist > MAX_COLOR_DIST * (float(tol) / 100.0)


def _luma(arr, keep, gamma, invert):
    """-> float32 luminance in 0..1, contrast-stretched over kept pixels."""
    lum = (arr @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)) / 255.0
    sample = lum if keep is None else lum[keep]
    if sample.size > 16:
        lo, hi = np.percentile(sample, (2.0, 98.0))
        if hi - lo > 1e-3:
            lum = np.clip((lum - lo) / (hi - lo), 0.0, 1.0)
    if gamma != 1.0:
        lum = lum**gamma
    if invert:
        lum = 1.0 - lum
    return lum


def _dither(lum):
    h, w = lum.shape
    thr = (
        np.tile(BAYER4, (-(-h // 4), -(-w // 4)))[:h, :w] + 0.5
    ) / 16.0
    return lum > thr


def _braille_codes(lit, rows, cols):
    bits = lit.reshape(rows, 4, cols, 2).astype(np.int32)
    return 0x2800 + (bits * BRAILLE_W[None, :, None, :]).sum(axis=(1, 3))


# ── style renderers: arr (px_h, px_w, 3) -> list of ANSI/text lines ──


def _render_blocks(arr, cols, rows, keep):
    top, bot = arr[0::2], arr[1::2]
    if keep is None:
        ktop = kbot = np.ones((rows, cols), dtype=bool)
    else:
        ktop, kbot = keep[0::2], keep[1::2]
    lines = []
    for y in range(rows):
        parts, last = [], None
        t_row, b_row = top[y], bot[y]
        kt_row, kb_row = ktop[y], kbot[y]
        for x in range(cols):
            kt, kb = kt_row[x], kb_row[x]
            if not kt and not kb:
                state, ch = (None, None), " "
            elif kt and kb:
                state = (tuple(t_row[x]), tuple(b_row[x]))
                ch = "▀"
            elif kt:
                state, ch = (tuple(t_row[x]), None), "▀"
            else:
                state, ch = (tuple(b_row[x]), None), "▄"
            if state != last:
                f, b = state
                parts.append(
                    RESET + (fg(*f) if f else "") + (bg(*b) if b else "")
                )
                last = state
            parts.append(ch)
        lines.append("".join(parts) + RESET)
    return lines


def _render_ascii(arr, cols, rows, keep, gamma, invert, color):
    lum = _luma(arr, keep, gamma, invert)
    idx = np.minimum((lum * len(RAMP)).astype(np.int32), len(RAMP) - 1)
    if keep is not None:
        idx[~keep] = 0
    lines = []
    for y in range(rows):
        row = idx[y]
        if not color:
            lines.append("".join(RAMP[i] for i in row))
            continue
        parts, last = [], None
        crow = arr[y]
        for x in range(cols):
            ch = RAMP[row[x]]
            if ch != " ":
                c = tuple(crow[x])
                if c != last:
                    parts.append(fg(*c))
                    last = c
            parts.append(ch)
        lines.append("".join(parts) + RESET)
    return lines


def _render_braille(arr, cols, rows, keep, gamma, invert):
    lit = _dither(_luma(arr, keep, gamma, invert))
    if keep is not None:
        lit &= keep
    codes = _braille_codes(lit, rows, cols)
    return ["".join(map(chr, code_row)) for code_row in codes]


def _render_dots(arr, cols, rows, keep, gamma, invert):
    """The little-OLED look: a dense dot matrix, each cell colored like
    the pixels that light it."""
    lit = _dither(_luma(arr, keep, gamma, invert))
    if keep is not None:
        lit &= keep
    codes = _braille_codes(lit, rows, cols)

    # average color of the lit pixels in each 2x4 cell
    litf = lit.reshape(rows, 4, cols, 2).astype(np.float32)
    counts = litf.sum(axis=(1, 3))
    cells = arr.reshape(rows, 4, cols, 2, 3).astype(np.float32)
    sums = (cells * litf[..., None]).sum(axis=(1, 3))
    colors = (
        sums / np.maximum(counts, 1)[..., None]
    ).astype(np.uint8)

    lines = []
    for y in range(rows):
        parts, last = [], None
        for x in range(cols):
            code = codes[y, x]
            if code == 0x2800:
                parts.append(" ")
                continue
            c = tuple(colors[y, x])
            if c != last:
                parts.append(fg(*c))
                last = c
            parts.append(chr(code))
        lines.append("".join(parts) + RESET)
    return lines


def _render_image(img, arr, keep):
    if keep is None:
        return img.convert("RGB")
    alpha = np.where(keep, 255, 0).astype(np.uint8)
    return Image.fromarray(np.dstack([arr, alpha]), "RGBA")


def render_frame(img, style, cols, rows, gamma=1.0, invert=False, key=None):
    arr = np.asarray(img.convert("RGB"), dtype=np.uint8)
    keep = _keep_mask(arr, key)
    if style == "image":
        return _render_image(img, arr, keep)
    if style == "blocks":
        return _render_blocks(arr, cols, rows, keep)
    if style == "dots":
        return _render_dots(arr, cols, rows, keep, gamma, invert)
    if style == "ascii":
        return _render_ascii(arr, cols, rows, keep, gamma, invert, False)
    if style == "ascii-color":
        return _render_ascii(arr, cols, rows, keep, gamma, invert, True)
    if style == "braille":
        return _render_braille(arr, cols, rows, keep, gamma, invert)
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
                 max_frames=400, gamma=1.0, invert=False, key=None,
                 speed=1.0):
    """Yield rendered frames from a video file. `speed` retimes the video:
    2.0 plays twice as fast, 0.5 at half speed (frames are dropped or
    duplicated by ffmpeg; playback fps stays the same)."""
    src_w, src_h = probe_video(path)
    x, y, cw, ch = crop_box(src_w, src_h, crop)
    cols, rows, px_w, px_h = grid_size(cw, ch, width, style)

    speed = max(0.05, float(speed or 1.0))
    sample_fps = fps / speed
    filters = (
        f"crop={cw}:{ch}:{x}:{y},fps={sample_fps:.4f},"
        f"scale={px_w}:{px_h}:flags=area"
    )
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
            yield render_frame(img, style, cols, rows, gamma, invert, key)
            count += 1
    finally:
        proc.stdout.close()
        proc.terminate()
        proc.wait()
    if count == 0:
        raise ConvertError("ffmpeg produced no frames — is this a video file?")


def image_frames(path, width, style, crop, gamma=1.0, invert=False, key=None):
    """-> rendered frames from an image (animated GIFs: all frames)."""
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
        frames.append(render_frame(rgb, style, cols, rows, gamma, invert, key))
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
