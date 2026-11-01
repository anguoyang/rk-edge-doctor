"""The sixteen v0.1 board checks; no writes, network requests or inference."""

import re
import sys
from typing import Any, Optional

from ..probe import Probe


def result(check_id: str, title: str, status: str, value: Any,
           detail: str = "", hint: Optional[str] = None) -> dict:
    return {"id": check_id, "title": title, "status": status, "value": value,
            "detail": detail, "hint": hint if status != "pass" else None}


def skipped(check_id: str, title: str, detail: str,
            hint: str = "Check that the source exists and is readable.") -> dict:
    return result(check_id, title, "skip", None, detail, hint)


def check_soc(probe: Probe) -> dict:
    model = probe.read("/proc/device-tree/model")
    compatible = probe.read("/proc/device-tree/compatible")
    text = ((compatible or "") + "\x00" + (model or "")).lower()
    rockchip = "rockchip" in text
    matches = re.findall(r"\brk\d{4}[a-z]*\b", text) if rockchip else []
    soc = next(iter(matches), "unknown")
    value = {"soc": soc, "rockchip": rockchip, "model": model or "unknown"}
    if model is None and compatible is None:
        architecture = probe.system("machine")
        if architecture in ("x86_64", "amd64", "i386", "i686"):
            return result("soc", "SoC", "warn", value,
                          "Device tree unavailable; non-Rockchip x86 host.",
                          "Run on a Rockchip board for board acceptance.")
        value["rockchip"] = None
        return result("soc", "SoC", "skip", value,
                      "Device-tree model and compatible are missing or unreadable.",
                      "Provide readable /proc/device-tree model and compatible files.")
    return result("soc", "SoC", "pass" if rockchip else "warn", value,
                  "Read device-tree model and NUL-separated compatible.",
                  "Run on a Rockchip board for board acceptance.")


def check_os(probe: Probe) -> dict:
    content = probe.read("/etc/os-release")
    fields = {}
    for line in (content or "").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            fields[key] = value.strip().strip('\"\'')
    distro = fields.get("PRETTY_NAME") or fields.get("NAME") or "unknown"
    arch = probe.system("machine") or "unknown"
    status = "warn" if arch not in ("aarch64", "unknown") else "pass"
    if status == "pass" and (distro == "unknown" or arch == "unknown"):
        status = "skip"
    return result("os", "Operating system", status,
                  {"distribution": distro, "architecture": arch},
                  "Distribution from /etc/os-release; architecture from running uname -m.",
                  "Use an aarch64 OS and check that /etc/os-release is readable.")


def check_kernel(probe: Probe) -> dict:
    version = probe.system("release")
    if version is None:
        return skipped("kernel", "Kernel", "uname -r unavailable.")
    return result("kernel", "Kernel", "pass", version,
                  "Running uname -r; recorded without version judgment.")


def check_cpu(probe: Probe) -> dict:
    content = probe.read("/proc/cpuinfo")
    cores = len(re.findall(r"^processor\s*:", content or "", re.MULTILINE))
    paths = probe.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq")
    clusters = {}
    incomplete = not cores or not paths
    for path in paths or []:
        members = probe.read(path + "/related_cpus")
        frequency = probe.read(path + "/scaling_cur_freq")
        key = members or path.split("/")[-2]
        khz = int(frequency) if frequency and frequency.isdigit() else None
        clusters[key] = {"cpus": key, "current_khz": khz}
        incomplete = incomplete or khz is None
    return result("cpu", "CPU", "skip" if incomplete else "pass",
                  {"cores": cores or None, "clusters": list(clusters.values())},
                  "Core count from cpuinfo; cpufreq grouped by related_cpus; "
                  "missing/unreadable frequencies are null.",
                  "Check /proc/cpuinfo and enable readable CPU frequency sysfs entries.")


def check_memory(probe: Probe) -> dict:
    content = probe.read("/proc/meminfo")
    fields = dict(re.findall(r"^(MemTotal|MemAvailable):\s+(\d+)\s+kB\s*$",
                             content or "", re.MULTILINE))
    total, available = int(fields.get("MemTotal", 0)), int(fields.get("MemAvailable", -1))
    if total <= 0 or not 0 <= available <= total:
        return skipped("memory", "Memory", "MemTotal/MemAvailable missing or invalid.")
    percent = available / total * 100
    return result("memory", "Memory", "warn" if percent < 10 else "pass",
                  {"total_kib": total, "available_kib": available,
                   "available_percent": round(percent, 2)},
                  "Warn when available memory is below 10%.",
                  "Stop unused workloads to free memory.")


