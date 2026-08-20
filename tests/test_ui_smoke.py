import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from openpyxl import Workbook, load_workbook
from PySide6.QtCore import QEventLoop, QItemSelectionModel, QTimer
from PySide6.QtWidgets import QApplication, QAbstractItemView, QFrame, QHeaderView, QMessageBox, QPlainTextEdit, QSplitter

from spd_model_injector import __version__
from spd_model_injector.core.spd import PartialCktBlock, RefDesRecord, SpdInventory
from spd_model_injector.ui.main_window import MainWindow
from spd_model_injector.ui.workers import ExportWorker


def _spin_until(app: QApplication, predicate, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError(f"Timed out after {timeout}s waiting for: {what}")


def _make_block(name: str, ext_nodes: list[str] | None = None) -> PartialCktBlock:
    ext_nodes = ext_nodes if ext_nodes is not None else ["1", "2"]
    return PartialCktBlock(
        component_name=name,
        ext_nodes=ext_nodes,
        start_line=1,
        end_line=3,
        block_start_offset=0,
        body_start_offset=30,
        body_end_offset=40,
        block_end_offset=55,
        header_lines=[f".PartialCkt {name} ExtNode =  " + " ".join(ext_nodes)],
    )


def test_main_window_has_expected_title_and_empty_initial_state() -> None:
    app = QApplication.instance() or QApplication([])

    window = MainWindow()

    assert app is not None
    assert window.windowTitle() == f"SPD Model Injector {__version__}"
    assert window.component_list.count() == 0
    assert "Load an SPD file" in window.status_label.text()
    assert window.undo_component_change_action is not None
    assert window.undo_component_change_action.shortcut().toString() == "Ctrl+Z"


def test_main_window_places_refdes_list_in_right_side_work_area() -> None:
    QApplication.instance() or QApplication([])

    window = MainWindow()
    root = window.centralWidget()
    work_splitter = window.findChild(QSplitter, "work_splitter")
    toolbar_actions = [action.text() for action in window.toolBar.actions()]

    assert isinstance(root, QSplitter)
    assert root.count() == 2
    assert work_splitter is not None
    assert work_splitter.orientation() == window.centralWidget().orientation()
    assert work_splitter.count() == 2
    assert work_splitter.widget(0).findChild(type(window.editor)) is window.editor
    assert work_splitter.widget(1).findChild(type(window.refdes_table)) is window.refdes_table
    assert window.status_log.parent() is not work_splitter.widget(0)
    assert window.status_log.parent() is not work_splitter.widget(1)
    assert "Export RefDes Excel" in toolbar_actions
    assert "Import RefDes Status Excel" in toolbar_actions
    assert toolbar_actions.index("Import RefDes Status Excel") == toolbar_actions.index("Export RefDes Excel") + 1


def test_main_window_refdes_list_has_framed_sortable_resizable_columns() -> None:
    QApplication.instance() or QApplication([])

    window = MainWindow()
    header = window.refdes_table.horizontalHeader()

    assert window.refdes_panel.frameShape() != QFrame.Shape.NoFrame
    assert window.refdes_panel.layout().indexOf(window.refdes_table) >= 0
    assert window.refdes_table.isSortingEnabled()
    assert header.sectionsClickable()
    assert header.isSortIndicatorShown()
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive
    assert header.sectionResizeMode(1) == QHeaderView.ResizeMode.Interactive
    assert window.refdes_table.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection
    assert window.refdes_table.acceptDrops()


def test_main_window_import_model_text_maps_selected_block_ports(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    block = _make_block("C1")
    window.spd_path = tmp_path / "board.spd"
    window.blocks = [block]
    window.populate_components()
    assert window.component_list.item(0).text().startswith("C1")
    window.component_list.setCurrentRow(0)

    window.import_model_text(".SUBCKT CAP Port1 Port2\nC1 Port1 Port2 1u\n.ENDS CAP\n")

    assert window.editor.toPlainText() == "C1 1 2 1u\n"
    assert window.replacements == {"C1": "C1 1 2 1u\n"}
    assert "OK" in window.validation_label.text()


def test_main_window_refdes_table_follows_selected_component(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    cap_block = _make_block("CAP_0402")
    res_block = PartialCktBlock(
        component_name="RES_0402",
        ext_nodes=["A", "B"],
        start_line=4,
        end_line=6,
        block_start_offset=56,
        body_start_offset=90,
        body_end_offset=100,
        block_end_offset=115,
        header_lines=[".PartialCkt RES_0402 ExtNode =  A B"],
    )
    window.spd_path = tmp_path / "board.spd"
    window.inventory = SpdInventory(
        blocks=[cap_block, res_block],
        refdes_records=[
            RefDesRecord(component_name="CAP_0402", refdes_name="C100_0", activation_status="Automatic"),
            RefDesRecord(component_name="CAP_0402", refdes_name="C285_0", activation_status="Enabled"),
            RefDesRecord(component_name="RES_0402", refdes_name="R1", activation_status="Disabled"),
        ],
    )
    window.blocks = window.inventory.blocks
    window.refdes_records = window.inventory.refdes_records
    window.refdes_by_component = window.inventory.refdes_by_component
    window.populate_components()

    window.component_list.setCurrentRow(0)

    assert window.refdes_table.rowCount() == 2
    assert window.refdes_table.item(0, 0).text() == "C100_0"
    assert window.refdes_table.item(0, 1).text() == "Automatic"
    assert "CAP_0402" in window.refdes_label.text()

    window.component_list.setCurrentRow(1)

    assert window.refdes_table.rowCount() == 1
    assert window.refdes_table.item(0, 0).text() == "R1"
    assert window.refdes_table.item(0, 1).text() == "Disabled"
    assert "RES_0402" in window.refdes_label.text()


def test_main_window_changes_multiple_refdes_components_and_undoes_batch(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    cap_block = _make_block("CAP_0402")
    res_block = PartialCktBlock(
        component_name="RES_0402",
        ext_nodes=["A", "B"],
        start_line=4,
        end_line=6,
        block_start_offset=56,
        body_start_offset=90,
        body_end_offset=100,
        block_end_offset=115,
        header_lines=[".PartialCkt RES_0402 ExtNode =  A B"],
    )
    window.spd_path = tmp_path / "board.spd"
    window.inventory = SpdInventory(
        blocks=[cap_block, res_block],
        refdes_records=[
            RefDesRecord(component_name="CAP_0402", refdes_name="C100_0", activation_status="Automatic"),
            RefDesRecord(component_name="CAP_0402", refdes_name="C285_0", activation_status="Enabled"),
            RefDesRecord(component_name="RES_0402", refdes_name="R1", activation_status="Disabled"),
        ],
    )
    window.blocks = window.inventory.blocks
    window.refdes_records = window.inventory.refdes_records
    window.refdes_by_component = window.inventory.refdes_by_component
    window.populate_components()
    window.component_list.setCurrentRow(0)

    first = window.refdes_table.model().index(0, 0)
    second = window.refdes_table.model().index(1, 0)
    selection = window.refdes_table.selectionModel()
    selection.select(first, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    selection.select(second, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    window.apply_refdes_component_changes(window.selected_refdes_names(), "RES_0402")

    assert window.refdes_component_changes == {"C100_0": "RES_0402", "C285_0": "RES_0402"}
    assert window.refdes_table.rowCount() == 0
    assert window.undo_component_change_action is not None
    assert window.undo_component_change_action.isEnabled()

    window.undo_refdes_component_change()

    assert window.refdes_component_changes == {}
    assert window.refdes_table.rowCount() == 2
    assert not window.undo_component_change_action.isEnabled()


def test_main_window_refdes_excel_export_uses_changed_components(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    window.blocks = [
        PartialCktBlock("CAP_0402", ["1"], 1, 2, 0, 10, 11, 12, [".PartialCkt CAP_0402 ExtNode = 1"]),
        PartialCktBlock("CAP_0603", ["1"], 3, 4, 13, 20, 21, 22, [".PartialCkt CAP_0603 ExtNode = 1"]),
    ]
    window.refdes_records = [RefDesRecord(component_name="CAP_0402", refdes_name="C100_0", activation_status="Automatic")]
    window.rebuild_refdes_groups()
    window.apply_refdes_component_changes(["C100_0"], "CAP_0603")
    output_path = tmp_path / "refdes.xlsx"

    window.export_refdes_excel(output_path)

    workbook = load_workbook(output_path)
    rows = list(workbook.active.iter_rows(values_only=True))
    workbook.close()

    assert rows == [
        ("Component", "RefDes Name", "Activation Status", "Net Name"),
        ("CAP_0603", "C100_0", "Automatic", None),
    ]


def test_main_window_imports_refdes_status_excel_with_legacy_header(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    window.blocks = [_make_block("CAP_0402"), _make_block("RES_0402")]
    window.refdes_records = [
        RefDesRecord(component_name="CAP_0402", refdes_name="C100_0", activation_status="Automatic"),
        RefDesRecord(component_name="CAP_0402", refdes_name="C285_0", activation_status="Enabled"),
        RefDesRecord(component_name="RES_0402", refdes_name="R1", activation_status="Disabled"),
    ]
    window.rebuild_refdes_groups()
    window.populate_components()
    window.component_list.setCurrentRow(0)
    status_path = tmp_path / "status.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Component", "RefDes Name", "Activation Status"])
    sheet.append(["CAP_0402", "C100_0", "Enabled"])
    sheet.append(["CAP_0402", "C285_0", "Disabled"])
    sheet.append(["RES_0402", "R1", "Automatic"])
    workbook.save(status_path)

    window.import_refdes_status_file(status_path)

    assert window.refdes_activation_status_changes == {
        "C100_0": "Enabled",
        "C285_0": "Disabled",
        "R1": "Automatic",
    }
    assert [(record.refdes_name, record.activation_status) for record in window.effective_refdes_records()] == [
        ("C100_0", "Enabled"),
        ("C285_0", "Disabled"),
        ("R1", "Automatic"),
    ]
    assert window.refdes_table.item(0, 1).text() == "Enabled"
    assert window.refdes_table.item(1, 1).text() == "Disabled"
    assert "Imported 3 RefDes activation status" in window.status_log.toPlainText()


def test_main_window_imports_four_column_refdes_export(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    window.blocks = [_make_block("CAP_0402")]
    window.refdes_records = [
        RefDesRecord(component_name="CAP_0402", refdes_name="C100_0", activation_status="Automatic", net_name="5V_A"),
    ]
    status_path = tmp_path / "status_export.xlsx"
    window.export_refdes_excel(status_path)

    window.import_refdes_status_file(status_path)

    assert window.refdes_activation_status_changes == {}
    assert window.validate_refdes_status_file(status_path) == []


def test_main_window_accepts_partial_refdes_status_excel_and_rejects_unexpected_names(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    window.blocks = [_make_block("CAP_0402")]
    window.refdes_records = [
        RefDesRecord(component_name="CAP_0402", refdes_name="C100_0", activation_status="Automatic"),
        RefDesRecord(component_name="CAP_0402", refdes_name="C285_0", activation_status="Enabled"),
    ]
    window.rebuild_refdes_groups()
    mismatch_path = tmp_path / "status_mismatch.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Component", "RefDes Name", "Activation Status"])
    sheet.append(["CAP_0402", "C100_0", "Disabled"])
    sheet.append(["CAP_0402", "C999_0", "Enabled"])
    workbook.save(mismatch_path)
    partial_path = tmp_path / "status_partial.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Component type", "REFDES", "Status"])
    sheet.append(["CAP_0402", "C100_0", "Disabled"])
    workbook.save(partial_path)
    legacy_partial_path = tmp_path / "status_legacy_partial.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Component", "RefDes Name", "Activation Status"])
    sheet.append(["CAP_0402", "C100_0", "Disabled"])
    workbook.save(legacy_partial_path)

    assert "Unexpected RefDes: C999_0" in window.validate_refdes_status_file(mismatch_path)
    assert window.validate_refdes_status_file(partial_path) == []
    assert "Missing RefDes: C285_0" in window.validate_refdes_status_file(legacy_partial_path)
    assert "RefDes count mismatch: expected 2, got 1" in window.validate_refdes_status_file(legacy_partial_path)

    window.import_refdes_status_file(partial_path)

    assert window.refdes_activation_status_changes == {"C100_0": "Disabled"}
    assert window.effective_activation_status_for_refdes("C285_0") == "Enabled"


def test_main_window_imports_c01_style_refdes_status_excel_as_partial_update(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    window.blocks = [_make_block("CAP_0402_100NF")]
    window.refdes_records = [
        RefDesRecord(component_name="CAP_0402_100NF", refdes_name="C1077_0", activation_status="Automatic"),
        RefDesRecord(component_name="CAP_0402_100NF", refdes_name="C164_0", activation_status="Disabled"),
        RefDesRecord(component_name="CAP_0402_100NF", refdes_name="C1149_0", activation_status="Enabled"),
        RefDesRecord(component_name="CAP_0402_100NF", refdes_name="C1094_0", activation_status="Automatic"),
    ]
    window.rebuild_refdes_groups()
    status_path = tmp_path / "c01_style.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Component type", "REFDES", "Status"])
    sheet.append(["CAP_0402_100NF", "C1077_0", "Enabled"])
    sheet.append(["CAP_0402_100NF", "C164_0", "Automatic"])
    sheet.append(["CAP_0402_100NF", "C1149_0", "Disabled"])
    workbook.save(status_path)

    window.import_refdes_status_file(status_path)

    assert window.refdes_activation_status_changes == {
        "C1077_0": "Enabled",
        "C164_0": "Automatic",
        "C1149_0": "Disabled",
    }
    assert window.effective_activation_status_for_refdes("C1094_0") == "Automatic"


def test_main_window_refdes_status_import_keeps_strict_row_validation(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    window.refdes_records = [
        RefDesRecord(component_name="CAP_0402", refdes_name="C100_0", activation_status="Automatic"),
        RefDesRecord(component_name="CAP_0402", refdes_name="C285_0", activation_status="Enabled"),
    ]

    def write_status_file(name: str, rows: list[list[str]]) -> Path:
        path = tmp_path / name
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Component type", "REFDES", "Status"])
        for row in rows:
            sheet.append(row)
        workbook.save(path)
        return path

    duplicate_path = write_status_file(
        "status_duplicate.xlsx",
        [["CAP_0402", "C100_0", "Enabled"], ["CAP_0402", "C100_0", "Disabled"]],
    )
    blank_path = write_status_file("status_blank.xlsx", [["CAP_0402", "", "Disabled"]])
    extra_path = write_status_file("status_extra.xlsx", [["CAP_0402", "C100_0", "Disabled", "extra"]])
    wrong_case_path = write_status_file("status_wrong_case.xlsx", [["CAP_0402", "C100_0", "disabled"]])
    header_only_path = write_status_file("status_header_only.xlsx", [])

    assert "Duplicate RefDes: C100_0" in window.validate_refdes_status_file(duplicate_path)
    assert "Row 2: Component, RefDes Name, and Activation Status are required." in window.validate_refdes_status_file(blank_path)
    assert "Row 2: expected exactly 3 columns." in window.validate_refdes_status_file(extra_path)
    assert "Unknown Activation Status: disabled" in window.validate_refdes_status_file(wrong_case_path)
    assert window.validate_refdes_status_file(header_only_path) == ["RefDes status file has no data rows."]
    assert window.refdes_activation_status_changes == {}


def test_main_window_refdes_status_import_rejects_component_mismatch_atomically(
    tmp_path: Path,
    monkeypatch,
) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    window.refdes_records = [
        RefDesRecord(component_name="CAP_0402", refdes_name="C100_0", activation_status="Automatic"),
        RefDesRecord(component_name="CAP_0402", refdes_name="C285_0", activation_status="Enabled"),
    ]
    window.refdes_activation_status_changes = {"C285_0": "Disabled"}
    status_path = tmp_path / "status_component_mismatch.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Component type", "REFDES", "Status"])
    sheet.append(["CAP_0402", "C100_0", "Disabled"])
    sheet.append(["WRONG_COMPONENT", "C285_0", "Automatic"])
    workbook.save(status_path)
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, message: warnings.append(message))

    window.import_refdes_status_file(status_path)

    assert window.refdes_activation_status_changes == {"C285_0": "Disabled"}
    assert warnings == ["Component mismatch for C285_0: expected CAP_0402, got WRONG_COMPONENT"]


