"""Command-line entry point."""

import argparse
from pathlib import Path
import sys
from typing import List, Optional

from . import __version__
from .checks.board import run_checks
from .checks.model import diagnose_model
from .probe import Probe
from .report import (build_report, terminal_model_report, terminal_report,
                     write_model_reports, write_reports)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="rkdoctor", description="Offline Rockchip board diagnostics")
    parser.add_argument("--version", action="version", version="rkdoctor " + __version__)
    commands = parser.add_subparsers(dest="command", required=True)
    board = commands.add_parser("board", help="Inspect board health")
    board.add_argument("--json", metavar="PATH", help="Write schema 1 JSON")
    board.add_argument("--html", metavar="PATH", help="Write a standalone HTML report")
    board.add_argument("--root", default="/", metavar="DIR", help="Redirect probe files into DIR")
    model = commands.add_parser("model", help="Diagnose a converted .rknn model (CPU fallback, latency)")
    model.add_argument("model_path", metavar="MODEL", help="Path to the .rknn model file")
    model.add_argument("--runs", type=int, default=20, help="Measured inference runs (default 20)")
    model.add_argument("--warmup", type=int, default=3, help="Warmup runs before measuring (default 3)")
    model.add_argument("--onnx", metavar="PATH", help="Optional source .onnx for output drift comparison")
    model.add_argument("--cpu-heavy-pct", type=float, default=20.0,
                       help="CPU fallback time share above which the verdict is CPU_HEAVY (default 20)")
    model.add_argument("--json", metavar="PATH", help="Write schema 1 JSON")
    model.add_argument("--html", metavar="PATH", help="Write a standalone HTML report")
    commands.add_parser("rtsp", help="Planned; not implemented yet")
    args, extra = parser.parse_known_args(argv)

    if args.command == "rtsp":
        print("rtsp is planned for v0.3; not implemented yet.")
        print("rtsp 计划于 v0.3 实现；当前仅保留接口。")
        return 3
    if extra:
        parser.error("unrecognized arguments: " + " ".join(extra))

    if args.command == "model":
        path = Path(args.model_path)
        if not path.is_file():
            print("rkdoctor: model file not found: " + args.model_path, file=sys.stderr)
            return 2
        if args.onnx and not Path(args.onnx).is_file():
            print("rkdoctor: onnx file not found: " + args.onnx, file=sys.stderr)
            return 2
        report = diagnose_model(path, runs=args.runs, warmup=args.warmup,
                                onnx_path=Path(args.onnx) if args.onnx else None,
                                cpu_heavy_pct=args.cpu_heavy_pct)
        try:
            write_model_reports(report, args.json, args.html)
        except (OSError, ValueError) as error:
            print("rkdoctor: cannot write report: " + str(error), file=sys.stderr)
            return 1
        print(terminal_model_report(report, [p for p in (args.json, args.html) if p]))
        return 0

    probe = Probe(args.root)
    report = build_report(run_checks(probe), probe)
    try:
        write_reports(report, args.json, args.html)
    except (OSError, ValueError) as error:
        print("rkdoctor: cannot write report: " + str(error), file=sys.stderr)
        return 1
    print(terminal_report(report, [path for path in (args.json, args.html) if path]))
    return 0
