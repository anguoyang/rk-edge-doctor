# Board checks — schema 1 / v0.1

Check IDs are permanent anchors. Each check returns exactly
`{id, title, status, value, detail, hint}`. `status` is `pass`, `warn`, `fail` or
`skip`; `hint` is null for pass and one actionable sentence otherwise. Values
retain structured measurements with explicit units. Missing measurements are
null, and unrecognized versions are `unknown`.

Sources are read without modification. Missing or inaccessible measurements
skip; explicit device/library absence follows the check-specific rules below.
Partially readable CPU/frequency/thermal data is retained with skip, except
that a known thermal warning/failure takes precedence over an unreadable zone.

## soc

Sources: `/proc/device-tree/model`, `/proc/device-tree/compatible`. The latter
is a real NUL-separated list. Rockchip identification requires `rockchip` in
these sources; explicit `rkNNNN` identifiers retain suffixes such as `rk3588s`.
The compatible list is preferred for the SoC name. No specific SoC is guessed
when its identifier is unavailable.

Value: `{soc, rockchip, model}`. A Rockchip board passes; a readable non-Rockchip
tree warns. On x86, missing device tree yields a non-Rockchip warning using the
running architecture. If neither tree data nor architecture establishes board
identity, skip with `rockchip: null`. Retry on the board with readable device-tree
files. Model/compatible are not used to extract a board serial number.

## os

Sources: `/etc/os-release` and running `uname -m` via Python's platform module.
Value: `{distribution, architecture}`. Prefer PRETTY_NAME, then NAME. Any known
architecture other than `aarch64` warns. Missing distribution or architecture
skips unless a known architecture already warrants warn. Use an aarch64 OS and
check os-release permissions. `--root` redirects only the distribution source.

## kernel

Source: running `uname -r` via the platform module. The value is the kernel
release string. Any readable release passes without version judgment because
vendor kernels vary. An unavailable release skips. `--root` does not change it.

## cpu

Sources: `/proc/cpuinfo` and `/sys/devices/system/cpu/cpu*/cpufreq/`.
Count `processor` records; group `scaling_cur_freq` by `related_cpus`, using
per-CPU labels if cluster membership is absent. Value: `{cores, clusters}`;
each cluster has `cpus` and `current_khz`. Missing counts or frequencies skip
with retained partial data. No frequency threshold is imposed. Verify cpuinfo
and CPU frequency sysfs support/read permissions.

## memory

Source: `/proc/meminfo`, fields MemTotal and MemAvailable, in KiB. Value:
`{total_kib, available_kib, available_percent}`. Available **<10%** warns;
exactly 10% passes. Invalid, missing or inconsistent fields skip. No invented
MemFree fallback. Stop unused workloads when memory is low.

## disk

Source: `shutil.disk_usage('/')`; with `--root`, query the filesystem containing
DIR. Value: `{total_bytes, free_bytes, free_percent}`. Free **<5%** fails;
otherwise **<10%** warns. Exactly 5% warns; exactly 10% passes. Failed queries
skip. Free space on the measured filesystem. A filesystem snapshot contains no
original disk-capacity measurement; this check measures the execution host.

## npu_device

Source: `/dev/rknpu*`. Value: matching paths, or null when skipped. A detected
device passes. Some BSPs (e.g. kernel 6.1 with rknpu driver 0.9.8) expose no
`/dev/rknpu*` node at all — the NPU is reached via DRM render nodes
(`/dev/dri/renderD*`) while the driver still reports version/load via debugfs
and devfreq; in that case live driver evidence (readable
`/sys/kernel/debug/rknpu/version` or an NPU devfreq node) also passes, with
value `{dev_nodes: [], render_nodes: [...], access: "drm_render/debugfs"}`.
Explicit absence of both device nodes and driver evidence on a known RK board
fails; absence on a known non-RK host skips. Inaccessible enumeration or
unknown SoC skips. Enable the board's rknpu kernel driver and verify device
creation when required.

## npu_driver

Source: `/sys/kernel/debug/rknpu/version`. A readable source passes and records
its version; unrecognized formats give `unknown`. Missing, empty or unreadable
source skips with the hint “用 sudo 重跑可读 / Rerun with sudo to read debugfs.”
The tool does not mount debugfs or change permissions itself.

## npu_load

