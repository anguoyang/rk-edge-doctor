# Changelog

## v0.2.0 (2026-11-01)

- `rkdoctor model` is implemented: it loads a converted `.rknn` model through
  the system librknnrt via ctypes (Python standard library only, no rknnlite
  or other packages needed), runs warmup + measured inferences, and reports:
  - the operators that execute on CPU instead of the NPU (fallback list),
    with input/output staging operators reported separately;
  - p50/p95/mean latency from the runtime's own PERF_RUN counters;
  - a verdict: `NPU_ONLY` / `MIXED` / `CPU_HEAVY` / `UNKNOWN`, driven by the
    CPU fallback share of total layer time (thresholds in the report,
    `--cpu-heavy-pct` adjusts the CPU_HEAVY line);
  - optional output drift against the source ONNX (`--onnx`, only when
    onnxruntime is installed).
- Model reports are schema 1 JSON (`kind: "model"`) plus a single-file HTML
  report. For privacy the report records the model file name and SHA-256,
  never the full path.
- Without librknnrt, or with an unloadable/dynamic-shape model, the tool
  still writes a report with verdict `UNKNOWN` and the reason, and exits 0.
- Verified on a real RK3588 board (librknnrt 2.3.2, driver 0.9.8): a public
  YOLOv8n model measures 108 layers, zero non-structural CPU fallback,
  verdict `NPU_ONLY`.

## v0.1.1 (2026-11-01)

- Fix: boards whose BSP exposes the NPU only through DRM render nodes (no
  `/dev/rknpu*`) no longer fail the `npu_device` check when live driver
  evidence exists. Found on a real RK3588 board; regression fixture added.

## v0.1.0 (2026-11-01)

- `rkdoctor board`: 16 offline health checks for Rockchip boards with
  schema 1 JSON and single-file HTML reports. `model`/`rtsp` placeholders.
