import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from spd_model_injector.core.spd import SpdInventory, VrmSinkRecord, VrmSinkSetting, scan_spd_inventory
from spd_model_injector.ui.main_window import MainWindow
from spd_model_injector.ui.workers import ExportWorker

_BOARD = (
    ".Port\n.EndPort\n.NetList\n\tDGND -> GroundNets\n.EndNetList\n"
    '.VRM NominalVoltage = 0.85 SenseVoltage = 0.85 OutputCurrent = 1 Name = "VRM_LGA_VDD_A/0_DGND"\n.EndVRM\n'
    '.VRM NominalVoltage = 1.8 SenseVoltage = 1.8 OutputCurrent = 1 Name = "VRM_LGA_VDD18/0_DGND"\n.EndVRM\n'
    '.Sink NominalVoltage = 0.85 Current = 1 Model = 2 PFMode = 2 PinEqualCurrent = 1 Name = "SINK_SITE0_VDD_A/0_DGND"\n'
    ".SinkCurrentSource\n.EndSinkCurrentSource\n.EndSink\n"
)


def _vrm(name: str, volt: str = "0.95") -> VrmSinkRecord:
    return VrmSinkRecord("VRM", name, (("NominalVoltage", volt), ("SenseVoltage", volt), ("OutputCurrent", "1")), 0, 10, ".VRM")


def _sink(name: str, volt: str = "0.95") -> VrmSinkRecord:
    props = (("NominalVoltage", volt), ("Current", "1"), ("Model", "2"), ("PFMode", "2"), ("PinEqualCurrent", "1"))
    return VrmSinkRecord("Sink", name, props, 0, 10, ".Sink")


_HEADERS = ["Type", "Name", "NominalVoltage", "SenseVoltage", "OutputCurrent", "Current", "Model", "PFMode", "PinEqualCurrent", "Status"]


def _window(tmp_path: Path) -> MainWindow:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    window.spd_path = tmp_path / "board.spd"
    window.spd_path.write_text("source\n", encoding="utf-8")
    window.inventory = SpdInventory([], [], vrm_sink_records=(
        _vrm("VRM_LGA_ADC_VDD_CPU/0_DGND"), _vrm("VRM_LGA_ADC_VDD_CPU/1_DGND"), _vrm("VRM_LGA_ADC_VDD18_PCIE0/0_DGND", "1.8"),
        _sink("SINK_SITE1_ADC_VDD_CPU/1_DGND"), _sink("SINK_SITE0_ADC_VDD18_PCIE0/0_DGND", "1.8"),
    ))
    window._populate_vrm_sink_table()
    return window


def _row_of(window: MainWindow, name: str) -> int:
    for row in range(window.vrm_sink_table.rowCount()):
        if window.vrm_sink_table.item(row, 1).text() == name:
            return row
    raise AssertionError(name)


def _row_texts(window: MainWindow, name: str) -> list[str]:
    row = _row_of(window, name)
    return [window.vrm_sink_table.item(row, column).text() for column in range(window.vrm_sink_table.columnCount())]


def _headers(window: MainWindow) -> list[str]:
    table = window.vrm_sink_table
    return [table.horizontalHeaderItem(column).text() for column in range(table.columnCount())]


def test_vrm_sink_table_lists_union_of_header_properties(tmp_path: Path) -> None:
    window = _window(tmp_path)
    assert window.vrm_sink_table.rowCount() == 5
    assert _headers(window) == _HEADERS
    assert _row_texts(window, "VRM_LGA_ADC_VDD18_PCIE0/0_DGND") == ["VRM", "VRM_LGA_ADC_VDD18_PCIE0/0_DGND", "1.8", "1.8", "1", "", "", "", "", ""]
    assert _row_texts(window, "SINK_SITE1_ADC_VDD_CPU/1_DGND") == ["Sink", "SINK_SITE1_ADC_VDD_CPU/1_DGND", "0.95", "", "", "1", "2", "2", "1", ""]
    assert [window.vrm_sink_property_combo.itemText(i) for i in range(window.vrm_sink_property_combo.count())] == _HEADERS[2:-1]
    assert window.vrm_sink_summary.text() == "VRM/Sink: 5/5 shown; 0 selected; 0 pending"
    assert not window.vrm_sink_apply_button.isEnabled() and not window.apply_vrm_sink_action.isEnabled()
    assert not window.vrm_sink_export_button.isEnabled()


