"""Minimal ECMA-335 (.NET assembly metadata) reader.

We can't use .NET reflection from pure Python, so this parses the raw PE/CLI
metadata tables directly -- the same information reflection would give us,
just read off disk instead of through the CLR. This replaces guessing a mod's
identity from string *shapes* in the raw bytes (fragile: case-sensitive GUID
assumptions, no way to tell a plugin's own metadata from an unrelated
attribute's constructor args sitting nearby) with resolving the exact
CustomAttribute row whose type name matches, then decoding only that row's
argument blob.

Scope is deliberately narrow: enough of the format to enumerate custom
attributes (with their resolved type name) and to disassemble method bodies
looking for `ldstr` / `call`|`callvirt` pairs. Not a general-purpose .NET
metadata library.
"""

import bisect
import struct

_PE_SIG_OFFSET = 0x3C
_DATA_DIR_CLI_HEADER = 14


class MetadataError(Exception):
    pass


def _u1(data, off):
    return data[off]


def _u2(data, off):
    return struct.unpack_from("<H", data, off)[0]


def _u4(data, off):
    return struct.unpack_from("<I", data, off)[0]


def _u8(data, off):
    return struct.unpack_from("<Q", data, off)[0]


class _Sections:
    def __init__(self, entries):
        # entries: list of (virtual_address, virtual_size, raw_size, raw_ptr)
        self._entries = entries

    def rva_to_offset(self, rva):
        for va, vsize, raw_size, raw_ptr in self._entries:
            size = max(vsize, raw_size)
            if va <= rva < va + size:
                return raw_ptr + (rva - va)
        raise MetadataError(f"RVA 0x{rva:x} not in any section")


