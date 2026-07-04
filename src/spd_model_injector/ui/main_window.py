from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QFontDatabase, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
    fileDropError = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setPlaceholderText("Paste SPICE model text or drop a .mod/.txt file here.")
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        mime = event.mimeData()
        if mime.hasUrls():
            if any(url.isLocalFile() and Path(url.toLocalFile()).is_file() for url in mime.urls()):
                event.acceptProposedAction()
                return
            event.ignore()
            return
        if mime.hasText():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        mime = event.mimeData()
        if mime.hasUrls():
            for url in mime.urls():
                if not url.isLocalFile():
                    continue
                path = Path(url.toLocalFile())
                if not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    self.fileDropError.emit(f"Could not read {path.name}: {exc}")
                    event.acceptProposedAction()
                    return
                self.fileDropped.emit(text)
                event.acceptProposedAction()
                return
            event.ignore()
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
        self.setMinimumSize(900, 600)

        self.spd_path: Path | None = None
        self._pending_spd_path: Path | None = None
        self.blocks: list[PartialCktBlock] = []
        self.replacements: dict[str, str] = {}
        self._loading_editor = False
        self._busy = False
        self._scan_thread: QThread | None = None
        self._scan_worker: ScanWorker | None = None
        self._export_thread: QThread | None = None
        self._export_worker: ExportWorker | None = None

        self.component_list_label = QLabel("PartialCkt Components (0/0)")
        self.component_filter = QLineEdit()
        self.component_filter.setPlaceholderText("Filter components…")
        self.component_filter.setToolTip("Filter components by name (Ctrl+F)")
        self.component_filter.textChanged.connect(self._apply_component_filter)

        self.component_list = QListWidget()
        self.component_list.currentRowChanged.connect(self._on_current_row_changed)

        self.component_label = QLabel("No component selected")
        self.mapping_label = QLabel("Load an SPD file to inspect PartialCkt blocks.")
        self.mapping_label.setWordWrap(True)

        self.editor = DropModelEditor()
        self.editor.fileDropped.connect(self.import_model_text)
        self.editor.fileDropError.connect(lambda message: self._show_validation(message, error=True))
        self.editor.textChanged.connect(self._on_editor_changed)

        self.validation_label = QLabel("")
        self.validation_label.setWordWrap(True)
        self.status_label = QLabel("Load an SPD file to begin.")
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setRange(0, 0)

        self._build_layout()
        self._build_toolbar()
        self._build_shortcuts()
        self.setStatusBar(QStatusBar())
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.progress)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.load_action = QAction("Load SPD", self)
        self.load_action.setShortcut(QKeySequence("Ctrl+O"))
        self.load_action.setToolTip("Load an SPD file and scan for PartialCkt blocks (Ctrl+O)")
        self.load_action.triggered.connect(self.load_spd_dialog)
        toolbar.addAction(self.load_action)

        self.validate_action = QAction("Validate/Convert", self)
        self.validate_action.setShortcut(QKeySequence("Ctrl+Return"))
        self.validate_action.setToolTip("Validate or convert the current editor text (Ctrl+Return)")
        self.validate_action.triggered.connect(self.validate_current_text)
        toolbar.addAction(self.validate_action)

        self.revert_action = QAction("Revert", self)
        self.revert_action.setToolTip("Revert the selected component to the body stored in the SPD")
        self.revert_action.triggered.connect(self.revert_current)
        toolbar.addAction(self.revert_action)

        self.export_action = QAction("Export New SPD", self)
        self.export_action.setShortcut(QKeySequence("Ctrl+E"))
        self.export_action.setToolTip("Export a new SPD with modified bodies replaced (Ctrl+E)")
        self.export_action.triggered.connect(self.export_spd_dialog)
        toolbar.addAction(self.export_action)

    def _build_layout(self) -> None:
        root = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.component_list_label)
        left_layout.addWidget(self.component_filter)
        left_layout.addWidget(self.component_list)
        root.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(self.component_label)
        right_layout.addWidget(self.mapping_label)

        button_row = QHBoxLayout()
        self.import_button = QPushButton("Import .mod/.txt")
        self.import_button.setToolTip("Import a SPICE model file into the editor (Ctrl+I)")
        self.import_button.clicked.connect(self.import_model_dialog)
        self.validate_button = QPushButton("Validate/Convert")
        self.validate_button.setToolTip("Validate or convert the current editor text (Ctrl+Return)")
        self.validate_button.clicked.connect(self.validate_current_text)
        self.revert_button = QPushButton("Revert")
        self.revert_button.setToolTip("Revert the selected component to the body stored in the SPD")
        self.revert_button.clicked.connect(self.revert_current)
        button_row.addWidget(self.import_button)
        button_row.addWidget(self.validate_button)
        button_row.addWidget(self.revert_button)
        button_row.addStretch(1)
        right_layout.addLayout(button_row)

        right_layout.addWidget(self.editor, 1)
        right_layout.addWidget(self.validation_label)
        root.addWidget(right)
        root.setSizes([360, 840])
        self.setCentralWidget(root)

    def _build_shortcuts(self) -> None:
        # Keep references on self: an unreferenced QShortcut can be
        # garbage-collected even though Qt owns it via the parent widget.
        self._import_shortcut = QShortcut(QKeySequence("Ctrl+I"), self)
        self._import_shortcut.activated.connect(self.import_model_dialog)

        self._filter_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self._filter_shortcut.activated.connect(self._focus_component_filter)

    def _focus_component_filter(self) -> None:
        self.component_filter.setFocus()
        self.component_filter.selectAll()

    # ------------------------------------------------------------------
    # Busy state / thread lifecycle
    # ------------------------------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for action in (self.load_action, self.validate_action, self.export_action):
            action.setEnabled(not busy)
        for button in (self.import_button, self.validate_button):
            button.setEnabled(not busy)

    def _clear_scan_refs(self) -> None:
        # Guard on sender(): if a new scan started before this queued slot
        # ran, the refs already point at the new thread/worker.
        if self.sender() is self._scan_thread:
            self._scan_thread = None
            self._scan_worker = None

    def _clear_export_refs(self) -> None:
        if self.sender() is self._export_thread:
            self._export_thread = None
            self._export_worker = None

    def closeEvent(self, event) -> None:  # noqa: N802
        for thread in (self._scan_thread, self._export_thread):
            if thread is not None and thread.isRunning():
                thread.quit()
                thread.wait(5000)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Loading / scanning
    # ------------------------------------------------------------------

    def load_spd_dialog(self) -> None:
        directory = str(self.spd_path.parent) if self.spd_path else ""
        path, _ = QFileDialog.getOpenFileName(self, "Load SPD", directory, "SPD files (*.spd);;All files (*)")
        if path:
            self.load_spd(path)

    def load_spd(self, path: str | Path) -> None:
        pending_path = Path(path)
        self._pending_spd_path = pending_path

        # Reset to an empty, consistent state before the new scan lands so a
        # selection made mid-scan (or after a failed scan) can never read
        # stale byte offsets against the wrong file.
        self.blocks = []
        self.replacements.clear()
        self.component_list.clear()
        if self.component_filter.text():
            self.component_filter.blockSignals(True)
            self.component_filter.clear()
            self.component_filter.blockSignals(False)
        self._update_component_header()
        self.component_label.setText("No component selected")
        self.mapping_label.setText("Scanning for PartialCkt blocks...")
        self._set_editor_text("")
        self._show_validation("", error=False)

        self.status_label.setText(f"Scanning {pending_path.name}...")
        self.progress.setVisible(True)
        self._set_busy(True)
        self._scan_thread = QThread(self)
        # The worker must be kept on self: without a strong Python reference
        # PySide can garbage-collect it before (or while) the thread runs it.
        self._scan_worker = ScanWorker(pending_path)
        self._scan_worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.finished.connect(self._scan_finished)
        self._scan_worker.failed.connect(self._scan_failed)
        self._scan_worker.finished.connect(self._scan_thread.quit)
        self._scan_worker.failed.connect(self._scan_thread.quit)
        self._scan_thread.finished.connect(self._scan_worker.deleteLater)
        self._scan_thread.finished.connect(self._clear_scan_refs)
        self._scan_thread.start()

    def _scan_finished(self, blocks: list[PartialCktBlock]) -> None:
        self.spd_path = self._pending_spd_path
        self._pending_spd_path = None
        self.blocks = blocks
        self.replacements.clear()
        self.populate_components()
        self.progress.setVisible(False)
        self._set_busy(False)
        name = self.spd_path.name if self.spd_path else ""
        self.status_label.setText(f"Loaded {len(blocks)} PartialCkt blocks from {name}.")

    def _scan_failed(self, message: str) -> None:
        # The scan never landed, so make sure nothing references the
        # rejected path: leave spd_path as None rather than pointing at a
        # file whose blocks/offsets we no longer track.
        self._pending_spd_path = None
        self.spd_path = None
        self.blocks = []
        self.replacements.clear()
        self.component_list.clear()
        self._update_component_header()
        self.component_label.setText("No component selected")
        self.mapping_label.setText("Load an SPD file to inspect PartialCkt blocks.")
        self._set_editor_text("")
        self._show_worker_error(message)

    def populate_components(self) -> None:
        self.component_list.clear()
        for block in self.blocks:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, block.component_name)
            self.component_list.addItem(item)
            self._style_item(item, block)
        if self.component_filter.text():
            self.component_filter.blockSignals(True)
            self.component_filter.clear()
            self.component_filter.blockSignals(False)
        self._update_component_header()

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _apply_component_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self.component_list.count()):
            item = self.component_list.item(row)
            name = str(item.data(Qt.ItemDataRole.UserRole) or "")
            item.setHidden(bool(needle) and needle not in name.lower())
        self._update_component_header()

    def _update_component_header(self) -> None:
        total = self.component_list.count()
        visible = sum(1 for row in range(total) if not self.component_list.item(row).isHidden())
        self.component_list_label.setText(f"PartialCkt Components ({visible}/{total})")

    # ------------------------------------------------------------------
    # Import / validate / revert
    # ------------------------------------------------------------------

    def import_model_dialog(self) -> None:
        directory = str(self.spd_path.parent) if self.spd_path else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import SPICE Model", directory, "Model files (*.mod *.txt);;All files (*)"
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self._show_validation(f"Could not read {Path(path).name}: {exc}", error=True)
            return
        self.import_model_text(text)

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

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_spd_dialog(self) -> None:
        if self.spd_path is None:
            QMessageBox.warning(self, "Export", "Load an SPD file first.")
            return
        default_path = str(self.spd_path.parent / f"{self.spd_path.stem}_injected.spd")
        path, _ = QFileDialog.getSaveFileName(self, "Export New SPD", default_path, "SPD files (*.spd);;All files (*)")
        if not path:
            return

        output_path = Path(path)
        try:
            same_as_source = output_path.resolve() == self.spd_path.resolve()
        except OSError:
            same_as_source = False
        if same_as_source or output_path == self.spd_path:
            QMessageBox.warning(
                self,
                "Export",
                "Output path must differ from the source SPD file. Choose a different filename.",
            )
            return

        if not self.replacements:
            confirm = QMessageBox.question(
                self,
                "Export",
                "No components are modified; the output will be an LF-normalized copy of the source. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

        self.export_spd(output_path)

    def export_spd(self, output_path: str | Path) -> None:
        if self.spd_path is None:
            return
        output_path = Path(output_path)
        self.status_label.setText(f"Writing {output_path.name}...")
        self.progress.setVisible(True)
        self._set_busy(True)
        self._export_thread = QThread(self)
        # Keep a strong reference so the worker cannot be garbage-collected
        # while the thread is running it (see load_spd).
        self._export_worker = ExportWorker(self.spd_path, output_path, self.blocks, dict(self.replacements))
        self._export_worker.moveToThread(self._export_thread)
        self._export_thread.started.connect(self._export_worker.run)
        self._export_worker.finished.connect(self._export_finished)
        self._export_worker.failed.connect(self._show_worker_error)
        self._export_worker.finished.connect(self._export_thread.quit)
        self._export_worker.failed.connect(self._export_thread.quit)
        self._export_thread.finished.connect(self._export_worker.deleteLater)
        self._export_thread.finished.connect(self._clear_export_refs)
        self._export_thread.start()

    def _export_finished(self, output_path: str) -> None:
        self.progress.setVisible(False)
        self._set_busy(False)
        self.status_label.setText(f"Exported to {output_path}")

    def _show_worker_error(self, message: str) -> None:
        self.progress.setVisible(False)
        self._set_busy(False)
        self.status_label.setText("Operation failed.")
        # open() instead of the static critical(): this slot runs from worker
        # signals, and a nested modal exec() here would freeze the caller's
        # event processing. The dialog is still window-modal for the user.
        box = QMessageBox(
            QMessageBox.Icon.Critical,
            "SPD Model Injector",
            message,
            QMessageBox.StandardButton.Ok,
            self,
        )
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.open()

    # ------------------------------------------------------------------
    # Selection / editor plumbing
    # ------------------------------------------------------------------

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
        try:
            body = read_block_body(self.spd_path, block)
        except OSError as exc:
            self._set_editor_text("")
            message = f"Could not read {self.spd_path.name}: {exc}"
            self._show_validation(message, error=True)
            self.status_label.setText(message)
            return
        self._set_editor_text(body)

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

    # ------------------------------------------------------------------
    # Styling helpers
    # ------------------------------------------------------------------

    def _is_dark_palette(self) -> bool:
        return self.palette().color(QPalette.ColorRole.Window).lightness() < 128

    def _modified_color(self) -> QColor:
        return QColor("#fbbf24") if self._is_dark_palette() else QColor("#b45309")

    def _show_validation(self, message: str, *, error: bool) -> None:
        if not message:
            self.validation_label.setText("")
            return
        dark = self._is_dark_palette()
        if error:
            color = "#f87171" if dark else "#b91c1c"
            style = f"color:{color};font-weight:bold;"
        else:
            color = "#34d399" if dark else "#065f46"
            style = f"color:{color};"
        escaped = html.escape(message)
        self.validation_label.setText(f"<span style='{style}'>{escaped}</span>")

    def _refresh_current_item(self) -> None:
        block = self.current_block()
        row = self.component_list.currentRow()
        if block is None or row < 0:
            return
        self._style_item(self.component_list.item(row), block)

    def _style_item(self, item: QListWidgetItem, block: PartialCktBlock) -> None:
        item.setText(self._item_text(block))
        modified = block.component_name in self.replacements
        font = item.font()
        font.setBold(modified)
        item.setFont(font)
        if modified:
            item.setForeground(self._modified_color())
        else:
            item.setData(Qt.ItemDataRole.ForegroundRole, None)

    def _item_text(self, block: PartialCktBlock) -> str:
        modified = block.component_name in self.replacements
        marker = "● " if modified else ""
        state = "modified" if modified else "existing"
        return f"{marker}{block.component_name}\nports: {block.port_count}  {state}"


def _ensure_lf_ending(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized if not normalized or normalized.endswith("\n") else normalized + "\n"
