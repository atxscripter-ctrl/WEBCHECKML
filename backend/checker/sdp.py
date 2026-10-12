"""SdpStruct codec — port of com.example.webviewapp.SdpStruct."""
from __future__ import annotations
import struct as _struct
from typing import Any

TYPE_POS_INT      = 0
TYPE_NEG_INT      = 1
TYPE_FLOAT        = 2
TYPE_DOUBLE       = 3
TYPE_BYTES        = 4
TYPE_LIST         = 5
TYPE_DICT         = 6
TYPE_STRUCT_BEGIN = 7
TYPE_STRUCT_END   = 8

_END = object()


def _write_varint(out: bytearray, n: int) -> None:
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n & 0x7F)


def _read_varint(data: bytes, off: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        b = data[off]
        off += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, off


def _pack_header(out: bytearray, tag: int, type_: int) -> None:
    if tag < 0xF:
        out.append(((type_ & 0xF) << 4) | (tag & 0xF))
    else:
        out.append(((type_ & 0xF) << 4) | 0xF)
        _write_varint(out, tag)


def _pack(out: bytearray, tag: int, value: Any) -> None:
    if value is None:
        raise ValueError("None not encodable")
    if isinstance(value, bool):
        _pack_header(out, tag, TYPE_POS_INT)
        _write_varint(out, 1 if value else 0)
    elif isinstance(value, int):
        if value < 0:
            _pack_header(out, tag, TYPE_NEG_INT)
            _write_varint(out, -value)
        else:
            _pack_header(out, tag, TYPE_POS_INT)
            _write_varint(out, value)
    elif isinstance(value, float):
        _pack_header(out, tag, TYPE_DOUBLE)
        _write_varint(out, 8)
        out.extend(_struct.pack("<d", value))
    elif isinstance(value, (bytes, bytearray)):
        _pack_header(out, tag, TYPE_BYTES)
        _write_varint(out, len(value))
        out.extend(value)
    elif isinstance(value, str):
        b = value.encode("utf-8")
        _pack_header(out, tag, TYPE_BYTES)
        _write_varint(out, len(b))
        out.extend(b)
    elif isinstance(value, SdpStruct):
        _pack_header(out, tag, TYPE_STRUCT_BEGIN)
        raw = value.raw()
        out.extend(raw[1:-1])
        out.append(0x80)
    elif isinstance(value, (list, tuple)):
        _pack_header(out, tag, TYPE_LIST)
        _write_varint(out, len(value))
        for item in value:
            _pack(out, 0, item)
    elif isinstance(value, dict):
        _pack_header(out, tag, TYPE_DICT)
        items = sorted(value.items(), key=lambda kv: (str(type(kv[0])), kv[0]))
        _write_varint(out, len(items))
        for k, v in items:
            _pack(out, 0, k)
            _pack(out, 0, v)
    else:
        raise TypeError(f"Unsupported SDP type: {type(value).__name__}")


class SdpStruct:
    __slots__ = ("_data", "_values")

    def __init__(self, values: dict | None = None, data: bytes | None = None):
        self._values: dict[int, Any] = values if values is not None else {}
        if data is not None:
            self._data = data
            self._values.clear()
            self._unpack_from_binary(data)
        else:
            self._data = self._pack_to_binary()

    @classmethod
    def build(cls, *args) -> "SdpStruct":
        if len(args) % 2 != 0:
            raise ValueError("build() requires tag/value pairs")
        s = cls()
        d: dict[int, Any] = {}
        for i in range(0, len(args), 2):
            d[int(args[i])] = args[i + 1]
        s._values = d
        s._data = s._pack_to_binary()
        return s

    @classmethod
    def decode(cls, data: bytes) -> "SdpStruct":
        return cls(data=data)

    def _pack_to_binary(self) -> bytes:
        out = bytearray()
        out.append(0x70)
        for tag in sorted(self._values.keys()):
            _pack(out, tag, self._values[tag])
        out.append(0x80)
        return bytes(out)

    def raw(self) -> bytes:
        return self._data

    def _unpack_from_binary(self, data: bytes) -> None:
        if not data or (data[0] >> 4) != TYPE_STRUCT_BEGIN:
            return
        off = 1
        while off < len(data):
            tag, value, off = self._unpack(data, off)
            if value is _END:
                break
            if value is not None:
                self._values[tag] = value

    def _unpack(self, data: bytes, off: int) -> tuple[int, Any, int]:
        if off >= len(data):
            return 0, None, off
        b = data[off]
        off += 1
        type_ = b >> 4
        tag = b & 0xF
        if tag == 0xF:
            tag, off = _read_varint(data, off)

        if type_ == TYPE_POS_INT:
            v, off = _read_varint(data, off)
            return tag, v, off
        if type_ == TYPE_NEG_INT:
            v, off = _read_varint(data, off)
            return tag, -v, off
        if type_ == TYPE_FLOAT:
            size, off = _read_varint(data, off)
            raw = data[off:off + size]; off += size
            raw = raw.ljust(4, b"\x00")[:4]
            return tag, _struct.unpack("<f", raw)[0], off
        if type_ == TYPE_DOUBLE:
            size, off = _read_varint(data, off)
            raw = data[off:off + size]; off += size
            raw = raw.ljust(8, b"\x00")[:8]
            return tag, _struct.unpack("<d", raw)[0], off
        if type_ == TYPE_BYTES:
            size, off = _read_varint(data, off)
            raw = data[off:off + size]; off += size
            try:
                return tag, raw.decode("utf-8"), off
            except UnicodeDecodeError:
                return tag, raw, off
        if type_ == TYPE_LIST:
            size, off = _read_varint(data, off)
            items = []
            for _ in range(size):
                _, item, off = self._unpack(data, off)
                items.append(item)
            return tag, items, off
        if type_ == TYPE_DICT:
            size, off = _read_varint(data, off)
            d: dict = {}
            for _ in range(size):
                _, k, off = self._unpack(data, off)
                _, v, off = self._unpack(data, off)
                d[k] = v
            return tag, d, off
        if type_ == TYPE_STRUCT_BEGIN:
            sub = SdpStruct()
            while off < len(data):
                sub_tag, value, off = self._unpack(data, off)
                if value is _END:
                    break
                if value is not None:
                    sub._values[sub_tag] = value
            sub._data = sub._pack_to_binary()
            return tag, sub, off
        if type_ == TYPE_STRUCT_END:
            return tag, _END, off
        raise ValueError(f"Unknown SDP data type: {type_}")

    def get(self, tag: int) -> Any:
        return self._values.get(tag)

    def has(self, tag: int) -> bool:
        return tag in self._values

    def get_long(self, tag: int) -> int:
        v = self._values.get(tag)
        return int(v) if isinstance(v, (int, float)) else 0

    def get_str(self, tag: int) -> str | None:
        v = self._values.get(tag)
        return v if isinstance(v, str) else None

    def get_list(self, tag: int) -> list | None:
        v = self._values.get(tag)
        return v if isinstance(v, list) else None

    def get_dict(self, tag: int) -> dict | None:
        v = self._values.get(tag)
        return v if isinstance(v, dict) else None

    def get_struct(self, tag: int) -> "SdpStruct | None":
        v = self._values.get(tag)
        return v if isinstance(v, SdpStruct) else None

    def tags(self) -> list[int]:
        return sorted(self._values.keys())

    def __repr__(self) -> str:
        return f"SdpStruct({self._values})"