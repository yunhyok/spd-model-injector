from pathlib import Path

from openpyxl import load_workbook
import pytest

from spd_model_injector.core import refdes_export
from spd_model_injector.core.refdes_export import export_refdes_xlsx
from spd_model_injector.core.spd import RefDesRecord, scan_spd_inventory


def test_export_refdes_xlsx_writes_headers_and_records_in_order(tmp_path: Path) -> None:
    output_path = tmp_path / "refdes.xlsx"
    records = [
        RefDesRecord(component_name="CAP_0402", refdes_name="C100_0", activation_status="Automatic", net_name="5V_A"),
        RefDesRecord(component_name="CAP_0402", refdes_name="C285_0", activation_status="Enabled", net_name="GND"),
        RefDesRecord(component_name="RES_0402", refdes_name="R1", activation_status="Disabled", net_name=""),
    ]

    export_refdes_xlsx(output_path, records)

    workbook = load_workbook(output_path)
    try:
        sheet = workbook.active
        rows = list(sheet.iter_rows(values_only=True))
    finally:
        workbook.close()
    assert rows == [
        ("Component", "RefDes Name", "Activation Status", "Net Name"),
        ("CAP_0402", "C100_0", "Automatic", "5V_A"),
        ("CAP_0402", "C285_0", "Enabled", "GND"),
        ("RES_0402", "R1", "Disabled", None),
    ]


def test_export_refdes_xlsx_reports_power_net_for_swapped_decap_pins(tmp_path: Path) -> None:
    spd_path = tmp_path / "board.spd"
    spd_path.write_text(
        ".Connect C100 CAP_0402 Checked = 1\n"
        "1 $Package.Node1!!1::VDD_1V8\n"
        "2 $Package.Node2!!2::DGND\n"
        ".EndC\n"
        ".Connect C101 CAP_0402 Usage = 0b1000 Checked = 1\n"
        "1 $Package.Node3!!1::DGND\n"
        "2 $Package.Node4!!2::VDD_1V8\n"
        ".EndC\n"
        ".NetList\n"
        "\tDGND -> GroundNets Color = YELLOW\n"
        "\tVDD_1V8 -> PowerNets Color = RED\n"
        ".EndNetList\n",
        encoding="utf-8",
        newline="\n",
    )
    output_path = tmp_path / "refdes.xlsx"

    inventory = scan_spd_inventory(spd_path)
    export_refdes_xlsx(output_path, inventory.refdes_records)

    workbook = load_workbook(output_path)
    try:
        rows = list(workbook.active.iter_rows(values_only=True))
    finally:
        workbook.close()
    assert rows == [
        ("Component", "RefDes Name", "Activation Status", "Net Name"),
        ("CAP_0402", "C100", "Automatic", "VDD_1V8"),
        ("CAP_0402", "C101", "Enabled", "VDD_1V8"),
    ]


def test_export_refdes_xlsx_preserves_existing_file_on_write_error(tmp_path: Path, monkeypatch) -> None:
    output_path = tmp_path / "refdes.xlsx"
    output_path.write_bytes(b"existing workbook")

    def fail_replace(src, dst):
        raise PermissionError("file is open in Excel")

    monkeypatch.setattr(refdes_export.os, "replace", fail_replace)
    with pytest.raises(PermissionError, match="open in Excel"):
        export_refdes_xlsx(
            output_path,
            [RefDesRecord(component_name="CAP_0402", refdes_name="C1", activation_status="Automatic", net_name="5V_A")],
        )
    assert output_path.read_bytes() == b"existing workbook"
    assert [path.name for path in tmp_path.iterdir()] == ["refdes.xlsx"]
