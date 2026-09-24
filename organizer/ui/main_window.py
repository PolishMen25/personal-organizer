"""Fenêtre principale : barre latérale, en-tête, pile des onglets et barre d'état."""

from __future__ import annotations

import base64
from datetime import date

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_TITLE
from ..context import AppContext
from ..dates import format_duration, format_long
from .files_tab import FilesTab
from .focus_tab import FocusTab
from .notes_tab import NotesTab
from .tasks_tab import TasksTab
from .theme import COLORS, MARGIN, SPACING
from .widgets import read_setting
from .today_tab import TodayTab

SIDEBAR_WIDTH = 200

_SECTIONS: tuple[tuple[str, type[QWidget]], ...] = (
    ("Aujourd'hui", TodayTab),
    ("Tâches", TasksTab),
    ("Notes", NotesTab),
    ("Concentration", FocusTab),
    ("Fichiers", FilesTab),
)

_GEOMETRY_KEY = "window_geometry"
_SECTION_KEY = "last_section"

# Délai de regroupement des écritures : plusieurs `dataChanged` rapprochés
# (une case cochée, un ajout) ne provoquent qu'une seule relecture.
REFRESH_DELAY_MS = 50

# Vérification du passage de minuit : l'en-tête et les vues du jour en dépendent.
MIDNIGHT_CHECK_MS = 30_000

# Au-delà, la liste des règles écartées deviendrait illisible.
_MAX_RULES_ERRORS = 6


def _scaled(label: QLabel, delta: int, bold: bool = False) -> None:
    """Décale de `delta` points la police d'un libellé."""
    font = label.font()
    font.setBold(bold)
    if font.pointSize() > 0:
        font.setPointSize(max(font.pointSize() + delta, 6))
    label.setFont(font)