def test_vrm_sink_filter_and_multi_select_apply_queue_visible_rows_only(tmp_path: Path) -> None:
    window = _window(tmp_path)
    window.vrm_sink_table.selectAll()
    window.vrm_sink_filter.setText(" cpu ")
    assert window._selected_vrm_sink_keys() == [
        ("Sink", "SINK_SITE1_ADC_VDD_CPU/1_DGND"), ("VRM", "VRM_LGA_ADC_VDD_CPU/0_DGND"), ("VRM", "VRM_LGA_ADC_VDD_CPU/1_DGND"),
    ]
    assert window._selected_vrm_sink_keys(include_hidden=True) == window._selected_vrm_sink_keys()
    assert window.vrm_sink_summary.text() == "VRM/Sink: 3/5 shown; 3 selected; 0 pending"
    assert window.vrm_sink_apply_button.isEnabled() and window.apply_vrm_sink_action.isEnabled()

    window.vrm_sink_property_combo.setCurrentText("NominalVoltage")
    window.vrm_sink_value_edit.setText("0.9")
    window.apply_vrm_sink_setting_to_selected()
    assert window.vrm_sink_settings == {
        ("VRM", "VRM_LGA_ADC_VDD_CPU/0_DGND"): {"NominalVoltage": "0.9"},
        ("VRM", "VRM_LGA_ADC_VDD_CPU/1_DGND"): {"NominalVoltage": "0.9"},
        ("Sink", "SINK_SITE1_ADC_VDD_CPU/1_DGND"): {"NominalVoltage": "0.9"},
    }
    assert _row_texts(window, "VRM_LGA_ADC_VDD_CPU/0_DGND") == ["VRM", "VRM_LGA_ADC_VDD_CPU/0_DGND", "0.9", "0.95", "1", "", "", "", "", "Pending"]
    row = _row_of(window, "VRM_LGA_ADC_VDD_CPU/0_DGND")
    assert window.vrm_sink_table.item(row, 2).font().bold() and not window.vrm_sink_table.item(row, 3).font().bold()
    assert window.vrm_sink_table.item(row, 2).toolTip() == "File value: 0.95" and window.vrm_sink_table.item(row, 3).toolTip() == ""
    assert window.vrm_sink_summary.text() == "VRM/Sink: 3/5 shown; 3 selected; 3 pending"
    assert window.vrm_sink_export_button.isEnabled() and window.vrm_sink_clear_button.isEnabled() and window.vrm_sink_revert_button.isEnabled()
    assert "Queued NominalVoltage = 0.9 for 3 VRM/Sink(s)" in window.status_log.toPlainText()

    window.vrm_sink_property_combo.setCurrentText("Current")
    window.vrm_sink_value_edit.setText("2.5")
    window.vrm_sink_value_edit.returnPressed.emit()
    assert window.vrm_sink_settings[("Sink", "SINK_SITE1_ADC_VDD_CPU/1_DGND")] == {"NominalVoltage": "0.9", "Current": "2.5"}
    assert window.vrm_sink_settings[("VRM", "VRM_LGA_ADC_VDD_CPU/0_DGND")] == {"NominalVoltage": "0.9"}
    assert "(2 skipped: no Current property)" in window.status_log.toPlainText()
    assert window._queued_vrm_sink_settings() == [
        VrmSinkSetting("Sink", "SINK_SITE1_ADC_VDD_CPU/1_DGND", (("Current", "2.5"), ("NominalVoltage", "0.9"))),
        VrmSinkSetting("VRM", "VRM_LGA_ADC_VDD_CPU/0_DGND", (("NominalVoltage", "0.9"),)),
        VrmSinkSetting("VRM", "VRM_LGA_ADC_VDD_CPU/1_DGND", (("NominalVoltage", "0.9"),)),
    ]

    window.vrm_sink_filter.clear()
    window.vrm_sink_table.clearSelection()
    window.vrm_sink_table.selectRow(_row_of(window, "VRM_LGA_ADC_VDD_CPU/1_DGND"))
    window.revert_vrm_sink_selected()
    assert sorted(window.vrm_sink_settings) == [("Sink", "SINK_SITE1_ADC_VDD_CPU/1_DGND"), ("VRM", "VRM_LGA_ADC_VDD_CPU/0_DGND")]
    assert _row_texts(window, "VRM_LGA_ADC_VDD_CPU/1_DGND")[-1] == ""
    window.clear_vrm_sink_settings()
    assert not window.vrm_sink_settings
    assert not window.vrm_sink_export_button.isEnabled()


