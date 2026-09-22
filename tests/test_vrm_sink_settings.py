from pathlib import Path

import pytest

from spd_model_injector.core.spd import DcSetting, VrmSinkRecord, VrmSinkSetting, is_number, scan_spd_inventory, write_spd_with_replacements


_PREFIX = (
    ".PartialCkt CAP ExtNode = 1 2\nC 1 2 1u\n.EndPartialCkt\n"
    ".Port\n.EndPort\n"
    ".NetList\n\tDGND -> GroundNets\n\tVDD_A/0 -> PowerNets\n.EndNetList\n"
    '.OtherCircuit Device = C1_0 Name = "C1_0"\n'
)
_VRM_A = '.VRM NominalVoltage = 0.85 SenseVoltage = 0.85 OutputCurrent = 1 Name = "VRM_LGA_VDD_A/0_DGND"\n'
_VRM_B = '.VRM NominalVoltage = 1.8 SenseVoltage = 1.8 OutputCurrent = 1 Name = "VRM_LGA_VDD18/0_DGND"\n'
_SINK = '.Sink NominalVoltage = 0.85 Current = 1 Model = 2 PFMode = 2 PinEqualCurrent = 1 Name = "SINK_SITE0_VDD_A/0_DGND"\n'
_VRM_BODY = (
    '.Pin Name = "Positive Pin"\n.Map CircuitName = LGA CircuitPinName = A1\n.Node Name = Node1!!A1::VDD_A/0\n.EndMap\n.EndPin\n'
    '.Pin Name = "Positive Sense Pin"\n.EndPin\n.EndVRM\n'
)
_SINK_BODY = (
    '.Pin Name = "Positive Pin"\n.Map CircuitName = SITE0 CircuitPinName = 12\n.Node Name = Node2!!12::VDD_A/0 Voltage = inf\n.EndMap\n.EndPin\n'
    ".SinkCurrentSource\n.EndSinkCurrentSource\n.EndSink\n"
)
_SUFFIX = '* VRMGroup description lines\n.CurveProperty Name = "VRM" Type = 1\n.End\n'
_BOARD = _PREFIX + _VRM_A + _VRM_BODY + _VRM_B + ".EndVRM\n" + _SINK + _SINK_BODY + _SUFFIX


def _write(path: Path, text: str = _BOARD) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def test_scan_records_vrm_and_sink_headers_with_properties_and_offsets(tmp_path: Path) -> None:
    source = _write(tmp_path / "vrm.spd")
    inventory = scan_spd_inventory(source)
    records = inventory.vrm_sink_records

    assert [(record.kind, record.name) for record in records] == [
        ("VRM", "VRM_LGA_VDD_A/0_DGND"), ("VRM", "VRM_LGA_VDD18/0_DGND"), ("Sink", "SINK_SITE0_VDD_A/0_DGND"),
    ]
    assert records[0].properties == (("NominalVoltage", "0.85"), ("SenseVoltage", "0.85"), ("OutputCurrent", "1"))
    assert records[2].properties == (
        ("NominalVoltage", "0.85"), ("Current", "1"), ("Model", "2"), ("PFMode", "2"), ("PinEqualCurrent", "1"),
    )
    assert records[2].key == ("Sink", "SINK_SITE0_VDD_A/0_DGND")
    raw = source.read_bytes()
    for record in records:
        assert raw[record.line_start_offset:record.line_end_offset] == (record.line + "\n").encode()
    # Unrelated sections are untouched by the new scan.
    assert inventory.power_nets == ("VDD_A/0",) and inventory.ground_nets == ("DGND",)
    assert [block.component_name for block in inventory.blocks] == ["CAP"]


def test_scan_ignores_crlf_and_nameless_headers(tmp_path: Path) -> None:
    source = _write(tmp_path / "crlf.spd", _BOARD.replace("\n", "\r\n").replace(' Name = "VRM_LGA_VDD18/0_DGND"', ""))
    records = scan_spd_inventory(source).vrm_sink_records
    assert [record.name for record in records] == ["VRM_LGA_VDD_A/0_DGND", "SINK_SITE0_VDD_A/0_DGND"]
    assert records[0].line == _VRM_A.rstrip("\n")


