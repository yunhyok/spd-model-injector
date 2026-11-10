from __future__ import annotations

from dataclasses import dataclass
import mmap
import ntpath
from pathlib import Path
import re


_LAYER = re.compile(r"(?:Signal|Plane|Medium)\$\S+", re.IGNORECASE)
_FIELD = re.compile(r'([\w.]+)\s*=\s*("[^"]*"|\S+)')
_PHYSICAL = {"thickness", "material", "dielectricmaterial", "width"}
_TARGET_LAYER_FIELDS = {"shape", "color", "doglegholethreshold", "thermalholethreshold",
                        "smallholethreshold", "viaholethreshold"}
_UNSELECTED = re.compile(r"::Unselected(?=::|\|\||$)", re.IGNORECASE)
_Edit = tuple[int, int, str]


def build_structure_edits(
    source: Path, target: Path, *, copy_stackup: bool, copy_activation: bool,
) -> tuple[list[_Edit], list[str]]:
    """Validate all board structure and plan only bounded target setting edits."""
    with source.open("rb") as src, target.open("rb") as dst:
        if not source.stat().st_size or not target.stat().st_size:
            raise ValueError("The source and target must both be nonempty SPD files.")
        with mmap.mmap(src.fileno(), 0, access=mmap.ACCESS_READ) as donor, mmap.mmap(dst.fileno(), 0, access=mmap.ACCESS_READ) as board:
            source_layer_range = _layer_range(donor)
            target_layer_range = _layer_range(board)
            source_rows = _layer_rows(_read(donor, *source_layer_range))
            target_rows = _layer_rows(_read(board, *target_layer_range))
            source_names = _layer_names(source_rows)
            target_names = _layer_names(target_rows)
            if source_names != target_names:
                raise ValueError(f"Stackup mismatch: layer names, types, or order differ ({len(source_names)} vs {len(target_names)} layers).")
            for label in ("Stackup", "Zone", "ConformalLayer"):
                donor_text = _description_body(donor, label).strip()
                if donor_text != _description_body(board, label).strip():
                    raise ValueError(f"Stackup mismatch: different {label} definitions.")
                if copy_stackup and donor_text:
                    raise ValueError(f"Nonempty {label} definitions are not supported for stackup copy.")

            source_nets = _nets(donor)
            target_nets = _nets(board)
            if len(source_nets) != len(target_nets):
                raise ValueError(f"NET count mismatch: source has {len(source_nets)}, target has {len(target_nets)}.")
            if source_nets.keys() != target_nets.keys():
                missing = ', '.join(sorted(source_nets.keys() - target_nets.keys())[:3])
                extra = ', '.join(sorted(target_nets.keys() - source_nets.keys())[:3])
                raise ValueError(f"NET name mismatch: missing in target: {missing}; target only: {extra}.")
            for name, net in source_nets.items():
                if net.group != target_nets[name].group:
                    raise ValueError(f"NET group mismatch: {name} belongs to a different group in the target.")

            edits: list[_Edit] = []
            summary = [f"Matched {len(target_names)} layers and {len(target_nets)} NETs."]
            if copy_stackup:
                if _description_body(donor, "SurfaceRoughness").strip() != _description_body(board, "SurfaceRoughness").strip():
                    raise ValueError("Different SurfaceRoughness definitions are not supported for stackup copy.")
                layer_text, required = _copy_layers(source_rows, target_rows)
                if layer_text != _read(board, *target_layer_range):
                    edits.append((*target_layer_range, layer_text))
                edits.extend(_material_edits(donor, board, required))
                summary.append(f"Copied stackup settings for {len(target_names)} layers.")
            if copy_activation:
                activation = [net.change(source_nets[name].disabled) for name, net in target_nets.items()
                              if net.disabled != source_nets[name].disabled]
                edits.extend(activation)
                summary.append(f"Changed activation for {len(activation)} NETs.")
            return sorted(edits), summary


def _markers(data: mmap.mmap, marker: bytes, suffix: bytes = b"\r\n") -> int | None:
    """Find one exact, unindented section marker without decoding board geometry."""
    after = len(marker)
    found = 0 if data[:after] == marker and (after == len(data) or data[after] in suffix) else None
    cursor = 0
    while (index := data.find(b"\n" + marker, cursor)) >= 0:
        index += 1
        cursor = index + len(marker)
        if cursor < len(data) and data[cursor] not in suffix:
            continue
        if found is not None:
            raise ValueError(f"Duplicate SPD marker: {marker.decode('ascii')}.")
        found = index
    return found


