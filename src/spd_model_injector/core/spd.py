from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import re
from typing import Callable, Mapping, Sequence


_PARTIAL_RE = re.compile(r"^\.PartialCkt\s+(.+?)\s+ExtNode\s*=\s*(.*)$", re.IGNORECASE)
_PARTIAL_START_RE = re.compile(r"^\.PartialCkt(?:\s|$)", re.IGNORECASE)
_END_PARTIAL_RE = re.compile(r"^\.EndPartialCkt(?:\s|$)", re.IGNORECASE)
_CONNECT_RE = re.compile(r"^\.Connect\s+(\S+)\s+(\S+)(?:\s+(.*))?$", re.IGNORECASE)
_END_CONNECT_RE = re.compile(r"^\.EndC(?:\s|$)", re.IGNORECASE)
# Connection entries use numeric pins for discrete parts and alphanumeric pins
# (for example ``A10`` or ``LGA_A10``) for DUT/LGA packages.  Requiring the
# package-node token avoids mistaking ``Usage = ...`` or ``.EndC`` for a pin.
_CONNECT_NODE_RE = re.compile(r"^\s*[A-Za-z0-9_./+-]+\s+\$Package\.Node\S*(?:\s|$)", re.IGNORECASE)
_USAGE_RE = re.compile(r"\bUsage\s*=\s*(\S+)", re.IGNORECASE)
_USAGE_ASSIGNMENT_RE = re.compile(r"\s+Usage\s*=\s*\S+", re.IGNORECASE)

_CHUNK_SIZE = 1024 * 1024
ProgressCallback = Callable[[str, int, int], None]


@dataclass(frozen=True)
class PartialCktBlock:
    component_name: str
    ext_nodes: list[str]
    start_line: int
    end_line: int
    block_start_offset: int
    body_start_offset: int
    body_end_offset: int
    block_end_offset: int
    header_lines: list[str]
    source_component_name: str | None = None
    clone_source_name: str | None = None

    @property
    def port_count(self) -> int:
        return len(self.ext_nodes)


@dataclass(frozen=True)
class RefDesRecord:
    component_name: str
    refdes_name: str
    activation_status: str
    connect_line_start_offset: int | None = None
    connect_line_end_offset: int | None = None
    connect_line: str | None = None
    net_name: str = ""


@dataclass(frozen=True)
class SpdInventory:
    blocks: list[PartialCktBlock]
    refdes_records: list[RefDesRecord]
    refdes_by_component: dict[str, list[RefDesRecord]] = field(init=False)

    def __post_init__(self) -> None:
        grouped: dict[str, list[RefDesRecord]] = {}
        for record in self.refdes_records:
            grouped.setdefault(record.component_name, []).append(record)
        object.__setattr__(self, "refdes_by_component", grouped)


def scan_spd(path: str | Path, progress_callback: ProgressCallback | None = None) -> list[PartialCktBlock]:
    return scan_spd_inventory(path, progress_callback).blocks


