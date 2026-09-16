"""Terminal playback: loop an animation in place, or compose it beside the
output of fastfetch (or any command) the way the classic donut banner does —
draw the block, walk the cursor back up, draw the next frame.

Any keypress or Ctrl+C stops the loop and restores the terminal.
"""

import os
import select
import shutil
import subprocess
import sys
import termios
import time
import tty

from . import ansi
from .ansi import CLEAR_EOL, RESET, cursor_up


def tint_prefix(meta, tint=None):
    """SGR prefix for mono animations ('' for color ones)."""
    if meta.get("mode") != "mono":
        return ""
    rgb = ansi.parse_hex(tint) if tint else None
    if rgb is None:
        rgb = ansi.accent_color()
    return "\x1b[1m" + ansi.fg(*rgb)


def compose_lines(meta, frames, info_lines, gap=3, tint=None):
    """Pair each frame with the info box -> (list of list-of-lines, height)."""
    color = tint_prefix(meta, tint)
    w = meta["width"]
    height = max(meta["height"], len(info_lines))
    off = (height - len(info_lines)) // 2
    blank = " " * w
    composed = []
    for frame in frames:
        out = []
        for i in range(height):
            art = frame[i] if i < len(frame) else blank
            j = i - off
            info = info_lines[j] if 0 <= j < len(info_lines) else ""
            out.append(f"{color}{art}{RESET}{' ' * gap}{info}")
        composed.append(out)
    return composed, height


def compose_fetch(meta, frames, info_lines, gap=3, tint=None):
    """-> list of full blocks (str) pairing each frame with the info box."""
    composed, height = compose_lines(meta, frames, info_lines, gap, tint)
    blocks = [
        "\n".join(line + CLEAR_EOL for line in frame) + "\n"
        for frame in composed
    ]
    return blocks, height


def frames_to_blocks(meta, frames, tint=None):
    color = tint_prefix(meta, tint)
    blocks = [
        "\n".join(f"{color}{line}{RESET}{CLEAR_EOL}" for line in frame) + "\n"
        for frame in frames
    ]
    return blocks, meta["height"]


class _RawStdin:
    """cbreak mode while the animation runs, restored on exit."""

    def __enter__(self):
        self.fd = None
        if sys.stdin.isatty():
            self.fd = sys.stdin.fileno()
            self.old = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)

    def key_pressed(self, timeout):
        if self.fd is None:
            time.sleep(timeout)
            return False
        r, _, _ = select.select([sys.stdin], [], [], timeout)
        if r:
            sys.stdin.read(1)
            return True
        return False


def run_loop(blocks, height, fps, secs=None, clear=True):
    """Draw blocks in a loop until a key, Ctrl+C, or `secs` elapses."""
    out = sys.stdout
    if not out.isatty():
        out.write(blocks[0])
        return
    delay = 1 / max(fps, 1)
    limit = int(secs * fps) if secs else 0
    if clear:
        out.write(ansi.CLEAR_SCREEN)
    out.write(ansi.HIDE_CURSOR)
    try:
        with _RawStdin() as stdin:
            n = 0
            while True:
                out.write(blocks[n % len(blocks)])
                out.flush()
                if limit and n >= limit - 1:
                    break
                if stdin.key_pressed(delay):
                    break
                out.write(cursor_up(height))
                n += 1
    except KeyboardInterrupt:
        pass
    finally:
        out.write(RESET + ansi.SHOW_CURSOR)
        out.flush()


def check_fit(width, height):
    """Return an error string if the animation cannot fit this terminal."""
    cols, rows = shutil.get_terminal_size()
    if width > cols or height + 1 > rows:
        return (
            f"needs {width}x{height + 1}, terminal is {cols}x{rows} — "
            "widen the window or re-convert with a smaller --width"
        )
    return None


def fetch_info(cmd=None):
    """Capture the info box: fastfetch without its logo, colours kept."""
    if cmd:
        argv = ["sh", "-c", cmd]
    else:
        if not shutil.which("fastfetch"):
            raise RuntimeError(
                "fastfetch not found — install it, or use --cmd 'your-fetch'"
            )
        argv = ["fastfetch", "--logo", "none", "--pipe", "false"]
    env = dict(os.environ, COLUMNS=str(shutil.get_terminal_size().columns))
    out = subprocess.run(argv, capture_output=True, text=True, env=env).stdout
    lines = out.rstrip("\n").split("\n")
    width = max((ansi.visible_len(l) for l in lines), default=0)
    # pad so every info line clears cleanly over the previous frame
    return [l + " " * (width - ansi.visible_len(l)) for l in lines], width
