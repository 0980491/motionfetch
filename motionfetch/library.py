"""On-disk animation library.

Each animation lives in $XDG_DATA_HOME/motionfetch/<name>/ as:
    meta.json        name, mode, fps, width, height, frame count, source
    frames/0000.txt  one file per frame; every line is exactly `width`
                     visible columns (padding included at convert time)

Frames in "color" mode carry their own truecolor escapes; "mono" frames are
plain text and get tinted at play time.
"""

import json
import os
import re
import shutil

DATA_DIR = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
    "motionfetch",
)

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class LibraryError(Exception):
    pass


def check_name(name: str) -> str:
    if not _NAME_RE.fullmatch(name):
        raise LibraryError(
            f"invalid name {name!r}: use letters, digits, '-' or '_'"
        )
    return name


def anim_dir(name: str) -> str:
    return os.path.join(DATA_DIR, name)


def exists(name: str) -> bool:
    return os.path.isfile(os.path.join(anim_dir(name), "meta.json"))


def save(name, frames, meta, overwrite=False):
    """frames: list of list-of-lines (text styles) or PIL images ("image"
    style, stored as PNG). meta: dict merged into meta.json."""
    check_name(name)
    if exists(name) and not overwrite:
        raise LibraryError(f"animation {name!r} already exists (use --force)")
    path = anim_dir(name)
    tmp = path + ".new"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.join(tmp, "frames"))
    for i, frame in enumerate(frames):
        if isinstance(frame, list):
            with open(os.path.join(tmp, "frames", f"{i:04d}.txt"), "w") as f:
                f.write("\n".join(frame) + "\n")
        else:
            frame.save(os.path.join(tmp, "frames", f"{i:04d}.png"))
    meta = dict(meta, name=name, frames=len(frames))
    with open(os.path.join(tmp, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    shutil.rmtree(path, ignore_errors=True)
    os.rename(tmp, path)
    return meta


def load(name: str):
    """-> (meta, frames): list-of-lines per frame for text styles, or raw
    PNG bytes per frame when meta["mode"] == "image"."""
    path = anim_dir(name)
    try:
        with open(os.path.join(path, "meta.json")) as f:
            meta = json.load(f)
    except FileNotFoundError:
        raise LibraryError(
            f"no animation named {name!r} (see `motionfetch list`)"
        ) from None
    frames = []
    frame_dir = os.path.join(path, "frames")
    for fname in sorted(os.listdir(frame_dir)):
        if fname.endswith(".txt"):
            with open(os.path.join(frame_dir, fname)) as f:
                frames.append(f.read().split("\n")[: meta["height"]])
        elif fname.endswith(".png"):
            with open(os.path.join(frame_dir, fname), "rb") as f:
                frames.append(f.read())
    if not frames:
        raise LibraryError(f"animation {name!r} has no frames")
    return meta, frames


def list_all():
    """-> sorted list of meta dicts for every stored animation."""
    metas = []
    if os.path.isdir(DATA_DIR):
        for name in sorted(os.listdir(DATA_DIR)):
            try:
                with open(os.path.join(DATA_DIR, name, "meta.json")) as f:
                    metas.append(json.load(f))
            except (OSError, ValueError):
                continue
    return metas


def remove(name: str):
    if not exists(name):
        raise LibraryError(f"no animation named {name!r}")
    shutil.rmtree(anim_dir(name))


def rename(old: str, new: str):
    check_name(new)
    if not exists(old):
        raise LibraryError(f"no animation named {old!r}")
    if exists(new):
        raise LibraryError(f"animation {new!r} already exists")
    os.rename(anim_dir(old), anim_dir(new))
    meta_path = os.path.join(anim_dir(new), "meta.json")
    with open(meta_path) as f:
        meta = json.load(f)
    meta["name"] = new
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
