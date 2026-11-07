"""Terminal greeter: one animation from your rotation every time a terminal
opens, playing beside fastfetch while the prompt stays usable.

`fetch` animates by redrawing the block in a loop, which owns the terminal
until you press a key. This can't: the shell needs the terminal back right
away. The trick is that kitty can animate an image *by itself*. Every frame
goes over the graphics protocol once (a=t, then a=f per frame), kitty is told
to loop it (a=a,s=3,v=1), and the command exits. The animation keeps playing
in the scrollback while you type, scrolls away with the text, and is gone
after `clear`.

Text styles have no pixels, so they are rasterised once per animation, cell
size and tint into ~/.cache/motionfetch/greet (raster.py). Every later
terminal hands kitty the cached PNGs by path (t=f), so the hot path never
imports numpy or PIL — that is the reason this module stands apart from
cli.py, which takes ~100 ms longer to import.

The rotation lives in ~/.config/motionfetch/greet.json:

    enabled  play anything at all
    pool     animations to pick from; empty = the whole library
    fetch    fastfetch beside the animation

Terminals without the kitty graphics protocol get nothing: a greeter that
prints a still frame into every VS Code or IDE terminal would be noise.
"""

import argparse
import base64
import fcntl
import hashlib
import json
import os
import random
import shutil
import struct
import subprocess
import sys
import termios

from . import library

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "motionfetch",
)
CONFIG = os.path.join(CONFIG_DIR, "greet.json")
CACHE = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "motionfetch", "greet",
)
LAST = os.path.join(CACHE, "last")
CELLS = os.path.join(CACHE, "cells.json")   # cell sizes seen, for --warm

DEFAULTS = {"enabled": True, "pool": [], "fetch": True}

GAP = 3            # columns between the animation and the info box
MAX_FRAMES = 120   # kitty keeps every frame; longer clips get thinned out
KEEP_RENDERS = 3   # cached renders per animation (cell sizes x tints)

# Bumping this throws every cached render away — for raster.py changes.
RENDER_VERSION = 1


# ── config ──


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG) as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update({k: data[k] for k in DEFAULTS if k in data})
    except (OSError, ValueError):
        pass
    if not isinstance(cfg["pool"], list):
        cfg["pool"] = []
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG + ".new"
    with open(tmp, "w") as f:
        json.dump({k: cfg[k] for k in DEFAULTS}, f, indent=2)
        f.write("\n")
    os.rename(tmp, CONFIG)


def pick(cfg, exclude_last=True):
    """A random animation from the pool, never the same one twice in a row
    when there is anything else to choose from."""
    names = [m["name"] for m in library.list_all()]
    pool = [n for n in cfg["pool"] if n in names] if cfg["pool"] else names
    if not pool:
        return None
    last = None
    if exclude_last and len(pool) > 1:
        try:
            with open(LAST) as f:
                last = f.read().strip()
        except OSError:
            pass
    choice = random.choice([n for n in pool if n != last] or pool)
    try:
        os.makedirs(CACHE, exist_ok=True)
        with open(LAST, "w") as f:
            f.write(choice)
    except OSError:
        pass
    return choice


# ── terminal ──


def kitty_ok():
    """The graphics protocol with animation: kitty itself. Ghostty speaks
    the protocol but does not animate, so it would show a still."""
    env = os.environ
    return env.get("TERM") == "xterm-kitty" and "KITTY_WINDOW_ID" in env \
        and "TMUX" not in env


def window_size():
    """-> (cols, rows, cell_w, cell_h). kitty fills in the pixel size, which
    is what lets the rasterised frames land on exact cell boundaries; the
    cell size is 0x0 when the terminal does not report it."""
    try:
        rows, cols, xp, yp = struct.unpack(
            "HHHH", fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ,
                                b"\0" * 8)
        )
    except OSError:
        size = shutil.get_terminal_size()
        cols, rows, xp, yp = size.columns, size.lines, 0, 0
    if not (xp and yp and cols and rows):
        return cols, rows, 0, 0   # unknown: the caller picks a fallback
    return cols, rows, max(xp // cols, 4), max(yp // rows, 8)


def _apc(out, ctrl, payload=b""):
    out.write(b"\x1b_G" + ctrl.encode() + b";" + payload + b"\x1b\\")


def _send(out, ctrl, path, local):
    """One frame, by path when kitty can read our disk, inline otherwise."""
    if local:
        _apc(out, ctrl + ",t=f", base64.standard_b64encode(path.encode()))
        return
    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read())
    first = True
    while True:
        chunk, data = data[:4096], data[4096:]
        more = 1 if data else 0
        _apc(out, (ctrl + "," if first else "") + f"m={more}", chunk)
        first = False
        if not more:
            break


