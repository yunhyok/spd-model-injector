from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QWidget


def section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("sectionTitle", True)
    return label


def apply_theme(window: QWidget) -> None:
    app = QApplication.instance()
    # Fusion consistently honors the palette for Windows controls and popup menus.
    if app.style().objectName().lower() != "fusion":
        app.setStyle("Fusion")
    palette = QPalette()
    for role, color in {
        QPalette.ColorRole.Window: "#202226",
        QPalette.ColorRole.WindowText: "#eceef1",
        QPalette.ColorRole.Base: "#191b1f",
        QPalette.ColorRole.AlternateBase: "#25282e",
        QPalette.ColorRole.Text: "#eceef1",
        QPalette.ColorRole.Button: "#363b43",
        QPalette.ColorRole.ButtonText: "#eceef1",
        QPalette.ColorRole.Highlight: "#345d8c",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.ToolTipBase: "#363b43",
        QPalette.ColorRole.ToolTipText: "#ffffff",
        QPalette.ColorRole.PlaceholderText: "#a4aebb",
        QPalette.ColorRole.Link: "#8fc1ff",
        QPalette.ColorRole.Light: "#626b78",
        QPalette.ColorRole.Midlight: "#444a54",
        QPalette.ColorRole.Mid: "#555e6b",
        QPalette.ColorRole.Dark: "#15171a",
        QPalette.ColorRole.Shadow: "#111316",
    }.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText,
                 QPalette.ColorRole.ButtonText, QPalette.ColorRole.PlaceholderText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#929ba8"))
    app.setPalette(palette)
    window.setPalette(palette)
    font = window.font()
    font.setPointSize(10)
    window.setFont(font)
    window.setStyleSheet("""
        QFrame[panel="true"], QWidget#model_library {
            background: #2b2e34; border: 1px solid #505761; border-radius: 5px;
        }
        QLabel { background: transparent; border: none; }
        QLabel[sectionTitle="true"] { color: #b9c7d9; font-size: 11px; font-weight: 600; }
        QLabel[muted="true"] { color: #b9c1cc; }
        QLabel#component_name { font-size: 15px; font-weight: 600; }
        QLineEdit, QComboBox, QAbstractItemView, QPlainTextEdit {
            background: #1d2025; color: #eceef1; border: 1px solid #626c7a;
            border-radius: 3px; selection-background-color: #345d8c; selection-color: white;
        }
        QLineEdit, QComboBox { padding: 4px 6px; min-height: 16px; }
        QLineEdit:focus, QComboBox:focus, QAbstractItemView:focus, QPlainTextEdit:focus {
            border: 1px solid #8fc1ff;
        }
        QLineEdit:disabled, QComboBox:disabled, QAbstractItemView:disabled {
            color: #929ba8; background: #292d33; border-color: #444b55;
        }
        QComboBox QAbstractItemView { background: #292d33; selection-background-color: #345d8c; }
        QAbstractItemView { alternate-background-color: #25282e; }
        QAbstractItemView::item:selected { background: #345d8c; color: white; }
        QListWidget::item { padding: 7px 8px; border-bottom: 1px solid #353a43; }
        QListWidget::item:hover { background: #303946; }
        QListWidget::item:selected { background: #345d8c; color: white; }
        QHeaderView::section {
            background: #353a42; color: #dce3ed; border: none;
            border-right: 1px solid #505761; border-bottom: 1px solid #626c7a;
            padding: 5px 7px; font-weight: 600;
        }
        QTableWidget { gridline-color: #3d444f; }
        QTableCornerButton::section { background: #353a42; border: none; }
        QPlainTextEdit#model_editor { background: #15181d; padding: 6px; }
        QPlainTextEdit#output_log { background: #1a1d22; color: #becbdc; padding: 4px; }
        QPushButton {
            background: #3b414b; color: #eceef1; border: 1px solid #717c8d;
            border-radius: 4px; padding: 4px 9px; min-height: 18px;
        }
        QPushButton:hover { background: #48515e; border-color: #a0adbf; }
        QPushButton:pressed { background: #252d39; }
        QPushButton[primary="true"] { background: #386cb0; border-color: #7fa9e0; color: white; }
        QPushButton[primary="true"]:hover { background: #437dc7; }
        QPushButton[primary="true"]:pressed { background: #2d588f; }
        QPushButton:focus { border: 1px solid #c1dcff; }
        QPushButton:disabled { background: #30353e; color: #929ba8; border-color: #4f5865; }
        QTabWidget::pane { background: #202226; border: none; padding-top: 8px; }
        QTabBar::tab {
            background: #292d33; color: #b9c1cc; padding: 8px 14px;
            border-bottom: 2px solid #292d33; margin-right: 3px;
        }
        QTabBar::tab:selected { background: #353b45; color: white; border-bottom-color: #8fc1ff; }
        QTabBar::tab:hover { background: #3a424f; color: white; }
        QMenuBar { background: #292d33; padding: 3px; }
        QMenuBar::item { background: transparent; padding: 4px 10px; }
        QMenuBar::item:selected, QMenu::item:selected { background: #345d8c; color: white; }
        QMenu { background: #292d33; border: 1px solid #626c7a; padding: 4px; }
        QMenu::item { padding: 6px 24px; }
        QMenu::item:disabled { color: #929ba8; }
        QMenu::separator { height: 1px; background: #505761; margin: 4px 8px; }
        QSplitter::handle { background: #202226; }
        QSplitter::handle:hover { background: #6287b4; }
        QStatusBar { background: #292d33; color: #b9c7d9; }
        QStatusBar::item { border: none; }
        QProgressBar { background: #1d2025; border: 1px solid #626c7a; border-radius: 3px; text-align: center; }
        QProgressBar::chunk { background: #386cb0; }
        QToolTip { background: #363b43; color: white; border: 1px solid #7e8999; padding: 6px; }
    """)
