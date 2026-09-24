"""Minuteur de concentration (pomodoro) et journal du temps passé.

La machine à états ne connaît ni horloge ni interface : l'appelant rythme
`tick()`, ce qui la rend testable et réutilisable hors de Qt.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

SANS_TACHE = "Sans tâche"


@dataclass
class TimeEntry:
    id: int
    task_id: int | None
    kind: str
    started_at: str
    ended_at: str
    duration_s: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "TimeEntry":
        return cls(
            id=row["id"],
            task_id=row["task_id"],
            kind=row["kind"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            duration_s=row["duration_s"],
        )


@dataclass
class PomodoroConfig:
    focus_min: int = 25
    break_min: int = 5
    long_break_min: int = 15
    cycles_before_long: int = 4


class Phase(str, Enum):
    """Les valeurs servent aussi de `kind` dans la table `time_entries`."""

    FOCUS = "focus"
    BREAK = "break"
    LONG_BREAK = "long_break"


_LABELS = {
    Phase.FOCUS: "Concentration",
    Phase.BREAK: "Pause",
    Phase.LONG_BREAK: "Pause longue",
}


def _checked(config: PomodoroConfig) -> PomodoroConfig:
    if min(config.focus_min, config.break_min, config.long_break_min) <= 0:
        raise ValueError("Les durées du minuteur doivent être supérieures à zéro.")
    if config.cycles_before_long < 1:
        raise ValueError("Il faut au moins un cycle avant la pause longue.")
    return config


class PomodoroTimer:
    """Alternance concentration / pause, avec une pause longue tous les N cycles."""

    def __init__(self, config: PomodoroConfig | None = None):
        self.config = _checked(config or PomodoroConfig())
        self.phase = Phase.FOCUS
        self.remaining_s = self._duration(Phase.FOCUS)
        self.running = False
        self.completed_focus = 0
        self._since_long = 0

    def start(self) -> None:
        self.running = True

    def pause(self) -> None:
        self.running = False

    def reset(self) -> None:
        """Retour au début d'une concentration, compteur de cycles remis à zéro."""
        self.running = False
        self.phase = Phase.FOCUS
        self.remaining_s = self._duration(Phase.FOCUS)
        self.completed_focus = 0
        self._since_long = 0

    def skip(self) -> None:
        """Abandonne la phase courante : elle n'est pas comptée comme achevée."""
        self._advance(completed=False)

    def tick(self, seconds: int = 1) -> Phase | None:
        """Fait avancer le minuteur ; renvoie la phase qui vient de s'achever, sinon `None`."""
        if seconds < 0:
            raise ValueError("Le nombre de secondes ne peut pas être négatif.")
        if not self.running or seconds == 0:
            return None
        self.remaining_s -= seconds
        if self.remaining_s > 0:
            return None
        finished = self.phase
        self._advance(completed=True)
        return finished

    def apply_config(self, config: PomodoroConfig) -> None:
        """Une phase en cours garde son compte à rebours ; à l'arrêt il est recalibré."""
        self.config = _checked(config)
        if not self.running:
            self.remaining_s = self._duration(self.phase)

    def label(self) -> str:
        return _LABELS[self.phase]

    def display(self) -> str:
        """`24:37`"""
        minutes, seconds = divmod(max(self.remaining_s, 0), 60)
        return f"{minutes:02d}:{seconds:02d}"

    def elapsed_s(self) -> int:
        return max(self._duration(self.phase) - self.remaining_s, 0)

    def _advance(self, completed: bool) -> None:
        if self.phase is Phase.FOCUS:
            if completed:
                self.completed_focus += 1
                self._since_long += 1
            if self._since_long >= self.config.cycles_before_long:
                self._since_long = 0
                self.phase = Phase.LONG_BREAK
            else:
                self.phase = Phase.BREAK
        else:
            self.phase = Phase.FOCUS
        self.remaining_s = self._duration(self.phase)

    def _duration(self, phase: Phase) -> int:
        minutes = {
            Phase.FOCUS: self.config.focus_min,
            Phase.BREAK: self.config.break_min,
            Phase.LONG_BREAK: self.config.long_break_min,
        }[phase]
        return minutes * 60