class MainWindow(QMainWindow):
    """Assemble les cinq onglets et les tient synchronisés entre eux."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._tabs: list[QWidget] = []
        # Empêche un refresh en cascade de relancer les signaux dataChanged.
        self._refreshing = False
        # Onglets à relire : ils ne le seront qu'au moment où ils s'affichent.
        self._stale: set[int] = set()
        self._day = date.today()

        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(960, 620)
        self.resize(1180, 760)

        self._sidebar = self._build_sidebar()
        self._section_label = QLabel()
        self._date_label = QLabel()
        self._stack = QStackedWidget()
        self._focus_label = QLabel()
        self._tasks_label = QLabel()

        self._pending = QTimer(self)
        self._pending.setSingleShot(True)
        self._pending.setInterval(REFRESH_DELAY_MS)
        self._pending.timeout.connect(self._apply_pending)

        self._midnight = QTimer(self)
        self._midnight.setInterval(MIDNIGHT_CHECK_MS)
        self._midnight.timeout.connect(self._check_day)
        self._midnight.start()

        self._build_layout()
        self._build_tabs()
        self._build_status_bar()
        self._build_shortcuts()

        self._restore_geometry()
        self._sidebar.setCurrentRow(self._restored_section())
        self.refresh_status()
        # Après l'ouverture : la boîte ne doit pas s'afficher sur une fenêtre absente.
        QTimer.singleShot(0, self._report_rules_errors)

    # Construction

    def _build_sidebar(self) -> QListWidget:
        sidebar = QListWidget()
        sidebar.setFrameShape(QFrame.Shape.NoFrame)
        sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sidebar.setStyleSheet(
            "QListWidget { background: transparent; border: none; outline: none; }"
            f"QListWidget::item {{ color: {COLORS['text_dim']}; padding: 9px 10px;"
            " border-radius: 8px; margin-bottom: 2px; }"
            f"QListWidget::item:hover {{ background-color: {COLORS['surface_alt']};"
            f" color: {COLORS['text']}; }}"
            f"QListWidget::item:selected {{ background-color: {COLORS['accent_dim']};"
            f" color: {COLORS['text']}; }}"
        )
        sidebar.currentRowChanged.connect(self._on_section_changed)
        return sidebar

    def _build_layout(self) -> None:
        brand = QLabel(APP_TITLE)
        _scaled(brand, 1, bold=True)
        brand.setStyleSheet(f"color: {COLORS['text']}; background: transparent; padding-left: 10px;")

        panel = QWidget()
        panel.setObjectName("sidebar")
        panel.setFixedWidth(SIDEBAR_WIDTH)
        panel.setStyleSheet(
            f"QWidget#sidebar {{ background-color: {COLORS['surface']};"
            f" border-right: 1px solid {COLORS['border']}; }}"
        )
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(SPACING, MARGIN, SPACING, SPACING)
        panel_layout.setSpacing(MARGIN)
        panel_layout.addWidget(brand)
        panel_layout.addWidget(self._sidebar, 1)

        _scaled(self._section_label, 5, bold=True)
        self._section_label.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")
        self._date_label.setStyleSheet(f"color: {COLORS['text_dim']}; background: transparent;")

        header = QWidget()
        header.setObjectName("header")
        header.setStyleSheet(
            f"QWidget#header {{ background-color: {COLORS['bg']};"
            f" border-bottom: 1px solid {COLORS['border']}; }}"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(MARGIN, SPACING, MARGIN, SPACING)
        header_layout.addWidget(self._section_label)
        header_layout.addStretch(1)
        header_layout.addWidget(self._date_label)

        self._stack.setContentsMargins(MARGIN, SPACING, MARGIN, SPACING)
        self._stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(header)
        content_layout.addWidget(self._stack, 1)

        central = QWidget()
        central_layout = QHBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(panel)
        central_layout.addWidget(content, 1)
        self.setCentralWidget(central)

    def _build_tabs(self) -> None:
        # Insertion sans signaux : la sélection d'office du premier élément déclencherait
        # un rafraîchissement avant que la section mémorisée soit connue.
        self._sidebar.blockSignals(True)
        try:
            for index, (label, factory) in enumerate(_SECTIONS):
                tab = factory(self.ctx)
                tab.dataChanged.connect(lambda source=index: self._on_data_changed(source))
                self._tabs.append(tab)
                self._stack.addWidget(tab)
                self._sidebar.addItem(label)
        finally:
            self._sidebar.blockSignals(False)
            self._sidebar.setCurrentRow(-1)

    def _build_status_bar(self) -> None:
        self._focus_label.setStyleSheet(f"color: {COLORS['text_dim']}; padding: 0 8px;")
        self._tasks_label.setStyleSheet(f"color: {COLORS['text_dim']}; padding: 0 8px;")
        bar = self.statusBar()
        bar.setSizeGripEnabled(False)
        bar.addWidget(self._focus_label)
        bar.addPermanentWidget(self._tasks_label)

    def _build_shortcuts(self) -> None:
        for index in range(len(_SECTIONS)):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda row=index: self._sidebar.setCurrentRow(row))
        QShortcut(QKeySequence("Ctrl+Q"), self).activated.connect(self.close)

    # Synchronisation

    def refresh_status(self) -> None:
        """Met à jour la barre d'état (concentration du jour, tâches ouvertes) et la date."""
        self._date_label.setText(format_long(self._day))
        self._focus_label.setText(
            f"Concentration aujourd'hui : {format_duration(self.ctx.times.total_seconds())}"
        )
        open_tasks = len(self.ctx.tasks.list("open"))
        if open_tasks == 0:
            self._tasks_label.setText("Aucune tâche ouverte")
        else:
            plural = "s" if open_tasks > 1 else ""
            self._tasks_label.setText(f"{open_tasks} tâche{plural} ouverte{plural}")

    def _on_data_changed(self, source: int) -> None:
        """Un onglet a écrit en base : les autres sont marqués à relire.

        Relire les cinq onglets à chaque écriture gelait la fenêtre de plusieurs
        secondes, alors que l'utilisateur n'en regarde qu'un : les autres
        attendent d'être affichés.
        """
        if self._refreshing:
            return
        self._stale.update(index for index in range(len(self._tabs)) if index != source)
        self._pending.start()

    def _apply_pending(self) -> None:
        """Relit l'onglet visible s'il a besoin de l'être, puis la barre d'état."""
        self._refresh_tab(self._sidebar.currentRow())
        self.refresh_status()

    def _refresh_tab(self, row: int) -> None:
        if not 0 <= row < len(self._tabs) or row not in self._stale:
            return
        self._stale.discard(row)
        self._refreshing = True
        try:
            self._tabs[row].refresh()
        finally:
            self._refreshing = False

    def flush_refresh(self) -> None:
        """Applique tout de suite une relecture en attente (fermeture, tests)."""
        if self._pending.isActive():
            self._pending.stop()
            self._apply_pending()

    def _check_day(self) -> None:
        """Au passage de minuit, la date de l'en-tête et les vues du jour changent."""
        today = date.today()
        if today == self._day:
            return
        self._day = today
        self._stale.update(range(len(self._tabs)))
        self._apply_pending()

    def _on_section_changed(self, row: int) -> None:
        if not 0 <= row < len(self._tabs):
            return
        self._stack.setCurrentIndex(row)
        self._section_label.setText(_SECTIONS[row][0])
        self.ctx.settings.set(_SECTION_KEY, row)
        if not self._refreshing:
            self._refresh_tab(row)
        self.refresh_status()

    def _report_rules_errors(self) -> None:
        """Signale les règles de rangement écartées au chargement."""
        if not self.ctx.rules_errors:
            return
        shown = self.ctx.rules_errors[:_MAX_RULES_ERRORS]
        remaining = len(self.ctx.rules_errors) - len(shown)
        if remaining > 0:
            shown.append(f"… et {remaining} autre(s).")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Règles de rangement")
        box.setText("Certaines règles de rangement n'ont pas pu être lues.")
        box.setInformativeText("\n".join(f"• {message}" for message in shown))
        box.addButton("Fermer", QMessageBox.ButtonRole.AcceptRole)
        box.exec()

    # État de la fenêtre

    def _restored_section(self) -> int:
        section = read_setting(self.ctx.settings, _SECTION_KEY, 0)
        if isinstance(section, int) and 0 <= section < len(_SECTIONS):
            return section
        return 0

    def _restore_geometry(self) -> None:
        raw = read_setting(self.ctx.settings, _GEOMETRY_KEY, None)
        if not isinstance(raw, str):
            return
        try:
            data = base64.b64decode(raw.encode("ascii"), validate=True)
        except (ValueError, UnicodeEncodeError):
            return
        self.restoreGeometry(QByteArray(data))

    def _save_geometry(self) -> None:
        encoded = base64.b64encode(self.saveGeometry().data()).decode("ascii")
        self.ctx.settings.set(_GEOMETRY_KEY, encoded)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._midnight.stop()
        self.flush_refresh()
        self._save_geometry()
        for tab in self._tabs:
            flush = getattr(tab, "flush", None)
            if callable(flush):
                # La fermeture ne doit pas être bloquée par un onglet qui échoue à enregistrer.
                try:
                    flush()
                except Exception:
                    pass
        event.accept()
