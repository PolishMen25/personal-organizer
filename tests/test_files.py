"""Tests du moteur de rangement : plans sans effet de bord, collisions, annulation."""

from __future__ import annotations

import json
import os
import shutil
import stat
import time
from pathlib import Path

import pytest

from organizer import files as module_files
from organizer.db import connect
from organizer.files import (
    DEFAULT_RULES,
    FileOrganizer,
    Progress,
    Rule,
    load_rules,
    read_rules,
    save_rules,
)


@pytest.fixture()
def conn():
    connection = connect(":memory:")
    yield connection
    connection.close()


@pytest.fixture()
def root(tmp_path):
    """Racine de destination, volontairement absente au départ."""
    return tmp_path / "Rangé"


def _write(path: Path, content: str = "contenu") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _tree(folder: Path) -> set[str]:
    return {str(item.relative_to(folder)) for item in folder.rglob("*")}


def _age(path: Path, days: int) -> None:
    """Vieillit la date de modification du fichier de `days` jours."""
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


def _case_sensitive(folder: Path) -> bool:
    probe = _write(folder / "Sonde.txt")
    result = not (folder / "sonde.txt").exists()
    probe.unlink()
    return result


# --- Règles ---------------------------------------------------------------


@pytest.mark.parametrize(
    "destination",
    ["/etc/passwd", "C:/Windows", "\\\\serveur\\partage", "../secret", "Documents/../../secret", "   ", "."],
)
def test_rule_refuse_une_destination_invalide(destination):
    with pytest.raises(ValueError):
        Rule("Test", destination, ["pdf"])


def test_rule_refuse_un_nom_vide_et_un_age_negatif():
    with pytest.raises(ValueError, match="nom"):
        Rule("  ", "Documents", ["pdf"])
    with pytest.raises(ValueError, match="négatif"):
        Rule("Test", "Documents", ["pdf"], min_age_days=-1)


def test_matches_extension_motif_et_regle_desactivee(tmp_path):
    fichier = _write(tmp_path / "Facture Août.PDF")

    assert Rule("PDF", "Documents/PDF", [".PDF"]).matches(fichier)
    assert Rule("Factures", "Documents/Factures", [], ["*FACTURE*"]).matches(fichier)
    assert not Rule("Images", "Images", ["png"]).matches(fichier)
    assert not Rule("PDF", "Documents/PDF", ["pdf"], enabled=False).matches(fichier)
    assert not Rule("Vide", "Divers").matches(fichier)  # sans extension ni motif : ne prend rien


def test_rule_normalise_et_survit_aux_cles_absentes():
    regle = Rule.from_dict({"name": " Perso ", "destination": "Perso\\Sous-dossier", "extensions": "PDF"})
    assert (regle.name, regle.destination, regle.extensions) == ("Perso", "Perso/Sous-dossier", ["pdf"])
    assert (regle.patterns, regle.min_age_days, regle.enabled) == ([], 0, True)
    assert Rule.from_dict(regle.to_dict()) == regle


def test_save_et_load_rules_font_un_tour_complet(tmp_path):
    chemin = tmp_path / "config" / "regles.json"

    assert load_rules(chemin) == DEFAULT_RULES  # absent : le fichier est créé
    assert list(chemin.parent.iterdir()) == [chemin]  # écriture atomique, aucun .tmp résiduel
    assert load_rules(chemin) == DEFAULT_RULES

    modifiees = [Rule("Perso", "Perso\\Sous-dossier", ["xyz"], ["*perso*"], min_age_days=3, enabled=False)]
    save_rules(chemin, modifiees)
    assert load_rules(chemin) == modifiees
    assert json.loads(chemin.read_text(encoding="utf-8"))[0]["destination"] == "Perso/Sous-dossier"


def test_load_rules_conserve_l_original_illisible(tmp_path):
    """Constat 5 : un fichier illisible était écrasé par les défauts, sans copie."""
    chemin = tmp_path / "regles.json"
    chemin.write_text("{ ceci n'est pas du JSON", encoding="utf-8")

    rapport = read_rules(chemin)

    assert rapport.rules == DEFAULT_RULES
    assert rapport.errors and "JSON" in rapport.errors[0]
    # L'original doit survivre à côté, pour être récupérable à la main.
    assert rapport.backup is not None
    assert rapport.backup.read_text(encoding="utf-8") == "{ ceci n'est pas du JSON"
    assert "invalide-" in rapport.backup.name
    assert json.loads(chemin.read_text(encoding="utf-8"))[0]["name"] == "Factures"

    rapport.rules[0].name = "Modifiée sur la copie"
    assert DEFAULT_RULES[0].name == "Factures"  # les défauts du module restent intacts


def test_load_rules_garde_les_regles_valides_malgre_une_fautive(tmp_path):
    """Constat 5 : une seule règle fautive effaçait les onze autres du disque."""
    chemin = tmp_path / "regles.json"
    ecrites = [
        {"name": f"Règle {index}", "destination": f"Dossier{index}", "extensions": [f"e{index}"]}
        for index in range(11)
    ]
    # Erreur naturelle : les destinations sont relatives, celle-ci est absolue.
    ecrites.append({"name": "Chantier", "destination": "D:/Chantier 2026", "extensions": ["dwg"]})
    brut = json.dumps(ecrites, ensure_ascii=False)
    chemin.write_text(brut, encoding="utf-8")

    rapport = read_rules(chemin)

    assert [regle.name for regle in rapport.rules] == [f"Règle {index}" for index in range(11)]
    assert len(rapport.errors) == 1
    assert "Chantier" in rapport.errors[0] and "Règle 12" in rapport.errors[0]
    # Surtout : le fichier de l'utilisateur n'a pas bougé.
    assert chemin.read_text(encoding="utf-8") == brut
    assert rapport.backup is None
    assert sorted(item.name for item in tmp_path.iterdir()) == ["regles.json"]


def test_load_rules_cree_le_fichier_seulement_s_il_est_absent(tmp_path):
    chemin = tmp_path / "regles.json"

    assert read_rules(chemin).rules == DEFAULT_RULES
    assert chemin.is_file()

    # Une liste vide ne vaut rien pour le rangement, mais ne justifie pas
    # d'écraser le fichier : les défauts ne servent qu'en mémoire.
    chemin.write_text("[]", encoding="utf-8")
    rapport = read_rules(chemin)
    assert rapport.rules == DEFAULT_RULES
    assert rapport.errors
    assert chemin.read_text(encoding="utf-8") == "[]"


# --- plan() ---------------------------------------------------------------


def test_plan_ne_touche_a_rien(conn, tmp_path, root):
    source = tmp_path / "Téléchargements"
    _write(source / "rapport.pdf")
    _write(source / "photo.jpg")
    avant = _tree(tmp_path)
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)

    moves = organizer.plan(source)

    assert {move.src.name: move.dst for move in moves} == {
        "photo.jpg": root / "Pictures" / "photo.jpg",
        "rapport.pdf": root / "Documents" / "PDF" / "rapport.pdf",
    }
    assert not root.exists()
    assert _tree(tmp_path) == avant
    assert conn.execute("SELECT COUNT(*) FROM file_moves").fetchone()[0] == 0


def test_plan_premiere_regle_activee_gagne_et_ignore_le_reste(conn, tmp_path, root):
    source = tmp_path / "Entrée"
    _write(source / "facture-2026.pdf")
    _write(source / "inconnu.qqq")
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)

    moves = organizer.plan(source)

    assert [(move.src.name, move.rule) for move in moves] == [("facture-2026.pdf", "Factures")]
    assert moves[0].dst == root / "Documents" / "Factures" / "facture-2026.pdf"


def test_plan_ignore_dossiers_fichiers_caches_et_temporaires(conn, tmp_path, root):
    source = tmp_path / "Entrée"
    (source / "un-dossier").mkdir(parents=True)
    garde = _write(source / "rapport.docx")
    for nom in ("~$rapport.docx", "film.mp4.part", "image.png.crdownload", "Brouillon.TMP", "lien.lnk"):
        _write(source / nom)
    _write(source / ".cache.docx")
    # Une règle attrape-tout : seuls les filtres de sûreté peuvent écarter un fichier.
    organizer = FileOrganizer(conn, [Rule("Fourre-tout", "Divers", [], ["*"])], root)

    assert [move.src for move in organizer.plan(source)] == [garde]


