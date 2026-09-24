"""Tests du stockage SQLite : emplacement, migrations et réglages."""

from __future__ import annotations

import json
import sqlite3

import pytest

from organizer import db
from organizer.db import APP_NAME, MIGRATIONS, Settings, connect, data_dir, migrate

TABLES = {"tasks", "notes", "time_entries", "file_moves", "settings"}


@pytest.fixture()
def conn():
    connexion = connect(":memory:")
    yield connexion
    connexion.close()


def _version(connexion: sqlite3.Connection) -> int:
    return connexion.execute("PRAGMA user_version").fetchone()[0]


def _tables(connexion: sqlite3.Connection) -> set[str]:
    rows = connexion.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {row["name"] for row in rows}


# --- Schéma et migrations ----------------------------------------------------


def test_connect_cree_le_schema_complet(conn):
    assert TABLES <= _tables(conn)
    assert _version(conn) == len(MIGRATIONS)


def test_connect_active_les_cles_etrangeres_et_les_lignes_nommees(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("SELECT 1 AS un").fetchone()["un"] == 1


def test_migrate_est_idempotente(conn):
    avant = _tables(conn)

    migrate(conn)
    migrate(conn)

    assert _tables(conn) == avant
    assert _version(conn) == len(MIGRATIONS)


def test_migrate_conserve_les_donnees_existantes(conn):
    with conn:
        conn.execute("INSERT INTO tasks (title, created_at) VALUES ('Garder', '2026-01-01T09:00:00')")

    migrate(conn)

    assert conn.execute("SELECT title FROM tasks").fetchone()["title"] == "Garder"


def test_migrate_ne_touche_pas_une_base_plus_recente(conn):
    future = len(MIGRATIONS) + 4
    conn.execute(f"PRAGMA user_version = {future}")

    migrate(conn)

    assert _version(conn) == future


def test_connect_reouvre_une_base_fichier_sans_rejouer_les_migrations(tmp_path):
    chemin = tmp_path / "organizer.db"
    premiere = connect(chemin)
    with premiere:
        premiere.execute("INSERT INTO tasks (title, created_at) VALUES ('Persistée', '2026-01-01T09:00:00')")
    premiere.close()

    seconde = connect(chemin)
    try:
        assert _version(seconde) == len(MIGRATIONS)
        assert [row["title"] for row in seconde.execute("SELECT title FROM tasks")] == ["Persistée"]
    finally:
        seconde.close()


def test_tasks_priorite_bornee_par_le_schema(conn):
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute("INSERT INTO tasks (title, priority, created_at) VALUES ('X', 4, '2026-01-01T09:00:00')")


# --- Réglages ----------------------------------------------------------------


@pytest.mark.parametrize(
    "valeur",
    [
        7,
        0,
        -3,
        1.5,
        True,
        False,
        None,
        "texte",
        "",
        "accentué : à l'œuvre",
        [],
        [1, "deux", None],
        {},
        {"é": "à", "imbriqué": {"liste": [1, 2], "vrai": True}},
    ],
)
def test_settings_conserve_les_valeurs_json(conn, valeur):
    settings = Settings(conn)

    settings.set("cle", valeur)

    assert settings.get("cle") == valeur
    stocke = conn.execute("SELECT value FROM settings WHERE key = 'cle'").fetchone()["value"]
    assert json.loads(stocke) == valeur


def test_settings_defaut_sur_cle_absente(conn):
    settings = Settings(conn)
    assert settings.get("jamais-ecrite") is None
    assert settings.get("jamais-ecrite", "défaut") == "défaut"
    assert settings.get("jamais-ecrite", 0) == 0


def test_settings_valeur_nulle_stockee_prime_sur_le_defaut(conn):
    settings = Settings(conn)
    settings.set("vide", None)
    assert settings.get("vide", "défaut") is None


def test_settings_ecrase_la_valeur_precedente(conn):
    settings = Settings(conn)
    settings.set("theme", "clair")
    settings.set("theme", "sombre")

    assert settings.get("theme") == "sombre"
    assert conn.execute("SELECT COUNT(*) AS n FROM settings").fetchone()["n"] == 1


def test_settings_cles_independantes(conn):
    settings = Settings(conn)
    settings.set("a", 1)
    settings.set("b", [2])

    assert (settings.get("a"), settings.get("b")) == (1, [2])


def test_settings_partage_la_connexion(conn):
    Settings(conn).set("minuteur", {"focus": 25})
    assert Settings(conn).get("minuteur") == {"focus": 25}


# --- Emplacement des données --------------------------------------------------


def test_data_dir_respecte_organizer_data_dir(tmp_path, monkeypatch):
    cible = tmp_path / "donnees" / "organizer"
    monkeypatch.setenv("ORGANIZER_DATA_DIR", str(cible))

    assert data_dir() == cible
    assert cible.is_dir()


def test_connect_sans_chemin_ouvre_la_base_du_dossier_de_donnees(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGANIZER_DATA_DIR", str(tmp_path / "donnees"))

    connexion = connect()
    try:
        assert _version(connexion) == len(MIGRATIONS)
    finally:
        connexion.close()

    assert (tmp_path / "donnees" / "organizer.db").is_file()


def test_data_dir_sous_windows_utilise_appdata(tmp_path, monkeypatch):
    monkeypatch.delenv("ORGANIZER_DATA_DIR", raising=False)
    monkeypatch.setattr(db.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))

    assert data_dir() == tmp_path / "AppData" / "Roaming" / APP_NAME
    assert data_dir().is_dir()


def test_data_dir_sous_linux_utilise_xdg_data_home(tmp_path, monkeypatch):
    monkeypatch.delenv("ORGANIZER_DATA_DIR", raising=False)
    monkeypatch.setattr(db.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))

    assert data_dir() == tmp_path / "share" / APP_NAME