def test_write_vrm_sink_settings_rewrites_only_targeted_header_values(tmp_path: Path) -> None:
    source = _write(tmp_path / "vrm.spd")
    output = tmp_path / "vrm_out.spd"
    inventory = scan_spd_inventory(source)
    write_spd_with_replacements(
        source, output, inventory.blocks, {}, inventory=inventory,
        vrm_sink_settings=[
            VrmSinkSetting("VRM", "VRM_LGA_VDD_A/0_DGND", (("NominalVoltage", "0.9"), ("SenseVoltage", "0.9"))),
            VrmSinkSetting("Sink", "SINK_SITE0_VDD_A/0_DGND", (("Current", "2.5"), ("Model", "1"))),
        ],
        dc_settings=[DcSetting("VDD_A/0", 0.9, "DGND")],
    )
    text = output.read_text(encoding="utf-8")
    assert text == (
        _PREFIX.replace("\tVDD_A/0 -> PowerNets\n", "\tVDD_A/0 -> PowerNets Voltage = 0.9 GroundNet = DGND\n")
        + '.VRM NominalVoltage = 0.9 SenseVoltage = 0.9 OutputCurrent = 1 Name = "VRM_LGA_VDD_A/0_DGND"\n' + _VRM_BODY
        + _VRM_B + ".EndVRM\n"
        + '.Sink NominalVoltage = 0.85 Current = 2.5 Model = 1 PFMode = 2 PinEqualCurrent = 1 Name = "SINK_SITE0_VDD_A/0_DGND"\n'
        + _SINK_BODY + _SUFFIX
    )
    fresh = {record.key: dict(record.properties) for record in scan_spd_inventory(output).vrm_sink_records}
    assert fresh[("VRM", "VRM_LGA_VDD_A/0_DGND")] == {"NominalVoltage": "0.9", "SenseVoltage": "0.9", "OutputCurrent": "1"}
    assert fresh[("Sink", "SINK_SITE0_VDD_A/0_DGND")]["Current"] == "2.5"


@pytest.mark.parametrize("setting, message", [
    (VrmSinkSetting("VRM", "MISSING", (("NominalVoltage", "1"),)), "Unknown VRM: MISSING"),
    (VrmSinkSetting("Sink", "VRM_LGA_VDD_A/0_DGND", (("NominalVoltage", "1"),)), "Unknown Sink"),
    (VrmSinkSetting("VRM", "VRM_LGA_VDD_A/0_DGND", ()), "No property change"),
    (VrmSinkSetting("VRM", "VRM_LGA_VDD_A/0_DGND", (("Current", "1"),)), "has no Current property"),
    (VrmSinkSetting("VRM", "VRM_LGA_VDD_A/0_DGND", (("Name", "X"),)), "has no Name property"),
    (VrmSinkSetting("VRM", "VRM_LGA_VDD_A/0_DGND", (("NominalVoltage", ""),)), "single unquoted token"),
    (VrmSinkSetting("VRM", "VRM_LGA_VDD_A/0_DGND", (("NominalVoltage", "1 2"),)), "single unquoted token"),
    (VrmSinkSetting("VRM", "VRM_LGA_VDD_A/0_DGND", (("NominalVoltage", "abc"),)), "finite number"),
    (VrmSinkSetting("Sink", "SINK_SITE0_VDD_A/0_DGND", (("Current", "inf"),)), "finite number"),
])
def test_write_vrm_sink_settings_rejects_invalid_requests_without_touching_output(tmp_path: Path, setting, message) -> None:
    source = _write(tmp_path / "vrm.spd")
    output = tmp_path / "vrm_out.spd"
    output.write_text("sentinel", encoding="utf-8")
    inventory = scan_spd_inventory(source)
    with pytest.raises(ValueError, match=message):
        write_spd_with_replacements(source, output, inventory.blocks, {}, inventory=inventory, vrm_sink_settings=[setting])
    assert output.read_text(encoding="utf-8") == "sentinel"


def test_write_vrm_sink_settings_rejects_duplicates_and_stale_headers(tmp_path: Path) -> None:
    source = _write(tmp_path / "vrm.spd")
    output = tmp_path / "vrm_out.spd"
    inventory = scan_spd_inventory(source)
    setting = VrmSinkSetting("VRM", "VRM_LGA_VDD_A/0_DGND", (("NominalVoltage", "1"),))
    with pytest.raises(ValueError, match="Duplicate VRM setting"):
        write_spd_with_replacements(source, output, inventory.blocks, {}, inventory=inventory, vrm_sink_settings=[setting, setting])
    _write(source, _BOARD + _VRM_A + ".EndVRM\n")
    with pytest.raises(ValueError, match="metadata changed"):
        write_spd_with_replacements(source, output, inventory.blocks, {}, inventory=inventory, vrm_sink_settings=[setting])
    duplicated = scan_spd_inventory(source)
    with pytest.raises(ValueError, match="Duplicate VRM names"):
        write_spd_with_replacements(source, output, duplicated.blocks, {}, inventory=duplicated, vrm_sink_settings=[setting])
    assert not output.exists()


def test_record_key_and_is_number() -> None:
    assert VrmSinkRecord("VRM", "X", (), 0, 1, ".VRM").key == ("VRM", "X")
    assert [is_number(text) for text in ("0.85", "1", "-2e-3", "inf", "nan", "abc", "")] == [True, True, True, False, False, False, False]