def test_plan_ignore_un_lien_symbolique(conn, tmp_path, root):
    source = tmp_path / "Entrée"
    cible = _write(tmp_path / "ailleurs" / "vrai.pdf")
    try:
        (source).mkdir(parents=True, exist_ok=True)
        (source / "lien.pdf").symlink_to(cible)
    except (OSError, NotImplementedError):
        pytest.skip("les liens symboliques ne sont pas disponibles ici")
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)

    assert organizer.plan(source) == []


def test_plan_ignore_un_fichier_deja_a_destination(conn, tmp_path, root):
    destination = root / "Documents" / "PDF"
    _write(destination / "rapport.pdf")
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)

    assert organizer.plan(destination) == []


def test_plan_respecte_min_age_days(conn, tmp_path, root):
    source = tmp_path / "Entrée"
    ancien = _write(source / "ancien.pdf")
    _write(source / "recent.pdf")
    _age(ancien, 10)
    organizer = FileOrganizer(conn, [Rule("Vieux PDF", "Documents/PDF", ["pdf"], min_age_days=7)], root)

    assert [move.src.name for move in organizer.plan(source)] == ["ancien.pdf"]


def test_plan_refuse_un_dossier_introuvable(conn, tmp_path, root):
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)
    with pytest.raises(ValueError, match="introuvable"):
        organizer.plan(tmp_path / "absent")


def test_target_root_par_defaut_est_le_dossier_utilisateur(conn):
    assert FileOrganizer(conn, DEFAULT_RULES).target_root == Path.home()


# --- Collisions -----------------------------------------------------------


def test_collision_avec_un_fichier_existant_donne_deux(conn, tmp_path, root):
    source = tmp_path / "Entrée"
    _write(source / "rapport.pdf", "nouveau")
    _write(root / "Documents" / "PDF" / "rapport.pdf", "ancien")
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)

    (move,) = organizer.plan(source)
    assert move.dst.name == "rapport (2).pdf"

    organizer.apply([move])
    destination = root / "Documents" / "PDF"
    assert (destination / "rapport.pdf").read_text(encoding="utf-8") == "ancien"
    assert (destination / "rapport (2).pdf").read_text(encoding="utf-8") == "nouveau"


def test_collision_interne_au_meme_plan(conn, tmp_path, root):
    source = tmp_path / "Entrée"
    source.mkdir(parents=True)
    if not _case_sensitive(source):
        pytest.skip("système de fichiers insensible à la casse : la collision interne est impossible ici")
    _write(source / "Rapport.PDF", "majuscules")
    _write(source / "rapport.pdf", "minuscules")
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)

    moves = organizer.plan(source)

    assert len(moves) == 2
    assert len({str(move.dst).casefold() for move in moves}) == 2
    assert any(move.dst.name.endswith(" (2).pdf") for move in moves)


def test_collision_interne_au_meme_lot(conn, tmp_path, root):
    _write(tmp_path / "A" / "notes.txt", "A")
    _write(tmp_path / "B" / "notes.txt", "B")
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)

    moves = organizer.plan(tmp_path / "A") + organizer.plan(tmp_path / "B")
    batch_id = organizer.apply(moves)

    destination = root / "Documents" / "Textes"
    assert organizer.last_failures == []
    assert sorted(item.name for item in destination.iterdir()) == ["notes (2).txt", "notes.txt"]
    assert sorted(item.read_text(encoding="utf-8") for item in destination.iterdir()) == ["A", "B"]
    cibles = conn.execute("SELECT dst FROM file_moves WHERE batch_id = ?", (batch_id,)).fetchall()
    assert len({row["dst"] for row in cibles}) == 2


def test_apply_renomme_si_la_cible_est_apparue_depuis_le_plan(conn, tmp_path, root):
    source = tmp_path / "Entrée"
    _write(source / "rapport.pdf", "à ranger")
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)
    moves = organizer.plan(source)
    _write(root / "Documents" / "PDF" / "rapport.pdf", "arrivé entre-temps")

    organizer.apply(moves)

    destination = root / "Documents" / "PDF"
    assert (destination / "rapport.pdf").read_text(encoding="utf-8") == "arrivé entre-temps"
    assert (destination / "rapport (2).pdf").read_text(encoding="utf-8") == "à ranger"


# --- apply() / undo() / batches() ----------------------------------------


def test_apply_puis_undo_complet(conn, tmp_path, root):
    source = tmp_path / "Entrée"
    _write(source / "rapport.pdf", "un")
    _write(source / "photo.jpg", "deux")
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)

    batch_id = organizer.apply(organizer.plan(source))

    assert organizer.last_failures == []
    assert (root / "Documents" / "PDF" / "rapport.pdf").read_text(encoding="utf-8") == "un"
    assert (root / "Pictures" / "photo.jpg").read_text(encoding="utf-8") == "deux"
    assert list(source.iterdir()) == []
    lot = organizer.batches()
    assert [(item.batch_id, item.count) for item in lot] == [(batch_id, 2)]
    assert lot[0].moved_at

    assert organizer.undo(batch_id) == 2
    assert (source / "rapport.pdf").read_text(encoding="utf-8") == "un"
    assert (source / "photo.jpg").read_text(encoding="utf-8") == "deux"
    assert organizer.batches() == []
    assert organizer.undo(batch_id) == 0  # un lot annulé ne se rejoue pas


def test_undo_refuse_une_source_reapparue(conn, tmp_path, root):
    source = tmp_path / "Entrée"
    _write(source / "rapport.pdf", "original")
    _write(source / "photo.jpg", "image")
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)
    batch_id = organizer.apply(organizer.plan(source))
    _write(source / "rapport.pdf", "homonyme récent")

    assert organizer.undo(batch_id) == 1

    assert (source / "rapport.pdf").read_text(encoding="utf-8") == "homonyme récent"
    assert (root / "Documents" / "PDF" / "rapport.pdf").read_text(encoding="utf-8") == "original"
    assert (source / "photo.jpg").read_text(encoding="utf-8") == "image"
    assert [path.name for path, _ in organizer.last_failures] == ["rapport.pdf"]
    restantes = conn.execute("SELECT src, undone FROM file_moves ORDER BY src").fetchall()
    assert [(Path(row["src"]).name, row["undone"]) for row in restantes] == [
        ("photo.jpg", 1),
        ("rapport.pdf", 0),
    ]
    assert [item.count for item in organizer.batches()] == [1]


def test_un_echec_n_interrompt_pas_le_lot(conn, tmp_path, root):
    source = tmp_path / "Entrée"
    _write(source / "bloque.pdf", "x")
    _write(source / "photo.jpg", "y")
    _write(root / "Documents", "un fichier là où il faudrait un dossier")
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)

    batch_id = organizer.apply(organizer.plan(source))

    assert [path.name for path, _ in organizer.last_failures] == ["bloque.pdf"]
    assert all(message for _, message in organizer.last_failures)
    assert (source / "bloque.pdf").exists()
    assert (root / "Pictures" / "photo.jpg").exists()
    assert [(item.batch_id, item.count) for item in organizer.batches()] == [(batch_id, 1)]


def test_apply_signale_une_source_disparue(conn, tmp_path, root):
    source = tmp_path / "Entrée"
    _write(source / "rapport.pdf")
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)
    moves = organizer.plan(source)
    (source / "rapport.pdf").unlink()

    batch_id = organizer.apply(moves)

    assert [path.name for path, _ in organizer.last_failures] == ["rapport.pdf"]
    assert organizer.batches() == []
    assert conn.execute("SELECT COUNT(*) FROM file_moves WHERE batch_id = ?", (batch_id,)).fetchone()[0] == 0


