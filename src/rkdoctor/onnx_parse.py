"""Minimal read-only ONNX parser on raw protobuf wire format.

Standard library only. Extracts model structure for the convert pre-flight
check: opset, graph inputs/outputs with shapes, node op histogram, and
initializer sizes. Weight *values* are never decoded.
"""


class OnnxParseError(Exception):
    pass


def _varint(data, pos):
    result = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise OnnxParseError("truncated varint")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, pos
        shift += 7
        if shift > 70:
            raise OnnxParseError("varint too long")


def _fields(data):
    """Yield (field_number, wire_type, value) — value is int or bytes."""
    pos = 0
    while pos < len(data):
        tag, pos = _varint(data, pos)
        number, wire = tag >> 3, tag & 7
        if wire == 0:
            value, pos = _varint(data, pos)
            yield number, wire, value
        elif wire == 1:
            if pos + 8 > len(data):
                raise OnnxParseError("truncated 64-bit field")
            yield number, wire, data[pos:pos + 8]
            pos += 8
        elif wire == 2:
            length, pos = _varint(data, pos)
            if pos + length > len(data):
                raise OnnxParseError("truncated length-delimited field")
            yield number, wire, data[pos:pos + length]
            pos += length
        elif wire == 5:
            if pos + 4 > len(data):
                raise OnnxParseError("truncated 32-bit field")
            yield number, wire, data[pos:pos + 4]
            pos += 4
        else:
            raise OnnxParseError("unsupported wire type %d" % wire)


def _text(raw):
    return raw.decode("utf-8", "replace")


def _parse_shape(type_bytes):
    """TypeProto -> (elem_type, dims) where a dim is int or 'param:name'."""
    elem_type = None
    dims = []
    for number, wire, value in _fields(type_bytes):
        if number == 1 and wire == 2:  # tensor_type
            for tnum, twire, tval in _fields(value):
                if tnum == 1 and twire == 0:
                    elem_type = tval
                elif tnum == 2 and twire == 2:  # shape
                    for dnum, dwire, dval in _fields(tval):
                        if dnum == 1 and dwire == 2:  # dim
                            dim_value = None
                            dim_param = None
                            for fnum, fwire, fval in _fields(dval):
                                if fnum == 1 and fwire == 0:
                                    dim_value = fval
                                elif fnum == 2 and fwire == 2:
                                    dim_param = _text(fval)
                            dims.append(dim_value if dim_param is None
                                        else "param:" + dim_param)
    return elem_type, dims


def _parse_value_info(data):
    name = None
    elem_type = None
    dims = []
    for number, wire, value in _fields(data):
        if number == 1 and wire == 2:
            name = _text(value)
        elif number == 2 and wire == 2:
            elem_type, dims = _parse_shape(value)
    return {"name": name, "elem_type": elem_type, "shape": dims}


def _parse_tensor(data):
    dims = []
    data_type = None
    name = None
    stored = len(data)
    for number, wire, value in _fields(data):
        if number == 1:
            if wire == 0:
                dims.append(value)
            elif wire == 2:  # packed int64
                pos = 0
                while pos < len(value):
                    item, pos = _varint(value, pos)
                    dims.append(item)
        elif number == 2 and wire == 0:
            data_type = value
        elif number == 8 and wire == 2:
            name = _text(value)
    return {"name": name, "dims": dims, "data_type": data_type, "stored_bytes": stored}


def parse_onnx(data: bytes) -> dict:
    """Parse ModelProto bytes into a structural summary."""
    model = {"opset": None, "producer": None, "graph_name": None,
             "inputs": [], "outputs": [], "nodes": [], "op_hist": {},
             "initializers": [], "weights_bytes": 0}
    graph_bytes = None

    def read_opset(payload):
        version = None
        domain = ""
        for onum, owire, oval in _fields(payload):
            if onum == 1 and owire == 2:
                domain = _text(oval)
            elif onum == 2 and owire == 0:
                version = oval
        if domain in ("", "ai.onnx") and version is not None and 1 <= version <= 30:
            return version
        return None

    for number, wire, value in _fields(data):
        # opset_import is field 8 in current files; very old layouts put it
        # at field 2, where newer files carry producer_name instead. Tell
        # them apart by content, not by faith.
        if number == 8 and wire == 2:
            version = read_opset(value)
            if version is not None:
                model["opset"] = version
        elif number == 2 and wire == 2:
            if all(32 <= byte < 127 for byte in value):
                model["producer"] = _text(value)
            else:
                try:
                    version = read_opset(value)
                except OnnxParseError:
                    version = None
                if version is not None:
                    model["opset"] = version
        elif number == 3 and wire == 2 and model["producer"] is None:
            model["producer"] = _text(value)
        elif number == 7 and wire == 2:
            graph_bytes = value
    if graph_bytes is None:
        raise OnnxParseError("no graph in model")
    initializer_names = set()
    for number, wire, value in _fields(graph_bytes):
        if number == 1 and wire == 2:  # node
            op_type = None
            node_name = None
            for nnum, nwire, nval in _fields(value):
                if nnum == 3 and nwire == 2:
                    node_name = _text(nval)
                elif nnum == 4 and nwire == 2:
                    op_type = _text(nval)
            if op_type:
                model["nodes"].append({"op_type": op_type, "name": node_name})
                model["op_hist"][op_type] = model["op_hist"].get(op_type, 0) + 1
        elif number == 2 and wire == 2:
            model["graph_name"] = _text(value)
        elif number == 5 and wire == 2:  # initializer
            tensor = _parse_tensor(value)
            model["initializers"].append(tensor)
            model["weights_bytes"] += tensor["stored_bytes"]
            if tensor["name"]:
                initializer_names.add(tensor["name"])
        elif number == 11 and wire == 2:
            model["inputs"].append(_parse_value_info(value))
        elif number == 12 and wire == 2:
            model["outputs"].append(_parse_value_info(value))
    for entry in model["inputs"]:
        entry["is_initializer"] = entry["name"] in initializer_names
    return model
