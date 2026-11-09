"""Command-line entry point."""

import argparse
from pathlib import Path
import sys
from typing import List, Optional

from . import __version__
from .checks.board import run_checks
from .checks.convert import build_recipe, check_onnx, verify_model
from .checks.model import diagnose_model
from .checks.rtsp import diagnose_rtsp
from .onnx_parse import OnnxParseError, parse_onnx
from .probe import Probe
from .report import (build_report, terminal_convert_report, terminal_model_report,
                     terminal_report, terminal_rtsp_report, verdict_line,
                     write_convert_reports, write_model_reports, write_reports,
                     write_rtsp_reports)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="rkdoctor", description="Offline Rockchip board diagnostics")
    parser.add_argument("--version", action="version", version="rkdoctor " + __version__)
    commands = parser.add_subparsers(dest="command", required=True)
    board = commands.add_parser("board", help="Inspect board health")
    board.add_argument("--json", metavar="PATH", help="Write schema 1 JSON")
    board.add_argument("--html", metavar="PATH", help="Write a standalone HTML report")
    board.add_argument("--root", default="/", metavar="DIR", help="Redirect probe files into DIR")
    board.add_argument("--quiet", action="store_true", help="Print only verdict and report paths")
    model = commands.add_parser("model", help="Diagnose a converted .rknn model (CPU fallback, latency)")
    model.add_argument("model_path", metavar="MODEL", help="Path to the .rknn model file")
    model.add_argument("--runs", type=int, default=20, help="Measured inference runs (default 20)")
    model.add_argument("--warmup", type=int, default=3, help="Warmup runs before measuring (default 3)")
    model.add_argument("--onnx", metavar="PATH", help="Optional source .onnx for output drift comparison")
    model.add_argument("--cpu-heavy-pct", type=float, default=20.0,
                       help="CPU fallback time share above which the verdict is CPU_HEAVY (default 20)")
    model.add_argument("--json", metavar="PATH", help="Write schema 1 JSON")
    model.add_argument("--html", metavar="PATH", help="Write a standalone HTML report")
    rtsp = commands.add_parser("rtsp", help="Acceptance-check an RTSP camera stream")
    rtsp.add_argument("url", metavar="URL", help="rtsp:// URL of the stream")
    rtsp.add_argument("--duration", type=float, default=15.0,
                      help="Measurement window in seconds (default 15)")
    rtsp.add_argument("--transport", choices=["udp", "tcp"], default="udp",
                      help="RTP transport (default udp)")
    rtsp.add_argument("--expect-width", type=int, help="Expected frame width in px")
    rtsp.add_argument("--expect-height", type=int, help="Expected frame height in px")
    rtsp.add_argument("--expect-fps", type=float, help="Expected frame rate")
    rtsp.add_argument("--json", metavar="PATH", help="Write schema 1 JSON")
    rtsp.add_argument("--html", metavar="PATH", help="Write a standalone HTML report")
    convert = commands.add_parser("convert", help="ONNX -> RKNN conversion trio (check / recipe / verify)")
    convert_sub = convert.add_subparsers(dest="convert_command", required=True)
    cv_check = convert_sub.add_parser("check", help="Pre-flight check an ONNX model")
    cv_check.add_argument("onnx_path", metavar="ONNX", help="Path to the .onnx model")
    cv_check.add_argument("--target", default="rk3588", help="Target platform (default rk3588)")
    cv_check.add_argument("--json", metavar="PATH", help="Write schema 1 JSON")
    cv_check.add_argument("--html", metavar="PATH", help="Write a standalone HTML report")
    cv_recipe = convert_sub.add_parser("recipe", help="Generate a Toolkit2 conversion script")
    cv_recipe.add_argument("onnx_path", metavar="ONNX", help="Path to the .onnx model")
    cv_recipe.add_argument("--target", default="rk3588", help="Target platform (default rk3588)")
    cv_recipe.add_argument("--quantize", choices=["none", "int8"], default="none",
                           help="Quantization for the recipe (default none)")
    cv_recipe.add_argument("--dataset", metavar="PATH", help="Calibration dataset list for int8")
    cv_recipe.add_argument("--out", metavar="PATH", help="Write the script here (default: stdout)")
    cv_verify = convert_sub.add_parser("verify", help="Post-conversion acceptance (same engine as `model`)")
    cv_verify.add_argument("model_path", metavar="MODEL", help="Path to the converted .rknn model")
    cv_verify.add_argument("--onnx", metavar="PATH", help="Optional source .onnx for drift comparison")
    cv_verify.add_argument("--runs", type=int, default=20, help="Measured inference runs (default 20)")
    cv_verify.add_argument("--warmup", type=int, default=3, help="Warmup runs (default 3)")
    cv_verify.add_argument("--json", metavar="PATH", help="Write schema 1 JSON")
    cv_verify.add_argument("--html", metavar="PATH", help="Write a standalone HTML report")
    args, extra = parser.parse_known_args(argv)

    if extra:
        parser.error("unrecognized arguments: " + " ".join(extra))

    if args.command == "rtsp":
        if not args.url.startswith("rtsp://"):
            print("rkdoctor: not an rtsp:// URL: " + args.url, file=sys.stderr)
            return 2
        expect = {}
        if args.expect_width:
            expect["width"] = args.expect_width
        if args.expect_height:
            expect["height"] = args.expect_height
        if args.expect_fps:
            expect["fps"] = args.expect_fps
        report = diagnose_rtsp(args.url, duration=args.duration,
                               transport=args.transport, expect=expect)
        try:
            write_rtsp_reports(report, args.json, args.html)
        except (OSError, ValueError) as error:
            print("rkdoctor: cannot write report: " + str(error), file=sys.stderr)
            return 1
        print(terminal_rtsp_report(report, [p for p in (args.json, args.html) if p]))
        return 0

    if args.command == "convert":
        if args.convert_command in ("check", "recipe"):
            path = Path(args.onnx_path)
            if not path.is_file():
                print("rkdoctor: onnx file not found: " + args.onnx_path, file=sys.stderr)
                return 2
        if args.convert_command == "check":
            report = check_onnx(path, target=args.target)
            try:
                write_convert_reports(report, args.json, args.html)
            except (OSError, ValueError) as error:
                print("rkdoctor: cannot write report: " + str(error), file=sys.stderr)
                return 1
            print(terminal_convert_report(report, [p for p in (args.json, args.html) if p]))
            return 0
        if args.convert_command == "recipe":
            try:
                info = parse_onnx(path.read_bytes())
            except OnnxParseError as error:
                print("rkdoctor: cannot parse onnx: " + str(error), file=sys.stderr)
                return 2
            script, recipe = build_recipe(path, target=args.target,
                                          quantize=args.quantize,
                                          dataset=args.dataset, info=info)
            if args.out:
                Path(args.out).write_text(script, encoding="utf-8")
                print("recipe written: " + args.out + " -> " + recipe["output"]
                      + " (target " + recipe["target"] + ", quantize " + recipe["quantize"] + ")")
            else:
                print(script)
            return 0
        # verify
        path = Path(args.model_path)
        if not path.is_file():
            print("rkdoctor: model file not found: " + args.model_path, file=sys.stderr)
            return 2
        if args.onnx and not Path(args.onnx).is_file():
            print("rkdoctor: onnx file not found: " + args.onnx, file=sys.stderr)
            return 2
        report = verify_model(path, onnx_path=Path(args.onnx) if args.onnx else None,
                              runs=args.runs, warmup=args.warmup)
        try:
            write_model_reports(report, args.json, args.html)
        except (OSError, ValueError) as error:
            print("rkdoctor: cannot write report: " + str(error), file=sys.stderr)
            return 1
        print(terminal_model_report(report, [p for p in (args.json, args.html) if p]))
        return 0

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
    paths = [path for path in (args.json, args.html) if path]
    print(verdict_line(report, paths) if args.quiet else terminal_report(report, paths))
    return 0
