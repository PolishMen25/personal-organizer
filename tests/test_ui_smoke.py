"""Fumée de l'interface : la fenêtre complète, pilotée comme le ferait un utilisateur.

Les boîtes modales (confirmations, comptes rendus) sont court-circuitées : sans
personne pour cliquer, leur `exec()` bloquerait la suite du test.
"""

from __future__ import annotations

import os
import time
from datetime import date
from inspect import signature
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QCheckBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from organizer import app as module_app
from organizer import files as module_files
from organizer.context import AppContext
from organizer.tasks import PRIORITY_HIGH
from organizer.timer import Phase
from organizer.ui.files_tab import FOLDER_PLACEHOLDER, LAST_FOLDER_KEY, FilesTab
from organizer.ui import files_tab as module_files_tab
from organizer.ui import focus_tab as module_focus
from organizer.ui.focus_tab import FocusTab
from organizer.ui.main_window import MainWindow
from organizer.ui.notes_tab import (
    SAVE_DELAY_MS,
    SEARCH_PLACEHOLDER,
    TITLE_PLACEHOLDER,
    NotesTab,
)
from organizer.ui.tasks_tab import TasksTab
from organizer.ui.theme import COLORS
from organizer.ui.today_tab import EMPTY_HINT as TODAY_EMPTY_HINT
from organizer.ui.today_tab import TodayTab
from organizer.ui.widgets import EmptyState, SectionHeader, card, styled_button

ONGLETS = (TodayTab, TasksTab, NotesTab, FocusTab, FilesTab)

# Clés de couleur que tout onglet peut exiger du thème.
CLES_COULEURS = {
    "bg",
    "surface",
    "surface_alt",
    "border",
    "text",
    "text_dim",
    "accent",
    "accent_dim",
    "danger",
    "warn",
    "ok",
}

# Extensions couvertes par les règles par défaut, plus un intrus sans règle.
FAUX_FICHIERS = ("rapport.pdf", "photo.jpg", "notes.txt", "facture-mars.pdf", "sans-regle.xyz")


@pytest.fixture()
def window(qapp, ctx: AppContext):
    """Fenêtre principale bâtie sur le contexte jetable."""
    fenetre = MainWindow(ctx)
    yield fenetre
    fenetre.close()


def _onglet(window: MainWindow, classe: type[QWidget]) -> QWidget:
    """Onglet de la classe demandée, tel que la fenêtre l'a construit."""
    tab = window.findChild(classe)
    assert tab is not None, f"onglet {classe.__name__} absent de la fenêtre"
    return tab


def _bouton(parent: QWidget, libelle: str) -> QPushButton:
    """Bouton repéré par son libellé, comme l'utilisateur le lit à l'écran."""
    for button in parent.findChildren(QPushButton):
        if button.text() == libelle:
            return button
    raise AssertionError(f"bouton « {libelle} » introuvable dans {type(parent).__name__}")


def _champ(parent: QWidget, placeholder: str) -> QLineEdit:
    """Champ de saisie repéré par son texte d'invite."""
    for edit in parent.findChildren(QLineEdit):
        if edit.placeholderText() == placeholder:
            return edit
    raise AssertionError(f"champ « {placeholder} » introuvable dans {type(parent).__name__}")


def _cliquer(button: QPushButton) -> None:
    assert button.isEnabled(), f"le bouton « {button.text()} » est désactivé"
    button.click()


def _saisie_rapide(window: MainWindow, texte: str) -> None:
    """Remplit la saisie rapide de l'onglet « Aujourd'hui » et valide par Entrée."""
    champ = _onglet(window, TodayTab).findChild(QLineEdit)
    champ.setFocus()
    # `QTest.keyClicks` ne sait pas taper les accents : le texte est posé d'un coup.
    champ.setText(texte)
    QTest.keyClick(champ, Qt.Key.Key_Return)


def _dossier_de_test(racine: Path) -> Path:
    """Dossier rempli de faux fichiers, dont un que les règles ignorent."""
    dossier = racine / "À ranger"
    dossier.mkdir()
    for nom in FAUX_FICHIERS:
        (dossier / nom).write_text(f"contenu de {nom}\n", encoding="utf-8")
    return dossier


def _minuteur_d_une_minute(window: MainWindow, ctx: AppContext) -> FocusTab:
    """Ramène la concentration à une minute pour qu'elle tienne en 60 ticks."""
    focus = _onglet(window, FocusTab)
    ctx.settings.set("pomodoro_focus_min", 1)
    focus.refresh()
    assert focus.timer.config.focus_min == 1
    _horloge_factice(focus)
    return focus


# Horloges factices des onglets « Concentration », par instance.
_HORLOGES: dict[int, list[float]] = {}