def test_vrm_sink_apply_drops_pending_values_identical_to_the_file(tmp_path: Path) -> None:
    window = _window(tmp_path)
    window.vrm_sink_table.selectRow(_row_of(window, "VRM_LGA_ADC_VDD_CPU/0_DGND"))
    window.vrm_sink_property_combo.setCurrentText("OutputCurrent")
    window.vrm_sink_value_edit.setText("3")
    window.apply_vrm_sink_setting_to_selected()
    assert window.vrm_sink_settings == {("VRM", "VRM_LGA_ADC_VDD_CPU/0_DGND"): {"OutputCurrent": "3"}}
    window.vrm_sink_value_edit.setText("1.0")  # numerically equal to the file's "1"
    window.apply_vrm_sink_setting_to_selected()
    assert not window.vrm_sink_settings
    assert "(1 already in the file)" in window.status_log.toPlainText()


def test_vrm_sink_apply_rejects_bad_values_without_queueing(tmp_path: Path, monkeypatch) -> None:
    window = _window(tmp_path)
    warnings: list[str] = []
    monkeypatch.setattr("spd_model_injector.ui.main_window.QMessageBox.warning", lambda *args: warnings.append(args[2]))
    window.apply_vrm_sink_setting_to_selected()
    assert "Select one or more VRMs / Sinks" in window.status_log.toPlainText()
    window.vrm_sink_table.selectRow(_row_of(window, "SINK_SITE1_ADC_VDD_CPU/1_DGND"))
    window.vrm_sink_property_combo.setCurrentText("Current")
    for value in ("", "1 2", 'a"b', "abc"):
        window.vrm_sink_value_edit.setText(value)
        window.apply_vrm_sink_setting_to_selected()
    assert not window.vrm_sink_settings
    assert [text.split(":")[0] for text in warnings] == [
        "Current value must be a single unquoted token", "Current value must be a single unquoted token",
        "Current value must be a single unquoted token", "Current must be a number",
    ]


def test_vrm_sink_apply_refuses_duplicate_names(tmp_path: Path, monkeypatch) -> None:
    window = _window(tmp_path)
    window.inventory = SpdInventory([], [], vrm_sink_records=(_vrm("VRM_DUP"), _vrm("VRM_DUP", "1.8"), _vrm("VRM_OK")))
    window._populate_vrm_sink_table()
    warnings: list[str] = []
    monkeypatch.setattr("spd_model_injector.ui.main_window.QMessageBox.warning", lambda *args: warnings.append(args[2]))
    window.vrm_sink_table.selectAll()
    window.vrm_sink_property_combo.setCurrentText("NominalVoltage")
    window.vrm_sink_value_edit.setText("1")
    window.apply_vrm_sink_setting_to_selected()
    assert not window.vrm_sink_settings
    assert warnings == ["Duplicate names in the SPD cannot be edited unambiguously: VRM VRM_DUP, VRM VRM_DUP"]


def test_vrm_sink_property_columns_sort_numerically(tmp_path: Path) -> None:
    window = _window(tmp_path)
    window.inventory = SpdInventory([], [], vrm_sink_records=(_sink("S_TEN", "10"), _sink("S_TWO", "2"), _sink("S_HALF", "0.5"), _vrm("V_NONE")))
    window._populate_vrm_sink_table()
    window.vrm_sink_table.horizontalHeader().setSortIndicator(2, Qt.SortOrder.AscendingOrder)
    assert [window.vrm_sink_table.item(row, 2).text() for row in range(4)] == ["0.5", "0.95", "2", "10"]
    window.vrm_sink_table.horizontalHeader().setSortIndicator(5, Qt.SortOrder.DescendingOrder)  # PFMode: "" for the VRM row sorts last
    assert [window.vrm_sink_table.item(row, 5).text() for row in range(4)] == ["2", "2", "2", ""]


