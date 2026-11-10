from __future__ import annotations

from dataclasses import dataclass
import mmap
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from spd_model_injector.core.spd import (
    ProgressCallback,
    _copy_range,
    _ensure_output_is_not_source,
    _normalize_newlines,
    _replace_connect_component,
    _source_signature,
    scan_spd_inventory,
)
from spd_model_injector.core.settings_transfer_structure import build_structure_edits


@dataclass(frozen=True)
class CopySettingsOptions:
    stackup: bool = True
    channel_activation: bool = True
    component_models: bool = True
    ports: bool = True


def _unique(records, attribute: str, label: str) -> dict:
    result = {}
    for record in records:
        name = getattr(record, attribute)
        if not name or name in result:
            raise ValueError(f"Duplicate or empty {label}: {name!r}.")
        result[name] = record
    return result


def _text(handle, start: int, end: int) -> str:
    handle.seek(start)
    raw = handle.read(end - start)
    if len(raw) != end - start:
        raise ValueError("SPD changed while reading settings; reload both files.")
    return _normalize_newlines(raw.decode("utf-8"))


def _connect_header(handle, record) -> tuple[str, int]:
    header = _text(handle, record.connect_line_start_offset, record.connect_line_end_offset).rstrip("\n")
    end = record.connect_line_end_offset
    while end < record.connect_block_end_offset:
        handle.seek(end)
        raw = handle.readline()
        if not raw.lstrip().startswith(b"+"):
            break
        header += " " + raw.decode("utf-8").strip()[1:].strip()
        end += len(raw)
    return header + "\n", end


def _pins(handle, record) -> dict[str, tuple[str, str]]:
    _, start = _connect_header(handle, record)
    text = _text(handle, start, record.connect_block_end_offset)
    lines = text.splitlines()
    if not lines or lines[-1].strip().lower() != ".endc":
        raise ValueError(f"Missing .EndC for RefDes {record.refdes_name}.")
    pins = {}
    for line in lines[:-1]:
        if not line.strip() or line.lstrip().startswith("*"):
            continue
        match = re.fullmatch(r"\s*(\S+)\s+(\$Package\.Node\S+)\s*", line, re.I)
        if not match or match[1] in pins:
            raise ValueError(f"Unsupported or duplicate pin in RefDes {record.refdes_name}: {line.strip()}")
        pin, token = match.groups()
        pins[pin] = (token, token.split("::", 1)[1] if "::" in token else "")
    return pins


def _part_entries(path: Path) -> dict[str, tuple[int, int, str]]:
    entries = {}
    with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        for match in re.finditer(rb"(?im)^\.Part[ \t]+([^\s]+)[^\r\n]*(?:\r?\n[ \t]*\+[^\r\n]*)*(?:\r?\n|$)", data):
            name = match[1].decode("utf-8")
            if name in entries:
                raise ValueError(f"Duplicate .Part definition: {name}.")
            entries[name] = (match.start(), match.end(), _normalize_newlines(match[0].decode("utf-8")))
    return entries


