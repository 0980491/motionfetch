"""ANSI escape helpers shared by the converter, player and exporter."""

import os
import re

RESET = "\x1b[0m"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"
CLEAR_SCREEN = "\x1b[H\x1b[2J\x1b[3J"
CLEAR_EOL = "\x1b[K"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def fg(r: int, g: int, b: int) -> str:
    return f"\x1b[38;2;{r};{g};{b}m"


def bg(r: int, g: int, b: int) -> str:
    return f"\x1b[48;2;{r};{g};{b}m"


def strip(text: str) -> str:
    """Remove SGR escape sequences."""
    return _ANSI_RE.sub("", text)


def visible_len(text: str) -> int:
    return len(strip(text))


def cursor_up(n: int) -> str:
    return f"\x1b[{n}A" if n > 0 else ""


def parse_hex(color: str):
    """'#rrggbb' or 'rrggbb' -> (r, g, b), or None if malformed."""
    color = color.strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{6}", color):
        return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))
    return None


def accent_color():
    """Best-effort accent for tinting mono animations.

    Order: $MOTIONFETCH_TINT, the quickshell/matugen palette if present,
    then a neutral default.
    """
    env = os.environ.get("MOTIONFETCH_TINT", "")
    rgb = parse_hex(env) if env else None
    if rgb:
        return rgb
    try:
        import json

        path = os.path.expanduser("~/.config/quickshell/colors.json")
        with open(path) as f:
            rgb = parse_hex(json.load(f).get("primary", ""))
        if rgb:
            return rgb
    except Exception:
        pass
    return (137, 180, 250)
