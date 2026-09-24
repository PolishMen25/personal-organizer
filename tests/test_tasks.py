"""Tests de la saisie rapide et du stockage des tâches."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

import pytest

from organizer.db import connect
from organizer.tasks import (
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_NORMAL,
    TaskStore,
    parse_quick_add,
)

# Jeudi : les jours de semaine cités dans les tests restent dans le même mois.
JEUDI = date(2026, 9, 24)


@pytest.fixture()
def store():
    conn = connect(":memory:")
    yield TaskStore(conn)
    conn.close()


def _backdate_completed(store: TaskStore, task_id: int, stamp: str) -> None:
    """Fixe `completed_at` pour rendre l'ordre des tâches faites prévisible."""
    with store.conn:
        store.conn.execute("UPDATE tasks SET completed_at = ? WHERE id = ?", (stamp, task_id))


# --- Saisie rapide -----------------------------------------------------------


def test_parse_quick_add_titre_seul():
    parsed = parse_quick_add("Ranger le garage", JEUDI)
    assert parsed.title == "Ranger le garage"
    assert parsed.priority == PRIORITY_NORMAL
    assert parsed.due_date is None
    assert parsed.project == ""


def test_parse_quick_add_priorite():
    assert parse_quick_add("Appeler !1", JEUDI).priority == PRIORITY_HIGH
    assert parse_quick_add("Appeler !2", JEUDI).priority == PRIORITY_NORMAL
    assert parse_quick_add("Appeler !3", JEUDI).priority == PRIORITY_LOW


def test_parse_quick_add_priorite_hors_bornes_reste_dans_le_titre():
    parsed = parse_quick_add("Appeler !4", JEUDI)
    assert parsed.title == "Appeler !4"
    assert parsed.priority == PRIORITY_NORMAL


def test_parse_quick_add_projet():
    parsed = parse_quick_add("Payer le loyer #maison", JEUDI)
    assert (parsed.title, parsed.project) == ("Payer le loyer", "maison")
    assert parse_quick_add("Bilan #projet-long_1", JEUDI).project == "projet-long_1"
    # Un dièse seul n'est pas un projet.
    assert parse_quick_add("Noter # ", JEUDI) == parse_quick_add("Noter #", JEUDI)
    assert parse_quick_add("Noter #", JEUDI).project == ""


def test_parse_quick_add_echeance():
    assert parse_quick_add("Réunion demain", JEUDI).due_date == date(2026, 9, 25)
    assert parse_quick_add("Réunion vendredi", JEUDI).due_date == date(2026, 9, 25)
    assert parse_quick_add("Réunion 12/10", JEUDI).due_date == date(2026, 10, 12)
    assert parse_quick_add("Réunion 12/10/2027", JEUDI).due_date == date(2027, 10, 12)


def test_parse_quick_add_date_impossible_reste_dans_le_titre():
    parsed = parse_quick_add("Inventaire 31/02", JEUDI)
    assert parsed.title == "Inventaire 31/02"
    assert parsed.due_date is None


def test_parse_quick_add_combinaisons_dans_n_importe_quel_ordre():
    attendu = ("Appeler la banque", PRIORITY_HIGH, date(2026, 9, 25), "admin")
    for texte in (
        "Appeler la banque demain !1 #admin",
        "!1 #admin demain Appeler la banque",
        "Appeler !1 la #admin banque demain",
    ):
        parsed = parse_quick_add(texte, JEUDI)
        assert (parsed.title, parsed.priority, parsed.due_date, parsed.project) == attendu


def test_parse_quick_add_dernier_marqueur_gagne():
    parsed = parse_quick_add("Bilan #a #b !3 !1 demain 12/10", JEUDI)
    assert (parsed.project, parsed.priority, parsed.due_date) == ("b", PRIORITY_HIGH, date(2026, 10, 12))


def test_parse_quick_add_normalise_les_espaces():
    assert parse_quick_add("  Ranger   le  garage \n", JEUDI).title == "Ranger le garage"