# ── frames ──


def frame_paths(name, meta, cw, ch):
    """-> (list of PNG paths, gap in ms). Renders into the cache on a miss."""
    stride = max(1, -(-meta["frames"] // MAX_FRAMES))  # ceil
    gap = max(10, round(1000 * stride / max(meta.get("fps", 15), 1)))
    src = library.anim_dir(name)

    if meta.get("mode") == "image":
        fdir = os.path.join(src, "frames")
        files = sorted(f for f in os.listdir(fdir) if f.endswith(".png"))
        return [os.path.join(fdir, f) for f in files[::stride]], gap

    tint = ""
    if meta.get("mode") == "mono":
        from .ansi import accent_color
        tint = "%02x%02x%02x" % accent_color()
    stamp = os.stat(os.path.join(src, "meta.json")).st_mtime_ns
    key = hashlib.sha1(
        f"{RENDER_VERSION}|{stamp}|{cw}x{ch}|{tint}|{stride}".encode()
    ).hexdigest()[:12]
    home = os.path.join(CACHE, name)
    out = os.path.join(home, key)
    if not os.path.isfile(os.path.join(out, "done")):
        from . import raster  # numpy + PIL: only on a cache miss
        _, frames = library.load(name)
        raster.render(meta, frames[::stride], cw, ch, tint, out)
        _prune(home, keep=out)
    else:
        os.utime(out)  # most recently used, for _prune
    files = sorted(f for f in os.listdir(out) if f.endswith(".png"))
    return [os.path.join(out, f) for f in files], gap


def _prune(home, keep):
    dirs = [os.path.join(home, d) for d in os.listdir(home)]
    # ".new<pid>" is another terminal's render still in progress
    dirs = sorted((d for d in dirs if os.path.isdir(d) and d != keep
                   and ".new" not in os.path.basename(d)),
                  key=os.path.getmtime, reverse=True)
    for d in dirs[KEEP_RENDERS - 1:]:
        shutil.rmtree(d, ignore_errors=True)


def remember_cells(cw, ch):
    try:
        with open(CELLS) as f:
            seen = [tuple(c) for c in json.load(f)]
    except (OSError, ValueError, TypeError):
        seen = []
    if seen[:1] == [(cw, ch)]:
        return
    seen = [(cw, ch)] + [c for c in seen if c != (cw, ch)]
    try:
        os.makedirs(CACHE, exist_ok=True)
        with open(CELLS, "w") as f:
            json.dump(seen[:KEEP_RENDERS], f)
    except OSError:
        pass


def warm(cfg):
    """Render the whole rotation ahead of time for the cell sizes terminals
    have used. Mono animations are tinted with the wallpaper's accent, so a
    wallpaper change invalidates them; run this from the theming hook and
    the next terminal opens without the one-second render."""
    try:
        with open(CELLS) as f:
            cells = [tuple(c) for c in json.load(f)]
    except (OSError, ValueError, TypeError):
        return 0
    names = [m["name"] for m in library.list_all()]
    pool = [n for n in cfg["pool"] if n in names] if cfg["pool"] else names
    for name in pool:
        try:
            with open(os.path.join(library.anim_dir(name), "meta.json")) as f:
                meta = json.load(f)
            for cw, ch in cells:
                frame_paths(name, meta, cw, ch)
        except Exception:
            continue
    return 0


# ── the greeting ──


def start_fetch(enabled, cols):
    if not enabled or not shutil.which("fastfetch"):
        return None
    env = dict(os.environ, COLUMNS=str(cols))
    try:
        return subprocess.Popen(
            ["fastfetch", "--logo", "none", "--pipe", "false"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
            text=True,
        )
    except OSError:
        return None


def read_fetch(proc):
    if proc is None:
        return [], 0
    from .ansi import visible_len
    try:
        out, _ = proc.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        return [], 0
    lines = out.rstrip("\n").split("\n") if out.strip() else []
    return lines, max((visible_len(l) for l in lines), default=0)


def fit(meta, cols, rows, info_w, info_h):
    """Cells the animation gets -> (c, r, show_info). kitty scales the
    image to whatever box it is placed in, so a narrow terminal shrinks the
    animation instead of dropping it; the info box goes first when even a
    small animation would not fit beside it."""
    w, h = meta["width"], meta["height"]
    show_info = info_h > 0
    room = cols - (GAP + info_w if show_info else 0)
    if show_info and room < min(w, 16):
        show_info, room = False, cols
    c = min(w, room)
    r = max(1, round(h * c / w))
    limit = max(4, rows - 2)
    if r > limit:
        r = limit
        c = max(1, round(w * r / h))
    return c, r, show_info


def greet(name=None, cfg=None):
    cfg = cfg or load_config()
    if not (sys.stdout.isatty() and kitty_ok()):
        return 0
    name = name or pick(cfg)
    if not name:
        return 0
    try:
        with open(os.path.join(library.anim_dir(name), "meta.json")) as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return 0

    cols, rows, cw, ch = window_size()
    if cw:
        remember_cells(cw, ch)
    else:
        cw, ch = 10, 20
    proc = start_fetch(cfg["fetch"], cols)
    paths, gap = frame_paths(name, meta, cw, ch)   # overlaps with fastfetch
    info, info_w = read_fetch(proc)
    if not paths:
        sys.stdout.write("\n".join(info) + ("\n" if info else ""))
        return 0
    c, r, show_info = fit(meta, cols, rows, info_w, len(info))
    if not show_info:
        info = []

    height = max(r, len(info))
    info_off = (height - len(info)) // 2
    anim_off = (height - r) // 2
    local = not (os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))
    iid = random.randrange(1, 1 << 24)

    out = sys.stdout.buffer
    # Lay the block out first so the space exists (this is what scrolls the
    # screen when the prompt is at the bottom), then go back up and drop the
    # image into its top-left corner. C=1 keeps the image from moving the
    # cursor, so walking back down lands under the block.
    for i in range(height):
        j = i - info_off
        line = info[j] if 0 <= j < len(info) else ""
        if line:
            out.write(f"\x1b[{c + GAP}C".encode() + line.encode())
        out.write(b"\x1b[0m\x1b[K\n")
    out.write(f"\x1b[{height}A\r".encode())
    if anim_off:
        out.write(f"\x1b[{anim_off}B".encode())

    q = "q=2"  # no replies: they would land in the shell's input
    _send(out, f"a=t,f=100,i={iid},{q}", paths[0], local)
    for p in paths[1:]:
        _send(out, f"a=f,f=100,i={iid},z={gap},{q}", p, local)
    if len(paths) > 1:
        _apc(out, f"a=a,i={iid},r=1,z={gap},{q}")   # the first frame's gap
    _apc(out, f"a=p,i={iid},c={c},r={r},C=1,{q}")
    if len(paths) > 1:
        _apc(out, f"a=a,i={iid},s=3,v=1,{q}")       # loop forever

    down = height - anim_off
    out.write(f"\x1b[{down}B\r".encode() if down else b"\r")
    out.flush()
    return 0


# ── command line ──


HOOKS = {
    "bash": (
        '# motionfetch: an animation from your rotation in every new terminal\n'
        'if [[ $- == *i* && ${MOTIONFETCH_GREETED-} != "${KITTY_PID-}:${KITTY_WINDOW_ID-}" ]]; then\n'
        '    export MOTIONFETCH_GREETED="${KITTY_PID-}:${KITTY_WINDOW_ID-}"\n'
        '    command -v motionfetch >/dev/null && motionfetch greet\n'
        'fi\n'
    ),
    "zsh": (
        '# motionfetch: an animation from your rotation in every new terminal\n'
        'if [[ -o interactive && ${MOTIONFETCH_GREETED-} != "${KITTY_PID-}:${KITTY_WINDOW_ID-}" ]]; then\n'
        '    export MOTIONFETCH_GREETED="${KITTY_PID-}:${KITTY_WINDOW_ID-}"\n'
        '    (( $+commands[motionfetch] )) && motionfetch greet\n'
        'fi\n'
    ),
    "fish": (
        '# motionfetch: an animation from your rotation in every new terminal\n'
        'if status is-interactive; and test "$MOTIONFETCH_GREETED" != "$KITTY_PID:$KITTY_WINDOW_ID"\n'
        '    set -gx MOTIONFETCH_GREETED "$KITTY_PID:$KITTY_WINDOW_ID"\n'
        '    type -q motionfetch; and motionfetch greet\n'
        'end\n'
    ),
}


def status(cfg):
    names = [m["name"] for m in library.list_all()]
    pool = cfg["pool"]
    missing = [n for n in pool if n not in names]
    print(f"greeting:  {'on' if cfg['enabled'] else 'off'}")
    print(f"fastfetch: {'beside it' if cfg['fetch'] else 'off'}")
    if not pool:
        print(f"rotation:  the whole library ({len(names)} animations)")
    elif len(pool) == 1:
        print(f"rotation:  always {pool[0]}")
    else:
        print(f"rotation:  random from {', '.join(pool)}")
    if missing:
        print(f"           (not in the library: {', '.join(missing)})")
    print(f"config:    {CONFIG}")


def build_parser():
    p = argparse.ArgumentParser(
        prog="motionfetch greet",
        description="Play one animation (beside fastfetch) when a terminal "
                    "opens, without holding the prompt. Needs kitty. With no "
                    "options it greets; the options edit the rotation.",
    )
    p.add_argument("--name", help="play this one now, ignoring the rotation")
    p.add_argument("--use", nargs="+", metavar="NAME",
                   help="rotation = exactly these (one = always that one)")
    p.add_argument("--add", nargs="+", metavar="NAME",
                   help="add to the rotation")
    p.add_argument("--remove", nargs="+", metavar="NAME",
                   help="take out of the rotation")
    p.add_argument("--all", action="store_true",
                   help="rotation = the whole library")
    p.add_argument("--on", dest="enabled", action="store_true", default=None,
                   help="greet in new terminals")
    p.add_argument("--off", dest="enabled", action="store_false",
                   help="stop greeting (the hook stays, it just exits)")
    p.add_argument("--fetch", dest="fetch", action="store_true", default=None,
                   help="fastfetch beside the animation")
    p.add_argument("--no-fetch", dest="fetch", action="store_false",
                   help="the animation alone")
    p.add_argument("--status", action="store_true",
                   help="show the rotation and settings")
    p.add_argument("--warm", action="store_true",
                   help="pre-render the rotation (after a wallpaper change)")
    p.add_argument("--hook", choices=sorted(HOOKS),
                   help="print the snippet for your shell's rc file")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.hook:
        sys.stdout.write(HOOKS[args.hook])
        return 0

    cfg = load_config()
    edits = args.all or args.use or args.add or args.remove \
        or args.enabled is not None or args.fetch is not None
    if edits:
        names = [m["name"] for m in library.list_all()]
        unknown = [n for n in (args.use or []) + (args.add or [])
                   if n not in names]
        if unknown:
            print(f"error: not in the library: {', '.join(unknown)} "
                  "(see `motionfetch list`)", file=sys.stderr)
            return 1
        if args.all:
            cfg["pool"] = []
        if args.use:
            cfg["pool"] = list(dict.fromkeys(args.use))
        for n in args.add or []:
            if n not in cfg["pool"]:
                cfg["pool"].append(n)
        if args.remove:
            # an empty pool means everything, so "everything but these"
            pool = cfg["pool"] or names
            cfg["pool"] = [n for n in pool if n not in args.remove]
        if args.enabled is not None:
            cfg["enabled"] = args.enabled
        if args.fetch is not None:
            cfg["fetch"] = args.fetch
        save_config(cfg)
        status(cfg)
        return 0
    if args.status:
        status(cfg)
        return 0
    if args.warm:
        return warm(cfg) if cfg["enabled"] else 0

    if args.name:
        if not library.exists(args.name):
            print(f"error: no animation named {args.name!r}", file=sys.stderr)
            return 1
        return greet(args.name, cfg)
    if not cfg["enabled"]:
        return 0
    try:
        return greet(cfg=cfg)
    except Exception:
        # A greeter must never be the reason a terminal opens with a
        # traceback. MOTIONFETCH_DEBUG=1 shows it.
        if os.environ.get("MOTIONFETCH_DEBUG"):
            raise
        return 0