def _horloge_factice(focus: FocusTab) -> None:
    """Remplace l'horloge du minuteur : le temps n'avance que sur demande.

    Le temps de concentration se lit à l'horloge murale (constat 11), donc un
    test qui appelle `_on_tick()` en boucle doit faire avancer le temps.
    """
    horloge = [0.0]
    _HORLOGES[id(focus)] = horloge
    focus._now = lambda: horloge[0]


def _battements(focus: FocusTab, secondes: int) -> None:
    """`secondes` battements d'une seconde, horloge factice et minuteur d'accord."""
    horloge = _HORLOGES[id(focus)]
    for _ in range(secondes):
        horloge[0] += 1.0
        focus._on_tick()


def _attendre_fichiers(files: FilesTab, pump, limite_ms: int = 10_000) -> None:
    """Attend la fin de l'opération disque, qui tourne hors du fil graphique.

    Les opérations de l'onglet « Fichiers » sont déportées dans un fil de travail
    (constats 6 et 10) : un test doit donc lui laisser rendre la main.
    """
    fin = time.monotonic() + limite_ms / 1000
    while files.busy() and time.monotonic() < fin:
        QTest.qWait(5)
        pump()
    assert not files.busy(), "l'opération disque n'a pas rendu la main"
    pump()


def _repondre_oui(monkeypatch: pytest.MonkeyPatch, files: FilesTab) -> None:
    """Remplace les trois boîtes modales du rangement par un « oui » silencieux."""
    monkeypatch.setattr(files, "_confirm_apply", lambda count: True)
    monkeypatch.setattr(files, "_confirm_undo", lambda batch: True)
    monkeypatch.setattr(files, "_report", lambda *args, **kwargs: None)


def _compter(ctx: AppContext, requete: str) -> int:
    return int(ctx.conn.execute(requete).fetchone()[0])


# -- Assemblage -----------------------------------------------------------


def test_la_fenetre_expose_les_cinq_onglets(window: MainWindow) -> None:
    onglets = [_onglet(window, classe) for classe in ONGLETS]
    assert len(onglets) == 5
    for tab in onglets:
        assert hasattr(tab, "dataChanged"), f"{type(tab).__name__} n'expose pas dataChanged"
        tab.refresh()  # relecture complète : aucune exception ne doit remonter


def _libelles_rognes(racine: QWidget) -> list[str]:
    """Libellés à retour à la ligne qui reçoivent moins de hauteur qu'il ne leur en faut."""
    return [
        label.text()[:40]
        for label in racine.findChildren(QLabel)
        if label.isVisible() and label.wordWrap() and label.height() < label.heightForWidth(label.width())
    ]


@pytest.mark.parametrize("largeur", [420, 800, 1400])
def test_etat_vide_jamais_rogne(qapp, pump, largeur) -> None:
    """Constat (capture sous Windows) : sous « Rien d'urgent. », le texte d'aide
    était coupé. L'alignement du layout ramenait chaque libellé à sa largeur
    préférée, où un QLabel à retour à la ligne n'obtient pas sa hauteur."""
    hote = QWidget()
    layout = QVBoxLayout(hote)
    layout.addWidget(EmptyState("Rien d'urgent.", TODAY_EMPTY_HINT))
    layout.addStretch(1)
    hote.resize(largeur, 600)
    hote.show()
    pump()
    try:
        assert _libelles_rognes(hote) == []
    finally:
        hote.close()


def test_onglet_aujourd_hui_vide_lisible_en_entier(window: MainWindow, pump) -> None:
    window.resize(1050, 780)
    window.show()
    pump()
    assert _libelles_rognes(_onglet(window, TodayTab)) == []


def test_le_contrat_d_interface_est_respecte(qapp) -> None:
    """Palette, briques réutilisables et signature commune aux cinq onglets."""
    assert CLES_COULEURS <= set(COLORS)
    assert SectionHeader("Titre", 3) is not None
    assert EmptyState("Rien ici", "Un indice") is not None
    assert card(EmptyState("Vide")) is not None
    for kind in ("primary", "ghost", "danger"):
        assert styled_button("Agir", kind).property("kind") == kind
    with pytest.raises(ValueError):
        styled_button("Agir", "inconnu")

    for classe in ONGLETS:
        parametres = list(signature(classe.__init__).parameters)
        assert parametres[:3] == ["self", "ctx", "parent"], classe.__name__
        assert isinstance(classe.dataChanged, Signal), classe.__name__
        assert callable(classe.refresh), classe.__name__


def test_refresh_est_idempotent(window: MainWindow, pump) -> None:
    for _ in range(3):
        for classe in ONGLETS:
            _onglet(window, classe).refresh()
        pump()


# -- Tâches ---------------------------------------------------------------


