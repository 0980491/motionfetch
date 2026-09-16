"""Per-animation commands in ~/.local/bin (shared by the CLI and the GUI).

Each link is a tiny marked shell script, so `unlink` can refuse to touch
anything motionfetch did not create.
"""

import os
import stat

BIN_DIR = os.path.expanduser("~/.local/bin")

MARK = "# managed by motionfetch"


class LinkError(Exception):
    pass


def installed():
    """-> {animation name: command name} for links created by us."""
    links = {}
    if not os.path.isdir(BIN_DIR):
        return links
    for fname in os.listdir(BIN_DIR):
        path = os.path.join(BIN_DIR, fname)
        try:
            if not os.path.isfile(path) or os.path.getsize(path) > 4096:
                continue
            with open(path) as f:
                text = f.read()
        except OSError:
            continue
        if MARK in text:
            for line in text.splitlines():
                if line.startswith("# animation:"):
                    links[line.split(":", 1)[1].strip()] = fname
    return links


def create(anim_name, command=None, play=False, force=False):
    """Install a command that runs `motionfetch fetch/play <anim>`."""
    command = command or f"fastfetch-{anim_name}"
    os.makedirs(BIN_DIR, exist_ok=True)
    path = os.path.join(BIN_DIR, command)
    if os.path.exists(path) and not force:
        with open(path) as f:
            if MARK not in f.read():
                raise LinkError(
                    f"{path} exists and was not created by motionfetch"
                )
    action = "play" if play else "fetch"
    with open(path, "w") as f:
        f.write(
            "#!/bin/sh\n"
            f"{MARK}\n"
            f"# animation: {anim_name}\n"
            f'exec motionfetch {action} "{anim_name}" "$@"\n'
        )
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return path


def remove(command):
    path = os.path.join(BIN_DIR, command)
    if not os.path.isfile(path):
        raise LinkError(f"no such command: {path}")
    with open(path) as f:
        if MARK not in f.read():
            raise LinkError(
                f"{path} was not created by motionfetch — not touching it"
            )
    os.remove(path)