def check_disk(probe: Probe) -> dict:
    usage = probe.disk_usage()
    if usage is None or usage.total <= 0:
        return skipped("disk", "Disk", "Filesystem usage unavailable.")
    percent = usage.free / usage.total * 100
    status = "fail" if percent < 5 else "warn" if percent < 10 else "pass"
    return result("disk", "Disk", status,
                  {"total_bytes": usage.total, "free_bytes": usage.free,
                   "free_percent": round(percent, 2)},
                  "Usage of /, or the filesystem containing --root; not snapshot disk capacity.",
                  "Free disk space on the reported filesystem.")


def check_npu_device(probe: Probe, rockchip: Optional[bool]) -> dict:
    paths = probe.glob("/dev/rknpu*")
    if paths:
        return result("npu_device", "NPU device", "pass", paths)
    if paths is None or rockchip is None:
        return skipped("npu_device", "NPU device", "Device access or SoC identification unavailable.")
    if not rockchip:
        return skipped("npu_device", "NPU device", "No NPU device on non-Rockchip host.",
                       "Run on a Rockchip board with the rknpu driver enabled.")
    return result("npu_device", "NPU device", "fail", [], "No /dev/rknpu* device found.",
                  "Enable the board's rknpu kernel driver and check device creation.")


def check_npu_driver(probe: Probe) -> dict:
    version = probe.read("/sys/kernel/debug/rknpu/version")
    if not version:
        return skipped("npu_driver", "NPU driver", "Debugfs version missing or unreadable.",
                       "用 sudo 重跑可读 / Rerun with sudo to read debugfs.")
    match = re.search(r"(?<![\w.])v?(\d+\.\d+(?:\.\d+)*(?:[-+][\w.]+)?)", version)
    return result("npu_driver", "NPU driver", "pass",
                  match.group(1) if match else "unknown",
                  "Version from /sys/kernel/debug/rknpu/version.")


def check_npu_load(probe: Probe) -> dict:
    content = probe.read("/sys/kernel/debug/rknpu/load")
    if content is None:
        return skipped("npu_load", "NPU load", "Debugfs load missing or unreadable.",
                       "用 sudo 重跑可读 / Rerun with sudo to read debugfs.")
    matches = re.findall(r"Core\s*(\d+)\s*:\s*([+-]?\d+(?:\.\d+)?)\s*%", content, re.I)
    core_count = len(re.findall(r"Core\s*\d+\s*:", content, re.I))
    loads = {"core" + key: float(value) for key, value in matches}
    if not loads:
        single = re.fullmatch(r"\s*(?:NPU\s+load\s*:\s*)?(\d+(?:\.\d+)?)\s*%\s*", content, re.I)
        loads = {"core0": float(single.group(1))} if single else {}
    if not loads or (core_count and core_count != len(loads)) or any(not 0 <= value <= 100 for value in loads.values()):
        return skipped("npu_load", "NPU load", "Unrecognized or invalid NPU load format.",
                       "Inspect /sys/kernel/debug/rknpu/load for valid per-core percentages.")
    return result("npu_load", "NPU load", "pass", loads, "Per-core utilization in percent.")


def check_npu_freq(probe: Probe) -> dict:
    paths = probe.glob("/sys/class/devfreq/*npu*")
    if not paths:
        return skipped("npu_freq", "NPU frequency", "NPU devfreq entries missing or unreadable.")
    values, incomplete = [], False
    for path in paths:
        frequency, governor = probe.read(path + "/cur_freq"), probe.read(path + "/governor")
        hz = int(frequency) if frequency and frequency.isdigit() else None
        values.append({"device": path.rsplit("/", 1)[-1], "current_hz": hz,
                       "governor": governor or "unknown"})
        incomplete = incomplete or hz is None or not governor
    return result("npu_freq", "NPU frequency", "skip" if incomplete else "pass", values,
                  "Governor is recorded without judgment; missing frequency/governor is skipped.",
                  "Check read permissions for NPU devfreq cur_freq and governor.")


