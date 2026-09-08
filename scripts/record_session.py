import sys

from polar_activity.cli import main

raise SystemExit(main(["record", *sys.argv[1:]]))
