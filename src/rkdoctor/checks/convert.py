"""rkdoctor convert: pre-flight check, recipe generation, post-check.

Stands on top of the official RKNN Toolkit2 — it never converts anything
itself. `check` reads an ONNX file's structure, `recipe` writes a
ready-to-run Toolkit2 script for your own environment, `verify` reuses the
`model` diagnosis as the post-conversion acceptance step.
"""

import hashlib
import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .. import __version__
from ..onnx_parse import OnnxParseError, parse_onnx
from ..probe import Probe
from .model import diagnose_model

# Ops the RKNN converter cannot place as a static graph (control flow must
# be unrolled/removed before conversion).
CONTROL_FLOW_OPS = {"Loop", "If", "Scan"}

# Ops that are documented as limited or frequently need attention in RKNN
# Toolkit2 conversions; presence means "check against your Toolkit2
# version's supported-op list", not automatic failure.
CAUTION_OPS = {
    "NonMaxSuppression": "usually kept outside the RKNN graph as CPU post-processing",
    "RoiAlign": "limited support; check alignment/params against Toolkit2 docs",
    "GridSample": "limited support and modes in Toolkit2",
    "Resize": "some modes/attributes convert with restrictions",
    "ConvTranspose": "some stride/padding combinations convert with restrictions",
    "Einsum": "partial equation support",
    "GatherND": "check indices dtype and batch dims",
    "ScatterND": "check reduction modes",
    "NonZero": "data-dependent output shape; problematic for static graphs",
    "Unique": "data-dependent output shape; problematic for static graphs",
}


def _host_info() -> dict:
    probe = Probe("/")
    try:
        loadavg = [round(value, 2) for value in os.getloadavg()]
    except (OSError, AttributeError):
        loadavg = None
    return {"hostname": platform.node() or "unknown",
            "kernel": platform.release() or "unknown", "loadavg": loadavg}


def _check(check_id, title, status, value, detail, hint=None):
    return {"id": check_id, "title": title, "status": status,
            "value": value, "detail": detail, "hint": hint}


def check_onnx(path: Path, target: str = "rk3588") -> dict:
    data = path.read_bytes()
    report = {
        "schema": 1, "kind": "convert-check", "tool_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "host": _host_info(), "target": target,
        "model": {"file": path.name, "size_bytes": len(data),
                  "sha256": hashlib.sha256(data).hexdigest()},
        "onnx": None, "checks": [], "verdict": "LIKELY_TO_FAIL", "notes": [],
    }
    checks = report["checks"]
    try:
        info = parse_onnx(data)
    except OnnxParseError as error:
        checks.append(_check("parse", "ONNX structure", "fail", None,
                             "could not parse: %s" % error,
                             "The file may be truncated or not an ONNX model"))
        return report
    report["onnx"] = {
        "opset": info["opset"], "producer": info["producer"],
        "graph_name": info["graph_name"], "nodes": len(info["nodes"]),
        "op_hist": info["op_hist"],
        "weights_mb": round(info["weights_bytes"] / 1e6, 2),
        "inputs": [{"name": e["name"], "shape": e["shape"]}
                   for e in info["inputs"] if not e["is_initializer"]],
        "outputs": [{"name": e["name"], "shape": e["shape"]} for e in info["outputs"]],
    }
    checks.append(_check("parse", "ONNX structure", "pass",
                         "%d nodes" % len(info["nodes"]),
                         "opset %s, producer %s" % (info["opset"], info["producer"])))
    if info["opset"] is not None and not (11 <= info["opset"] <= 19):
        checks.append(_check("opset", "ONNX opset", "warn", str(info["opset"]),
                             "outside the commonly used 11-19 range",
                             "Re-export the model with an opset in range, or verify against your Toolkit2 version"))
    else:
        checks.append(_check("opset", "ONNX opset", "pass",
                             str(info["opset"]), "within the commonly used range"))
    dynamic = []
    for entry in info["inputs"]:
        if entry["is_initializer"]:
            continue
        for dim in entry["shape"]:
            if isinstance(dim, str) or dim is None or (isinstance(dim, int) and dim <= 0):
                dynamic.append("%s: %s" % (entry["name"], entry["shape"]))
                break
    if dynamic:
        checks.append(_check("dynamic_dims", "Dynamic dimensions", "warn",
                             "%d input(s)" % len(dynamic), "; ".join(dynamic),
                             "Fix input shapes before conversion (the recipe can pin them); dynamic shapes complicate quantization and deployment"))
    else:
        checks.append(_check("dynamic_dims", "Dynamic dimensions", "pass",
                             "static", "all graph inputs have fixed shapes"))
    present_control = sorted(set(info["op_hist"]) & CONTROL_FLOW_OPS)
    if present_control:
        checks.append(_check("control_flow", "Control-flow operators", "fail",
                             ", ".join(present_control),
                             "RKNN targets a static graph; control flow must be unrolled or removed",
                             "Re-export with loops/conditions unrolled, or split the model"))
    caution = sorted(set(info["op_hist"]) & set(CAUTION_OPS))
    if caution:
        detail = "; ".join("%s (%s)" % (op, CAUTION_OPS[op]) for op in caution)
        checks.append(_check("caution_ops", "Operators needing attention", "warn",
                             ", ".join(caution), detail,
                             "Check each against the supported-op list of your Toolkit2 version"))
    if not present_control:
        checks.append(_check("control_flow", "Control-flow operators", "pass",
                             "none", "no Loop/If/Scan nodes"))
    if not caution:
        checks.append(_check("caution_ops", "Operators needing attention", "pass",
                             "none", "no known-sensitive operators found"))
    tail_ops = [node["op_type"] for node in info["nodes"][-3:]]
    if any(op in ("Sigmoid", "Softmax") for op in tail_ops):
        checks.append(_check("quant_tail", "Quantization-sensitive tail", "warn",
                             ", ".join(tail_ops),
                             "Sigmoid/Softmax at the graph tail often loses accuracy under int8",
                             "Consider FP16 for the tail, or keep those ops outside the quantized graph"))
    real_inputs = [e for e in info["inputs"] if not e["is_initializer"]]
    if len(real_inputs) > 1:
        checks.append(_check("multi_input", "Multiple inputs", "warn",
                             "%d inputs" % len(real_inputs),
                             "the recipe must feed every input; calibration data must match",
                             None))
    checks.append(_check("weights", "Weights volume", "pass",
                         "%.2f MB" % (info["weights_bytes"] / 1e6),
                         "%d initializers" % len(info["initializers"])))
    fails = any(item["status"] == "fail" for item in checks)
    warns = any(item["status"] == "warn" for item in checks)
    report["verdict"] = "LIKELY_TO_FAIL" if fails else "RISKY" if warns else "READY"
    return report


