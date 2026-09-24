"""Tâches : stockage et saisie rapide."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime

from .dates import now_iso, parse_day

PRIORITY_HIGH, PRIORITY_NORMAL, PRIORITY_LOW = 1, 2, 3
PRIORITY_LABELS = {PRIORITY_HIGH: "Haute", PRIORITY_NORMAL: "Normale", PRIORITY_LOW: "Basse"}

_EDITABLE = {"title", "notes", "priority", "due_date", "project"}


def _normalise_priority(value: object) -> int:
    """Sans ce contrôle, la contrainte CHECK de SQLite remonte une IntegrityError illisible."""
    if isinstance(value, bool) or not isinstance(value, int) or value not in PRIORITY_LABELS:
        raise ValueError(f"Priorité invalide : {value!r} (attendu 1, 2 ou 3).")
    return value


def _normalise_due_date(value: object) -> str | None:
    """Normalise une échéance en texte ISO.

    Le contrôle a lieu AVANT l'écriture : une valeur non ISO rendrait la tâche
    illisible, `Task.from_row` ne sachant plus la relire.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
    raise ValueError(f"Échéance invalide : {value!r} (attendu une date ou « AAAA-MM-JJ »).")


@dataclass
class Task:
    id: int
    title: str
    notes: str
    priority: int
    due_date: date | None
    project: str
    done: bool
    created_at: str
    completed_at: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Task":
        return cls(
            id=row["id"],
            title=row["title"],
            notes=row["notes"],
            priority=row["priority"],
            due_date=date.fromisoformat(row["due_date"]) if row["due_date"] else None,
            project=row["project"],
            done=bool(row["done"]),
            created_at=row["created_at"],
            completed_at=row["completed_at"],
        )

    def is_overdue(self, today: date | None = None) -> bool:
        return not self.done and self.due_date is not None and self.due_date < (today or date.today())


@dataclass
class QuickAdd:
    title: str
    priority: int = PRIORITY_NORMAL
    due_date: date | None = None
    project: str = ""


def parse_quick_add(text: str, today: date | None = None) -> QuickAdd:
    """Analyse une saisie rapide comme `Appeler la banque demain !1 #admin`.

    - `!1` `!2` `!3` : priorité (haute, normale, basse)
    - `#projet` : projet
    - `aujourd'hui`, `demain`, `après-demain`, un jour de la semaine, `jj/mm` ou `jj/mm/aaaa` : échéance
    """
    today = today or date.today()
    result = QuickAdd(title="")
    words = []
    for token in text.split():
        if re.fullmatch(r"![1-3]", token):
            result.priority = int(token[1])
        elif re.fullmatch(r"#\S+", token):
            result.project = token[1:]
        elif (day := parse_day(token, today)) is not None:
            result.due_date = day
        else:
            words.append(token)
    result.title = " ".join(words)
    if not result.title:
        raise ValueError("La tâche doit avoir un titre.")
    return result


class TaskStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add(
        self,
        title: str,
        priority: int = PRIORITY_NORMAL,
        due_date: date | None = None,
        project: str = "",
        notes: str = "",
    ) -> Task:
        title = title.strip()
        if not title:
            raise ValueError("La tâche doit avoir un titre.")
        priority = _normalise_priority(priority)
        due = _normalise_due_date(due_date)
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO tasks (title, notes, priority, due_date, project, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (title, notes, priority, due, project.strip(), now_iso()),
            )
        return self.get(cursor.lastrowid)

    def add_quick(self, text: str, today: date | None = None) -> Task:
        parsed = parse_quick_add(text, today)
        return self.add(parsed.title, parsed.priority, parsed.due_date, parsed.project)

    def get(self, task_id: int) -> Task:
        row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return Task.from_row(row)

    def update(self, task_id: int, **fields) -> Task:
        unknown = set(fields) - _EDITABLE
        if unknown:
            raise ValueError(f"Champs non modifiables : {', '.join(sorted(unknown))}")
        if "title" in fields:
            fields["title"] = str(fields["title"]).strip()
            if not fields["title"]:
                raise ValueError("La tâche doit avoir un titre.")
        if "priority" in fields:
            fields["priority"] = _normalise_priority(fields["priority"])
        if "due_date" in fields:
            fields["due_date"] = _normalise_due_date(fields["due_date"])
        if fields:
            assignments = ", ".join(f"{name} = ?" for name in fields)
            with self.conn:
                self.conn.execute(f"UPDATE tasks SET {assignments} WHERE id = ?", (*fields.values(), task_id))
        return self.get(task_id)

    def set_done(self, task_id: int, done: bool = True) -> Task:
        with self.conn:
            self.conn.execute(
                "UPDATE tasks SET done = ?, completed_at = ? WHERE id = ?",
                (int(done), now_iso() if done else None, task_id),
            )
        return self.get(task_id)

    def delete(self, task_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    def list(self, status: str = "open", project: str | None = None, search: str | None = None) -> list[Task]:
        """`status` : `open`, `done` ou `all`. Tri : à faire d'abord, puis échéance, puis priorité."""
        clauses, params = [], []
        if status == "open":
            clauses.append("done = 0")
        elif status == "done":
            clauses.append("done = 1")
        if project:
            clauses.append("project = ?")
            params.append(project)
        if search:
            # « % » et « _ » sont des jokers de LIKE : une recherche de « 100% »
            # ou de « note_1 » doit les chercher tels quels.
            needle = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("(title LIKE ? ESCAPE '\\' OR notes LIKE ? ESCAPE '\\')")
            params += [f"%{needle}%"] * 2
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        order = (
            "ORDER BY done, completed_at DESC, due_date IS NULL, due_date, priority, id"
            if status != "done"
            else "ORDER BY completed_at DESC, id DESC"
        )
        rows = self.conn.execute(f"SELECT * FROM tasks {where} {order}", params).fetchall()
        return [Task.from_row(row) for row in rows]

    def today(self, today: date | None = None) -> dict[str, list[Task]]:
        """Vue « Aujourd'hui » : en retard, prévu aujourd'hui, priorité haute sans échéance."""
        today = today or date.today()
        sections: dict[str, list[Task]] = {"overdue": [], "today": [], "important": []}
        for task in self.list("open"):
            if task.due_date is None:
                if task.priority == PRIORITY_HIGH:
                    sections["important"].append(task)
            elif task.due_date < today:
                sections["overdue"].append(task)
            elif task.due_date == today:
                sections["today"].append(task)
        return sections

    def projects(self) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT project FROM tasks WHERE project != '' ORDER BY project")
        return [row["project"] for row in rows]