def _day_bounds(day: date) -> tuple[str, str]:
    """Bornes `[jour, lendemain)` comparables au préfixe ISO de `started_at`."""
    return day.isoformat(), (day + timedelta(days=1)).isoformat()


class TimeStore:
    """Journal du temps passé (table `time_entries`)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def log(
        self,
        started_at: str,
        ended_at: str,
        duration_s: int,
        task_id: int | None = None,
        kind: str = "focus",
    ) -> TimeEntry:
        if not started_at or not ended_at:
            raise ValueError("Les horodatages de début et de fin sont obligatoires.")
        if duration_s <= 0:
            raise ValueError("La durée enregistrée doit être supérieure à zéro.")
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO time_entries (task_id, kind, started_at, ended_at, duration_s) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, kind, started_at, ended_at, int(duration_s)),
            )
        row = self.conn.execute(
            "SELECT * FROM time_entries WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return TimeEntry.from_row(row)

    def entries(self, day: date | None = None) -> list[TimeEntry]:
        """Entrées d'un jour, ou toutes si `day` vaut `None`, de la plus récente à la plus ancienne."""
        sql = "SELECT * FROM time_entries"
        params: tuple = ()
        if day is not None:
            start, end = _day_bounds(day)
            sql += " WHERE started_at >= ? AND started_at < ?"
            params = (start, end)
        sql += " ORDER BY started_at DESC, id DESC"
        rows = self.conn.execute(sql, params).fetchall()
        return [TimeEntry.from_row(row) for row in rows]

    def total_seconds(self, day: date | None = None, kind: str = "focus") -> int:
        start, end = _day_bounds(day or date.today())
        row = self.conn.execute(
            "SELECT COALESCE(SUM(duration_s), 0) AS total FROM time_entries "
            "WHERE kind = ? AND started_at >= ? AND started_at < ?",
            (kind, start, end),
        ).fetchone()
        return int(row["total"])

    def week_totals(self, ending: date | None = None) -> list[tuple[date, int]]:
        """Concentration des 7 jours s'achevant à `ending` (aujourd'hui par défaut), jours vides compris."""
        ending = ending or date.today()
        first = ending - timedelta(days=6)
        rows = self.conn.execute(
            "SELECT substr(started_at, 1, 10) AS jour, SUM(duration_s) AS total "
            "FROM time_entries WHERE kind = ? AND started_at >= ? AND started_at < ? "
            "GROUP BY jour",
            (Phase.FOCUS.value, first.isoformat(), _day_bounds(ending)[1]),
        ).fetchall()
        totals = {row["jour"]: int(row["total"]) for row in rows}
        days = [first + timedelta(days=offset) for offset in range(7)]
        return [(day, totals.get(day.isoformat(), 0)) for day in days]

    def per_task(self, since: date | None = None) -> list[tuple[str, int]]:
        """Concentration par tâche, du plus long au plus court ; une tâche supprimée retombe sur `Sans tâche`."""
        # Le libellé de repli passe en paramètre : concaténé, une apostrophe y
        # casserait la requête.
        sql = (
            "SELECT COALESCE(tasks.title, ?) AS libelle, "
            "SUM(entries.duration_s) AS total "
            "FROM time_entries AS entries LEFT JOIN tasks ON tasks.id = entries.task_id "
            "WHERE entries.kind = ?"
        )
        params: tuple = (SANS_TACHE, Phase.FOCUS.value)
        if since is not None:
            sql += " AND entries.started_at >= ?"
            params += (since.isoformat(),)
        sql += " GROUP BY libelle ORDER BY total DESC, libelle"
        rows = self.conn.execute(sql, params).fetchall()
        return [(row["libelle"], int(row["total"])) for row in rows]
