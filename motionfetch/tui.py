"""Interactive browser: run `motionfetch` with no arguments."""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import library, player

console = Console(highlight=False)


def run():
    from .cli import ensure_stock

    ensure_stock()
    while True:
        metas = library.list_all()
        console.print()
        console.print(
            Panel.fit(
                "[bold]motionfetch[/] — terminal animations for your fetch",
                border_style="magenta",
            )
        )
        if not metas:
            console.print(
                "\nno animations yet. get one with:\n"
                "  [bold]motionfetch generate donut[/]\n"
                "  [bold]motionfetch add some-video.mp4[/]\n"
            )
            return

        table = Table(box=None, header_style="bold dim")
        table.add_column("#", justify="right")
        table.add_column("name")
        table.add_column("size")
        table.add_column("frames")
        table.add_column("style")
        links = __import__("motionfetch.cli", fromlist=["cli"]).installed_links()
        for i, m in enumerate(metas, 1):
            extra = f"  [dim]→ {links[m['name']]}[/]" if m["name"] in links else ""
            table.add_row(
                str(i),
                f"[bold]{m['name']}[/]{extra}",
                f"{m['width']}x{m['height']}",
                str(m["frames"]),
                m.get("style", "?"),
            )
        console.print(table)
        console.print(
            "\n[dim]number[/] play   [dim]f number[/] fetch   "
            "[dim]l number[/] link   [dim]q[/] quit"
        )
        try:
            choice = console.input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if choice in ("q", "quit", "exit", ""):
            return

        action, _, num = choice.rpartition(" ")
        action = action or "play"
        try:
            meta = metas[int(num) - 1]
        except (ValueError, IndexError):
            console.print("[red]?[/] pick a number from the list")
            continue

        name = meta["name"]
        try:
            if action in ("f", "fetch"):
                from .cli import build_parser

                args = build_parser().parse_args(["fetch", name])
                args.func(args)
            elif action in ("l", "link"):
                from .cli import build_parser

                args = build_parser().parse_args(["link", name])
                args.func(args)
            else:
                _, frames = library.load(name)
                blocks, height = player.frames_to_blocks(meta, frames)
                fit = player.check_fit(meta["width"], meta["height"])
                if fit:
                    console.print(f"[red]![/] {fit}")
                    continue
                player.run_loop(blocks, height, meta["fps"])
        except SystemExit:
            pass
        except library.LibraryError as e:
            console.print(f"[red]![/] {e}")