def test_batches_du_plus_recent_au_plus_ancien(conn, tmp_path, root):
    source = tmp_path / "Entrée"
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)
    _write(source / "premier.pdf")
    premier = organizer.apply(organizer.plan(source))
    _write(source / "second.pdf")
    second = organizer.apply(organizer.plan(source))

    assert [item.batch_id for item in organizer.batches()] == [second, premier]
    assert [item.batch_id for item in organizer.batches(limit=1)] == [second]


# --- Doublons -------------------------------------------------------------


def test_find_duplicates_sur_contenu_identique_de_tailles_egales(conn, tmp_path, root):
    dossier = tmp_path / "Dossier"
    premier = _write(dossier / "a.txt", "contenu identique")
    second = _write(dossier / "sous" / "b.txt", "contenu identique")
    leurre = _write(dossier / "c.txt", "contenu different")
    _write(dossier / "vide1.txt", "")
    _write(dossier / "vide2.txt", "")
    _write(dossier / "seul.txt", "taille unique pour ce contenu")
    avant = _tree(tmp_path)
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)

    assert premier.stat().st_size == leurre.stat().st_size  # même taille, contenu différent
    groupes = organizer.find_duplicates(dossier)

    assert [set(groupe) for groupe in groupes] == [{premier, second}]
    assert _tree(tmp_path) == avant  # aucune suppression, aucun déplacement


def test_find_duplicates_non_recursif_reste_dans_le_dossier(conn, tmp_path, root):
    dossier = tmp_path / "Dossier"
    _write(dossier / "a.txt", "contenu identique")
    _write(dossier / "sous" / "b.txt", "contenu identique")
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)

    assert organizer.find_duplicates(dossier, recursive=False) == []


def test_find_duplicates_ignore_les_fichiers_caches(conn, tmp_path, root):
    dossier = tmp_path / "Dossier"
    _write(dossier / "a.txt", "contenu identique")
    _write(dossier / ".a.txt", "contenu identique")
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)

    assert organizer.find_duplicates(dossier) == []


# --- Interruptions : le journal ne doit jamais être en retard sur le disque ---


# Référence prise avant tout monkeypatch : `shutil.move` désigne ensuite le leurre.
_VRAI_MOVE = shutil.move


def _interrompt_apres(nombre: int):
    """Remplace `shutil.move` par une version qui abandonne après `nombre` appels."""
    vrai = _VRAI_MOVE
    appels = {"n": 0}

    def faux(source, destination, **extra):
        appels["n"] += 1
        if appels["n"] > nombre:
            raise KeyboardInterrupt("processus interrompu")
        return vrai(source, destination, **extra)

    return faux


def test_apply_journalise_chaque_deplacement_immediatement(conn, tmp_path, root, monkeypatch):
    """Constat 2 : le journal n'était écrit qu'après le lot, donc une interruption
    laissait des fichiers déplacés et aucune possibilité d'annulation."""
    source = tmp_path / "Entrée"
    for index in range(6):
        _write(source / f"doc{index}.pdf", f"contenu {index}")
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)
    plan = organizer.plan(source)
    monkeypatch.setattr(module_files.shutil, "move", _interrompt_apres(3))

    with pytest.raises(KeyboardInterrupt):
        organizer.apply(plan)

    deplaces = sorted(chemin.name for chemin in (root / "Documents" / "PDF").iterdir())
    lignes = conn.execute("SELECT src, dst FROM file_moves").fetchall()
    assert len(deplaces) == 3
    assert len(lignes) == 3  # le journal suit le disque, pas le lot
    assert sorted(Path(ligne["dst"]).name for ligne in lignes) == deplaces
    lots = organizer.batches()
    assert [lot.count for lot in lots] == [3]  # « Annuler » a bien de quoi travailler

    # Et l'annulation remet effectivement ces trois fichiers en place.
    monkeypatch.setattr(module_files.shutil, "move", _VRAI_MOVE)
    assert organizer.undo(lots[0].batch_id) == 3
    assert sorted(chemin.name for chemin in source.iterdir()) == [f"doc{i}.pdf" for i in range(6)]


def test_apply_signale_un_journal_verrouille_sans_lever(tmp_path, root):
    """Constat 2 (variante) : une 2e connexion qui tient un verrou faisait remonter
    `OperationalError` d'`apply()` jusqu'au slot Qt, après avoir déplacé les fichiers."""
    base = tmp_path / "organizer.db"
    connexion = connect(base)
    verrou = connect(base)
    try:
        connexion.execute("PRAGMA busy_timeout = 100")  # sans cela, cinq secondes d'attente
        verrou.execute("BEGIN EXCLUSIVE")  # sauvegarde, antivirus, 2e instance…
        source = tmp_path / "Entrée"
        _write(source / "rapport.pdf")
        organizer = FileOrganizer(connexion, DEFAULT_RULES, root)
        plan = organizer.plan(source)

        batch_id = organizer.apply(plan)  # ne lève pas

        assert isinstance(batch_id, str)
        assert [chemin.name for chemin, _ in organizer.last_failures] == ["rapport.pdf"]
        assert "non journalisé" in organizer.last_failures[0][1]
        assert (root / "Documents" / "PDF" / "rapport.pdf").is_file()
    finally:
        verrou.close()
        connexion.close()


def test_undo_marque_chaque_ligne_des_son_retour(conn, tmp_path, root, monkeypatch):
    """Constat 3 : `undone` n'était posé qu'à la fin, donc une annulation
    interrompue laissait un journal définitivement faux."""
    source = tmp_path / "Entrée"
    for index in range(4):
        _write(source / f"doc{index}.pdf", f"contenu {index}")
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)
    batch_id = organizer.apply(organizer.plan(source))
    monkeypatch.setattr(module_files.shutil, "move", _interrompt_apres(2))

    with pytest.raises(KeyboardInterrupt):
        organizer.undo(batch_id)

    revenus = sorted(chemin.name for chemin in source.iterdir())
    assert len(revenus) == 2
    restantes = conn.execute("SELECT COUNT(*) AS n FROM file_moves WHERE undone = 0").fetchone()["n"]
    assert restantes == 2  # seules les lignes non traitées restent ouvertes
    assert [lot.count for lot in organizer.batches()] == [2]

    # La reprise remet les deux derniers sans inventer d'échec.
    monkeypatch.setattr(module_files.shutil, "move", _VRAI_MOVE)
    assert organizer.undo(batch_id) == 2
    assert organizer.last_failures == []
    assert organizer.batches() == []


def test_undo_solde_une_ligne_dont_la_source_est_deja_revenue(conn, tmp_path, root):
    """Constat 3 : un fichier déjà revenu provoquait un message mensonger et
    une ligne bloquée à `undone = 0` pour toujours."""
    source = tmp_path / "Entrée"
    _write(source / "rapport.pdf", "texte")
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)
    batch_id = organizer.apply(organizer.plan(source))
    # L'utilisateur (ou une annulation interrompue) a remis le fichier lui-même.
    _VRAI_MOVE(str(root / "Documents" / "PDF" / "rapport.pdf"), str(source / "rapport.pdf"))

    restaures = organizer.undo(batch_id)

    assert restaures == 0  # rien n'a été déplacé, et c'est normal
    assert organizer.last_failures == []
    assert organizer.batches() == []  # le lot ne réapparaît plus


def test_undo_distingue_une_source_occupee(conn, tmp_path, root):
    source = tmp_path / "Entrée"
    _write(source / "rapport.pdf", "original")
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)
    batch_id = organizer.apply(organizer.plan(source))
    _write(source / "rapport.pdf", "un autre fichier a pris la place")

    assert organizer.undo(batch_id) == 0
    assert "occupé par un autre fichier" in organizer.last_failures[0][1]
    assert [lot.count for lot in organizer.batches()] == [1]  # rien n'est soldé à tort


