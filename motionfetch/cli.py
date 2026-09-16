"""motionfetch command line interface."""

import argparse
import os
import sys

from rich.console import Console
from rich.table import Table

from . import __version__, convert, generators, library, links, player

console = Console(highlight=False)
err_console = Console(stderr=True, style="bold red", highlight=False)

BIN_DIR = os.path.expanduser("~/.local/bin")


def die(msg):
    err_console.print(f"error: {msg}")
    sys.exit(1)


def encode_pngs(frames):
    """PIL frames -> list of PNG bytes (image style)."""
    import io

    out = []
    for img in frames:
        buf = io.BytesIO()
        img.save(buf, "PNG")
        out.append(buf.getvalue())
    return out


def crop_dict(args):
    return {
        "top": args.crop_top,
        "bottom": args.crop_bottom,
        "left": args.crop_left,
        "right": args.crop_right,
    }


# ── commands ──


def cmd_add(args):
    source = args.source
    if not os.path.exists(source):
        die(f"no such file: {source}")
    name = args.name or os.path.splitext(os.path.basename(source))[0]
    name = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)
    library.check_name(name)
    if library.exists(name) and not args.force:
        die(f"animation {name!r} already exists (pick --name or use --force)")

    video_exts = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".gif"}
    is_video = os.path.splitext(source)[1].lower() in video_exts
    if os.path.splitext(source)[1].lower() == ".gif":
        is_video = False  # Pillow reads GIF frames directly

    crop = crop_dict(args)
    with console.status(f"converting [bold]{source}[/] → [bold]{name}[/] …"):
        if is_video:
            frames = list(
                convert.video_frames(
                    source, args.width, args.style, args.fps, crop,
                    start=args.start, duration=args.duration,
                    max_frames=args.max_frames, gamma=args.gamma,
                    invert=args.invert,
                )
            )
        else:
            frames = convert.image_frames(
                source, args.width, args.style, crop,
                gamma=args.gamma, invert=args.invert,
            )
    if not frames:
        die("conversion produced no frames")
    convert.pad_frames(frames, args.width)

    if args.style == "image":
        mode = "image"
        # rows from the actual aspect of the converted frames
        height = max(
            1, round(args.width * frames[0].height / frames[0].width * 0.5)
        )
    else:
        mode = "color" if args.style in ("blocks", "ascii-color") else "mono"
        height = len(frames[0])
    meta = {
        "mode": mode,
        "style": args.style,
        "fps": args.fps,
        "width": args.width,
        "height": height,
        "source": os.path.basename(source),
    }

    if not args.no_preview and sys.stdout.isatty():
        console.print(
            f"[dim]previewing {len(frames)} frames — press any key to stop[/]"
        )
        if args.style == "image":
            try:
                player.run_kitty_loop(
                    encode_pngs(frames), meta["width"], meta["height"],
                    args.fps, clear=False,
                )
            except RuntimeError as e:
                console.print(f"[yellow]no preview:[/] {e}")
        else:
            blocks, height = player.frames_to_blocks(meta, frames, args.tint)
            player.run_loop(blocks, height, args.fps, clear=False)
        try:
            answer = console.input(f"save as [bold]{name}[/]? \\[Y/n] ")
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer.strip().lower() in ("n", "no"):
            console.print("[dim]discarded[/]")
            return

    library.save(name, frames, meta, overwrite=args.force)
    console.print(
        f"[green]saved[/] [bold]{name}[/] "
        f"({len(frames)} frames, {meta['width']}x{meta['height']}, {args.style})"
    )
    console.print(
        f"  play it:            [bold]motionfetch play {name}[/]\n"
        f"  with fastfetch:     [bold]motionfetch fetch {name}[/]\n"
        f"  as its own command: [bold]motionfetch link {name}[/]"
    )


