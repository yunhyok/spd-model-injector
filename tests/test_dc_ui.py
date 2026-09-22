import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from spd_model_injector.core.spd import DcSetting, PowerNetRecord, SpdInventory, scan_spd_inventory
from spd_model_injector.ui.main_window import MainWindow
from spd_model_injector.ui.workers import ExportWorker


def _record(name: str, selected: bool = True, voltage: str = "", ground: str = "", offset: int = 0) -> PowerNetRecord:
    return PowerNetRecord(name, selected, voltage, ground, offset, offset + 10, f"\t{name} Color = RED")


def _dc_window(tmp_path: Path) -> MainWindow:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    window.spd_path = tmp_path / "board.spd"
    window.spd_path.write_text("source\n", encoding="utf-8")
    records = (
        _record("ADC_VDDI_TRIP0/0"), _record("ADC_VDDI_TRIP0/1"), _record("ADC_VDDI_TRIP1/0", selected=False),
        _record("ADC_VDD085_DCPHY0/0", voltage="0.85", ground="DGND"), _record("ADC_VDD_GPU/0", voltage="0"),
    )
    window.inventory = SpdInventory(
        [], [], net_names=("DGND", "ADC_VDDI_TRIP0/0", "ADC_VDDI_TRIP0/1", "ADC_VDD085_DCPHY0/0", "ADC_VDD_GPU/0"),
        power_nets=("ADC_VDDI_TRIP0/0", "ADC_VDDI_TRIP0/1", "ADC_VDD085_DCPHY0/0", "ADC_VDD_GPU/0"),
        ground_nets=("DGND",), power_net_records=records, port_insertion_offset=1,
    )
    window._set_dc_ground_options(("DGND",), window.inventory.net_names)
    window._populate_dc_table()
    return window


def _row_of(window: MainWindow, name: str) -> int:
    for row in range(window.dc_table.rowCount()):
        if window.dc_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == name:
            return row
    raise AssertionError(name)


def _row_texts(window: MainWindow, name: str) -> list[str]:
    row = _row_of(window, name)
    return [window.dc_table.item(row, column).text() for column in range(5)]


def test_dc_table_lists_channels_with_existing_values_and_default_ground(tmp_path: Path) -> None:
    window = _dc_window(tmp_path)
    assert window.dc_table.rowCount() == 5
    assert window.dc_ground_combo.currentText() == "DGND"
    assert _row_texts(window, "ADC_VDD085_DCPHY0/0") == ["ADC_VDD085_DCPHY0/0", "Yes", "DGND", "0.85", "Existing"]
    assert _row_texts(window, "ADC_VDD_GPU/0") == ["ADC_VDD_GPU/0", "Yes", "", "0", "Existing"]
    assert _row_texts(window, "ADC_VDDI_TRIP1/0") == ["ADC_VDDI_TRIP1/0", "No", "", "", ""]
    assert window.dc_summary.text() == "Power NETs: 5/5 shown; 0 selected; 0 pending"
    assert not window.dc_apply_button.isEnabled() and not window.apply_dc_action.isEnabled()
    assert not window.dc_export_button.isEnabled()