def _model_edits(source, target, src_inv, dst_inv, src_refs, dst_refs):
    source_models = _unique(src_inv.blocks, "component_name", "source component model")
    target_models = _unique(dst_inv.blocks, "component_name", "target component model")
    if set(src_refs) != set(dst_refs):
        missing = sorted(set(src_refs) - set(dst_refs))[:5]
        extra = sorted(set(dst_refs) - set(src_refs))[:5]
        raise ValueError(f"RefDes names/count differ. Missing in target: {missing}; target-only: {extra}.")
    if not source_models:
        raise ValueError("Source contains no component models to copy.")
    edits = []
    with source.open("rb") as src, target.open("rb") as dst:
        for name, source_record in src_refs.items():
            target_record = dst_refs[name]
            source_pins, target_pins = _pins(src, source_record), _pins(dst, target_record)
            if {p: n for p, (_, n) in source_pins.items()} != {p: n for p, (_, n) in target_pins.items()}:
                raise ValueError(f"RefDes pin/NET mismatch: {name}.")
            model = source_models.get(source_record.component_name)
            if model is None or not set(source_pins).issubset(model.ext_nodes):
                raise ValueError(f"Missing or incompatible component model for RefDes {name}.")
            # Keep target placement/pins and non-model flags; Usage is the model activation setting.
            source_header, _ = _connect_header(src, source_record)
            target_header, header_end = _connect_header(dst, target_record)
            header = _replace_connect_component(target_header, source_record.component_name)
            header = re.sub(r"\s+Usage\s*=\s*\S+", "", header, flags=re.I).rstrip("\r\n")
            usage = re.search(r"\bUsage\s*=\s*(\S+)", source_header, re.I)
            if usage:
                header += f" Usage = {usage[1]}"
            edits.append((target_record.connect_line_start_offset, header_end, header + "\n"))
        additions = []
        for name, model in source_models.items():
            if not model.ext_nodes or len(set(model.ext_nodes)) != len(model.ext_nodes):
                raise ValueError(f"Invalid ExtNode pins for component model {name}.")
            text = _text(src, model.block_start_offset, model.block_end_offset)
            if re.search(r"(?im)^\s*\.(?:include|inc|lib)\b", text):
                raise ValueError(f"Model {name} references an external .include/.lib file. Embed its model before copying settings.")
            if name in target_models:
                existing = target_models[name]
                if model.ext_nodes != existing.ext_nodes:
                    raise ValueError(f"Component ExtNode names/order differ: {name}.")
                edits.append((existing.body_start_offset, existing.body_end_offset,
                              _text(src, model.body_start_offset, model.body_end_offset)))
            else:
                additions.append(text if text.endswith("\n") else text + "\n")
    assignments = [name for name in src_refs if src_refs[name].component_name != dst_refs[name].component_name]
    if additions or assignments:
        source_parts, target_parts = _part_entries(source), _part_entries(target)
        for refdes in assignments:
            new_name = src_refs[refdes].component_name
            old_part = target_parts.get(dst_refs[refdes].component_name)
            new_part = target_parts.get(new_name) if new_name in target_models else source_parts.get(new_name)
            if old_part is None or new_part is None or old_part[2].split()[2:] != new_part[2].split()[2:]:
                raise ValueError(f"Component assignment .Part geometry differs for RefDes {refdes}; copy stopped.")
    if additions:
        extra_models = source_models.keys() - target_models.keys()
        if not dst_inv.blocks or not target_parts or any(name not in source_parts or name in target_parts for name in extra_models):
            raise ValueError("New component models require unique .Part definitions and an existing target model library.")
        part_offset = max(end for _, end, _ in target_parts.values())
        edits.append((part_offset, part_offset, "".join(source_parts[name][2] for name in sorted(extra_models))))
        model_offset = max(block.block_end_offset for block in dst_inv.blocks)
        edits.append((model_offset, model_offset, "\n" + "".join(additions)))
    return edits, f"Copied {len(source_models)} component models and assignments/activation for {len(src_refs)} RefDes instances."