def _after_line(data: mmap.mmap, offset: int) -> int:
    end = data.find(b"\n", offset)
    return len(data) if end < 0 else end + 1


def _read(data: mmap.mmap, start: int, end: int) -> str:
    try:
        return data[start:end].decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError as exc:
        raise ValueError("An SPD settings section contains unsupported text encoding.") from exc


def _bounded(data: mmap.mmap, first: bytes, last: bytes, *, optional: bool = False) -> tuple[int, int] | None:
    start, end = _markers(data, first), _markers(data, last)
    markers = [match.start() for match in re.finditer(
        rb"(?im)^[ \t]*(?:" + re.escape(first) + b"|" + re.escape(last) + rb")(?=[ \t\r\n]|$)", data)]
    if markers != [offset for offset in (start, end) if offset is not None]:
        raise ValueError(f"Malformed or multiple SPD sections: {first.decode('ascii')}.")
    if optional and start is None and end is None:
        return None
    if start is None or end is None or _after_line(data, start) > end:
        raise ValueError(f"Missing or malformed SPD section: {first.decode('ascii')}.")
    return _after_line(data, start), end


def _description_body(data: mmap.mmap, label: str) -> str:
    start = _markers(data, f"* {label} description lines".encode("ascii"))
    if start is None:
        return ""
    start = _after_line(data, start)
    marker = re.compile(rb"\n(?:\* [^\r\n]+ description lines|\.NetList)[\r\n]").search(data, start - 1)
    end = marker.start() + 1 if marker else len(data)
    if end - start > 16 * 1024 * 1024:
        raise ValueError(f"Unexpectedly large {label} section; stackup copy is not supported.")
    return _read(data, start, end)


def _layer_range(data: mmap.mmap) -> tuple[int, int]:
    start = _markers(data, b"* Layer description lines")
    if start is None:
        raise ValueError("Missing SPD Layer description section.")
    start = _after_line(data, start)
    end = data.find(b"\n* ", start - 1)
    if end < 0 or not data[end + 1:end + 39].startswith((b"* ConformalLayer description lines", b"* Node description lines")):
        raise ValueError("Missing or malformed end of SPD Layer description section.")
    return start, end + 1


