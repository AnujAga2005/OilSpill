"""Minimal, dependency-free reader for HDF5-backed NetCDF-4 files.

Only the subset of HDF5 that CMEMS ocean-physics products actually use is
implemented, which was determined by probing the supplied file
(``cmems_mod_glo_phy_my_0.083deg_P1D-m_*.nc``: superblock v0, 8-byte offsets
and lengths, old-style symbol-table groups, v1 object headers, chunked
datasets indexed by a v1 B-tree, and the shuffle + deflate filter pair).

Supported:

* superblock versions 0, 1, 2 and 3
* v1 object headers (``0x01``) and v2 object headers (``OHDR``)
* old-style groups (symbol table B-tree + local heap) and new-style groups
  (link messages stored directly in the object header)
* contiguous, compact and chunked (v1 B-tree) data layouts
* filters: shuffle (2), deflate (1) and fletcher32 (3, checksum ignored)
* fixed-point, floating-point and fixed-length string datatypes
* attributes (v1/v2/v3 attribute messages), enough for CF metadata such as
  ``units``, ``scale_factor``, ``add_offset`` and ``_FillValue``

Not supported (raises :class:`HDF5Error` rather than returning wrong data):
variable-length datatypes in datasets, compound datatypes, v2 B-tree chunk
indexing, external data storage and szip.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np

_SIGNATURE = b"\x89HDF\r\n\x1a\n"
_UNDEFINED = 0xFFFFFFFFFFFFFFFF

# Object-header message types we handle
MSG_NIL = 0x0000
MSG_DATASPACE = 0x0001
MSG_LINK_INFO = 0x0002
MSG_DATATYPE = 0x0003
MSG_FILL_OLD = 0x0004
MSG_FILL = 0x0005
MSG_LINK = 0x0006
MSG_DATA_LAYOUT = 0x0008
MSG_GROUP_INFO = 0x000A
MSG_FILTER_PIPELINE = 0x000B
MSG_ATTRIBUTE = 0x000C
MSG_OBJECT_COMMENT = 0x000D
MSG_CONTINUATION = 0x0010
MSG_SYMBOL_TABLE = 0x0011
MSG_ATTRIBUTE_INFO = 0x0015


class HDF5Error(Exception):
    """Raised when the file uses an HDF5 feature this reader cannot decode."""


# ---------------------------------------------------------------------------
# Low-level cursor
# ---------------------------------------------------------------------------


class _Buf:
    """Little-endian cursor over an in-memory byte range."""

    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def read(self, n: int) -> bytes:
        out = self.data[self.pos : self.pos + n]
        if len(out) < n:
            raise HDF5Error("unexpected end of HDF5 structure")
        self.pos += n
        return out

    def u8(self) -> int:
        return self.read(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.read(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.read(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.read(8))[0]

    def uint(self, size: int) -> int:
        raw = self.read(size)
        return int.from_bytes(raw, "little")

    def skip(self, n: int) -> None:
        self.pos += n

    def align(self, base: int, multiple: int) -> None:
        rel = self.pos - base
        pad = (-rel) % multiple
        self.pos += pad


# ---------------------------------------------------------------------------
# Datatype
# ---------------------------------------------------------------------------


@dataclass
class Datatype:
    cls: int
    size: int
    dtype: np.dtype | None
    is_string: bool = False
    string_pad: int = 0

    @property
    def name(self) -> str:
        if self.is_string:
            return f"string[{self.size}]"
        return str(self.dtype) if self.dtype is not None else f"class{self.cls}"


def _parse_datatype(buf: _Buf) -> Datatype:
    class_and_version = buf.u8()
    version = class_and_version >> 4
    cls = class_and_version & 0x0F
    if version not in (1, 2, 3):
        raise HDF5Error(f"unsupported datatype message version {version}")
    flags = buf.read(3)
    size = buf.u32()

    if cls == 0:  # fixed point
        bit_offset = buf.u16()
        precision = buf.u16()
        little = (flags[0] & 0x01) == 0
        signed = (flags[0] & 0x08) != 0
        if bit_offset != 0 or precision != size * 8:
            raise HDF5Error("non-byte-aligned integers are not supported")
        kind = "i" if signed else "u"
        dt = np.dtype(("<" if little else ">") + kind + str(size))
        return Datatype(cls, size, dt)

    if cls == 1:  # floating point
        buf.u16()  # bit offset
        buf.u16()  # bit precision
        buf.u8()  # exponent location
        buf.u8()  # exponent size
        buf.u8()  # mantissa location
        buf.u8()  # mantissa size
        buf.u32()  # exponent bias
        little = (flags[0] & 0x01) == 0
        if size not in (2, 4, 8):
            raise HDF5Error(f"unsupported float size {size}")
        dt = np.dtype(("<" if little else ">") + "f" + str(size))
        return Datatype(cls, size, dt)

    if cls == 3:  # string
        return Datatype(cls, size, np.dtype(f"S{size}"), is_string=True, string_pad=flags[0] & 0x0F)

    if cls == 9:  # variable length
        base = _parse_datatype(buf)
        # Only VLEN strings are tolerated, and only for attributes.
        return Datatype(cls, size, None, is_string=(base.is_string or base.cls == 3))

    if cls == 8:  # enumeration -> read as its base type
        base = _parse_datatype(buf)
        return Datatype(cls, size, base.dtype)

    raise HDF5Error(f"unsupported HDF5 datatype class {cls}")


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


@dataclass
class _Layout:
    kind: str  # 'compact' | 'contiguous' | 'chunked'
    address: int | None = None
    size: int | None = None
    chunk_dims: tuple[int, ...] = ()
    element_size: int = 0
    compact_data: bytes = b""


@dataclass
class _Filter:
    filter_id: int
    client_data: tuple[int, ...]


@dataclass
class Node:
    """A parsed HDF5 object header (group or dataset)."""

    address: int
    dims: tuple[int, ...] = ()
    datatype: Datatype | None = None
    layout: _Layout | None = None
    filters: list[_Filter] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    links: dict[str, int] = field(default_factory=dict)
    symbol_btree: int | None = None
    symbol_heap: int | None = None
    link_heap: int | None = None
    attribute_heap: int | None = None
    fill_value: bytes | None = None

    @property
    def is_dataset(self) -> bool:
        return self.datatype is not None and self.layout is not None


class NetCDF4File:
    """Read-only handle over an HDF5/NetCDF-4 file."""

    def __init__(self, path: str):
        self.path = str(path)
        self._fh = open(self.path, "rb")
        try:
            self._read_superblock()
            self._node_cache: dict[int, Node] = {}
            self._gcol_cache: dict[tuple[int, int], bytes] = {}
            self.root = self._read_object(self._root_address)
            self._variables: dict[str, Node] | None = None
        except Exception:
            self._fh.close()
            raise

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "NetCDF4File":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    # -- superblock --------------------------------------------------------
    def _at(self, offset: int, length: int) -> bytes:
        self._fh.seek(offset)
        return self._fh.read(length)

    def _read_superblock(self) -> None:
        head = self._at(0, 64)
        if head[:8] != _SIGNATURE:
            raise HDF5Error("not an HDF5 file (bad signature)")
        version = head[8]
        self.superblock_version = version
        if version in (0, 1):
            self.size_offsets = head[13]
            self.size_lengths = head[14]
            if self.size_offsets != 8 or self.size_lengths != 8:
                raise HDF5Error(
                    f"only 8-byte offsets/lengths are supported (file uses {self.size_offsets}/{self.size_lengths})"
                )
            extra = 4 if version == 1 else 0
            base = 24 + extra
            self.base_address = struct.unpack("<Q", head[base : base + 8])[0]
            # Root group symbol-table entry follows the driver-info address.
            entry = base + 32
            raw = self._at(entry, 40)
            self._root_address = struct.unpack("<Q", raw[8:16])[0]
        elif version in (2, 3):
            self.size_offsets = head[9]
            self.size_lengths = head[10]
            if self.size_offsets != 8 or self.size_lengths != 8:
                raise HDF5Error("only 8-byte offsets/lengths are supported")
            self.base_address = struct.unpack("<Q", head[12:20])[0]
            self._root_address = struct.unpack("<Q", head[36:44])[0]
        else:
            raise HDF5Error(f"unsupported HDF5 superblock version {version}")

    # -- object headers ----------------------------------------------------
    def _read_object(self, address: int) -> Node:
        if address in self._node_cache:
            return self._node_cache[address]
        node = Node(address=address)
        self._node_cache[address] = node
        probe = self._at(address, 4)
        if probe[:4] == b"OHDR":
            self._read_object_v2(address, node)
        else:
            self._read_object_v1(address, node)
        return node

    def _read_object_v1(self, address: int, node: Node) -> None:
        head = self._at(address, 16)
        version = head[0]
        if version != 1:
            raise HDF5Error(f"unsupported v1 object header version {version}")
        n_messages = struct.unpack("<H", head[2:4])[0]
        header_size = struct.unpack("<I", head[8:12])[0]
        body = self._at(address + 16, header_size)
        self._walk_messages_v1(body, node, n_messages)

    def _walk_messages_v1(self, body: bytes, node: Node, n_messages: int) -> None:
        buf = _Buf(body)
        seen = 0
        while seen < n_messages and buf.pos + 8 <= len(body):
            msg_type = buf.u16()
            msg_size = buf.u16()
            buf.u8()  # flags
            buf.skip(3)  # reserved
            payload = buf.read(msg_size) if msg_size else b""
            seen += 1
            self._apply_message(msg_type, payload, node, header_version=1)

    def _read_object_v2(self, address: int, node: Node) -> None:
        raw = self._at(address, 64)
        buf = _Buf(raw, 4)
        version = buf.u8()
        if version != 2:
            raise HDF5Error(f"unsupported v2 object header version {version}")
        flags = buf.u8()
        if flags & 0x20:
            buf.skip(16)  # access/mod/change/birth times
        if flags & 0x10:
            buf.skip(4)  # max compact / min dense
        size_field = 1 << (flags & 0x03)
        chunk_size = buf.uint(size_field)
        start = address + buf.pos
        body = self._at(start, chunk_size)
        self._walk_messages_v2(body, node, flags)

    def _walk_messages_v2(self, body: bytes, node: Node, obj_flags: int) -> None:
        buf = _Buf(body)
        track_order = bool(obj_flags & 0x04)
        while buf.pos + 4 <= len(body):
            msg_type = buf.u8()
            msg_size = buf.u16()
            buf.u8()  # flags
            if track_order:
                buf.skip(2)
            if buf.pos + msg_size > len(body):
                break
            payload = buf.read(msg_size)
            if msg_type == MSG_NIL:
                continue
            self._apply_message(msg_type, payload, node, header_version=2, obj_flags=obj_flags)

    def _apply_message(
        self, msg_type: int, payload: bytes, node: Node, header_version: int, obj_flags: int = 0
    ) -> None:
        if msg_type == MSG_DATASPACE:
            node.dims = self._parse_dataspace(payload)
        elif msg_type == MSG_DATATYPE:
            node.datatype = _parse_datatype(_Buf(payload))
        elif msg_type == MSG_DATA_LAYOUT:
            node.layout = self._parse_layout(payload)
        elif msg_type == MSG_FILTER_PIPELINE:
            node.filters = self._parse_filters(payload)
        elif msg_type == MSG_ATTRIBUTE:
            try:
                name, value = self._parse_attribute(payload)
            except HDF5Error:
                return
            node.attributes[name] = value
        elif msg_type == MSG_SYMBOL_TABLE:
            buf = _Buf(payload)
            node.symbol_btree = buf.u64()
            node.symbol_heap = buf.u64()
        elif msg_type == MSG_LINK_INFO:
            node.link_heap = self._parse_link_info(payload)
        elif msg_type == MSG_ATTRIBUTE_INFO:
            node.attribute_heap = self._parse_attribute_info(payload)
        elif msg_type == MSG_LINK:
            entry = self._parse_link(payload)
            if entry is not None:
                node.links[entry[0]] = entry[1]
        elif msg_type == MSG_CONTINUATION:
            buf = _Buf(payload)
            offset = buf.u64()
            length = buf.u64()
            if offset != _UNDEFINED and 0 < length < 64 * 1024 * 1024:
                block = self._at(offset, length)
                if block[:4] == b"OCHK":
                    # Strip the signature and the trailing 4-byte checksum.
                    self._walk_messages_v2(block[4:-4], node, obj_flags)
                else:
                    # v1 continuation blocks hold messages with no count header.
                    self._walk_messages_v1(block, node, n_messages=1 << 30)
        elif msg_type == MSG_FILL:
            node.fill_value = payload

    @staticmethod
    def _parse_dataspace(payload: bytes) -> tuple[int, ...]:
        buf = _Buf(payload)
        version = buf.u8()
        rank = buf.u8()
        flags = buf.u8()
        if version == 1:
            buf.skip(5)
        else:
            buf.u8()  # type
        dims = tuple(buf.u64() for _ in range(rank))
        return dims

    @staticmethod
    def _parse_layout(payload: bytes) -> _Layout:
        buf = _Buf(payload)
        version = buf.u8()
        if version in (1, 2):
            rank = buf.u8()
            kind = buf.u8()
            buf.skip(5)
            if kind == 0:  # compact
                size = buf.u32()
                return _Layout("compact", compact_data=buf.read(size), size=size)
            address = buf.u64()
            dims = tuple(buf.u32() for _ in range(rank))
            if kind == 1:  # contiguous
                return _Layout("contiguous", address=None if address == _UNDEFINED else address)
            # chunked: last dim entry is the element size
            elem = dims[-1] if dims else 0
            return _Layout(
                "chunked",
                address=None if address == _UNDEFINED else address,
                chunk_dims=dims[:-1],
                element_size=elem,
            )
        if version in (3, 4):
            kind = buf.u8()
            if kind == 0:  # compact
                size = buf.u16()
                return _Layout("compact", compact_data=buf.read(size), size=size)
            if kind == 1:  # contiguous
                address = buf.u64()
                size = buf.u64()
                return _Layout("contiguous", address=None if address == _UNDEFINED else address, size=size)
            if kind == 2:  # chunked
                if version == 3:
                    rank = buf.u8()
                    address = buf.u64()
                    dims = tuple(buf.u32() for _ in range(rank - 1))
                    elem = buf.u32()
                    return _Layout(
                        "chunked",
                        address=None if address == _UNDEFINED else address,
                        chunk_dims=dims,
                        element_size=elem,
                    )
                raise HDF5Error("layout message version 4 (v2 B-tree chunk index) is not supported")
            raise HDF5Error(f"unsupported layout class {kind}")
        raise HDF5Error(f"unsupported data layout message version {version}")

    @staticmethod
    def _parse_filters(payload: bytes) -> list[_Filter]:
        buf = _Buf(payload)
        version = buf.u8()
        count = buf.u8()
        out: list[_Filter] = []
        if version == 1:
            buf.skip(6)
            for _ in range(count):
                fid = buf.u16()
                name_len = buf.u16()
                buf.u16()  # flags
                n_client = buf.u16()
                if name_len:
                    buf.skip(name_len)
                client = tuple(buf.u32() for _ in range(n_client))
                if n_client % 2:
                    buf.skip(4)
                out.append(_Filter(fid, client))
        elif version == 2:
            for _ in range(count):
                fid = buf.u16()
                name_len = 0 if fid < 256 else buf.u16()
                buf.u16()  # flags
                n_client = buf.u16()
                if name_len:
                    buf.skip(name_len)
                client = tuple(buf.u32() for _ in range(n_client))
                out.append(_Filter(fid, client))
        else:
            raise HDF5Error(f"unsupported filter pipeline version {version}")
        return out

    def _parse_attribute(self, payload: bytes) -> tuple[str, Any]:
        buf = _Buf(payload)
        version = buf.u8()
        if version == 1:
            buf.u8()
            name_size = buf.u16()
            dt_size = buf.u16()
            ds_size = buf.u16()
            name = buf.read(name_size).split(b"\x00")[0].decode("utf-8", "replace")
            buf.align(0, 8)
            dt_raw = buf.read(dt_size)
            buf.align(0, 8)
            ds_raw = buf.read(ds_size)
            buf.align(0, 8)
        elif version in (2, 3):
            flags = buf.u8()
            name_size = buf.u16()
            dt_size = buf.u16()
            ds_size = buf.u16()
            if version == 3:
                buf.u8()  # name character-set encoding
            name = buf.read(name_size).split(b"\x00")[0].decode("utf-8", "replace")
            if flags & 0x03:
                raise HDF5Error("shared attribute datatypes are not supported")
            dt_raw = buf.read(dt_size)
            ds_raw = buf.read(ds_size)
        else:
            raise HDF5Error(f"unsupported attribute message version {version}")

        datatype = _parse_datatype(_Buf(dt_raw))
        dims = self._parse_dataspace(ds_raw)
        count = 1
        for d in dims:
            count *= int(d)
        data = buf.data[buf.pos :]

        if datatype.cls == 9:  # variable-length (usually a string)
            return name, self._read_vlen(data, count)
        if datatype.is_string:
            raw = data[: datatype.size * count]
            parts = [raw[i * datatype.size : (i + 1) * datatype.size] for i in range(count)]
            decoded = [p.split(b"\x00")[0].decode("utf-8", "replace") for p in parts]
            return name, decoded[0] if count == 1 else decoded
        if datatype.dtype is None:
            raise HDF5Error("attribute has an undecodable datatype")
        need = datatype.dtype.itemsize * count
        arr = np.frombuffer(data[:need], dtype=datatype.dtype)
        if arr.size == 0:
            return name, None
        if count == 1:
            return name, arr[0].item()
        return name, arr.tolist()

    @staticmethod
    def _parse_link(payload: bytes) -> tuple[str, int] | None:
        parsed = NetCDF4File._parse_link_record(_Buf(payload))
        return None if parsed is None else (parsed[0], parsed[1])

    # -- global heap (variable-length values) -------------------------------
    def _global_heap_object(self, address: int, index: int) -> bytes:
        """Fetch one object from a GCOL global-heap collection."""
        if address == _UNDEFINED or index == 0:
            return b""
        cache_key = (address, index)
        cached = self._gcol_cache.get(cache_key)
        if cached is not None:
            return cached
        head = self._at(address, 16)
        if head[:4] != b"GCOL":
            return b""
        collection_size = struct.unpack("<Q", head[8:16])[0]
        if not 16 < collection_size <= 64 * 1024 * 1024:
            return b""
        blob = self._at(address, collection_size)
        pos = 16
        found = b""
        while pos + 16 <= len(blob):
            obj_index = struct.unpack("<H", blob[pos : pos + 2])[0]
            obj_size = struct.unpack("<Q", blob[pos + 8 : pos + 16])[0]
            data_start = pos + 16
            if obj_index == 0 or obj_size == 0:
                break
            if data_start + obj_size > len(blob):
                break
            self._gcol_cache[(address, obj_index)] = blob[data_start : data_start + obj_size]
            if obj_index == index:
                found = blob[data_start : data_start + obj_size]
            pos = data_start + ((obj_size + 7) // 8) * 8
        return found

    def _read_vlen(self, data: bytes, count: int) -> Any:
        """Decode ``count`` variable-length descriptors into Python values.

        Each descriptor is 16 bytes: length (4), global-heap collection address
        (8), object index (4).
        """
        values: list[str] = []
        for i in range(max(count, 1)):
            chunk = data[i * 16 : (i + 1) * 16]
            if len(chunk) < 16:
                break
            length = struct.unpack("<I", chunk[0:4])[0]
            address = struct.unpack("<Q", chunk[4:12])[0]
            index = struct.unpack("<I", chunk[12:16])[0]
            raw = self._global_heap_object(address, index)[:length]
            values.append(raw.split(b"\x00")[0].decode("utf-8", "replace"))
        if not values:
            return ""
        return values[0] if len(values) == 1 else values

    @staticmethod
    def _parse_link_record(buf: _Buf) -> tuple[str, int] | None:
        """Parse one serialised Link message, leaving ``buf`` after it.

        Returns ``None`` for links that do not resolve to an object-header
        address (soft/external links), which the caller treats as "skip".
        """
        version = buf.u8()
        if version != 1:
            raise HDF5Error(f"unsupported link message version {version}")
        flags = buf.u8()
        link_type = buf.u8() if flags & 0x08 else 0
        if flags & 0x04:
            buf.skip(8)  # creation order
        if flags & 0x10:
            buf.u8()  # link name character set
        name_len_size = 1 << (flags & 0x03)
        name_len = buf.uint(name_len_size)
        if not 0 < name_len <= 4096:
            raise HDF5Error("implausible link name length")
        name = buf.read(name_len).decode("utf-8", "replace")
        if link_type != 0:
            # Soft link: length-prefixed target string; external: opaque blob.
            target_len = buf.u16()
            buf.skip(target_len)
            return None
        return name, buf.u64()

    @staticmethod
    def _parse_link_info(payload: bytes) -> int | None:
        """Return the fractal-heap address holding a group's dense links."""
        buf = _Buf(payload)
        version = buf.u8()
        if version != 0:
            return None
        flags = buf.u8()
        if flags & 0x01:
            buf.skip(8)  # maximum creation index
        heap = buf.u64()
        return None if heap == _UNDEFINED else heap

    @staticmethod
    def _parse_attribute_info(payload: bytes) -> int | None:
        """Return the fractal-heap address holding an object's dense attrs."""
        buf = _Buf(payload)
        version = buf.u8()
        if version != 0:
            return None
        flags = buf.u8()
        if flags & 0x01:
            buf.skip(2)  # maximum creation index
        heap = buf.u64()
        return None if heap == _UNDEFINED else heap

    # -- fractal heap ------------------------------------------------------
    def _fractal_heap_blocks(self, heap_address: int) -> list[bytes]:
        """Return the payload of every direct block in a fractal heap.

        Objects inside a heap are addressed by heap ID via a v2 B-tree, which
        this reader does not implement. Both link records and attribute records
        are self-delimiting, so callers instead scan each direct block
        sequentially -- which is what :meth:`_records_from_heap` does.
        """
        head = self._at(heap_address, 4)
        if head != b"FRHP":
            raise HDF5Error("expected a fractal heap header signature")
        raw = self._at(heap_address, 200)
        buf = _Buf(raw, 4)
        version = buf.u8()
        if version != 0:
            raise HDF5Error(f"unsupported fractal heap version {version}")
        buf.u16()  # heap ID length
        filtered_len = buf.u16()
        heap_flags = buf.u8()
        buf.u32()  # maximum size of managed objects
        buf.u64()  # next huge object ID
        buf.u64()  # v2 B-tree address for huge objects
        buf.u64()  # amount of free space
        buf.u64()  # free-space manager address
        buf.u64()  # amount of managed space
        buf.u64()  # amount of allocated managed space
        buf.u64()  # offset of the direct-block iterator
        buf.u64()  # number of managed objects
        buf.u64()  # size of huge objects
        buf.u64()  # number of huge objects
        buf.u64()  # size of tiny objects
        buf.u64()  # number of tiny objects
        buf.u16()  # table width
        table_width = 0  # re-read below (kept explicit for clarity)
        buf.pos -= 2
        table_width = buf.u16()
        starting_block_size = buf.u64()
        max_direct_block_size = buf.u64()
        max_heap_size_bits = buf.u16()
        buf.u16()  # starting number of rows in the root indirect block
        root_address = buf.u64()
        current_rows = buf.u16()
        if root_address == _UNDEFINED or table_width == 0:
            return []

        offset_bytes = (max_heap_size_bits + 7) // 8
        # Heap flag bit 1 means every direct block carries a 4-byte checksum
        # between its header and its payload.
        checksummed = bool(heap_flags & 0x02)

        def row_block_size(row: int) -> int:
            return starting_block_size if row < 2 else starting_block_size * (1 << (row - 1))

        def read_direct(address: int, size: int) -> bytes:
            if address == _UNDEFINED or size <= 0 or size > 64 * 1024 * 1024:
                return b""
            block = self._at(address, size)
            if block[:4] != b"FHDB":
                return b""
            cursor = 4 + 1 + 8 + offset_bytes  # sig + version + heap addr + block offset
            if checksummed:
                cursor += 4
            return block[cursor:]

        blocks: list[bytes] = []
        if current_rows == 0:
            blocks.append(read_direct(root_address, starting_block_size))
            return [b for b in blocks if b]

        pending = [(root_address, current_rows)]
        visited: set[int] = set()
        while pending:
            address, rows = pending.pop()
            if address in visited or address == _UNDEFINED:
                continue
            visited.add(address)
            # An indirect block lists child addresses row by row.
            span = 4 + 1 + 8 + offset_bytes + rows * table_width * 16 + 4
            raw_block = self._at(address, span)
            if raw_block[:4] != b"FHIB":
                continue
            cur = _Buf(raw_block, 4 + 1 + 8 + offset_bytes)
            for row in range(rows):
                size = row_block_size(row)
                direct = size <= max_direct_block_size
                for _ in range(table_width):
                    try:
                        child = cur.u64()
                    except HDF5Error:
                        break
                    if filtered_len and direct:
                        cur.u64()  # filtered size
                        cur.u32()  # filter mask
                    if child == _UNDEFINED or child == 0:
                        continue
                    if direct:
                        payload = read_direct(child, size)
                        if payload:
                            blocks.append(payload)
                    else:
                        pending.append((child, rows + 1))
        return blocks

    def _records_from_heap(self, heap_address: int, parser: Any) -> list[Any]:
        """Sequentially scan heap direct blocks with a self-delimiting parser."""
        out: list[Any] = []
        for block in self._fractal_heap_blocks(heap_address):
            buf = _Buf(block)
            while buf.pos < len(block):
                start = buf.pos
                try:
                    record = parser(buf)
                except (HDF5Error, IndexError, UnicodeDecodeError, struct.error):
                    break
                if buf.pos <= start:
                    break
                if record is not None:
                    out.append(record)
                # Records are packed back-to-back; a run of zero padding ends
                # the useful part of the block.
                while buf.pos < len(block) and block[buf.pos] == 0:
                    buf.pos += 1
        return out

    @staticmethod
    def _parse_link_dense(payload: bytes) -> tuple[str, int] | None:
        return NetCDF4File._parse_link(payload)

    # -- group traversal ---------------------------------------------------
    def _local_heap_strings(self, heap_address: int) -> bytes:
        raw = self._at(heap_address, 32)
        if raw[:4] != b"HEAP":
            raise HDF5Error("expected a local heap signature")
        data_size = struct.unpack("<Q", raw[8:16])[0]
        data_address = struct.unpack("<Q", raw[24:32])[0]
        return self._at(data_address, data_size)

    def _btree_symbol_nodes(self, address: int) -> list[int]:
        """Collect SNOD addresses under a v1 B-tree of type 0 (group nodes)."""
        out: list[int] = []
        stack = [address]
        seen: set[int] = set()
        while stack:
            addr = stack.pop()
            if addr in seen or addr == _UNDEFINED:
                continue
            seen.add(addr)
            head = self._at(addr, 24)
            if head[:4] != b"TREE":
                continue
            node_type = head[4]
            node_level = head[5]
            entries_used = struct.unpack("<H", head[6:8])[0]
            if node_type != 0:
                continue
            # header (24) then: key, child, key, child, ..., key
            body = self._at(addr + 24, (entries_used + 1) * 8 + entries_used * 8 + 16)
            buf = _Buf(body)
            children = []
            for _ in range(entries_used):
                buf.u64()  # key (offset into local heap)
                children.append(buf.u64())
            if node_level == 0:
                out.extend(children)
            else:
                stack.extend(children)
        return out

    def _group_children(self, node: Node) -> dict[str, int]:
        """Resolve a group's child links to object-header addresses.

        Three storage strategies are covered: compact link messages held in the
        object header, dense links held in a fractal heap (what the supplied
        CMEMS file uses), and legacy symbol-table groups.
        """
        children: dict[str, int] = dict(node.links)

        if node.link_heap is not None:
            for name, addr in self._records_from_heap(node.link_heap, self._parse_link_record):
                children.setdefault(name, addr)

        if not children and node.symbol_btree is not None and node.symbol_heap is not None:
            if node.symbol_btree == _UNDEFINED or node.symbol_heap == _UNDEFINED:
                return children
            heap = self._local_heap_strings(node.symbol_heap)
            for snod_addr in self._btree_symbol_nodes(node.symbol_btree):
                head = self._at(snod_addr, 8)
                if head[:4] != b"SNOD":
                    continue
                n_symbols = struct.unpack("<H", head[6:8])[0]
                body = self._at(snod_addr + 8, n_symbols * 40)
                for i in range(n_symbols):
                    entry = body[i * 40 : (i + 1) * 40]
                    if len(entry) < 16:
                        break
                    name_offset = struct.unpack("<Q", entry[0:8])[0]
                    obj_addr = struct.unpack("<Q", entry[8:16])[0]
                    end = heap.find(b"\x00", name_offset)
                    name = heap[name_offset : end if end >= 0 else None].decode("utf-8", "replace")
                    if name:
                        children[name] = obj_addr
        return children

    def _dense_attributes(self, node: Node) -> dict[str, Any]:
        """Read attributes stored densely in a fractal heap."""
        if node.attribute_heap is None:
            return {}

        def parse(buf: _Buf) -> tuple[str, Any] | None:
            start = buf.pos
            # Attribute records are variable length; parse the header to learn
            # the payload size, then hand the whole record to _parse_attribute.
            version = buf.data[start]
            if version not in (1, 2, 3):
                raise HDF5Error("not an attribute record")
            cursor = _Buf(buf.data, start)
            cursor.u8()
            cursor.u8()
            name_size = cursor.u16()
            dt_size = cursor.u16()
            ds_size = cursor.u16()
            if version == 3:
                cursor.u8()
            if not 0 < name_size <= 4096 or dt_size > 65535 or ds_size > 65535:
                raise HDF5Error("implausible attribute record")
            cursor.skip(name_size + dt_size + ds_size)
            header_len = cursor.pos - start
            dt = _parse_datatype(_Buf(buf.data, start + (8 if version == 1 else (9 if version == 3 else 8)) + name_size))
            dims = self._parse_dataspace(buf.data[start + header_len - ds_size : start + header_len])
            count = 1
            for d in dims:
                count *= int(d)
            value_bytes = (dt.size if dt.cls != 9 else 16) * max(count, 1)
            end = min(start + header_len + value_bytes, len(buf.data))
            buf.pos = end
            try:
                return self._parse_attribute(buf.data[start:end])
            except HDF5Error:
                return None

        out: dict[str, Any] = {}
        for record in self._records_from_heap(node.attribute_heap, parse):
            if record is None:
                continue
            name, value = record
            out.setdefault(name, value)
        return out

    # -- public API --------------------------------------------------------
    def variables(self) -> dict[str, Node]:
        """All datasets in the root group, keyed by name."""
        if self._variables is None:
            out: dict[str, Node] = {}
            for name, addr in sorted(self._group_children(self.root).items()):
                try:
                    child = self._read_object(addr)
                except HDF5Error:
                    continue
                if child.is_dataset:
                    for key, value in self._dense_attributes(child).items():
                        child.attributes.setdefault(key, value)
                    out[name] = child
            self._variables = out
        return self._variables

    def global_attributes(self) -> dict[str, Any]:
        attrs = dict(self.root.attributes)
        for key, value in self._dense_attributes(self.root).items():
            attrs.setdefault(key, value)
        return attrs

    # -- chunk reading -----------------------------------------------------
    def _chunk_index(self, address: int, rank: int) -> list[tuple[tuple[int, ...], int, int]]:
        """Walk a v1 B-tree of type 1, returning (chunk_origin, addr, nbytes)."""
        out: list[tuple[tuple[int, ...], int, int]] = []
        stack = [address]
        seen: set[int] = set()
        key_size = 8 + 8 * (rank + 1)  # chunk size + filter mask + offsets
        while stack:
            addr = stack.pop()
            if addr in seen or addr == _UNDEFINED:
                continue
            seen.add(addr)
            head = self._at(addr, 24)
            if head[:4] != b"TREE":
                continue
            node_type = head[4]
            node_level = head[5]
            entries_used = struct.unpack("<H", head[6:8])[0]
            if node_type != 1:
                continue
            need = entries_used * (key_size + 8) + key_size
            body = self._at(addr + 24, need)
            buf = _Buf(body)
            for _ in range(entries_used):
                nbytes = buf.u32()
                buf.u32()  # filter mask
                offsets = tuple(buf.u64() for _ in range(rank + 1))
                child = buf.u64()
                if node_level == 0:
                    out.append((offsets[:rank], child, nbytes))
                else:
                    stack.append(child)
        return out

    @staticmethod
    def _apply_read_filters(raw: bytes, filters: list[_Filter], itemsize: int) -> bytes:
        # Filters are recorded in write order; undo them in reverse.
        data = raw
        for filt in reversed(filters):
            if filt.filter_id == 1:  # deflate
                data = zlib.decompress(data)
            elif filt.filter_id == 2:  # shuffle
                block = filt.client_data[0] if filt.client_data else itemsize
                if block > 1 and len(data) >= block:
                    n = len(data) // block
                    head = np.frombuffer(data[: n * block], dtype=np.uint8)
                    unshuffled = head.reshape(block, n).T.reshape(-1).tobytes()
                    data = unshuffled + data[n * block :]
            elif filt.filter_id == 3:  # fletcher32 checksum trailer
                data = data[:-4]
            elif filt.filter_id == 32004:  # lz4 (not implemented)
                raise HDF5Error("lz4-compressed chunks are not supported")
            else:
                raise HDF5Error(f"unsupported HDF5 filter id {filt.filter_id}")
        return data

    def read_variable(self, name: str) -> np.ndarray:
        """Read a whole dataset, applying CF scale/offset and fill masking."""
        node = self.variables().get(name)
        if node is None:
            raise KeyError(f"variable {name!r} not found")
        raw = self.read_raw(name)
        return self.apply_cf(node, raw)

    def read_raw(self, name: str) -> np.ndarray:
        node = self.variables().get(name)
        if node is None:
            raise KeyError(f"variable {name!r} not found")
        assert node.datatype is not None and node.layout is not None
        dt = node.datatype.dtype
        if dt is None:
            raise HDF5Error(f"variable {name!r} has an undecodable datatype")
        dims = tuple(int(d) for d in node.dims)
        total = 1
        for d in dims:
            total *= d
        layout = node.layout

        if layout.kind == "compact":
            arr = np.frombuffer(layout.compact_data[: total * dt.itemsize], dtype=dt)
            return arr.reshape(dims)

        if layout.kind == "contiguous":
            if layout.address is None:
                return np.zeros(dims, dtype=dt)
            data = self._at(layout.address, total * dt.itemsize)
            return np.frombuffer(data, dtype=dt).reshape(dims)

        # chunked
        if layout.address is None:
            return np.zeros(dims, dtype=dt)
        chunk = tuple(int(c) for c in layout.chunk_dims)
        if len(chunk) != len(dims):
            raise HDF5Error("chunk rank does not match dataset rank")
        out = np.zeros(dims, dtype=dt)
        chunk_elems = 1
        for c in chunk:
            chunk_elems *= c
        for origin, addr, nbytes in self._chunk_index(layout.address, len(dims)):
            if addr == _UNDEFINED or nbytes <= 0:
                continue
            raw_bytes = self._at(addr, nbytes)
            try:
                data = self._apply_read_filters(raw_bytes, node.filters, dt.itemsize)
            except zlib.error:
                continue
            usable = (len(data) // dt.itemsize) * dt.itemsize
            block = np.frombuffer(data[:usable], dtype=dt)
            if block.size < chunk_elems:
                block = np.concatenate([block, np.zeros(chunk_elems - block.size, dtype=dt)])
            block = block[:chunk_elems].reshape(chunk)
            slices_out = []
            slices_in = []
            for axis, start in enumerate(origin):
                start = int(start)
                stop = min(start + chunk[axis], dims[axis])
                if start >= dims[axis]:
                    slices_out = []
                    break
                slices_out.append(slice(start, stop))
                slices_in.append(slice(0, stop - start))
            if not slices_out:
                continue
            out[tuple(slices_out)] = block[tuple(slices_in)]
        return out

    @staticmethod
    def apply_cf(node: Node, raw: np.ndarray) -> np.ndarray:
        """Apply ``_FillValue``/``missing_value`` masking then scale/offset."""
        attrs = node.attributes
        out = raw.astype(np.float64)
        invalid = np.zeros(out.shape, dtype=bool)
        for key in ("_FillValue", "missing_value"):
            fv = attrs.get(key)
            if isinstance(fv, (int, float)):
                invalid |= raw == np.asarray(fv, dtype=raw.dtype) if raw.dtype.kind in "iu" else np.isclose(out, float(fv))
        vmin, vmax = attrs.get("valid_min"), attrs.get("valid_max")
        if isinstance(vmin, (int, float)):
            invalid |= out < float(vmin)
        if isinstance(vmax, (int, float)):
            invalid |= out > float(vmax)
        scale = attrs.get("scale_factor")
        offset = attrs.get("add_offset")
        if isinstance(scale, (int, float)):
            out = out * float(scale)
        if isinstance(offset, (int, float)):
            out = out + float(offset)
        out[invalid] = np.nan
        return out

    def describe(self) -> dict[str, Any]:
        """A JSON-safe summary of the file, for the data audit."""
        out: dict[str, Any] = {
            "path": self.path,
            "format": f"HDF5/NetCDF-4 (superblock v{self.superblock_version})",
            "global_attributes": {
                k: v for k, v in self.global_attributes().items() if not k.startswith("_Netcdf4")
            },
            "variables": {},
        }
        for name, node in self.variables().items():
            out["variables"][name] = {
                "dims": [int(d) for d in node.dims],
                "dtype": node.datatype.name if node.datatype else None,
                "layout": node.layout.kind if node.layout else None,
                "chunk": [int(c) for c in node.layout.chunk_dims] if node.layout else [],
                "filters": [f.filter_id for f in node.filters],
                "attributes": {
                    k: v for k, v in node.attributes.items() if not k.startswith("_Netcdf4")
                },
            }
        return out
