"""Parse librknnrt PERF_DETAIL text and judge CPU fallback.

Pure functions, unit-tested against a real capture (public YOLOv8n on
RK3588, librknnrt 2.3.2). The perf detail blob has three sections:

1. "Network Layer Information Table": one row per layer with columns
   ID OpType DataType Target InputShape OutputShape Cycles(DDR/NPU/Total)
   Time(us) MacUsage WorkLoad RW(KB) FullName. Shapes contain commas and
   "..." but never spaces, so rows split cleanly on whitespace: Target is
   token 3, and the token after the "a/b/c" Cycles token is Time(us).
2. "Total Operator Elapsed Per Frame Time(us): N".
3. "Operator Time Consuming Ranking Table": per-OpType aggregates.
"""

import math
import re
from typing import List, Optional

STRUCTURAL_OPS = ("InputOperator", "OutputOperator")

_TOTAL_RE = re.compile(r"Total Operator Elapsed Per Frame Time\(us\):\s*(\d+)")
_CYCLES_RE = re.compile(r"^\d+/\d+/\d+$")


def parse_perf_detail(text: str) -> dict:
    layers = []
    ranking = []
    total_us = None
    warnings = 0
    section = None
    for line in (text or "").splitlines():
        if "Network Layer Information Table" in line:
            section = "layers"
            continue
        if "Operator Time Consuming Ranking" in line:
            section = "ranking"
            continue
        match = _TOTAL_RE.search(line)
        if match:
            total_us = int(match.group(1))
            continue
        tokens = line.split()
        if section == "layers" and tokens and tokens[0].isdigit():
            layer = _parse_layer(tokens)
            if layer is None:
                warnings += 1
            else:
                layers.append(layer)
        elif section == "ranking" and tokens and not tokens[0].isdigit():
            row = _parse_ranking(tokens)
            if row is not None:
                ranking.append(row)
    return {"layers": layers, "total_us": total_us,
            "ranking": ranking, "parse_warnings": warnings}


def _parse_layer(tokens: List[str]) -> Optional[dict]:
    try:
        target = tokens[3]
        if target not in ("CPU", "NPU"):
            return None
        time_us = None
        for i, token in enumerate(tokens):
            if _CYCLES_RE.match(token):
                time_us = int(tokens[i + 1])
                break
        if time_us is None:
            return None
        return {"id": int(tokens[0]), "op_type": tokens[1],
                "data_type": tokens[2], "target": target,
                "time_us": time_us, "full_name": tokens[-1]}
    except (IndexError, ValueError):
        return None


def _parse_ranking(tokens: List[str]) -> Optional[dict]:
    if len(tokens) < 7 or tokens[0] in ("OpType", "Total"):
        return None
    try:
        return {"op_type": tokens[0], "calls": int(tokens[1]),
                "cpu_us": int(tokens[2]), "gpu_us": int(tokens[3]),
                "npu_us": int(tokens[4]), "total_us": int(tokens[5]),
                "ratio_pct": float(tokens[6].rstrip("%"))}
    except ValueError:
        return None


def latency_stats(values: List[int]) -> dict:
    if not values:
        return {"p50": None, "p95": None, "mean": None, "min": None, "max": None}
    ordered = sorted(values)

    def percentile(p: float) -> int:
        rank = max(1, math.ceil(p / 100.0 * len(ordered)))
        return ordered[rank - 1]

    return {"p50": percentile(50), "p95": percentile(95),
            "mean": round(sum(ordered) / len(ordered), 1),
            "min": ordered[0], "max": ordered[-1]}


def judge(layers: List[dict], cpu_heavy_pct: float = 20.0,
          npu_only_fallback_pct: float = 1.0, single_op_us: int = 100) -> dict:
    """Verdict from parsed layers. Structural operators (input/output
    staging) always sit on CPU and are reported separately; only real
    fallback ops drive the verdict."""
    structural = [l for l in layers if l["target"] == "CPU" and l["op_type"] in STRUCTURAL_OPS]
    fallback = [l for l in layers if l["target"] == "CPU" and l["op_type"] not in STRUCTURAL_OPS]
    cpu_us = sum(l["time_us"] for l in layers if l["target"] == "CPU")
    npu_us = sum(l["time_us"] for l in layers if l["target"] == "NPU")
    total = cpu_us + npu_us
    fallback_us = sum(l["time_us"] for l in fallback)
    fallback_share = (100.0 * fallback_us / total) if total else 0.0
    worst_single = max((l["time_us"] for l in fallback), default=0)
    if total == 0:
        verdict = "UNKNOWN"
    elif fallback_share > cpu_heavy_pct:
        verdict = "CPU_HEAVY"
    elif fallback_share >= npu_only_fallback_pct or worst_single >= single_op_us:
        verdict = "MIXED"
    else:
        verdict = "NPU_ONLY"
    key = lambda l: (-l["time_us"], l["id"])  # noqa: E731 - sort by time desc
    return {
        "verdict": verdict,
        "cpu_us": cpu_us, "npu_us": npu_us, "layers_total_us": total,
        "cpu_share_pct": round(100.0 * cpu_us / total, 3) if total else 0.0,
        "fallback_us": fallback_us,
        "fallback_share_pct": round(fallback_share, 3),
        "fallback_ops": sorted(fallback, key=key),
        "structural_cpu_ops": sorted(structural, key=key),
    }