def _layer_rows(body: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for raw in body.splitlines(keepends=True):
        line = raw.strip()
        if line.startswith("+"):
            if not rows or not rows[-1][0]:
                raise ValueError("Unattached SPD layer continuation.")
            name, fields, original = rows.pop()
            rows.append((name, fields + " " + line[1:].strip(), original + raw))
        else:
            pieces = line.split(maxsplit=1)
            rows.append((pieces[0] if pieces else "", pieces[1] if len(pieces) == 2 else "", raw))
    return rows


def _layer_names(rows: list[tuple[str, str, str]]) -> list[str]:
    names = []
    for name, fields, _ in rows:
        if _LAYER.fullmatch(name):
            _fields(fields)
            names.append(name)
        elif name and name.lower() != ".trapezoidaltraceangle" and not name.lower().startswith("patchsignal$"):
            raise ValueError(f"Unsupported layer declaration: {name}.")
    if not names or len(set(names)) != len(names):
        raise ValueError("SPD Layer description has no supported layers or has duplicate layer names.")
    return names


def _fields(text: str) -> dict[str, tuple[str, str]]:
    fields: dict[str, tuple[str, str]] = {}
    cursor = 0
    for match in _FIELD.finditer(text):
        key, value = match.groups()
        if text[cursor:match.start()].strip() or key.lower() in fields:
            raise ValueError("Malformed or duplicate SPD layer property.")
        fields[key.lower()] = (key, value)
        cursor = match.end()
    if text[cursor:].strip():
        raise ValueError("Malformed SPD layer property.")
    return fields


def _copy_layers(source_rows, target_rows) -> tuple[str, set[tuple[str, str]]]:
    def classify(rows):
        primary, angles = {}, {}
        for name, text, raw in rows:
            if _LAYER.fullmatch(name):
                primary[name] = _fields(text)
            elif name.lower() == ".trapezoidaltraceangle":
                fields = _fields(text)
                layer = fields.get("layer", ("", ""))[1]
                if set(fields) != {"layer", "angle"} or layer in angles:
                    raise ValueError("Malformed or duplicate trapezoidal trace angle.")
                angles[layer] = (fields["angle"][1], raw)
        if angles.keys() - primary.keys():
            raise ValueError("Trapezoidal trace angle references an unknown layer.")
        return primary, angles

    source, source_angles = classify(source_rows)
    target, target_angles = classify(target_rows)
    output, required = [], set()
    pending_angle = None
    for name, text, raw in target_rows:
        if name in target:
            if pending_angle:
                output.append(pending_angle)
            donor, board = source[name], target[name]
            if "thickness" not in donor or "thickness" not in board:
                raise ValueError(f"Missing SPD layer thickness: {name}.")
            for key in (donor.keys() | board.keys()) - _PHYSICAL - _TARGET_LAYER_FIELDS:
                if donor.get(key) != board.get(key):
                    raise ValueError(f"Unsupported stackup property differs: {name} / {key}.")
            for key, kind in (("material", "dielectricmodel" if name.lower().startswith("medium$") else "metalmodel"),
                              ("dielectricmaterial", "dielectricmodel")):
                if key in donor:
                    required.add((kind, donor[key][1].strip('"')))
            if any(board.get(key) != donor.get(key) for key in _PHYSICAL):
                merged = {key: donor[key] if key in _PHYSICAL else value for key, value in board.items()
                          if key not in _PHYSICAL or key in donor}
                merged.update((key, value) for key, value in donor.items() if key in _PHYSICAL and key not in merged)
                raw = _format_layer(name, merged.values())
            pending_angle = source_angles[name][1] if name in source_angles and name not in target_angles else None
        elif name.lower() == ".trapezoidaltraceangle":
            layer = _fields(text)["layer"][1]
            if layer not in source_angles:
                continue
            if source_angles[layer][0] != target_angles[layer][0]:
                raw = source_angles[layer][1]
        output.append(raw)
    if pending_angle:
        output.append(pending_angle)
    return "".join(output), required


def _format_layer(name, fields) -> str:
    lines = [name]
    for key, value in fields:
        assignment = f" {key} = {value}"
        if len(lines[-1] + assignment) > 145 and lines[-1] != name:
            lines.append("+           " + assignment)
        else:
            lines[-1] += assignment
    return "\n".join(lines) + "\n"


def _material_models(data: mmap.mmap) -> tuple[int | None, dict[tuple[str, str], list[_Edit]]]:
    section = _bounded(data, b".Material", b".EndMaterial", optional=True)
    if section is None:
        return None, {}
    start, end = section
    models: dict[tuple[str, str], list[_Edit]] = {}
    variants: set[tuple[str, str, str]] = set()
    cursor = start
    while cursor < end:
        offset = cursor
        cursor = _after_line(data, cursor)
        line = _read(data, offset, cursor).strip()
        if not line or line.startswith("*"):
            continue
        match = re.fullmatch(r'\.(\w+Model)\s+("[^"]*"|\S+)(.*)', line, re.IGNORECASE)
        if not match:
            raise ValueError("Unsupported SPD material declaration.")
        kind, name, attributes = match.groups()
        key = (kind.lower(), name.strip('"'))
        temperature = re.search(r"\bTemperature\s*=\s*(\S+)", attributes, re.IGNORECASE)
        variant = (*key, temperature[1] if temperature else "")
        if variant in variants:
            raise ValueError(f"Duplicate SPD material definition: {name} / {kind}.")
        variants.add(variant)
        while cursor < end:
            line_end = _after_line(data, cursor)
            if _read(data, cursor, line_end).strip().lower() == f".end{kind.lower()}":
                models.setdefault(key, []).append((offset, line_end, _read(data, offset, line_end)))
                cursor = line_end
                break
            cursor = line_end
        else:
            raise ValueError(f"Unclosed SPD material definition: {name} / {kind}.")
    return end, models


def _external_material(data: mmap.mmap) -> str | None:
    offset = _markers(data, b".MaterialFileName", b" \t\r\n")
    if offset is None:
        return None
    match = re.fullmatch(r'\.MaterialFileName\s+"([^"]+)"', _read(data, offset, _after_line(data, offset)).strip())
    if not match:
        raise ValueError("Unsupported SPD external material file reference.")
    path = match[1]
    return ntpath.normcase(ntpath.normpath(path)) if ntpath.splitdrive(path)[0] and ntpath.isabs(path) else None


def _material_edits(source: mmap.mmap, target: mmap.mmap, required: set[tuple[str, str]]) -> list[_Edit]:
    _, donor = _material_models(source)
    insertion, board = _material_models(target)
    external = required - donor.keys()
    if external:
        path = _external_material(source)
        if external & board.keys() or path is None or path != _external_material(target):
            names = ', '.join(sorted({name for _, name in external}))
            raise ValueError(f"Cannot resolve source stackup materials from a matching external material file: {names}.")
    names = {name for _, name in required}
    edits, additions = [], []
    for key, models in donor.items():
        if key[1] not in names:
            continue
        model_texts = [model for _, _, model in models]
        if key not in board:
            additions.extend(model_texts)
        elif model_texts != [model for _, _, model in board[key]]:
            (start, end, _), *other_variants = board[key]
            edits.append((start, end, "".join(model_texts)))
            edits.extend((start, end, "") for start, end, _ in other_variants)
    if additions:
        addition = "".join(additions)
        if insertion is None:
            marker = _markers(target, b"* Material description lines")
            if marker is None:
                raise ValueError("No safe Material section location in target SPD.")
            insertion = _after_line(target, marker)
            addition = f".Material\n{addition}.EndMaterial\n"
        edits.append((insertion, insertion, addition))
    return edits


@dataclass(frozen=True)
class _Net:
    start: int
    end: int
    raw: str
    group: str
    token_start: int
    token_end: int
    disabled: bool

    def change(self, disabled: bool) -> _Edit:
        token = self.raw[self.token_start:self.token_end]
        if disabled:
            base, separator, flags = token.partition("||")
            token = base + "::Unselected" + separator + flags
        else:
            token = _UNSELECTED.sub("", token)
        return self.start, self.end, self.raw[:self.token_start] + token + self.raw[self.token_end:]


def _nets(data: mmap.mmap) -> dict[str, _Net]:
    section = _bounded(data, b".NetList", b".EndNetList")
    assert section is not None
    cursor, end = section
    group, seen_default = "", False
    nets: dict[str, _Net] = {}
    while cursor < end:
        offset = cursor
        cursor = _after_line(data, cursor)
        line = _read(data, offset, cursor)
        if not line.strip() or line.lstrip().startswith("*"):
            continue
        first = re.match(r"\s*(\S+)", line)
        assert first is not None
        net_token = first[1]
        if net_token.startswith(("::", "||")) or re.match(r"\s*(?:RiseTime|%Coupling)\s*=", line, re.IGNORECASE):
            if nets or seen_default:
                raise ValueError("Unexpected or duplicate unnamed NET default row.")
            seen_default = True
            continue
        if net_token.startswith((".", "+")):
            raise ValueError("Unsupported NET row in SPD .NetList.")
        name = re.split(r"::|\|\|", net_token, maxsplit=1)[0]
        if name in nets:
            raise ValueError(f"Duplicate NET name: {name}.")
        arrow = re.match(r"\s*(\S+)\s*->\s*(\S+)", line)
        if arrow:
            if arrow[1] != net_token:
                raise ValueError(f"Malformed NET group on {name}.")
            group = re.split(r"::|\|\|", arrow[2], maxsplit=1)[0]
            if not group or len(_UNSELECTED.findall(arrow[1] + " " + arrow[2])) > 1:
                raise ValueError(f"Malformed NET group or activation flags on {name}.")
        elif line[first.end():].lstrip().startswith("->") or "->" in net_token:
            raise ValueError(f"Malformed NET group on {name}.")
        token, index = (arrow, 2) if arrow and not _UNSELECTED.search(net_token) else (first, 1)
        flags = _UNSELECTED.findall(token[index])
        if len(flags) > 1:
            raise ValueError(f"Duplicate activation flags on NET {name}.")
        nets[name] = _Net(offset, cursor, line, group, *token.span(index), bool(flags))
    if not nets:
        raise ValueError("No named NETs in SPD .NetList.")
    return nets