def build_recipe(path: Path, target: str = "rk3588", quantize: str = "none",
                 dataset: Optional[str] = None, info: Optional[dict] = None) -> tuple:
    if info is None:
        info = parse_onnx(path.read_bytes())
    stem = path.stem
    do_quant = quantize == "int8"
    real_inputs = [e for e in info["inputs"] if not e["is_initializer"]]
    static_shapes = []
    dynamic_inputs = []
    for entry in real_inputs:
        if entry["shape"] and all(isinstance(dim, int) and dim > 0 for dim in entry["shape"]):
            static_shapes.append([int(dim) for dim in entry["shape"]])
        else:
            dynamic_inputs.append(entry["name"])
    lines = []
    lines.append("#!/usr/bin/env python3")
    lines.append('"""Convert %s to RKNN for %s — generated by rkdoctor %s.'
                 % (path.name, target, __version__))
    lines.append("")
    lines.append("Run this in YOUR environment with rknn-toolkit2 (2.x) installed;")
    lines.append("rkdoctor only generates the recipe, it does not convert.")
    lines.append('"""')
    lines.append("import sys")
    lines.append("from rknn.api import RKNN")
    lines.append("")
    lines.append("rknn = RKNN(verbose=True)")
    config = ["target_platform=%r" % target]
    if do_quant:
        config.append("quantized_dtype='asymmetric_quantized-8'")
        config.append("quantized_algorithm='normal'")
        config.append("quantized_method='channel'")
    lines.append("ret = rknn.config(%s)" % ", ".join(config))
    lines.append("assert ret == 0, 'config failed'")
    load_args = "model=%r" % path.name
    if static_shapes:
        load_args += ", inputs=%r, input_size_list=%r" % (
            [e["name"] for e in real_inputs], static_shapes)
    lines.append("ret = rknn.load_onnx(%s)" % load_args)
    lines.append("assert ret == 0, 'load_onnx failed'")
    if dynamic_inputs:
        lines.append("# NOTE: input(s) %s have dynamic shapes in the ONNX file." % dynamic_inputs)
        lines.append("# Pin them via input_size_list in load_onnx before building,")
        lines.append("# or re-export the ONNX with fixed shapes.")
    if do_quant:
        lines.append("ret = rknn.build(do_quantization=True, dataset=%r)"
                     % (dataset or "dataset.txt"))
        lines.append("# dataset.txt: one calibration image path per line (100-500 samples).")
    else:
        lines.append("ret = rknn.build(do_quantization=False)")
    lines.append("assert ret == 0, 'build failed'")
    lines.append("ret = rknn.export_rknn(%r)" % (stem + ".rknn"))
    lines.append("assert ret == 0, 'export failed'")
    lines.append("rknn.release()")
    lines.append("print('wrote %s.rknn')" % stem)
    lines.append("")
    recipe = {"model": path.name, "target": target, "quantize": quantize,
              "dataset": dataset if do_quant else None,
              "input_size_list": static_shapes or None,
              "dynamic_inputs": dynamic_inputs,
              "output": stem + ".rknn", "tool_version": __version__}
    return "\n".join(lines), recipe


def verify_model(rknn_path: Path, onnx_path: Optional[Path] = None,
                 runs: int = 20, warmup: int = 3, cpu_heavy_pct: float = 20.0) -> dict:
    report = diagnose_model(rknn_path, runs=runs, warmup=warmup,
                            onnx_path=onnx_path, cpu_heavy_pct=cpu_heavy_pct)
    report["convert_stage"] = "verify"
    return report
