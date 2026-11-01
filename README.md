# RK Edge Doctor

Offline health checks for Rockchip boards, starting with RK3588/RK3588S. Finding
missing NPU devices, unreadable drivers, memory pressure and overheating should
not require installing a diagnostic stack. v0.1 implements `board` with Python
3.9+ and the standard library only.

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

> Screenshot placeholder: terminal summary and standalone HTML report.
> This abbreviated output illustrates the RK3588 fixture on an x86 development host.

See the real development-host [JSON](examples/sample-report-x86.json) and
[HTML](examples/sample-report-x86.html), and the
[RK3588 fixture HTML](examples/sample-report-rk3588-fixture.html).
[中文说明](README.zh-CN.md)

## Usage

```sh
PYTHONPATH=src python3 -m rkdoctor --version
PYTHONPATH=src python3 -m rkdoctor board
PYTHONPATH=src python3 -m rkdoctor board --root tests/fixtures/fake_rk3588 --html examples/fixture.html
PYTHONPATH=src python3 -m rkdoctor model
PYTHONPATH=src python3 -m rkdoctor rtsp
```

An optional package installation exposes the `rkdoctor` console command:
`python3 -m pip install .` (requires a locally available setuptools build backend
for an offline installation). Running from source above needs no packages.

`board` exits 0 after a completed report, including a `CRITICAL` report; inspect
the JSON verdict for acceptance decisions. Report-writing errors exit 1, CLI
usage errors exit 2. `model` and `rtsp` print one English and one Chinese planned
line and exit 3. `--version` exits 0. Create output parent directories first.

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
| `npu_device` | `/dev/rknpu*`; missing on RK fails, on non-RK skips |
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
- v0.2: model checks, CPU fallback operators, p50/p95, ONNX/RKNN differences;
  reserved verdicts: `NPU_ONLY / MIXED / CPU_HEAVY / UNKNOWN`.
- v0.3: RTSP connection, actual codec/resolution/frame rate, frame interval
  jitter, freezes, reconnects and snapshots.

RK Edge Doctor is an open-source project associated with Site AI Gateway: https://site-ai-gateway.com/open-source/rk-edge-doctor/

Licensed under [Apache-2.0](LICENSE).