def check_rknn_runtime(probe: Probe) -> dict:
    candidates, inaccessible = set(), False
    if probe.live:
        cache = probe.command(["ldconfig", "-p"]) or ""
        candidates.update(re.findall(r"librknnrt\.so[^\s]*[^\n]*=>\s*(/\S+)", cache))
    for pattern in ("/usr/lib/librknnrt.so*", "/usr/lib/*/librknnrt.so*",
                    "/usr/local/lib/librknnrt.so*", "/usr/local/lib/*/librknnrt.so*",
                    "/lib/librknnrt.so*", "/lib/*/librknnrt.so*", "/usr/lib64/librknnrt.so*"):
        paths = probe.glob(pattern)
        inaccessible = inaccessible or paths is None
        candidates.update(paths or [])
    paths = sorted(path for path in candidates if probe.exists(path))
    if not paths:
        if inaccessible:
            return skipped("rknn_runtime", "RKNN runtime", "Runtime library paths are unreadable.")
        return result("rknn_runtime", "RKNN runtime", "warn", {"present": False, "version": "unknown"},
                      "librknnrt.so not found; a static C API deployment may be intentional.",
                      "Install the board vendor's RKNN runtime if dynamic RKNN execution is needed.")
    version = "unknown"
    for path in paths:
        data = probe.read_bytes(path, limit=16 * 1024 * 1024) or b""
        match = re.search(rb"(?:librknnrt|RKNN(?:\s+Runtime)?)\s+(?:version\s*[:=]?\s*)v?(\d+\.\d+(?:\.\d+)*)", data, re.I)
        if match:
            version = match.group(1).decode("ascii")
            break
    return result("rknn_runtime", "RKNN runtime", "pass",
                  {"present": True, "version": version, "paths": paths},
                  "Read explicit runtime version markers only; unknown when unavailable.")


def check_thermal(probe: Probe) -> dict:
    paths = probe.glob("/sys/class/thermal/thermal_zone*")
    if not paths:
        return skipped("thermal", "Thermal zones", "Thermal zones missing or unreadable.")
    zones, incomplete = [], False
    for path in paths:
        kind, raw = probe.read(path + "/type"), probe.read(path + "/temp")
        valid = bool(raw and re.fullmatch(r"-?\d+", raw))
        temperature = int(raw) / 1000 if valid else None
        zones.append({"zone": path.rsplit("/", 1)[-1], "type": kind or "unknown",
                      "temperature_c": temperature})
        incomplete = incomplete or temperature is None or not kind
    peak = max((zone["temperature_c"] for zone in zones if zone["temperature_c"] is not None), default=None)
    status = "fail" if peak is not None and peak >= 85 else "warn" if peak is not None and peak >= 75 else "skip" if incomplete else "pass"
    return result("thermal", "Thermal zones", status, zones,
                  "Warn at >=75 C; fail at >=85 C." + (" Some zone data is missing/unreadable." if incomplete else ""),
                  "Check cooling and reduce load; verify thermal sysfs access.")


def check_mpp(probe: Probe) -> dict:
    return device_check(probe, "mpp", "MPP video acceleration", "/dev/mpp_service")


def check_rga(probe: Probe) -> dict:
    return device_check(probe, "rga", "RGA acceleration", "/dev/rga")


def device_check(probe: Probe, check_id: str, title: str, path: str) -> dict:
    present = probe.exists(path)
    if present is None:
        return skipped(check_id, title, path + " is unreadable.")
    return result(check_id, title, "pass" if present else "warn", {"present": present},
                  path + "; needed only for relevant video/image workloads.",
                  "Enable the board's " + check_id + " driver if your workload needs it.")


def check_video_devices(probe: Probe) -> dict:
    paths = probe.glob("/dev/video*")
    if not paths:
        return result("video_devices", "Video devices", "skip", {"count": 0 if paths == [] else None},
                      "No video devices found or device directory unreadable.",
                      "Connect a video capture device and check its kernel driver if needed.")
    return result("video_devices", "Video devices", "pass", {"count": len(paths), "paths": paths})


def check_python_env(probe: Probe) -> dict:
    modules = {name: probe.python_module(name) for name in ("rknnlite", "cv2")}
    failed = any(value["present"] is None for value in modules.values())
    return result("python_env", "Python environment", "skip" if failed else "pass",
                  {"python": sys.version.split()[0], "modules": modules},
                  "Running Python environment, including with --root; optional absence is informational.",
                  "Check optional module dependencies and retry their imports with the running Python.")


CHECKS = (check_soc, check_os, check_kernel, check_cpu, check_memory, check_disk,
          check_npu_device, check_npu_driver, check_npu_load, check_npu_freq,
          check_rknn_runtime, check_thermal, check_mpp, check_rga,
          check_video_devices, check_python_env)


def run_checks(probe: Probe) -> list:
    checks = []
    rockchip = None
    for check in CHECKS:
        try:
            item = check(probe, rockchip) if check is check_npu_device else check(probe)
        except Exception:
            # Last boundary: a new driver format or failed probe must not abort a report.
            check_id = check.__name__.removeprefix("check_")
            item = skipped(check_id, check_id.replace("_", " ").title(),
                           "Probe failed; source unavailable or unrecognized.")
        checks.append(item)
        if item["id"] == "soc" and isinstance(item["value"], dict):
            rockchip = item["value"].get("rockchip")
    return checks