@pytest.mark.parametrize("texte", ["", "   ", "!1", "#admin", "demain", "!1 #admin demain"])
def test_parse_quick_add_refuse_un_titre_vide(texte):
    with pytest.raises(ValueError, match="titre"):
        parse_quick_add(texte, JEUDI)


def test_parse_quick_add_utilise_le_jour_courant_par_defaut():
    assert parse_quick_add("Réunion aujourd'hui").due_date == date.today()


# --- Ajout, lecture, modification --------------------------------------------


def test_add_conserve_les_champs(store):
    task = store.add("Appeler", priority=PRIORITY_HIGH, due_date=JEUDI, project="  admin  ", notes="numéro à trouver")
    assert (task.title, task.priority, task.due_date) == ("Appeler", PRIORITY_HIGH, JEUDI)
    assert (task.project, task.notes) == ("admin", "numéro à trouver")
    assert task.done is False
    assert task.completed_at is None
    assert store.get(task.id) == task


@pytest.mark.parametrize("titre", ["", "   ", "\n"])
def test_add_refuse_un_titre_vide(store, titre):
    with pytest.raises(ValueError, match="titre"):
        store.add(titre)
    assert store.list("all") == []


def test_add_quick_enregistre_la_saisie_analysee(store):
    task = store.add_quick("Appeler la banque demain !1 #admin", JEUDI)
    assert (task.title, task.priority, task.due_date, task.project) == (
        "Appeler la banque",
        PRIORITY_HIGH,
        date(2026, 9, 25),
        "admin",
    )
    assert store.get(task.id) == task


def test_get_sur_tache_absente(store):
    with pytest.raises(KeyError):
        store.get(404)


def test_update_modifie_les_champs_autorises(store):
    task = store.add("Brouillon")
    modifiee = store.update(
        task.id,
        title="Propre",
        notes="détails",
        priority=PRIORITY_LOW,
        due_date=date(2027, 1, 1),
        project="admin",
    )
    assert (modifiee.title, modifiee.notes, modifiee.priority) == ("Propre", "détails", PRIORITY_LOW)
    assert (modifiee.due_date, modifiee.project) == (date(2027, 1, 1), "admin")
    assert modifiee.created_at == task.created_at


def test_update_accepte_une_echeance_effacee(store):
    task = store.add("Sans date", due_date=JEUDI)
    assert store.update(task.id, due_date=None).due_date is None


def test_update_sans_champ_relit_la_tache(store):
    task = store.add("Inchangée")
    assert store.update(task.id) == task


def test_update_refuse_un_champ_inconnu(store):
    task = store.add("Brouillon")
    with pytest.raises(ValueError, match="Champs non modifiables"):
        store.update(task.id, done=1)
    with pytest.raises(ValueError, match="completed_at, created_at"):
        store.update(task.id, created_at="2020-01-01T00:00:00", completed_at="2020-01-01T00:00:00")
    assert store.get(task.id) == task


def test_update_refuse_un_titre_vide(store):
    task = store.add("Brouillon")
    with pytest.raises(ValueError, match="titre"):
        store.update(task.id, title="   ")
    assert store.get(task.id).title == "Brouillon"


def test_update_sur_tache_absente(store):
    with pytest.raises(KeyError):
        store.update(404, title="x")


@pytest.mark.parametrize("priorite", [0, 4, 7, -1, "1", True, None, 1.5])
def test_priorite_hors_bornes_refusee(store, priorite):
    with pytest.raises(ValueError, match="Priorité invalide"):
        store.add("Tâche", priority=priorite)
    task = store.add("Tâche valide")
    with pytest.raises(ValueError, match="Priorité invalide"):
        store.update(task.id, priority=priorite)
    assert store.get(task.id).priority == PRIORITY_NORMAL


