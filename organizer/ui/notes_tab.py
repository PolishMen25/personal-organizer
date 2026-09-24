"""Onglet « Notes » : liste filtrable à gauche, éditeur Markdown à droite.

L'enregistrement est automatique : un minuteur monocoup écrit la note après une
courte pause dans la frappe. Changer de note ou fermer la fenêtre écrit tout de
suite, et `refresh()` ne touche jamais aux champs d'une saisie en cours.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QKeySequence,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..context import AppContext
from ..dates import format_long
from ..notes import DEFAULT_TITLE, Note
from .theme import COLORS, MARGIN, SPACING
from .widgets import EmptyState, SectionHeader, styled_button

SAVE_DELAY_MS = 800

# Temporisation de la recherche : sans elle, chaque touche relisait toutes les
# notes et reconstruisait la liste, ce qui hachait la frappe.
SEARCH_DELAY_MS = 250
PREVIEW_WIDTH = 90
PIN_MARK = "★"
ROW_MIN_HEIGHT = 46
LIST_MIN_WIDTH = 240
EDITOR_MIN_WIDTH = 380

SEARCH_PLACEHOLDER = "Rechercher un titre ou un mot…"
TITLE_PLACEHOLDER = "Titre de la note"
BODY_PLACEHOLDER = "Écrivez ici. Le Markdown est conservé tel quel."
EMPTY_PREVIEW = "Note vide"

STATE_PENDING = "Modification en attente…"
STATE_SAVED = "Enregistré"
STATE_GONE = "Cette note n'existe plus."

# Pages des deux piles : liste à gauche, éditeur à droite.
_LIST_PAGE, _EMPTY_ALL_PAGE, _EMPTY_SEARCH_PAGE = 0, 1, 2
_EDITOR_PAGE, _NO_SELECTION_PAGE = 0, 1


def _mono_font(base: QFont) -> QFont:
    """Police à chasse fixe du système, à la taille du thème."""
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setStyleHint(QFont.StyleHint.Monospace)
    if base.pointSize() > 0:
        font.setPointSize(base.pointSize())
    return font


def _format_stamp(stamp: str) -> str:
    """`Modifiée le mercredi 24 septembre 2026 à 09:12`."""
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:  # horodatage écrit par une version plus ancienne
        return f"Modifiée le {stamp}"
    return f"Modifiée le {format_long(moment.date())} à {moment:%H:%M}"


class _EllipsisLabel(QLabel):
    """Libellé d'une ligne qui coupe son texte par une ellipse quand la place manque."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = text
        # Sans « Ignored », la largeur du texte imposerait celle de la liste entière.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        self._elide()

    def set_full_text(self, text: str) -> None:
        self._full = text
        self._elide()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        width = max(self.width(), 32)
        self.setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, width))