def scan_spd_inventory(path: str | Path, progress_callback: ProgressCallback | None = None) -> SpdInventory:
    """Scan an SPD file for PartialCkt blocks and RefDes links without loading bodies into memory."""
    spd_path = Path(path)
    blocks: list[PartialCktBlock] = []
    refdes_records: list[RefDesRecord] = []
    line_no = 0
    file_size = spd_path.stat().st_size
    _report_progress(progress_callback, "Opening SPD file", 0, file_size)

    with spd_path.open("rb", buffering=_CHUNK_SIZE) as handle:
        active_connect: RefDesRecord | None = None
        active_connect_net_seen = False
        while True:
            line_start = handle.tell()
            raw_line = handle.readline()
            if not raw_line:
                break
            line_no += 1
            line_text = _decode_line(raw_line)

            if active_connect is not None:
                if _END_CONNECT_RE.match(_strip_newline(line_text)):
                    refdes_records.append(active_connect)
                    active_connect = None
                    active_connect_net_seen = False
                    continue
                # Use the first node entry that actually carries a ``::Net``;
                # some DUT/LGA blocks have unannotated pins before the net-bearing entry.
                if not active_connect_net_seen and _CONNECT_NODE_RE.match(line_text) and "::" in line_text:
                    net_name = line_text.split("::", 1)[1].strip()
                    if net_name:
                        active_connect = _replace_record_net_name(active_connect, net_name)
                        active_connect_net_seen = True
                # A malformed block may omit .EndC; preserve the old scanner's
                # behavior by allowing a new .Connect to terminate the prior one.
                if _CONNECT_RE.match(_strip_newline(line_text)):
                    refdes_records.append(active_connect)
                    active_connect = None
                    active_connect_net_seen = False

            if _PARTIAL_START_RE.match(line_text):
                block_start_offset = line_start
                start_line = line_no
                header_lines = [_strip_newline(line_text)]
                body_start_offset = handle.tell()
                pending: tuple[int, bytes, str, int] | None = None

                while True:
                    next_start = handle.tell()
                    next_raw = handle.readline()
                    if not next_raw:
                        break
                    line_no += 1
                    next_text = _decode_line(next_raw)
                    if next_text.lstrip().startswith("+"):
                        header_lines.append(_strip_newline(next_text))
                        body_start_offset = handle.tell()
                        continue
                    pending = (next_start, next_raw, next_text, line_no)
                    break

                body_end_offset = body_start_offset
                end_line = line_no
                block_end_offset = body_start_offset

                if pending is not None:
                    current_start, current_raw, current_text, current_line = pending
                    while True:
                        if _END_PARTIAL_RE.match(current_text):
                            body_end_offset = current_start
                            end_line = current_line
                            block_end_offset = current_start + len(current_raw)
                            break
                        current_start = handle.tell()
                        current_raw = handle.readline()
                        if not current_raw:
                            body_end_offset = handle.tell()
                            block_end_offset = handle.tell()
                            break
                        line_no += 1
                        current_line = line_no
                        current_text = _decode_line(current_raw)

                component_name = _parse_component_name(header_lines[0])
                blocks.append(
                    PartialCktBlock(
                        component_name=component_name,
                        ext_nodes=_parse_ext_nodes(header_lines),
                        start_line=start_line,
                        end_line=end_line,
                        block_start_offset=block_start_offset,
                        body_start_offset=body_start_offset,
                        body_end_offset=body_end_offset,
                        block_end_offset=block_end_offset,
                        header_lines=header_lines,
                    )
                )
                _report_progress(
                    progress_callback,
                    f"Found {component_name} at line {start_line}",
                    block_end_offset,
                    file_size,
                )
                continue

            record = _parse_connect_line(line_text, start_offset=line_start, end_offset=line_start + len(raw_line))
            if record is not None:
                active_connect = record
                active_connect_net_seen = False

        if active_connect is not None:
            refdes_records.append(active_connect)

    _report_progress(
        progress_callback,
        f"Scan complete: {len(blocks)} PartialCkt blocks, {len(refdes_records)} RefDes records",
        file_size,
        file_size,
    )
    return SpdInventory(blocks=blocks, refdes_records=refdes_records)


def read_block_body(path: str | Path, block: PartialCktBlock) -> str:
    with Path(path).open("rb") as handle:
        handle.seek(block.body_start_offset)
        raw = handle.read(block.body_end_offset - block.body_start_offset)
    return _normalize_newlines(raw.decode("utf-8", errors="replace"))


