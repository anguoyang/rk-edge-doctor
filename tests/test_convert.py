import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest

from rkdoctor.checks.convert import build_recipe, check_onnx
from rkdoctor.cli import main
from rkdoctor.onnx_parse import parse_onnx


def _varint(n):
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def fv(num, val):
    return _varint((num << 3) | 0) + _varint(val)


def fb(num, data):
    if isinstance(data, str):
        data = data.encode()
    return _varint((num << 3) | 2) + _varint(len(data)) + data


def _dim(value):
    if isinstance(value, int):
        return fv(1, value)
    return fb(2, value)


def _value_info(name, dims):
    shape = b"".join(fb(1, _dim(d)) for d in dims)
    tensor = fv(1, 1) + fb(2, shape)
    return fb(1, name) + fb(2, fb(1, tensor))


def _node(op):
    return fb(4, op)


def _initializer(name, dims, raw_len=16):
    packed = b"".join(_varint(d) for d in dims)
    return fb(1, packed) + fv(2, 1) + fb(8, name) + fb(9, b"\0" * raw_len)


def make_onnx(nodes, input_dims=(1, 3, 224, 224), opset=13, initializers=()):
    graph = b"".join(fb(1, _node(op)) for op in nodes)
    graph += fb(2, "g")
    for init in initializers:
        graph += fb(5, init)
    graph += fb(11, _value_info("x", list(input_dims)))
    graph += fb(12, _value_info("y", [1, 8]))
    # Real-world layout (PyTorch exporter): producer_name at field 2,
    # opset_import at field 8, graph at field 7.
    model = fb(2, "test-producer") + fb(7, graph) + fb(8, fv(2, opset))
    return model


class ParseTests(unittest.TestCase):
    def test_static_cnn(self):
        data = make_onnx(["Conv", "Relu", "Conv"],
                         initializers=[_initializer("W", [8, 3, 3, 3])])
        info = parse_onnx(data)
        self.assertEqual(info["opset"], 13)
        self.assertEqual(info["producer"], "test-producer")
        self.assertEqual(info["op_hist"], {"Conv": 2, "Relu": 1})
        self.assertEqual(info["inputs"][0]["shape"], [1, 3, 224, 224])
        self.assertEqual(info["initializers"][0]["name"], "W")
        self.assertGreater(info["weights_bytes"], 0)

    def test_dynamic_dim_param(self):
        info = parse_onnx(make_onnx(["Conv"], input_dims=("batch", 3, 224, 224)))
        self.assertEqual(info["inputs"][0]["shape"][0], "param:batch")


class CheckTests(unittest.TestCase):
    def _check(self, data, target="rk3588"):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.onnx"
            path.write_bytes(data)
            return check_onnx(path, target=target)

    def test_ready(self):
        report = self._check(make_onnx(["Conv", "Relu", "Conv"]))
        self.assertEqual(report["verdict"], "READY")

    def test_dynamic_is_risky(self):
        report = self._check(make_onnx(["Conv"], input_dims=("batch", 3, 224, 224)))
        self.assertEqual(report["verdict"], "RISKY")
        dyn = [c for c in report["checks"] if c["id"] == "dynamic_dims"]
        self.assertEqual(dyn[0]["status"], "warn")

    def test_control_flow_fails(self):
        report = self._check(make_onnx(["Conv", "Loop", "Add"]))
        self.assertEqual(report["verdict"], "LIKELY_TO_FAIL")

    def test_caution_and_tail_warn(self):
        report = self._check(make_onnx(["Conv", "NonMaxSuppression", "Softmax"]))
        self.assertEqual(report["verdict"], "RISKY")
        ids = {c["id"]: c["status"] for c in report["checks"]}
        self.assertEqual(ids["caution_ops"], "warn")
        self.assertEqual(ids["quant_tail"], "warn")

    def test_garbage_is_likely_to_fail(self):
        report = self._check(b"\xff\xff\xff not an onnx")
        self.assertEqual(report["verdict"], "LIKELY_TO_FAIL")
        self.assertEqual(report["checks"][0]["status"], "fail")


class RecipeTests(unittest.TestCase):
    def test_static_recipe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "det.onnx"
            path.write_bytes(make_onnx(["Conv", "Relu"]))
            script, recipe = build_recipe(path, target="rk3576", quantize="int8",
                                          dataset="calib.txt")
            self.assertIn("target_platform='rk3576'", script)
            self.assertIn("input_size_list=[[1, 3, 224, 224]]", script)
            self.assertIn("do_quantization=True, dataset='calib.txt'", script)
            self.assertEqual(recipe["output"], "det.rknn")

    def test_dynamic_recipe_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.onnx"
            path.write_bytes(make_onnx(["Conv"], input_dims=("batch", 3, 224, 224)))
            script, recipe = build_recipe(path)
            self.assertIn("dynamic shapes", script)
            self.assertEqual(recipe["dynamic_inputs"], ["x"])


class CliTests(unittest.TestCase):
    def test_check_and_recipe_via_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            onnx = Path(tmp) / "m.onnx"
            onnx.write_bytes(make_onnx(["Conv", "Relu"]))
            report_json = Path(tmp) / "r.json"
            out = io.StringIO()
            with redirect_stdout(out):
                status = main(["convert", "check", str(onnx), "--json", str(report_json)])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(report_json.read_text())["verdict"], "READY")
            script = Path(tmp) / "convert_m.py"
            with redirect_stdout(io.StringIO()):
                status = main(["convert", "recipe", str(onnx), "--out", str(script)])
            self.assertEqual(status, 0)
            self.assertIn("rknn.config", script.read_text())

    def test_missing_file_is_usage_error(self):
        with redirect_stdout(io.StringIO()):
            status = main(["convert", "check", "/nonexistent/m.onnx"])
        self.assertEqual(status, 2)


if __name__ == "__main__":
    unittest.main()
