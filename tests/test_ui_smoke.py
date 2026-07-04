import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFontInfo
from PySide6.QtWidgets import QApplication, QPlainTextEdit

from spd_model_injector.core.spd import PartialCktBlock
from spd_model_injector.ui.main_window import MainWindow


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
    assert window.windowTitle() == "SPD Model Injector"
    assert window.component_list.count() == 0
    assert "Load an SPD file" in window.status_label.text()


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
    window.component_list.setCurrentRow(0)

    window.import_model_text(".SUBCKT CAP Port1 Port2\nC1 Port1 Port2 1u\n.ENDS CAP\n")

    assert window.editor.toPlainText() == "C1 1 2 1u\n"
    assert window.replacements == {"C1": "C1 1 2 1u\n"}
    assert "OK" in window.validation_label.text()


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

    # Row -> block index mapping must stay intact: hidden rows keep their
    # original index rather than being removed from the widget.
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
    assert "●" not in item_before.text()

    window.import_model_text(".SUBCKT CAP Port1 Port2\nC1 Port1 Port2 1u\n.ENDS CAP\n")

    item_after = window.component_list.item(0)
    assert item_after.font().bold()
    assert "●" in item_after.text()
    assert "ports: 2" in item_after.text()


def test_load_block_body_missing_file_shows_error_and_leaves_editor_empty(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    block = _make_block("C1")
    window.spd_path = tmp_path / "missing.spd"
    window.blocks = [block]
    window.populate_components()

    # Selecting the row triggers _on_current_row_changed -> _load_block_body,
    # which must not propagate the OSError raised by read_block_body.
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


def test_load_spd_real_threaded_scan_completes(tmp_path: Path) -> None:
    """Regression: the scan worker must be kept alive (strong ref on self).

    Without ``self._scan_worker`` the worker QObject can be garbage-collected
    after load_spd() returns, so ``started -> run`` never fires and the scan
    hangs forever. This drives a real QThread scan end to end.
    """
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

    # The cleanup slot should release the thread/worker refs once the
    # thread winds down.
    _spin_until(
        app,
        lambda: window._scan_thread is None and window._scan_worker is None,
        timeout=15.0,
        what="scan thread/worker refs to be cleared",
    )


def test_editor_uses_fixed_pitch_font_and_no_wrap() -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()

    # QFont.fixedPitch() only reflects an explicitly-set flag; QFontInfo
    # resolves the actual matched font, which is what rendering uses.
    assert QFontInfo(window.editor.font()).fixedPitch()
    assert window.editor.lineWrapMode() == QPlainTextEdit.LineWrapMode.NoWrap
