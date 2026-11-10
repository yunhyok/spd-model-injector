from dataclasses import fields
from pathlib import Path

import pytest

from spd_model_injector.core.settings_transfer import CopySettingsOptions, copy_spd_settings
from spd_model_injector.core.spd import read_block_body, scan_spd_inventory


_PORTS_ONLY = CopySettingsOptions(stackup=False, channel_activation=False, component_models=False)
_MODELS_ONLY = CopySettingsOptions(stackup=False, channel_activation=False, ports=False)


def _board(*, donor: bool = False) -> str:
    thickness, node, x = (24, 10, 1) if donor else (20, 20, 9)
    model, usage, checked = ("CAP_TUNED", "0b111000", 0) if donor else ("CAP", "0b1000", 1)
    port, state, impedance = ("Port7", " Disabled", 55) if donor else ("Port2", "", 50)
    disabled = "::Unselected" if donor else ""
    clone = ".PartialCkt CAP_TUNED ExtNode = 1 2\nCtune 1 2 10u\n.EndPartialCkt\n" if donor else ""
    clone_part = '.Part CAP_TUNED Outline = 1mm 2mm Tags = "DISCRETE"\n' if donor else ""
    return (
        "* Stackup description lines\n* Zone description lines\n* Layer description lines\n"
        f"Signal$TOP Thickness = {thickness}u Material = COPPER\n"
        "Medium$D1 Thickness = 100u Material = FR4\nSignal$BOTTOM Thickness = 20u Material = COPPER\n"
        "* ConformalLayer description lines\n* Node description lines\n"
        f"Node{node}!!1::SIG X = {x}mm Y = 2mm Layer = Signal$TOP\n"
        f"Node{node + 1}!!2::GND X = {x}mm Y = 3mm Layer = Signal$TOP\n"
        "* Material description lines\n.Material\n"
        ".MetalModel COPPER\n1e3 5e7\n.EndMetalModel\n"
        ".DielectricModel FR4\n1e3 4.3 0.01\n.EndDielectricModel\n.EndMaterial\n"
        "* SurfaceRoughness description lines\n* PartialCkt description lines\n"
        f".PartialCkt CAP ExtNode = 1 2\nCbase 1 2 {1 if donor else 2}u\n.EndPartialCkt\n{clone}"
        "* Connect description lines\n"
        f".Connect C1 {model} Usage = {usage} Checked = {checked}\n"
        f"1 $Package.Node{node}!!1::SIG\n2 $Package.Node{node + 1}!!2::GND\n.EndC\n"
        "* Part description lines\n"
        f'.Part CAP Outline = 1mm 2mm Tags = "DISCRETE"\n{clone_part}'
        f"* Component description lines\n.Component C1 {x}mm 2mm StartLayer = Signal$TOP\n"
        "* Port description lines\n.Port\n"
        f'{port}_C1::SIG{state} Auto RefZ = {impedance} GenFromCktInstance="C1" GenFromCktModel="{model}"\n'
        f"+ PositiveTerminal $Package.Node{node}!!1::SIG\n"
        f"+ NegativeTerminal $Package.Node{node + 1}!!2::GND\n.EndPort\n"
        f".NetList\n\tSIG{disabled} Color = RED\n\tGND -> GroundNets Voltage = 0\n.EndNetList\n"
    )


@pytest.fixture
def paths(tmp_path: Path):
    source, target, output = (tmp_path / f"{name}.spd" for name in ("source", "target", "result"))
    source.write_bytes(_board(donor=True).encode())
    target.write_bytes(_board().replace("\n", "\r\n").encode())
    return source, target, output


@pytest.mark.parametrize("setting", ("stackup", "channel_activation", "component_models", "ports"))
def test_settings_can_be_copied_independently(paths, setting):
    source, target, output = paths
    originals = source.read_bytes(), target.read_bytes()
    options = CopySettingsOptions(**{field.name: field.name == setting for field in fields(CopySettingsOptions)})
    copy_spd_settings(source, target, output, options)

    result = output.read_text(encoding="utf-8")
    assert ("Signal$TOP Thickness = 24u" in result) == (setting == "stackup")
    assert ("SIG::Unselected Color = RED" in result) == (setting == "channel_activation")
    assert ("CAP_TUNED" in result) == (setting == "component_models")
    assert ("Port7_C1::SIG Disabled Auto RefZ = 55" in result) == (setting == "ports")
    assert "Node20!!1::SIG X = 9mm Y = 2mm" in result
    assert "1 $Package.Node20!!1::SIG\n2 $Package.Node21!!2::GND\n.EndC" in result
    assert ".Component C1 9mm 2mm StartLayer = Signal$TOP" in result
    assert "Node10!!1::SIG" not in result
    assert (source.read_bytes(), target.read_bytes()) == originals