def test_apply_efface_une_copie_inachevee_vers_un_autre_volume(conn, tmp_path, root, monkeypatch):
    """Constat 4 : une copie interrompue laissait à destination une archive
    tronquée portant le nom canonique, que rien ne signalait."""
    source = tmp_path / "Entrée"
    _write(source / "sauvegarde.zip", "Z" * 3000)
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)
    plan = organizer.plan(source)

    def copie_saturee(_source, destination, **_extra):
        # `shutil.move` vers un autre volume copie puis supprime : ici le volume
        # de destination est saturé au milieu de la copie.
        Path(destination).write_text("Z" * 1000, encoding="utf-8")
        raise OSError(27, "File too large")

    monkeypatch.setattr(module_files.shutil, "move", copie_saturee)

    organizer.apply(plan)

    assert [chemin.name for chemin, _ in organizer.last_failures] == ["sauvegarde.zip"]
    assert (source / "sauvegarde.zip").read_text(encoding="utf-8") == "Z" * 3000
    assert not (root / "Archives" / "sauvegarde.zip").exists()
    assert conn.execute("SELECT COUNT(*) AS n FROM file_moves").fetchone()["n"] == 0


def test_apply_journalise_un_deplacement_reussi_malgre_une_erreur(conn, tmp_path, root, monkeypatch):
    """La source a disparu et la cible est en place : le déplacement a eu lieu,
    il doit être journalisé plutôt que compté comme un échec."""
    source = tmp_path / "Entrée"
    _write(source / "rapport.pdf", "texte")
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)
    plan = organizer.plan(source)
    vrai = _VRAI_MOVE

    def deplace_puis_rale(origine, destination, **extra):
        vrai(origine, destination, **extra)
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(module_files.shutil, "move", deplace_puis_rale)

    organizer.apply(plan)

    assert organizer.last_failures == []
    assert (root / "Documents" / "PDF" / "rapport.pdf").is_file()
    assert [lot.count for lot in organizer.batches()] == [1]


# --- Robustesse du plan et du parcours ---------------------------------------


def test_plan_ecarte_un_seul_fichier_dont_la_destination_est_inutilisable(conn, tmp_path, root):
    """Constat 14 : un chemin trop long faisait échouer toute l'analyse, avec un
    message qui accusait le dossier source."""
    source = tmp_path / "Entrée"
    _write(source / "facture.pdf")
    _write(source / "photo.jpg")
    root.mkdir(parents=True, exist_ok=True)  # la racine existe : cas d'un 2e rangement
    regles = [Rule("Trop long", "A" * 300, ["pdf"]), *DEFAULT_RULES]
    organizer = FileOrganizer(conn, regles, root)

    moves = organizer.plan(source)

    # L'autre fichier reste proposé, et le fautif est nommé.
    assert [move.src.name for move in moves] == ["photo.jpg"]
    assert [chemin.name for chemin, _ in organizer.last_failures] == ["facture.pdf"]
    assert "inutilisable" in organizer.last_failures[0][1]


@pytest.fixture()
def stat_facon_windows(monkeypatch):
    """Reproduit Windows : un nom invalide y est « absent » au lieu de lever une erreur.

    Python y compte ERROR_INVALID_NAME parmi les erreurs à ignorer : `exists()`
    et `is_symlink()` répondent `False` là où Linux lève ENAMETOOLONG. Sans ce
    montage, la suite passe sous Linux sur un code qui échoue sous Windows.
    """
    for nom in ("exists", "is_symlink"):
        origine = getattr(Path, nom)

        def silencieux(self, *args, _origine=origine, **kwargs):
            try:
                return _origine(self, *args, **kwargs)
            except OSError:
                return False

        monkeypatch.setattr(Path, nom, silencieux)


@pytest.mark.parametrize("racine_existe", [True, False])
def test_plan_ecarte_un_nom_trop_long_meme_si_exists_ne_leve_rien(
    conn, tmp_path, root, stat_facon_windows, racine_existe
):
    """Sous Windows, le plan proposait le fichier vers un dossier impossible à
    créer, faute d'erreur levée par `exists()`."""
    source = tmp_path / "Entrée"
    _write(source / "facture.pdf")
    _write(source / "photo.jpg")
    if racine_existe:
        root.mkdir(parents=True)
    organizer = FileOrganizer(conn, [Rule("Trop long", "A" * 300, ["pdf"]), *DEFAULT_RULES], root)

    moves = organizer.plan(source)

    assert [move.src.name for move in moves] == ["photo.jpg"]
    assert [chemin.name for chemin, _ in organizer.last_failures] == ["facture.pdf"]
    assert "inutilisable" in organizer.last_failures[0][1]


def test_le_suffixe_de_collision_ne_fait_pas_depasser_la_limite(tmp_path, stat_facon_windows):
    """« nom (2).pdf » dépasse la limite quand « nom.pdf » l'atteignait tout juste.

    La place est prise via `claimed`, pour ne pas avoir à créer sur le disque un
    fichier au nom de 255 caractères.
    """
    nom = "a" * (module_files.MAX_NAME_LENGTH - len(".pdf")) + ".pdf"
    dossier = tmp_path / "PDF"
    claimed = {module_files._claim_key(dossier / nom)}

    with pytest.raises(OSError, match="inutilisable"):
        module_files._free_path(dossier, nom, claimed)


def test_un_nom_a_la_limite_reste_accepte(tmp_path, monkeypatch):
    # Le chemin complet dépasse 259 caractères : sur un Windows aux réglages
    # d'usine, c'est la limite de chemin qui répondrait, pas celle du nom.
    monkeypatch.setattr(module_files, "_long_paths_enabled", lambda: True)
    nom = "a" * (module_files.MAX_NAME_LENGTH - len(".pdf")) + ".pdf"
    assert module_files._free_path(tmp_path, nom, set()) == tmp_path / nom


@pytest.mark.parametrize(
    ("nom", "sous_windows", "ailleurs"),
    [
        ("a" * 10, 10, 10),
        ("é" * 200, 200, 400),  # une unité UTF-16, deux octets UTF-8
        ("😀" * 10, 20, 40),  # hors du plan de base : paire de substitution, quatre octets
    ],
)
def test_longueur_de_nom_selon_le_systeme(monkeypatch, nom, sous_windows, ailleurs):
    """NTFS compte en unités UTF-16, ext4 en octets : 200 « é » passent sous
    Windows et pas sous Linux."""
    monkeypatch.setattr(module_files, "_windows", lambda: True)
    assert module_files._name_length(nom) == sous_windows
    monkeypatch.setattr(module_files, "_windows", lambda: False)
    assert module_files._name_length(nom) == ailleurs


# --- Longueur des chemins sous Windows sans chemins longs ---------------------


@pytest.fixture()
def windows_sans_chemins_longs(monkeypatch):
    """Réglage par défaut de Windows 10 et 11 : LongPathsEnabled vaut 0."""
    monkeypatch.setattr(module_files, "_windows", lambda: True)
    monkeypatch.setattr(module_files, "_long_paths_enabled", lambda: False)


def _chemin(dossier: int, fichier: int) -> Path:
    """Chemin absolu dont le dossier fait `dossier` caractères et le nom `fichier`.

    La racine vient d'`abspath` : « / », ou « D:\\ » sous Windows, où un « / »
    seul recevrait la lettre du lecteur courant et décalerait les bornes.
    """
    racine = os.path.abspath(os.sep)
    return Path(racine + "d" * (dossier - len(racine))) / ("f" * fichier)


@pytest.mark.parametrize(
    ("dossier", "fichier"),
    [(200, 58), (247, 5)],  # 259 caractères au total ; dossier de 247
)
def test_chemin_juste_sous_les_limites_accepte(windows_sans_chemins_longs, dossier, fichier):
    module_files._check_path_lengths(_chemin(dossier, fichier))


@pytest.mark.parametrize(
    ("dossier", "fichier", "motif"),
    [
        (200, 59, "chemin complet"),  # 260 caractères : MAX_PATH atteint
        (248, 5, "dossier de destination"),  # CreateDirectoryW refuse 248 et plus
    ],
)
def test_chemin_au_dela_des_limites_refuse(windows_sans_chemins_longs, dossier, fichier, motif):
    with pytest.raises(OSError, match=motif):
        module_files._check_path_lengths(_chemin(dossier, fichier))


