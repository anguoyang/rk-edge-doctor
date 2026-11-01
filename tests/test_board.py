"""Offline tests: pytest collection and a standard-library unittest fallback."""

import ast
from collections import namedtuple
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from rkdoctor.checks import board
from rkdoctor.cli import main
from rkdoctor.probe import Probe
from rkdoctor.report import FOOTER, build_report, render_html, terminal_report

FIXTURES = Path(__file__).parent / "fixtures"
REPO = Path(__file__).resolve().parents[1]
IDS = ["soc", "os", "kernel", "cpu", "memory", "disk", "npu_device",
       "npu_driver", "npu_load", "npu_freq", "rknn_runtime", "thermal",
       "mpp", "rga", "video_devices", "python_env"]
Usage = namedtuple("Usage", "total used free")


class FixtureTests(unittest.TestCase):
    def fixture_report(self, name, arch="aarch64"):
        probe = Probe(str(FIXTURES / name))
        values = {"machine": arch, "release": "6.1.0-fixture", "node": "test-host"}
        with patch.object(probe, "system", side_effect=values.get), \
                patch.object(probe, "disk_usage", return_value=Usage(1000, 500, 500)), \
                patch.object(probe, "python_module", return_value={"present": False, "version": "unknown"}):
            return build_report(board.run_checks(probe), probe)

    def test_complete_rk3588(self):
        report = self.fixture_report("fake_rk3588")
        checks = {item["id"]: item for item in report["checks"]}
        self.assertEqual(checks["soc"]["value"]["soc"], "rk3588")
        for check_id in IDS:
            self.assertEqual(checks[check_id]["status"], "pass", check_id)
        self.assertEqual(checks["npu_driver"]["value"], "0.9.8")
        self.assertEqual(checks["npu_load"]["value"], {"core0": 0.0, "core1": 15.0, "core2": 100.0})
        self.assertEqual(checks["rknn_runtime"]["value"]["version"], "2.3.0")
        self.assertEqual(len(checks["cpu"]["value"]["clusters"]), 3)
        self.assertEqual(report["verdict"], "HEALTHY")

    def test_x86(self):
        report = self.fixture_report("fake_x86", "x86_64")
        checks = {item["id"]: item for item in report["checks"]}
        self.assertEqual(checks["soc"]["status"], "warn")
        self.assertEqual(checks["os"]["status"], "warn")
        self.assertEqual(checks["npu_device"]["status"], "skip")
        self.assertEqual(report["verdict"], "ATTENTION")

    def test_rk_without_driver(self):
        report = self.fixture_report("fake_rk_no_driver")
        checks = {item["id"]: item for item in report["checks"]}
        self.assertEqual(checks["soc"]["status"], "pass")
        self.assertEqual(checks["npu_device"]["status"], "fail")
        for check_id in ("npu_driver", "npu_load", "npu_freq"):
            self.assertEqual(checks[check_id]["status"], "skip")
        self.assertEqual(report["verdict"], "CRITICAL")

    def test_rk_drm_only_no_dev_rknpu(self):
        # Real-board regression (RK3588, kernel 6.1, driver 0.9.8): this BSP
        # has no /dev/rknpu* node; the NPU is reached via DRM render nodes.
        # Driver evidence must count as device presence, not a false fail.
        report = self.fixture_report("fake_rk_drm_only")
        checks = {item["id"]: item for item in report["checks"]}
        self.assertEqual(checks["npu_device"]["status"], "pass")
        self.assertEqual(checks["npu_driver"]["status"], "pass")
        self.assertNotEqual(report["verdict"], "CRITICAL")

    def test_schema_and_summary(self):
        report = self.fixture_report("fake_rk3588")
        self.assertEqual(set(report), {"schema", "tool_version", "generated_at", "host", "summary", "verdict", "checks"})
        self.assertEqual(report["schema"], 1)
        self.assertIsNotNone(datetime.fromisoformat(report["generated_at"]).tzinfo)
        self.assertEqual(set(report["host"]), {"hostname", "soc", "os", "kernel"})
        self.assertEqual([item["id"] for item in report["checks"]], IDS)
        self.assertEqual(set(report["summary"]), {"pass", "warn", "fail", "skip"})
        self.assertEqual(sum(report["summary"].values()), 16)
        for item in report["checks"]:
            self.assertEqual(set(item), {"id", "title", "status", "value", "detail", "hint"})
            self.assertIn(item["status"], report["summary"])
            self.assertIsNone(item["hint"])
        self.assertEqual(json.loads(json.dumps(report)), report)

    def test_verdict_precedence_and_hints(self):
        for name, arch in (("fake_x86", "x86_64"), ("fake_rk_no_driver", "aarch64")):
            report = self.fixture_report(name, arch)
            for item in report["checks"]:
                if item["status"] != "pass":
                    self.assertTrue(item["hint"], item["id"])
            expected = "CRITICAL" if report["summary"]["fail"] else "ATTENTION"
            self.assertEqual(report["verdict"], expected)

    def test_html_is_standalone_and_escapes_sources(self):
        report = self.fixture_report("fake_rk3588")
        report["checks"][0]["value"]["model"] = '<script>alert("test")</script>'
        report["host"]["hostname"] = "<host>"
        html = render_html(report)
        self.assertIn(FOOTER, html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;host&gt;", html)
        self.assertNotIn("<script", html)
        self.assertNotIn("<link", html)
        self.assertNotIn("src=", html)
        self.assertIn("<style>", html)
        self.assertEqual(html.count('<div class="card '), 4)

    def test_terminal_lines_and_paths(self):
        report = self.fixture_report("fake_x86", "x86_64")
        output = terminal_report(report, ["a.json", "b.html"]).splitlines()
        self.assertEqual(len(output), 17)
        self.assertTrue(output[0].startswith("! soc:"))
        self.assertEqual(output[-1], "ATTENTION | Reports: a.json, b.html")

    def test_device_tree_uses_real_nuls(self):
        for name in ("fake_rk3588", "fake_rk_no_driver"):
            raw = (FIXTURES / name / "proc/device-tree/compatible").read_bytes()
            self.assertEqual(raw.split(b"\x00"), [b"fixture,rk3588-board", b"rockchip,rk3588", b""])


class ProbeAndThresholdTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=REPO / "tests")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(FIXTURES / "fake_rk3588", self.root, dirs_exist_ok=True)
        self.probe = Probe(str(self.root))

    def write(self, path, content):
        target = self.root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def test_memory_thresholds(self):
        for available, expected in ((101, "pass"), (100, "pass"), (99, "warn"), (0, "warn")):
            with self.subTest(available=available):
                self.write("/proc/meminfo", f"MemTotal: 1000 kB\nMemAvailable: {available} kB\n")
                self.assertEqual(board.check_memory(self.probe)["status"], expected)

    def test_disk_thresholds(self):
        for free, expected in ((100, "pass"), (99, "warn"), (50, "warn"), (49, "fail"), (0, "fail")):
            with self.subTest(free=free), patch.object(self.probe, "disk_usage", return_value=Usage(1000, 1000-free, free)):
                self.assertEqual(board.check_disk(self.probe)["status"], expected)

    def test_thermal_thresholds_and_any_zone(self):
        self.write("/sys/class/thermal/thermal_zone1/type", "gpu-thermal")
        for temperature, expected in ((74999, "pass"), (75000, "warn"), (84999, "warn"), (85000, "fail")):
            with self.subTest(temperature=temperature):
                self.write("/sys/class/thermal/thermal_zone1/temp", str(temperature))
                self.assertEqual(board.check_thermal(self.probe)["status"], expected)

    def test_partial_thermal_read_preserves_failure(self):
        self.write("/sys/class/thermal/thermal_zone0/temp", "85000")
        self.write("/sys/class/thermal/thermal_zone1/type", "unreadable-zone")
        self.assertEqual(board.check_thermal(self.probe)["status"], "fail")
        self.write("/sys/class/thermal/thermal_zone0/temp", "42000")
        self.assertEqual(board.check_thermal(self.probe)["status"], "skip")

    def test_governor_is_informational(self):
        for governor in ("performance", "powersave", "simple_ondemand"):
            self.write("/sys/class/devfreq/fdab0000.npu/governor", governor)
            self.assertEqual(board.check_npu_freq(self.probe)["status"], "pass")

    def test_soc_variants_and_unknown(self):
        self.write("/proc/device-tree/model", "Example board\x00")
        for soc in ("rk3588", "rk3588s", "rk3576", "rk3566", "rk3568"):
            self.write("/proc/device-tree/compatible", "vendor,board\x00rockchip," + soc + "\x00")
            self.assertEqual(board.check_soc(self.probe)["value"]["soc"], soc)
        self.write("/proc/device-tree/compatible", "rockchip,custom-board\x00")
        self.assertEqual(board.check_soc(self.probe)["value"]["soc"], "unknown")

    def test_non_rockchip_tree(self):
        self.write("/proc/device-tree/model", "Other ARM board")
        self.write("/proc/device-tree/compatible", "vendor,board\x00")
        self.assertEqual(board.check_soc(self.probe)["status"], "warn")

    def test_unknown_versions_are_not_guessed(self):
        self.write("/sys/kernel/debug/rknpu/version", "custom driver")
        self.write("/usr/lib/librknnrt.so", "unrelated compiler 12.3.0")
        self.assertEqual(board.check_npu_driver(self.probe)["value"], "unknown")
        self.assertEqual(board.check_rknn_runtime(self.probe)["value"]["version"], "unknown")

    def test_bad_source_data_skips(self):
        cases = [("/proc/meminfo", board.check_memory),
                 ("/sys/kernel/debug/rknpu/load", board.check_npu_load),
                 ("/sys/class/devfreq/fdab0000.npu/cur_freq", board.check_npu_freq),
                 ("/sys/class/thermal/thermal_zone0/temp", board.check_thermal)]
        for path, check in cases:
            with self.subTest(path=path):
                self.write(path, "malformed")
                self.assertEqual(check(self.probe)["status"], "skip")

    def test_load_formats_and_range(self):
        cases = (("NPU load: 25%", "pass"), ("Core0: 101%", "skip"),
                 ("Core0: -1%", "skip"), ("Core0: invalid, Core1: 25%", "skip"),
                 ("Core0: -1%, Core1: 25%", "skip"))
        for content, expected in cases:
            self.write("/sys/kernel/debug/rknpu/load", content)
            self.assertEqual(board.check_npu_load(self.probe)["status"], expected)

    def test_missing_and_permission_failures(self):
        self.assertIsNone(self.probe.read("/proc/missing"))
        self.assertIsNone(self.probe.read("/proc"))
        with patch.object(Path, "open", side_effect=PermissionError("denied")):
            self.assertIsNone(self.probe.read("/proc/meminfo"))
            for check in (board.check_npu_driver, board.check_npu_load):
                result = check(self.probe)
                self.assertEqual(result["status"], "skip")
                self.assertIn("用 sudo 重跑可读", result["hint"])
        with patch("rkdoctor.probe.os.scandir", side_effect=PermissionError("denied")):
            self.assertIsNone(self.probe.glob("/dev/rknpu*"))
            self.assertEqual(board.check_npu_device(self.probe, True)["status"], "skip")
        with patch.object(Path, "stat", side_effect=PermissionError("denied")):
            self.assertIsNone(self.probe.exists("/dev/rga"))

    def test_snapshot_paths_do_not_escape(self):
        self.assertIsNone(self.probe.path("/../outside"))
        (self.root / "escape").symlink_to("/proc")
        self.assertIsNone(self.probe.read("/escape/meminfo"))
        (self.root / "internal").symlink_to("proc")
        self.assertEqual(self.probe.read("/internal/meminfo"), self.probe.read("/proc/meminfo"))

    def test_snapshot_does_not_use_host_ldconfig(self):
        with patch.object(self.probe, "command", side_effect=AssertionError("host command")):
            self.assertEqual(board.check_rknn_runtime(self.probe)["status"], "pass")

    def test_library_glob_ignores_non_directories(self):
        self.write("/usr/lib/not-a-directory", "plain file")
        self.assertEqual(self.probe.glob("/usr/lib/*/librknnrt.so*"), [])
        self.assertFalse(self.probe.exists("/usr/lib/not-a-directory/librknnrt.so"))
        (self.root / "usr/lib/librknnrt.so").unlink()
        self.assertEqual(board.check_rknn_runtime(self.probe)["status"], "warn")

    def test_live_runtime_uses_ldconfig_path(self):
        probe = Probe()
        with patch.object(probe, "command", return_value="librknnrt.so (libc6) => /opt/vendor/librknnrt.so"), \
                patch.object(probe, "glob", return_value=[]), \
                patch.object(probe, "exists", return_value=True), \
                patch.object(probe, "read_bytes", return_value=b"librknnrt version: 1.6.0"):
            self.assertEqual(board.check_rknn_runtime(probe)["value"]["version"], "1.6.0")

    def test_disk_targets_root_and_handles_failure(self):
        with patch("rkdoctor.probe.shutil.disk_usage", return_value=Usage(100, 50, 50)) as usage:
            self.probe.disk_usage()
            usage.assert_called_once_with(self.root)
        with patch("rkdoctor.probe.shutil.disk_usage", side_effect=PermissionError()):
            self.assertEqual(board.check_disk(self.probe)["status"], "skip")

    def test_command_failure_and_timeout(self):
        for error in (FileNotFoundError(), PermissionError(), subprocess.TimeoutExpired("test", 5)):
            with patch("rkdoctor.probe.subprocess.run", side_effect=error):
                self.assertIsNone(self.probe.command(["not-executed"]))

    def test_import_failure_isolated(self):
        for output in (None, "noise", 'RKDOCTOR:{"present":null,"version":"unknown","reason":"import failed"}'):
            with patch.object(self.probe, "command", return_value=output):
                self.assertIsNone(self.probe.python_module("cv2")["present"])
                self.assertEqual(board.check_python_env(self.probe)["status"], "skip")

    def test_real_optional_import_absence(self):
        result = self.probe.python_module("rkdoctor_nonexistent_test_module")
        self.assertFalse(result["present"])
        self.assertEqual(result["version"], "unknown")

    def test_check_boundary_never_aborts_report(self):
        with patch.object(self.probe, "read", side_effect=RuntimeError("unexpected driver error")), \
                patch.object(self.probe, "python_module", return_value={"present": False, "version": "unknown"}):
            checks = board.run_checks(self.probe)
        self.assertEqual(len(checks), 16)
        self.assertEqual(checks[0]["status"], "skip")

    def test_missing_arm_device_tree_is_not_invented(self):
        with patch.object(self.probe, "read", return_value=None), \
                patch.object(self.probe, "system", return_value="aarch64"):
            result = board.check_soc(self.probe)
        self.assertEqual(result["status"], "skip")
        self.assertIsNone(result["value"]["rockchip"])


