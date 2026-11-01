"""rkdoctor model: run a converted .rknn model and judge CPU fallback."""

import hashlib
import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .. import __version__
from ..perf_parse import judge, latency_stats, parse_perf_detail
from ..probe import Probe
from ..rknn_runtime import ModelLoadError, RknnRuntime, RuntimeUnavailable


def _host_info() -> dict:
    probe = Probe("/")
    model = probe.read("/proc/device-tree/model") or "unknown"
    os_name = "unknown"
    os_release = probe.read("/etc/os-release") or ""
    for line in os_release.splitlines():
        if line.startswith("PRETTY_NAME="):
            os_name = line.split("=", 1)[1].strip().strip('"') or os_name
            break
    try:
        loadavg = [round(value, 2) for value in os.getloadavg()]
    except (OSError, AttributeError):
        loadavg = None
    return {"hostname": platform.node() or "unknown", "soc": model,
            "os": os_name, "kernel": platform.release() or "unknown",
            "loadavg": loadavg}


def _empty_report(path: Path, host: dict) -> dict:
    return {
        "schema": 1, "kind": "model", "tool_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "host": host,
        "runtime": {"api_version": None, "drv_version": None, "lib_path": None},
        "model": {"file": path.name, "size_bytes": None, "sha256": None},
        "inputs": [], "outputs": [],
        "runs": {"warmup": 0, "measured": 0},
        "latency_us": {"p50": None, "p95": None, "mean": None, "min": None, "max": None},
        "perf": None, "fallback_ops": [], "structural_cpu_ops": [],
        "op_type_ranking": [],
        "thresholds": {"cpu_heavy_pct": 20.0, "npu_only_fallback_pct": 1.0,
                       "single_op_us": 100},
        "drift": None, "verdict": "UNKNOWN", "notes": [],
    }


def diagnose_model(path: Path, runs: int = 20, warmup: int = 3,
                   onnx_path: Optional[Path] = None, cpu_heavy_pct: float = 20.0,
                   backend=None) -> dict:
    report = _empty_report(path, _host_info())
    report["thresholds"]["cpu_heavy_pct"] = cpu_heavy_pct
    notes = report["notes"]
    backend = backend if backend is not None else RknnRuntime()
    report["runtime"]["lib_path"] = getattr(backend, "lib_path", None)

    data = path.read_bytes()
    report["model"]["size_bytes"] = len(data)
    report["model"]["sha256"] = hashlib.sha256(data).hexdigest()

    ok, reason = backend.available()
    if not ok:
        notes.append("runtime unavailable: " + reason)
        return report
    try:
        backend.load(data)
    except (ModelLoadError, RuntimeUnavailable) as error:
        notes.append("model load failed: " + str(error))
        return report
    try:
        try:
            api_version, drv_version = backend.sdk_version()
            report["runtime"]["api_version"] = api_version
            report["runtime"]["drv_version"] = drv_version
        except ModelLoadError as error:
            notes.append("sdk version query failed: " + str(error))
        input_attrs, output_attrs = backend.io_attrs()
        report["inputs"] = [_public_attr(attr) for attr in input_attrs]
        report["outputs"] = [_public_attr(attr) for attr in output_attrs]
        if any(dim <= 0 for attr in input_attrs for dim in attr["dims"]):
            notes.append("dynamic input shape: latency and fallback need a fixed input; not measured")
            return report
        inputs = backend.make_zero_inputs(input_attrs)
        for _ in range(warmup):
            backend.run_once(inputs)
        samples = [backend.run_once(inputs) for _ in range(runs)]
        report["runs"] = {"warmup": warmup, "measured": len(samples)}
        report["latency_us"] = latency_stats(samples)
        detail = parse_perf_detail(backend.perf_detail())
        if not detail["layers"]:
            notes.append("PERF_DETAIL returned no layer rows; fallback cannot be judged")
            return report
        result = judge(detail["layers"], cpu_heavy_pct=cpu_heavy_pct)
        report["verdict"] = result["verdict"]
        report["fallback_ops"] = result["fallback_ops"]
        report["structural_cpu_ops"] = result["structural_cpu_ops"]
        report["op_type_ranking"] = detail["ranking"]
        report["perf"] = {
            "layers_total_us": result["layers_total_us"],
            "detail_total_us": detail["total_us"],
            "perf_run_last_us": samples[-1] if samples else None,
            "cpu_us": result["cpu_us"], "npu_us": result["npu_us"],
            "cpu_share_pct": result["cpu_share_pct"],
            "fallback_us": result["fallback_us"],
            "fallback_share_pct": result["fallback_share_pct"],
        }
        _reconcile(report, detail, samples)
        if detail["parse_warnings"]:
            notes.append("perf detail: %d layer row(s) could not be parsed" % detail["parse_warnings"])
        if onnx_path is not None:
            report["drift"] = _drift(backend, onnx_path, input_attrs, notes)
        return report
    except ModelLoadError as error:
        notes.append("diagnosis failed: " + str(error))
        report["verdict"] = "UNKNOWN"
        return report
    finally:
        backend.destroy()


