"""Aide au formatage et à la saisie des dates en français."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

_RELATIVE = {
    "aujourd'hui": 0, "aujourdhui": 0, "auj": 0,
    "demain": 1,
    "après-demain": 2, "apres-demain": 2, "après-d": 2,
}
_NUMERIC = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?$")


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def format_long(day: date) -> str:
    """`mercredi 24 septembre 2026`"""
    return f"{JOURS[day.weekday()]} {day.day} {MOIS[day.month - 1]} {day.year}"


def format_due(due: date | None, today: date | None = None) -> str:
    if due is None:
        return ""
    today = today or date.today()
    delta = (due - today).days
    if delta == 0:
        return "Aujourd'hui"
    if delta == 1:
        return "Demain"
    if delta == -1:
        return "Hier"
    if 1 < delta < 7:
        return JOURS[due.weekday()].capitalize()
    if due.year == today.year:
        return due.strftime("%d/%m")
    return due.strftime("%d/%m/%Y")


def format_duration(seconds: float) -> str:
    """`1 h 05` / `25 min` / `0 min`"""
    minutes = int(seconds) // 60
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d}" if hours else f"{minutes} min"


def parse_day(token: str, today: date) -> date | None:
    """Interprète `demain`, `vendredi`, `12/10`, `12/10/2027`… ; `None` sinon."""
    word = token.lower().replace("’", "'")
    if word in _RELATIVE:
        return today + timedelta(days=_RELATIVE[word])
    if word in JOURS:
        ahead = (JOURS.index(word) - today.weekday()) % 7 or 7
        return today + timedelta(days=ahead)
    match = _NUMERIC.match(word)
    if not match:
        return None
    day, month, year = match.groups()
    explicit_year = year is not None
    year_value = int(year) if explicit_year else today.year
    if explicit_year and year_value < 100:
        year_value += 2000
    try:
        result = date(year_value, int(month), int(day))
    except ValueError:
        return None
    if not explicit_year and result < today:
        try:
            result = result.replace(year=result.year + 1)
        except ValueError:  # 29/02 vers une année non bissextile
            return None
    return result
