"""Stockage SQLite local : emplacement des données, schéma et migrations."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

APP_NAME = "PersonalOrganizer"

# Chaque entrée est une migration ; son index + 1 devient le `user_version` de la base.
# Les « IF NOT EXISTS » rattrapent une base laissée à mi-chemin par une version
# antérieure de `migrate()`, qui validait le schéma avant `user_version`.
MIGRATIONS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        title         TEXT    NOT NULL,
        notes         TEXT    NOT NULL DEFAULT '',
        priority      INTEGER NOT NULL DEFAULT 2 CHECK (priority BETWEEN 1 AND 3),
        due_date      TEXT,
        project       TEXT    NOT NULL DEFAULT '',
        done          INTEGER NOT NULL DEFAULT 0,
        created_at    TEXT    NOT NULL,
        completed_at  TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_tasks_open ON tasks (done, due_date);

    CREATE TABLE IF NOT EXISTS notes (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        title       TEXT    NOT NULL DEFAULT '',
        body        TEXT    NOT NULL DEFAULT '',
        pinned      INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT    NOT NULL,
        updated_at  TEXT    NOT NULL
    );

    CREATE TABLE IF NOT EXISTS time_entries (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id     INTEGER REFERENCES tasks (id) ON DELETE SET NULL,
        kind        TEXT    NOT NULL DEFAULT 'focus',
        started_at  TEXT    NOT NULL,
        ended_at    TEXT    NOT NULL,
        duration_s  INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_time_started ON time_entries (started_at);

    CREATE TABLE IF NOT EXISTS file_moves (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id  TEXT    NOT NULL,
        src       TEXT    NOT NULL,
        dst       TEXT    NOT NULL,
        moved_at  TEXT    NOT NULL,
        undone    INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_file_moves_batch ON file_moves (batch_id);

    CREATE TABLE IF NOT EXISTS settings (
        key    TEXT PRIMARY KEY,
        value  TEXT NOT NULL
    );
    """,
]


def data_dir() -> Path:
    """Dossier des données : %APPDATA%\\PersonalOrganizer sous Windows.

    Lève `ValueError` si le dossier ne peut pas être utilisé, plutôt que d'écrire
    silencieusement à côté de l'exécutable.
    """
    override = os.environ.get("ORGANIZER_DATA_DIR")
    if override:
        path = Path(override)
    else:
        # On teste la valeur, pas seulement la présence de la variable : un
        # `APPDATA` vide donnerait un chemin relatif, donc une base différente
        # selon le dossier depuis lequel l'application a été lancée.
        if sys.platform == "win32":
            root = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        else:
            root = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        path = Path(root or Path.home()) / APP_NAME
    if not path.is_absolute():
        raise ValueError(
            f"Le dossier de données « {path} » n'est pas un chemin absolu : "
            "corrigez ORGANIZER_DATA_DIR ou APPDATA."
        )
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ValueError(f"Le dossier de données « {path} » n'a pas pu être créé : {error}") from error
    return path


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Ouvre (et migre si besoin) la base. `":memory:"` est accepté pour les tests.

    `check_same_thread=False` laisse le fil de travail des opérations disque
    journaliser ses déplacements : SQLite est compilé en mode sérialisé
    (`sqlite3.threadsafety == 3`), il garde donc lui-même l'accès cohérent.
    """
    conn = sqlite3.connect(str(path or data_dir() / "organizer.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Applique les migrations manquantes, chacune en tout ou rien.

    La transaction est ouverte dans le script lui-même : `executescript` valide
    chaque instruction DDL séparément et solde toute transaction déjà ouverte,
    si bien qu'un `BEGIN` posé de l'extérieur ne l'englobe pas. Sans cela, une
    interruption laisserait le schéma en avance sur `user_version`, et la base
    ne s'ouvrirait plus jamais.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for index, script in enumerate(MIGRATIONS[version:], start=version + 1):
        try:
            conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {index};\nCOMMIT;")
        except BaseException:
            conn.rollback()  # schéma et user_version reviennent ensemble en arrière
            raise


class Settings:
    """Petit magasin clé/valeur (valeurs sérialisées en JSON)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value: Any) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )
