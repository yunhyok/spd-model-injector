from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from spd_model_injector.core.spd import (
    ProgressCallback,
    _PROPERTY_RE,
    _copy_range,
    _ensure_output_is_not_source,
    _format_port_terminal,
    _normalize_newlines,
    _package_node_sort_key,
    read_spice_model,
    _source_signature,
    scan_spd_inventory,
)
from spd_model_injector.core.settings_transfer_structure import build_structure_edits
from spd_model_injector.core.spice import prepare_model_for_partialckt


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


def _model_edits(source, src_inv, dst_inv):
    source_models = _unique(src_inv.blocks, "component_name", "source component model")
    target_models = _unique(dst_inv.blocks, "component_name", "target component model")
    edits, empty = [], 0
    with source.open("rb") as src:
        for name in sorted(source_models.keys() & target_models.keys()):
            model, target = source_models[name], target_models[name]
            body = _text(src, model.body_start_offset, model.body_end_offset)
            if not any(line.strip() and not line.lstrip().startswith(("*", ";", "$")) for line in body.splitlines()):
                empty += 1
                continue
            if re.search(r"(?im)^\s*\.(?:include|inc|lib)\b", body):
                raise ValueError(f"Model {name} references an external .include/.lib file. Embed its model before copying settings.")
            try:
                spice = read_spice_model(source, model)
                nodes = target.ext_nodes
                if (not nodes or len({node.casefold() for node in nodes}) != len(nodes)
                        or any(not node.isprintable() or node.startswith(("*", "$"))
                               or re.search(r"[\s,();=\"']", node) for node in nodes)):
                    raise ValueError("Target ExtNode pins must be distinct valid SPICE node tokens.")
                if len(model.ext_nodes) != len(nodes):
                    raise ValueError(f"ExtNode pin count differs ({len(model.ext_nodes)} vs {len(nodes)}).")
                # Preserve shared pin names; match only renamed pins by their remaining order.
                source_names = {node.casefold() for node in model.ext_nodes}
                target_names = {node.casefold(): node for node in nodes}
                if source_names != target_names.keys():
                    remaining = iter(node for node in nodes if node.casefold() not in source_names)
                    mapped = [target_names[node.casefold()] if node.casefold() in target_names else next(remaining)
                              for node in model.ext_nodes]
                    body = prepare_model_for_partialckt(spice, mapped)
            except ValueError as exc:
                raise ValueError(f"Cannot apply component model {name}: {exc}") from exc
            edits.append((target.body_start_offset, target.body_end_offset, body))
    source_only = len(source_models.keys() - target_models.keys())
    target_only = len(target_models.keys() - source_models.keys())
    detail = (f"Applied {len(edits)} matching component models. Skipped {source_only} source-only and {empty} empty models; "
              f"preserved {target_only} target-only models and all target RefDes assignments/activation.")
    if not edits:
        raise ValueError("No nonempty source models have matching component names in the target. " + detail)
    return edits, detail


_PORT_NODE = re.compile(r"\$Package\.Node\S+", re.I)


@dataclass
class _Port:
    name: str
    header_index: int
    terminals: dict[str, list[tuple[int, list[str]]]]


def _port_lines(path, inventory) -> list[str]:
    if inventory.port_section_start_offset is None and inventory.port_section_end_offset is None:
        if inventory.port_insertion_offset is None:
            raise ValueError("Missing or malformed Port/NetList section.")
        return [".Port\n", ".EndPort\n"]
    if inventory.port_section_start_offset is None or inventory.port_section_end_offset is None or inventory.port_insertion_offset is None:
        raise ValueError("Malformed or multiple Port sections.")
    with path.open("rb") as handle:
        text = _text(handle, inventory.port_section_start_offset, inventory.port_section_end_offset)
    lines = text.splitlines(keepends=True)
    if lines[0].strip() != ".Port" or lines[-1].strip() != ".EndPort":
        raise ValueError("Unsupported Port section header/end marker.")
    return lines


