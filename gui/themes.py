"""Utilitarian, dense desktop themes for PySide6 widgets."""

from typing import Dict

# ── White (Light) Theme ───────────────────────────────────────────────────
_WHITE_THEME = """
QWidget {
    background-color: #f5f5f5;
    color: #1a1a1a;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 12px;
}

QMainWindow, QDialog {
    background-color: #f0f0f0;
}

QSplitter::handle {
    background-color: #d0d0d0;
}
QSplitter::handle:horizontal {
    width: 3px;
}
QSplitter::handle:vertical {
    height: 3px;
}

/* ── Table & Headers ── */
QTableWidget {
    background-color: #ffffff;
    alternate-background-color: #fafafa;
    border: 1px solid #d0d0d0;
    gridline-color: #e5e5e5;
    selection-background-color: #2b79c2;
    selection-color: #ffffff;
}
QTableWidget::item {
    padding: 2px 4px;
}
QTableWidget::item:selected {
    background-color: #2b79c2;
    color: #ffffff;
}
QHeaderView::section {
    background-color: #eaeaea;
    color: #333333;
    padding: 3px 6px;
    border: none;
    border-right: 1px solid #d0d0d0;
    border-bottom: 1px solid #c0c0c0;
    font-weight: 600;
}

/* ── Buttons ── */
QPushButton {
    background-color: #e4e4e4;
    border: 1px solid #b8b8b8;
    border-radius: 2px;
    padding: 3px 10px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #d8d8d8;
    border-color: #999999;
}
QPushButton:pressed {
    background-color: #cacaca;
}
QPushButton:disabled {
    background-color: #eeeeee;
    color: #999999;
    border-color: #cccccc;
}

/* ── Inputs & Edits ── */
QPlainTextEdit, QTextEdit, QLineEdit {
    background-color: #ffffff;
    color: #111111;
    border: 1px solid #c0c0c0;
    border-radius: 2px;
    padding: 2px;
}
QPlainTextEdit:focus, QTextEdit:focus, QLineEdit:focus {
    border: 1px solid #2b79c2;
}

QComboBox, QSpinBox {
    background-color: #ffffff;
    color: #111111;
    border: 1px solid #b8b8b8;
    border-radius: 2px;
    padding: 2px 6px;
}

/* ── Tabs ── */
QTabWidget::pane {
    border: 1px solid #d0d0d0;
    background-color: #ffffff;
    top: -1px;
}
QTabBar::tab {
    background-color: #e8e8e8;
    color: #444444;
    border: 1px solid #d0d0d0;
    border-bottom: none;
    padding: 4px 12px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: #ffffff;
    color: #111111;
    font-weight: 600;
    border-bottom: 1px solid #ffffff;
}

/* ── Slider ── */
QSlider::groove:horizontal {
    height: 4px;
    background: #dcdcdc;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: #2b79c2;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #ffffff;
    border: 1px solid #888888;
    width: 12px;
    margin-top: -4px;
    margin-bottom: -4px;
    border-radius: 6px;
}

/* ── Progress Bar ── */
QProgressBar {
    border: 1px solid #c0c0c0;
    border-radius: 2px;
    text-align: center;
    background-color: #e8e8e8;
    height: 14px;
}
QProgressBar::chunk {
    background-color: #2b79c2;
}

/* ── Menu & Status Bar ── */
QMenu {
    background-color: #ffffff;
    color: #111111;
    border: 1px solid #b0b0b0;
    padding: 2px;
}
QMenu::item {
    padding: 4px 20px 4px 12px;
}
QMenu::item:selected {
    background-color: #2b79c2;
    color: #ffffff;
}

QStatusBar {
    background-color: #eaeaea;
    border-top: 1px solid #d0d0d0;
}
"""