class NotesTab(QWidget):
    """Notes Markdown : recherche, épinglage et enregistrement automatique."""

    dataChanged = Signal()

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._notes: list[Note] = []
        self._current_id: int | None = None
        self._pinned = False
        # Dernier contenu connu de la base pour la note affichée ; None tant qu'elle
        # n'est pas chargée dans les champs, ce qui interdit tout enregistrement.
        self._saved: tuple[str, str] | None = None
        # Vrai pendant un remplissage programmatique : les signaux sont à ignorer.
        self._filling = False

        self._build()
        self.refresh()

    # -- Construction ----------------------------------------------------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        root.setSpacing(SPACING)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_list_panel())
        splitter.addWidget(self._build_editor_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 760])
        root.addWidget(splitter, 1)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self._save_now)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DELAY_MS)
        self._search_timer.timeout.connect(self.refresh)

        self._build_shortcuts()

    def _build_list_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(LIST_MIN_WIDTH)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, SPACING, 0)
        layout.setSpacing(SPACING)

        # L'en-tête porte un compteur : il est reconstruit à chaque rafraîchissement.
        self._header_box = QVBoxLayout()
        self._header_box.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._header_box)

        self._search = QLineEdit()
        self._search.setPlaceholderText(SEARCH_PLACEHOLDER)
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search)
        layout.addWidget(self._search)

        self._new_button = styled_button("Nouvelle note", "primary")
        self._new_button.setToolTip("Créer une note (Ctrl+N)")
        self._new_button.clicked.connect(self._on_new_note)
        layout.addWidget(self._new_button)

        self._list = QListWidget()
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setUniformItemSizes(False)
        self._list.setSpacing(2)
        self._list.currentRowChanged.connect(self._on_row_changed)

        self._left = QStackedWidget()
        self._left.addWidget(self._list)
        self._left.addWidget(
            EmptyState("Aucune note.", "Le bouton ci-dessus crée votre première note.")
        )
        self._left.addWidget(
            EmptyState("Aucune note ne correspond.", "Essayez un autre mot ou effacez la recherche.")
        )
        layout.addWidget(self._left, 1)
        return panel

    def _build_editor_panel(self) -> QWidget:
        editor = QWidget()
        layout = QVBoxLayout(editor)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING)

        self._title = QLineEdit()
        self._title.setPlaceholderText(TITLE_PLACEHOLDER)
        title_font = QFont(self._title.font())
        title_font.setBold(True)
        if title_font.pointSize() > 0:
            title_font.setPointSize(title_font.pointSize() + 2)
        self._title.setFont(title_font)
        self._title.textChanged.connect(self._on_edited)
        layout.addWidget(self._title)

        self._body = QPlainTextEdit()
        self._body.setPlaceholderText(BODY_PLACEHOLDER)
        body_font = _mono_font(self._body.font())
        self._body.setFont(body_font)
        self._body.setTabStopDistance(4 * QFontMetricsF(body_font).horizontalAdvance(" "))
        self._body.textChanged.connect(self._on_edited)
        layout.addWidget(self._body, 1)

        self._meta = QLabel()
        self._meta.setStyleSheet(f"color: {COLORS['text_dim']}; background: transparent;")
        self._state = QLabel()
        self._state.setStyleSheet(f"color: {COLORS['text_dim']}; background: transparent;")

        self._pin = styled_button("Épingler", "ghost")
        self._pin.clicked.connect(self._on_toggle_pin)
        self._delete = styled_button("Supprimer", "danger")
        self._delete.clicked.connect(self._on_delete)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(SPACING)
        footer.addWidget(self._meta)
        footer.addStretch(1)
        footer.addWidget(self._state)
        footer.addWidget(self._pin)
        footer.addWidget(self._delete)
        layout.addLayout(footer)

        panel = QStackedWidget()
        panel.setMinimumWidth(EDITOR_MIN_WIDTH)
        panel.addWidget(editor)
        panel.addWidget(
            EmptyState("Aucune note sélectionnée.", "Choisissez une note à gauche ou créez-en une.")
        )
        self._right = panel
        return panel

    def _build_shortcuts(self) -> None:
        raccourcis = (
            ("Ctrl+N", self._on_new_note),
            ("Ctrl+S", self.flush),
            ("Ctrl+F", self._focus_search),
        )
        for keys, slot in raccourcis:
            shortcut = QShortcut(QKeySequence(keys), self)
            # Sans ce contexte, le raccourci agirait depuis n'importe quel onglet.
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)

    # -- Rafraîchissement ------------------------------------------------

    def refresh(self) -> None:
        """Relit les notes, redessine la liste et remet l'éditeur en accord avec la base."""
        self._notes = self.ctx.notes.list(self._search.text())
        self._set_header(len(self._notes))
        self._fill_list()
        self._load_editor()

    def _set_header(self, count: int) -> None:
        while self._header_box.count():
            item = self._header_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._header_box.addWidget(SectionHeader("Notes", count))

    def _fill_list(self) -> None:
        self._filling = True
        self._list.blockSignals(True)
        try:
            self._list.clear()
            current: QListWidgetItem | None = None
            for note in self._notes:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, note.id)
                item.setToolTip(note.title)
                row = self._row(note)
                item.setSizeHint(QSize(1, max(row.sizeHint().height(), ROW_MIN_HEIGHT)))
                self._list.addItem(item)
                self._list.setItemWidget(item, row)
                if note.id == self._current_id:
                    current = item
            self._list.setCurrentItem(current)
        finally:
            self._list.blockSignals(False)
            self._filling = False
        if current is not None:
            self._list.scrollToItem(current)
        self._left.setCurrentIndex(self._left_page())

    def _left_page(self) -> int:
        if self._notes:
            return _LIST_PAGE
        return _EMPTY_SEARCH_PAGE if self._search.text().strip() else _EMPTY_ALL_PAGE

    def _row(self, note: Note) -> QWidget:
        """Ligne de liste : repère d'épinglage, titre et aperçu."""
        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        if note.pinned:
            mark = QLabel(PIN_MARK)
            mark.setToolTip("Note épinglée")
            mark.setStyleSheet(f"color: {COLORS['warn']}; background: transparent;")
            head.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)

        title = _EllipsisLabel(note.title or DEFAULT_TITLE)
        font = QFont(title.font())
        font.setBold(True)
        title.setFont(font)
        title.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")
        head.addWidget(title, 1)
        layout.addLayout(head)

        preview = _EllipsisLabel(note.preview(PREVIEW_WIDTH) or EMPTY_PREVIEW)
        small = QFont(preview.font())
        if small.pointSize() > 0:
            small.setPointSize(max(small.pointSize() - 1, 6))
        preview.setFont(small)
        preview.setStyleSheet(f"color: {COLORS['text_dim']}; background: transparent;")
        layout.addWidget(preview)
        return row

    def _load_editor(self) -> None:
        """Met le panneau de droite en accord avec la note courante."""
        note = self._current_note()
        if note is None:
            self._current_id = None
            self._saved = None
            self._right.setCurrentIndex(_NO_SELECTION_PAGE)
            return
        self._pinned = note.pinned
        self._pin.setText("Détacher" if note.pinned else "Épingler")
        self._pin.setToolTip("Retirer des notes épinglées" if note.pinned else "Garder en haut de liste")
        self._meta.setText(_format_stamp(note.updated_at))
        self._sync_fields(note)
        self._right.setCurrentIndex(_EDITOR_PAGE)

    def _sync_fields(self, note: Note) -> None:
        """Recopie la note dans les champs, sauf si une saisie attend d'être enregistrée."""
        if self._saved is None:  # première ouverture de cette note
            self._set_fields(note.title, note.body)
            self._state.clear()
            return
        if self._dirty():
            return
        # Un titre vide suit le corps : `NoteStore` en dérive un, inutile de le recopier.
        title = self._title.text()
        if note.body == self._body.toPlainText() and (not title or note.title == title):
            return
        self._set_fields(note.title, note.body, keep_cursor=True)

    def _set_fields(self, title: str, body: str, keep_cursor: bool = False) -> None:
        """Remplit titre et corps sans réveiller l'enregistrement automatique."""
        position = self._body.textCursor().position() if keep_cursor else 0
        self._filling = True
        self._title.blockSignals(True)
        self._body.blockSignals(True)
        try:
            self._title.setText(title)
            self._body.setPlainText(body)
            cursor = self._body.textCursor()
            cursor.setPosition(min(position, len(self._body.toPlainText())))
            self._body.setTextCursor(cursor)
        finally:
            self._body.blockSignals(False)
            self._title.blockSignals(False)
            self._filling = False
        self._saved = (self._title.text(), self._body.toPlainText())

    # -- Enregistrement --------------------------------------------------

    def flush(self) -> None:
        """Écrit sans attendre la note affichée ; appelée à la fermeture de la fenêtre."""
        # Une recherche en attente ne doit pas se réveiller après la fermeture.
        self._search_timer.stop()
        self._save_now()

    def _dirty(self) -> bool:
        if self._saved is None:
            return False
        return (self._title.text(), self._body.toPlainText()) != self._saved

    def _save_now(self, refresh_list: bool = True) -> bool:
        """Enregistre la note affichée si son contenu a changé."""
        self._save_timer.stop()
        if self._current_id is None or not self._dirty():
            return False
        title, body = self._title.text(), self._body.toPlainText()
        try:
            note = self.ctx.notes.update(self._current_id, title=title, body=body)
        except KeyError:  # supprimée depuis un autre onglet
            self._forget_current()
            self._state.setText(STATE_GONE)
            self.refresh()
            return False
        except ValueError as erreur:
            self._state.setText(str(erreur))
            return False
        self._saved = (title, body)
        self._meta.setText(_format_stamp(note.updated_at))
        self._state.setText(STATE_SAVED)
        if refresh_list:  # le titre et l'ordre de la liste dépendent de ce qui vient d'être écrit
            self.refresh()
        self.dataChanged.emit()
        return True

    def _on_edited(self) -> None:
        if self._filling or self._current_id is None:
            return
        self._state.setText(STATE_PENDING if self._dirty() else STATE_SAVED)
        self._save_timer.start()

    # -- Actions ---------------------------------------------------------

    def _on_search(self, _texte: str) -> None:
        # Une frappe rapide n'arme qu'une seule relecture, à son terme.
        self._search_timer.start()

    def _focus_search(self) -> None:
        self._search.setFocus()
        self._search.selectAll()

    def _on_row_changed(self, row: int) -> None:
        if self._filling:
            return
        item = self._list.item(row)
        note_id = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if note_id is None or note_id == self._current_id:
            return
        self._select(note_id)

    def _select(self, note_id: int | None) -> None:
        """Change de note : la précédente est écrite tout de suite."""
        self._save_now(refresh_list=False)
        self._current_id = note_id
        self._saved = None
        self._load_editor()
        # La liste est reconstruite hors du signal en cours : ses lignes, dont celle
        # qui vient d'être cliquée, seraient détruites pendant leur propre événement.
        QTimer.singleShot(0, self.refresh)

    def _on_new_note(self) -> None:
        self._save_now(refresh_list=False)
        note = self.ctx.notes.add()
        self._search.clear()  # sinon le filtre masquerait la note qui vient de naître
        self._current_id = note.id
        self._saved = None
        self._load_editor()
        self.refresh()
        self._title.setFocus()
        self._title.selectAll()
        self.dataChanged.emit()

    def _on_toggle_pin(self) -> None:
        if self._current_id is None:
            return
        try:
            self.ctx.notes.set_pinned(self._current_id, not self._pinned)
        except KeyError:
            self._forget_current()
            self._state.setText(STATE_GONE)
            self.refresh()
            return
        self.refresh()
        self.dataChanged.emit()

    def _on_delete(self) -> None:
        note = self._current_note()
        if note is None:
            return
        if not self._confirm_delete(note):
            return
        index = next((i for i, item in enumerate(self._notes) if item.id == note.id), 0)
        self._forget_current()  # les modifications en attente partent avec la note
        self.ctx.notes.delete(note.id)
        self.refresh()
        if self._notes:  # l'attention se porte sur la note voisine
            self._select(self._notes[min(index, len(self._notes) - 1)].id)
        self.dataChanged.emit()

    def _confirm_delete(self, note: Note) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Supprimer la note")
        box.setText(f"Supprimer « {note.title} » ?")
        box.setInformativeText("Cette suppression est définitive.")
        confirm = box.addButton("Supprimer", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton("Annuler", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is confirm

    # -- Utilitaires -----------------------------------------------------

    def _current_note(self) -> Note | None:
        if self._current_id is None:
            return None
        try:
            return self.ctx.notes.get(self._current_id)
        except KeyError:
            return None

    def _forget_current(self) -> None:
        """Oublie la note affichée et annule tout enregistrement en attente."""
        self._save_timer.stop()
        self._current_id = None
        self._saved = None
