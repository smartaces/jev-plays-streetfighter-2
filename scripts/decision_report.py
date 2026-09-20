"""Review a local run without any model calls or game inputs."""
import argparse
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from controller.config import ROOT
from controller.report import latest_completed_play, write_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", nargs="?", type=Path, help="Run directory; defaults to latest completed Play session")
    parser.add_argument("--open", action="store_true", help="Open the report in your Mac's browser")
    args = parser.parse_args()
    try:
        path = write_report(args.run or latest_completed_play(ROOT / "runs"))
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(path)
    if args.open:
        subprocess.run(["open", str(path)], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
