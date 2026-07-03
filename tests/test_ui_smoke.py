import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from openpyxl import Workbook, load_workbook
from PySide6.QtCore import QEventLoop, QItemSelectionModel, QTimer
from PySide6.QtWidgets import QApplication, QAbstractItemView, QFrame, QHeaderView, QSplitter

from spd_model_injector.core.spd import PartialCktBlock, RefDesRecord, SpdInventory
from spd_model_injector.ui.main_window import MainWindow


def test_main_window_has_expected_title_and_empty_initial_state() -> None:
    app = QApplication.instance() or QApplication([])

    window = MainWindow()

    assert app is not None
    assert window.windowTitle() == "SPD Model Injector 0.1.6"
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
    block = PartialCktBlock(
        component_name="C1",
        ext_nodes=["1", "2"],
        start_line=1,
        end_line=3,
        block_start_offset=0,
        body_start_offset=30,
        body_end_offset=40,
        block_end_offset=55,
        header_lines=[".PartialCkt C1 ExtNode =  1 2"],
    )
    window.spd_path = tmp_path / "board.spd"
    window.blocks = [block]
    window.populate_components()
    assert window.component_list.item(0).text() == 'C1'
    window.component_list.setCurrentRow(0)

    window.import_model_text(".SUBCKT CAP Port1 Port2\nC1 Port1 Port2 1u\n.ENDS CAP\n")

    assert window.editor.toPlainText() == "C1 1 2 1u\n"
    assert window.replacements == {"C1": "C1 1 2 1u\n"}
    assert "OK" in window.validation_label.text()


def test_main_window_refdes_table_follows_selected_component(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    cap_block = PartialCktBlock(
        component_name="CAP_0402",
        ext_nodes=["1", "2"],
        start_line=1,
        end_line=3,
        block_start_offset=0,
        body_start_offset=30,
        body_end_offset=40,
        block_end_offset=55,
        header_lines=[".PartialCkt CAP_0402 ExtNode =  1 2"],
    )
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
    cap_block = PartialCktBlock(
        component_name="CAP_0402",
        ext_nodes=["1", "2"],
        start_line=1,
        end_line=3,
        block_start_offset=0,
        body_start_offset=30,
        body_end_offset=40,
        block_end_offset=55,
        header_lines=[".PartialCkt CAP_0402 ExtNode =  1 2"],
    )
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
        ("Component", "RefDes Name", "Activation Status"),
        ("CAP_0603", "C100_0", "Automatic"),
    ]


def test_main_window_refdes_component_undo_stack_keeps_ten_batches(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    block_a = PartialCktBlock(
        component_name="A",
        ext_nodes=["1"],
        start_line=1,
        end_line=2,
        block_start_offset=0,
        body_start_offset=10,
        body_end_offset=11,
        block_end_offset=12,
        header_lines=[".PartialCkt A ExtNode =  1"],
    )
    block_b = PartialCktBlock(
        component_name="B",
        ext_nodes=["1"],
        start_line=3,
        end_line=4,
        block_start_offset=13,
        body_start_offset=20,
        body_end_offset=21,
        block_end_offset=22,
        header_lines=[".PartialCkt B ExtNode =  1"],
    )
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