def test_copies_model_body_clone_assignment_and_activation(paths):
    source, target, output = paths
    copy_spd_settings(source, target, output, _MODELS_ONLY)

    inventory = scan_spd_inventory(output)
    assert {block.component_name: read_block_body(output, block) for block in inventory.blocks} == {
        "CAP": "Cbase 1 2 1u\n", "CAP_TUNED": "Ctune 1 2 10u\n",
    }
    refdes, = inventory.refdes_records
    assert (refdes.refdes_name, refdes.component_name, refdes.activation_status) == ("C1", "CAP_TUNED", "Disabled")
    assert "Checked = 1" in refdes.connect_line
    assert '.Part CAP_TUNED Outline = 1mm 2mm Tags = "DISCRETE"' in output.read_text(encoding="utf-8")
    port, = inventory.port_records
    assert (port.name, port.component_name, port.enabled) == ("Port2_C1::SIG", "CAP_TUNED", True)


def test_copies_usage_from_connect_continuation_while_preserving_target_flags(paths):
    source, target, output = paths
    source.write_bytes(source.read_bytes().replace(b"CAP_TUNED Usage", b"CAP_TUNED\n+            Usage"))
    target.write_bytes(target.read_bytes().replace(b"CAP Usage", b"CAP\r\n+            Usage"))
    copy_spd_settings(source, target, output, _MODELS_ONLY)

    connect = output.read_text(encoding="utf-8").split(".Connect C1 ", 1)[1].split("\n.EndC", 1)[0]
    assert connect.startswith("CAP_TUNED")
    assert connect.count("Usage = 0b111000") == 1
    assert "0b1000" not in connect
    assert "Checked = 1" in connect and "Checked = 0" not in connect
    assert "1 $Package.Node20!!1::SIG\n2 $Package.Node21!!2::GND" in connect


def test_rejects_cloning_donor_part_with_different_geometry(paths):
    source, target, output = paths
    source.write_bytes(source.read_bytes().replace(b".Part CAP_TUNED Outline = 1mm", b".Part CAP_TUNED Outline = 6mm"))
    with pytest.raises(ValueError, match=r"(?i)geometry|\.part"):
        copy_spd_settings(source, target, output, _MODELS_ONLY)
    assert not output.exists()


def test_rejects_existing_model_assignment_with_different_part_geometry(paths):
    source, target, output = paths
    with target.open("ab") as handle:
        handle.write(b'.Part CAP_TUNED Outline = 6mm 7mm Tags = "DISCRETE"\r\n'
                     b'.PartialCkt CAP_TUNED ExtNode = 1 2\r\nC1 1 2 3u\r\n.EndPartialCkt\r\n')
    with pytest.raises(ValueError, match="geometry differs"):
        copy_spd_settings(source, target, output, _MODELS_ONLY)
    assert not output.exists()


@pytest.mark.parametrize(("extra", "options"), [
    (b".port\r\n.endport\r\n", _MODELS_ONLY),
    (b".netlist\r\nOTHER\r\n.endnetlist\r\n", CopySettingsOptions(False, True, False, False)),
    (b"  .NetList\r\nOTHER\r\n  .EndNetList\r\n", CopySettingsOptions(False, True, False, False)),
])
def test_rejects_noncanonical_duplicate_sections(paths, extra, options):
    source, target, output = paths
    with target.open("ab") as handle:
        handle.write(extra)
    with pytest.raises(ValueError, match="Malformed or multiple"):
        copy_spd_settings(source, target, output, options)
    assert not output.exists()


def test_model_copy_updates_linked_port_header_without_parsing_target_terminals(paths):
    source, target, output = paths
    terminal = b"+ PositiveTerminal Node20!!1::SIG\r\n"
    target.write_bytes(target.read_bytes().replace(b"+ PositiveTerminal $Package.Node20!!1::SIG\r\n", terminal))
    copy_spd_settings(source, target, output, _MODELS_ONLY)

    port, = scan_spd_inventory(output).port_records
    assert (port.name, port.component_name, port.enabled) == ("Port2_C1::SIG", "CAP_TUNED", True)
    assert terminal.replace(b"\r\n", b"\n") in output.read_bytes()


@pytest.mark.parametrize("copy_models", (False, True))
def test_ports_retain_properties_and_use_matching_target_pins(paths, copy_models):
    source, target, output = paths
    options = CopySettingsOptions(stackup=False, channel_activation=False, component_models=copy_models)
    copy_spd_settings(source, target, output, options)

    port, = scan_spd_inventory(output).port_records
    assert port.name == "Port7_C1::SIG"
    assert port.instance == "C1"
    assert port.component_name == ("CAP_TUNED" if copy_models else "CAP")
    assert not port.enabled
    assert "Auto RefZ = 55" in port.header_line
    result = output.read_text(encoding="utf-8")
    assert "+ PositiveTerminal $Package.Node20!!1::SIG\n" in result
    assert "+ NegativeTerminal $Package.Node21!!2::GND\n" in result
    assert "Port2_C1::SIG" not in result


