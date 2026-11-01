# RK Edge Doctor

![RK Edge Doctor](assets/banner.png)

[![tests](https://github.com/anguoyang/rk-edge-doctor/actions/workflows/ci.yml/badge.svg)](https://github.com/anguoyang/rk-edge-doctor/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)

Offline health checks for Rockchip boards, starting with RK3588/RK3588S. Finding
missing NPU devices, unreadable drivers, memory pressure and overheating should
not require installing a diagnostic stack. `board` and (since v0.2) `model` —
RKNN model diagnosis with CPU-fallback detection — run on Python 3.9+ and the
standard library only.

From this repository, run one command without installation:

```sh
PYTHONPATH=src python3 -m rkdoctor board --json examples/report.json --html examples/report.html
```

No network requests, telemetry, registration or license checks. No proprietary
Rockchip binaries or libraries are bundled. Unknown versions stay `unknown`;
unavailable sources are skipped with a reason and an actionable hint.

```text
+ soc: {"model": "Rockchip RK3588 fixture board", "rockchip": true, "soc": "rk3588"}
+ npu_driver: 0.9.8
+ npu_load: {"core0": 0.0, "core1": 15.0, "core2": 100.0}
...
ATTENTION | Reports: examples/report.json, examples/report.html
```

> The abbreviated output above illustrates the RK3588 fixture on an x86
> development host. For a real board run, see the RK3588 sample reports under
> `examples/` (16/16 checks pass).

See the real development-host [JSON](examples/sample-report-x86.json) and
[HTML](examples/sample-report-x86.html), the
[RK3588 fixture HTML](examples/sample-report-rk3588-fixture.html), and a report
from a **real RK3588 board** (Ubuntu 22.04, kernel 6.1, rknpu driver 0.9.8,
16/16 checks pass): [JSON](examples/sample-report-rk3588-rk2.json) /
[HTML](examples/sample-report-rk3588-rk2.html).
[中文说明](README.zh-CN.md)

## Usage

```sh
PYTHONPATH=src python3 -m rkdoctor --version
PYTHONPATH=src python3 -m rkdoctor board
PYTHONPATH=src python3 -m rkdoctor board --root tests/fixtures/fake_rk3588 --html examples/fixture.html
PYTHONPATH=src python3 -m rkdoctor model model.rknn --json model-report.json --html model-report.html
PYTHONPATH=src python3 -m rkdoctor rtsp rtsp://user:pass@192.0.2.10:554/stream --duration 15 --json rtsp-report.json --html rtsp-report.html
```

An optional package installation exposes the `rkdoctor` console command:
`python3 -m pip install .` (requires a locally available setuptools build backend
for an offline installation). Running from source above needs no packages.

`board` exits 0 after a completed report, including a `CRITICAL` report; inspect
the JSON verdict for acceptance decisions. Report-writing errors exit 1, CLI
usage errors exit 2. `model` exits 0 whenever a report is generated — including
an `UNKNOWN` report when librknnrt or the model is unusable — and 2 for usage
errors such as a missing file. `rtsp` exits 0 whenever a report is generated —
including a `BROKEN` report when the stream cannot be opened — and 2 for usage
errors such as a non-rtsp URL. `--version` exits 0. Create output parent
directories first.

`--root DIR` redirects `/proc`, `/sys`, `/dev` and `/etc` reads. Runtime-library
searches in `/usr` and `/lib` are redirected too, and host `ldconfig` is disabled
for snapshots. Relative symlinks within the snapshot work; symlinks escaping
its root are skipped. Include their target files when preparing a snapshot.

`uname`, hostname and optional Python imports describe the running machine,
even with `--root`. Disk usage describes the filesystem containing DIR (the `/`
filesystem by default), not the original board's disk capacity. Consequently,
an RK3588 fixture run on x86 still warns about the x86 architecture. Unit tests
substitute these live values when testing deterministic board verdicts.

## Checks

| Stable ID | What is checked |
| --- | --- |
| `soc` | Device-tree model/compatible; non-Rockchip warns |
| `os` | OS release and running architecture; non-aarch64 warns |
| `kernel` | Running kernel release; recorded without judgment |
| `cpu` | Core count and current frequency by cluster |
| `memory` | Total/available memory; available <10% warns |
| `disk` | Free space; <10% warns, <5% fails |
| `npu_device` | `/dev/rknpu*` or rknpu driver evidence (some BSPs expose the NPU only via DRM render nodes); missing both on RK fails, on non-RK skips |
| `npu_driver` | Debugfs driver version; unavailable skips with sudo hint |
| `npu_load` | Per-core NPU utilization; unavailable skips |
| `npu_freq` | NPU frequency/governor; all governors are informational |
| `rknn_runtime` | `librknnrt.so` presence/version; missing warns |
| `thermal` | All thermal zones; >=75°C warns, >=85°C fails |
| `mpp` | `/dev/mpp_service`; missing warns for video workloads |
| `rga` | `/dev/rga`; missing warns for image/video workloads |
| `video_devices` | `/dev/video*` count; none skips |
| `python_env` | Running Python, `rknnlite`/`cv2` availability and versions |

See [check details and schema](docs/checks.md). Any failure yields `CRITICAL`;
otherwise any warning yields `ATTENTION`; otherwise `HEALTHY`. A non-Rockchip
host is identified by the `soc` warning; verdict strings are unchanged. Skips
mean incomplete evidence, even if the resulting verdict is `HEALTHY`.

JSON includes the tool version, timestamp with timezone, hostname, SoC, OS and
kernel. It does not include board serial numbers. HTML is a single file with
inline CSS and no JavaScript or external resources.

## RTSP acceptance checks (`rkdoctor rtsp`)

A pure-stdlib RTSP client (no ffmpeg/ffprobe needed) opens the stream over UDP
or TCP (`--transport tcp`), negotiates via SDP, and measures a window
(`--duration`, default 15s): reachability, negotiated codec, time to first
frame, measured frame rate (compare with `--expect-fps` / `--expect-width` /
`--expect-height`), RTP packet loss, inter-arrival jitter, bitrate, and RTP
timestamp health (regressions/gaps). When OpenCV is installed, an H.264 decode
spot-check also flags black or frozen pictures. Verdict:
`HEALTHY` / `DEGRADED` / `BROKEN`. **Passwords in the URL are always redacted**
in terminal output and in both reports. Reports are schema 1 JSON
(`kind: "rtsp"`) plus a single-file HTML report; see
[the sample report](examples/sample-report-rtsp-local-stream.json) captured
against a synthetic local stream on an RK3588 board. Digest and Basic auth are
supported.

## Development

With pytest already available:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
```

The same tests can run without development packages:

```sh
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Fixtures under `tests/fixtures/` are synthetic text trees; device-tree files
contain actual NUL separators. The `librknnrt.so` fixture is plain text, not a
Rockchip library. CI runs pytest on Python 3.9 and 3.12.

## Roadmap

- v0.1: board checks and offline reports.
- v0.2: model diagnosis — `rkdoctor model` runs the model on the NPU
  through the system librknnrt (no extra packages), lists operators that fall
  back to CPU, measures p50/p95 latency from the runtime's own counters, and
  gives a verdict: `NPU_ONLY / MIXED / CPU_HEAVY / UNKNOWN`. Optional `--onnx`
  compares outputs against the source ONNX model when onnxruntime is present.
- v0.3 (current): RTSP acceptance — `rkdoctor rtsp` opens a camera stream with
  a pure-stdlib client, measures frame rate / loss / jitter / first frame /
  timestamp health, optionally spot-checks decoding (black/frozen), and gives
  a verdict: `HEALTHY / DEGRADED / BROKEN`.
- v0.4 (planned): conversion trio around the official RKNN Toolkit —
  pre-conversion checks, recipe generation, post-conversion verification.

RK Edge Doctor is an open-source project associated with Site AI Gateway: https://site-ai-gateway.com/open-source/rk-edge-doctor/

Licensed under [Apache-2.0](LICENSE).