def _port_text(path, inventory) -> str:
    if inventory.port_section_start_offset is None and inventory.port_section_end_offset is None:
        if inventory.port_insertion_offset is None:
            raise ValueError("Missing or malformed Port/NetList section.")
        return ".Port\n.EndPort\n"
    if inventory.port_section_start_offset is None or inventory.port_section_end_offset is None or inventory.port_insertion_offset is None:
        raise ValueError("Malformed or multiple Port sections.")
    with path.open("rb") as handle:
        text = _text(handle, inventory.port_section_start_offset, inventory.port_section_end_offset)
    lines = text.splitlines()
    if lines[0].strip() != ".Port" or lines[-1].strip() != ".EndPort":
        raise ValueError("Unsupported Port section header/end marker.")
    names = set()
    terminals = None
    current_terminal = None
    declared = set()
    for line in lines[1:-1]:
        if not line.strip() or line.lstrip().startswith("*"):
            continue
        header = re.match(r"^(Port\d+\S*)(?:\s|$)", line, re.I)
        if header:
            if terminals is not None and (not terminals["positive"] or not terminals["negative"]):
                raise ValueError("Each Port must have nonempty positive and negative terminals.")
            if header[1] in names:
                raise ValueError(f"Duplicate Port name: {header[1]}.")
            names.add(header[1])
            terminals = {"positive": [], "negative": []}
            declared = set()
            current_terminal = None
            continue
        if terminals is None or not line.lstrip().startswith("+"):
            raise ValueError(f"Unsupported Port record: {line.strip()}")
        content = line.lstrip()[1:].strip()
        terminal = re.match(r"(Positive|Negative)Terminal\b\s*(.*)", content, re.I)
        if terminal:
            current_terminal, content = terminal[1].lower(), terminal[2]
            if current_terminal in declared:
                raise ValueError("Duplicate Port terminal declaration.")
            declared.add(current_terminal)
        tokens = content.split()
        if current_terminal is None or any(not re.fullmatch(r"\$Package\.Node\S+", token, re.I) for token in tokens):
            raise ValueError("Port terminals must reference identifiable Package.Node pins.")
        terminals[current_terminal].extend(tokens)
    if terminals is not None and (not terminals["positive"] or not terminals["negative"]):
        raise ValueError("Each Port must have nonempty positive and negative terminals.")
    if len(names) != len(inventory.port_records):
        raise ValueError("Port records are ambiguous; copy stopped.")
    return text


def _port_models(text: str, references: dict) -> str:
    lines = []
    for line in text.splitlines(keepends=True):
        if re.match(r"^Port\d", line, re.I):
            instance = re.search(r'\bGenFromCktInstance\s*=\s*"([^"]+)"', line, re.I)
            model = re.search(r'\bGenFromCktModel\s*=\s*"([^"]+)"', line, re.I)
            if instance or model:
                if not instance or not model or instance[1] not in references:
                    raise ValueError("Port refers to a missing or ambiguous RefDes/model.")
                name = references[instance[1]].component_name
                line = line[:model.start(1)] + name + line[model.end(1):]
        lines.append(line)
    return "".join(lines)


def _port_edits(source, target, src_inv, dst_inv, src_refs, dst_refs, copy_models):
    text = _port_text(source, src_inv)
    _port_text(target, dst_inv)
    wanted = {token for line in text.splitlines() if line.lstrip().startswith("+")
              for token in re.findall(r"\$Package\.Node\S+", line, re.I)}
    node_map: dict[str, set[str]] = {}
    with source.open("rb") as src, target.open("rb") as dst:
        for name, record in src_refs.items():
            source_pins = _pins(src, record)
            used = {pin: value for pin, value in source_pins.items()
                    if value[0] in wanted or value[0].split("::", 1)[0] in wanted}
            if not used:
                continue
            if name not in dst_refs:
                raise ValueError(f"Port RefDes missing in target: {name}.")
            target_pins = _pins(dst, dst_refs[name])
            for pin, (old, net) in used.items():
                if pin not in target_pins or not net or target_pins[pin][1] != net:
                    raise ValueError(f"Port pin/NET mismatch: {name}.{pin}.")
                new = target_pins[pin][0]
                for key in (old, old.split("::", 1)[0]):
                    node_map.setdefault(key, set()).add(new)
    for token in wanted:
        if len(node_map.get(token, ())) != 1:
            raise ValueError(f"Port node has no unique RefDes/pin match: {token}.")
    text = "".join(re.sub(r"\$Package\.Node\S+", lambda match: next(iter(node_map[match[0]])), line, flags=re.I)
                   if line.lstrip().startswith("+") else line for line in text.splitlines(keepends=True))
    text = _port_models(text, src_refs if copy_models else dst_refs)
    start = dst_inv.port_section_start_offset
    end = dst_inv.port_section_end_offset
    if start is None:
        start = end = dst_inv.port_insertion_offset
    return [(start, end, text)], f"Copied {len(src_inv.port_records)} ports and remapped {len(wanted)} terminal node references."