def test_data_dir_refuse_un_appdata_vide(tmp_path, monkeypatch):
    """Constat 18 : APPDATA défini mais vide donnait un chemin relatif, résolu
    depuis le dossier de travail courant (donc une base par raccourci)."""
    monkeypatch.delenv("ORGANIZER_DATA_DIR", raising=False)
    monkeypatch.setattr(db.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", "")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))

    # APPDATA vide : on retombe sur LOCALAPPDATA, jamais sur un chemin relatif.
    assert data_dir() == tmp_path / "AppData" / "Local" / APP_NAME

    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "profil"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profil"))
    assert data_dir().is_absolute()


def test_data_dir_refuse_un_chemin_relatif(monkeypatch):
    monkeypatch.setenv("ORGANIZER_DATA_DIR", "donnees-relatives")

    with pytest.raises(ValueError, match="chemin absolu"):
        data_dir()


def test_data_dir_signale_un_dossier_impossible_a_creer(tmp_path, monkeypatch):
    fichier = tmp_path / "pas-un-dossier"
    fichier.write_text("", encoding="utf-8")
    monkeypatch.setenv("ORGANIZER_DATA_DIR", str(fichier / APP_NAME))

    with pytest.raises(ValueError, match="n'a pas pu être créé"):
        data_dir()


# --- Atomicité des migrations -------------------------------------------------


def test_migration_fautive_ne_laisse_rien_derriere_elle(tmp_path, monkeypatch):
    """Constat 1 : le schéma était validé instruction par instruction alors que
    `user_version` n'avançait qu'à la fin, ce qui bloquait tous les démarrages."""
    chemin = tmp_path / "organizer.db"
    connect(chemin).close()
    fautive = "CREATE TABLE tags (id INTEGER PRIMARY KEY);\nSELECT colonne_absente FROM tags;"
    monkeypatch.setattr(db, "MIGRATIONS", [*MIGRATIONS, fautive])

    with pytest.raises(sqlite3.OperationalError):
        connect(chemin)

    temoin = sqlite3.connect(str(chemin))
    try:
        # La table de la migration ratée ne doit pas survivre, sinon la relance
        # échouerait pour toujours sur « table tags already exists ».
        assert "tags" not in {row[0] for row in temoin.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
        assert temoin.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    finally:
        temoin.close()

    # Migration corrigée : la relance repart proprement.
    monkeypatch.setattr(db, "MIGRATIONS", [*MIGRATIONS, "CREATE TABLE tags (id INTEGER PRIMARY KEY);"])
    connexion = connect(chemin)
    try:
        assert "tags" in _tables(connexion)
        assert _version(connexion) == len(MIGRATIONS) + 1
    finally:
        connexion.close()


def test_migration_interrompue_a_mi_chemin_se_rattrape(tmp_path):
    """Une base laissée par une version antérieure (schéma créé, `user_version`
    resté à 0) doit pouvoir s'ouvrir, pas échouer à chaque lancement."""
    chemin = tmp_path / "organizer.db"
    partiel = MIGRATIONS[0].split("CREATE TABLE IF NOT EXISTS settings")[0]
    brute = sqlite3.connect(str(chemin))
    brute.executescript(partiel)
    assert brute.execute("PRAGMA user_version").fetchone()[0] == 0
    brute.close()

    connexion = connect(chemin)
    try:
        assert TABLES <= _tables(connexion)
        assert _version(connexion) == len(MIGRATIONS)
    finally:
        connexion.close()