def test_saisie_rapide_puis_case_cochee(window: MainWindow, ctx: AppContext, pump) -> None:
    _saisie_rapide(window, "Appeler la banque aujourd'hui !1 #admin")
    pump()

    taches = ctx.tasks.list("all")
    assert len(taches) == 1
    tache = taches[0]
    assert tache.title == "Appeler la banque"
    assert tache.priority == PRIORITY_HIGH
    assert tache.project == "admin"
    assert tache.due_date == date.today()
    assert tache.done is False

    cases = _onglet(window, TodayTab).findChildren(QCheckBox)
    assert len(cases) == 1, "la tâche du jour devrait offrir une seule case à cocher"
    cases[0].setChecked(True)
    pump()

    relue = ctx.tasks.get(tache.id)
    assert relue.done is True
    assert relue.completed_at is not None
    # L'onglet « Tâches » a suivi, sans être celui qui a écrit.
    assert ctx.tasks.list("open") == []


def test_la_tache_cochee_depuis_le_tableau_est_enregistree(
    window: MainWindow, ctx: AppContext, pump
) -> None:
    tache = ctx.tasks.add("Relire le contrat")
    tasks = _onglet(window, TasksTab)
    tasks.refresh()

    case = tasks._table.item(0, 0)
    assert case is not None
    case.setCheckState(Qt.CheckState.Checked)
    pump()

    assert ctx.tasks.get(tache.id).done is True


# -- Notes ----------------------------------------------------------------


def test_note_creee_puis_enregistree_automatiquement(
    window: MainWindow, ctx: AppContext, pump
) -> None:
    notes = _onglet(window, NotesTab)
    _cliquer(_bouton(notes, "Nouvelle note"))
    pump()

    assert len(ctx.notes.list()) == 1
    creee = ctx.notes.list()[0]

    titre = _champ(notes, TITLE_PLACEHOLDER)
    corps = notes.findChild(QPlainTextEdit)
    titre.setText("Compte rendu")
    corps.setPlainText("Point un\nPoint deux")
    assert notes._save_timer.isActive(), "la frappe devrait armer l'enregistrement différé"
    assert notes._save_timer.interval() == SAVE_DELAY_MS

    # Le délai réel est ramené à l'immédiat : la suite n'attend pas 800 ms.
    notes._save_timer.setInterval(0)
    notes._save_timer.start()
    QTest.qWait(30)
    pump()

    relue = ctx.notes.get(creee.id)
    assert relue.title == "Compte rendu"
    assert relue.body == "Point un\nPoint deux"

    # La recherche filtre bien la note qui vient d'être écrite.
    _champ(notes, SEARCH_PLACEHOLDER).setText("point deux")
    pump()
    assert [note.id for note in ctx.notes.list("point deux")] == [creee.id]


def test_une_saisie_en_cours_survit_au_rafraichissement_croise(
    window: MainWindow, ctx: AppContext, pump
) -> None:
    """Une écriture venue d'un autre onglet ne doit pas effacer la note en cours de frappe."""
    notes = _onglet(window, NotesTab)
    _cliquer(_bouton(notes, "Nouvelle note"))
    pump()
    corps = notes.findChild(QPlainTextEdit)
    corps.setPlainText("Brouillon pas encore enregistré")

    # L'onglet « Aujourd'hui » écrit : la fenêtre rafraîchit tous les autres onglets.
    _saisie_rapide(window, "Une autre tâche")
    pump()

    assert corps.toPlainText() == "Brouillon pas encore enregistré"
    notes.flush()
    pump()
    assert ctx.notes.list()[0].body == "Brouillon pas encore enregistré"


def test_note_enregistree_par_flush(window: MainWindow, ctx: AppContext, pump) -> None:
    notes = _onglet(window, NotesTab)
    _cliquer(_bouton(notes, "Nouvelle note"))
    pump()
    notes.findChild(QPlainTextEdit).setPlainText("Écrit sans attendre")
    notes.flush()
    pump()
    assert ctx.notes.list()[0].body == "Écrit sans attendre"


# -- Concentration --------------------------------------------------------


def test_minuteur_demarre_tick_puis_arrete(window: MainWindow, ctx: AppContext, pump) -> None:
    focus = _minuteur_d_une_minute(window, ctx)

    _cliquer(_bouton(focus, "Démarrer"))
    assert focus.timer.running is True
    assert focus.timer.phase is Phase.FOCUS

    _battements(focus, 60)  # une minute de concentration, seconde par seconde
    pump()

    entrees = ctx.times.entries()
    assert len(entrees) == 1
    assert entrees[0].duration_s == 60
    assert entrees[0].kind == Phase.FOCUS.value
    assert focus.timer.completed_focus == 1
    assert focus.timer.phase is Phase.BREAK  # la pause a pris la suite

    _cliquer(_bouton(focus, "Pause"))
    pump()
    assert focus.timer.running is False
    assert focus._clock.isActive() is False
    assert ctx.times.total_seconds() == 60