Source: `/sys/kernel/debug/rknpu/load`. Parse `Core0: 0%, Core1: 15%, ...` into
`{core0: 0.0, core1: 15.0, ...}`. Also accept the single-core `NPU load: 25%`
format. Percentages must be between 0 and 100. Readable valid data passes;
missing/unreadable data skips with the sudo hint above; unrecognized data skips
with a format-inspection hint. No utilization threshold is imposed.

## npu_freq

Sources: `/sys/class/devfreq/*npu*/cur_freq` and `governor`. Value: a list of
`{device, current_hz, governor}`. Readable values pass. Governors including
`simple_ondemand` and `powersave` are recorded without warnings. Missing or
invalid values skip with partial data retained; check sysfs read permissions.

## rknn_runtime

Sources: `ldconfig -p` on a live host, plus common paths under `/usr/lib`,
`/usr/local/lib`, `/lib` (including one architecture subdirectory), and
`/usr/lib64`. Look for `librknnrt.so` and versioned filenames. With `--root`, all
library paths redirect and live ldconfig is disabled.

Value: `{present, version, paths}` when present, otherwise `{present, version}`.
Presence passes. Scan at most 16 MiB per library for explicit `librknnrt version`
or `RKNN [Runtime] version` markers. Do not load the library or infer its version
from filenames or unrelated strings; unavailable versions are `unknown`.
Absence warns because static C API deployment or no installed runtime may be
intentional. Inaccessible search paths skip. If dynamic RKNN is needed, install
the appropriate runtime separately from the board vendor.

## thermal

Sources: `/sys/class/thermal/thermal_zone*/{type,temp}`. Convert integer
millidegrees Celsius to °C. Value: a list of `{zone, type, temperature_c}`.
Any zone **>=85°C** fails; otherwise any zone **>=75°C** warns. Cooler readable
zones pass. Missing or malformed data skips unless a known zone already
warrants warn/fail. Improve cooling/reduce workload and check sysfs access.

## mpp

Source: `/dev/mpp_service`. Value: `{present}`. Presence passes, explicit absence
warns, inaccessible metadata skips. MPP is only needed for relevant video
workloads; enable the board's MPP driver if required. No codec operation runs.

## rga

Source: `/dev/rga`. Value: `{present}`. Presence passes, absence warns,
inaccessible metadata skips. Enable RGA if your image/video workload needs it.
No acceleration benchmark runs.

## video_devices

Source: `/dev/video*`. Value: `{count, paths}` if found; `{count: 0}` if absent,
or `{count: null}` if enumeration fails. At least one device passes; none or
unreadable sources skip. Connect a capture device and check its driver if needed.
Presence does not prove capture capability or successful streaming.

## python_env

Sources: running `sys.version`, attempts to import `rknnlite` and `cv2` in child
processes using the same interpreter. Value: `{python, modules}`; each module
reports `present`, `version`, and a failure reason where relevant. Missing
optional modules are informational and pass. Failed imports, missing transitive
dependencies, crashes or timeouts skip with `present: null`. Import attempts have
a five-second timeout and do not write Python bytecode. Unavailable
`__version__` is `unknown`. This always probes the execution environment, not
Python installations in a snapshot.

## Report contract

```text
{
  schema: 1,
  tool_version: string,
  generated_at: ISO8601 string with timezone,
  host: {hostname, soc, os, kernel},
  summary: {pass, warn, fail, skip},
  verdict: CRITICAL | ATTENTION | HEALTHY,
  checks: [{id, title, status, value, detail, hint}, ...]
}
```

Counters are integers. A fail takes precedence over warn; skips do not change
the verdict. Non-Rockchip is expressed by the soc warning, without adding text
to the verdict enum. Threshold decisions use unrounded values; displayed
percentages are rounded to two decimal places. Hostname is the execution host's
hostname, including snapshot runs. No serial-number fields are collected.

Terminal output has one symbol + ID + value line per check (`+` pass, `!` warn,
`X` fail, `-` skip), then the verdict and output paths. JSON retains reasons and
hints; HTML displays them in the check table below summary cards. HTML escapes
all source data and has inline CSS, no JS dependency and no external resources.
Its fixed footer is:

```text
Generated by RK Edge Doctor · https://site-ai-gateway.com/open-source/rk-edge-doctor/
```

Reports record observations, not an inference workload or performance benchmark.
`board` returns 0 after writing requested reports, even with CRITICAL; callers
should inspect verdict. Output failure is 1, usage error is 2, planned
model/rtsp commands return 3.