def cmd_generate(args):
    name = args.name or args.kind
    if library.exists(name) and not args.force:
        die(f"animation {name!r} already exists (pick --name or use --force)")
    with console.status(f"rendering [bold]{args.kind}[/] …"):
        frames, meta = generators.make(
            args.kind, args.width, args.height, args.frames
        )
    convert.pad_frames(frames, meta["width"])
    meta["fps"] = args.fps
    library.save(name, frames, meta, overwrite=args.force)
    console.print(
        f"[green]saved[/] [bold]{name}[/] ({len(frames)} frames) — "
        f"try [bold]motionfetch fetch {name}[/]"
    )


def ensure_stock():
    """First run: create the two stock animations (donut, logo)."""
    if library.list_all():
        return
    with console.status("first run — rendering the stock animations …"):
        created = generators.ensure_stock()
    if created:
        console.print(f"[dim]created stock animations: {', '.join(created)}[/]")


def cmd_list(_args):
    ensure_stock()
    metas = library.list_all()
    if not metas:
        console.print(
            "no animations yet — try [bold]motionfetch generate donut[/] "
            "or [bold]motionfetch add video.mp4[/]"
        )
        return
    table = Table(box=None, header_style="bold")
    for col in ("name", "size", "frames", "fps", "style", "source"):
        table.add_column(col)
    links = installed_links()
    for m in metas:
        name = m["name"]
        suffix = f"  [dim]→ {links[name]}[/]" if name in links else ""
        table.add_row(
            f"[bold]{name}[/]{suffix}",
            f"{m['width']}x{m['height']}",
            str(m["frames"]),
            str(m["fps"]),
            m.get("style", "?"),
            m.get("source", "?"),
        )
    console.print(table)


def load_or_die(name):
    try:
        return library.load(name)
    except library.LibraryError as e:
        die(e)


def cmd_play(args):
    meta, frames = load_or_die(args.name)
    problem = player.check_fit(meta["width"], meta["height"])
    if problem:
        die(problem)
    if meta.get("mode") == "image":
        try:
            player.run_kitty_loop(
                frames, meta["width"], meta["height"],
                args.fps or meta["fps"], secs=args.secs,
                clear=not args.no_clear,
            )
        except RuntimeError as e:
            die(e)
        return
    blocks, height = player.frames_to_blocks(meta, frames, args.tint)
    player.run_loop(
        blocks, height, args.fps or meta["fps"], secs=args.secs,
        clear=not args.no_clear,
    )


def cmd_fetch(args):
    meta, frames = load_or_die(args.name)
    try:
        info, info_w = player.fetch_info(args.cmd)
    except RuntimeError as e:
        die(e)
    if meta.get("mode") == "image":
        problem = player.check_fit(
            meta["width"] + args.gap + info_w,
            max(meta["height"], len(info)),
        )
        if problem:
            sys.stdout.write("\n".join(info) + "\n")
            console.print(f"[dim]({args.name} hidden: {problem})[/]")
            return
        try:
            player.run_kitty_loop(
                frames, meta["width"], meta["height"],
                args.fps or meta["fps"], info_lines=info, gap=args.gap,
                secs=args.secs, clear=not args.no_clear, once=args.once,
            )
        except RuntimeError as e:
            die(e)
        return
    blocks, height = player.compose_fetch(
        meta, frames, info, gap=args.gap, tint=args.tint
    )
    problem = player.check_fit(meta["width"] + args.gap + info_w, height)
    if problem:
        # narrow terminal: print the info box alone rather than wrap
        sys.stdout.write("\n".join(info) + "\n")
        console.print(f"[dim]({args.name} hidden: {problem})[/]")
        return
    if args.once:
        sys.stdout.write(blocks[0])
        return
    player.run_loop(
        blocks, height, args.fps or meta["fps"], secs=args.secs,
        clear=not args.no_clear,
    )


def installed_links():
    return links.installed()