def test_minuteur_associe_la_tache_choisie(window: MainWindow, ctx: AppContext, pump) -> None:
    tache = ctx.tasks.add("Rédiger le rapport")
    focus = _minuteur_d_une_minute(window, ctx)
    focus._task_box.setCurrentIndex(focus._task_box.findData(tache.id))

    _cliquer(_bouton(focus, "Démarrer"))
    _battements(focus, 60)
    pump()
    _cliquer(_bouton(focus, "Pause"))

    assert [entree.task_id for entree in ctx.times.entries()] == [tache.id]


# -- Fichiers -------------------------------------------------------------


def test_rangement_planifie_applique_puis_annule(
    window: MainWindow, ctx: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pump
) -> None:
    dossier = _dossier_de_test(tmp_path)
    files = _onglet(window, FilesTab)
    _repondre_oui(monkeypatch, files)

    _champ(files, FOLDER_PLACEHOLDER).setText(str(dossier))
    _cliquer(_bouton(files, "Analyser"))
    _attendre_fichiers(files, pump)
    pump()

    plan = files._plan
    assert len(plan) == len(FAUX_FICHIERS) - 1, "le fichier sans règle ne doit pas être planifié"
    assert {move.src.name for move in plan} == set(FAUX_FICHIERS) - {"sans-regle.xyz"}
    # Un plan ne touche à rien : le dossier est intact.
    assert {chemin.name for chemin in dossier.iterdir()} == set(FAUX_FICHIERS)

    racine = ctx.files.target_root
    assert tmp_path in racine.parents, "le rangement doit rester sous le dossier temporaire"

    _cliquer(_bouton(files, "Ranger la sélection"))
    _attendre_fichiers(files, pump)
    pump()

    deplaces = sorted(chemin.name for chemin in racine.rglob("*") if chemin.is_file())
    assert deplaces == sorted(set(FAUX_FICHIERS) - {"sans-regle.xyz"})
    assert {chemin.name for chemin in dossier.iterdir()} == {"sans-regle.xyz"}
    assert _compter(ctx, "SELECT COUNT(*) FROM file_moves WHERE undone = 0") == len(plan)
    assert len(ctx.files.batches()) == 1

    _cliquer(_bouton(files, "Annuler le dernier rangement"))
    _attendre_fichiers(files, pump)
    pump()

    assert {chemin.name for chemin in dossier.iterdir()} == set(FAUX_FICHIERS)
    assert [chemin for chemin in racine.rglob("*") if chemin.is_file()] == []
    assert _compter(ctx, "SELECT COUNT(*) FROM file_moves WHERE undone = 0") == 0
    assert ctx.files.batches() == []


def test_recherche_de_doublons_sans_rien_deplacer(
    window: MainWindow, ctx: AppContext, tmp_path: Path, pump
) -> None:
    dossier = _dossier_de_test(tmp_path)
    (dossier / "copie-de-notes.txt").write_text("contenu de notes.txt\n", encoding="utf-8")
    avant = sorted(chemin.name for chemin in dossier.iterdir())

    files = _onglet(window, FilesTab)
    _champ(files, FOLDER_PLACEHOLDER).setText(str(dossier))
    _cliquer(_bouton(files, "Chercher les doublons"))
    _attendre_fichiers(files, pump)
    pump()

    assert "1 groupe" in files._dup_status.text()
    # La recherche ne touche à rien : ni le disque, ni la base.
    assert sorted(chemin.name for chemin in dossier.iterdir()) == avant
    assert _compter(ctx, "SELECT COUNT(*) FROM file_moves") == 0


def test_dossier_introuvable_affiche_une_erreur(
    window: MainWindow, ctx: AppContext, tmp_path: Path, pump
) -> None:
    files = _onglet(window, FilesTab)
    _champ(files, FOLDER_PLACEHOLDER).setText(str(tmp_path / "absent"))
    _cliquer(_bouton(files, "Analyser"))
    _attendre_fichiers(files, pump)
    pump()

    assert "introuvable" in files._plan_status.text()
    assert files._plan == []
    assert ctx.files.batches() == []


def test_dossier_relatif_rendu_absolu_et_memorise(
    window: MainWindow, ctx: AppContext, tmp_path: Path, pump, monkeypatch
) -> None:
    """Un dossier relatif dépend du dossier courant, qui change selon la façon
    dont l'application est lancée : il est rendu absolu dès la saisie, et c'est
    la forme absolue qui est affichée et mémorisée."""
    dossier = _dossier_de_test(tmp_path)
    monkeypatch.chdir(dossier.parent)
    files = _onglet(window, FilesTab)
    champ = _champ(files, FOLDER_PLACEHOLDER)
    champ.setText(dossier.name)
    _cliquer(_bouton(files, "Analyser"))
    _attendre_fichiers(files, pump)
    pump()

    assert champ.text() == str(dossier)
    assert ctx.settings.get(LAST_FOLDER_KEY) == str(dossier)
    assert files._plan and all(move.src.is_absolute() for move in files._plan)


