# 拟替换 Edge Doctor repo/docs/MODEL_HEALTH.md 的新内容
# （rk-bench v0.1 发布后，在 Edge Doctor 仓单独提交，提交说明注明数据真源迁移）

# RKNN model health table — moved

This table now lives in **[rk-bench](https://github.com/anguoyang/rk-bench)**,
the measured RKNN leaderboard for Rockchip boards, which is the single
source of truth for this data from 2026-11-02 on.

Why the move: RK Edge Doctor diagnoses *one* model in depth; rk-bench is
the cross-model leaderboard (latency p50/p95, CPU-fallback verdict,
memory) under one frozen protocol — same measurement method, same
thresholds, numbers directly comparable with `rkdoctor model` reports.

The five models first measured here (YOLOv5s-ReLU, YOLOv8n, YOLO11n,
YOLOv10s, YOLOv8s-seg on RK3588) were re-measured under the rk-bench
protocol (priming + 3 sessions, median p50/p95) and are published there
with the full table. Two protocol notes explain the small differences
from the numbers originally published on this page:

- rkdoctor's single-process runs include a first-context effect: on
  RK3588 (rknpu_ondemand) the first RKNN process after idle measures
  ~15% slower. rk-bench primes before measuring.
- rk-bench reports the median of three independent sessions instead of
  one session, so transient slow sessions on a busy board do not leak
  into the table.

To submit a model for the table, run `rkbench run` and open a
bench-report issue in the rk-bench repository.