def cmd_link(args):
    meta, _ = load_or_die(args.name)
    try:
        path = links.create(
            meta["name"], args.command, play=args.play, force=args.force
        )
    except links.LinkError as e:
        die(e)
    console.print(f"[green]created[/] [bold]{os.path.basename(path)}[/] → {path}")
    if BIN_DIR not in os.environ.get("PATH", "").split(":"):
        console.print(f"[yellow]note:[/] {BIN_DIR} is not in your PATH")


def cmd_unlink(args):
    try:
        links.remove(args.command)
    except links.LinkError as e:
        die(e)
    console.print(f"removed [bold]{args.command}[/]")


def cmd_export(args):
    from . import export

    meta, frames = load_or_die(args.name)
    if args.fetch and meta.get("mode") == "image":
        die("--fetch export is not supported for the image style yet")
    if args.fetch:
        try:
            info, info_w = player.fetch_info(args.cmd)
        except RuntimeError as e:
            die(e)
        frames, height = player.compose_lines(
            meta, frames, info, gap=args.gap, tint=args.tint
        )
        meta = dict(
            meta,
            width=meta["width"] + args.gap + info_w,
            height=height,
            mode="color",  # tint is baked into the composed lines
        )
    with console.status(f"rendering [bold]{args.out}[/] …"):
        path, n = export.export(
            meta, frames, args.out, fps=args.fps, font_size=args.font_size,
            tint=args.tint, max_frames=args.max_frames,
        )
    console.print(f"[green]wrote[/] {path} ({n} frames)")


def cmd_remove(args):
    try:
        library.remove(args.name)
    except library.LibraryError as e:
        die(e)
    console.print(f"removed [bold]{args.name}[/]")


def cmd_rename(args):
    try:
        library.rename(args.old, args.new)
    except library.LibraryError as e:
        die(e)
    console.print(f"renamed [bold]{args.old}[/] → [bold]{args.new}[/]")


def cmd_info(args):
    meta, frames = load_or_die(args.name)
    for key in ("name", "style", "mode", "fps", "frames", "source"):
        console.print(f"[bold]{key:8}[/] {meta.get(key)}")
    console.print(f"[bold]{'size':8}[/] {meta['width']}x{meta['height']} cells")
    console.print(f"[bold]{'path':8}[/] {library.anim_dir(meta['name'])}")


# ── parser ──


def add_tint(p):
    p.add_argument("--tint", metavar="HEX",
                   help="tint mono animations with this #rrggbb color")


def add_playback(p):
    p.add_argument("--fps", type=int, help="override stored frame rate")
    p.add_argument("--secs", type=float, help="stop after this many seconds")
    p.add_argument("--no-clear", action="store_true",
                   help="draw at the cursor instead of clearing the screen")
    add_tint(p)


