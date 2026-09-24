"""Onglet « Tâches » : liste filtrable, édition et suppression d'une tâche."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QKeyEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..context import AppContext
from ..dates import format_due
from ..tasks import (
    PRIORITY_HIGH,
    PRIORITY_LABELS,
    PRIORITY_LOW,
    PRIORITY_NORMAL,
    Task,
)
from .theme import COLORS, MARGIN, SPACING
from .widgets import EmptyState, SectionHeader, styled_button

PLACEHOLDER = "Nouvelle tâche — ex. : Appeler la banque demain !1 #admin"
SEARCH_PLACEHOLDER = "Rechercher dans les titres et les notes"

# Temporisation de la recherche, comme dans l'onglet « Notes ».
SEARCH_DELAY_MS = 250
ALL_PROJECTS = "Tous les projets"
NO_VALUE = "—"

COLUMNS = ("Fait", "Tâche", "Priorité", "Projet", "Échéance")

# Libellé du filtre, valeur passée à TaskStore.list(), intitulé de la liste.
STATUSES: tuple[tuple[str, str, str], ...] = (
    ("À faire", "open", "À faire"),
    ("Terminées", "done", "Terminées"),
    ("Toutes", "all", "Toutes les tâches"),
)

PRIORITY_ORDER = (PRIORITY_HIGH, PRIORITY_NORMAL, PRIORITY_LOW)
_PRIORITY_KEYS = {PRIORITY_HIGH: "danger", PRIORITY_NORMAL: "accent", PRIORITY_LOW: "text_dim"}
_DOT = 10

HELP_TITLE = "Aide sur la syntaxe"
HELP_TEXT = (
    "Priorité : « !1 » haute, « !2 » normale, « !3 » basse.\n"
    "Projet : « #projet », par exemple « #admin ».\n"
    "Échéance : « aujourd'hui », « demain », « après-demain », un jour de la semaine\n"
    "comme « vendredi », ou une date « 12/10 » ou « 12/10/2027 ».\n"
    "Le reste de la ligne forme le titre.\n\n"
    "Exemple : Appeler la banque demain !1 #admin"
)
EMPTY_HINT = "Ajoutez-en une ci-dessus, puis consultez l'aide sur la syntaxe."

# Une pastille par priorité, dessinée une seule fois pour toute l'application.
_ICONS: dict[int, QIcon] = {}


def _priority_icon(priority: int) -> QIcon:
    """Pastille ronde colorée selon la priorité."""
    if priority not in _ICONS:
        colour = COLORS[_PRIORITY_KEYS.get(priority, "text_dim")]
        pixmap = QPixmap(_DOT, _DOT)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colour))
        painter.drawEllipse(0, 0, _DOT - 1, _DOT - 1)
        painter.end()
        _ICONS[priority] = QIcon(pixmap)
    return _ICONS[priority]


def _read_only(text: str) -> QTableWidgetItem:
    """Cellule non éditable : le tableau n'est qu'un affichage."""
    item = QTableWidgetItem(text)
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    return item


def _short(text: str, width: int = 60) -> str:
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def _to_qdate(day: date) -> QDate:
    return QDate(day.year, day.month, day.day)


def _from_qdate(value: QDate) -> date:
    return date(value.year(), value.month(), value.day())


class TaskDialog(QDialog):
    """Fiche d'édition d'une tâche ; `values()` donne les champs à enregistrer."""

    def __init__(self, task: Task, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modifier la tâche")
        self.setMinimumWidth(440)

        self._title = QLineEdit(task.title)
        self._title.returnPressed.connect(self._on_save)

        self._notes = QPlainTextEdit(task.notes)
        self._notes.setMinimumHeight(110)
        self._notes.setPlaceholderText("Notes (facultatif)")

        self._priority = QComboBox()
        for value in PRIORITY_ORDER:
            self._priority.addItem(PRIORITY_LABELS[value], value)
        found = self._priority.findData(task.priority)
        self._priority.setCurrentIndex(found if found >= 0 else PRIORITY_ORDER.index(PRIORITY_NORMAL))

        self._due = QDateEdit()
        self._due.setCalendarPopup(True)
        self._due.setDisplayFormat("dd/MM/yyyy")
        self._due.setDate(_to_qdate(task.due_date or date.today()))
        self._due.setEnabled(task.due_date is not None)

        self._no_due = QCheckBox("Aucune échéance")
        self._no_due.setChecked(task.due_date is None)
        self._no_due.toggled.connect(lambda coche: self._due.setEnabled(not coche))

        self._project = QLineEdit(task.project)
        self._project.setPlaceholderText("Sans projet")

        due_row = QWidget()
        due_layout = QHBoxLayout(due_row)
        due_layout.setContentsMargins(0, 0, 0, 0)
        due_layout.setSpacing(SPACING)
        due_layout.addWidget(self._due)
        due_layout.addWidget(self._no_due)
        due_layout.addStretch(1)

        form = QFormLayout()
        form.setSpacing(SPACING)
        form.addRow("Titre", self._title)
        form.addRow("Notes", self._notes)
        form.addRow("Priorité", self._priority)
        form.addRow("Échéance", due_row)
        form.addRow("Projet", self._project)

        self._save = styled_button("Enregistrer", "primary")
        self._save.setDefault(True)
        self._save.clicked.connect(self._on_save)
        cancel = styled_button("Annuler", "ghost")
        cancel.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.setSpacing(SPACING)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self._save)

        root = QVBoxLayout(self)
        root.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        root.setSpacing(SPACING)
        root.addLayout(form)
        root.addLayout(buttons)

    def values(self) -> dict[str, object]:
        """Champs à passer à `TaskStore.update()`."""
        return {
            "title": self._title.text().strip(),
            "notes": self._notes.toPlainText().strip(),
            "priority": self._priority.currentData(),
            "due_date": None if self._no_due.isChecked() else _from_qdate(self._due.date()),
            # Le « # » de la saisie rapide est toléré ici aussi.
            "project": self._project.text().strip().lstrip("#").strip(),
        }

    def _on_save(self) -> None:
        if not self._title.text().strip():
            QMessageBox.warning(self, "Titre manquant", "La tâche doit avoir un titre.")
            self._title.setFocus()
            return
        self.accept()


class _TaskTable(QTableWidget):
    """Tableau des tâches ; la touche Suppr y demande la suppression."""

    deleteRequested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Delete and not event.modifiers():
            self.deleteRequested.emit()
            return
        super().keyPressEvent(event)


class TasksTab(QWidget):
    """Liste complète des tâches : filtres, saisie rapide et édition."""

    dataChanged = Signal()

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        # Vrai pendant le remplissage : les signaux des cellules sont alors ignorés.
        self._loading = False
        self._build()
        self.refresh()

    # -- Construction ----------------------------------------------------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        root.setSpacing(SPACING)
        root.addLayout(self._build_top_bar())
        root.addWidget(self._build_error())
        root.addLayout(self._build_filters())
        root.addLayout(self._build_header())
        root.addWidget(self._build_stack(), 1)

    def _build_top_bar(self) -> QHBoxLayout:
        self._input = QLineEdit()
        self._input.setPlaceholderText(PLACEHOLDER)
        self._input.setClearButtonEnabled(True)
        self._input.returnPressed.connect(self._on_add)
        self._input.textEdited.connect(lambda _texte: self._hide_error())

        add = styled_button("Ajouter", "primary")
        add.clicked.connect(self._on_add)
        help_button = styled_button(HELP_TITLE, "ghost")
        help_button.clicked.connect(self._show_help)

        layout = QHBoxLayout()
        layout.setSpacing(SPACING)
        layout.addWidget(self._input, 1)
        layout.addWidget(add)
        layout.addWidget(help_button)
        return layout

    def _build_error(self) -> QLabel:
        self._error = QLabel()
        self._error.setWordWrap(True)
        self._error.setStyleSheet(f"color: {COLORS['danger']}; background: transparent;")
        self._error.hide()
        return self._error

    def _build_filters(self) -> QHBoxLayout:
        self._status_box = QComboBox()
        self._status_box.setToolTip("Statut des tâches")
        for label, value, _title in STATUSES:
            self._status_box.addItem(label, value)
        self._status_box.currentIndexChanged.connect(lambda _index: self.refresh())

        self._project_box = QComboBox()
        self._project_box.setToolTip("Projet")
        self._project_box.setMinimumWidth(160)
        self._project_box.addItem(ALL_PROJECTS, None)
        self._project_box.currentIndexChanged.connect(lambda _index: self.refresh())

        self._search = QLineEdit()
        self._search.setPlaceholderText(SEARCH_PLACEHOLDER)
        self._search.setClearButtonEnabled(True)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DELAY_MS)
        self._search_timer.timeout.connect(self.refresh)
        self._search.textChanged.connect(lambda _texte: self._search_timer.start())

        layout = QHBoxLayout()
        layout.setSpacing(SPACING)
        layout.addWidget(self._status_box)
        layout.addWidget(self._project_box)
        layout.addWidget(self._search, 1)
        return layout

    def _build_header(self) -> QHBoxLayout:
        self._header = QWidget()
        self._header_layout = QHBoxLayout(self._header)
        self._header_layout.setContentsMargins(0, 0, 0, 0)
        self._header_layout.setSpacing(0)

        self._edit_button = styled_button("Modifier", "ghost")
        self._edit_button.clicked.connect(self._open_editor)
        self._delete_button = styled_button("Supprimer", "danger")
        self._delete_button.clicked.connect(self._on_delete)

        layout = QHBoxLayout()
        layout.setSpacing(SPACING)
        layout.addWidget(self._header, 1)
        layout.addWidget(self._edit_button)
        layout.addWidget(self._delete_button)
        return layout

    def _build_stack(self) -> QStackedWidget:
        self._table = _TaskTable()
        self._table.setColumnCount(len(COLUMNS))
        self._table.setHorizontalHeaderLabels(list(COLUMNS))
        self._table.setShowGrid(False)
        self._table.setWordWrap(False)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(30)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

        header = self._table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionsClickable(False)
        header.setHighlightSections(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        self._table.itemChanged.connect(self._on_item_changed)
        self._table.itemSelectionChanged.connect(self._update_actions)
        self._table.doubleClicked.connect(self._on_double_clicked)
        self._table.deleteRequested.connect(self._on_delete)

        self._empty = EmptyState("Aucune tâche pour l'instant.", EMPTY_HINT)
        self._empty_filtered = EmptyState(
            "Aucune tâche ne correspond.",
            "Changez le statut, le projet ou la recherche.",
        )

        self._stack = QStackedWidget()
        for page in (self._table, self._empty, self._empty_filtered):
            self._stack.addWidget(page)
        return self._stack

    # -- Rafraîchissement ------------------------------------------------

    def refresh(self) -> None:
        """Relit la base avec les filtres courants et redessine le tableau."""
        selected = self._selected_id()
        offset = self._table.verticalScrollBar().value()
        self._loading = True
        try:
            self._fill_projects()
            tasks = self.ctx.tasks.list(
                self._status_box.currentData(),
                self._project_box.currentData(),
                self._search.text().strip() or None,
            )
            self._fill_rows(tasks)
        finally:
            self._loading = False
        self._fill_header(len(tasks))
        self._restore_selection(selected)
        self._table.verticalScrollBar().setValue(offset)
        self._stack.setCurrentWidget(self._page_for(len(tasks)))
        self._update_actions()

    def _fill_projects(self) -> None:
        """Recharge la liste des projets en gardant la sélection si elle existe encore."""
        current = self._project_box.currentData()
        self._project_box.blockSignals(True)
        self._project_box.clear()
        self._project_box.addItem(ALL_PROJECTS, None)
        for name in self.ctx.tasks.projects():
            self._project_box.addItem(f"#{name}", name)
        found = self._project_box.findData(current) if current else 0
        self._project_box.setCurrentIndex(max(found, 0))
        self._project_box.blockSignals(False)

    def _fill_rows(self, tasks: list[Task]) -> None:
        self._table.setRowCount(0)  # détruit les cellules précédentes
        self._table.setRowCount(len(tasks))
        today = date.today()
        for row, task in enumerate(tasks):
            self._table.setItem(row, 0, self._done_cell(task))
            self._table.setItem(row, 1, self._title_cell(task))
            self._table.setItem(row, 2, self._priority_cell(task))
            self._table.setItem(row, 3, self._project_cell(task))
            self._table.setItem(row, 4, self._due_cell(task, today))

    def _done_cell(self, task: Task) -> QTableWidgetItem:
        item = QTableWidgetItem()
        item.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable
        )
        item.setCheckState(Qt.CheckState.Checked if task.done else Qt.CheckState.Unchecked)
        # Seule cette cellule porte l'identifiant : elle sert de clé de ligne.
        item.setData(Qt.ItemDataRole.UserRole, task.id)
        item.setToolTip("Rouvrir la tâche" if task.done else "Marquer comme terminée")
        return item

    def _title_cell(self, task: Task) -> QTableWidgetItem:
        item = _read_only(task.title)
        if task.done:
            font = QFont(self._table.font())
            font.setStrikeOut(True)
            item.setFont(font)
            item.setForeground(QColor(COLORS["text_dim"]))
        if task.notes:
            item.setToolTip(task.notes)
        return item

    def _priority_cell(self, task: Task) -> QTableWidgetItem:
        item = _read_only(PRIORITY_LABELS.get(task.priority, NO_VALUE))
        item.setIcon(_priority_icon(task.priority))
        if task.done:
            item.setForeground(QColor(COLORS["text_dim"]))
        return item

    def _project_cell(self, task: Task) -> QTableWidgetItem:
        item = _read_only(f"#{task.project}" if task.project else NO_VALUE)
        colour = "text_dim" if task.done or not task.project else "accent"
        item.setForeground(QColor(COLORS[colour]))
        return item

    def _due_cell(self, task: Task, today: date) -> QTableWidgetItem:
        item = _read_only(format_due(task.due_date, today) or NO_VALUE)
        if task.is_overdue(today):
            item.setForeground(QColor(COLORS["danger"]))
        elif task.done or task.due_date is None:
            item.setForeground(QColor(COLORS["text_dim"]))
        return item

    def _fill_header(self, count: int) -> None:
        """Remplace l'intitulé de la liste, dont le compteur a changé."""
        while self._header_layout.count():
            previous = self._header_layout.takeAt(0).widget()
            if previous is not None:
                # Détaché tout de suite, détruit ensuite : rien ne subsiste à l'écran.
                previous.setParent(None)
                previous.deleteLater()
        title = next(item[2] for item in STATUSES if item[1] == self._status_box.currentData())
        self._header_layout.addWidget(SectionHeader(title, count))

    def _page_for(self, count: int) -> QWidget:
        if count:
            return self._table
        filtered = bool(self._search.text().strip()) or self._project_box.currentData() is not None
        if filtered or self._status_box.currentData() != "open":
            return self._empty_filtered
        return self._empty

    # -- Sélection -------------------------------------------------------

    def _selected_id(self) -> int | None:
        rows = self._table.selectionModel().selectedRows()
        item = self._table.item(rows[0].row(), 0) if rows else None
        return None if item is None else int(item.data(Qt.ItemDataRole.UserRole))

    def _restore_selection(self, task_id: int | None) -> None:
        if task_id is None:
            return
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == task_id:
                self._table.setCurrentCell(row, 1)
                return

    def _update_actions(self) -> None:
        available = self._selected_id() is not None
        self._edit_button.setEnabled(available)
        self._delete_button.setEnabled(available)

    # -- Actions ---------------------------------------------------------

    def _on_add(self) -> None:
        try:
            self.ctx.tasks.add_quick(self._input.text())
        except ValueError as erreur:  # la saisie reste en place pour être corrigée
            self._show_error(str(erreur))
            return
        self._input.clear()
        self._hide_error()
        self.dataChanged.emit()
        self.refresh()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """Case « Fait » cochée ou décochée par l'utilisateur."""
        if self._loading or item.column() != 0:
            return
        done = item.checkState() == Qt.CheckState.Checked
        try:
            self.ctx.tasks.set_done(int(item.data(Qt.ItemDataRole.UserRole)), done)
        except (KeyError, ValueError):
            self._show_error("Cette tâche n'existe plus.")
        else:
            self._hide_error()
            self.dataChanged.emit()
        # Le rafraîchissement détruirait la cellule pendant l'émission de son signal.
        QTimer.singleShot(0, self.refresh)

    def _on_double_clicked(self, index: QModelIndex) -> None:
        if index.column() != 0:  # la première colonne sert à cocher
            self._open_editor()

    def _open_editor(self) -> None:
        task = self._selected_task()
        if task is None:
            return
        dialog = TaskDialog(task, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.ctx.tasks.update(task.id, **dialog.values())
        except KeyError:
            self._vanished()
            return
        except ValueError as erreur:
            QMessageBox.warning(self, "Modification impossible", str(erreur))
            return
        self._hide_error()
        self.dataChanged.emit()
        self.refresh()

    def _on_delete(self) -> None:
        task = self._selected_task()
        if task is None:
            return
        box = self._confirm_box(task.title)
        box.exec()
        clicked = box.clickedButton()
        if clicked is None or box.buttonRole(clicked) != QMessageBox.ButtonRole.DestructiveRole:
            return
        self.ctx.tasks.delete(task.id)
        self._hide_error()
        self.dataChanged.emit()
        self.refresh()

    def _selected_task(self) -> Task | None:
        """Tâche sélectionnée, relue en base ; `None` si elle a disparu."""
        task_id = self._selected_id()
        if task_id is None:
            return None
        try:
            return self.ctx.tasks.get(task_id)
        except KeyError:
            self._vanished()
            return None

    def _vanished(self) -> None:
        self._show_error("Cette tâche n'existe plus.")
        self.refresh()

    # -- Boîtes de dialogue ----------------------------------------------

    def _confirm_box(self, title: str) -> QMessageBox:
        """Confirmation de suppression ; le bouton destructif vaut « oui »."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Supprimer la tâche")
        box.setText(f"Supprimer « {_short(title)} » ?")
        box.setInformativeText("Cette action est définitive.")
        box.addButton("Supprimer", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton("Annuler", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        return box

    def _help_box(self) -> QMessageBox:
        """Rappel de la syntaxe de la saisie rapide."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(HELP_TITLE)
        box.setText("La saisie rapide reconnaît trois raccourcis.")
        box.setInformativeText(HELP_TEXT)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        return box

    def _show_help(self) -> None:
        self._help_box().exec()

    def flush(self) -> None:
        """Appelée à la fermeture : une recherche en attente ne doit pas se réveiller."""
        self._search_timer.stop()

    # -- Messages --------------------------------------------------------

    def _show_error(self, message: str) -> None:
        self._error.setText(message)
        self._error.show()

    def _hide_error(self) -> None:
        self._error.clear()
        self._error.hide()
