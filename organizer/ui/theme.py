"""Thème sombre de l'application : palette unique et feuille de style globale.

`COLORS` est la seule source de vérité pour les couleurs ; les widgets qui
doivent rester utilisables hors de `apply_theme` y puisent directement.
"""

from __future__ import annotations

from string import Template

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

COLORS: dict[str, str] = {
    "bg": "#12141a",
    "surface": "#1a1d26",
    "surface_alt": "#21252f",
    "border": "#262b37",
    "text": "#e6e9ef",
    "text_dim": "#8b93a7",
    "accent": "#4c8dff",
    "accent_dim": "#2f5fb0",
    "danger": "#ef5d5d",
    "warn": "#f0a92e",
    "ok": "#3ecf8e",
}

RADIUS = 10
SPACING = 12
MARGIN = 16

# Nuances dérivées de la palette, utiles aux seuls états des boutons. Publiques :
# `widgets.py` les réutilise pour ses boutons autonomes, et une seconde copie de
# ces valeurs finirait par diverger de celle-ci.
TINTS: dict[str, str] = {
    "on_accent": "#0f1320",
    "accent_hover": "#6aa1ff",
    "on_danger": "#1c0e0e",
}

_STYLESHEET = Template(
    """
    QWidget { color: $text; }
    QMainWindow, QDialog { background-color: $bg; }
    QLabel, QCheckBox, QRadioButton, QGroupBox { background: transparent; }
    /* Rôles de libellé : une propriété Qt évite une feuille de style posée sur
       chaque ligne, dont le coût se paie à toute reconstruction de liste. */
    QLabel[role="dim"] { color: $text_dim; }
    QLabel[role="text"] { color: $text; }
    QLabel[role="accent"] { color: $accent; }
    QLabel[role="danger"] { color: $danger; }
    QToolTip {
        background-color: $surface_alt; color: $text;
        border: 1px solid $border; border-radius: 6px; padding: 4px 8px;
    }

    QPushButton {
        background-color: $surface_alt; color: $text;
        border: 1px solid $border; border-radius: 8px; padding: 6px 14px;
    }
    QPushButton:hover { background-color: $border; }
    QPushButton:pressed { background-color: $surface; }
    QPushButton:disabled { color: $text_dim; border-color: $surface_alt; background-color: $surface; }
    QPushButton[kind="primary"] {
        background-color: $accent; border-color: $accent; color: $on_accent; font-weight: 600;
    }
    QPushButton[kind="primary"]:hover { background-color: $accent_hover; border-color: $accent_hover; }
    QPushButton[kind="primary"]:pressed { background-color: $accent_dim; border-color: $accent_dim; }
    QPushButton[kind="ghost"] { background-color: transparent; color: $text_dim; }
    QPushButton[kind="ghost"]:hover { background-color: $surface_alt; color: $text; border-color: $accent; }
    QPushButton[kind="danger"] { background-color: transparent; color: $danger; border-color: $danger; }
    QPushButton[kind="danger"]:hover { background-color: $danger; color: $on_danger; }

    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox,
    QComboBox, QDateEdit, QTimeEdit, QDateTimeEdit {
        background-color: $surface; color: $text;
        border: 1px solid $border; border-radius: 8px; padding: 6px 8px;
        selection-background-color: $accent_dim; selection-color: $text;
    }
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus,
    QDoubleSpinBox:focus, QComboBox:focus { border-color: $accent; }
    QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
    QSpinBox:disabled, QComboBox:disabled { color: $text_dim; }
    QComboBox::drop-down { border: none; width: 18px; }
    QComboBox QAbstractItemView {
        background-color: $surface_alt; border: 1px solid $border;
        selection-background-color: $accent_dim; selection-color: $text;
    }
    QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 14px; border: none; background: transparent; }

    QListWidget, QListView, QTreeWidget, QTreeView, QTableWidget, QTableView {
        background-color: $surface; alternate-background-color: $surface_alt;
        border: 1px solid $border; border-radius: ${radius}px; outline: none;
    }
    QListView::item, QTreeView::item { padding: 6px 8px; border-radius: 6px; }
    QListView::item:hover, QTreeView::item:hover { background-color: $surface_alt; }
    QListView::item:selected, QTreeView::item:selected,
    QTableView::item:selected { background-color: $accent_dim; color: $text; }
    QTableView { gridline-color: $border; }
    QHeaderView::section {
        background-color: $surface_alt; color: $text_dim;
        border: none; border-bottom: 1px solid $border; padding: 6px 8px;
    }

    QCheckBox, QRadioButton { spacing: 8px; }
    QCheckBox::indicator, QRadioButton::indicator {
        width: 15px; height: 15px; border: 1px solid $border;
        border-radius: 4px; background-color: $surface;
    }
    QRadioButton::indicator { border-radius: 8px; }
    QCheckBox::indicator:hover, QRadioButton::indicator:hover { border-color: $accent; }
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {
        background-color: $accent; border-color: $accent;
    }

    QScrollArea, QAbstractScrollArea::corner { background: transparent; border: none; }
    QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
    QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
    QScrollBar::handle:vertical { background-color: $border; border-radius: 5px; min-height: 28px; }
    QScrollBar::handle:horizontal { background-color: $border; border-radius: 5px; min-width: 28px; }
    QScrollBar::handle:hover { background-color: $text_dim; }
    QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
    QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

    QTabWidget::pane { border: 1px solid $border; border-radius: ${radius}px; }
    QTabBar::tab { background: transparent; color: $text_dim; padding: 8px 14px; border-radius: 8px; }
    QTabBar::tab:selected { background-color: $surface_alt; color: $text; }

    QProgressBar {
        background-color: $surface_alt; color: $text_dim;
        border: none; border-radius: 5px; height: 10px; text-align: center;
    }
    QProgressBar::chunk { background-color: $accent; border-radius: 5px; }

    QStatusBar { background-color: $surface; color: $text_dim; border-top: 1px solid $border; }
    QStatusBar::item { border: none; }
    QMenu {
        background-color: $surface_alt; color: $text;
        border: 1px solid $border; border-radius: 8px; padding: 4px;
    }
    QMenu::item { padding: 6px 18px; border-radius: 6px; }
    QMenu::item:selected { background-color: $accent_dim; }
    QSplitter::handle { background-color: $border; }
    """
)