# ── Dark (Slate / Charcoal) Theme ─────────────────────────────────────────
_DARK_THEME = """
QWidget {
    background-color: #202225;
    color: #dcddde;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 12px;
}

QMainWindow, QDialog {
    background-color: #1b1c1e;
}

QSplitter::handle {
    background-color: #2f3136;
}
QSplitter::handle:horizontal {
    width: 3px;
}
QSplitter::handle:vertical {
    height: 3px;
}

/* ── Table & Headers ── */
QTableWidget {
    background-color: #1e1f22;
    alternate-background-color: #24262a;
    border: 1px solid #36393f;
    gridline-color: #2b2d31;
    selection-background-color: #38608e;
    selection-color: #ffffff;
}
QTableWidget::item {
    padding: 2px 4px;
}
QTableWidget::item:selected {
    background-color: #38608e;
    color: #ffffff;
}
QHeaderView::section {
    background-color: #2b2d31;
    color: #b9bbbe;
    padding: 3px 6px;
    border: none;
    border-right: 1px solid #36393f;
    border-bottom: 1px solid #36393f;
    font-weight: 600;
}

/* ── Buttons ── */
QPushButton {
    background-color: #2f3136;
    border: 1px solid #40444b;
    border-radius: 2px;
    padding: 3px 10px;
    color: #ffffff;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #3a3c42;
    border-color: #585c64;
}
QPushButton:pressed {
    background-color: #26282c;
}
QPushButton:disabled {
    background-color: #1c1d20;
    color: #55585e;
    border-color: #2a2c30;
}

/* ── Inputs & Edits ── */
QPlainTextEdit, QTextEdit, QLineEdit {
    background-color: #18191c;
    color: #e0e0e0;
    border: 1px solid #36393f;
    border-radius: 2px;
    padding: 2px;
}
QPlainTextEdit:focus, QTextEdit:focus, QLineEdit:focus {
    border: 1px solid #5865f2;
}

QComboBox, QSpinBox {
    background-color: #25272b;
    color: #e0e0e0;
    border: 1px solid #40444b;
    border-radius: 2px;
    padding: 2px 6px;
}

/* ── Tabs ── */
QTabWidget::pane {
    border: 1px solid #36393f;
    background-color: #1e1f22;
    top: -1px;
}
QTabBar::tab {
    background-color: #2b2d31;
    color: #8e9297;
    border: 1px solid #36393f;
    border-bottom: none;
    padding: 4px 12px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: #1e1f22;
    color: #ffffff;
    font-weight: 600;
    border-bottom: 1px solid #1e1f22;
}

/* ── Slider ── */
QSlider::groove:horizontal {
    height: 4px;
    background: #36393f;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: #38608e;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #dcddde;
    border: 1px solid #202225;
    width: 12px;
    margin-top: -4px;
    margin-bottom: -4px;
    border-radius: 6px;
}

/* ── Progress Bar ── */
QProgressBar {
    border: 1px solid #36393f;
    border-radius: 2px;
    text-align: center;
    background-color: #18191c;
    color: #ffffff;
    height: 14px;
}
QProgressBar::chunk {
    background-color: #38608e;
}

/* ── Menu & Status Bar ── */
QMenu {
    background-color: #202225;
    color: #dcddde;
    border: 1px solid #36393f;
    padding: 2px;
}
QMenu::item {
    padding: 4px 20px 4px 12px;
}
QMenu::item:selected {
    background-color: #38608e;
    color: #ffffff;
}

QStatusBar {
    background-color: #1b1c1e;
    border-top: 1px solid #2f3136;
}
"""

# ── OLED Dark (Pure #000000 Pixel-Off) Theme ──────────────────────────────
_OLED_DARK_THEME = """
QWidget {
    background-color: #000000;
    color: #ffffff;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 12px;
}

QMainWindow, QDialog {
    background-color: #000000;
}

QSplitter::handle {
    background-color: #1a1a1a;
}
QSplitter::handle:horizontal {
    width: 2px;
}
QSplitter::handle:vertical {
    height: 2px;
}

/* ── Table & Headers ── */
QTableWidget {
    background-color: #000000;
    alternate-background-color: #080808;
    border: 1px solid #222222;
    gridline-color: #181818;
    selection-background-color: #1b4974;
    selection-color: #ffffff;
}
QTableWidget::item {
    padding: 2px 4px;
}
QTableWidget::item:selected {
    background-color: #1b4974;
    color: #ffffff;
}
QHeaderView::section {
    background-color: #0d0d0d;
    color: #dddddd;
    padding: 3px 6px;
    border: none;
    border-right: 1px solid #222222;
    border-bottom: 1px solid #222222;
    font-weight: 600;
}

/* ── Buttons ── */
QPushButton {
    background-color: #111111;
    border: 1px solid #333333;
    border-radius: 2px;
    padding: 3px 10px;
    color: #ffffff;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #222222;
    border-color: #555555;
}
QPushButton:pressed {
    background-color: #080808;
}
QPushButton:disabled {
    background-color: #050505;
    color: #444444;
    border-color: #1a1a1a;
}

/* ── Inputs & Edits ── */
QPlainTextEdit, QTextEdit, QLineEdit {
    background-color: #000000;
    color: #ffffff;
    border: 1px solid #2a2a2a;
    border-radius: 2px;
    padding: 2px;
}
QPlainTextEdit:focus, QTextEdit:focus, QLineEdit:focus {
    border: 1px solid #1b4974;
}

QComboBox, QSpinBox {
    background-color: #0a0a0a;
    color: #ffffff;
    border: 1px solid #333333;
    border-radius: 2px;
    padding: 2px 6px;
}

/* ── Tabs ── */
QTabWidget::pane {
    border: 1px solid #222222;
    background-color: #000000;
    top: -1px;
}
QTabBar::tab {
    background-color: #0d0d0d;
    color: #888888;
    border: 1px solid #222222;
    border-bottom: none;
    padding: 4px 12px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: #000000;
    color: #ffffff;
    font-weight: 600;
    border-bottom: 1px solid #000000;
}

/* ── Slider ── */
QSlider::groove:horizontal {
    height: 4px;
    background: #1c1c1c;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: #1b4974;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #ffffff;
    border: 1px solid #000000;
    width: 12px;
    margin-top: -4px;
    margin-bottom: -4px;
    border-radius: 6px;
}

/* ── Progress Bar ── */
QProgressBar {
    border: 1px solid #222222;
    border-radius: 2px;
    text-align: center;
    background-color: #080808;
    color: #ffffff;
    height: 14px;
}
QProgressBar::chunk {
    background-color: #1b4974;
}

/* ── Menu & Status Bar ── */
QMenu {
    background-color: #050505;
    color: #ffffff;
    border: 1px solid #333333;
    padding: 2px;
}
QMenu::item {
    padding: 4px 20px 4px 12px;
}
QMenu::item:selected {
    background-color: #1b4974;
    color: #ffffff;
}

QStatusBar {
    background-color: #000000;
    border-top: 1px solid #1a1a1a;
}
"""

THEMES: Dict[str, str] = {
    "White": _WHITE_THEME,
    "Dark": _DARK_THEME,
    "OLED Dark": _OLED_DARK_THEME,
}