def test_dossier_existant_au_dela_de_248_accepte(windows_sans_chemins_longs, monkeypatch):
    """La limite de 248 vaut pour CRÉER un dossier ; un fichier se crée dans un
    dossier existant tant que le chemin complet reste sous 260.

    L'existence est simulée : sans chemins longs, Windows refuserait justement
    de créer ce dossier pour le test.
    """
    chemin = _chemin(250, 5)
    dossier = os.path.dirname(os.path.abspath(chemin))
    vrai_isdir = os.path.isdir
    monkeypatch.setattr(module_files.os.path, "isdir", lambda p: os.fspath(p) == dossier or vrai_isdir(p))
    module_files._check_path_lengths(chemin)


def test_chemins_longs_actives_levent_la_limite(monkeypatch):
    monkeypatch.setattr(module_files, "_windows", lambda: True)
    monkeypatch.setattr(module_files, "_long_paths_enabled", lambda: True)
    module_files._check_path_lengths(_chemin(248, 59))


def test_plan_ecarte_une_destination_au_dela_de_max_path(conn, tmp_path, root, windows_sans_chemins_longs):
    """Chaque composant tient sous 255, mais le chemin complet dépasse 259 : sous
    Windows par défaut, le dossier ne pourra pas être créé. `exists()` y répond
    « absent » plutôt que de lever, donc seul un contrôle explicite l'aperçoit."""
    source = tmp_path / "Entrée"
    long_nom = "r" * 100 + ".pdf"
    _write(source / long_nom)
    _write(source / "photo.jpg")
    organizer = FileOrganizer(conn, [Rule("Profond", "D" * 150, ["pdf"]), *DEFAULT_RULES], root)

    moves = organizer.plan(source)

    assert [move.src.name for move in moves] == ["photo.jpg"]
    assert [chemin.name for chemin, _ in organizer.last_failures] == [long_nom]
    assert "chemin complet" in organizer.last_failures[0][1]


# --- Noms contenant une moitié d'emoji isolée ----------------------------------


def test_une_moitie_d_emoji_isolee_est_comptee_sous_windows(monkeypatch):
    """NTFS accepte une moitié d'emoji isolée, laissée par un navigateur qui a
    tronqué un nom ; `listdir` la rend telle quelle. Le décompte ne doit pas lever."""
    monkeypatch.setattr(module_files, "_windows", lambda: True)
    assert module_files._name_length("photo \ud83d.pdf") == len("photo .pdf") + 1


def test_un_nom_non_encodable_devient_un_emplacement_inutilisable(monkeypatch):
    """Une UnicodeError est une ValueError, que `plan()` ne rattrape pas : elle
    doit devenir une OSError, qui écarte le seul fichier fautif.

    Le décompte est simulé : selon la plateforme, `os.fsencode` lève ou non sur
    une moitié d'emoji (Windows la laisse passer, Linux la refuse).
    """

    def decompte(nom: str) -> int:
        raise UnicodeEncodeError("utf-8", nom, 0, 1, "surrogates not allowed")

    monkeypatch.setattr(module_files, "_name_length", decompte)
    with pytest.raises(OSError, match="inutilisable"):
        module_files._check_path_lengths(Path(os.path.abspath(os.sep)) / "photo.pdf")


def test_plan_ecarte_seulement_le_fichier_au_nom_illisible(conn, tmp_path, root, monkeypatch):
    """Un nom que le décompte ne sait pas encoder arrêtait l'analyse de TOUT le
    dossier, avec un message qui ne nommait pas le fichier."""
    source = tmp_path / "Entrée"
    _write(source / "fautif.pdf")
    _write(source / "sain.pdf")
    vrai = module_files._name_length

    def decompte(nom: str) -> int:
        if "fautif" in nom:
            raise UnicodeEncodeError("utf-16-le", nom, 0, 1, "surrogates not allowed")
        return vrai(nom)

    monkeypatch.setattr(module_files, "_name_length", decompte)
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)

    moves = organizer.plan(source)

    assert [move.src.name for move in moves] == ["sain.pdf"]
    assert [chemin.name for chemin, _ in organizer.last_failures] == ["fautif.pdf"]


# --- Chemins relatifs et annulation ------------------------------------------


def test_annulation_revient_au_meme_endroit_malgre_un_dossier_relatif(conn, tmp_path, root, monkeypatch):
    """Un dossier source relatif était journalisé tel quel. Relancée depuis un
    autre dossier courant, l'annulation renvoyait le fichier ailleurs et
    soldait le lot : plus aucun moyen de le retrouver par l'application."""
    base = tmp_path / "base"
    _write(base / "Entrée" / "facture.pdf")
    monkeypatch.chdir(base)
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)
    plan = organizer.plan("Entrée")
    assert plan and all(move.src.is_absolute() for move in plan)
    batch_id = organizer.apply(plan)

    ailleurs = tmp_path / "ailleurs"
    ailleurs.mkdir()
    monkeypatch.chdir(ailleurs)  # l'application relancée depuis un autre dossier

    assert organizer.undo(batch_id) == 1
    assert (base / "Entrée" / "facture.pdf").is_file()
    assert not (ailleurs / "Entrée").exists()


