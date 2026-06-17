import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from spd_model_injector.core.spd import PartialCktBlock
from spd_model_injector.ui.main_window import MainWindow


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