def write_spd_with_replacements(
    source_path: str | Path,
    output_path: str | Path,
    blocks: Sequence[PartialCktBlock],
    replacements: Mapping[str, str],
    *,
    refdes_component_changes: Mapping[str, str] | None = None,
    refdes_activation_status_changes: Mapping[str, str] | None = None,
    refdes_records: Sequence[RefDesRecord] | None = None,
    component_renames: Mapping[str, str] | None = None,
    component_clones: Mapping[str, str] | None = None,
) -> None:
    """Write a new SPD, replacing selected bodies and component identities."""
    source = Path(source_path)
    output = Path(output_path)
    _ensure_output_is_not_source(source, output)
    replacement_by_offset: dict[int, tuple[str, int]] = {
        block.body_start_offset: (_normalize_model_text(replacements[block.component_name]), block.body_end_offset)
        for block in blocks
        if block.component_name in replacements and block.clone_source_name is None
    }

    renames = dict(component_renames or {})
    clones = dict(component_clones or {})
    by_name = {block.component_name: block for block in blocks}
    by_name.update({block.source_component_name: block for block in blocks if block.source_component_name})
    identity_names = (*renames, *renames.values(), *clones, *clones.values())
    if any(not name or any(char.isspace() for char in name) for name in identity_names):
        raise ValueError("Component names must be non-empty single tokens.")
    effective_names = {block.component_name for block in blocks}
    known_names = {block.component_name for block in blocks}
    known_names.update(block.source_component_name for block in blocks if block.source_component_name)
    if any(source not in known_names for source in renames):
        raise ValueError("Unknown component rename source.")
    rename_targets = set(renames.values())
    renamed_current_names = {
        block.component_name
        for block in blocks
        if (block.source_component_name or block.component_name) in renames
    }
    collision = any(target in effective_names - renamed_current_names for target in rename_targets)
    if len(rename_targets) != len(renames) or collision:
        raise ValueError("Component rename collision.")
    if rename_targets & set(clones):
        raise ValueError("Component clone/rename collision.")
    part_entries = _find_part_entries(source_path, set(clones.values()) | set(renames))
    for new_name, source_name in clones.items():
        source_block = by_name.get(source_name)
        if source_block is None:
            raise ValueError(f"Unknown clone source: {source_name}")
        if new_name in known_names:
            # The UI may include the synthetic clone block in ``blocks``.
            if not any(block.component_name == new_name and block.clone_source_name == source_name for block in blocks):
                raise ValueError(f"Component already exists: {new_name}")
        part = part_entries.get(source_name)
        if part is None:
            raise ValueError(f"Clone source lacks a .Part definition: {source_name}")
        _, part_end, part_lines = part
        insertion = _rename_part_lines(part_lines, new_name)
        prior = replacement_by_offset.get(part_end)
        replacement_by_offset[part_end] = ((prior[0] if prior and prior[1] == part_end else "") + insertion, part_end)
        body = replacements.get(new_name)
        if body is None:
            body = replacements.get(source_block.component_name)
        if body is None:
            body = read_block_body(source_path, source_block)
        header = _rename_partial_header(source_block.header_lines, new_name)
        clone_text = "\n".join(header) + "\n" + _normalize_model_text(body) + ".EndPartialCkt\n"
        offset = source_block.block_end_offset
        prior = replacement_by_offset.get(offset)
        replacement_by_offset[offset] = ((prior[0] if prior and prior[1] == offset else "") + clone_text, offset)

    # Rename the header range for each identity-bearing PartialCkt block.
    for block in blocks:
        old_name = block.source_component_name or block.component_name
        new_name = renames.get(old_name)
        if new_name and old_name != new_name:
            header_text = "\n".join(_rename_partial_header(block.header_lines, new_name)) + "\n"
            prior = replacement_by_offset.get(block.block_start_offset)
            replacement_by_offset[block.block_start_offset] = (
                (prior[0] if prior and prior[1] == block.block_start_offset else "") + header_text,
                block.body_start_offset,
            )

    # Part definitions (including + continuation lines) are edited in-place.
    for old_name, new_name in renames.items():
        part = part_entries.get(old_name)
        if part is None:
            continue
        start, end, lines = part
        renamed_part = _rename_part_lines(lines, new_name)
        prior = replacement_by_offset.get(start)
        if prior is not None and prior[1] != start:
            raise ValueError("Overlapping component definition edits.")
        replacement_by_offset[start] = ((prior[0] if prior else "") + renamed_part, end)

    if refdes_component_changes or refdes_activation_status_changes or renames:
        component_changes = refdes_component_changes or {}
        status_changes = refdes_activation_status_changes or {}
        records = refdes_records if refdes_records is not None else scan_spd_inventory(source).refdes_records
        for record in records:
            new_component = component_changes.get(record.refdes_name) or renames.get(record.component_name)
            new_status = status_changes.get(record.refdes_name)
            if new_component is None and new_status is None:
                continue
            if record.connect_line_start_offset is None or record.connect_line_end_offset is None or record.connect_line is None:
                raise ValueError(f"RefDes {record.refdes_name} does not include .Connect line metadata.")
            connect_line = record.connect_line
            if new_component is not None:
                connect_line = _replace_connect_component(connect_line, new_component)
            if new_status is not None:
                connect_line = _replace_connect_activation_status(connect_line, new_status)
            replacement_by_offset[record.connect_line_start_offset] = (
                _normalize_newlines(connect_line),
                record.connect_line_end_offset,
            )

    with source.open("rb", buffering=_CHUNK_SIZE) as src, output.open("wb", buffering=_CHUNK_SIZE) as dst:
        cursor = 0
        pending_cr = False
        for start_offset in sorted(replacement_by_offset):
            replacement, end_offset = replacement_by_offset[start_offset]
            pending_cr = _copy_range(src, dst, cursor, start_offset, pending_cr)
            if pending_cr:
                dst.write(b"\n")
                pending_cr = False
            dst.write(replacement.encode("utf-8"))
            cursor = end_offset
        pending_cr = _copy_range(src, dst, cursor, None, pending_cr)
        if pending_cr:
            dst.write(b"\n")


