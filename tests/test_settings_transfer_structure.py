from pathlib import Path

import pytest

from spd_model_injector.core.settings_transfer_structure import build_structure_edits


_NETS = (
    "\t::Unselected||DropShape RiseTime = 0ps %Coupling = 0\n"
    "\tSIG::Unselected||DropShape Color = RED\n"
    "\tGND -> GroundNets Color = BLUE Voltage = 0\n"
    "\tVDD -> PowerNets::Unselected||DropShape Voltage = 0.9 GroundNet = GND\n"
    "\tAUX Voltage = 1.2\n"
)
_LAYERS = (
    "Signal$TOP Thickness = 20u Material = CU Width = 36u Color = GREEN\n"
    "PatchSignal$TOP Shape = target_top Layer = Signal$TOP\n"
    ".TrapezoidalTraceAngle Layer = Signal$TOP Angle = 90\n"
    "Medium$DIEL Thickness = 15u Material = CORE\n"
    "Plane$BOTTOM Thickness = 17u Material = CU DielectricMaterial = CORE Shape = target_bottom\n"
)
_MATERIALS = (
    ".MetalModel CU\n1e3 5e7\n.EndMetalModel\n"
    ".DielectricModel CORE\n1e3 3.9 0.01\n.EndDielectricModel\n"
    ".DielectricModel TARGET_ONLY\n1e3 3 0\n.EndDielectricModel\n"
)


def _board(path: Path, *, layers: str = _LAYERS, nets: str = _NETS, materials: str = _MATERIALS,
           header: str = "", line_end: str = "\n") -> Path:
    spd = (f"{header}* Stackup description lines\n\n* Zone description lines\n\n"
           f"* Layer description lines\n{layers}\n* ConformalLayer description lines\n\n"
           f"* Node description lines\nNode1 X = 1mm Y = 1mm Layer = Signal$TOP\n"
           f"* Material description lines\n.Material\n{materials}.EndMaterial\n"
           f"* SurfaceRoughness description lines\n\n.NetList\n{nets}.EndNetList\n")
    path.write_bytes(spd.replace("\n", line_end).encode())
    return path


def test_transfers_physical_stackup_and_activation_without_copying_board_data(tmp_path):
    source = _board(tmp_path / "source.spd", layers=(
        _LAYERS.replace("20u", "21u").replace("Width = 36u ", "")
        .replace("Color = GREEN", "\n+            Color = WHITE")
        .replace("target_", "donor_").replace("Angle = 90", "Angle = 88")
        .replace("Material = CORE", "Material = NEW")
    ), nets=(
        _NETS.replace("SIG::Unselected", "SIG").replace("VDD -> PowerNets::Unselected", "VDD -> PowerNets")
        .replace("AUX Voltage = 1.2", "AUX::Unselected Voltage = 2.5")
    ), materials=_MATERIALS.replace("CORE", "NEW").replace("5e7", "4e7"))
    target = _board(tmp_path / "target.spd", line_end="\r\n")
    edits, summary = build_structure_edits(source, target, copy_stackup=True, copy_activation=True)
    result = target.read_bytes()
    for start, end, text in reversed(edits):
        result = result[:start] + text.encode() + result[end:]
    result = result.decode().replace("\r\n", "\n")
    assert "Signal$TOP Thickness = 21u Material = CU Color = GREEN\n" in result
    assert "Angle = 88" in result
    assert "donor_" not in result and "Shape = target_bottom" in result and "Shape = target_top" in result
    assert "Medium$DIEL Thickness = 15u Material = NEW" in result
    assert ".DielectricModel NEW\n" in result and ".DielectricModel TARGET_ONLY\n" in result
    assert ".MetalModel CU\n1e3 4e7\n" in result
    assert "SIG||DropShape Color = RED" in result
    assert "VDD -> PowerNets||DropShape Voltage = 0.9 GroundNet = GND" in result
    assert "AUX::Unselected Voltage = 1.2" in result
    assert "Changed activation for 3 NETs." in summary