def _parse_pe_sections(data):
    if data[:2] != b"MZ":
        raise MetadataError("not a PE file (missing MZ header)")
    e_lfanew = _u4(data, _PE_SIG_OFFSET)
    if data[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        raise MetadataError("missing PE signature")

    coff_off = e_lfanew + 4
    num_sections = _u2(data, coff_off + 2)
    opt_header_size = _u2(data, coff_off + 16)
    opt_off = coff_off + 20

    magic = _u2(data, opt_off)
    if magic == 0x10B:
        num_rva_sizes_off = opt_off + 92
    elif magic == 0x20B:
        num_rva_sizes_off = opt_off + 108
    else:
        raise MetadataError(f"unrecognized optional header magic 0x{magic:x}")

    num_rva_sizes = _u4(data, num_rva_sizes_off)
    data_dirs_off = num_rva_sizes_off + 4
    if _DATA_DIR_CLI_HEADER >= num_rva_sizes:
        raise MetadataError("no CLI header data directory (not a managed assembly)")
    cli_rva = _u4(data, data_dirs_off + _DATA_DIR_CLI_HEADER * 8)
    cli_size = _u4(data, data_dirs_off + _DATA_DIR_CLI_HEADER * 8 + 4)
    if cli_rva == 0:
        raise MetadataError("empty CLI header RVA (not a managed assembly)")

    section_table_off = opt_off + opt_header_size
    entries = []
    for i in range(num_sections):
        rec = section_table_off + i * 40
        virtual_size = _u4(data, rec + 8)
        virtual_address = _u4(data, rec + 12)
        raw_size = _u4(data, rec + 16)
        raw_ptr = _u4(data, rec + 20)
        entries.append((virtual_address, virtual_size, raw_size, raw_ptr))

    return _Sections(entries), cli_rva, cli_size


# ── Coded indices: (tag_bit_width, [table indices in tag order]) ──────────
# Table indices per ECMA-335 Partition II §22.
TYPEDEF, TYPEREF, TYPESPEC = 0x02, 0x01, 0x1B
FIELD, METHODDEF, PARAM = 0x04, 0x06, 0x08
MODULE, MODULEREF, MEMBERREF = 0x00, 0x1A, 0x0A
ASSEMBLY, ASSEMBLYREF = 0x20, 0x23
CUSTOMATTRIBUTE = 0x0C

CODED_INDEXES = {
    "TypeDefOrRef": (2, [TYPEDEF, TYPEREF, TYPESPEC]),
    "HasConstant": (2, [FIELD, PARAM, 0x17]),
    "HasCustomAttribute": (5, [
        METHODDEF, FIELD, TYPEREF, TYPEDEF, PARAM, 0x09, MEMBERREF, MODULE,
        0x0E, 0x17, 0x14, 0x11, MODULEREF, TYPESPEC, ASSEMBLY, ASSEMBLYREF,
        0x26, 0x27, 0x28, 0x2A, 0x2C, 0x2B,
    ]),
    "HasFieldMarshal": (1, [FIELD, PARAM]),
    "HasDeclSecurity": (2, [TYPEDEF, METHODDEF, ASSEMBLY]),
    "MemberRefParent": (3, [TYPEDEF, TYPEREF, MODULEREF, METHODDEF, TYPESPEC]),
    "HasSemantics": (1, [0x14, 0x17]),
    "MethodDefOrRef": (1, [METHODDEF, MEMBERREF]),
    "MemberForwarded": (1, [FIELD, METHODDEF]),
    "Implementation": (2, [0x26, ASSEMBLYREF, 0x27]),
    "CustomAttributeType": (3, [MODULE, MODULE, METHODDEF, MEMBERREF, MODULE]),
    "ResolutionScope": (2, [MODULE, MODULEREF, ASSEMBLYREF, TYPEREF]),
    "TypeOrMethodDef": (1, [TYPEDEF, METHODDEF]),
}

# ── Table row layouts ───────────────────────────────────────────────────
# Field kinds: 'u1','u2','u4' fixed width; 'str'/'guid'/'blob' heap indices;
# ('tbl', idx) simple index into another table; ('coded', name) coded index.
TABLE_LAYOUTS = {
    0x00: [("u2",), ("str",), ("guid",), ("guid",), ("guid",)],                       # Module
    0x01: [("coded", "ResolutionScope"), ("str",), ("str",)],                          # TypeRef
    0x02: [("u4",), ("str",), ("str",), ("coded", "TypeDefOrRef"),                     # TypeDef
           ("tbl", FIELD), ("tbl", METHODDEF)],
    0x03: [("tbl", FIELD)],                                                           # FieldPtr
    0x04: [("u2",), ("str",), ("blob",)],                                             # Field
    0x05: [("tbl", METHODDEF)],                                                       # MethodPtr
    0x06: [("u4",), ("u2",), ("u2",), ("str",), ("blob",), ("tbl", PARAM)],            # MethodDef
    0x07: [("tbl", PARAM)],                                                           # ParamPtr
    0x08: [("u2",), ("u2",), ("str",)],                                               # Param
    0x09: [("tbl", TYPEDEF), ("coded", "TypeDefOrRef")],                              # InterfaceImpl
    0x0A: [("coded", "MemberRefParent"), ("str",), ("blob",)],                        # MemberRef
    0x0B: [("u2",), ("coded", "HasConstant"), ("blob",)],                             # Constant
    0x0C: [("coded", "HasCustomAttribute"), ("coded", "CustomAttributeType"), ("blob",)],  # CustomAttribute
    0x0D: [("coded", "HasFieldMarshal"), ("blob",)],                                  # FieldMarshal
    0x0E: [("u2",), ("coded", "HasDeclSecurity"), ("blob",)],                          # DeclSecurity
    0x0F: [("u2",), ("u4",), ("tbl", TYPEDEF)],                                        # ClassLayout
    0x10: [("u4",), ("tbl", FIELD)],                                                  # FieldLayout
    0x11: [("blob",)],                                                                # StandAloneSig
    0x12: [("tbl", TYPEDEF), ("tbl", 0x14)],                                          # EventMap
    0x13: [("tbl", 0x14)],                                                            # EventPtr
    0x14: [("u2",), ("str",), ("coded", "TypeDefOrRef")],                             # Event
    0x15: [("tbl", TYPEDEF), ("tbl", 0x17)],                                          # PropertyMap
    0x16: [("tbl", 0x17)],                                                            # PropertyPtr
    0x17: [("u2",), ("str",), ("blob",)],                                             # Property
    0x18: [("u2",), ("tbl", METHODDEF), ("coded", "HasSemantics")],                    # MethodSemantics
    0x19: [("tbl", TYPEDEF), ("coded", "MethodDefOrRef"), ("coded", "MethodDefOrRef")],  # MethodImpl
    0x1A: [("str",)],                                                                 # ModuleRef
    0x1B: [("blob",)],                                                                # TypeSpec
    0x1C: [("u2",), ("coded", "MemberForwarded"), ("str",), ("tbl", MODULEREF)],       # ImplMap
    0x1D: [("u4",), ("tbl", FIELD)],                                                  # FieldRVA
    0x1E: [("u4",), ("u4",)],                                                         # ENCLog
    0x1F: [("u4",)],                                                                  # ENCMap
    0x20: [("u4",), ("u2",), ("u2",), ("u2",), ("u2",), ("u4",), ("blob",), ("str",), ("str",)],  # Assembly
    0x21: [("u4",)],                                                                  # AssemblyProcessor
    0x22: [("u4",), ("u4",), ("u4",)],                                                # AssemblyOS
    0x23: [("u2",), ("u2",), ("u2",), ("u2",), ("u4",), ("blob",), ("str",), ("str",), ("blob",)],  # AssemblyRef
    0x24: [("u4",), ("tbl", ASSEMBLYREF)],                                            # AssemblyRefProcessor
    0x25: [("u4",), ("u4",), ("u4",), ("tbl", ASSEMBLYREF)],                          # AssemblyRefOS
    0x26: [("u4",), ("str",), ("blob",)],                                            # File
    0x27: [("u4",), ("u4",), ("str",), ("str",), ("coded", "Implementation")],         # ExportedType
    0x28: [("u4",), ("u4",), ("str",), ("coded", "Implementation")],                   # ManifestResource
    0x29: [("tbl", TYPEDEF), ("tbl", TYPEDEF)],                                       # NestedClass
    0x2A: [("u2",), ("u2",), ("coded", "TypeOrMethodDef"), ("str",)],                  # GenericParam
    0x2B: [("coded", "MethodDefOrRef"), ("blob",)],                                   # MethodSpec
    0x2C: [("tbl", 0x2A), ("coded", "TypeDefOrRef")],                                 # GenericParamConstraint
}

MAX_TABLE_INDEX = 0x2C


class AssemblyMetadata:
    """Parsed metadata for one .NET assembly: enough to enumerate custom
    attributes (resolved to their declaring type's name) and disassemble
    method bodies."""

    def __init__(self, data):
        self._data = data
        sections, cli_rva, _ = _parse_pe_sections(data)
        self._sections = sections
        cli_off = sections.rva_to_offset(cli_rva)
        meta_rva = _u4(data, cli_off + 8)
        self._meta_off = sections.rva_to_offset(meta_rva)
        self._parse_metadata_root()
        self._parse_tables_stream()
        self._method_owner_starts = None

    # ── Root / streams ──────────────────────────────────────────────
    def _parse_metadata_root(self):
        data, off = self._data, self._meta_off
        if _u4(data, off) != 0x424A5342:
            raise MetadataError("bad metadata root signature")
        version_len = _u4(data, off + 12)
        stream_hdrs_off = off + 16 + version_len
        num_streams = _u2(data, stream_hdrs_off + 2)
        pos = stream_hdrs_off + 4
        self._streams = {}
        for _ in range(num_streams):
            stream_off = _u4(data, pos)
            stream_size = _u4(data, pos + 4)
            name_start = pos + 8
            name_end = data.index(b"\0", name_start)
            name = data[name_start:name_end].decode("ascii")
            self._streams[name] = (off + stream_off, stream_size)
            pos = name_end + 1
            pos = (pos + 3) & ~3  # 4-byte align

    def _heap(self, name):
        return self._streams.get(name)

    def _string_at(self, index):
        if index == 0:
            return ""
        heap = self._heap("#Strings")
        if not heap:
            return ""
        start = heap[0] + index
        end = self._data.index(b"\0", start)
        return self._data[start:end].decode("utf-8", errors="replace")

    def _blob_at(self, index):
        heap = self._heap("#Blob")
        if not heap or index == 0:
            return b""
        off = heap[0] + index
        length, off = self._read_compressed_u32(off)
        return self._data[off:off + length]

    def _read_compressed_u32(self, off):
        b0 = self._data[off]
        if b0 & 0x80 == 0:
            return b0, off + 1
        if b0 & 0xC0 == 0x80:
            val = ((b0 & 0x3F) << 8) | self._data[off + 1]
            return val, off + 2
        val = ((b0 & 0x1F) << 24) | (self._data[off + 1] << 16) | \
              (self._data[off + 2] << 8) | self._data[off + 3]
        return val, off + 4

    # ── #~ tables stream ────────────────────────────────────────────
    def _parse_tables_stream(self):
        heap = self._heap("#~") or self._heap("#-")
        if not heap:
            raise MetadataError("no #~ tables stream")
        base = heap[0]
        heap_sizes = _u1(self._data, base + 6)
        self._str_idx_size = 4 if heap_sizes & 0x01 else 2
        self._guid_idx_size = 4 if heap_sizes & 0x02 else 2
        self._blob_idx_size = 4 if heap_sizes & 0x04 else 2

        valid = _u8(self._data, base + 8)
        pos = base + 24
        row_counts = {}
        for idx in range(MAX_TABLE_INDEX + 1):
            if valid & (1 << idx):
                row_counts[idx] = _u4(self._data, pos)
                pos += 4
        self._row_counts = row_counts

        self._table_offsets = {}
        self._table_row_sizes = {}
        for idx in sorted(row_counts):
            row_size = self._row_size(TABLE_LAYOUTS[idx])
            self._table_offsets[idx] = pos
            self._table_row_sizes[idx] = row_size
            pos += row_size * row_counts[idx]

    def _coded_index_size(self, name):
        tag_bits, tables = CODED_INDEXES[name]
        max_rows = max((self._row_counts.get(t, 0) for t in tables), default=0)
        return 4 if max_rows >= (1 << (16 - tag_bits)) else 2

    def _field_size(self, field):
        kind = field[0]
        if kind == "u1":
            return 1
        if kind == "u2":
            return 2
        if kind == "u4":
            return 4
        if kind == "str":
            return self._str_idx_size
        if kind == "guid":
            return self._guid_idx_size
        if kind == "blob":
            return self._blob_idx_size
        if kind == "tbl":
            return 4 if self._row_counts.get(field[1], 0) >= (1 << 16) else 2
        if kind == "coded":
            return self._coded_index_size(field[1])
        raise MetadataError(f"unknown field kind {kind}")

    def _row_size(self, layout):
        return sum(self._field_size(f) for f in layout)

    def row_count(self, table_idx):
        return self._row_counts.get(table_idx, 0)

    def _read_field(self, data, off, field):
        kind = field[0]
        if kind == "u1":
            return _u1(data, off), off + 1
        if kind == "u2":
            return _u2(data, off), off + 2
        if kind == "u4":
            return _u4(data, off), off + 4
        if kind in ("str", "guid", "blob"):
            size = self._field_size(field)
            val = _u2(data, off) if size == 2 else _u4(data, off)
            return val, off + size
        if kind == "tbl":
            size = self._field_size(field)
            val = _u2(data, off) if size == 2 else _u4(data, off)
            return val, off + size
        if kind == "coded":
            size = self._field_size(field)
            raw = _u2(data, off) if size == 2 else _u4(data, off)
            tag_bits, tables = CODED_INDEXES[field[1]]
            tag_mask = (1 << tag_bits) - 1
            tag = raw & tag_mask
            row_idx = raw >> tag_bits
            table = tables[tag] if tag < len(tables) else None
            return (table, row_idx), off + size
        raise MetadataError(f"unknown field kind {kind}")

    def read_row(self, table_idx, row_number):
        """1-based row_number, per ECMA-335 convention (0 means null)."""
        if row_number == 0 or table_idx not in self._table_offsets:
            return None
        layout = TABLE_LAYOUTS[table_idx]
        row_size = self._table_row_sizes[table_idx]
        off = self._table_offsets[table_idx] + (row_number - 1) * row_size
        data = self._data
        values = []
        for field in layout:
            val, off = self._read_field(data, off, field)
            values.append(val)
        return values

    def iter_rows(self, table_idx):
        for i in range(1, self.row_count(table_idx) + 1):
            yield i, self.read_row(table_idx, i)

    # ── Semantic helpers ─────────────────────────────────────────────
    def type_name_of_typeref(self, row_idx):
        row = self.read_row(TYPEREF, row_idx)
        if not row:
            return None, None
        _scope, name_idx, ns_idx = row
        return self._string_at(name_idx), self._string_at(ns_idx)

    def type_name_of_typedef(self, row_idx):
        row = self.read_row(TYPEDEF, row_idx)
        if not row:
            return None, None
        _flags, name_idx, ns_idx, _extends, _fl, _ml = row
        return self._string_at(name_idx), self._string_at(ns_idx)

    def _owning_typedef(self, method_row_idx):
        """MethodDef rows don't store their declaring type; ownership is
        implied by TypeDef.MethodList ranges, so find which TypeDef's method
        range contains this MethodDef row index. Cached + binary-searched
        since this is looked up once per custom attribute / method scanned,
        and a linear rescan per lookup is quadratic on large assemblies."""
        if self._method_owner_starts is None:
            starts = [
                (self.read_row(TYPEDEF, i)[5], i)
                for i in range(1, self.row_count(TYPEDEF) + 1)
            ]
            starts.sort()
            self._method_owner_starts = starts

        starts = self._method_owner_starts
        idx = bisect.bisect_right(starts, (method_row_idx, float("inf"))) - 1
        return starts[idx][1] if idx >= 0 else None

    def custom_attribute_type_name(self, ca_row):
        """Resolve a CustomAttribute row's Type coded index to the declaring
        type's (name, namespace), regardless of whether the constructor is a
        MethodDef (declared in this assembly) or a MemberRef (declared in a
        referenced assembly, e.g. BepInEx.dll)."""
        _parent, (table, row_idx), _value = ca_row
        if table == METHODDEF:
            type_idx = self._owning_typedef(row_idx)
            return self.type_name_of_typedef(type_idx) if type_idx else (None, None)
        if table == MEMBERREF:
            mr = self.read_row(MEMBERREF, row_idx)
            if not mr:
                return None, None
            (parent_table, parent_idx), _name, _sig = mr
            if parent_table == TYPEREF:
                return self.type_name_of_typeref(parent_idx)
            if parent_table == TYPEDEF:
                return self.type_name_of_typedef(parent_idx)
            return None, None
        return None, None

    def custom_attributes(self):
        """Yields (type_name, type_namespace, raw_value_blob) for every
        CustomAttribute row in the assembly."""
        for _i, row in self.iter_rows(CUSTOMATTRIBUTE):
            if row is None:
                continue
            name, ns = self.custom_attribute_type_name(row)
            if name is None:
                continue
            value_blob = self._blob_at(row[2])
            yield name, ns, value_blob

    def decode_fixed_string_args(self, blob, count):
        """Decode the first `count` fixed string arguments from a custom
        attribute's argument blob (prolog 0x0001 + SerString-encoded args).
        Assumes a constructor of `count` leading string parameters, true for
        both BepInPlugin(guid,name,version) and BepInDependency(guid,ver)."""
        if len(blob) < 2 or blob[0:2] != b"\x01\x00":
            return None
        off = 2
        out = []
        for _ in range(count):
            if off >= len(blob):
                return None
            first = blob[off]
            if first == 0xFF:  # null string
                out.append(None)
                off += 1
                continue
            length, off = self._read_compressed_u32_blob(blob, off)
            out.append(blob[off:off + length].decode("utf-8", errors="replace"))
            off += length
        return out

    def _read_compressed_u32_blob(self, blob, off):
        b0 = blob[off]
        if b0 & 0x80 == 0:
            return b0, off + 1
        if b0 & 0xC0 == 0x80:
            val = ((b0 & 0x3F) << 8) | blob[off + 1]
            return val, off + 2
        val = ((b0 & 0x1F) << 24) | (blob[off + 1] << 16) | \
              (blob[off + 2] << 8) | blob[off + 3]
        return val, off + 4

    # ── Method bodies (IL) ───────────────────────────────────────────
    def method_rva(self, method_row_idx):
        row = self.read_row(METHODDEF, method_row_idx)
        return row[0] if row else 0

    def method_il_bytes(self, method_row_idx):
        rva = self.method_rva(method_row_idx)
        if rva == 0:
            return b""
        off = self._sections.rva_to_offset(rva)
        header_byte = self._data[off]
        if header_byte & 0x03 == 0x02:  # tiny format
            code_size = header_byte >> 2
            return self._data[off + 1:off + 1 + code_size]
        if header_byte & 0x03 == 0x03:  # fat format
            flags_size = _u2(self._data, off)
            code_size = _u4(self._data, off + 4)
            header_size_words = (flags_size >> 12) & 0x0F
            body_off = off + header_size_words * 4
            return self._data[body_off:body_off + code_size]
        return b""

    def user_string_at(self, index):
        heap = self._heap("#US")
        if not heap or index == 0:
            return ""
        off = heap[0] + index
        length, off = self._read_compressed_u32(off)
        if length == 0:
            return ""
        # UTF-16LE, minus the trailing single flag byte per ECMA-335 §24.2.4.
        text_len = (length - 1) // 2
        return self._data[off:off + text_len * 2].decode("utf-16-le", errors="replace")

    def method_name(self, method_row_idx):
        row = self.read_row(METHODDEF, method_row_idx)
        return self._string_at(row[3]) if row else None

    def resolve_method_name(self, token):
        """Resolve a call/callvirt operand token (top byte = table, low 3
        bytes = row index) to the called method's name, whether it's a
        MethodDef (defined in this assembly) or a MemberRef (defined in a
        referenced assembly)."""
        table = (token >> 24) & 0xFF
        row_idx = token & 0x00FFFFFF
        if table == METHODDEF:
            row = self.read_row(METHODDEF, row_idx)
            return self._string_at(row[3]) if row else None
        if table == MEMBERREF:
            row = self.read_row(MEMBERREF, row_idx)
            return self._string_at(row[1]) if row else None
        return None


# ── CIL opcode operand sizes ─────────────────────────────────────────────
# Just enough to correctly step over every instruction (most single-byte
# opcodes take no operand); we only need to *recognize* ldstr/dup/call/
# callvirt, everything else just has to be skipped by the right amount so
# the byte offset stays aligned.
_OP_SIZE_1 = {0x0E, 0x0F, 0x10, 0x11, 0x12, 0x13, 0x1F, 0x2B, 0x2C, 0x2D, 0x2E,
              0x2F, 0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0xDE}
_OP_SIZE_2 = {0xFE09, 0xFE0A, 0xFE0B, 0xFE0C, 0xFE0D, 0xFE0E}
_OP_SIZE_4 = {0x20, 0x22, 0x27, 0x28, 0x29, 0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3D,
              0x3E, 0x3F, 0x40, 0x41, 0x42, 0x43, 0x44, 0x6F, 0x70, 0x71, 0x72,
              0x73, 0x74, 0x75, 0x79, 0x7B, 0x7C, 0x7D, 0x7E, 0x7F, 0x80, 0x81,
              0x8C, 0x8D, 0x8F, 0xA3, 0xA4, 0xA5, 0xC2, 0xC6, 0xD0, 0xDD,
              0xFE06, 0xFE07, 0xFE15, 0xFE16, 0xFE1C}
_OP_SIZE_8 = {0x21, 0x23}
_OP_SIZE_FE_1 = {0xFE12, 0xFE19}
_SWITCH = 0x45

LDSTR, DUP, CALL, CALLVIRT = 0x72, 0x25, 0x28, 0x6F


def _il_instructions(il):
    """Yields (opcode, operand_bytes) for each instruction, in order. Opcode
    is the single byte, or 0xFE00|second_byte for two-byte-prefixed opcodes."""
    off = 0
    n = len(il)
    while off < n:
        op = il[off]
        off += 1
        if op == 0xFE:
            op2 = il[off]
            off += 1
            op = 0xFE00 | op2
            size = 4 if op in _OP_SIZE_4 else (2 if op in _OP_SIZE_2 else
                   (1 if op in _OP_SIZE_FE_1 else 0))
        elif op == _SWITCH:
            count = struct.unpack_from("<I", il, off)[0]
            off += 4
            operand = il[off:off + count * 4]
            off += count * 4
            yield op, operand
            continue
        else:
            size = (4 if op in _OP_SIZE_4 else 8 if op in _OP_SIZE_8 else
                   1 if op in _OP_SIZE_1 else 0)
        operand = il[off:off + size]
        off += size
        yield op, operand


def scan_property_setters(meta, method_row_idx, wanted_setters):
    """Walk one method body's IL for the `dup / ldstr <str> / call set_X`
    pattern Roslyn emits for C# object-initializer syntax (`new T { X = ... }`),
    and return {property_name: string_value} for whichever of `wanted_setters`
    it finds. Best-effort: relies on this being a stable compiler lowering,
    not general data-flow analysis."""
    il = meta.method_il_bytes(method_row_idx)
    if not il:
        return {}

    found = {}
    pending_string = None
    for op, operand in _il_instructions(il):
        if op == LDSTR:
            token = struct.unpack("<I", operand)[0]
            index = token & 0x00FFFFFF
            pending_string = meta.user_string_at(index)
        elif op in (CALL, CALLVIRT):
            if pending_string is not None:
                token = struct.unpack("<I", operand)[0]
                name = meta.resolve_method_name(token)
                if name and name.startswith("set_"):
                    prop = name[4:]
                    if prop in wanted_setters:
                        found[prop] = pending_string
            pending_string = None
        elif op != DUP:
            pending_string = None
    return found


def find_property_set_cluster(meta, required, optional=()):
    """Scan every method body in the assembly for the dup/ldstr/call-setter
    pattern, looking for one method that sets all of `required` (e.g. Guid
    and Version). Returns the matched dict, or None if zero or more than one
    method qualifies -- ambiguity means skipping rather than guessing which
    one is the real mod metadata."""
    wanted = set(required) | set(optional)
    matches = []
    for row_idx in range(1, meta.row_count(METHODDEF) + 1):
        try:
            found = scan_property_setters(meta, row_idx, wanted)
        except (IndexError, struct.error):
            continue
        if all(key in found for key in required):
            matches.append(found)
    if len(matches) == 1:
        return matches[0]
    return None


def load(dll_path):
    data = dll_path.read_bytes() if hasattr(dll_path, "read_bytes") else open(dll_path, "rb").read()
    return AssemblyMetadata(data)
