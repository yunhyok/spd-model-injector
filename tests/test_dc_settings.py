from pathlib import Path

import pytest

from spd_model_injector.core.spd import DcSetting, PortRequest, infer_voltage, scan_spd_inventory, write_spd_with_replacements


_DC_NETLIST = (
    ".NetList\n"
    "\t::Unselected RiseTime = 0ps %Coupling = 0\n"
    "\tGroundNets Color = LIME\n"
    "\tPowerNets Color = RED\n"
    "\tSIG_A::Unselected Color = BLUE\n"
    "\tDGND -> GroundNets Color = YELLOW Voltage = 0\n"
    "\tVDD_OFF/0 -> PowerNets::Unselected Color = RED\n"
    "\tVDD085_A/0 Color = BLUE\n"
    "\tVDD18_B/1::Unselected||DropShape Color = OLIVE\n"
    "\tVDD_GPU/0 Color = DARKMAGENTA Voltage = 0\n"
    "\tVDD_DONE/0||DropShape Color = RED Voltage = 0.75 GroundNet = DGND\n"
    ".EndNetList\n"
)


def test_scan_records_every_powernets_member_with_offsets_and_dc_attributes(tmp_path: Path) -> None:
    source = tmp_path / "dc.spd"
    source.write_text(".Port\n.EndPort\n" + _DC_NETLIST, encoding="utf-8", newline="\n")
    inventory = scan_spd_inventory(source)
    records = {record.net_name: record for record in inventory.power_net_records}

    assert list(records) == ["VDD_OFF/0", "VDD085_A/0", "VDD18_B/1", "VDD_GPU/0", "VDD_DONE/0"]
    assert inventory.power_nets == ("VDD085_A/0", "VDD_GPU/0", "VDD_DONE/0")
    assert inventory.ground_nets == ("DGND",)
    assert [(name, record.selected, record.voltage, record.ground_net) for name, record in records.items()] == [
        ("VDD_OFF/0", False, "", ""), ("VDD085_A/0", True, "", ""), ("VDD18_B/1", False, "", ""),
        ("VDD_GPU/0", True, "0", ""), ("VDD_DONE/0", True, "0.75", "DGND"),
    ]
    raw = source.read_bytes()
    for record in records.values():
        assert raw[record.line_start_offset:record.line_end_offset] == (record.line + "\n").encode()


def test_scan_strips_dropshape_flags_from_powerdc_saved_group_tokens(tmp_path: Path) -> None:
    source = tmp_path / "powerdc.spd"
    source.write_text(
        ".Port\n.EndPort\n.NetList\n\tDGND -> GroundNets||DropShape Color = YELLOW Voltage = 0\n"
        "\tVDD/0 -> PowerNets||DropShape Color = RED Voltage = 0.95 GroundNet = DGND\n"
        "\tVDD/1||DropShape Color = RED\n.EndNetList\n",
        encoding="utf-8", newline="\n",
    )
    inventory = scan_spd_inventory(source)
    assert inventory.ground_nets == ("DGND",)
    assert inventory.power_nets == ("VDD/0", "VDD/1")
    assert inventory.net_names == ("DGND", "VDD/0", "VDD/1")
    assert [record.net_name for record in inventory.power_net_records] == ["VDD/0", "VDD/1"]


def test_write_dc_settings_rewrites_only_targeted_netlist_lines(tmp_path: Path) -> None:
    source = tmp_path / "dc.spd"
    output = tmp_path / "dc_out.spd"
    prefix = ".PartialCkt CAP ExtNode = 1 2\nC 1 2 1u\n.EndPartialCkt\n.Port\n.EndPort\n"
    source.write_text(prefix + _DC_NETLIST + ".End\n", encoding="utf-8", newline="\n")
    inventory = scan_spd_inventory(source)
    write_spd_with_replacements(
        source, output, inventory.blocks, {}, inventory=inventory,
        dc_settings=[
            DcSetting("VDD085_A/0", 0.85, "DGND"),
            DcSetting("VDD18_B/1", 1.8, "DGND"),
            DcSetting("VDD_OFF/0", 1.0, "DGND"),
            DcSetting("VDD_GPU/0", 0.95, "DGND"),
        ],
    )
    text = output.read_text(encoding="utf-8")
    assert text == prefix + (
        ".NetList\n"
        "\t::Unselected RiseTime = 0ps %Coupling = 0\n"
        "\tGroundNets Color = LIME\n"
        "\tPowerNets Color = RED\n"
        "\tSIG_A::Unselected Color = BLUE\n"
        "\tDGND -> GroundNets Color = YELLOW Voltage = 0\n"
        "\tVDD_OFF/0 -> PowerNets Color = RED Voltage = 1 GroundNet = DGND\n"
        "\tVDD085_A/0 Color = BLUE Voltage = 0.85 GroundNet = DGND\n"
        "\tVDD18_B/1||DropShape Color = OLIVE Voltage = 1.8 GroundNet = DGND\n"
        "\tVDD_GPU/0 Color = DARKMAGENTA Voltage = 0.95 GroundNet = DGND\n"
        "\tVDD_DONE/0||DropShape Color = RED Voltage = 0.75 GroundNet = DGND\n"
        ".EndNetList\n.End\n"
    )
    fresh = scan_spd_inventory(output)
    assert fresh.power_nets == ("VDD_OFF/0", "VDD085_A/0", "VDD18_B/1", "VDD_GPU/0", "VDD_DONE/0")
    assert all(record.selected and record.ground_net == "DGND" for record in fresh.power_net_records)


