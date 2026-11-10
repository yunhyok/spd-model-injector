from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from spd_model_injector.core.settings_transfer import CopySettingsOptions, copy_spd_settings
from spd_model_injector.ui.theme import section_title


class SettingsCopyThread(QThread):
    progress = Signal(str, object, object)

    def __init__(
        self,
        source_path: Path,
        target_path: Path,
        output_path: Path,
        options: CopySettingsOptions,
        target_signature: tuple[int, int, int, int] | None,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.source_path = source_path
        self.target_path = target_path
        self.output_path = output_path
        self.options = options
        self.target_signature = target_signature
        self.summary: list[str] = []
        self.error: str | None = None

    def run(self) -> None:
        try:
            self.summary = copy_spd_settings(
                self.source_path,
                self.target_path,
                self.output_path,
                self.options,
                target_signature=self.target_signature,
                progress_callback=self.progress.emit,
            )
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__


class SettingsCopyDialog(QDialog):
    def __init__(
        self,
        target_path: Path,
        target_signature: tuple[int, int, int, int] | None,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Copy Settings from SPD")
        self.target_path = target_path
        self.target_signature = target_signature
        self.output_path: Path | None = None
        self._worker: SettingsCopyThread | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        self.files_panel = QFrame()
        self.files_panel.setProperty("panel", True)
        files = QVBoxLayout(self.files_panel)
        files.addWidget(section_title("Target SPD (currently loaded)"))
        target_edit = QLineEdit(str(target_path))
        target_edit.setReadOnly(True)
        target_edit.setAccessibleName("Target SPD")
        target_edit.setToolTip(str(target_path))
        files.addWidget(target_edit)
        files.addWidget(section_title("Source SPD (settings to copy)"))
        self.source_edit = QLineEdit()
        self.source_edit.setReadOnly(True)
        self.source_edit.setAccessibleName("Source SPD")
        self.source_edit.setPlaceholderText("Choose an SPD file...")
        self.source_edit.textChanged.connect(self._update_copy_button)
        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self._browse_source)
        source_row = QHBoxLayout()
        source_row.addWidget(self.source_edit, 1)
        source_row.addWidget(self.browse_button)
        files.addLayout(source_row)
        layout.addWidget(self.files_panel)

        self.options_panel = QFrame()
        self.options_panel.setProperty("panel", True)
        categories = QVBoxLayout(self.options_panel)
        categories.addWidget(section_title("Settings to Copy"))
        self.category_checks: dict[str, QCheckBox] = {}
        for name, label, tooltip in (
            ("stackup", "Layer Stackup", "Copy layer properties such as thickness and materials."),
            ("channel_activation", "Channel Activation", "Copy activation for all named NETs."),
            ("component_models", "Component Models", "Copy model definitions, RefDes assignments, and model activation."),
            ("ports", "Ports", "Replace all target ports and match terminals by RefDes, pin, and NET."),
        ):
            check = QCheckBox(label)
            check.setChecked(True)
            check.setToolTip(tooltip)
            check.toggled.connect(self._update_copy_button)
            categories.addWidget(check)
            self.category_checks[name] = check
        help_text = QLabel(
            "Creates a copy of the target with the selected settings from the source. Every copy requires "
            "matching layer structure, NET names, and NET counts."
        )
        help_text.setWordWrap(True)
        help_text.setProperty("muted", True)
        categories.addWidget(help_text)
        layout.addWidget(self.options_panel)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.hide()
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setAccessibleName("Settings copy progress")
        self.progress.hide()
        layout.addWidget(self.progress)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.copy_button = buttons.addButton("Validate && Copy...", QDialogButtonBox.ButtonRole.ActionRole)
        self.copy_button.setProperty("primary", True)
        self.copy_button.setDefault(True)
        self.copy_button.clicked.connect(self._start_copy)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_copy_button()
        self.browse_button.setFocus()
        preferred_width = max(self.sizeHint().width(), 60 * self.fontMetrics().horizontalAdvance("0"))
        self.resize(preferred_width, layout.heightForWidth(preferred_width))

    def _update_copy_button(self) -> None:
        self.copy_button.setEnabled(
            self._worker is None and bool(self.source_edit.text())
            and any(check.isChecked() for check in self.category_checks.values())
        )

    def _browse_source(self) -> None:
        directory = Path(self.source_edit.text()).parent if self.source_edit.text() else self.target_path.parent
        path, _ = QFileDialog.getOpenFileName(self, "Choose Source SPD", str(directory), "SPD files (*.spd);;All files (*)")
        if path:
            source = str(Path(path))
            self.source_edit.setText(source)
            self.source_edit.setToolTip(source)

    def _start_copy(self) -> None:
        if not self.copy_button.isEnabled():
            return
        source_path = Path(self.source_edit.text())
        options = CopySettingsOptions(**{name: check.isChecked() for name, check in self.category_checks.items()})
        default_path = self.target_path.with_name(f"{self.target_path.stem}_settings.spd")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Copied SPD", str(default_path), "SPD files (*.spd)"
        )
        if not path:
            return
        output_path = Path(path)

        self._worker = SettingsCopyThread(source_path, self.target_path, output_path, options, self.target_signature, self)
        self._worker.progress.connect(self._show_progress)
        self._worker.finished.connect(self._copy_finished)
        self._set_busy(True)
        self._show_progress("Validating both SPD files...", 0, 0)
        self._worker.start()

    def _set_busy(self, busy: bool) -> None:
        self.files_panel.setEnabled(not busy)
        self.options_panel.setEnabled(not busy)
        self.cancel_button.setEnabled(not busy)
        self._update_copy_button()
        self.progress.setVisible(busy)

    @Slot(str, object, object)
    def _show_progress(self, message: str, current: int, total: int) -> None:
        self.status.setText(message)
        self.status.show()
        self.progress.setRange(0, 100 if total > 0 else 0)
        if total > 0:
            self.progress.setValue(min(100, max(0, current * 100 // total)))

    @Slot()
    def _copy_finished(self) -> None:
        worker = self._worker
        assert worker is not None
        self._worker = None
        worker.deleteLater()
        self._set_busy(False)
        if worker.error is not None:
            self.status.setText("Copy stopped.")
            self._show_message(QMessageBox.Icon.Warning, "Could Not Copy Settings", worker.error)
            return

        self.output_path = worker.output_path
        self.status.setText("Settings copied.")
        summary = "\n".join(worker.summary)
        self._show_message(
            QMessageBox.Icon.Information,
            "Settings Copied",
            f"{summary}\n\nSaved to {self.output_path}\n\nLoad this SPD to continue working with it.",
        )
        self.accept()

    def _show_message(self, icon: QMessageBox.Icon, title: str, message: str) -> None:
        box = QMessageBox(icon, title, message, QMessageBox.StandardButton.Ok, self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        if icon == QMessageBox.Icon.Warning and (len(message) > 480 or message.count("\n") > 6):
            first_line = message.lstrip().partition("\n")[0]
            box.setText(first_line if len(first_line) <= 180 else first_line[:180].rstrip() + "…")
            box.setDetailedText(message)
        box.exec()
        box.deleteLater()

    def done(self, result: int) -> None:
        if self._worker is None:
            super().done(result)