def _ensure_output_is_not_source(source: Path, output: Path) -> None:
    if source.exists() and output.exists() and os.path.samefile(source, output):
        raise ValueError("Output path must differ from the source SPD path; writing would destroy the source.")
    if source.resolve() == output.resolve():
        raise ValueError("Output path must differ from the source SPD path; writing would destroy the source.")


def _copy_range(src, dst, start: int, end: int | None, pending_cr: bool) -> bool:
    """Copy [start, end) (or to EOF when end is None), LF-normalizing while streaming."""
    src.seek(start)
    remaining = None if end is None else end - start
    while remaining is None or remaining > 0:
        size = _CHUNK_SIZE if remaining is None else min(_CHUNK_SIZE, remaining)
        chunk = src.read(size)
        if not chunk:
            break
        if remaining is not None:
            remaining -= len(chunk)
        normalized, pending_cr = _normalize_chunk(chunk, pending_cr)
        dst.write(normalized)
    return pending_cr


def _normalize_chunk(chunk: bytes, pending_cr: bool) -> tuple[bytes, bool]:
    prefix = b""
    if pending_cr:
        if chunk[:1] == b"\n":
            chunk = chunk[1:]
        prefix = b"\n"
    trailing_cr = chunk.endswith(b"\r")
    if trailing_cr:
        chunk = chunk[:-1]
    normalized = chunk.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return prefix + normalized, trailing_cr


def _parse_component_name(first_header_line: str) -> str:
    match = _PARTIAL_RE.match(first_header_line)
    if not match:
        return first_header_line.split(maxsplit=1)[1].strip() if " " in first_header_line else first_header_line
    return match.group(1).strip()


_PART_RE = re.compile(r"^\.Part\s+(\S+)(.*)$", re.IGNORECASE)


def _rename_partial_header(header_lines: Sequence[str], name: str) -> list[str]:
    first = header_lines[0]
    match = _PARTIAL_RE.match(first)
    if match:
        first = f".PartialCkt {name} ExtNode = {match.group(2)}"
    else:
        parts = first.split(maxsplit=1)
        first = f"{parts[0]} {name}" if len(parts) > 1 else f"{parts[0]} {name}"
    return [first, *header_lines[1:]]


def _rename_part_lines(lines: Sequence[str], name: str) -> str:
    match = _PART_RE.match(lines[0])
    if not match:
        raise ValueError(f"Invalid .Part line: {lines[0]}")
    return "\n".join([f".Part {name}{match.group(2)}", *lines[1:]]) + "\n"