def test_write_dc_settings_combines_with_port_generation(tmp_path: Path) -> None:
    source = tmp_path / "combo.spd"
    output = tmp_path / "combo_out.spd"
    source.write_text(
        ".Connect C1 CAP Checked = 1\n1 $Package.Node1!!1::VDD/0\n2 $Package.Node2!!2::DGND\n.EndC\n"
        "* Port description lines\n\n.NetList\nVDD/0 -> PowerNets\nDGND -> GroundNets\n.EndNetList\n",
        encoding="utf-8", newline="\n",
    )
    inventory = scan_spd_inventory(source)
    write_spd_with_replacements(
        source, output, inventory.blocks, {}, inventory=inventory, refdes_records=inventory.refdes_records,
        port_requests=[PortRequest("C1", "VDD/0", "DGND")], dc_settings=[DcSetting("VDD/0", 1.2, "DGND")],
    )
    text = output.read_text(encoding="utf-8")
    assert "Port1_C1_1::VDD/0 Auto" in text
    assert "\nVDD/0 -> PowerNets Voltage = 1.2 GroundNet = DGND\n" in text
    assert scan_spd_inventory(output).existing_port_keys == (("C1", "VDD/0"),)


@pytest.mark.parametrize("setting, message", [
    (DcSetting("MISSING", 1.0, "DGND"), "Unknown power NET"),
    (DcSetting("VDD085_A/0", 1.0, ""), "required"),
    (DcSetting("VDD085_A/0", 1.0, "VDD085_A/0"), "must differ"),
    (DcSetting("VDD085_A/0", 1.0, "NOPE"), "not present in .NetList"),
    (DcSetting("VDD085_A/0", float("nan"), "DGND"), "finite"),
])
def test_write_dc_settings_rejects_invalid_requests_without_touching_output(tmp_path: Path, setting, message) -> None:
    source = tmp_path / "dc.spd"
    output = tmp_path / "dc_out.spd"
    source.write_text(".Port\n.EndPort\n" + _DC_NETLIST, encoding="utf-8", newline="\n")
    output.write_text("sentinel", encoding="utf-8")
    inventory = scan_spd_inventory(source)
    with pytest.raises(ValueError, match=message):
        write_spd_with_replacements(source, output, inventory.blocks, {}, inventory=inventory, dc_settings=[setting])
    assert output.read_text(encoding="utf-8") == "sentinel"


def test_write_dc_settings_rejects_duplicate_and_stale_netlist(tmp_path: Path) -> None:
    source = tmp_path / "dc.spd"
    output = tmp_path / "dc_out.spd"
    source.write_text(".Port\n.EndPort\n" + _DC_NETLIST, encoding="utf-8", newline="\n")
    inventory = scan_spd_inventory(source)
    with pytest.raises(ValueError, match="Duplicate DC setting"):
        write_spd_with_replacements(source, output, inventory.blocks, {}, inventory=inventory,
                                    dc_settings=[DcSetting("VDD085_A/0", 1.0, "DGND"), DcSetting("VDD085_A/0", 0.9, "DGND")])
    source.write_text(".Port\n.EndPort\n" + _DC_NETLIST.replace("Color = BLUE\n", "Color = RED\n"), encoding="utf-8", newline="\n")
    with pytest.raises(ValueError, match="metadata changed"):
        write_spd_with_replacements(source, output, inventory.blocks, {}, inventory=inventory,
                                    dc_settings=[DcSetting("VDD085_A/0", 1.0, "DGND")])
    assert not output.exists()


@pytest.mark.parametrize("name, expected", [
    ("ADC_VDD085_DCPHY0/1", 0.85), ("ADC_VDD075_CLKMON_CPU_GPU/0", 0.75), ("ADC_VDD18_PCIE1/1", 1.8),
    ("VDD12_IO/3", 1.2), ("VDD105_CORE", 1.05), ("VCC33/0", 3.3), ("AVDD_1V8_PLL", 1.8), ("VDD_0P75", 0.75),
    ("XDCPHY2_VREG_0P4V/3", 0.4), ("VDD_1.2V", 1.2), ("VBUS_5V", 5.0), ("VDD9/12", None),
    ("ADC_VDDI_TRIP0/0", None), ("ADC_VDD_GPU/1", None), ("ADC_ADC_VDDQ_DRAM0/3", None), ("VDD00", None),
])
def test_infer_voltage_from_net_name(name: str, expected: float | None) -> None:
    assert infer_voltage(name) == expected