def test_main_window_refdes_drop_routes_status_and_component_xlsx(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    window.blocks = [_make_block("CAP_0402"), _make_block("CAP_0603")]
    window.refdes_records = [
        RefDesRecord(component_name="CAP_0402", refdes_name="C100_0", activation_status="Automatic"),
        RefDesRecord(component_name="CAP_0402", refdes_name="C285_0", activation_status="Enabled"),
    ]
    window.rebuild_refdes_groups()

    status_path = tmp_path / "status_drop.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Component type", "REFDES", "Status"])
    sheet.append(["CAP_0402", "C100_0", "Disabled"])
    workbook.save(status_path)
    component_path = tmp_path / "component_drop.xlsx"
    workbook = Workbook()
    workbook.active.append(["C285_0", "CAP_0603"])
    workbook.save(component_path)

    window.import_refdes_drop_file(status_path)
    window.import_refdes_drop_file(component_path)

    assert window.refdes_activation_status_changes == {"C100_0": "Disabled"}
    assert window.refdes_component_changes == {"C285_0": "CAP_0603"}


def test_main_window_refdes_drop_rejects_malformed_status_header_once(tmp_path: Path, monkeypatch) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    window.refdes_records = [
        RefDesRecord(component_name="CAP_0402", refdes_name="C100_0", activation_status="Automatic")
    ]
    status_path = tmp_path / "status_bad_header.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Component type", "REFDES", "State"])
    sheet.append(["CAP_0402", "C100_0", "Disabled"])
    workbook.save(status_path)
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, message: warnings.append(message))

    window.import_refdes_drop_file(status_path)

    assert len(warnings) == 1
    assert warnings[0].startswith("Row 1: expected header:")
    assert window.refdes_activation_status_changes == {}
    assert window.refdes_component_changes == {}


