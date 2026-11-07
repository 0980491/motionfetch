"""Entry point. `greet` runs in every new terminal, so it is dispatched before
cli.py and its imports (rich, numpy, PIL) are loaded — about 100 ms saved on
each terminal open."""

import sys


def run():
    if sys.argv[1:2] == ["greet"]:
        from .greet import main as greet_main

        sys.exit(greet_main(sys.argv[2:]))
    from .cli import main

    main()


if __name__ == "__main__":
    run()
