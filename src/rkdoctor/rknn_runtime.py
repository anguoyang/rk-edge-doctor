"""Minimal ctypes binding for librknnrt (Rockchip RKNN runtime).

Only the calls RK Edge Doctor needs: load a model, read tensor attributes,
run inference, and collect the runtime's own performance accounting
(PERF_RUN / PERF_DETAIL). No third-party dependency; if librknnrt is not
present the caller gets RuntimeUnavailable and reports UNKNOWN instead of
guessing.
"""

import ctypes
import os
import subprocess
from typing import List, Optional, Tuple

# rknn_query_cmd (rknn_api.h, rknn-toolkit2 2.x)
Q_IN_OUT_NUM = 0
Q_INPUT_ATTR = 1
Q_OUTPUT_ATTR = 2
Q_PERF_DETAIL = 3
Q_PERF_RUN = 4
Q_SDK_VERSION = 5

RKNN_FLAG_COLLECT_PERF_MASK = 0x00000008

# rknn_tensor_type -> (name, element size in bytes)
TENSOR_TYPES = {
    1: ("FLOAT32", 4),
    2: ("FLOAT16", 2),
    3: ("INT8", 1),
    4: ("UINT8", 1),
    5: ("INT16", 2),
    6: ("INT32", 4),
    7: ("INT64", 8),
    8: ("BOOL", 1),
}
TENSOR_FORMATS = {0: "NCHW", 1: "NHWC", 2: "NC1HWC2"}

COMMON_LIB_PATHS = (
    "/usr/lib/librknnrt.so",
    "/usr/lib/aarch64-linux-gnu/librknnrt.so",
    "/usr/local/lib/librknnrt.so",
)


class RuntimeUnavailable(Exception):
    """librknnrt cannot be found or loaded on this host."""


class ModelLoadError(Exception):
    """The runtime rejected the model or a query failed."""


class _InOutNum(ctypes.Structure):
    _fields_ = [("n_input", ctypes.c_uint32), ("n_output", ctypes.c_uint32)]


class _TensorAttr(ctypes.Structure):
    _fields_ = [
        ("index", ctypes.c_uint32),
        ("n_dims", ctypes.c_uint32),
        ("dims", ctypes.c_uint32 * 16),
        ("name", ctypes.c_char * 256),
        ("n_elems", ctypes.c_uint32),
        ("size", ctypes.c_uint32),
        ("fmt", ctypes.c_int),
        ("type", ctypes.c_int),
        ("qnt_type", ctypes.c_int),
        ("fl", ctypes.c_int8),
        ("zp", ctypes.c_int32),
        ("scale", ctypes.c_float),
        ("w_stride", ctypes.c_uint32),
        ("size_with_stride", ctypes.c_uint32),
        ("pass_through", ctypes.c_uint8),
        ("h_stride", ctypes.c_uint32),
    ]


class _Input(ctypes.Structure):
    _fields_ = [
        ("index", ctypes.c_uint32),
        ("buf", ctypes.c_void_p),
        ("size", ctypes.c_uint32),
        ("pass_through", ctypes.c_uint8),
        ("type", ctypes.c_int),
        ("fmt", ctypes.c_int),
    ]


class _Output(ctypes.Structure):
    _fields_ = [
        ("index", ctypes.c_uint32),
        ("want_float", ctypes.c_uint8),
        ("is_prealloc", ctypes.c_uint8),
        ("buf", ctypes.c_void_p),
        ("size", ctypes.c_uint32),
    ]


class _PerfRun(ctypes.Structure):
    _fields_ = [("run_duration", ctypes.c_int64)]


class _PerfDetail(ctypes.Structure):
    _fields_ = [("data", ctypes.c_void_p), ("data_len", ctypes.c_uint64)]


class _SdkVersion(ctypes.Structure):
    _fields_ = [("api_version", ctypes.c_char * 256),
                ("drv_version", ctypes.c_char * 256)]


def find_library() -> Optional[str]:
    override = os.environ.get("RKDOCTOR_RKNNRT")
    if override:
        return override if os.path.exists(override) else None
    try:
        out = subprocess.run(["ldconfig", "-p"], capture_output=True, text=True,
                             timeout=5, check=False)
        for line in out.stdout.splitlines():
            if "librknnrt.so" in line and "=>" in line:
                candidate = line.split("=>", 1)[1].strip()
                if os.path.exists(candidate):
                    return candidate
    except (OSError, subprocess.SubprocessError):
        pass
    for candidate in COMMON_LIB_PATHS:
        if os.path.exists(candidate):
            return candidate
    return None


def tensor_type_name(type_id: int) -> str:
    entry = TENSOR_TYPES.get(type_id)
    return entry[0] if entry else "type_%d" % type_id


def tensor_format_name(fmt_id: int) -> str:
    return TENSOR_FORMATS.get(fmt_id, "fmt_%d" % fmt_id)