class CliTests(unittest.TestCase):
    def test_planned_commands(self):
        for name, version in (("model", "v0.2"), ("rtsp", "v0.3")):
            output = io.StringIO()
            with redirect_stdout(output):
                status = main([name, "--future-option", "value"])
            self.assertEqual(status, 3)
            self.assertEqual(len(output.getvalue().splitlines()), 2)
            self.assertIn("planned", output.getvalue())
            self.assertIn(version, output.getvalue())
            self.assertIn("计划", output.getvalue())

    def test_version(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as error:
            main(["--version"])
        self.assertEqual(error.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), "rkdoctor 0.1.1")

    def test_board_rejects_unknown_flags(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main(["board", "--typo"])
        self.assertEqual(error.exception.code, 2)

    def test_cli_reports_for_all_fixtures(self):
        with tempfile.TemporaryDirectory(dir=REPO / "tests") as directory:
            for fixture in ("fake_rk3588", "fake_x86", "fake_rk_no_driver"):
                json_path, html_path = Path(directory) / "report.json", Path(directory) / "report.html"
                process = subprocess.run(
                    [sys.executable, "-B", "-m", "rkdoctor", "board", "--root", str(FIXTURES / fixture),
                     "--json", str(json_path), "--html", str(html_path)],
                    capture_output=True, text=True, cwd=REPO,
                    env={**os.environ, "PYTHONPATH": str(REPO / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
                    timeout=30,
                )
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(len(json.loads(json_path.read_text())["checks"]), 16)
                self.assertIn(FOOTER, html_path.read_text())
                self.assertNotIn("Traceback", process.stdout + process.stderr)

    def test_output_failure_is_clean(self):
        with patch("rkdoctor.cli.write_reports", side_effect=PermissionError("denied")), \
                patch.object(Probe, "python_module", return_value={"present": False, "version": "unknown"}), \
                redirect_stderr(io.StringIO()) as errors:
            status = main(["board", "--root", str(FIXTURES / "fake_x86"), "--json", "unwritable"])
        self.assertEqual(status, 1)
        self.assertNotIn("Traceback", errors.getvalue())

    def test_check_functions_below_sixty_lines(self):
        tree = ast.parse((REPO / "src/rkdoctor/checks/board.py").read_text())
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("check_"):
                self.assertLess(node.end_lineno - node.lineno + 1, 60, node.name)


if __name__ == "__main__":
    unittest.main()
