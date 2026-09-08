import sys

from polar_activity.cli import main

raise SystemExit(main(["verify", *sys.argv[1:]]))