@pytest.mark.parametrize("echeance", ["pas-une-date", "2026-13-01", "2026-02-30", "31/12/2026", 20261231, []])
def test_echeance_invalide_ne_corrompt_pas_la_tache(store, echeance):
    task = store.add("Essai", due_date=date(2026, 1, 1))
    with pytest.raises(ValueError, match="Échéance invalide"):
        store.update(task.id, due_date=echeance)
    # La tâche doit rester relisible : une valeur non ISO en base la rendrait
    # illisible pour Task.from_row, donc invisible dans toute l'interface.
    assert store.get(task.id).due_date == date(2026, 1, 1)
    assert [t.id for t in store.list("open")] == [task.id]


@pytest.mark.parametrize("echeance", ["pas-une-date", 20261231])
def test_echeance_invalide_refusee_des_la_creation(store, echeance):
    with pytest.raises(ValueError, match="Échéance invalide"):
        store.add("Essai", due_date=echeance)
    assert store.list("all") == []


@pytest.mark.parametrize(
    ("valeur", "attendu"),
    [
        (date(2026, 3, 4), date(2026, 3, 4)),
        ("2026-03-04", date(2026, 3, 4)),
        (datetime(2026, 3, 4, 17, 30), date(2026, 3, 4)),
        (None, None),
        ("", None),
    ],
)
def test_echeance_normalisee(store, valeur, attendu):
    task = store.add("Essai", due_date=valeur)
    assert store.get(task.id).due_date == attendu
    autre = store.add("Autre")
    assert store.update(autre.id, due_date=valeur).due_date == attendu


def test_set_done_et_retour_en_arriere(store):
    task = store.add("Finir")
    faite = store.set_done(task.id)
    assert faite.done is True
    assert faite.completed_at is not None

    rouverte = store.set_done(task.id, False)
    assert rouverte.done is False
    assert rouverte.completed_at is None


def test_delete_retire_la_tache(store):
    gardee = store.add("Gardée")
    jetee = store.add("Jetée")

    store.delete(jetee.id)

    assert [t.id for t in store.list("all")] == [gardee.id]
    store.delete(jetee.id)  # idempotent


def test_is_overdue(store):
    retard = store.add("Retard", due_date=date(2026, 9, 20))
    jour = store.add("Aujourd'hui", due_date=JEUDI)
    sans_date = store.add("Sans date")

    assert retard.is_overdue(JEUDI) is True
    assert jour.is_overdue(JEUDI) is False
    assert sans_date.is_overdue(JEUDI) is False
    # Une tâche faite n'est jamais en retard.
    assert store.set_done(retard.id).is_overdue(JEUDI) is False


# --- Listage -----------------------------------------------------------------


@pytest.fixture()
def peuple(store):
    """Un jeu de tâches couvrant les échéances, les priorités et les projets."""
    store.add("Retard", due_date=date(2026, 9, 20), project="admin")
    store.add("Aujourd'hui", due_date=JEUDI, project="maison")
    store.add("Futur", due_date=date(2026, 12, 1), priority=PRIORITY_HIGH)
    store.add("Important", priority=PRIORITY_HIGH)
    store.add("Banal", notes="rien d'urgent")
    store.add("Basse", priority=PRIORITY_LOW)
    finie = store.add("Finie", project="admin")
    store.set_done(finie.id)
    return store


def test_list_open_trie_par_echeance_puis_priorite(peuple):
    assert [t.title for t in peuple.list()] == [
        "Retard",
        "Aujourd'hui",
        "Futur",
        "Important",
        "Banal",
        "Basse",
    ]
    assert peuple.list() == peuple.list("open")


def test_list_done_et_all(peuple):
    assert [t.title for t in peuple.list("done")] == ["Finie"]
    assert len(peuple.list("all")) == 7
    assert [t.title for t in peuple.list("all")][-1] == "Finie"


def test_list_done_du_plus_recent_au_plus_ancien(store):
    ancienne = store.add("Ancienne")
    recente = store.add("Récente")
    store.set_done(ancienne.id)
    store.set_done(recente.id)
    _backdate_completed(store, ancienne.id, "2026-01-01T09:00:00")
    _backdate_completed(store, recente.id, "2026-03-01T09:00:00")

    assert [t.title for t in store.list("done")] == ["Récente", "Ancienne"]