def _port_text(path, inventory) -> tuple[list[str], list[_Port]]:
    lines = _port_lines(path, inventory)
    names = set()
    ports: list[_Port] = []
    current_terminal = None
    for index, line in enumerate(lines[1:-1], start=1):
        if not line.strip() or line.lstrip().startswith("*"):
            continue
        header = re.match(r"^(Port\d+\S*)(?:\s|$)", line, re.I)
        if header:
            if header[1] in names:
                raise ValueError(f"Duplicate Port name: {header[1]}.")
            names.add(header[1])
            ports.append(_Port(header[1], index, {}))
            current_terminal = None
            continue
        if not ports or not line.lstrip().startswith("+"):
            raise ValueError(f"Unsupported Port record: {line.strip()}")
        terminals = ports[-1].terminals
        content = line.lstrip()[1:].strip()
        terminal = re.match(r"(Positive|Negative)Terminal\b\s*(.*)", content, re.I)
        if terminal:
            current_terminal, content = terminal[1].lower(), terminal[2]
            if current_terminal in terminals:
                raise ValueError("Duplicate Port terminal declaration.")
            terminals[current_terminal] = []
        tokens = content.split()
        if current_terminal is None or any(not _PORT_NODE.fullmatch(token) for token in tokens):
            raise ValueError("Port terminals must reference identifiable Package.Node pins.")
        terminals[current_terminal].append((index, tokens))
    for port in ports:
        if not all(any(tokens for _, tokens in port.terminals.get(side, [])) for side in ("positive", "negative")):
            raise ValueError("Each Port must have nonempty positive and negative terminals.")
    if len(names) != len(inventory.port_records):
        raise ValueError("Port records are ambiguous; copy stopped.")
    return lines, ports


def _port_models(line: str, source_refs: dict, target_refs: dict) -> tuple[str, str | None]:
    name = line.split(maxsplit=1)[0]
    linked = [match for match in _PROPERTY_RE.finditer(line, len(name))
              if match[1].lower() in {"genfromcktinstance", "genfromcktmodel"}]
    if not linked:
        return line, None
    metadata = {match[1].lower(): match for match in linked}
    if len(linked) != 2 or len(metadata) != 2 or any(not re.fullmatch(r'"[^"]+"', match[2]) for match in linked):
        raise ValueError(f"Port {name} has malformed GenFromCktInstance/GenFromCktModel attributes.")
    instance = metadata["genfromcktinstance"][2][1:-1]
    if instance not in source_refs or instance not in target_refs:
        raise ValueError(f"Port {name} refers to a missing RefDes: {instance}.")
    model = metadata["genfromcktmodel"]
    component = target_refs[instance].component_name
    return line[:model.start(2)] + f'"{component}"' + line[model.end(2):], instance


def _check_port_nodes(name: str, terminals: dict[str, list[str]]) -> None:
    nodes = [re.split(r"!!|::", token, maxsplit=1)[0].casefold()
             for tokens in terminals.values() for token in tokens]
    if len(set(nodes)) != len(nodes):
        raise ValueError(f"Port {name} has duplicate or overlapping terminal nodes.")


def _generated_port_nets(instance, terminals, owners, source_pins) -> dict[str, str]:
    # Only complete instance-local NET selections can be regenerated as a recipe.
    if instance is None or instance not in source_pins:
        return {}
    nets = {}
    for side, tokens in terminals.items():
        group = [[(pin, net) for ref, pin, net in owners.get(token, []) if ref == instance] for token in tokens]
        if any(len(candidates) != 1 for candidates in group):
            return {}
        selected = {candidates[0][0] for candidates in group}
        unique_nets = {candidates[0][1] for candidates in group}
        if len(unique_nets) != 1 or "" in unique_nets:
            return {}
        net = next(iter(unique_nets))
        if selected != {pin for pin, (_, pin_net) in source_pins[instance].items() if pin_net == net}:
            return {}
        nets[side] = net
    return nets if nets["positive"] != nets["negative"] else {}