def test_dc_filter_and_multi_select_apply_queue_settings_for_visible_rows_only(tmp_path: Path) -> None:
    window = _dc_window(tmp_path)
    window.dc_table.selectAll()
    window.dc_net_filter.setText(" trip ")
    hidden = [name for name in ("ADC_VDD085_DCPHY0/0", "ADC_VDD_GPU/0") if window.dc_table.isRowHidden(_row_of(window, name))]
    assert hidden == ["ADC_VDD085_DCPHY0/0", "ADC_VDD_GPU/0"]
    assert window._selected_dc_nets() == ["ADC_VDDI_TRIP0/0", "ADC_VDDI_TRIP0/1", "ADC_VDDI_TRIP1/0"]
    assert window._selected_dc_nets(include_hidden=True) == window._selected_dc_nets()
    assert window.dc_summary.text() == "Power NETs: 3/5 shown; 3 selected; 0 pending"
    assert window.dc_apply_button.isEnabled() and window.apply_dc_action.isEnabled()

    window.dc_volt_edit.setText("0.95")
    window.apply_dc_settings_to_selected()
    assert window.dc_settings == {
        "ADC_VDDI_TRIP0/0": DcSetting("ADC_VDDI_TRIP0/0", 0.95, "DGND"),
        "ADC_VDDI_TRIP0/1": DcSetting("ADC_VDDI_TRIP0/1", 0.95, "DGND"),
        "ADC_VDDI_TRIP1/0": DcSetting("ADC_VDDI_TRIP1/0", 0.95, "DGND"),
    }
    assert _row_texts(window, "ADC_VDDI_TRIP1/0") == ["ADC_VDDI_TRIP1/0", "No → Yes", "DGND", "0.95", "Pending"]
    assert _row_texts(window, "ADC_VDDI_TRIP0/0")[4] == "Pending"
    assert window.dc_table.item(_row_of(window, "ADC_VDDI_TRIP0/0"), 0).font().bold()
    assert window._selected_dc_nets() == ["ADC_VDDI_TRIP0/0", "ADC_VDDI_TRIP0/1", "ADC_VDDI_TRIP1/0"]
    assert window.dc_summary.text() == "Power NETs: 3/5 shown; 3 selected; 3 pending"
    assert window.dc_export_button.isEnabled() and window.dc_clear_button.isEnabled() and window.dc_revert_button.isEnabled()
    assert "Queued DC setting for 3 channel(s) with DGND" in window.status_log.toPlainText()

    window.dc_net_filter.clear()
    window.dc_table.clearSelection()
    window.dc_table.selectRow(_row_of(window, "ADC_VDDI_TRIP0/1"))
    window.revert_dc_selected()
    assert sorted(window.dc_settings) == ["ADC_VDDI_TRIP0/0", "ADC_VDDI_TRIP1/0"]
    assert _row_texts(window, "ADC_VDDI_TRIP0/1")[4] == ""
    window.clear_dc_settings()
    assert not window.dc_settings
    assert not window.dc_export_button.isEnabled()


def test_dc_sorting_keeps_filter_applied_to_matching_rows(tmp_path: Path) -> None:
    window = _dc_window(tmp_path)
    window.dc_net_filter.setText("GPU")
    window.dc_table.sortItems(0, Qt.SortOrder.DescendingOrder)
    window.dc_table.horizontalHeader().setSortIndicator(0, Qt.SortOrder.DescendingOrder)
    visible = [window.dc_table.item(row, 0).text() for row in range(window.dc_table.rowCount()) if not window.dc_table.isRowHidden(row)]
    assert visible == ["ADC_VDD_GPU/0"]


def test_dc_auto_fill_reads_voltage_from_net_names_and_skips_unchanged_rows(tmp_path: Path) -> None:
    window = _dc_window(tmp_path)
    window.dc_table.selectAll()
    window.auto_fill_dc_selected()
    assert {name: setting.voltage for name, setting in window.dc_settings.items()} == {
        "ADC_VDDI_TRIP0/0": 1.0, "ADC_VDDI_TRIP0/1": 1.0, "ADC_VDDI_TRIP1/0": 1.0, "ADC_VDD_GPU/0": 1.0,
    }
    assert "ADC_VDD085_DCPHY0/0" not in window.dc_settings  # already 0.85 / DGND in the file
    assert _row_texts(window, "ADC_VDD085_DCPHY0/0")[4] == "Existing"


def test_dc_apply_rejects_bad_voltage_or_unknown_ground_without_queueing(tmp_path: Path, monkeypatch) -> None:
    window = _dc_window(tmp_path)
    warnings: list[str] = []
    monkeypatch.setattr("spd_model_injector.ui.main_window.QMessageBox.warning", lambda *args: warnings.append(args[2]))
    window.dc_table.selectRow(_row_of(window, "ADC_VDDI_TRIP0/0"))
    window.dc_volt_edit.setText("abc")
    window.apply_dc_settings_to_selected()
    window.dc_volt_edit.setText("0.9")
    window.dc_ground_combo.setCurrentText("NOPE")
    window.apply_dc_settings_to_selected()
    window.dc_ground_combo.setCurrentText("ADC_VDDI_TRIP0/0")
    window.apply_dc_settings_to_selected()
    assert not window.dc_settings
    assert [text.split(":")[0] for text in warnings] == [
        "Volt (V) must be a number", "Pairing P/G NET must be an active NET in .NetList",
    ]
    assert "1 skipped: same as Pairing NET" in window.status_log.toPlainText()


