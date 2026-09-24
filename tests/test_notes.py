"""Tests du stockage des notes."""

from __future__ import annotations

import pytest

from organizer.db import connect
from organizer.notes import DEFAULT_TITLE, NoteStore


@pytest.fixture()
def store():
    conn = connect(":memory:")
    yield NoteStore(conn)
    conn.close()


def _backdate(store: NoteStore, note_id: int, stamp: str) -> None:
    """Fixe `updated_at` pour rendre l'ordre de listage prévisible."""
    with store.conn:
        store.conn.execute("UPDATE notes SET updated_at = ? WHERE id = ?", (stamp, note_id))


def test_add_conserve_le_titre_et_le_corps(store):
    note = store.add("Courses", "Pain\nLait")
    assert (note.title, note.body) == ("Courses", "Pain\nLait")
    assert note.pinned is False
    assert note.created_at == note.updated_at
    assert store.get(note.id) == note


def test_add_derive_le_titre_du_corps(store):
    assert store.add(body="# Réunion équipe\n\nOrdre du jour").title == "Réunion équipe"
    assert store.add(title="   ", body="\n\nPremière ligne utile\nsuite").title == "Première ligne utile"
    assert store.add().title == DEFAULT_TITLE
    assert store.add(body="   \n\n").title == DEFAULT_TITLE


def test_derive_title_tronque_a_soixante_caracteres(store):
    titre = NoteStore.derive_title("## " + "mot " * 40)
    assert len(titre) <= 60
    assert titre.endswith("…")


def test_preview_prend_la_premiere_ligne_non_vide(store):
    note = store.add("Idées", "\n\n   Acheter un carnet neuf   \nligne suivante")
    assert note.preview() == "Acheter un carnet neuf"
    assert note.preview(10) == "Acheter…"
    assert store.add("Vide", "").preview() == ""


def test_list_epinglees_puis_modification_decroissante(store):
    ancienne = store.add("Ancienne", "a")
    moyenne = store.add("Moyenne", "b")
    recente = store.add("Récente", "c")
    _backdate(store, ancienne.id, "2026-01-01T09:00:00")
    _backdate(store, moyenne.id, "2026-02-01T09:00:00")
    _backdate(store, recente.id, "2026-03-01T09:00:00")

    assert [n.title for n in store.list()] == ["Récente", "Moyenne", "Ancienne"]

    epinglee = store.set_pinned(ancienne.id)
    assert epinglee.pinned is True
    assert epinglee.updated_at == "2026-01-01T09:00:00"  # l'épinglage ne compte pas comme une modification
    assert [n.title for n in store.list()] == ["Ancienne", "Récente", "Moyenne"]

    store.set_pinned(ancienne.id, False)
    assert [n.title for n in store.list()] == ["Récente", "Moyenne", "Ancienne"]


def test_list_recherche_titre_et_corps_sans_casse(store):
    store.add("Réunion budget", "prévoir les chiffres")
    store.add("Courses", "acheter du RÉUNIONnais")
    store.add("Vacances", "réserver un gîte")

    assert {n.title for n in store.list("réunion")} == {"Réunion budget", "Courses"}
    assert {n.title for n in store.list("CHIFFRES")} == {"Réunion budget"}
    assert store.list("introuvable") == []
    assert len(store.list("   ")) == 3


def test_update_modifie_les_champs_et_updated_at(store):
    note = store.add("Brouillon", "texte")
    _backdate(store, note.id, "2026-01-01T09:00:00")

    modifiee = store.update(note.id, title="Propre", body="nouveau texte", pinned=True)
    assert (modifiee.title, modifiee.body, modifiee.pinned) == ("Propre", "nouveau texte", True)
    assert modifiee.updated_at > "2026-01-01T09:00:00"
    assert modifiee.created_at == note.created_at


def test_update_titre_vide_redevient_derive(store):
    note = store.add("Brouillon", "ancien corps")
    assert store.update(note.id, title="").title == "ancien corps"
    assert store.update(note.id, title="   ", body="# Nouveau corps").title == "Nouveau corps"


def test_update_refuse_un_champ_inconnu(store):
    note = store.add("Brouillon", "texte")
    with pytest.raises(ValueError, match="Champs non modifiables"):
        store.update(note.id, created_at="2020-01-01T00:00:00")
    assert store.get(note.id).created_at == note.created_at


def test_get_et_update_sur_note_absente(store):
    with pytest.raises(KeyError):
        store.get(404)
    with pytest.raises(KeyError):
        store.update(404, title="x")


def test_delete_retire_la_note(store):
    gardee = store.add("Gardée", "a")
    jetee = store.add("Jetée", "b")

    store.delete(jetee.id)

    assert [n.id for n in store.list()] == [gardee.id]
    with pytest.raises(KeyError):
        store.get(jetee.id)
    store.delete(jetee.id)  # idempotent