def test_list_filtre_par_projet(peuple):
    assert [t.title for t in peuple.list("open", project="admin")] == ["Retard"]
    assert [t.title for t in peuple.list("all", project="admin")] == ["Retard", "Finie"]
    assert peuple.list("all", project="inconnu") == []
    # Un projet vide ne filtre rien.
    assert len(peuple.list("all", project="")) == 7


def test_list_recherche_titre_et_notes_sans_casse(peuple):
    assert [t.title for t in peuple.list("open", search="retard")] == ["Retard"]
    assert [t.title for t in peuple.list("open", search="RETARD")] == ["Retard"]
    assert [t.title for t in peuple.list("open", search="urgent")] == ["Banal"]
    assert peuple.list("all", search="introuvable") == []
    assert len(peuple.list("all", search="")) == 7


def test_list_combine_les_filtres(peuple):
    assert [t.title for t in peuple.list("all", project="admin", search="fin")] == ["Finie"]
    assert peuple.list("open", project="admin", search="fin") == []


# --- Vue « Aujourd'hui » et projets ------------------------------------------


def test_today_classe_retard_jour_et_important(peuple):
    sections = peuple.today(JEUDI)
    assert set(sections) == {"overdue", "today", "important"}
    assert [t.title for t in sections["overdue"]] == ["Retard"]
    assert [t.title for t in sections["today"]] == ["Aujourd'hui"]
    # Priorité haute sans échéance seulement : « Futur » est daté, « Banal » n'est pas prioritaire.
    assert [t.title for t in sections["important"]] == ["Important"]


def test_today_ignore_les_taches_faites(store):
    retard = store.add("Retard", due_date=date(2026, 9, 20))
    important = store.add("Important", priority=PRIORITY_HIGH)
    store.set_done(retard.id)
    store.set_done(important.id)

    assert store.today(JEUDI) == {"overdue": [], "today": [], "important": []}


def test_today_suit_le_jour_de_reference(store):
    store.add("Échéance", due_date=JEUDI)

    veille = store.today(date(2026, 9, 23))
    assert [t.title for t in veille["today"]] == []
    assert [t.title for t in veille["overdue"]] == []

    lendemain = store.today(date(2026, 9, 25))
    assert [t.title for t in lendemain["overdue"]] == ["Échéance"]


def test_projects_distincts_et_tries(peuple):
    assert peuple.projects() == ["admin", "maison"]


def test_projects_sans_projet(store):
    store.add("Sans projet")
    store.add("Espaces", project="   ")
    assert store.projects() == []


def test_priorite_hors_bornes_rejetee_par_le_schema(store):
    """Le CHECK du schéma reste la seconde ligne de défense.

    `TaskStore.add` refuse désormais la valeur avant l'écriture ; on écrit donc
    en SQL direct pour vérifier que la contrainte protège quand même la base.
    """
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO tasks (title, priority, created_at) VALUES ('Tâche', 0, '2026-01-01T00:00:00')"
        )
    assert store.list("all") == []


def test_recherche_protege_les_jokers_de_like(store):
    """Les caractères « % » et « _ » sont des jokers de LIKE : une recherche
    littérale ne doit pas ramener tout le reste."""
    remise = store.add("Remise de 100% sur la facture")
    store.add("Remise de 10 euros")
    note_1 = store.add("Relire note_1")
    store.add("Relire noteX1")

    assert [tache.id for tache in store.list("all", search="100%")] == [remise.id]
    assert [tache.id for tache in store.list("all", search="note_1")] == [note_1.id]
    # Un antislash saisi reste littéral lui aussi.
    chemin = store.add(r"Ranger C:\Temp")
    assert [tache.id for tache in store.list("all", search=r"C:\Temp")] == [chemin.id]
