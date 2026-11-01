"""Command-line entry point."""

import argparse
import sys
from typing import List, Optional

from . import __version__
from .checks.board import run_checks
from .probe import Probe
from .report import build_report, terminal_report, write_reports


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="rkdoctor", description="Offline Rockchip board diagnostics")
    parser.add_argument("--version", action="version", version="rkdoctor " + __version__)
    commands = parser.add_subparsers(dest="command", required=True)
    board = commands.add_parser("board", help="Inspect board health")
    board.add_argument("--json", metavar="PATH", help="Write schema 1 JSON")
    board.add_argument("--html", metavar="PATH", help="Write a standalone HTML report")
    board.add_argument("--root", default="/", metavar="DIR", help="Redirect probe files into DIR")
    for name in ("model", "rtsp"):
        commands.add_parser(name, help="Planned; not implemented in v0.1")
    args, extra = parser.parse_known_args(argv)
    if args.command in ("model", "rtsp"):
        version = "v0.2" if args.command == "model" else "v0.3"
        print(args.command + " is planned for " + version + "; not implemented in v0.1.")
        print(args.command + " 计划于 " + version + " 实现；v0.1 仅保留接口。")
        return 3
    if extra:
        parser.error("unrecognized arguments: " + " ".join(extra))
    probe = Probe(args.root)
    report = build_report(run_checks(probe), probe)
    try:
        write_reports(report, args.json, args.html)
    except (OSError, ValueError) as error:
        print("rkdoctor: cannot write report: " + str(error), file=sys.stderr)
        return 1
    print(terminal_report(report, [path for path in (args.json, args.html) if path]))
    return 0