def test_copies_a_verifier_en_tete_du_compte_rendu_jamais_tronquees(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mêlées aux échecs ordinaires, les copies à vérifier pouvaient disparaître
    derrière « … et N autres » : l'utilisateur croyait tous ses fichiers restés
    en place, alors qu'un original avait peut-être quitté le partage."""
    files = _onglet(window, FilesTab)
    boites: list = []
    monkeypatch.setattr(module_files_tab.QMessageBox, "exec", lambda box: boites.append(box) or 0)
    echecs = [(Path(f"verrou-{index}.pdf"), "fichier utilisé") for index in range(12)]
    gardee = Path("Rangé") / "Contrats" / "contrat (copie à vérifier).pdf"
    echecs.append((Path("contrat.pdf"), "Une copie est conservée…"))

    files._report("Rangement terminé", "2 fichiers rangés.", echecs, [gardee])

    texte = boites[0].informativeText()
    assert texte.startswith("1 copie à vérifier")
    assert f"• {gardee}" in texte
    assert "… et 5 autres." in texte  # la troncature ne touche que les échecs ordinaires


def test_ligne_d_etat_en_erreur_quand_une_copie_est_a_verifier(window: MainWindow) -> None:
    """Après fermeture du compte rendu, la ligne d'état reste la seule trace :
    elle ne doit pas annoncer en couleur neutre un rangement réussi."""
    files = _onglet(window, FilesTab)
    files._set_status(files._plan_status, "2 fichiers rangés.", [Path("copie (copie à vérifier).pdf")])
    assert "1 copie à vérifier" in files._plan_status.text()
    assert COLORS["danger"] in files._plan_status.styleSheet()

    files._set_status(files._plan_status, "2 fichiers rangés.", [])
    assert files._plan_status.text() == "2 fichiers rangés."
    assert COLORS["danger"] not in files._plan_status.styleSheet()


# -- Navigation et fermeture ---------------------------------------------


def test_changement_de_section_et_fermeture(window: MainWindow, ctx: AppContext, pump) -> None:
    for row in range(len(ONGLETS)):
        window._sidebar.setCurrentRow(row)
        pump()
        assert window._stack.currentIndex() == row
        assert ctx.settings.get("last_section") == row

    assert window.close() is True
    pump()
    assert window.isVisible() is False
    assert isinstance(ctx.settings.get("window_geometry"), str)


# -- Scénario complet ----------------------------------------------------


def test_scenario_complet(
    qapp, ctx: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pump
) -> None:
    """Le parcours entier dans une seule fenêtre, de l'ouverture à la fermeture."""
    window = MainWindow(ctx)
    for classe in ONGLETS:
        _onglet(window, classe).refresh()

    _saisie_rapide(window, "Préparer la réunion aujourd'hui !1 #équipe")
    pump()
    tache = ctx.tasks.list("all")[0]
    _onglet(window, TodayTab).findChildren(QCheckBox)[0].setChecked(True)
    pump()
    assert ctx.tasks.get(tache.id).done is True

    notes = _onglet(window, NotesTab)
    _cliquer(_bouton(notes, "Nouvelle note"))
    pump()
    notes.findChild(QPlainTextEdit).setPlainText("# Ordre du jour\n\nBudget")
    notes.flush()
    pump()
    assert ctx.notes.list()[0].body.endswith("Budget")

    focus = _minuteur_d_une_minute(window, ctx)
    _cliquer(_bouton(focus, "Démarrer"))
    _battements(focus, 60)
    _cliquer(_bouton(focus, "Pause"))
    pump()
    assert ctx.times.total_seconds() == 60

    dossier = _dossier_de_test(tmp_path)
    files = _onglet(window, FilesTab)
    _repondre_oui(monkeypatch, files)
    _champ(files, FOLDER_PLACEHOLDER).setText(str(dossier))
    _cliquer(_bouton(files, "Analyser"))
    _attendre_fichiers(files, pump)
    pump()
    assert files._plan
    _cliquer(_bouton(files, "Ranger la sélection"))
    _attendre_fichiers(files, pump)
    pump()
    assert len(ctx.files.batches()) == 1
    _cliquer(_bouton(files, "Annuler le dernier rangement"))
    _attendre_fichiers(files, pump)
    pump()
    assert ctx.files.batches() == []
    assert {chemin.name for chemin in dossier.iterdir()} == set(FAUX_FICHIERS)

    for row in range(len(ONGLETS)):
        window._sidebar.setCurrentRow(row)
        pump()

    assert window.close() is True
    pump()
    assert window.isVisible() is False


# -- Constats sur le minuteur ---------------------------------------------


def test_un_blocage_de_la_boucle_ne_perd_pas_de_concentration(
    window: MainWindow, ctx: AppContext, pump
) -> None:
    """Constat 11 : Qt fusionne les battements manqués, donc un blocage de la
    boucle (recherche de doublons, rangement) effaçait ce temps du journal."""
    focus = _minuteur_d_une_minute(window, ctx)
    horloge = _HORLOGES[id(focus)]
    _cliquer(_bouton(focus, "Démarrer"))

    horloge[0] += 1.0
    focus._on_tick()
    # 30 secondes de boucle bloquée : un seul battement en ressort.
    horloge[0] += 30.0
    focus._on_tick()

    assert focus._elapsed_s == 31
    assert focus.timer.remaining_s == 60 - 31
    assert focus.timer.display() == "00:29"

    # La fin de phase arrive au bon moment du temps réel, pas 30 s plus tard.
    horloge[0] += 29.0
    focus._on_tick()
    pump()
    entrees = ctx.times.entries()
    assert [entree.duration_s for entree in entrees] == [60]
    assert focus.timer.phase is Phase.BREAK


def test_le_temps_compte_ne_depasse_pas_le_temps_reel(window: MainWindow) -> None:
    """Corollaire du constat 11 : le temps vient de l'horloge, donc des battements
    surnuméraires ne peuvent pas gonfler la durée journalisée."""
    focus = _onglet(window, FocusTab)  # horloge réelle, concentration de 25 min
    _cliquer(_bouton(focus, "Démarrer"))
    try:
        QTest.qWait(1100)
        focus._on_tick()
        assert focus._elapsed_s == 1

        # 200 battements de plus n'ajoutent pas 200 secondes.
        for _ in range(200):
            focus._on_tick()
        assert focus._elapsed_s <= 2
    finally:
        focus.timer.pause()
        focus._clock.stop()


def test_la_tache_cochee_ailleurs_reste_associee_a_la_session(
    window: MainWindow, ctx: AppContext, pump
) -> None:
    """Constat 12 : cocher la tâche sur laquelle on se concentre — geste normal
    quand on la finit — journalisait la session avec `task_id = NULL`."""
    tache = ctx.tasks.add("Rédiger le rapport")
    focus = _minuteur_d_une_minute(window, ctx)
    focus._task_box.setCurrentIndex(focus._task_box.findData(tache.id))
    _cliquer(_bouton(focus, "Démarrer"))
    _battements(focus, 30)

    # L'utilisateur coche la tâche depuis « Tâches » ou « Aujourd'hui ».
    ctx.tasks.set_done(tache.id, True)
    focus.refresh()
    pump()

    # Elle reste choisie, signalée comme terminée.
    assert focus._task_box.currentData() == tache.id
    assert focus._task_box.currentText().endswith("(terminée)")

    _battements(focus, 30)
    pump()
    assert [entree.task_id for entree in ctx.times.entries()] == [tache.id]
    assert ctx.times.per_task() == [("Rédiger le rapport", 60)]


def test_une_session_a_cheval_sur_minuit_est_decoupee(
    window: MainWindow, ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une concentration commencée avant minuit disparaissait du journal du jour."""
    focus = _onglet(window, FocusTab)
    focus._elapsed_s = 1200
    focus._started_at = "2026-09-24T23:50:00"
    focus._task_id = None
    monkeypatch.setattr(module_focus, "now_iso", lambda: "2026-09-25T00:10:00")

    entree = focus._record(minimum=1)

    assert entree is not None
    entrees = sorted(ctx.times.entries(), key=lambda item: item.started_at)
    assert len(entrees) == 2
    assert entrees[0].started_at == "2026-09-24T23:50:00"
    assert entrees[1].started_at.startswith("2026-09-25T00:00:00")
    assert sum(item.duration_s for item in entrees) == 1200


# -- Constats sur les opérations disque longues ----------------------------


def test_le_rangement_laisse_la_fenetre_repondante(
    window: MainWindow, ctx: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pump
) -> None:
    """Constats 6 et 10 : les opérations disque tournaient dans le fil graphique ;
    la fenêtre gelait, et Windows invitait à tuer l'application en pleine opération."""
    dossier = _dossier_de_test(tmp_path)
    files = _onglet(window, FilesTab)
    _repondre_oui(monkeypatch, files)
    vrai_move = module_files.shutil.move

    def lent(source, destination, **extra):
        time.sleep(0.05)  # disque USB ou réseau
        return vrai_move(source, destination, **extra)

    monkeypatch.setattr(module_files.shutil, "move", lent)
    _champ(files, FOLDER_PLACEHOLDER).setText(str(dossier))
    _cliquer(_bouton(files, "Analyser"))
    _attendre_fichiers(files, pump)
    attendus = len(files._plan)
    assert attendus == 4

    _cliquer(_bouton(files, "Ranger la sélection"))

    # Le clic rend la main tout de suite : l'opération tourne ailleurs.
    assert files.busy()
    assert files._progress_row.isVisibleTo(files)
    assert not _bouton(files, "Analyser").isEnabled()

    battements: list[int] = []
    horloge = QTimer()
    horloge.setInterval(5)
    horloge.timeout.connect(lambda: battements.append(1))
    horloge.start()
    _attendre_fichiers(files, pump)
    horloge.stop()

    assert len(battements) >= 3, "la boucle d'événements doit continuer à tourner"
    assert _compter(ctx, "SELECT COUNT(*) FROM file_moves") == attendus
    assert not files._progress_row.isVisibleTo(files)
    assert _bouton(files, "Analyser").isEnabled()


def test_une_recherche_de_doublons_peut_etre_interrompue(
    window: MainWindow, ctx: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pump
) -> None:
    """Constats 6 et 10 : ni barre de progression, ni bouton d'annulation."""
    dossier = tmp_path / "Vidéos"
    dossier.mkdir()
    for index in range(12):  # même taille : tous seront hachés
        (dossier / f"video{index}.mp4").write_text("x" * 5000, encoding="utf-8")
    vrai_digest = module_files._digest

    def lent(chemin, limite=None):
        time.sleep(0.02)
        return vrai_digest(chemin, limite)

    monkeypatch.setattr(module_files, "_digest", lent)
    files = _onglet(window, FilesTab)
    _champ(files, FOLDER_PLACEHOLDER).setText(str(dossier))

    _cliquer(_bouton(files, "Chercher les doublons"))
    assert files.busy()
    _cliquer(_bouton(files, "Interrompre"))
    _attendre_fichiers(files, pump)

    assert "interrompue" in files._dup_status.text()
    # Une recherche ne touche à rien, interrompue ou non.
    assert sorted(chemin.name for chemin in dossier.iterdir()) == sorted(
        f"video{index}.mp4" for index in range(12)
    )
    assert _compter(ctx, "SELECT COUNT(*) FROM file_moves") == 0


# -- Constat 7 : une écriture ne doit pas relire les cinq onglets -----------


def test_une_ecriture_ne_relit_que_l_onglet_visible(
    window: MainWindow, ctx: AppContext, pump
) -> None:
    """Constat 7 : `_on_data_changed` relisait les cinq onglets, visibles ou non,
    ce qui gelait la fenêtre de 0,3 à 3,7 s à chaque case cochée."""
    relectures: dict[str, int] = {}
    for tab in window._tabs:
        nom = type(tab).__name__
        relectures[nom] = 0
        vrai = tab.refresh

        def compte(_vrai=vrai, _nom=nom):
            relectures[_nom] += 1
            _vrai()

        tab.refresh = compte  # type: ignore[method-assign]

    window._sidebar.setCurrentRow(0)  # « Aujourd'hui » est visible
    pump()
    relectures = dict.fromkeys(relectures, 0)

    _saisie_rapide(window, "Une tâche de plus")
    window.flush_refresh()
    pump()

    # « Aujourd'hui » se relit lui-même ; aucun autre onglet n'est touché.
    assert relectures["TasksTab"] == 0
    assert relectures["NotesTab"] == 0
    assert relectures["FocusTab"] == 0
    assert relectures["FilesTab"] == 0

    # Mais ils sont marqués, et se relisent dès qu'on les affiche.
    assert window._stale == {1, 2, 3, 4}
    window._sidebar.setCurrentRow(1)
    pump()
    assert relectures["TasksTab"] == 1
    assert window._stale == {2, 3, 4}

    # Un second passage sur un onglet à jour ne relit rien.
    window._sidebar.setCurrentRow(0)
    window._sidebar.setCurrentRow(1)
    pump()
    assert relectures["TasksTab"] == 1


def test_des_ecritures_rapprochees_ne_font_qu_une_relecture(
    window: MainWindow, ctx: AppContext, pump
) -> None:
    """Constat 7 : plusieurs `dataChanged` pour une même action provoquaient
    autant de cascades."""
    window._sidebar.setCurrentRow(1)  # « Tâches » visible
    pump()
    tasks = _onglet(window, TasksTab)
    relectures = []
    vrai = tasks.refresh
    tasks.refresh = lambda: (relectures.append(1), vrai())[1]  # type: ignore[method-assign]

    for _ in range(4):
        window._on_data_changed(0)  # quatre écritures rapprochées
    assert relectures == []  # rien n'a encore été relu
    window.flush_refresh()
    pump()

    assert len(relectures) == 1


def test_un_reglage_illisible_n_empeche_pas_le_demarrage(qapp, ctx: AppContext) -> None:
    """La lecture des réglages est encapsulée côté interface : une valeur qui
    n'est pas du JSON ne doit pas empêcher la fenêtre de s'ouvrir."""
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            ("last_section", "{ pas du JSON"),
        )
        ctx.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            ("window_geometry", "{ pas du JSON"),
        )
        ctx.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            ("pomodoro_focus_min", "{ pas du JSON"),
        )
        ctx.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            ("last_folder", "{ pas du JSON"),
        )

    fenetre = MainWindow(ctx)  # ne doit pas lever
    try:
        assert fenetre._sidebar.currentRow() == 0
        assert _onglet(fenetre, FocusTab).timer.config.focus_min == 25
    finally:
        fenetre.close()


