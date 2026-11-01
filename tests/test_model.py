import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from rkdoctor.cli import main
from rkdoctor.checks.model import diagnose_model
from rkdoctor.perf_parse import judge, latency_stats, parse_perf_detail

FIXTURE = Path(__file__).parent / "fixtures" / "perf_detail_yolov8n.txt"


def layer(op_type, target, time_us, layer_id=1):
    return {"id": layer_id, "op_type": op_type, "data_type": "FLOAT16",
            "target": target, "time_us": time_us, "full_name": op_type + ":x"}


class PerfParseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detail = parse_perf_detail(FIXTURE.read_text(encoding="utf-8"))

    def test_layer_count_and_targets(self):
        layers = self.detail["layers"]
        self.assertEqual(len(layers), 108)
        cpu = [item for item in layers if item["target"] == "CPU"]
        self.assertEqual(len(cpu), 10)
        self.assertEqual(sum(1 for item in cpu if item["op_type"] == "InputOperator"), 1)
        self.assertEqual(sum(1 for item in cpu if item["op_type"] == "OutputOperator"), 9)

    def test_totals(self):
        self.assertEqual(self.detail["total_us"], 38199)
        self.assertEqual(sum(item["time_us"] for item in self.detail["layers"]), 38199)

    def test_ranking(self):
        first = self.detail["ranking"][0]
        self.assertEqual(first["op_type"], "ConvExSwish")
        self.assertEqual(first["calls"], 57)
        self.assertEqual(first["total_us"], 29047)

    def test_real_model_is_npu_only(self):
        result = judge(self.detail["layers"])
        self.assertEqual(result["verdict"], "NPU_ONLY")
        self.assertEqual(result["fallback_ops"], [])
        self.assertEqual(len(result["structural_cpu_ops"]), 10)
        self.assertEqual(result["cpu_us"], 220)


class JudgeTest(unittest.TestCase):
    def test_mixed_by_share(self):
        layers = [layer("Conv", "NPU", 9500), layer("CustomOp", "CPU", 500, 2)]
        self.assertEqual(judge(layers)["verdict"], "MIXED")

    def test_cpu_heavy(self):
        layers = [layer("Conv", "NPU", 5000), layer("CustomOp", "CPU", 5000, 2)]
        self.assertEqual(judge(layers)["verdict"], "CPU_HEAVY")

    def test_single_big_op_forces_mixed(self):
        layers = [layer("Conv", "NPU", 100000), layer("CustomOp", "CPU", 150, 2)]
        result = judge(layers)
        self.assertLess(result["fallback_share_pct"], 1.0)
        self.assertEqual(result["verdict"], "MIXED")

    def test_structural_only_is_npu_only(self):
        layers = [layer("Conv", "NPU", 1000), layer("InputOperator", "CPU", 8, 2),
                  layer("OutputOperator", "CPU", 20, 3)]
        self.assertEqual(judge(layers)["verdict"], "NPU_ONLY")


class LatencyStatsTest(unittest.TestCase):
    def test_stats(self):
        stats = latency_stats([40, 10, 30, 20])
        self.assertEqual(stats["p50"], 20)
        self.assertEqual(stats["p95"], 40)
        self.assertEqual(stats["mean"], 25.0)
        self.assertEqual(stats["min"], 10)
        self.assertEqual(stats["max"], 40)

    def test_empty(self):
        self.assertIsNone(latency_stats([])["p50"])


class Attr(dict):
    __getattr__ = dict.get


class FakeRuntime:
    lib_path = "/fake/librknnrt.so"

    def __init__(self, detail_text, available=True):
        self._detail = detail_text
        self._available = available
        self.destroyed = False

    def available(self):
        return (True, "") if self._available else (False, "fake: no runtime")

    def load(self, data):
        pass

    def sdk_version(self):
        return ("2.3.2 (fake)", "0.9.8")

    def io_attrs(self):
        inputs = [{"index": 0, "name": "images", "dims": [1, 640, 640, 3],
                   "type": 1, "type_name": "FLOAT32", "fmt": 1,
                   "fmt_name": "NHWC", "n_elems": 1228800, "size": 4915200}]
        outputs = [{"index": 0, "name": "out", "dims": [1, 1, 20, 20],
                    "type": 1, "type_name": "FLOAT32", "fmt": 0,
                    "fmt_name": "NCHW", "n_elems": 400, "size": 1600}]
        return inputs, outputs

    def make_zero_inputs(self, attrs):
        return ("fake-inputs", 1)

    def run_once(self, inputs):
        return 38199

    def perf_detail(self):
        return self._detail

    def get_outputs(self):
        return None

    def destroy(self):
        self.destroyed = True


class DiagnoseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.model = Path(self.tmp.name) / "some_model.rknn"
        self.model.write_bytes(b"fake-rknn-bytes")

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_report_with_fake_runtime(self):
        backend = FakeRuntime(FIXTURE.read_text(encoding="utf-8"))
        report = diagnose_model(self.model, runs=3, warmup=1, backend=backend)
        self.assertEqual(report["kind"], "model")
        self.assertEqual(report["verdict"], "NPU_ONLY")
        self.assertEqual(report["model"]["file"], "some_model.rknn")
        self.assertEqual(report["model"]["size_bytes"], len(b"fake-rknn-bytes"))
        self.assertEqual(len(report["model"]["sha256"]), 64)
        self.assertEqual(report["runtime"]["api_version"], "2.3.2 (fake)")
        self.assertEqual(report["latency_us"]["p50"], 38199)
        self.assertEqual(report["runs"], {"warmup": 1, "measured": 3})
        self.assertEqual(report["perf"]["layers_total_us"], 38199)
        self.assertEqual(report["fallback_ops"], [])
        self.assertTrue(backend.destroyed)

    def test_unavailable_runtime_is_unknown(self):
        backend = FakeRuntime("", available=False)
        report = diagnose_model(self.model, backend=backend)
        self.assertEqual(report["verdict"], "UNKNOWN")
        self.assertTrue(any("runtime unavailable" in note for note in report["notes"]))


class CliModelTest(unittest.TestCase):
    def test_missing_file_exits_2(self):
        self.assertEqual(main(["model", "/nonexistent/model.rknn"]), 2)

    def test_unknown_report_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "m.rknn"
            model.write_bytes(b"junk")
            out = Path(tmp) / "report.json"
            with mock.patch.dict(os.environ, {"RKDOCTOR_RKNNRT": "/nonexistent/librknnrt.so"}):
                code = main(["model", str(model), "--json", str(out)])
            self.assertEqual(code, 0)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["verdict"], "UNKNOWN")
            self.assertEqual(report["model"]["file"], "m.rknn")


if __name__ == "__main__":
    unittest.main()
