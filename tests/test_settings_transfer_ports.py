import pytest

from spd_model_injector.core.settings_transfer import _port_edits
from spd_model_injector.core.spd import scan_spd_inventory


_SOURCE_PINS = {"C1": {"A": (10, "SIG"), "B": (11, "GND")}}
_TARGET_PINS = {"C1": {"Q": (20, "SIG"), "R": (21, "SIG"), "S": (22, "GND")}}
_PORT_HEADER = 'Port7_C1_A::SIG Disabled Auto RefZ = 55'
_LINKED_HEADER = _PORT_HEADER + ' GenFromCktInstance="C1" GenFromCktModel="DONOR"'
_TERMINALS = (
    "+ PositiveTerminal $Package.Node10!!A::SIG\n"
    "* Keep a note about $Package.Node10!!A::SIG\n"
    "+ NegativeTerminal $Package.Node11!!B::GND\n"
)


def _port_copy(tmp_path, *, source_pins=_SOURCE_PINS, target_pins=_TARGET_PINS,
               header=_LINKED_HEADER, terminals=_TERMINALS):
    def board(filename, refs, model, port_text):
        connections = []
        for ref, pins in refs.items():
            connections.append(f".Connect {ref} {model}\n")
            connections.extend(f"{pin} $Package.Node{node}!!{pin}::{net}\n" for pin, (node, net) in pins.items())
            connections.append(".EndC\n")
        filename.write_bytes(("".join(connections) + port_text + ".NetList\nSIG\nGND\n.EndNetList\n").encode())
        inventory = scan_spd_inventory(filename)
        return inventory, {record.refdes_name: record for record in inventory.refdes_records}

    source, target = tmp_path / "source.spd", tmp_path / "target.spd"
    source_inventory, source_refs = board(source, source_pins, "DONOR", f".Port\n{header}\n{terminals}.EndPort\n")
    target_inventory, target_refs = board(target, target_pins, "TARGET", "")
    edits, _ = _port_edits(source, target, source_inventory, target_inventory, source_refs, target_refs)
    (start, end, text), = edits
    assert start == end == target_inventory.port_insertion_offset
    return text


def test_generated_port_uses_all_current_target_pins(tmp_path):
    text = _port_copy(tmp_path)
    assert _LINKED_HEADER.replace('"DONOR"', '"TARGET"') in text
    assert "+            PositiveTerminal $Package.Node20!!Q::SIG $Package.Node21!!R::SIG\n" in text
    assert "+ NegativeTerminal $Package.Node22!!S::GND\n" in text
    assert "* Keep a note about $Package.Node10!!A::SIG\n" in text


@pytest.mark.parametrize("header", [_PORT_HEADER, _LINKED_HEADER])
def test_manual_and_subset_ports_only_use_explicit_pins(tmp_path, header):
    text = _port_copy(
        tmp_path,
        source_pins={"C1": {**_SOURCE_PINS["C1"], "C": (12, "SIG")}},
        target_pins={"C1": {"A": (20, "SIG"), "B": (21, "GND"), "D": (22, "SIG"), "E": (23, "GND")}},
        header=header,
    )
    assert "+ PositiveTerminal $Package.Node20!!A::SIG\n" in text
    assert "+ NegativeTerminal $Package.Node21!!B::GND\n" in text
    assert "$Package.Node22" not in text and "$Package.Node23" not in text


def test_explicit_port_can_span_multiple_refdes(tmp_path):
    text = _port_copy(
        tmp_path,
        source_pins={"C1": {"A": (10, "SIG")}, "C2": {"B": (11, "GND")}},
        target_pins={"C1": {"A": (20, "SIG"), "C": (21, "SIG")}, "C2": {"B": (22, "GND"), "D": (23, "GND")}},
    )
    assert "+ PositiveTerminal $Package.Node20!!A::SIG\n" in text
    assert "+ NegativeTerminal $Package.Node22!!B::GND\n" in text
    assert "$Package.Node21" not in text and "$Package.Node23" not in text


def test_generated_port_rejects_missing_target_net(tmp_path):
    with pytest.raises(ValueError, match=r"positive NET SIG is missing on target RefDes C1"):
        _port_copy(tmp_path, target_pins={"C1": {"S": (22, "GND")}})


def test_linked_instance_disambiguates_shared_node_on_a_mixed_refdes_port(tmp_path):
    text = _port_copy(
        tmp_path,
        source_pins={"C1": {"A": (10, "SIG")}, "C2": {"A": (10, "SIG")}, "C3": {"B": (11, "GND")}},
        target_pins={"C1": {"A": (20, "SIG")}, "C2": {"A": (21, "SIG")}, "C3": {"B": (22, "GND")}},
    )
    assert "+ PositiveTerminal $Package.Node20!!A::SIG\n" in text
    assert "+ NegativeTerminal $Package.Node22!!B::GND\n" in text
    assert "$Package.Node21" not in text


@pytest.mark.parametrize("header", [_PORT_HEADER, _LINKED_HEADER])
def test_genfrom_instance_disambiguates_shared_source_node(tmp_path, header):
    source_pins = {**_SOURCE_PINS, "C2": {"A": (10, "SIG")}}
    if header == _LINKED_HEADER:
        text = _port_copy(tmp_path, source_pins=source_pins, header=header)
        assert "$Package.Node20!!Q::SIG $Package.Node21!!R::SIG" in text
    else:
        with pytest.raises(ValueError, match="no unique RefDes/pin match"):
            _port_copy(tmp_path, source_pins=source_pins, header=header)


def test_genfrom_instance_disambiguates_shared_source_node_in_subset(tmp_path):
    text = _port_copy(
        tmp_path,
        source_pins={"C1": {**_SOURCE_PINS["C1"], "C": (12, "SIG")}, "C2": {"A": (10, "SIG")}},
        target_pins={"C1": {"A": (20, "SIG"), "B": (21, "GND"), "D": (22, "SIG")}, "C2": {"A": (23, "SIG")}},
    )
    assert "+ PositiveTerminal $Package.Node20!!A::SIG\n" in text
    assert "+ NegativeTerminal $Package.Node21!!B::GND\n" in text
    assert "$Package.Node22" not in text and "$Package.Node23" not in text


@pytest.mark.parametrize("terminals", [
    _TERMINALS.replace("$Package.Node10!!A::SIG\n", "$Package.Node10!!A::SIG $Package.Node10!!A\n", 1),
    _TERMINALS.replace("$Package.Node11!!B::GND", "$Package.Node10!!A"),
])
def test_rejects_duplicate_and_overlapping_donor_terminal_nodes(tmp_path, terminals):
    with pytest.raises(ValueError, match="duplicate or overlapping terminal nodes"):
        _port_copy(tmp_path, terminals=terminals)


def test_rejects_overlapping_resolved_terminal_nodes(tmp_path):
    with pytest.raises(ValueError, match="duplicate or overlapping terminal nodes"):
        _port_copy(tmp_path, target_pins={"C1": {"Q": (20, "SIG"), "R": (20, "GND")}})
