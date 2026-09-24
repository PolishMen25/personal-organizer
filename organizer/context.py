"""Contexte applicatif : l'unique objet que l'interface reçoit.

Toute la couche interface passe par ce contexte ; aucun onglet n'ouvre lui-même
la base de données. Cela garde une seule connexion SQLite pour l'application et
permet aux tests de construire un contexte en mémoire.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .db import Settings, connect, data_dir
from .files import FileOrganizer, read_rules
from .notes import NoteStore
from .tasks import TaskStore
from .timer import TimeStore


@dataclass
class AppContext:
    conn: sqlite3.Connection
    settings: Settings
    tasks: TaskStore
    notes: NoteStore
    times: TimeStore
    files: FileOrganizer
    rules_path: Path
    # Règles écartées à la dernière lecture du fichier, à signaler à l'utilisateur.
    rules_errors: list[str] = field(default_factory=list)

    @classmethod
    def open(cls, db_path: str | Path | None = None, rules_path: str | Path | None = None) -> "AppContext":
        conn = connect(db_path)
        rules_file = Path(rules_path) if rules_path else data_dir() / "regles-fichiers.json"
        report = read_rules(rules_file)
        return cls(
            conn=conn,
            settings=Settings(conn),
            tasks=TaskStore(conn),
            notes=NoteStore(conn),
            times=TimeStore(conn),
            files=FileOrganizer(conn, report.rules),
            rules_path=rules_file,
            rules_errors=list(report.errors),
        )

    def reload_rules(self) -> None:
        """À appeler après modification des règles de rangement sur le disque."""
        report = read_rules(self.rules_path)
        self.files.rules = report.rules
        self.rules_errors = list(report.errors)

    def close(self) -> None:
        self.conn.close()