def test_main_window_refdes_status_import_round_trips_unknown_only_for_unknown_records(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    window.blocks = [_make_block("CAP_0402")]
    window.refdes_records = [
        RefDesRecord(component_name="CAP_0402", refdes_name="C100_0", activation_status="Unknown"),
        RefDesRecord(component_name="CAP_0402", refdes_name="C285_0", activation_status="Enabled"),
    ]
    window.rebuild_refdes_groups()
    status_path = tmp_path / "status_unknown.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Component", "RefDes Name", "Activation Status"])
    sheet.append(["CAP_0402", "C100_0", "Unknown"])
    sheet.append(["CAP_0402", "C285_0", "Disabled"])
    workbook.save(status_path)
    invalid_path = tmp_path / "status_invalid_unknown.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Component", "RefDes Name", "Activation Status"])
    sheet.append(["CAP_0402", "C100_0", "Enabled"])
    sheet.append(["CAP_0402", "C285_0", "Unknown"])
    workbook.save(invalid_path)

    window.import_refdes_status_file(status_path)

    assert window.refdes_activation_status_changes == {"C285_0": "Disabled"}
    assert "Unknown Activation Status: Unknown" in window.validate_refdes_status_file(invalid_path)


def test_main_window_refdes_status_import_rejects_unreadable_xlsx(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    window.refdes_records = [RefDesRecord(component_name="CAP_0402", refdes_name="C100_0", activation_status="Automatic")]
    bad_path = tmp_path / "not_excel.xlsx"
    bad_path.write_text("not an excel workbook", encoding="utf-8")

    errors = window.validate_refdes_status_file(bad_path)

    assert len(errors) == 1
    assert "Could not read RefDes status Excel" in errors[0]


def test_main_window_changes_selected_refdes_activation_statuses(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    window.blocks = [_make_block("CAP_0402")]
    window.refdes_records = [
        RefDesRecord(component_name="CAP_0402", refdes_name="C100_0", activation_status="Automatic"),
        RefDesRecord(component_name="CAP_0402", refdes_name="C285_0", activation_status="Enabled"),
    ]
    window.rebuild_refdes_groups()
    window.populate_components()
    window.component_list.setCurrentRow(0)

    first = window.refdes_table.model().index(0, 0)
    second = window.refdes_table.model().index(1, 0)
    selection = window.refdes_table.selectionModel()
    selection.select(first, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    selection.select(second, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    window.apply_refdes_activation_status_changes(window.selected_refdes_names(), "Disabled")

    assert window.refdes_activation_status_changes == {"C100_0": "Disabled", "C285_0": "Disabled"}
    assert window.refdes_table.item(0, 1).text() == "Disabled"
    assert window.refdes_table.item(1, 1).text() == "Disabled"


def test_export_worker_preserves_refdes_record_fallback_scan_for_status_changes(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    spd_path = tmp_path / "board.spd"
    output_path = tmp_path / "board_out.spd"
    spd_path.write_text(
        ".Connect C100_0 CAP_0402 Checked = 1\n",
        encoding="utf-8",
        newline="\n",
    )
    finished: list[str] = []
    failed: list[str] = []
    worker = ExportWorker(
        spd_path,
        output_path,
        [],
        {},
        refdes_activation_status_changes={"C100_0": "Enabled"},
    )
    worker.finished.connect(finished.append)
    worker.failed.connect(failed.append)

    worker.run()

    assert failed == []
    assert finished == [str(output_path)]
    assert output_path.read_text(encoding="utf-8") == ".Connect C100_0 CAP_0402 Usage = 0b1000 Checked = 1\n"


def test_main_window_refdes_component_undo_stack_keeps_ten_batches(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    block_a = PartialCktBlock("A", ["1"], 1, 2, 0, 10, 11, 12, [".PartialCkt A ExtNode =  1"])
    block_b = PartialCktBlock("B", ["1"], 3, 4, 13, 20, 21, 22, [".PartialCkt B ExtNode =  1"])
    window.blocks = [block_a, block_b]
    window.refdes_records = [RefDesRecord(component_name="A", refdes_name=f"C{i}", activation_status="Automatic") for i in range(11)]
    window.rebuild_refdes_groups()

    for i in range(11):
        window.apply_refdes_component_changes([f"C{i}"], "B")

    assert len(window.refdes_component_undo_stack) == 10
    assert window.refdes_component_undo_stack[0].changes[0].refdes_name == "C1"


def test_main_window_imports_refdes_component_csv_and_xlsx_without_headers(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    window.blocks = [
        PartialCktBlock("CAP_0402", ["1"], 1, 2, 0, 10, 11, 12, [".PartialCkt CAP_0402 ExtNode = 1"]),
        PartialCktBlock("CAP_0603", ["1"], 3, 4, 13, 20, 21, 22, [".PartialCkt CAP_0603 ExtNode = 1"]),
    ]
    window.refdes_records = [
        RefDesRecord(component_name="CAP_0402", refdes_name="C100_0", activation_status="Automatic"),
        RefDesRecord(component_name="CAP_0402", refdes_name="C285_0", activation_status="Enabled"),
    ]
    window.rebuild_refdes_groups()
    csv_path = tmp_path / "changes.csv"
    csv_path.write_text("C100_0,CAP_0603\n", encoding="utf-8")
    xlsx_path = tmp_path / "changes.xlsx"
    workbook = Workbook()
    workbook.active.append(["C285_0", "CAP_0603"])
    workbook.save(xlsx_path)

    assert window.load_refdes_component_change_file(csv_path) == {"C100_0": "CAP_0603"}
    assert window.load_refdes_component_change_file(xlsx_path) == {"C285_0": "CAP_0603"}


def test_main_window_rejects_invalid_refdes_component_import_without_changes(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    window.blocks = [PartialCktBlock("CAP_0402", ["1"], 1, 2, 0, 10, 11, 12, [".PartialCkt CAP_0402 ExtNode = 1"])]
    window.refdes_records = [RefDesRecord(component_name="CAP_0402", refdes_name="C100_0", activation_status="Automatic")]
    window.rebuild_refdes_groups()
    duplicate_path = tmp_path / "duplicate.csv"
    duplicate_path.write_text("C100_0,CAP_0402\nC100_0,MISSING\n", encoding="utf-8")
    unknown_component_path = tmp_path / "unknown.csv"
    unknown_component_path.write_text("C100_0,MISSING\n", encoding="utf-8")

    assert window.validate_refdes_component_changes({"C100_0": "MISSING"}) == ["Unknown Component: MISSING"]
    assert "Duplicate RefDes: C100_0" in window.validate_refdes_component_change_file(duplicate_path)
    assert "Unknown Component: MISSING" in window.validate_refdes_component_change_file(unknown_component_path)
    assert window.refdes_component_changes == {}


def test_component_filter_hides_non_matching_rows_and_updates_header() -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    window.blocks = [_make_block("C1"), _make_block("C2"), _make_block("R1")]
    window.populate_components()

    assert window.component_list_label.text() == "PartialCkt Components (3/3)"

    window.component_filter.setText("c")

    visible_rows = [row for row in range(window.component_list.count()) if not window.component_list.item(row).isHidden()]
    hidden_rows = [row for row in range(window.component_list.count()) if window.component_list.item(row).isHidden()]
    assert len(visible_rows) == 2
    assert len(hidden_rows) == 1
    assert window.component_list_label.text() == "PartialCkt Components (2/3)"
    assert window.component_list.count() == 3

    window.component_filter.setText("")
    assert window.component_list_label.text() == "PartialCkt Components (3/3)"


def test_modified_item_is_visually_marked_after_import(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    block = _make_block("C1")
    window.spd_path = tmp_path / "board.spd"
    window.blocks = [block]
    window.populate_components()
    window.component_list.setCurrentRow(0)

    item_before = window.component_list.item(0)
    assert not item_before.font().bold()
    assert not item_before.text().startswith("* ")

    window.import_model_text(".SUBCKT CAP Port1 Port2\nC1 Port1 Port2 1u\n.ENDS CAP\n")

    item_after = window.component_list.item(0)
    assert item_after.font().bold()
    assert item_after.text().startswith("* ")
    assert "ports: 2" in item_after.text()


def test_load_block_body_missing_file_shows_error_and_leaves_editor_empty(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    block = _make_block("C1")
    window.spd_path = tmp_path / "missing.spd"
    window.blocks = [block]
    window.populate_components()

    window.component_list.setCurrentRow(0)

    assert window.editor.toPlainText() == ""
    assert "error" in window.validation_label.text().lower() or "could not read" in window.validation_label.text().lower()
    assert "could not read" in window.status_label.text().lower()


def test_busy_state_disables_load_export_validate_actions_and_buttons() -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()

    assert window.load_action.isEnabled()
    assert window.export_action.isEnabled()
    assert window.validate_action.isEnabled()
    assert window.import_button.isEnabled()
    assert window.validate_button.isEnabled()

    window._set_busy(True)

    assert not window.load_action.isEnabled()
    assert not window.export_action.isEnabled()
    assert not window.validate_action.isEnabled()
    assert not window.import_button.isEnabled()
    assert not window.validate_button.isEnabled()

    window._set_busy(False)

    assert window.load_action.isEnabled()
    assert window.export_action.isEnabled()
    assert window.validate_action.isEnabled()
    assert window.import_button.isEnabled()
    assert window.validate_button.isEnabled()


def test_main_window_load_spd_populates_components_from_worker(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    spd_path = tmp_path / "board.spd"
    spd_path.write_text(
        "Title\n"
        ".PartialCkt C1 ExtNode =  1 2\n"
        "C 1 2 1u\n"
        ".EndPartialCkt\n"
        ".Connect C100_0 C1 Checked = 1\n",
        encoding="utf-8",
        newline="\n",
    )
    window = MainWindow()
    loop = QEventLoop()
    original_finished = window._scan_finished

    def finished(inventory: SpdInventory) -> None:
        original_finished(inventory)
        loop.quit()

    window._scan_finished = finished
    window.load_spd(spd_path)
    QTimer.singleShot(3000, loop.quit)
    loop.exec()

    if window._scan_thread is not None and window._scan_thread.isRunning():
        window._scan_thread.quit()
        window._scan_thread.wait(1000)

    assert window.component_list.count() == 1
    assert window.blocks[0].component_name == "C1"
    assert window.refdes_table.rowCount() == 1
    assert window.refdes_table.item(0, 0).text() == "C100_0"
    assert window.refdes_table.item(0, 1).text() == "Automatic"
    assert "Loaded 1 PartialCkt blocks" in window.status_log.toPlainText()


def test_load_spd_real_threaded_scan_completes(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    spd = tmp_path / "board.spd"
    spd.write_text(
        ".PartialCkt C1 ExtNode =  1 2\nC 1 2 1u\n.EndPartialCkt\n"
        ".PartialCkt U1 ExtNode =  A B\nR A B 1\n.EndPartialCkt\n",
        newline="\n",
    )

    window.load_spd(spd)
    assert window._busy, "busy state should be set while the scan runs"
    assert window._scan_worker is not None, "worker must be strongly referenced during the scan"

    _spin_until(app, lambda: not window._busy, timeout=15.0, what="threaded scan to finish")

    assert window.spd_path == spd
    assert len(window.blocks) == 2
    assert window.component_list.count() == 2
    assert "2/2" in window.component_list_label.text()
    assert window.load_action.isEnabled()

    _spin_until(
        app,
        lambda: window._scan_thread is None and window._scan_worker is None,
        timeout=15.0,
        what="scan thread/worker refs to be cleared",
    )


def test_editor_uses_fixed_pitch_font_and_no_wrap() -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()

    assert window.editor.font().fixedPitch()
    assert window.editor.lineWrapMode() == QPlainTextEdit.LineWrapMode.NoWrap