# -- Constat 13 : un démarrage impossible doit s'expliquer ------------------


def test_une_base_corrompue_explique_la_marche_a_suivre(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qapp
) -> None:
    """Constat 13 : une base corrompue produisait un traceback Python, et
    l'application ne redémarrait plus jamais."""
    dossier = tmp_path / "donnees"
    dossier.mkdir()
    (dossier / "organizer.db").write_bytes(os.urandom(4096))
    monkeypatch.setenv("ORGANIZER_DATA_DIR", str(dossier))
    vues: list[tuple[str, str]] = []
    monkeypatch.setattr(
        module_app.QMessageBox,
        "critical",
        lambda parent, titre, texte: vues.append((titre, texte)),
    )

    code = module_app.main([])

    assert code == module_app.EXIT_DATA_ERROR
    assert len(vues) == 1
    titre, texte = vues[0]
    assert "base de données" in titre
    assert "organizer.db.corrompue" in texte
    assert str(dossier) in texte


def test_un_dossier_de_donnees_inutilisable_explique_la_marche_a_suivre(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qapp
) -> None:
    fichier = tmp_path / "pas-un-dossier"
    fichier.write_text("", encoding="utf-8")
    monkeypatch.setenv("ORGANIZER_DATA_DIR", str(fichier / "PersonalOrganizer"))
    vues: list[tuple[str, str]] = []
    monkeypatch.setattr(
        module_app.QMessageBox,
        "critical",
        lambda parent, titre, texte: vues.append((titre, texte)),
    )

    code = module_app.main([])

    assert code == module_app.EXIT_DATA_ERROR
    titre, texte = vues[0]
    assert "dossier de données" in titre
    assert "ORGANIZER_DATA_DIR" in texte


