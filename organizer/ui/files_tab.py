"""Onglet « Fichiers » : rangement d'un dossier par règles et repérage des doublons.

Les opérations lisent et écrivent le disque et peuvent durer des minutes : elles
tournent donc dans un fil de travail (`_Worker`), rendent compte de leur
avancement et acceptent d'être interrompues. La fenêtre reste répondante, ce qui
évite que l'utilisateur tue l'application en pleine opération. Rien n'est jamais
supprimé, et le dernier rangement reste annulable.
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QScrollArea,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..context import AppContext
from ..dates import format_long
from ..files import (
    DEFAULT_RULES,
    Cancelled,
    FileBatch,
    FileOrganizer,
    PlannedMove,
    Progress,
    Rule,
    save_rules,
)
from .theme import COLORS, MARGIN, SPACING
from .widgets import EmptyState, SectionHeader, card, read_setting, styled_button

# Intervalle minimal entre deux rapports d'avancement : sans lui, un dossier de
# cent mille fichiers noierait la file d'événements.
PROGRESS_MIN_S = 0.1

# Attente maximale du fil de travail à la fermeture de la fenêtre.
WORKER_WAIT_MS = 5000


class _Worker(QThread):
    """Exécute une opération disque hors du fil graphique."""

    stepped = Signal(str, int, int)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, job: Callable[[Progress], object], parent: QWidget | None = None):
        super().__init__(parent)
        self._job = job
        self._last_report = 0.0
        self.progress = Progress(self._report)

    def _report(self, label: str, done: int, total: int) -> None:
        moment = time.monotonic()
        if done != total and moment - self._last_report < PROGRESS_MIN_S:
            return
        self._last_report = moment
        self.stepped.emit(label, done, total)

    def run(self) -> None:
        """Tourne dans le fil de travail ; tout remonte par signal."""
        try:
            self.done.emit(self._job(self.progress))
        except Cancelled:
            self.failed.emit("")  # interruption demandée : ce n'est pas une erreur
        except (OSError, ValueError, sqlite3.Error) as error:
            self.failed.emit(str(error))

# Clé de mémorisation du dossier choisi, dans la table settings.
LAST_FOLDER_KEY = "last_folder"

PLAN_COLUMNS = ("", "Fichier", "Destination", "Règle")
RULE_COLUMNS = ("Nom", "Destination", "Extensions", "Motifs", "Âge min. (jours)", "Activée")

CHECK_COLUMN_WIDTH = 36
BATCH_LIMIT = 10
BATCH_LIST_HEIGHT = 108
# Au-delà, la liste des échecs deviendrait illisible dans une boîte de dialogue.
MAX_FAILURES_SHOWN = 8

NO_DELETE_NOTICE = "Aucune suppression : l'application ne supprime jamais de fichier."
FOLDER_PLACEHOLDER = "Dossier à ranger"

# Pages des deux piles : tableau, état vide avant analyse, état vide après analyse.
_PLAN_PAGE, _PLAN_START_PAGE, _PLAN_DONE_PAGE = 0, 1, 2
_DUP_PAGE, _DUP_START_PAGE, _DUP_NONE_PAGE = 0, 1, 2

_SIZE_UNITS = ("o", "Kio", "Mio", "Gio", "Tio")


def _plural(number: int) -> str:
    """« s » au-delà de un, pour accorder libellés et participes."""
    return "s" if number > 1 else ""


def _kept_heading(count: int) -> str:
    return (
        f"{count} copie{_plural(count)} à vérifier : l'original a peut-être disparu, "
        f"ne supprimez rien avant d'avoir vérifié."
    )


def _default_folder() -> Path:
    """Dossier des téléchargements de l'utilisateur, à défaut son dossier personnel."""
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    if location:
        path = Path(location)
        if path.is_dir():
            return path
    return Path.home()


def _format_size(size: int) -> str:
    """Taille lisible : « 934 o », « 1,2 Mio »."""
    value = float(max(int(size), 0))
    index = 0
    while value >= 1024 and index < len(_SIZE_UNITS) - 1:
        value /= 1024
        index += 1
    if index == 0:
        return f"{int(value)} {_SIZE_UNITS[0]}"
    return f"{value:.1f}".replace(".", ",") + f" {_SIZE_UNITS[index]}"


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _format_stamp(stamp: str) -> str:
    """`mercredi 24 septembre 2026 à 09:12`."""
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:  # horodatage écrit par une version plus ancienne
        return stamp
    return f"{format_long(moment.date())} à {moment:%H:%M}"


def _read_only(text: str) -> QTableWidgetItem:
    """Cellule d'affichage : ni éditable, ni cochable."""
    item = QTableWidgetItem(text)
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    return item