def copy_spd_settings(
    source_path: str | Path,
    target_path: str | Path,
    output_path: str | Path,
    options: CopySettingsOptions,
    *,
    target_signature: tuple[int, int, int, int] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> list[str]:
    """Validate the complete transfer before atomically writing a new target SPD."""
    source, target, output = Path(source_path), Path(target_path), Path(output_path)
    if not any((options.stackup, options.channel_activation, options.component_models, options.ports)):
        raise ValueError("Select at least one setting to copy.")
    _ensure_output_is_not_source(source, output)
    _ensure_output_is_not_source(target, output)
    _ensure_output_is_not_source(source, target)
    signatures = (_source_signature(source), _source_signature(target))
    if target_signature is not None and target_signature != signatures[1]:
        raise ValueError("Target SPD changed since loading. Reload before copying settings.")

    def progress(message, current=0, total=0):
        if progress_callback:
            progress_callback(message, current, total)

    progress("Validating layer structure and all NET names/counts")
    edits, summary = build_structure_edits(source, target, copy_stackup=options.stackup,
                                           copy_activation=options.channel_activation)
    if options.component_models or options.ports:
        progress("Scanning source models and connections")
        src_inv = scan_spd_inventory(source)
        progress("Scanning target models and connections")
        dst_inv = scan_spd_inventory(target)
        if dst_inv.port_insertion_offset is None:
            raise ValueError("Malformed or multiple Port sections in target; copy stopped.")
        src_refs = _unique(src_inv.refdes_records, "refdes_name", "source RefDes")
        dst_refs = _unique(dst_inv.refdes_records, "refdes_name", "target RefDes")
        if options.component_models:
            progress("Validating component models and RefDes pins")
            changes, detail = _model_edits(source, target, src_inv, dst_inv, src_refs, dst_refs)
            edits.extend(changes)
            summary.append(detail)
        if options.ports:
            progress("Matching Port terminals to target RefDes/pins/NETs")
            changes, detail = _port_edits(source, target, src_inv, dst_inv, src_refs, dst_refs, options.component_models)
            edits.extend(changes)
            summary.append(detail)
        elif options.component_models and dst_inv.port_section_start_offset is not None:
            with target.open("rb") as handle:
                for port in dst_inv.port_records:
                    text = _text(handle, port.header_start_offset, port.header_end_offset)
                    updated = _port_models(text, src_refs)
                    if updated != text:
                        edits.append((port.header_start_offset, port.header_end_offset, updated))

    edits.sort(key=lambda edit: (edit[0], edit[1]))
    cursor = 0
    for start, end, _ in edits:
        if not cursor <= start <= end <= signatures[1][2]:
            raise ValueError("Overlapping or stale settings ranges; copy stopped.")
        cursor = end
    temporary_path = None
    try:
        progress("Validation passed. Writing new SPD")
        with target.open("rb") as src, NamedTemporaryFile(mode="wb", dir=output.parent,
                prefix=f".{output.name}.", suffix=".tmp", delete=False) as dst:
            temporary_path = Path(dst.name)
            cursor, pending_cr = 0, False
            for start, end, text in edits:
                pending_cr = _copy_range(src, dst, cursor, start, pending_cr)
                if pending_cr:
                    dst.write(b"\n")
                    pending_cr = False
                dst.write(text.encode("utf-8"))
                cursor = end
            pending_cr = _copy_range(src, dst, cursor, None, pending_cr)
            if pending_cr:
                dst.write(b"\n")
            dst.flush()
            os.fsync(dst.fileno())
        if signatures != (_source_signature(source), _source_signature(target)):
            raise ValueError("An SPD changed during settings copy; no output was replaced. Reload both files.")
        _ensure_output_is_not_source(source, output)
        _ensure_output_is_not_source(target, output)
        os.replace(temporary_path, output)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    progress("Settings copied", signatures[1][2], signatures[1][2])
    return summary