def build_parser():
    p = argparse.ArgumentParser(
        prog="motionfetch",
        description="Turn videos and images into terminal animations, "
        "and run them beside fastfetch.",
    )
    p.add_argument("--version", action="version",
                   version=f"motionfetch {__version__}")
    sub = p.add_subparsers(dest="command")

    a = sub.add_parser("add", help="convert a video or image into an animation")
    a.add_argument("source", help="video (mp4/mkv/webm/…), gif, or image")
    a.add_argument("-n", "--name", help="library name (default: file name)")
    a.add_argument("-w", "--width", type=int, default=48,
                   help="width in terminal columns (default 48)")
    a.add_argument("--style", choices=convert.STYLES, default="blocks",
                   help="blocks = color pixels, ascii/braille = tintable text")
    a.add_argument("--fps", type=int, default=15, help="frames per second")
    a.add_argument("--start", help="skip into the video (seconds or 0:30)")
    a.add_argument("--duration", help="how much of the video to take")
    a.add_argument("--max-frames", type=int, default=400)
    a.add_argument("--crop-top", metavar="N", help="crop N px or N%% off the top"
                   " (e.g. --crop-top 15%% to drop a caption)")
    a.add_argument("--crop-bottom", metavar="N")
    a.add_argument("--crop-left", metavar="N")
    a.add_argument("--crop-right", metavar="N")
    a.add_argument("--gamma", type=float, default=1.0,
                   help="brightness curve for ascii/braille (try 0.6)")
    a.add_argument("--invert", action="store_true",
                   help="light-on-dark ↔ dark-on-light for ascii/braille")
    a.add_argument("--no-preview", action="store_true",
                   help="save without showing a preview first")
    a.add_argument("-f", "--force", action="store_true",
                   help="overwrite an existing animation")
    add_tint(a)
    a.set_defaults(func=cmd_add)

    g = sub.add_parser("generate", help="create a stock animation "
                       "(donut, logo)")
    g.add_argument("kind", choices=sorted(generators.GENERATORS))
    g.add_argument("-n", "--name")
    g.add_argument("-w", "--width", type=int, default=48)
    g.add_argument("--height", type=int, default=20)
    g.add_argument("--frames", type=int,
                   help="frames per loop (default: per animation)")
    g.add_argument("--fps", type=int, default=15)
    g.add_argument("-f", "--force", action="store_true")
    g.set_defaults(func=cmd_generate)

    sub.add_parser("list", help="list stored animations").set_defaults(
        func=cmd_list
    )

    pl = sub.add_parser("play", help="loop an animation until a key is pressed")
    pl.add_argument("name")
    add_playback(pl)
    pl.set_defaults(func=cmd_play)

    fe = sub.add_parser("fetch", help="animation + fastfetch, side by side")
    fe.add_argument("name")
    fe.add_argument("--gap", type=int, default=3,
                    help="columns between animation and info box")
    fe.add_argument("--cmd", help="use this command's output instead of "
                    "fastfetch (e.g. --cmd nitch)")
    fe.add_argument("--once", action="store_true",
                    help="print a single static frame and exit")
    add_playback(fe)
    fe.set_defaults(func=cmd_fetch)

    li = sub.add_parser("link", help="install a command that runs an animation"
                        " (default name: fastfetch-<name>)")
    li.add_argument("name", help="animation to link")
    li.add_argument("command", nargs="?",
                    help="command name, e.g. fastfetch_1")
    li.add_argument("--play", action="store_true",
                    help="command plays the animation alone, without fastfetch")
    li.add_argument("-f", "--force", action="store_true")
    li.set_defaults(func=cmd_link)

    ul = sub.add_parser("unlink", help="remove a command created by link")
    ul.add_argument("command")
    ul.set_defaults(func=cmd_unlink)

    ex = sub.add_parser("export", help="render to a .gif or .png preview")
    ex.add_argument("name")
    ex.add_argument("out", help="output file (.gif or .png)")
    ex.add_argument("--fps", type=int)
    ex.add_argument("--font-size", type=int, default=15)
    ex.add_argument("--max-frames", type=int)
    ex.add_argument("--fetch", action="store_true",
                    help="export the animation composed beside fastfetch")
    ex.add_argument("--cmd", help="info command for --fetch (default fastfetch)")
    ex.add_argument("--gap", type=int, default=3)
    add_tint(ex)
    ex.set_defaults(func=cmd_export)

    rm = sub.add_parser("remove", help="delete an animation")
    rm.add_argument("name")
    rm.set_defaults(func=cmd_remove)

    rn = sub.add_parser("rename")
    rn.add_argument("old")
    rn.add_argument("new")
    rn.set_defaults(func=cmd_rename)

    inf = sub.add_parser("info", help="show an animation's details")
    inf.add_argument("name")
    inf.set_defaults(func=cmd_info)

    gu = sub.add_parser("gui", help="open the graphical interface")
    gu.set_defaults(func=cmd_gui)

    return p


def cmd_gui(_args):
    try:
        from . import gui
    except ImportError:
        die(
            "the GUI needs PySide6 — install with:\n"
            "  pipx install 'motionfetch[gui]'   (or pip install PySide6)"
        )
    gui.main()


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command is None:
        from . import tui

        tui.run()
        return
    try:
        args.func(args)
    except (convert.ConvertError, library.LibraryError) as e:
        die(e)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