def _find_part_entries(path: str | Path, names: set[str]) -> dict[str, tuple[int, int, list[str]]]:
    """Collect requested .Part entries in one streaming pass."""
    found: dict[str, tuple[int, int, list[str]]] = {}
    if not names:
        return found
    with Path(path).open("rb") as handle:
        while True:
            start = handle.tell()
            raw = handle.readline()
            if not raw:
                break
            line = _strip_newline(_decode_line(raw))
            match = _PART_RE.match(line)
            if not match or match.group(1) not in names:
                continue
            part_name = match.group(1)
            lines = [line]
            end = handle.tell()
            while True:
                cont_start = handle.tell()
                cont_raw = handle.readline()
                if not cont_raw:
                    end = handle.tell()
                    break
                cont = _strip_newline(_decode_line(cont_raw))
                if not cont.lstrip().startswith("+"):
                    end = cont_start
                    handle.seek(cont_start)
                    break
                lines.append(cont)
                end = handle.tell()
            found[part_name] = (start, end, lines)
    return found


def _parse_ext_nodes(header_lines: Sequence[str]) -> list[str]:
    match = _PARTIAL_RE.match(header_lines[0])
    if not match:
        return []
    ext_text = [match.group(2)]
    for continuation in header_lines[1:]:
        stripped = continuation.lstrip()
        ext_text.append(stripped[1:] if stripped.startswith("+") else stripped)
    return [token for token in " ".join(ext_text).split() if token]


def _parse_connect_line(
    line: str,
    *,
    start_offset: int | None = None,
    end_offset: int | None = None,
) -> RefDesRecord | None:
    match = _CONNECT_RE.match(_strip_newline(line))
    if not match:
        return None
    return RefDesRecord(
        component_name=match.group(2),
        refdes_name=match.group(1),
        activation_status=_activation_status(match.group(3) or ""),
        connect_line_start_offset=start_offset,
        connect_line_end_offset=end_offset,
        connect_line=line,
    )


def _replace_record_net_name(record: RefDesRecord, net_name: str) -> RefDesRecord:
    return RefDesRecord(
        component_name=record.component_name,
        refdes_name=record.refdes_name,
        activation_status=record.activation_status,
        net_name=net_name,
        connect_line_start_offset=record.connect_line_start_offset,
        connect_line_end_offset=record.connect_line_end_offset,
        connect_line=record.connect_line,
    )


def _replace_connect_component(line: str, component_name: str) -> str:
    match = re.match(r"^(\.Connect\s+\S+\s+)(\S+)(.*)$", line, re.IGNORECASE | re.DOTALL)
    if not match:
        raise ValueError(f"Invalid .Connect line: {_strip_newline(line)}")
    return f"{match.group(1)}{component_name}{match.group(3)}"


def _replace_connect_activation_status(line: str, activation_status: str) -> str:
    usage = _usage_for_activation_status(activation_status)
    if usage is None:
        return _USAGE_ASSIGNMENT_RE.sub("", line, count=1)
    if _USAGE_ASSIGNMENT_RE.search(line):
        return re.sub(r"(Usage\s*=\s*)\S+", rf"\g<1>{usage}", line, count=1, flags=re.IGNORECASE)
    match = re.match(r"^(\.Connect\s+\S+\s+\S+)(.*)$", line, re.IGNORECASE | re.DOTALL)
    if not match:
        raise ValueError(f"Invalid .Connect line: {_strip_newline(line)}")
    return f"{match.group(1)} Usage = {usage}{match.group(2)}"


def _usage_for_activation_status(activation_status: str) -> str | None:
    if activation_status == "Automatic":
        return None
    if activation_status == "Enabled":
        return "0b1000"
    if activation_status == "Disabled":
        return "0b111000"
    raise ValueError(f"Unknown activation status: {activation_status}")


def _activation_status(attributes: str) -> str:
    match = _USAGE_RE.search(attributes)
    if not match:
        return "Automatic"
    usage = match.group(1)
    if usage == "0b1000":
        return "Enabled"
    if usage == "0b111000":
        return "Disabled"
    return "Unknown"


def _decode_line(raw_line: bytes) -> str:
    return raw_line.decode("utf-8", errors="replace")


def _report_progress(callback: ProgressCallback | None, message: str, current: int, total: int) -> None:
    if callback is not None:
        callback(message, current, total)


def _strip_newline(line: str) -> str:
    return line.rstrip("\r\n")


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _normalize_model_text(text: str) -> str:
    normalized = _normalize_newlines(text)
    return normalized if not normalized or normalized.endswith("\n") else normalized + "\n"