def _palette() -> QPalette:
    """Palette complète : Fusion s'en sert pour tout ce que la feuille de style ne couvre pas."""
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: "bg",
        QPalette.ColorRole.WindowText: "text",
        QPalette.ColorRole.Base: "surface",
        QPalette.ColorRole.AlternateBase: "surface_alt",
        QPalette.ColorRole.ToolTipBase: "surface_alt",
        QPalette.ColorRole.ToolTipText: "text",
        QPalette.ColorRole.PlaceholderText: "text_dim",
        QPalette.ColorRole.Text: "text",
        QPalette.ColorRole.Button: "surface_alt",
        QPalette.ColorRole.ButtonText: "text",
        QPalette.ColorRole.BrightText: "danger",
        QPalette.ColorRole.Light: "surface_alt",
        QPalette.ColorRole.Midlight: "border",
        QPalette.ColorRole.Mid: "border",
        QPalette.ColorRole.Dark: "bg",
        QPalette.ColorRole.Shadow: "bg",
        QPalette.ColorRole.Link: "accent",
        QPalette.ColorRole.LinkVisited: "accent_dim",
        QPalette.ColorRole.Highlight: "accent_dim",
        QPalette.ColorRole.HighlightedText: "text",
    }
    for role, key in roles.items():
        palette.setColor(role, QColor(COLORS[key]))
    disabled = {
        QPalette.ColorRole.WindowText: "text_dim",
        QPalette.ColorRole.Text: "text_dim",
        QPalette.ColorRole.ButtonText: "text_dim",
        QPalette.ColorRole.Base: "surface",
        QPalette.ColorRole.Button: "surface",
        QPalette.ColorRole.Highlight: "surface_alt",
        QPalette.ColorRole.HighlightedText: "text_dim",
    }
    for role, key in disabled.items():
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(COLORS[key]))
    return palette


def stylesheet() -> str:
    """Feuille de style de l'application, couleurs de `COLORS` substituées."""
    return _STYLESHEET.substitute(**COLORS, **TINTS, radius=RADIUS)


def apply_theme(app: QApplication) -> None:
    """Applique le style Fusion, la palette sombre et la feuille de style."""
    app.setStyle("Fusion")
    app.setPalette(_palette())
    app.setStyleSheet(stylesheet())