def test_racine_de_rangement_relative_rendue_absolue(conn, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    organizer = FileOrganizer(conn, DEFAULT_RULES, "Rangé")
    assert organizer.target_root == tmp_path / "Rangé"


def test_apply_refuse_un_deplacement_relatif(conn, tmp_path, monkeypatch):
    """Défense en profondeur : un plan construit à la main avec un chemin relatif
    ne doit jamais atteindre le journal."""
    monkeypatch.chdir(tmp_path)
    _write(tmp_path / "a.pdf")
    organizer = FileOrganizer(conn, DEFAULT_RULES, tmp_path / "R")

    organizer.apply([module_files.PlannedMove(src=Path("a.pdf"), dst=tmp_path / "R" / "a.pdf", rule="x")])

    assert (tmp_path / "a.pdf").is_file()
    assert "relatif" in organizer.last_failures[0][1]
    assert conn.execute("SELECT COUNT(*) AS n FROM file_moves").fetchone()["n"] == 0


# --- Déplacement interrompu : ne jamais effacer ce qui peut être la seule copie


def _journal(conn) -> list[tuple[str, str]]:
    """Lignes encore annulables du journal, (source, destination)."""
    return [(row["src"], row["dst"]) for row in conn.execute("SELECT src, dst FROM file_moves WHERE undone = 0")]


def _liaison_coupee(monkeypatch, dossier: Path, deplacer) -> None:
    """`shutil.move` remplacé par `deplacer`, et `dossier` illisible : un partage
    réseau ou une clé USB qui ne répond plus."""
    vrai_listdir = os.listdir

    def listdir(chemin="."):
        if str(chemin) == str(dossier):
            raise OSError(64, "Le nom réseau spécifié n'est plus disponible")
        return vrai_listdir(chemin)

    monkeypatch.setattr(module_files.shutil, "move", deplacer)
    monkeypatch.setattr(module_files.os, "listdir", listdir)


def _cle_retiree_pendant_la_copie(monkeypatch, source: Path, fichier: Path, octets: int) -> None:
    """La copie écrit `octets` octets, puis la clé disparaît : l'original ne répond plus."""

    def copie_puis_cle_retiree(_origine, destination, **_extra):
        Path(destination).write_text("V" * octets, encoding="utf-8")
        raise OSError(5, "Input/output error")

    vrai_is_regular = module_files._is_regular
    _liaison_coupee(monkeypatch, source, copie_puis_cle_retiree)
    monkeypatch.setattr(module_files, "_is_regular", lambda p: False if p == fichier else vrai_is_regular(p))


@pytest.mark.parametrize(
    "octets_copies",
    [
        1000,  # copie interrompue : la clé est retirée au milieu
        5000,  # copie complète, mais la suppression de l'original échoue : la clé a disparu
    ],
)
def test_source_injoignable_copie_gardee_et_journalisee(conn, tmp_path, root, monkeypatch, octets_copies):
    """Une source qui ne répond plus n'a pas forcément disparu, ni survécu. La
    copie est gardée (l'effacer perdrait peut-être la seule), renommée pour ne
    pas passer pour un rangement réussi, et journalisée pour rester annulable."""
    source = tmp_path / "Clé"
    fichier = _write(source / "vacances.mp4", "V" * 5000)
    organizer = FileOrganizer(conn, [Rule("Vidéos", "Videos", ["mp4"])], root)
    plan = organizer.plan(source)
    _cle_retiree_pendant_la_copie(monkeypatch, source, fichier, octets_copies)

    organizer.apply(plan)

    gardee = root / "Videos" / "vacances (copie à vérifier).mp4"
    assert [chemin.name for chemin, _ in organizer.last_failures] == ["vacances.mp4"]
    assert "copie est conservée" in organizer.last_failures[0][1]
    assert organizer.last_kept_copies == [gardee]
    assert not (root / "Videos" / "vacances.mp4").exists()
    assert gardee.read_text(encoding="utf-8") == "V" * octets_copies
    assert _journal(conn) == [(str(fichier), str(gardee))]
    assert fichier.read_text(encoding="utf-8") == "V" * 5000


def test_coupure_reseau_apres_suppression_de_l_original_copie_gardee_et_annulable(conn, tmp_path, root, monkeypatch):
    """Constat bloquant, relevé deux fois : le partage exécute la suppression de
    l'original, puis la liaison tombe avant la réponse. Effacer la copie perdait
    le fichier ; ne pas la journaliser le rendait inannulable, alors qu'il
    l'était sur main. Gardée et journalisée, elle revient à la place de
    l'original dès que la liaison est rétablie."""
    source = tmp_path / "NAS"
    fichier = _write(source / "contrat.pdf", "C" * 4000)
    organizer = FileOrganizer(conn, [Rule("Contrats", "Contrats", ["pdf"])], root)
    plan = organizer.plan(source)

    def deplace_puis_liaison_coupee(origine, destination, **extra):
        _VRAI_MOVE(origine, destination, **extra)  # copie ET suppression de l'original
        raise OSError(64, "Le nom réseau spécifié n'est plus disponible")

    _liaison_coupee(monkeypatch, source, deplace_puis_liaison_coupee)
    batch_id = organizer.apply(plan)

    gardee = root / "Contrats" / "contrat (copie à vérifier).pdf"
    assert not fichier.exists()  # l'original a bien disparu du partage
    assert gardee.read_text(encoding="utf-8") == "C" * 4000  # la seule copie survit
    assert "copie est conservée" in organizer.last_failures[0][1]

    monkeypatch.undo()  # la liaison est rétablie
    assert [lot.count for lot in organizer.batches()] == [1]
    assert organizer.undo(batch_id) == 1
    assert fichier.read_text(encoding="utf-8") == "C" * 4000
    assert not gardee.exists()
    assert organizer.batches() == []


def test_original_revenu_l_annulation_n_ecrase_rien_puis_se_solde(conn, tmp_path, root, monkeypatch):
    """La clé est rebranchée : l'original est là, intact, et la copie gardée à
    côté. L'annulation refuse d'écraser ; une fois le doublon écarté par
    l'utilisateur, elle solde le lot au lieu de le proposer sans fin."""
    source = tmp_path / "Clé"
    fichier = _write(source / "vacances.mp4", "V" * 5000)
    organizer = FileOrganizer(conn, [Rule("Vidéos", "Videos", ["mp4"])], root)
    plan = organizer.plan(source)
    _cle_retiree_pendant_la_copie(monkeypatch, source, fichier, 1000)
    batch_id = organizer.apply(plan)
    monkeypatch.undo()  # la clé est rebranchée

    gardee = root / "Videos" / "vacances (copie à vérifier).mp4"
    assert organizer.undo(batch_id) == 0
    assert "occupé par un autre fichier" in organizer.last_failures[0][1]
    assert fichier.read_text(encoding="utf-8") == "V" * 5000  # rien n'est écrasé
    assert gardee.read_text(encoding="utf-8") == "V" * 1000

    gardee.unlink()  # l'utilisateur a vérifié l'original et écarte le doublon
    assert organizer.undo(batch_id) == 0
    assert organizer.last_failures == []
    assert organizer.batches() == []


def test_original_disparu_et_copie_incomplete_la_copie_est_gardee(conn, tmp_path, root, monkeypatch):
    """L'original a disparu (dossier lisible, fichier absent), mais la copie n'a
    pas la taille relevée : ce n'est pas un succès, et c'est pourtant la seule
    trace qui reste. On la garde, et on la journalise."""
    source = tmp_path / "Entrée"
    fichier = _write(source / "rapport.pdf", "R" * 3000)
    organizer = FileOrganizer(conn, [Rule("PDF", "PDF", ["pdf"])], root)
    plan = organizer.plan(source)

    def copie_partielle_puis_original_supprime(origine, destination, **_extra):
        Path(destination).write_text("R" * 1000, encoding="utf-8")
        Path(origine).unlink()
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(module_files.shutil, "move", copie_partielle_puis_original_supprime)

    organizer.apply(plan)

    gardee = root / "PDF" / "rapport (copie à vérifier).pdf"
    assert not fichier.exists()
    assert gardee.read_text(encoding="utf-8") == "R" * 1000
    assert "copie est conservée" in organizer.last_failures[0][1]
    assert _journal(conn) == [(str(fichier), str(gardee))]


def test_copie_gardee_sous_son_nom_si_le_renommage_echoue(conn, tmp_path, root, monkeypatch):
    """Le renommage n'est qu'un signal : s'il échoue, la copie reste sous son nom
    et le message la désigne, mais rien n'est effacé."""
    source = tmp_path / "NAS"
    fichier = _write(source / "contrat.pdf", "C" * 4000)
    organizer = FileOrganizer(conn, [Rule("Contrats", "Contrats", ["pdf"])], root)
    plan = organizer.plan(source)

    def deplace_puis_liaison_coupee(origine, destination, **extra):
        _VRAI_MOVE(origine, destination, **extra)
        raise OSError(64, "Le nom réseau spécifié n'est plus disponible")

    def renommage_refuse(*_args, **_kwargs):
        raise PermissionError(13, "Accès refusé")

    _liaison_coupee(monkeypatch, source, deplace_puis_liaison_coupee)
    monkeypatch.setattr(module_files.os, "rename", renommage_refuse)

    organizer.apply(plan)

    gardee = root / "Contrats" / "contrat.pdf"
    assert gardee.read_text(encoding="utf-8") == "C" * 4000
    assert str(gardee) in organizer.last_failures[0][1]
    assert _journal(conn) == [(str(fichier), str(gardee))]


def _unlink_facon_windows(monkeypatch) -> None:
    """Windows refuse d'effacer un fichier en lecture seule ; Linux l'accepte."""
    vrai_unlink = Path.unlink

    def unlink(self, missing_ok=False):
        try:
            mode = self.stat().st_mode
        except FileNotFoundError:
            mode = None
        if mode is not None and not mode & stat.S_IWRITE:
            raise PermissionError(13, "Accès refusé", str(self))
        return vrai_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)


def test_copie_en_lecture_seule_retiree_quand_l_original_reste(conn, tmp_path, root, monkeypatch):
    """Constat : vers un autre volume, la copie reprend l'attribut lecture seule
    de l'original, que Windows refuse d'effacer. L'échec était avalé : la copie
    restait sous le nom canonique, et chaque essai en ajoutait une autre."""
    source = tmp_path / "Clé"
    fichier = _write(source / "facture.pdf", "F" * 2000)
    os.chmod(fichier, stat.S_IREAD)
    organizer = FileOrganizer(conn, [Rule("PDF", "PDF", ["pdf"])], root)
    plan = organizer.plan(source)

    def copie_puis_original_indelebile(origine, destination, **_extra):
        shutil.copy2(origine, destination)  # recopie l'attribut lecture seule
        raise PermissionError(13, "Accès refusé", str(origine))

    monkeypatch.setattr(module_files.shutil, "move", copie_puis_original_indelebile)
    _unlink_facon_windows(monkeypatch)
    try:
        for _essai in range(3):
            organizer.apply(plan)
            assert len(organizer.last_failures) == 1
    finally:
        os.chmod(fichier, stat.S_IREAD | stat.S_IWRITE)

    assert list((root / "PDF").iterdir()) == []  # ni copie, ni doublons accumulés
    assert _journal(conn) == []
    assert fichier.read_text(encoding="utf-8") == "F" * 2000


