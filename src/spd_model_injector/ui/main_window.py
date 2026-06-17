from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from spd_model_injector.core.spd import PartialCktBlock, read_block_body
from spd_model_injector.core.spice import ModelValidationError, prepare_model_for_partialckt
from spd_model_injector.ui.workers import ExportWorker, ScanWorker


class DropModelEditor(QPlainTextEdit):
    fileDropped = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setPlaceholderText("Paste SPICE model text or drop a .mod/.txt file here.")

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        mime = event.mimeData()
        if mime.hasUrls():
            for url in mime.urls():
                path = Path(url.toLocalFile())
                if path.is_file():
                    self.fileDropped.emit(path.read_text(encoding="utf-8"))
                    event.acceptProposedAction()
                    return
        if mime.hasText():
            self.fileDropped.emit(mime.text())
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SPD Model Injector")
        self.resize(1200, 760)

        self.spd_path: Path | None = None
        self.blocks: list[PartialCktBlock] = []
        self.replacements: dict[str, str] = {}
        self._loading_editor = False
        self._scan_thread: QThread | None = None
        self._export_thread: QThread | None = None

        self.component_list = QListWidget()
        self.component_list.currentRowChanged.connect(self._on_current_row_changed)

        self.component_label = QLabel("No component selected")
        self.mapping_label = QLabel("Load an SPD file to inspect PartialCkt blocks.")
        self.mapping_label.setWordWrap(True)

        self.editor = DropModelEditor()
        self.editor.fileDropped.connect(self.import_model_text)
        self.editor.textChanged.connect(self._on_editor_changed)

        self.validation_label = QLabel("")
        self.validation_label.setWordWrap(True)
        self.status_label = QLabel("Load an SPD file to begin.")
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setRange(0, 0)

        self._build_layout()
        self._build_toolbar()
        self.setStatusBar(QStatusBar())
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.progress)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        load_action = QAction("Load SPD", self)
        load_action.triggered.connect(self.load_spd_dialog)
        toolbar.addAction(load_action)

        validate_action = QAction("Validate/Convert", self)
        validate_action.triggered.connect(self.validate_current_text)
        toolbar.addAction(validate_action)

        revert_action = QAction("Revert", self)
        revert_action.triggered.connect(self.revert_current)
        toolbar.addAction(revert_action)

        export_action = QAction("Export New SPD", self)
        export_action.triggered.connect(self.export_spd_dialog)
        toolbar.addAction(export_action)

    def _build_layout(self) -> None:
        root = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("PartialCkt Components"))
        left_layout.addWidget(self.component_list)
        root.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(self.component_label)
        right_layout.addWidget(self.mapping_label)

        button_row = QHBoxLayout()
        import_button = QPushButton("Import .mod/.txt")
        import_button.clicked.connect(self.import_model_dialog)
        validate_button = QPushButton("Validate/Convert")
        validate_button.clicked.connect(self.validate_current_text)
        revert_button = QPushButton("Revert")
        revert_button.clicked.connect(self.revert_current)
        button_row.addWidget(import_button)
        button_row.addWidget(validate_button)
        button_row.addWidget(revert_button)
        button_row.addStretch(1)
        right_layout.addLayout(button_row)

        right_layout.addWidget(self.editor, 1)
        right_layout.addWidget(self.validation_label)
        root.addWidget(right)
        root.setSizes([360, 840])
        self.setCentralWidget(root)

    def load_spd_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load SPD", "", "SPD files (*.spd);;All files (*)")
        if path:
            self.load_spd(path)

    def load_spd(self, path: str | Path) -> None:
        self.spd_path = Path(path)
        self.status_label.setText(f"Scanning {self.spd_path.name}...")
        self.progress.setVisible(True)
        self._scan_thread = QThread(self)
        worker = ScanWorker(self.spd_path)
        worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(worker.run)
        worker.finished.connect(self._scan_finished)
        worker.failed.connect(self._worker_failed)
        worker.finished.connect(self._scan_thread.quit)
        worker.failed.connect(self._scan_thread.quit)
        self._scan_thread.finished.connect(worker.deleteLater)
        self._scan_thread.start()

    def _scan_finished(self, blocks: list[PartialCktBlock]) -> None:
        self.blocks = blocks
        self.replacements.clear()
        self.populate_components()
        self.progress.setVisible(False)
        self.status_label.setText(f"Loaded {len(blocks)} PartialCkt blocks.")

    def populate_components(self) -> None:
        self.component_list.clear()
        for block in self.blocks:
            item = QListWidgetItem(self._item_text(block))
            item.setData(Qt.ItemDataRole.UserRole, block.component_name)
            self.component_list.addItem(item)

    def import_model_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import SPICE Model", "", "Model files (*.mod *.txt);;All files (*)")
        if path:
            self.import_model_text(Path(path).read_text(encoding="utf-8"))

    def import_model_text(self, raw_model: str) -> None:
        block = self.current_block()
        if block is None:
            self._show_validation("Select a PartialCkt before importing a model.", error=True)
            return
        try:
            prepared = prepare_model_for_partialckt(raw_model, block.ext_nodes)
        except ModelValidationError as exc:
            self._show_validation(str(exc), error=True)
            return
        self._set_editor_text(prepared)
        self.replacements[block.component_name] = prepared
        self._show_mapping(block, raw_model)
        self._show_validation(f"OK: {block.port_count} model ports matched {block.port_count} ExtNode ports.", error=False)
        self._refresh_current_item()

    def validate_current_text(self) -> None:
        text = self.editor.toPlainText()
        if ".SUBCKT" in text.upper():
            self.import_model_text(text)
            return
        block = self.current_block()
        if block is None:
            self._show_validation("No component selected.", error=True)
            return
        self.replacements[block.component_name] = _ensure_lf_ending(text)
        self._show_validation("OK: manual PartialCkt body will be used as edited.", error=False)
        self._refresh_current_item()

    def revert_current(self) -> None:
        block = self.current_block()
        if block is None:
            return
        self.replacements.pop(block.component_name, None)
        self._load_block_body(block)
        self._show_validation("Reverted to the body currently stored in the SPD.", error=False)
        self._refresh_current_item()

    def export_spd_dialog(self) -> None:
        if self.spd_path is None:
            QMessageBox.warning(self, "Export", "Load an SPD file first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export New SPD", "", "SPD files (*.spd);;All files (*)")
        if path:
            self.export_spd(path)

    def export_spd(self, output_path: str | Path) -> None:
        if self.spd_path is None:
            return
        self.status_label.setText("Writing SPD output...")
        self.progress.setVisible(True)
        self._export_thread = QThread(self)
        worker = ExportWorker(self.spd_path, output_path, self.blocks, dict(self.replacements))
        worker.moveToThread(self._export_thread)
        self._export_thread.started.connect(worker.run)
        worker.finished.connect(self._export_finished)
        worker.failed.connect(self._worker_failed)
        worker.finished.connect(self._export_thread.quit)
        worker.failed.connect(self._export_thread.quit)
        self._export_thread.finished.connect(worker.deleteLater)
        self._export_thread.start()

    def _export_finished(self, output_path: str) -> None:
        self.progress.setVisible(False)
        self.status_label.setText(f"Exported {output_path}")

    def _worker_failed(self, message: str) -> None:
        self.progress.setVisible(False)
        self.status_label.setText("Operation failed.")
        QMessageBox.critical(self, "SPD Model Injector", message)

    def _on_current_row_changed(self, row: int) -> None:
        if row < 0 or row >= len(self.blocks):
            return
        block = self.blocks[row]
        self.component_label.setText(block.component_name)
        self._show_mapping(block)
        if block.component_name in self.replacements:
            self._set_editor_text(self.replacements[block.component_name])
        else:
            self._load_block_body(block)

    def _on_editor_changed(self) -> None:
        if self._loading_editor:
            return
        block = self.current_block()
        if block is None:
            return
        self.replacements[block.component_name] = _ensure_lf_ending(self.editor.toPlainText())
        self._show_validation("Edited manually. Use Validate/Convert before export if this is a full .SUBCKT model.", error=False)
        self._refresh_current_item()

    def _load_block_body(self, block: PartialCktBlock) -> None:
        if self.spd_path is None:
            self._set_editor_text("")
            return
        self._set_editor_text(read_block_body(self.spd_path, block))

    def _set_editor_text(self, text: str) -> None:
        self._loading_editor = True
        self.editor.setPlainText(text)
        self._loading_editor = False

    def current_block(self) -> PartialCktBlock | None:
        row = self.component_list.currentRow()
        if row < 0 or row >= len(self.blocks):
            return None
        return self.blocks[row]

    def _show_mapping(self, block: PartialCktBlock, raw_model: str | None = None) -> None:
        model_info = ""
        if raw_model and ".SUBCKT" in raw_model.upper():
            first_line = next((line for line in raw_model.splitlines() if line.strip().upper().startswith(".SUBCKT")), "")
            model_info = f" | Model: {first_line.strip()}"
        ext_preview = " ".join(block.ext_nodes[:20])
        if len(block.ext_nodes) > 20:
            ext_preview += f" ... (+{len(block.ext_nodes) - 20} more)"
        self.mapping_label.setText(f"ExtNode ({block.port_count}): {ext_preview}{model_info}")

    def _show_validation(self, message: str, *, error: bool) -> None:
        color = "#b91c1c" if error else "#065f46"
        self.validation_label.setText(f"<span style='color:{color}'>{message}</span>")

    def _refresh_current_item(self) -> None:
        block = self.current_block()
        row = self.component_list.currentRow()
        if block is None or row < 0:
            return
        self.component_list.item(row).setText(self._item_text(block))

    def _item_text(self, block: PartialCktBlock) -> str:
        state = "modified" if block.component_name in self.replacements else "existing"
        return f"{block.component_name}\nports: {block.port_count}  {state}"


def _ensure_lf_ending(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized if not normalized or normalized.endswith("\n") else normalized + "\n"