def test_vrm_sink_context_menu_applies_to_clicked_row(tmp_path: Path, monkeypatch) -> None:
    window = _window(tmp_path)

    class FakeMenu:
        def __init__(self, _parent) -> None:
            self.actions: list[str] = []

        def addAction(self, text: str) -> str:
            self.actions.append(text)
            return text

        def exec(self, _position) -> str:
            assert self.actions[0] == "Apply NominalVoltage (1 rows)"
            return self.actions[0]

    monkeypatch.setattr("spd_model_injector.ui.main_window.QMenu", FakeMenu)
    window.vrm_sink_property_combo.setCurrentText("NominalVoltage")
    window.vrm_sink_value_edit.setText("1.0")
    row = _row_of(window, "SINK_SITE0_ADC_VDD18_PCIE0/0_DGND")
    window._show_vrm_sink_context_menu(window.vrm_sink_table.visualRect(window.vrm_sink_table.model().index(row, 0)).center())
    assert list(window.vrm_sink_settings) == [("Sink", "SINK_SITE0_ADC_VDD18_PCIE0/0_DGND")]


def test_busy_state_disables_vrm_sink_controls_and_export_forwards_settings(tmp_path: Path) -> None:
    window = _window(tmp_path)
    window.vrm_sink_table.selectRow(_row_of(window, "VRM_LGA_ADC_VDD_CPU/0_DGND"))
    window.vrm_sink_property_combo.setCurrentText("SenseVoltage")
    window.vrm_sink_value_edit.setText("1.1")
    window.apply_vrm_sink_setting_to_selected()
    window._set_busy(True)
    assert not window.vrm_sink_table.isEnabled() and not window.vrm_sink_apply_button.isEnabled()
    assert not window.vrm_sink_export_button.isEnabled() and not window.clear_vrm_sink_action.isEnabled()
    window._set_busy(False)
    assert window.vrm_sink_table.isEnabled() and window.vrm_sink_export_button.isEnabled()

    worker = ExportWorker(window.spd_path, tmp_path / "out.spd", [], {}, vrm_sink_settings=window._queued_vrm_sink_settings())
    assert worker.vrm_sink_settings == [VrmSinkSetting("VRM", "VRM_LGA_ADC_VDD_CPU/0_DGND", (("SenseVoltage", "1.1"),))]


def _spin(app: QApplication, window: MainWindow) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and (window._busy or window._scan_thread is not None or window._export_thread is not None):
        app.processEvents()
        time.sleep(0.005)


def test_vrm_sink_workspace_end_to_end_export_and_reload(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "vrm.spd"
    output = tmp_path / "vrm_out.spd"
    source.write_text(_BOARD, encoding="utf-8", newline="\n")
    window = MainWindow()
    window.load_spd(source)
    _spin(app, window)
    window.workspace_tabs.setCurrentIndex(3)
    assert window.vrm_sink_table.rowCount() == 3
    window.vrm_sink_filter.setText("sink")
    window.vrm_sink_table.selectAll()
    window.vrm_sink_property_combo.setCurrentText("Current")
    window.vrm_sink_value_edit.setText("3")
    window.vrm_sink_apply_button.click()
    assert window.vrm_sink_settings == {("Sink", "SINK_SITE0_VDD_A/0_DGND"): {"Current": "3"}}
    window.export_spd(output)
    _spin(app, window)
    text = output.read_text(encoding="utf-8")
    assert '.Sink NominalVoltage = 0.85 Current = 3 Model = 2 PFMode = 2 PinEqualCurrent = 1 Name = "SINK_SITE0_VDD_A/0_DGND"\n' in text
    assert text.count(".VRM NominalVoltage = 0.85 SenseVoltage = 0.85 OutputCurrent = 1") == 1
    assert window.vrm_sink_settings  # retained for repeated exports
    assert len(scan_spd_inventory(output).vrm_sink_records) == 3

    window.load_spd(output)
    _spin(app, window)
    assert not window.vrm_sink_settings
    assert window.vrm_sink_filter.text() == ""
    assert _row_texts(window, "SINK_SITE0_VDD_A/0_DGND") == ["Sink", "SINK_SITE0_VDD_A/0_DGND", "0.85", "", "", "3", "2", "2", "1", ""]
    window.close()