def test_dc_context_menu_applies_to_clicked_row(tmp_path: Path, monkeypatch) -> None:
    window = _dc_window(tmp_path)

    class FakeMenu:
        def __init__(self, _parent) -> None:
            self.actions: list[str] = []

        def addAction(self, text: str) -> str:
            self.actions.append(text)
            return text

        def exec(self, _position) -> str:
            return self.actions[1]

    monkeypatch.setattr("spd_model_injector.ui.main_window.QMenu", FakeMenu)
    row = _row_of(window, "ADC_VDD_GPU/0")
    position = window.dc_table.visualRect(window.dc_table.model().index(row, 0)).center()
    window._show_dc_context_menu(position)
    assert list(window.dc_settings) == ["ADC_VDD_GPU/0"]


def test_busy_state_disables_dc_controls_and_export_forwards_settings(tmp_path: Path) -> None:
    window = _dc_window(tmp_path)
    window.dc_table.selectRow(_row_of(window, "ADC_VDDI_TRIP0/0"))
    window.dc_volt_edit.setText("1.1")
    window.apply_dc_settings_to_selected()
    window._set_busy(True)
    assert not window.dc_table.isEnabled() and not window.dc_apply_button.isEnabled()
    assert not window.dc_export_button.isEnabled() and not window.clear_dc_action.isEnabled()
    window._set_busy(False)
    assert window.dc_table.isEnabled() and window.dc_export_button.isEnabled()

    worker = ExportWorker(window.spd_path, tmp_path / "out.spd", [], {}, dc_settings=list(window.dc_settings.values()))
    assert worker.dc_settings == [DcSetting("ADC_VDDI_TRIP0/0", 1.1, "DGND")]


def test_dc_workspace_end_to_end_export_and_reload(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "dc.spd"
    output = tmp_path / "dc_out.spd"
    source.write_text(
        ".Port\n.EndPort\n.NetList\n\tDGND -> GroundNets Color = YELLOW Voltage = 0\n"
        "\tVDD18_IO/0 -> PowerNets Color = RED\n\tVDD18_IO/1::Unselected Color = RED\n\tVDD_CORE/0 Color = BLUE\n.EndNetList\n",
        encoding="utf-8", newline="\n",
    )
    window = MainWindow()
    window.load_spd(source)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and (window._busy or window._scan_thread is not None):
        app.processEvents()
        time.sleep(0.005)
    window.workspace_tabs.setCurrentIndex(2)
    assert window.dc_table.rowCount() == 3
    window.dc_net_filter.setText("VDD18")
    window.dc_table.selectAll()
    window.dc_auto_button.click()
    assert {name: setting.voltage for name, setting in window.dc_settings.items()} == {"VDD18_IO/0": 1.8, "VDD18_IO/1": 1.8}
    window.export_spd(output)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and (window._busy or window._export_thread is not None):
        app.processEvents()
        time.sleep(0.005)
    text = output.read_text(encoding="utf-8")
    assert "\tVDD18_IO/0 -> PowerNets Color = RED Voltage = 1.8 GroundNet = DGND\n" in text
    assert "\tVDD18_IO/1 Color = RED Voltage = 1.8 GroundNet = DGND\n" in text
    assert "\tVDD_CORE/0 Color = BLUE\n" in text
    assert window.dc_settings  # retained for repeated exports
    assert scan_spd_inventory(output).power_nets == ("VDD18_IO/0", "VDD18_IO/1", "VDD_CORE/0")

    window.load_spd(output)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and (window._busy or window._scan_thread is not None):
        app.processEvents()
        time.sleep(0.005)
    assert not window.dc_settings
    assert window.dc_net_filter.text() == ""
    assert _row_texts(window, "VDD18_IO/1") == ["VDD18_IO/1", "Yes", "DGND", "1.8", "Existing"]
    window.close()
