"""Onglet « Aujourd'hui » : saisie rapide, priorités du jour et résumé."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..context import AppContext
from ..dates import format_due, format_duration
from ..tasks import Task
from .theme import COLORS, MARGIN, SPACING
from .widgets import EmptyState, SectionHeader, card

PLACEHOLDER = "Nouvelle tâche — ex. : Appeler la banque demain !1 #admin"
EMPTY_HINT = (
    "Ajoutez une tâche ci-dessus : « !1 » pour la priorité, « #projet »,"
    " « demain » ou « 12/10 » pour l'échéance."
)

# Plafond d'affichage par section : au-delà, un renvoi vers l'onglet « Tâches ».
MAX_ROWS = 50

# Clé renvoyée par TaskStore.today(), titre affiché, et accent rouge ou non.
_SECTIONS: tuple[tuple[str, str, bool], ...] = (
    ("overdue", "En retard", True),
    ("today", "Aujourd'hui", False),
    ("important", "Important", False),
)


def _accent_danger(header: QWidget) -> None:
    """Passe l'intitulé d'une section en rouge sans toucher à son compteur."""
    labels = header.findChildren(QLabel)
    if labels:
        labels[0].setStyleSheet(f"color: {COLORS['danger']}; background: transparent;")


def _role(text: str, role: str) -> QLabel:
    """Libellé coloré par la feuille de style globale, via sa propriété « role »."""
    label = QLabel(text)
    label.setProperty("role", role)
    return label


class TodayTab(QWidget):
    """Vue d'entrée de l'application : ce qui compte aujourd'hui, et rien d'autre."""

    dataChanged = Signal()

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._build()
        self.refresh()

    # -- Construction ----------------------------------------------------

    def _build(self) -> None:
        """Monte la partie fixe : saisie rapide, zone défilante et résumé."""
        root = QVBoxLayout(self)
        root.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        root.setSpacing(SPACING)

        self._input = QLineEdit()
        self._input.setPlaceholderText(PLACEHOLDER)
        self._input.setClearButtonEnabled(True)
        self._input.returnPressed.connect(self._on_submit)
        self._input.textEdited.connect(lambda _texte: self._hide_error())
        root.addWidget(self._input)

        self._error = QLabel()
        self._error.setWordWrap(True)
        self._error.setStyleSheet(f"color: {COLORS['danger']}; background: transparent;")
        self._error.hide()
        root.addWidget(self._error)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self._scroll, 1)

        separator = QFrame()
        separator.setFixedHeight(1)
        separator.setStyleSheet(f"background-color: {COLORS['border']};")
        root.addWidget(separator)

        self._summary = QLabel()
        self._summary.setStyleSheet(f"color: {COLORS['text_dim']}; background: transparent;")
        root.addWidget(self._summary)

    # -- Rafraîchissement ------------------------------------------------

    def refresh(self) -> None:
        """Relit la base et redessine entièrement les sections et le résumé."""
        self._fill_sections()
        self._fill_summary()

    def _fill_sections(self) -> None:
        sections = self.ctx.tasks.today()

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING)
        drawn = False
        for key, title, danger in _SECTIONS:
            tasks = sections.get(key, [])
            if not tasks:  # une section sans tâche n'apparaît pas du tout
                continue
            header = SectionHeader(title, len(tasks))
            if danger:
                _accent_danger(header)
            layout.addWidget(header)
            # Les tâches en retard s'accumulant sans limite, l'affichage est
            # plafonné : un widget par ligne coûte trop cher pour en créer mille.
            shown = tasks[:MAX_ROWS]
            rows = [self._task_row(task, danger) for task in shown]
            hidden = len(tasks) - len(shown)
            if hidden:
                rows.append(
                    _role(
                        f"… et {hidden} autre{'s' if hidden > 1 else ''}"
                        " — la liste complète est dans l'onglet « Tâches ».",
                        "dim",
                    )
                )
            layout.addWidget(card(*rows))
            drawn = True
        if not drawn:
            layout.addWidget(EmptyState("Rien d'urgent.", EMPTY_HINT))
        layout.addStretch(1)

        # Le contenu précédent est détaché puis détruit en différé : aucune connexion
        # ne subsiste vers ses cases à cocher, même si l'on rafraîchit depuis leur signal.
        previous = self._scroll.takeWidget()
        if previous is not None:
            previous.deleteLater()
        self._scroll.setWidget(container)
        container.show()

    def _task_row(self, task: Task, danger: bool) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(10)
        top = Qt.AlignmentFlag.AlignTop

        box = QCheckBox()
        box.setToolTip("Marquer comme terminée")
        box.toggled.connect(lambda done, widget=box, task_id=task.id: self._on_toggle(task_id, done, widget))
        layout.addWidget(box, 0, top)

        title = _role(task.title, "text")
        title.setWordWrap(True)
        layout.addWidget(title, 1)

        if task.project:
            layout.addWidget(_role(f"#{task.project}", "accent"), 0, top)

        due = format_due(task.due_date)
        if due:
            layout.addWidget(_role(due, "danger" if danger else "dim"), 0, top)
        return row

    def _fill_summary(self) -> None:
        done = self._completed_today()
        noun = "tâche terminée" if done <= 1 else "tâches terminées"
        focus = format_duration(self.ctx.times.total_seconds())
        self._summary.setText(f"{done} {noun} aujourd'hui · {focus} de concentration")

    def _completed_today(self) -> int:
        today = date.today().isoformat()
        return sum(1 for task in self.ctx.tasks.list("done") if (task.completed_at or "").startswith(today))

    # -- Actions ---------------------------------------------------------

    def _on_submit(self) -> None:
        try:
            self.ctx.tasks.add_quick(self._input.text())
        except ValueError as erreur:  # la saisie reste en place pour être corrigée
            self._show_error(str(erreur))
            return
        self._input.clear()
        self._hide_error()
        self.dataChanged.emit()
        self.refresh()

    def _on_toggle(self, task_id: int, done: bool, box: QCheckBox) -> None:
        box.setEnabled(False)
        try:
            self.ctx.tasks.set_done(task_id, done)
        except (KeyError, ValueError):
            self._show_error("Cette tâche n'existe plus.")
        else:
            self.dataChanged.emit()
        # Le rafraîchissement détruirait la case pendant l'émission de son propre signal.
        QTimer.singleShot(0, self.refresh)

    def _show_error(self, message: str) -> None:
        self._error.setText(message)
        self._error.show()

    def _hide_error(self) -> None:
        self._error.clear()
        self._error.hide()
