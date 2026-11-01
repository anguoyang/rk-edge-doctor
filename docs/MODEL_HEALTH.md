# RKNN model health table

Measured with `rkdoctor model` — the runtime's own PERF_DETAIL accounting,
not wall-clock timing. If you have a model that comes out `MIXED` or
`CPU_HEAVY`, please share the report in an issue: those are the interesting
cases, and they are what this table is for.

## Method

- Board: RK3588 (Ubuntu 22.04, kernel 6.1.118), rknpu driver 0.9.8,
  librknnrt 2.3.2
- 20 measured runs after 3 warmups; latency from PERF_RUN hardware counters
- The board was busy during measurement (load average ≈ 16); hardware
  counters are used precisely so the numbers stay meaningful under load
- Models: public RKNN Model Zoo conversions, FP, RK3588 targets

## Results (2026-11-02)

| Model | Task | Verdict | p50 (ms) | p95 (ms) | CPU fallback ops | Fallback share |
| --- | --- | --- | --- | --- | --- | --- |
| YOLOv5s (ReLU) | detect | NPU_ONLY | 37.8 | 43.5 | 0 | 0.0% |
| YOLOv8n | detect | NPU_ONLY | 41.1 | 41.6 | 0 | 0.0% |
| YOLO11n | detect | NPU_ONLY | 52.9 | 53.4 | 0 | 0.0% |
| YOLOv10s | detect | NPU_ONLY | 96.7 | 99.2 | 0 | 0.0% |
| YOLOv8s-seg | segment | NPU_ONLY | 106.4 | 107.8 | 0 | 0.0% |

Notes:

- The official zoo conversions are clean — as expected. `rkdoctor model`
  earns its keep on *your own* conversions: custom backbones, unusual
  operators, aggressive quantization.
- For YOLOv10s and YOLO11n the per-layer PERF_DETAIL total runs ~10–20%
  below the PERF_RUN total (some time is not itemized per layer); the
  fallback verdict is unaffected because it is computed from the itemized
  layers, and latency above is the PERF_RUN figure.
- Input/output staging operators always execute on CPU; they are reported
  separately and never counted as fallback.

## Adding a row

Run `rkdoctor model your.rknn --json report.json` and open an issue with
the JSON attached (it contains the model file name and SHA-256 only, never
the path). Rows for other boards (RK3576, RK3566/68, RV1126) are welcome.