def test_copie_en_trop_indelebile_renommee_quand_l_original_reste(conn, tmp_path, root, monkeypatch):
    """Si notre copie résiste vraiment à l'effacement (partage coupé), elle ne
    doit pas rester sous le nom canonique, où elle passerait pour un rangement
    réussi : elle est renommée et le message la désigne."""
    source = tmp_path / "Entrée"
    fichier = _write(source / "facture.pdf", "F" * 2000)
    organizer = FileOrganizer(conn, [Rule("PDF", "PDF", ["pdf"])], root)
    plan = organizer.plan(source)
    cible = root / "PDF" / "facture.pdf"

    def copie_puis_echec(origine, destination, **_extra):
        shutil.copy2(origine, destination)
        raise OSError(5, "Input/output error")

    vrai_unlink = Path.unlink

    def unlink(self, missing_ok=False):
        if self == cible:
            raise OSError(64, "Le nom réseau spécifié n'est plus disponible")
        return vrai_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(module_files.shutil, "move", copie_puis_echec)
    monkeypatch.setattr(Path, "unlink", unlink)

    organizer.apply(plan)

    en_trop = root / "PDF" / "facture (copie à vérifier).pdf"
    assert not cible.exists()
    assert en_trop.is_file()
    assert "copie en trop" in organizer.last_failures[0][1]
    assert str(en_trop) in organizer.last_failures[0][1]
    assert _journal(conn) == []
    assert fichier.read_text(encoding="utf-8") == "F" * 2000


def test_annulation_d_un_fichier_ouvert_ne_laisse_ni_doublon_ni_lot_bloque(conn, tmp_path, root, monkeypatch):
    """Constat : un fichier rangé puis ouvert dans Word. Le renommage bute sur le
    verrou, `shutil.move` se replie sur une copie, puis ne peut pas effacer le
    fichier ouvert. Une copie restait sous le nom d'origine, et chaque
    annulation suivante était refusée : « occupé par un autre fichier »."""
    source = tmp_path / "Entrée"
    fichier = _write(source / "rapport.docx", "W" * 3000)
    organizer = FileOrganizer(conn, [Rule("Docs", "Docs", ["docx"])], root)
    batch_id = organizer.apply(organizer.plan(source))
    range_ = root / "Docs" / "rapport.docx"
    assert range_.is_file()

    def copie_puis_verrou(origine, destination, **_extra):
        shutil.copy2(origine, destination)  # Word autorise la lecture
        raise PermissionError(32, "Le processus ne peut pas accéder au fichier", str(origine))

    monkeypatch.setattr(module_files.shutil, "move", copie_puis_verrou)
    assert organizer.undo(batch_id) == 0
    assert not fichier.exists()  # aucun doublon sous le nom d'origine
    assert range_.read_text(encoding="utf-8") == "W" * 3000
    assert [lot.count for lot in organizer.batches()] == [1]

    monkeypatch.undo()  # Word est fermé
    assert organizer.undo(batch_id) == 1
    assert fichier.read_text(encoding="utf-8") == "W" * 3000
    assert not range_.exists()


def test_annulation_incertaine_solde_son_lot_et_libere_les_plus_anciens(conn, tmp_path, root, monkeypatch):
    """Constat (régression) : pendant une annulation, le serveur supprime le
    fichier rangé puis la liaison tombe. La copie revenue était renommée et la
    ligne restait ouverte : chaque annulation suivante butait sur « introuvable »,
    et l'interface, qui annule le lot le plus récent, n'atteignait plus les
    lots plus anciens. Sur main, la ligne se soldait."""
    source = tmp_path / "Entrée"
    ancien = _write(source / "ancien.pdf", "A" * 1000)
    regles = [Rule("Docs", "Docs", ["pdf", "docx"])]
    lot_ancien = FileOrganizer(conn, regles, root)
    lot_ancien_id = lot_ancien.apply(lot_ancien.plan(source))
    _write(source / "rapport.docx", "W" * 3000)
    organizer = FileOrganizer(conn, regles, root)
    batch_id = organizer.apply(organizer.plan(source))
    range_ = root / "Docs" / "rapport.docx"

    def retour_puis_liaison_coupee(origine, destination, **extra):
        _VRAI_MOVE(origine, destination, **extra)  # copie ET suppression du fichier rangé
        raise OSError(64, "Le nom réseau spécifié n'est plus disponible")

    _liaison_coupee(monkeypatch, root / "Docs", retour_puis_liaison_coupee)
    assert organizer.undo(batch_id) == 0
    monkeypatch.undo()  # la liaison est rétablie

    revenue = source / "rapport (copie à vérifier).docx"
    assert not range_.exists()
    assert revenue.read_text(encoding="utf-8") == "W" * 3000  # rien n'est perdu
    assert organizer.last_kept_copies == [revenue]
    assert [lot.batch_id for lot in organizer.batches()] == [lot_ancien_id]  # le lot ancien redevient accessible
    assert organizer.undo(lot_ancien_id) == 1
    assert ancien.read_text(encoding="utf-8") == "A" * 1000


def test_annulation_incertaine_ne_perd_pas_le_fichier_range_encore_present(conn, tmp_path, root, monkeypatch):
    """Solder la ligne ne doit rien perdre quand le fichier rangé existait encore,
    simplement injoignable : il reste en place, à côté de la copie revenue."""
    source = tmp_path / "Entrée"
    fichier = _write(source / "rapport.docx", "W" * 3000)
    organizer = FileOrganizer(conn, [Rule("Docs", "Docs", ["docx"])], root)
    batch_id = organizer.apply(organizer.plan(source))
    range_ = root / "Docs" / "rapport.docx"

    def copie_puis_liaison_coupee(origine, destination, **_extra):
        shutil.copy2(origine, destination)  # le fichier rangé n'est PAS supprimé
        raise OSError(64, "Le nom réseau spécifié n'est plus disponible")

    vrai_is_regular = module_files._is_regular
    _liaison_coupee(monkeypatch, root / "Docs", copie_puis_liaison_coupee)
    monkeypatch.setattr(module_files, "_is_regular", lambda p: False if p == range_ else vrai_is_regular(p))
    organizer.undo(batch_id)
    monkeypatch.undo()

    assert range_.read_text(encoding="utf-8") == "W" * 3000
    assert (source / "rapport (copie à vérifier).docx").read_text(encoding="utf-8") == "W" * 3000
    assert not fichier.exists()  # aucun des deux ne se fait passer pour l'original revenu
    assert any(str(range_) in message for _, message in organizer.last_failures)


def test_le_fichier_d_un_autre_logiciel_apparu_a_la_cible_n_est_jamais_efface(conn, tmp_path, root, monkeypatch):
    """Course de quelques microsecondes : un autre logiciel dépose un fichier en
    lecture seule à la cible. Le retrait de la lecture seule, prévu pour NOTRE
    copie, l'aurait effacé ; la date de modification, que `copy2` recopie à
    l'identique, le distingue d'une copie de l'original."""
    source = tmp_path / "Entrée"
    fichier = _write(source / "facture.pdf", "F" * 2000)
    organizer = FileOrganizer(conn, [Rule("PDF", "PDF", ["pdf"])], root)
    plan = organizer.plan(source)

    def intrus_puis_echec(_origine, destination, **_extra):
        intrus = Path(destination)
        intrus.write_text("CONTENU D'UN AUTRE LOGICIEL", encoding="utf-8")
        os.utime(intrus, ns=(10**9, 10**9))  # une date qui n'est pas celle de l'original
        os.chmod(intrus, stat.S_IREAD)
        raise PermissionError(13, "Accès refusé", str(destination))

    monkeypatch.setattr(module_files.shutil, "move", intrus_puis_echec)
    _unlink_facon_windows(monkeypatch)

    organizer.apply(plan)

    restants = list((root / "PDF").iterdir())
    try:
        assert [p.read_text(encoding="utf-8") for p in restants] == ["CONTENU D'UN AUTRE LOGICIEL"]
    finally:
        for chemin in restants:
            os.chmod(chemin, stat.S_IREAD | stat.S_IWRITE)
    assert fichier.read_text(encoding="utf-8") == "F" * 2000