def _known_note(files: FileOrganizer) -> str:
    """Précise où est « Documents » quand Windows l'a déplacé (OneDrive, réseau)."""
    documents = files.known_folders.get("documents")
    if documents is None or documents == files.target_root / "Documents":
        return ""
    return f" ; « Documents » suit Windows : « {documents} »"


def _destination_text(move: PlannedMove, root: Path) -> str:
    """Dossier d'arrivée relatif à la racine, et le nom de repli si la cible était prise."""
    folder = move.dst.parent
    try:
        shown = str(folder.relative_to(root))
    except ValueError:  # hors de la racine de rangement : le chemin entier reste parlant
        shown = str(folder)
    if move.dst.name != move.src.name:
        return f"{shown}  (renommé « {move.dst.name} »)"
    return shown


def _dim(text: str = "") -> QLabel:
    """Libellé secondaire, dans la couleur atténuée du thème."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {COLORS['text_dim']}; background: transparent;")
    return label


class RulesDialog(QDialog):
    """Édition des règles de rangement ; écrit le fichier JSON à l'enregistrement."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Règles de rangement")
        self.setMinimumSize(820, 460)
        self._build()
        self._fill(ctx.files.rules)

    # -- Construction ----------------------------------------------------

    def _build(self) -> None:
        self._table = QTableWidget()
        self._table.setColumnCount(len(RULE_COLUMNS))
        self._table.setHorizontalHeaderLabels(list(RULE_COLUMNS))
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(30)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

        header = self._table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setHighlightSections(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setColumnWidth(0, 160)

        add = styled_button("Ajouter", "ghost")
        add.clicked.connect(self._on_add)
        remove = styled_button("Supprimer", "danger")
        remove.clicked.connect(self._on_remove)
        defaults = styled_button("Restaurer les règles par défaut", "ghost")
        defaults.clicked.connect(self._on_defaults)
        cancel = styled_button("Annuler", "ghost")
        cancel.clicked.connect(self.reject)
        save = styled_button("Enregistrer", "primary")
        save.setDefault(True)
        save.clicked.connect(self._on_save)

        buttons = QHBoxLayout()
        buttons.setSpacing(SPACING)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addWidget(defaults)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(save)

        root = QVBoxLayout(self)
        root.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        root.setSpacing(SPACING)
        root.addWidget(SectionHeader("Règles de rangement"))
        root.addWidget(
            _dim(
                "L'ordre compte : la première règle qui correspond l'emporte. Les destinations sont "
                f"relatives à « {self.ctx.files.target_root} »{_known_note(self.ctx.files)}. "
                "Extensions et motifs se séparent par "
                "des virgules ; un motif accepte « * », comme « *facture* »."
            )
        )
        root.addWidget(self._table, 1)
        root.addLayout(buttons)

    # -- Remplissage -----------------------------------------------------

    def _fill(self, rules: list[Rule]) -> None:
        self._table.setRowCount(0)
        for rule in rules:
            self._add_row(
                rule.name,
                rule.destination,
                ", ".join(rule.extensions),
                ", ".join(rule.patterns),
                str(rule.min_age_days),
                rule.enabled,
            )

    def _add_row(
        self,
        name: str,
        destination: str,
        extensions: str,
        patterns: str,
        min_age: str,
        enabled: bool,
    ) -> int:
        row = self._table.rowCount()
        self._table.insertRow(row)
        for column, text in enumerate((name, destination, extensions, patterns, min_age)):
            self._table.setItem(row, column, QTableWidgetItem(text))
        state = QTableWidgetItem()
        state.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable
        )
        state.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
        state.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(row, 5, state)
        return row

    def _cell(self, row: int, column: int) -> str:
        item = self._table.item(row, column)
        return item.text().strip() if item is not None else ""

    # -- Validation ------------------------------------------------------

    def rules(self) -> list[Rule]:
        """Règles saisies, validées ligne par ligne. Lève `ValueError` au premier défaut."""
        collected: list[Rule] = []
        for row in range(self._table.rowCount()):
            collected.append(self._rule_at(row))
        return collected

    def _rule_at(self, row: int) -> Rule:
        line = row + 1
        name = self._cell(row, 0)
        if not name:
            raise ValueError(f"Ligne {line} : la règle doit avoir un nom.")
        destination = self._cell(row, 1)
        if not destination:
            raise ValueError(f"Ligne {line} : la destination ne peut pas être vide.")
        extensions = [part.strip() for part in self._cell(row, 2).split(",") if part.strip()]
        patterns = [part.strip() for part in self._cell(row, 3).split(",") if part.strip()]
        if not extensions and not patterns:
            raise ValueError(
                f"Ligne {line} : indiquez au moins une extension ou un motif, "
                "sinon la règle ne s'appliquerait à aucun fichier."
            )
        raw_age = self._cell(row, 4) or "0"
        try:
            min_age = int(raw_age)
        except ValueError:
            raise ValueError(
                f"Ligne {line} : l'âge minimum « {raw_age} » doit être un nombre entier de jours."
            ) from None
        if min_age < 0:
            raise ValueError(f"Ligne {line} : l'âge minimum ne peut pas être négatif.")
        state = self._table.item(row, 5)
        enabled = state is None or state.checkState() == Qt.CheckState.Checked
        try:
            # `Rule` refuse une destination absolue ou contenant « .. », et normalise le reste.
            return Rule(
                name=name,
                destination=destination,
                extensions=extensions,
                patterns=patterns,
                min_age_days=min_age,
                enabled=enabled,
            )
        except ValueError as erreur:
            # `Rule` préfixe déjà ses messages du nom de la règle : le numéro de ligne suffit.
            detail = str(erreur).removeprefix(f"Règle « {name} » : ")
            raise ValueError(f"Ligne {line} ({name}) : {detail}") from None

    # -- Actions ---------------------------------------------------------

    def _on_add(self) -> None:
        row = self._add_row("Nouvelle règle", "Documents", "", "", "0", True)
        self._table.setCurrentCell(row, 0)
        self._table.scrollToItem(self._table.item(row, 0))
        self._table.editItem(self._table.item(row, 0))

    def _on_remove(self) -> None:
        rows = sorted({index.row() for index in self._table.selectedIndexes()}, reverse=True)
        if not rows:
            QMessageBox.information(
                self, "Aucune règle choisie", "Sélectionnez d'abord la règle à supprimer."
            )
            return
        for row in rows:
            self._table.removeRow(row)

    def _on_defaults(self) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Restaurer les règles par défaut")
        box.setText("Remplacer les règles affichées par les règles par défaut ?")
        box.setInformativeText("Vos règles ne seront perdues que si vous enregistrez ensuite.")
        confirm = box.addButton("Restaurer", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Annuler", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        if box.clickedButton() is confirm:
            self._fill(DEFAULT_RULES)

    def _on_save(self) -> None:
        try:
            rules = self.rules()
        except ValueError as erreur:
            # Aucune écriture : le fichier garde les règles précédentes.
            QMessageBox.warning(self, "Règle invalide", str(erreur))
            return
        try:
            save_rules(self.ctx.rules_path, rules)
        except OSError as erreur:
            QMessageBox.critical(
                self, "Enregistrement impossible", f"Les règles n'ont pas pu être écrites : {erreur}"
            )
            return
        self.ctx.reload_rules()
        self.accept()


class FilesTab(QWidget):
    """Range un dossier selon les règles, annule un lot et repère les doublons."""

    dataChanged = Signal()

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._plan: list[PlannedMove] = []
        self._batches: list[FileBatch] = []
        # Vrai dès la première analyse : change l'état vide proposé à l'utilisateur.
        self._analysed = False
        # Vrai pendant un remplissage programmatique : les signaux de cases sont à ignorer.
        self._loading = False
        # Vrai pendant une opération longue : les boutons restent désactivés.
        self._working = False
        self._worker: _Worker | None = None
        # Vrai si la dernière opération a été interrompue par l'utilisateur.
        self._cancelled = False

        self._build()
        self.refresh()

    # -- Construction ----------------------------------------------------

    def _build(self) -> None:
        self._rules_button = styled_button("Règles…", "ghost")
        self._rules_button.setToolTip("Modifier les règles de rangement")
        self._rules_button.clicked.connect(self._on_rules)

        top = QHBoxLayout()
        top.setSpacing(SPACING)
        top.addWidget(SectionHeader("Rangement de fichiers"))
        top.addStretch(1)
        top.addWidget(self._rules_button)

        tabs = QTabWidget()
        tabs.addTab(self._build_sorting_page(), "Rangement")
        tabs.addTab(self._build_duplicates_page(), "Doublons")

        root = QVBoxLayout(self)
        root.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        root.setSpacing(SPACING)
        root.addLayout(top)
        root.addWidget(self._build_progress_row())
        root.addWidget(tabs, 1)

        self._buttons = (
            self._rules_button,
            self._browse_button,
            self._analyse_button,
            self._check_all_button,
            self._uncheck_all_button,
            self._apply_button,
            self._undo_button,
            self._duplicates_button,
        )

    def _build_progress_row(self) -> QWidget:
        """Avancement et interruption : visibles seulement pendant une opération."""
        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setRange(0, 0)  # indéterminé tant que le total est inconnu
        self._progress_label = _dim()
        self._cancel_button = styled_button("Interrompre", "danger")
        self._cancel_button.setToolTip("Arrêter l'opération en cours")
        self._cancel_button.clicked.connect(self._on_cancel)

        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING)
        layout.addWidget(self._progress, 1)
        layout.addWidget(self._progress_label)
        layout.addWidget(self._cancel_button)
        self._progress_row = row
        row.setVisible(False)
        return row

    def _build_sorting_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        layout.setSpacing(SPACING)
        layout.addLayout(self._build_folder_row())
        layout.addWidget(
            _dim(
                f"Les fichiers sont déplacés vers « {self.ctx.files.target_root} » selon vos règles"
                f"{_known_note(self.ctx.files)}. Rien n'est écrasé, rien n'est supprimé."
            )
        )
        self._plan_status = _dim()
        layout.addWidget(self._plan_status)
        layout.addLayout(self._build_plan_actions())
        layout.addWidget(self._build_plan_stack(), 1)
        layout.addWidget(self._build_batches_card())
        return page

    def _build_folder_row(self) -> QHBoxLayout:
        self._folder_edit = QLineEdit(self._remembered_folder())
        self._folder_edit.setPlaceholderText(FOLDER_PLACEHOLDER)
        self._folder_edit.setClearButtonEnabled(True)
        self._folder_edit.editingFinished.connect(self._on_folder_edited)

        self._browse_button = styled_button("Parcourir…", "ghost")
        self._browse_button.clicked.connect(self._on_browse)
        self._analyse_button = styled_button("Analyser", "primary")
        self._analyse_button.setToolTip("Calculer les déplacements sans rien modifier")
        self._analyse_button.clicked.connect(self._on_analyse)

        layout = QHBoxLayout()
        layout.setSpacing(SPACING)
        layout.addWidget(QLabel("Dossier"))
        layout.addWidget(self._folder_edit, 1)
        layout.addWidget(self._browse_button)
        layout.addWidget(self._analyse_button)
        return layout

    def _build_plan_actions(self) -> QHBoxLayout:
        self._check_all_button = styled_button("Tout cocher", "ghost")
        self._check_all_button.clicked.connect(lambda: self._set_all_checked(True))
        self._uncheck_all_button = styled_button("Tout décocher", "ghost")
        self._uncheck_all_button.clicked.connect(lambda: self._set_all_checked(False))
        self._selection_label = _dim()

        self._apply_button = styled_button("Ranger la sélection", "primary")
        self._apply_button.clicked.connect(self._on_apply)
        self._undo_button = styled_button("Annuler le dernier rangement", "ghost")
        self._undo_button.clicked.connect(self._on_undo)

        layout = QHBoxLayout()
        layout.setSpacing(SPACING)
        layout.addWidget(self._check_all_button)
        layout.addWidget(self._uncheck_all_button)
        layout.addWidget(self._selection_label, 1)
        layout.addWidget(self._apply_button)
        layout.addWidget(self._undo_button)
        return layout

    def _build_plan_stack(self) -> QStackedWidget:
        self._table = QTableWidget()
        self._table.setColumnCount(len(PLAN_COLUMNS))
        self._table.setHorizontalHeaderLabels(list(PLAN_COLUMNS))
        self._table.setShowGrid(False)
        self._table.setWordWrap(False)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(30)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._table.itemChanged.connect(self._on_item_changed)

        header = self._table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionsClickable(False)
        header.setHighlightSections(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setColumnWidth(0, CHECK_COLUMN_WIDTH)

        self._plan_stack = QStackedWidget()
        self._plan_stack.addWidget(self._table)
        self._plan_stack.addWidget(
            EmptyState("Aucune analyse pour l'instant.", "Choisissez un dossier, puis « Analyser ».")
        )
        self._plan_stack.addWidget(
            EmptyState(
                "Rien à ranger dans ce dossier.",
                "Aucun fichier ne correspond à vos règles ; « Règles… » permet de les ajuster.",
            )
        )
        self._plan_stack.setCurrentIndex(_PLAN_START_PAGE)
        return self._plan_stack

    def _build_batches_card(self) -> QWidget:
        # L'en-tête porte un compteur : il est reconstruit à chaque rafraîchissement.
        self._batches_header = QWidget()
        self._batches_header_box = QVBoxLayout(self._batches_header)
        self._batches_header_box.setContentsMargins(0, 0, 0, 0)

        self._batch_list = QListWidget()
        self._batch_list.setMaximumHeight(BATCH_LIST_HEIGHT)
        self._batch_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._batch_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._batches_empty = _dim("Aucun rangement enregistré pour l'instant.")
        return card(self._batches_header, self._batch_list, self._batches_empty)

    def _build_duplicates_page(self) -> QWidget:
        self._duplicates_button = styled_button("Chercher les doublons", "primary")
        self._duplicates_button.setToolTip("Comparer le contenu des fichiers du dossier choisi")
        self._duplicates_button.clicked.connect(self._on_find_duplicates)
        self._dup_status = _dim()

        top = QHBoxLayout()
        top.setSpacing(SPACING)
        top.addWidget(self._duplicates_button)
        top.addWidget(self._dup_status, 1)

        notice = QLabel(NO_DELETE_NOTICE)
        notice.setWordWrap(True)
        notice.setStyleSheet(f"color: {COLORS['warn']}; background: transparent;")

        container = QWidget()
        self._dup_layout = QVBoxLayout(container)
        self._dup_layout.setContentsMargins(0, 0, 0, 0)
        self._dup_layout.setSpacing(SPACING)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setWidget(container)

        self._dup_stack = QStackedWidget()
        self._dup_stack.addWidget(area)
        self._dup_stack.addWidget(
            EmptyState(
                "Aucune recherche pour l'instant.",
                "La recherche compare le contenu des fichiers du dossier et de ses sous-dossiers.",
            )
        )
        self._dup_stack.addWidget(
            EmptyState("Aucun doublon trouvé.", "Tous les fichiers examinés ont un contenu différent.")
        )
        self._dup_stack.setCurrentIndex(_DUP_START_PAGE)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        layout.setSpacing(SPACING)
        layout.addLayout(top)
        layout.addWidget(notice)
        layout.addWidget(self._dup_stack, 1)
        return page

    # -- Rafraîchissement ------------------------------------------------

    def refresh(self) -> None:
        """Relit les lots de déplacements en base et remet les boutons en accord.

        Le dossier choisi et le plan affiché viennent du disque, pas de la base :
        un rafraîchissement demandé par un autre onglet ne les efface pas.
        """
        self._batches = self.ctx.files.batches(BATCH_LIMIT)
        self._fill_batches()
        self._update_actions()

    def _fill_batches(self) -> None:
        while self._batches_header_box.count():
            item = self._batches_header_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._batches_header_box.addWidget(SectionHeader("Derniers rangements", len(self._batches)))

        self._batch_list.clear()
        for batch in self._batches:
            entry = QListWidgetItem(
                f"{_format_stamp(batch.moved_at)} — {batch.count} fichier{_plural(batch.count)}"
            )
            entry.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._batch_list.addItem(entry)
        self._batch_list.setVisible(bool(self._batches))
        self._batches_empty.setVisible(not self._batches)

    def _fill_plan(self) -> None:
        root = self.ctx.files.target_root
        self._loading = True
        self._table.blockSignals(True)
        try:
            self._table.setRowCount(0)
            self._table.setRowCount(len(self._plan))
            for row, move in enumerate(self._plan):
                check = QTableWidgetItem()
                check.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                check.setCheckState(Qt.CheckState.Checked)
                check.setData(Qt.ItemDataRole.UserRole, row)
                self._table.setItem(row, 0, check)

                source = _read_only(move.src.name)
                source.setToolTip(str(move.src))
                self._table.setItem(row, 1, source)

                destination = _read_only(_destination_text(move, root))
                destination.setToolTip(str(move.dst))
                self._table.setItem(row, 2, destination)

                self._table.setItem(row, 3, _read_only(move.rule))
        finally:
            self._table.blockSignals(False)
            self._loading = False
        self._plan_stack.setCurrentIndex(self._plan_page())
        self._update_actions()

    def _plan_page(self) -> int:
        if self._plan:
            return _PLAN_PAGE
        return _PLAN_DONE_PAGE if self._analysed else _PLAN_START_PAGE

    def _update_actions(self) -> None:
        """Accorde les boutons et le compteur avec le plan et les lots connus."""
        if self._working:
            return
        for button in self._buttons:
            button.setEnabled(True)
        total = len(self._plan)
        checked = len(self._checked_moves())
        self._check_all_button.setEnabled(total > 0)
        self._uncheck_all_button.setEnabled(total > 0)
        self._apply_button.setEnabled(checked > 0)
        self._undo_button.setEnabled(bool(self._batches))
        if total:
            self._selection_label.setText(
                f"{checked} fichier{_plural(checked)} coché{_plural(checked)} sur {total}"
            )
        else:
            self._selection_label.clear()
        if self._batches:
            latest = self._batches[0]
            self._undo_button.setToolTip(
                f"Remettre en place {latest.count} fichier{_plural(latest.count)} "
                f"rangés le {_format_stamp(latest.moved_at)}"
            )
        else:
            self._undo_button.setToolTip("Aucun rangement à annuler")

    # -- Opérations longues ----------------------------------------------

    def busy(self) -> bool:
        """Vrai tant qu'une opération disque tourne."""
        return self._working

    def _start(
        self,
        status: QLabel,
        message: str,
        job: Callable[[Progress], object],
        on_done: Callable[[object], None],
    ) -> None:
        """Lance une opération disque dans un fil de travail.

        Le drapeau est posé avant de désactiver les boutons : l'inverse laissait
        `_update_actions` les rallumer pendant l'opération.
        """
        if self._worker is not None:
            return
        self._working = True
        self._cancelled = False
        for button in self._buttons:
            button.setEnabled(False)
        self._set_info(status, message)
        self._show_progress(message)

        worker = _Worker(job, self)
        self._worker = worker
        worker.stepped.connect(self._on_step)
        worker.done.connect(lambda result: self._on_done(on_done, result))
        worker.failed.connect(lambda reason: self._on_failed(status, reason))
        worker.finished.connect(self._forget_worker)
        worker.start()

    def _show_progress(self, message: str) -> None:
        self._progress.setRange(0, 0)
        self._progress_label.setText(message)
        self._progress_row.setVisible(True)
        self._cancel_button.setEnabled(True)

    def _on_step(self, label: str, done: int, total: int) -> None:
        self._progress_label.setText(label)
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(done)
        else:
            self._progress.setRange(0, 0)

    def _on_done(self, on_done: Callable[[object], None], result: object) -> None:
        self._finish()
        on_done(result)
        self._update_actions()

    def _on_failed(self, status: QLabel, reason: str) -> None:
        self._finish()
        if reason:
            self._set_error(status, reason)
        else:
            self._set_info(status, "Opération interrompue. Rien de plus n'a été touché.")
        self._update_actions()

    def _finish(self) -> None:
        self._working = False
        self._progress_row.setVisible(False)

    def _forget_worker(self) -> None:
        self._worker = None

    def _on_cancel(self) -> None:
        worker = self._worker
        if worker is None:
            return
        self._cancel_button.setEnabled(False)
        self._progress_label.setText("Interruption…")
        self._cancelled = True
        worker.progress.cancel()

    def flush(self) -> None:
        """Fermeture de la fenêtre : on interrompt et on attend le fil de travail."""
        worker = self._worker
        if worker is None:
            return
        self._cancelled = True
        worker.progress.cancel()
        worker.wait(WORKER_WAIT_MS)

    # -- Actions : rangement ---------------------------------------------

    def _on_analyse(self) -> None:
        folder = self._chosen_folder(self._plan_status)
        if folder is None:
            return
        self._start(
            self._plan_status,
            f"Analyse de « {folder} »…",
            lambda progress: self.ctx.files.plan(folder, progress=progress),
            lambda plan: self._plan_ready(folder, plan),
        )

    def _plan_ready(self, folder: Path, plan: object) -> None:
        moves = plan if isinstance(plan, list) else []
        self._plan = moves
        self._analysed = True
        self._fill_plan()
        if moves:
            self._set_info(
                self._plan_status,
                f"{len(moves)} fichier{_plural(len(moves))} à ranger dans « {folder} ».",
            )
        else:
            self._set_info(self._plan_status, f"Rien à ranger dans « {folder} ».")
        # Un fichier dont la destination est inutilisable a été écarté : le taire
        # laisserait l'utilisateur sans moyen de savoir lequel.
        skipped = list(self.ctx.files.last_failures)
        if skipped:
            self._report(
                "Fichiers écartés de l'analyse",
                f"{len(skipped)} fichier{_plural(len(skipped))} ne peut pas être rangé.",
                skipped,
            )

    def _on_apply(self) -> None:
        selection = self._checked_moves()
        if not selection:
            QMessageBox.information(
                self, "Aucun fichier coché", "Cochez au moins un fichier à ranger."
            )
            return
        if not self._confirm_apply(len(selection)):
            return
        self._start(
            self._plan_status,
            "Rangement en cours…",
            lambda progress: self.ctx.files.apply(selection, progress=progress),
            self._applied,
        )

    def _applied(self, batch_id: object) -> None:
        failures = list(self.ctx.files.last_failures)
        # Les fichiers ont bougé : le plan affiché ne décrit plus le disque.
        self._plan = []
        self._analysed = False
        self._fill_plan()
        self.refresh()
        # Le compte vient du journal, pas de la sélection : un rangement
        # interrompu en annonce alors le nombre réel.
        moved = next((batch.count for batch in self._batches if batch.batch_id == batch_id), 0)
        summary = f"{moved} fichier{_plural(moved)} rangé{_plural(moved)}"
        kept = list(self.ctx.files.last_kept_copies)
        self._set_status(self._plan_status, f"{summary}.{self._interrupted_note()}", kept)
        self._report("Rangement terminé", f"{summary}.{self._interrupted_note()}", failures, kept)
        self.dataChanged.emit()

    def _interrupted_note(self) -> str:
        return " Opération interrompue." if self._cancelled else " Relancez l'analyse au besoin."

    def _on_undo(self) -> None:
        if not self._batches:
            return
        latest = self._batches[0]
        if not self._confirm_undo(latest):
            return
        self._start(
            self._plan_status,
            "Annulation en cours…",
            lambda progress: self.ctx.files.undo(latest.batch_id, progress=progress),
            self._undone,
        )

    def _undone(self, restored: object) -> None:
        count = restored if isinstance(restored, int) else 0
        failures = list(self.ctx.files.last_failures)
        summary = f"{count} fichier{_plural(count)} remis en place."
        if self._cancelled:
            summary += " Opération interrompue."
        kept = list(self.ctx.files.last_kept_copies)
        self._set_status(self._plan_status, summary, kept)
        self._report("Annulation terminée", summary, failures, kept)
        self.refresh()
        self.dataChanged.emit()

    def _confirm_apply(self, count: int) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Ranger les fichiers")
        box.setText(f"Déplacer {count} fichier{_plural(count)} ?")
        box.setInformativeText(
            "Les fichiers rejoignent leur dossier de destination. Aucun fichier n'est écrasé "
            "ni supprimé, et ce rangement restera annulable."
        )
        confirm = box.addButton("Ranger", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Annuler", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is confirm

    def _confirm_undo(self, batch: FileBatch) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Annuler le dernier rangement")
        box.setText(
            f"Remettre en place {batch.count} fichier{_plural(batch.count)} "
            f"rangés le {_format_stamp(batch.moved_at)} ?"
        )
        confirm = box.addButton("Remettre en place", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Annuler", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is confirm

    def _set_status(self, status: QLabel, summary: str, kept: list[Path]) -> None:
        """Ligne d'état après une opération ; en erreur s'il reste des copies à vérifier."""
        if kept:
            self._set_error(status, f"{summary} {_kept_heading(len(kept))}")
        else:
            self._set_info(status, summary)

    def _report(
        self,
        title: str,
        message: str,
        failures: list[tuple[Path, str]],
        kept: list[Path] | None = None,
    ) -> None:
        """Compte rendu d'une opération, échecs détaillés compris.

        Les copies à vérifier viennent en tête, chemin complet, et ne sont jamais
        tronquées : l'original a peut-être disparu, elles sont alors les seules
        qui restent. Mêlées aux échecs ordinaires, un « … et N autres » pourrait
        les cacher alors que l'utilisateur croit ses fichiers restés en place.
        """
        box = QMessageBox(self)
        box.setWindowTitle(title)
        if failures:
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText(f"{message} {len(failures)} échec{_plural(len(failures))}.")
            lines: list[str] = []
            if kept:
                lines.append(_kept_heading(len(kept)))
                lines += [f"• {path}" for path in kept]
                lines.append("")
            lines += [f"• {path.name} : {reason}" for path, reason in failures[:MAX_FAILURES_SHOWN]]
            remaining = len(failures) - min(len(failures), MAX_FAILURES_SHOWN)
            if remaining > 0:
                lines.append(f"… et {remaining} autre{_plural(remaining)}.")
            box.setInformativeText("\n".join(lines))
        else:
            box.setIcon(QMessageBox.Icon.Information)
            box.setText(message)
        box.addButton("Fermer", QMessageBox.ButtonRole.AcceptRole)
        box.exec()

    # -- Actions : doublons ----------------------------------------------

    def _on_find_duplicates(self) -> None:
        folder = self._chosen_folder(self._dup_status)
        if folder is None:
            return
        self._start(
            self._dup_status,
            f"Recherche des doublons dans « {folder} »…",
            lambda progress: self.ctx.files.find_duplicates(folder, progress=progress),
            lambda groups: self._duplicates_ready(folder, groups),
        )

    def _duplicates_ready(self, folder: Path, groups: object) -> None:
        found = groups if isinstance(groups, list) else []
        self._fill_duplicates(found)
        total = sum(len(group) for group in found)
        if found:
            self._set_info(
                self._dup_status,
                f"{len(found)} groupe{_plural(len(found))} de doublons, "
                f"{total} fichier{_plural(total)} au total.",
            )
        else:
            self._set_info(self._dup_status, f"Aucun doublon dans « {folder} ».")

    def _fill_duplicates(self, groups: list[list[Path]]) -> None:
        while self._dup_layout.count():
            item = self._dup_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for index, group in enumerate(groups, start=1):
            self._dup_layout.addWidget(self._group_card(index, group))
        self._dup_layout.addStretch(1)
        self._dup_stack.setCurrentIndex(_DUP_PAGE if groups else _DUP_NONE_PAGE)

    def _group_card(self, index: int, group: list[Path]) -> QWidget:
        """Une carte par groupe : les chemins, la taille, et l'accès au dossier."""
        detail = _dim(
            f"{len(group)} fichiers au contenu identique · {_format_size(_file_size(group[0]))} chacun"
        )

        paths = QLabel("\n".join(str(path) for path in group))
        paths.setWordWrap(True)
        paths.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        paths.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")

        folder = group[0].parent
        open_button = styled_button("Ouvrir le dossier", "ghost")
        open_button.setToolTip(f"Ouvrir « {folder} » dans l'explorateur")
        open_button.clicked.connect(lambda _checked=False, target=folder: self._open_folder(target))

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addStretch(1)
        row_layout.addWidget(open_button)

        return card(SectionHeader(f"Groupe {index}", len(group)), detail, paths, row)

    def _open_folder(self, folder: Path) -> None:
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.warning(
                self, "Ouverture impossible", f"Le dossier « {folder} » n'a pas pu être ouvert."
            )

    # -- Actions : règles et dossier -------------------------------------

    def _on_rules(self) -> None:
        dialog = RulesDialog(self.ctx, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        # Le plan avait été calculé avec les règles précédentes.
        self._plan = []
        self._analysed = False
        self._fill_plan()
        self._set_info(self._plan_status, "Règles enregistrées. Relancez l'analyse pour en tenir compte.")

    def _on_browse(self) -> None:
        start = self._folder_edit.text().strip() or str(_default_folder())
        chosen = QFileDialog.getExistingDirectory(self, "Choisir un dossier", start)
        if not chosen:
            return
        self._folder_edit.setText(chosen)
        self._remember_folder(chosen)

    def _on_folder_edited(self) -> None:
        self._remember_folder(self._folder_edit.text())

    def _remembered_folder(self) -> str:
        stored = read_setting(self.ctx.settings, LAST_FOLDER_KEY, None)
        if isinstance(stored, str) and stored.strip():
            return stored
        return str(_default_folder())

    def _remember_folder(self, folder: str | Path) -> None:
        text = str(folder).strip()
        if text:
            self.ctx.settings.set(LAST_FOLDER_KEY, text)

    def _chosen_folder(self, status: QLabel) -> Path | None:
        """Dossier saisi et vérifié ; affiche l'erreur et renvoie `None` sinon."""
        raw = self._folder_edit.text().strip()
        if not raw:
            self._set_error(status, "Choisissez d'abord un dossier.")
            return None
        # Absolu dès la saisie : « \Users\Jean\Downloads » dépend sinon du lecteur
        # courant, qui change selon la façon dont l'application est lancée.
        folder = Path(os.path.abspath(Path(raw).expanduser()))
        if not folder.is_dir():
            self._set_error(status, f"Le dossier « {folder} » est introuvable.")
            return None
        if str(folder) != raw:
            self._folder_edit.setText(str(folder))
        self._remember_folder(folder)
        return folder

    # -- Sélection du plan -----------------------------------------------

    def _checked_moves(self) -> list[PlannedMove]:
        """Déplacements cochés, dans l'ordre du tableau."""
        selection: list[PlannedMove] = []
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item is None or item.checkState() != Qt.CheckState.Checked:
                continue
            index = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(index, int) and 0 <= index < len(self._plan):
                selection.append(self._plan[index])
        return selection

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self._loading = True
        self._table.blockSignals(True)
        try:
            for row in range(self._table.rowCount()):
                item = self._table.item(row, 0)
                if item is not None:
                    item.setCheckState(state)
        finally:
            self._table.blockSignals(False)
            self._loading = False
        self._update_actions()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 0:
            return
        self._update_actions()

    # -- Messages d'état -------------------------------------------------

    def _set_info(self, label: QLabel, text: str) -> None:
        label.setStyleSheet(f"color: {COLORS['text_dim']}; background: transparent;")
        label.setText(text)

    def _set_error(self, label: QLabel, text: str) -> None:
        label.setStyleSheet(f"color: {COLORS['danger']}; background: transparent;")
        label.setText(text)
