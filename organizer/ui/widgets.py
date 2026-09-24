"""Briques d'interface réutilisables par les onglets.

Chaque fabrique renvoie un widget autonome : elle porte sa propre feuille de
style tirée de `COLORS`, donc elle reste présentable même sans `apply_theme`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .theme import COLORS, MARGIN, RADIUS, SPACING, TINTS

BUTTON_KINDS = ("primary", "ghost", "danger")


def _resized(base: QFont, delta: int, bold: bool = False) -> QFont:
    """Copie de la police avec une taille décalée de `delta` points."""
    font = QFont(base)
    font.setBold(bold)
    if font.pointSize() > 0:
        font.setPointSize(max(font.pointSize() + delta, 6))
    return font


def _button_style(kind: str) -> str:
    """Style d'une variante de bouton, pour un usage hors du thème global."""
    common = "border-radius: 8px; padding: 6px 14px;"
    if kind == "primary":
        return (
            f"QPushButton {{ {common} background-color: {COLORS['accent']};"
            f" border: 1px solid {COLORS['accent']}; color: {TINTS['on_accent']}; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: {TINTS['accent_hover']};"
            f" border-color: {TINTS['accent_hover']}; }}"
            f"QPushButton:pressed {{ background-color: {COLORS['accent_dim']};"
            f" border-color: {COLORS['accent_dim']}; }}"
            f"QPushButton:disabled {{ background-color: {COLORS['surface_alt']};"
            f" border-color: {COLORS['border']}; color: {COLORS['text_dim']}; }}"
        )
    if kind == "ghost":
        return (
            f"QPushButton {{ {common} background-color: transparent;"
            f" border: 1px solid {COLORS['border']}; color: {COLORS['text_dim']}; }}"
            f"QPushButton:hover {{ background-color: {COLORS['surface_alt']};"
            f" border-color: {COLORS['accent']}; color: {COLORS['text']}; }}"
            f"QPushButton:disabled {{ border-color: {COLORS['surface_alt']}; color: {COLORS['text_dim']}; }}"
        )
    return (
        f"QPushButton {{ {common} background-color: transparent;"
        f" border: 1px solid {COLORS['danger']}; color: {COLORS['danger']}; }}"
        f"QPushButton:hover {{ background-color: {COLORS['danger']}; color: {TINTS['on_danger']}; }}"
        f"QPushButton:disabled {{ border-color: {COLORS['surface_alt']}; color: {COLORS['text_dim']}; }}"
    )


def SectionHeader(text: str, count: int | None = None) -> QWidget:
    """Intitulé de section en gras, suivi d'un compteur discret."""
    label = text.strip()
    if not label:
        raise ValueError("L'en-tête de section doit avoir un intitulé.")

    container = QWidget()
    container.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)

    title = QLabel(label)
    title.setFont(_resized(title.font(), 1, bold=True))
    title.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")
    layout.addWidget(title)

    if count is not None:
        badge = QLabel(str(count))
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"color: {COLORS['text_dim']}; background-color: {COLORS['surface_alt']};"
            " border-radius: 9px; padding: 1px 8px;"
        )
        layout.addWidget(badge)

    layout.addStretch(1)
    return container


def EmptyState(message: str, hint: str = "") -> QWidget:
    """Message centré affiché à la place d'une liste vide."""
    text = message.strip()
    if not text:
        raise ValueError("L'état vide doit avoir un message.")

    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(MARGIN, MARGIN * 2, MARGIN, MARGIN * 2)
    layout.setSpacing(6)
    layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

    label = QLabel(text)
    label.setWordWrap(True)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setFont(_resized(label.font(), 1))
    label.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")
    layout.addWidget(label)

    if hint.strip():
        note = QLabel(hint.strip())
        note.setWordWrap(True)
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note.setFont(_resized(note.font(), -1))
        note.setStyleSheet(f"color: {COLORS['text_dim']}; background: transparent;")
        layout.addWidget(note)

    return container


def styled_button(text: str, kind: str = "primary") -> QPushButton:
    """Bouton d'action. `kind` : `primary`, `ghost` ou `danger`."""
    if kind not in BUTTON_KINDS:
        raise ValueError(f"Type de bouton inconnu : « {kind} ».")

    button = QPushButton(text)
    # Lue aussi par la feuille de style globale, qui sélectionne sur QPushButton[kind="…"].
    button.setProperty("kind", kind)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setStyleSheet(_button_style(kind))
    return button


def card(*children: QWidget) -> QWidget:
    """Conteneur arrondi qui empile ses enfants verticalement."""
    frame = QFrame()
    frame.setObjectName("card")
    frame.setStyleSheet(
        f"QFrame#card {{ background-color: {COLORS['surface']};"
        f" border: 1px solid {COLORS['border']}; border-radius: {RADIUS}px; }}"
    )
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
    layout.setSpacing(SPACING)
    for child in children:
        layout.addWidget(child)
    return frame


def read_setting(settings, key: str, default):
    """Réglage lu sans jamais empêcher le démarrage.

    Une valeur illisible en JSON fait lever `ValueError` au magasin. Le schéma
    de la base étant figé, la garde est posée ici, du côté de l'interface : le
    défaut prend le relais plutôt que de bloquer l'application.
    """
    try:
        return settings.get(key, default)
    except ValueError:
        return default