class RknnRuntime:
    """One loaded model context. Duck-typed backend for checks.model."""

    def __init__(self, lib_path: Optional[str] = None) -> None:
        self.lib_path = lib_path if lib_path is not None else find_library()
        self._lib = None
        self._ctx = None
        self._buffers = []

    def available(self) -> Tuple[bool, str]:
        if not self.lib_path:
            return False, "librknnrt.so not found (looked in ldconfig cache and standard paths)"
        try:
            self._ensure_lib()
        except RuntimeUnavailable as error:
            return False, str(error)
        return True, ""

    def _ensure_lib(self):
        if self._lib is None:
            try:
                self._lib = ctypes.CDLL(self.lib_path)
            except OSError as error:
                raise RuntimeUnavailable("cannot load %s: %s" % (self.lib_path, error))
        return self._lib

    def load(self, model_bytes: bytes) -> None:
        lib = self._ensure_lib()
        ctx = ctypes.c_uint64(0)
        ret = lib.rknn_init(ctypes.byref(ctx), model_bytes, len(model_bytes),
                            RKNN_FLAG_COLLECT_PERF_MASK, None)
        if ret != 0 or not ctx.value:
            raise ModelLoadError("rknn_init failed with code %d" % ret)
        self._ctx = ctx

    def _query(self, cmd: int, struct) -> None:
        ret = self._lib.rknn_query(self._ctx, cmd, ctypes.byref(struct), ctypes.sizeof(struct))
        if ret != 0:
            raise ModelLoadError("rknn_query cmd %d failed with code %d" % (cmd, ret))

    def sdk_version(self) -> Tuple[str, str]:
        version = _SdkVersion()
        self._query(Q_SDK_VERSION, version)
        return (version.api_version.decode("utf-8", "replace"),
                version.drv_version.decode("utf-8", "replace"))

    def io_attrs(self) -> Tuple[List[dict], List[dict]]:
        num = _InOutNum()
        self._query(Q_IN_OUT_NUM, num)
        inputs = [self._attr(Q_INPUT_ATTR, i) for i in range(num.n_input)]
        outputs = [self._attr(Q_OUTPUT_ATTR, i) for i in range(num.n_output)]
        return inputs, outputs

    def _attr(self, cmd: int, index: int) -> dict:
        attr = _TensorAttr()
        attr.index = index
        self._query(cmd, attr)
        return {
            "index": index,
            "name": attr.name.decode("utf-8", "replace"),
            "dims": [int(attr.dims[j]) for j in range(attr.n_dims)],
            "type": int(attr.type),
            "type_name": tensor_type_name(attr.type),
            "fmt": int(attr.fmt),
            "fmt_name": tensor_format_name(attr.fmt),
            "n_elems": int(attr.n_elems),
            "size": int(attr.size),
        }

    def make_zero_inputs(self, input_attrs: List[dict]) -> list:
        """Build the ctypes input array of zero buffers for the given attrs."""
        structs = []
        self._buffers = []
        for attr in input_attrs:
            entry = TENSOR_TYPES.get(attr["type"])
            if entry is None:
                raise ModelLoadError("unsupported input tensor type %d" % attr["type"])
            size = attr["n_elems"] * entry[1]
            buffer = ctypes.create_string_buffer(size)
            self._buffers.append(buffer)
            structs.append(_Input(index=attr["index"],
                                  buf=ctypes.cast(buffer, ctypes.c_void_p),
                                  size=size, pass_through=0,
                                  type=attr["type"], fmt=attr["fmt"]))
        return (_Input * len(structs))(*structs), len(structs)

    def run_once(self, inputs) -> int:
        """Run one inference; returns the runtime's PERF_RUN duration in us."""
        array, count = inputs
        ret = self._lib.rknn_inputs_set(self._ctx, count, array)
        if ret != 0:
            raise ModelLoadError("rknn_inputs_set failed with code %d" % ret)
        ret = self._lib.rknn_run(self._ctx, None)
        if ret != 0:
            raise ModelLoadError("rknn_run failed with code %d" % ret)
        perf = _PerfRun()
        self._query(Q_PERF_RUN, perf)
        return int(perf.run_duration)

    def perf_detail(self) -> str:
        """PERF_DETAIL text of the most recent run (may be empty)."""
        capacity = 1024 * 1024
        buffer = ctypes.create_string_buffer(capacity)
        detail = _PerfDetail(data=ctypes.cast(buffer, ctypes.c_void_p),
                             data_len=capacity)
        ret = self._lib.rknn_query(self._ctx, Q_PERF_DETAIL,
                                   ctypes.byref(detail), ctypes.sizeof(detail))
        if ret != 0 or not detail.data or not detail.data_len:
            return ""
        raw = ctypes.string_at(detail.data, detail.data_len)
        return raw.split(b"\x00")[0].decode("utf-8", "replace")

    def get_outputs(self) -> Optional[List[Tuple[dict, list]]]:
        """Output tensors as float lists; None if any output is not FLOAT32."""
        _, output_attrs = self.io_attrs()
        outputs = []
        structs = []
        for attr in output_attrs:
            if attr["type"] != 1:
                return None
            structs.append(_Output(index=attr["index"], want_float=1, is_prealloc=0))
        array = (_Output * len(structs))(*structs)
        ret = self._lib.rknn_outputs_get(self._ctx, len(structs), array, None)
        if ret != 0:
            return None
        try:
            for struct, attr in zip(structs, output_attrs):
                floats = ctypes.cast(struct.buf, ctypes.POINTER(ctypes.c_float))
                outputs.append((attr, [floats[i] for i in range(attr["n_elems"])]))
        finally:
            self._lib.rknn_outputs_release(self._ctx, len(structs), array)
        return outputs

    def destroy(self) -> None:
        if self._lib is not None and self._ctx is not None and self._ctx.value:
            try:
                self._lib.rknn_destroy(self._ctx)
            except (OSError, AttributeError):
                pass
        self._ctx = None
        self._buffers = []
