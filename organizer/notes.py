"""Notes : stockage, titre dérivé du corps et recherche plein texte."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .dates import now_iso

DEFAULT_TITLE = "Sans titre"
TITLE_MAX = 60

_EDITABLE = {"title", "body", "pinned"}


def _first_line(text: str) -> str:
    """Première ligne non vide, espaces multiples réduits."""
    for line in text.splitlines():
        stripped = " ".join(line.split())
        if stripped:
            return stripped
    return ""


def _shorten(text: str, width: int) -> str:
    """Tronque sur une limite de mot et termine par une ellipse."""
    if width <= 0 or len(text) <= width:
        return text
    cut = text[: width - 1]
    space = cut.rfind(" ")
    if space >= width // 2:  # sans cela un mot très long ne laisserait presque rien
        cut = cut[:space]
    return cut.rstrip() + "…"


@dataclass
class Note:
    id: int
    title: str
    body: str
    pinned: bool
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Note":
        return cls(
            id=row["id"],
            title=row["title"],
            body=row["body"],
            pinned=bool(row["pinned"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def preview(self, width: int = 80) -> str:
        """Aperçu d'une ligne pour la liste des notes."""
        return _shorten(_first_line(self.body), width)


class NoteStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add(self, title: str = "", body: str = "") -> Note:
        title = title.strip() or self.derive_title(body)
        stamp = now_iso()
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO notes (title, body, pinned, created_at, updated_at) VALUES (?, ?, 0, ?, ?)",
                (title, body, stamp, stamp),
            )
        return self.get(cursor.lastrowid)

    def get(self, note_id: int) -> Note:
        row = self.conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row is None:
            raise KeyError(note_id)
        return Note.from_row(row)

    def update(self, note_id: int, **fields) -> Note:
        """Champs modifiables : `title`, `body`, `pinned`. `updated_at` suit toujours."""
        unknown = set(fields) - _EDITABLE
        if unknown:
            raise ValueError(f"Champs non modifiables : {', '.join(sorted(unknown))}")
        note = self.get(note_id)
        if "pinned" in fields:
            fields["pinned"] = int(bool(fields["pinned"]))
        if "title" in fields:
            title = str(fields["title"]).strip()
            fields["title"] = title or self.derive_title(fields.get("body", note.body))
        fields["updated_at"] = now_iso()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self.conn:
            self.conn.execute(f"UPDATE notes SET {assignments} WHERE id = ?", (*fields.values(), note_id))
        return self.get(note_id)

    def set_pinned(self, note_id: int, pinned: bool = True) -> Note:
        """Épingle ou désépingle la note ; `updated_at` ne bouge pas, le contenu est intact."""
        self.get(note_id)
        with self.conn:
            self.conn.execute("UPDATE notes SET pinned = ? WHERE id = ?", (int(pinned), note_id))
        return self.get(note_id)

    def delete(self, note_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))

    def list(self, search: str | None = None) -> list[Note]:
        """Épinglées d'abord, puis de la plus récemment modifiée à la plus ancienne."""
        rows = self.conn.execute(
            "SELECT * FROM notes ORDER BY pinned DESC, updated_at DESC, id DESC"
        ).fetchall()
        notes = [Note.from_row(row) for row in rows]
        needle = (search or "").strip().casefold()
        if not needle:
            return notes
        # `LIKE` de SQLite ignore les accents : le filtrage en Python fait correspondre « Réunion » et « réunion ».
        return [n for n in notes if needle in n.title.casefold() or needle in n.body.casefold()]

    @staticmethod
    def derive_title(body: str) -> str:
        """Titre tiré de la première ligne du corps, sans les `#` Markdown."""
        line = _first_line(body).lstrip("#").strip()
        return _shorten(line, TITLE_MAX) if line else DEFAULT_TITLE