def test_port_node_replacements_skip_comments(paths):
    source, target, output = paths
    comment = b"* Nodes $Package.Node10!!1::SIG and $Package.Node999 in a note\n"
    source.write_bytes(source.read_bytes().replace(b".EndPort\n", comment + b".EndPort\n"))
    copy_spd_settings(source, target, output, _PORTS_ONLY)

    result = output.read_bytes()
    assert comment in result
    assert b"+ PositiveTerminal $Package.Node20!!1::SIG\n" in result


def test_copies_ports_when_target_has_no_port_section(paths):
    source, target, output = paths
    prefix, _, remaining = target.read_bytes().partition(b".Port\r\n")
    _, _, suffix = remaining.partition(b".EndPort\r\n")
    target.write_bytes(prefix + suffix)
    copy_spd_settings(source, target, output, _PORTS_ONLY)

    port, = scan_spd_inventory(output).port_records
    assert port.name == "Port7_C1::SIG"
    assert b"+ PositiveTerminal $Package.Node20!!1::SIG\n" in output.read_bytes()


@pytest.mark.parametrize(("before", "after", "message"), (
    ("Medium$D1", "Medium$D2", "Stackup mismatch"),
    ("SIG Color", "OTHER Color", "NET name mismatch"),
    ("\tSIG Color = RED\n", "\tSIG Color = RED\n\tOTHER Color = RED\n", "NET count mismatch"),
    ("1 $Package.Node20!!1::SIG", "1 $Package.Node20!!1::GND", "pin/NET mismatch"),
    ("2 $Package.Node21!!2::GND\n.EndC", "3 $Package.Node21!!2::GND\n.EndC", "pin/NET mismatch"),
))
def test_mismatch_leaves_both_inputs_and_existing_output_intact(paths, before, after, message):
    source, target, output = paths
    target.write_bytes(target.read_bytes().replace(before.replace("\n", "\r\n").encode(),
                                                   after.replace("\n", "\r\n").encode()))
    output.write_bytes(b"existing output")
    originals = source.read_bytes(), target.read_bytes(), output.read_bytes()

    with pytest.raises(ValueError, match=message):
        copy_spd_settings(source, target, output, CopySettingsOptions())

    assert (source.read_bytes(), target.read_bytes(), output.read_bytes()) == originals
    assert not list(output.parent.glob(f".{output.name}.*.tmp"))


@pytest.mark.parametrize(("before", "after", "message"), (
    (".EndPort\n", "", "Malformed or multiple Port"),
    ("+ NegativeTerminal $Package.Node11!!2::GND\n", "", "nonempty positive and negative"),
    ("PositiveTerminal $Package.Node10!!1::SIG", "PositiveTerminal $Package.Node999!!1::SIG", "no unique RefDes"),
))
def test_malformed_source_ports_abort_without_output(paths, before, after, message):
    source, target, output = paths
    source.write_bytes(source.read_bytes().replace(before.encode(), after.encode()))
    with pytest.raises(ValueError, match=message):
        copy_spd_settings(source, target, output, _PORTS_ONLY)
    assert not output.exists()


def test_detects_target_changed_after_loading(paths):
    source, target, output = paths
    loaded_signature = scan_spd_inventory(target).source_signature
    target.write_bytes(target.read_bytes() + b"\r\n")
    with pytest.raises(ValueError, match="Target SPD changed since loading"):
        copy_spd_settings(source, target, output, _PORTS_ONLY, target_signature=loaded_signature)
    assert not output.exists()


@pytest.mark.parametrize("input_index", (0, 1))
def test_detects_input_changed_during_copy(paths, input_index):
    source, target, output = paths
    output.write_bytes(b"existing output")

    def mutate_on_write(message, current, total):
        if message == "Validation passed. Writing new SPD":
            with paths[input_index].open("ab") as handle:
                handle.write(b"\n")

    with pytest.raises(ValueError, match="changed during settings copy"):
        copy_spd_settings(source, target, output, _PORTS_ONLY, progress_callback=mutate_on_write)
    assert output.read_bytes() == b"existing output"
    assert not list(output.parent.glob(f".{output.name}.*.tmp"))


@pytest.mark.parametrize("input_index", (0, 1))
def test_prevents_output_overwriting_an_input_or_its_hardlink(paths, input_index):
    source, target, output = paths
    output.hardlink_to(paths[input_index])
    originals = source.read_bytes(), target.read_bytes()
    for alias in (paths[input_index], output):
        with pytest.raises(ValueError, match="Output path must differ"):
            copy_spd_settings(source, target, alias, _PORTS_ONLY)
    assert (source.read_bytes(), target.read_bytes()) == originals
    assert output.samefile(paths[input_index])
