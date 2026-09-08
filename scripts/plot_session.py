import sys

from polar_activity.cli import main

raise SystemExit(main(["plot", *sys.argv[1:]]))