@pytest.mark.parametrize(("layers", "nets", "message"), [
    (_LAYERS.replace("Medium$DIEL", "Medium$DIEL2"), _NETS, "Stackup mismatch"),
    (_LAYERS, _NETS.replace("SIG::", "OTHER::"), "NET name mismatch"),
    (_LAYERS, _NETS + "OTHER Color = BLUE\n", "NET count mismatch"),
    (_LAYERS, _NETS.replace("PowerNets", "GroundNets"), "NET group mismatch"),
    (_LAYERS, _NETS + "SIG Color = BLUE\n", "Duplicate NET name"),
])
def test_requires_matching_structure_even_when_copying_other_settings(tmp_path, layers, nets, message):
    source = _board(tmp_path / "source.spd", layers=layers, nets=nets)
    target = _board(tmp_path / "target.spd")
    with pytest.raises(ValueError, match=message):
        build_structure_edits(source, target, copy_stackup=False, copy_activation=False)


def test_rejects_material_only_available_from_a_different_external_file(tmp_path):
    source = _board(tmp_path / "source.spd", materials="", header='.MaterialFileName "C:/cadence/A.cmx"\n')
    target = _board(tmp_path / "target.spd", materials="", header='.MaterialFileName "C:/cadence/B.cmx"\n')
    with pytest.raises(ValueError, match="matching external material file"):
        build_structure_edits(source, target, copy_stackup=True, copy_activation=False)


def test_replaces_the_referenced_material_family_with_temperature_variants(tmp_path):
    source = _board(tmp_path / "source.spd", materials=(
        _MATERIALS + ".DielectricModel CORE Temperature = 50\n1e3 4.1 0.01\n.EndDielectricModel\n"
    ))
    target = _board(tmp_path / "target.spd", materials=(
        _MATERIALS + ".DielectricModel CORE Temperature = 70\n1e3 3.8 0.01\n.EndDielectricModel\n"
    ))
    edits, _ = build_structure_edits(source, target, copy_stackup=True, copy_activation=False)
    assert len(edits) == 2
    assert ".DielectricModel CORE Temperature = 50\n" in edits[0][2]
    assert "Temperature = 70" in target.read_bytes()[edits[1][0]:edits[1][1]].decode()
    assert edits[1][2] == ""


def test_signed_net_names_are_channels_and_keep_their_activation_flags(tmp_path):
    nets = _NETS.replace("SIG", "+3_3V").replace("VDD", "+5V")
    source = _board(tmp_path / "source.spd", nets=nets)
    target = _board(tmp_path / "target.spd", nets=nets.replace("::Unselected", ""), line_end="\r\n")

    edits, summary = build_structure_edits(source, target, copy_stackup=False, copy_activation=True)
    result = target.read_bytes()
    for start, end, text in reversed(edits):
        result = result[:start] + text.encode() + result[end:]
    text = result.decode().replace("\r\n", "\n")
    assert "+3_3V::Unselected||DropShape Color = RED" in text
    assert "+5V -> PowerNets::Unselected||DropShape Voltage = 0.9 GroundNet = GND" in text
    assert "Matched 3 layers and 4 NETs." in summary
    assert "Changed activation for 2 NETs." in summary


def test_bare_plus_continuation_is_still_rejected(tmp_path):
    source = _board(tmp_path / "source.spd", nets=_NETS + "\t+ Voltage = 1.2\n")
    target = _board(tmp_path / "target.spd")
    with pytest.raises(ValueError, match="Unsupported NET row"):
        build_structure_edits(source, target, copy_stackup=False, copy_activation=True)


@pytest.mark.parametrize("direct_source", [True, False])
def test_stackup_switches_between_direct_dielectric_values_and_material_reference(tmp_path, direct_source):
    direct = _LAYERS.replace("Width = 36u", "Width = 36u Permittivity = 4.2 LossTangent = 0.02")
    material = _LAYERS.replace("Width = 36u", "Width = 36u DielectricMaterial = CORE")
    source = _board(tmp_path / "source.spd", layers=direct if direct_source else material)
    target = _board(tmp_path / "target.spd", layers=material if direct_source else direct)

    edits, _ = build_structure_edits(source, target, copy_stackup=True, copy_activation=False)
    result = target.read_bytes()
    for start, end, text in reversed(edits):
        result = result[:start] + text.encode() + result[end:]
    top = next(line for line in result.decode().splitlines() if line.startswith("Signal$TOP "))
    assert ("Permittivity = 4.2" in top) == direct_source
    assert ("LossTangent = 0.02" in top) == direct_source
    assert ("DielectricMaterial = CORE" in top) != direct_source
    assert "Color = GREEN" in top
    assert "PatchSignal$TOP Shape = target_top" in result.decode()