def _port_edits(source, target, src_inv, dst_inv, src_refs, dst_refs):
    lines, ports = _port_text(source, src_inv)
    _port_lines(target, dst_inv)
    wanted = {token for port in ports for rows in port.terminals.values() for _, tokens in rows for token in tokens}
    owners: dict[str, list[tuple[str, str, str]]] = {}
    source_pins = {}
    target_pins = {}
    node_count = 0
    with source.open("rb") as src, target.open("rb") as dst:

        def pins_for(name):
            if name not in dst_refs:
                raise ValueError(f"Port RefDes missing in target: {name}.")
            if name not in target_pins:
                target_pins[name] = _pins(dst, dst_refs[name])
            return target_pins[name]

        if wanted:
            for name, record in src_refs.items():
                pins = _pins(src, record)
                for pin, (token, net) in pins.items():
                    base = token.split("::", 1)[0]
                    for key in {token, base} & wanted:
                        owners.setdefault(key, []).append((name, pin, net))
                        source_pins[name] = pins

        for port in ports:
            lines[port.header_index], instance = _port_models(lines[port.header_index], src_refs, dst_refs)
            terminals = {side: [token for _, tokens in rows for token in tokens]
                         for side, rows in port.terminals.items()}
            _check_port_nodes(port.name, terminals)
            port_owners = owners
            if instance is not None:
                port_owners = {token: [owner for owner in owners.get(token, []) if owner[0] == instance] or owners.get(token, [])
                               for tokens in terminals.values() for token in tokens}
            nets = _generated_port_nets(instance, terminals, port_owners, source_pins)
            resolved = {}
            for side, tokens in terminals.items():
                if nets:
                    net = nets[side]
                    resolved[side] = sorted(
                        (token for token, pin_net in pins_for(instance).values() if pin_net == net),
                        key=_package_node_sort_key,
                    )
                    if not resolved[side]:
                        raise ValueError(f"Port {port.name} {side} NET {net} is missing on target RefDes {instance}.")
                else:
                    mapped = []
                    for token in tokens:
                        matches = port_owners.get(token, [])
                        if len(matches) != 1:
                            raise ValueError(f"Port node has no unique RefDes/pin match: {token}.")
                        name, pin, net = matches[0]
                        target_pin = pins_for(name).get(pin)
                        if target_pin is None or not net or target_pin[1] != net:
                            raise ValueError(f"Port pin/NET mismatch: {name}.{pin}.")
                        mapped.append(target_pin[0])
                    resolved[side] = mapped
            _check_port_nodes(port.name, resolved)
            for side, tokens in resolved.items():
                rows = port.terminals[side]
                node_count += len(tokens)
                if len(tokens) == len(terminals[side]):
                    new_tokens = iter(tokens)
                    for index, _ in rows:
                        lines[index] = _PORT_NODE.sub(lambda _: next(new_tokens), lines[index])
                else:
                    index, _ = rows[0]
                    lines[index] = "".join(_format_port_terminal(f"{side.title()}Terminal", tokens))
                    for index, _ in rows[1:]:
                        lines[index] = ""

    text = "".join(lines)
    start = dst_inv.port_section_start_offset
    end = dst_inv.port_section_end_offset
    if start is None:
        start = end = dst_inv.port_insertion_offset
    return [(start, end, text)], f"Copied {len(ports)} ports with {node_count} target terminal node references."


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

    progress("Validating selected stackup and NET activation settings")
    edits, summary = build_structure_edits(source, target, copy_stackup=options.stackup,
                                           copy_activation=options.channel_activation)
    if options.component_models or options.ports:
        progress("Scanning source models and connections")
        src_inv = scan_spd_inventory(source)
        progress("Scanning target models and connections")
        dst_inv = scan_spd_inventory(target)
        if options.component_models:
            progress("Applying SPICE models to matching component names")
            changes, detail = _model_edits(source, src_inv, dst_inv)
            edits.extend(changes)
            summary.append(detail)
        if options.ports:
            progress("Applying Port selections to target RefDes/NET pins")
            src_refs = _unique(src_inv.refdes_records, "refdes_name", "source RefDes")
            dst_refs = _unique(dst_inv.refdes_records, "refdes_name", "target RefDes")
            changes, detail = _port_edits(source, target, src_inv, dst_inv, src_refs, dst_refs)
            edits.extend(changes)
            summary.append(detail)

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