def _public_attr(attr: dict) -> dict:
    return {"name": attr["name"], "dims": attr["dims"],
            "type": attr["type_name"], "fmt": attr["fmt_name"]}


def _reconcile(report: dict, detail: dict, samples: list) -> None:
    layers_total = report["perf"]["layers_total_us"]
    reference = detail["total_us"]
    if reference:
        if abs(layers_total - reference) / reference > 0.10:
            report["notes"].append(
                "layer times sum to %d us but runtime total is %d us (>10%% apart)"
                % (layers_total, reference))
    if samples and reference:
        if abs(samples[-1] - reference) / reference > 0.10:
            report["notes"].append(
                "last PERF_RUN is %d us but PERF_DETAIL total is %d us (>10%% apart)"
                % (samples[-1], reference))


def _drift(backend, onnx_path: Path, input_attrs: list, notes: list):
    try:
        import numpy as np
        import onnxruntime as ort
    except ImportError:
        notes.append("drift skipped: onnxruntime is not installed")
        return None
    try:
        session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        rknn_outputs = backend.get_outputs()
        if rknn_outputs is None:
            notes.append("drift skipped: RKNN outputs are not FLOAT32")
            return None
        feeds = {}
        for attr, onnx_input in zip(input_attrs, session.get_inputs()):
            shape = [int(dim) for dim in onnx_input.shape]
            if shape != attr["dims"]:
                notes.append("drift skipped: ONNX input shape %s != RKNN %s"
                             % (shape, attr["dims"]))
                return None
            feeds[onnx_input.name] = np.zeros(shape, dtype=np.float32)
        onnx_outputs = session.run(None, feeds)
        if len(onnx_outputs) != len(rknn_outputs):
            notes.append("drift skipped: output count differs from the RKNN model")
            return None
        max_abs = 0.0
        total_abs = 0.0
        total_count = 0
        for (_, rknn_values), onnx_values in zip(rknn_outputs, onnx_outputs):
            flat = np.asarray(onnx_values, dtype=np.float64).ravel()
            if flat.size != len(rknn_values):
                notes.append("drift skipped: an output size differs from the RKNN model")
                return None
            diff = np.abs(flat - np.asarray(rknn_values, dtype=np.float64))
            max_abs = max(max_abs, float(diff.max()) if diff.size else 0.0)
            total_abs += float(diff.sum())
            total_count += int(diff.size)
        return {"max_abs": max_abs,
                "mean_abs": (total_abs / total_count) if total_count else 0.0,
                "outputs_compared": len(rknn_outputs)}
    except Exception as error:  # drift is best-effort; never fail the diagnosis
        notes.append("drift skipped: %s" % error)
        return None