def test_destination_illisible_le_message_nomme_les_deux_emplacements(conn, tmp_path, root, monkeypatch):
    """Renommage exécuté par le serveur, puis liaison coupée : ni la source ni la
    destination ne répondent. Le message ne citait que la source, alors que le
    fichier était peut-être déjà rangé."""
    source = tmp_path / "Partage"
    fichier = _write(source / "bilan.xlsx", "B" * 1000)
    organizer = FileOrganizer(conn, [Rule("Tableurs", "Tableurs", ["xlsx"])], root)
    plan = organizer.plan(source)
    cible = root / "Tableurs" / "bilan.xlsx"

    def deplace_puis_liaison_coupee(origine, destination, **extra):
        _VRAI_MOVE(origine, destination, **extra)
        raise OSError(64, "Le nom réseau spécifié n'est plus disponible")

    vrai_is_regular = module_files._is_regular
    _liaison_coupee(monkeypatch, source, deplace_puis_liaison_coupee)
    monkeypatch.setattr(module_files, "_is_regular", lambda p: False if p == cible else vrai_is_regular(p))

    organizer.apply(plan)

    message = organizer.last_failures[0][1]
    assert str(fichier) in message
    assert str(cible) in message


# --- Destinations saisies avec des espaces ou un point final -----------------


@pytest.mark.parametrize(
    ("saisie", "attendu"),
    [
        ("Documents / Factures", "Documents/Factures"),
        ("  Admin /  Banque  ", "Admin/Banque"),
        ("Documents\\ PDF ", "Documents/PDF"),
        # Espace insécable : Win32 la conserve, elle peut faire partie du nom.
        ("Documents/ Factures", "Documents/ Factures"),
        # Point final : Win32 le retire de chaque segment, de façon cohérente.
        ("Clients/ACME Inc./Contrats", "Clients/ACME Inc./Contrats"),
    ],
)
def test_destination_nettoyee_des_espaces_autour_des_dossiers(saisie, attendu):
    """Win32 ne retire les espaces finales que du dernier segment : « Documents /
    Factures » faisait échouer chaque fichier de la règle sous Windows."""
    assert Rule("Test", saisie, ["pdf"]).destination == attendu


def test_destination_refuse_un_dossier_sans_nom():
    with pytest.raises(ValueError, match="sans nom"):
        Rule("Test", "Documents/ /PDF", ["pdf"])


def test_regle_a_point_final_gardee_au_chargement(tmp_path):
    """Constat : refuser le point final écartait au chargement des règles qui
    fonctionnaient, et l'éditeur de règles les effaçait ensuite du fichier."""
    chemin = tmp_path / "regles.json"
    regle = {"name": "ACME", "destination": "Clients/ACME Inc.", "extensions": [], "patterns": ["*acme*"]}
    chemin.write_text(json.dumps([regle]), encoding="utf-8")

    rapport = read_rules(chemin)

    assert rapport.errors == []
    assert [r.destination for r in rapport.rules] == ["Clients/ACME Inc."]


def test_iter_files_ecarte_les_dossiers_caches_par_attribut(tmp_path, monkeypatch):
    """Constat 16 : le parcours récursif n'écartait que les dossiers commençant
    par un point, convention sans effet sous Windows (AppData, $RECYCLE.BIN)."""
    (tmp_path / "AppData" / "Cache").mkdir(parents=True)
    _write(tmp_path / "AppData" / "Cache" / "cache.dat")
    _write(tmp_path / "visible.dat")
    vrai_cache = module_files._is_hidden
    # Sous Windows l'attribut « caché » remplace la convention du point.
    monkeypatch.setattr(
        module_files, "_is_hidden", lambda chemin: chemin.name == "AppData" or vrai_cache(chemin)
    )

    trouves = [chemin.name for chemin in module_files._iter_files(tmp_path, recursive=True)]

    assert trouves == ["visible.dat"]


def test_find_duplicates_ignore_deux_noms_d_un_meme_fichier(conn, tmp_path, root):
    """Constat 15 : un lien dur (ou une jonction Windows) était présenté comme un
    doublon, invitant l'utilisateur à supprimer son unique copie."""
    dossier = tmp_path / "Photos"
    original = _write(dossier / "photo.jpg", "contenu de la photo")
    os.link(original, dossier / "photo-copie.jpg")
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)

    assert organizer.find_duplicates(dossier) == []

    # Une vraie copie, elle, doit bien être signalée.
    _write(dossier / "photo-bis.jpg", "contenu de la photo")
    groupes = organizer.find_duplicates(dossier)
    assert len(groupes) == 1
    assert "photo-bis.jpg" in {chemin.name for chemin in groupes[0]}


def test_find_duplicates_separe_sur_le_prefixe_sans_tout_lire(conn, tmp_path, root, monkeypatch):
    """Deux gros fichiers de même taille au début différent ne doivent pas être
    lus en entier (constat 6 : le hachage intégral figeait la fenêtre)."""
    dossier = tmp_path / "Vidéos"
    taille = module_files.PREFIX_SIZE * 3
    _write(dossier / "a.mp4", "A" + "=" * (taille - 1))
    _write(dossier / "b.mp4", "B" + "=" * (taille - 1))
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)
    lus = {"octets": 0}
    vrai_digest = module_files._digest

    def compte(chemin, limite=None):
        lus["octets"] += limite if limite is not None else chemin.stat().st_size
        return vrai_digest(chemin, limite)

    monkeypatch.setattr(module_files, "_digest", compte)

    assert organizer.find_duplicates(dossier) == []
    assert lus["octets"] <= module_files.PREFIX_SIZE * 2  # jamais les 384 Kio complets


def test_operations_longues_s_arretent_sur_demande(conn, tmp_path, root):
    """Constats 6 et 10 : les opérations doivent pouvoir être annulées."""
    source = tmp_path / "Entrée"
    for index in range(5):
        _write(source / f"doc{index}.pdf", f"contenu {index}")
    organizer = FileOrganizer(conn, DEFAULT_RULES, root)

    avancement = Progress()
    avancement.cancel()
    with pytest.raises(module_files.Cancelled):
        organizer.plan(source, progress=avancement)

    plan = organizer.plan(source)
    etapes: list[tuple[str, int, int]] = []
    suivi = Progress(lambda label, fait, total: etapes.append((label, fait, total)))
    batch_id = organizer.apply(plan, progress=suivi)
    assert len(etapes) == 5
    assert etapes[-1][1:] == (5, 5)

    # Une annulation demandée avant le lot laisse le disque et le journal d'accord.
    stop = Progress()
    stop.cancel()
    autre = organizer.apply(plan, progress=stop)
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM file_moves WHERE batch_id = ?", (autre,)
    ).fetchone()["n"] == 0
    assert [lot.batch_id for lot in organizer.batches()] == [batch_id]


# --- Validation des règles ---------------------------------------------------


@pytest.mark.parametrize(
    "destination",
    ["Docs<1>", 'Doc"s', "a|b", "Documents/NUL", "COM1", "Images/LPT9"],
)
def test_rule_refuse_une_destination_interdite_sous_windows(destination):
    with pytest.raises(ValueError):
        Rule("Règle", destination, ["pdf"])


def test_motif_a_crochets_ne_devient_pas_une_classe_de_caracteres(tmp_path):
    regle = Rule("Crochets", "Cible", [], ["*[1]*"])

    assert regle.matches(_write(tmp_path / "photo[1].jpg"))
    assert not regle.matches(_write(tmp_path / "photo1.jpg"))


def test_extension_composee_est_reconnue(tmp_path):
    regle = Rule("Archives tar", "Archives/Tar", ["tar.gz"])

    assert regle.matches(_write(tmp_path / "sauvegarde.tar.gz"))
    assert regle.matches(_write(tmp_path / "base.2026.tar.gz"))
    assert not regle.matches(_write(tmp_path / "sauvegarde.gz"))