def test_un_rangement_interrompu_annonce_le_nombre_reel(
    window: MainWindow, ctx: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pump
) -> None:
    """Un rangement arrêté en route doit annoncer ce qu'il a fait, et le laisser
    annulable — c'est ce que les constats 2 et 3 rendaient impossible."""
    dossier = tmp_path / "Entrée"
    dossier.mkdir()
    for index in range(8):
        (dossier / f"doc{index}.pdf").write_text(f"contenu {index}", encoding="utf-8")
    files = _onglet(window, FilesTab)
    _repondre_oui(monkeypatch, files)
    vrai_move = module_files.shutil.move

    def lent(source, destination, **extra):
        time.sleep(0.05)
        return vrai_move(source, destination, **extra)

    monkeypatch.setattr(module_files.shutil, "move", lent)
    _champ(files, FOLDER_PLACEHOLDER).setText(str(dossier))
    _cliquer(_bouton(files, "Analyser"))
    _attendre_fichiers(files, pump)
    assert len(files._plan) == 8

    _cliquer(_bouton(files, "Ranger la sélection"))
    QTest.qWait(120)  # le temps de deux ou trois déplacements
    pump()
    _cliquer(_bouton(files, "Interrompre"))
    _attendre_fichiers(files, pump)

    ranges = _compter(ctx, "SELECT COUNT(*) FROM file_moves")
    assert 0 < ranges < 8, "l'interruption doit avoir coupé le lot"
    # Le journal suit exactement le disque, et le message dit vrai.
    sur_disque = len([c for c in (ctx.files.target_root).rglob("*") if c.is_file()])
    assert sur_disque == ranges
    assert f"{ranges} fichier" in files._plan_status.text()
    assert "interrompue" in files._plan_status.text()

    # Et le lot partiel reste annulable.
    assert [lot.count for lot in ctx.files.batches()] == [ranges]
    _cliquer(_bouton(files, "Annuler le dernier rangement"))
    _attendre_fichiers(files, pump)
    assert sorted(c.name for c in dossier.iterdir()) == sorted(
        f"doc{index}.pdf" for index in range(8)
    )
    assert ctx.files.batches() == []
