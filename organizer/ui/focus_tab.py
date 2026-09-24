"""Onglet « Concentration » : minuteur pomodoro, journal du jour et réglages."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..context import AppContext
from ..dates import JOURS, format_duration, format_long, now_iso
from ..timer import SANS_TACHE, Phase, PomodoroConfig, PomodoroTimer, TimeEntry
from .theme import COLORS, MARGIN, SPACING
from .widgets import EmptyState, SectionHeader, card, read_setting, styled_button

NO_TASK = "Aucune tâche"
# Suffixe de la tâche sur laquelle on se concentre encore après l'avoir cochée.
DONE_SUFFIX = " (terminée)"
TICK_MS = 1000
CLOCK_PX = 48
BAR_MAX_PX = 96
# En deçà, une concentration interrompue à la main n'est pas journalisée.
MIN_LOGGED_S = 60
# Hauteur minimale des réglages de durée.
SPIN_MIN_PX = 26

EMPTY_MESSAGE = "Aucune session aujourd'hui."
EMPTY_HINT = "Lancez le minuteur : chaque concentration terminée s'ajoute ici."

_DEFAULTS = PomodoroConfig()

# Clé de réglage, intitulé et maximum en minutes ; l'ordre suit les trois
# premiers champs de PomodoroConfig.
_DURATIONS: tuple[tuple[str, str, int, int], ...] = (
    ("pomodoro_focus_min", "Concentration", _DEFAULTS.focus_min, 180),
    ("pomodoro_break_min", "Pause", _DEFAULTS.break_min, 60),
    ("pomodoro_long_break_min", "Pause longue", _DEFAULTS.long_break_min, 120),
)


def _clamped(raw: object, default: int, maximum: int) -> int:
    """Durée en minutes lue des réglages, ramenée dans les bornes acceptées."""
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return min(max(value, 1), maximum)


def _clock_font() -> QFont:
    """Grande police à chasse fixe : les chiffres ne bougent plus d'une seconde à l'autre."""
    font = QFont("Consolas")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setFixedPitch(True)
    font.setPixelSize(CLOCK_PX)
    font.setBold(True)
    return font


def _dim(text: str, key: str = "text_dim") -> QLabel:
    """Libellé discret, centré horizontalement par son conteneur."""
    label = QLabel(text)
    label.setStyleSheet(f"color: {COLORS[key]}; background: transparent;")
    return label


def _short(text: str, width: int = 48) -> str:
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def _start_hour(stamp: str) -> str:
    """`14:32` à partir d'un horodatage ISO."""
    try:
        return datetime.fromisoformat(stamp).strftime("%H:%M")
    except ValueError:
        return stamp


def _split_at_midnight(started: str, ended: str, seconds: int) -> list[tuple[str, str, int]]:
    """Découpe une session à cheval sur minuit en deux tronçons journalisables."""
    try:
        begin, end = datetime.fromisoformat(started), datetime.fromisoformat(ended)
    except ValueError:
        return [(started, ended, seconds)]
    span = (end - begin).total_seconds()
    if begin.date() == end.date() or span <= 0:
        return [(started, ended, seconds)]
    midnight = datetime.combine(begin.date() + timedelta(days=1), datetime.min.time())
    before = round(seconds * (midnight - begin).total_seconds() / span)
    after = seconds - before
    stamp = midnight.isoformat()
    parts = [(started, stamp, before), (stamp, ended, after)]
    kept = [part for part in parts if part[2] > 0]
    return kept or [(started, ended, seconds)]


def _centered(*children: QWidget) -> QWidget:
    """Ligne horizontale centrée."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACING)
    layout.addStretch(1)
    for child in children:
        layout.addWidget(child)
    layout.addStretch(1)
    return row


def _replace(layout: QLayout, widget: QWidget) -> None:
    """Remplace le contenu d'un conteneur ; l'ancien est détruit en différé."""
    while layout.count():
        old = layout.takeAt(0).widget()
        if old is not None:
            old.setParent(None)
            old.deleteLater()
    layout.addWidget(widget)


class FocusTab(QWidget):
    """Minuteur de concentration, journal du jour et réglages des durées."""

    dataChanged = Signal()

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.timer = PomodoroTimer(PomodoroConfig(*self._stored_minutes()))
        # Secondes de concentration déjà comptées, et début de la phase en cours.
        self._elapsed_s = 0
        self._started_at: str | None = None
        # Tâche retenue au démarrage : la cocher ailleurs ne doit pas la perdre.
        self._task_id: int | None = None
        # Le temps se mesure à l'horloge, pas au nombre de battements reçus :
        # Qt fusionne les battements manqués, donc un blocage de la boucle
        # effacerait autant de secondes du journal. Remplaçable par les tests.
        self._now: Callable[[], float] = time.monotonic
        self._last_tick = 0.0

        self._clock = QTimer(self)
        self._clock.setInterval(TICK_MS)
        self._clock.setTimerType(Qt.TimerType.CoarseTimer)
        self._clock.timeout.connect(self._on_tick)

        self._build()
        self.refresh()

    # -- Construction ----------------------------------------------------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        root.setSpacing(SPACING)
        root.addWidget(self._build_timer_card())

        bottom = QHBoxLayout()
        bottom.setSpacing(SPACING)
        bottom.addLayout(self._build_log_column(), 3)
        bottom.addLayout(self._build_side_column(), 2)
        root.addLayout(bottom, 1)

    def _build_timer_card(self) -> QWidget:
        self._task_box = QComboBox()
        self._task_box.setToolTip("Tâche associée aux sessions enregistrées")
        self._task_box.setMinimumWidth(260)
        self._task_box.addItem(NO_TASK, None)

        self._phase = QLabel(self.timer.label())
        self._phase.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._phase.setStyleSheet(f"color: {COLORS['text_dim']}; background: transparent;")

        self._display = QLabel(self.timer.display())
        self._display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._display.setFont(_clock_font())

        self._cycles = QLabel()
        self._cycles.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cycles.setStyleSheet(f"color: {COLORS['text_dim']}; background: transparent;")

        self._toggle_button = styled_button("Démarrer", "primary")
        self._toggle_button.clicked.connect(self._on_toggle)
        self._reset_button = styled_button("Réinitialiser", "ghost")
        self._reset_button.clicked.connect(self._on_reset)
        self._skip_button = styled_button("Passer", "ghost")
        self._skip_button.clicked.connect(self._on_skip)

        self._message = QLabel()
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setWordWrap(True)
        self._message.setMinimumHeight(22)
        self._message.setStyleSheet(f"color: {COLORS['ok']}; background: transparent;")

        return card(
            _centered(_dim("Tâche :"), self._task_box),
            self._phase,
            self._display,
            self._cycles,
            _centered(self._toggle_button, self._reset_button, self._skip_button),
            self._message,
        )

    def _build_log_column(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(SPACING)

        # En-tête redessiné à chaque relecture : il porte le nombre de sessions.
        self._log_header = QVBoxLayout()
        self._log_header.setContentsMargins(0, 0, 0, 0)
        column.addLayout(self._log_header)

        self._log_scroll = QScrollArea()
        self._log_scroll.setWidgetResizable(True)
        self._log_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._log_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        column.addWidget(self._log_scroll, 1)

        self._total = _dim("")
        column.addWidget(self._total)
        return column

    def _build_side_column(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(SPACING)

        chart_holder = QWidget()
        chart_holder.setMinimumHeight(BAR_MAX_PX + 40)
        self._chart_box = QVBoxLayout(chart_holder)
        self._chart_box.setContentsMargins(0, 0, 0, 0)
        column.addWidget(card(SectionHeader("7 derniers jours"), chart_holder))

        column.addWidget(card(SectionHeader("Durées"), self._build_durations()))
        column.addStretch(1)
        return column

    def _build_durations(self) -> QWidget:
        holder = QWidget()
        form = QFormLayout(holder)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)

        self._spins: dict[str, QSpinBox] = {}
        for key, label, _default, maximum in _DURATIONS:
            spin = QSpinBox()
            # Sans plancher, la compression de la fenêtre les écrase à 12 px.
            spin.setMinimumHeight(SPIN_MIN_PX)
            spin.setRange(1, maximum)
            spin.setSuffix(" min")
            # Les valeurs viennent des réglages, posées par _sync_durations.
            spin.valueChanged.connect(lambda value, name=key: self._on_duration_changed(name, value))
            self._spins[key] = spin
            form.addRow(f"{label} :", spin)
        return holder

    # -- Rafraîchissement ------------------------------------------------

    def refresh(self) -> None:
        """Relit la base : durées, tâches ouvertes, sessions du jour et histogramme."""
        self._sync_durations()
        self._fill_tasks()
        self._fill_log()
        self._fill_chart()
        self._render_clock()

    def _stored_minutes(self) -> tuple[int, ...]:
        """Durées enregistrées, dans l'ordre concentration, pause, pause longue."""
        return tuple(
            _clamped(read_setting(self.ctx.settings, key, default), default, maximum)
            for key, _label, default, maximum in _DURATIONS
        )

    def _sync_durations(self) -> None:
        """Aligne les compteurs sur les réglages sans casser un décompte en cours."""
        minutes = self._stored_minutes()
        for (key, _label, _default, _maximum), value in zip(_DURATIONS, minutes):
            spin = self._spins[key]
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

        current = self.timer.config
        if minutes != (current.focus_min, current.break_min, current.long_break_min):
            self.timer.apply_config(PomodoroConfig(*minutes))

    def _fill_tasks(self) -> None:
        """Recharge les tâches ouvertes en gardant la sélection, même terminée."""
        selected = self._task_box.currentData()
        self._task_box.blockSignals(True)
        self._task_box.clear()
        self._task_box.addItem(NO_TASK, None)
        open_ids = set()
        for task in self.ctx.tasks.list("open"):
            self._task_box.addItem(_short(task.title), task.id)
            open_ids.add(task.id)
        # Cocher la tâche sur laquelle on se concentre est un geste normal : elle
        # doit rester choisie, sinon la session serait journalisée sans tâche.
        if selected is not None and selected not in open_ids:
            title = self._closed_title(selected)
            if title is not None:
                self._task_box.addItem(_short(title) + DONE_SUFFIX, selected)
        found = self._task_box.findData(selected) if selected is not None else 0
        self._task_box.setCurrentIndex(max(found, 0))
        self._task_box.blockSignals(False)

    def _closed_title(self, task_id: int) -> str | None:
        try:
            return self.ctx.tasks.get(task_id).title
        except KeyError:
            return None  # tâche supprimée : plus rien à proposer

    def _fill_log(self) -> None:
        entries = [
            entry
            for entry in self.ctx.times.entries(date.today())
            if entry.kind == Phase.FOCUS.value
        ]
        titles = {task.id: task.title for task in self.ctx.tasks.list("all")}
        _replace(self._log_header, SectionHeader("Sessions du jour", len(entries)))

        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING)
        if entries:
            layout.addWidget(card(*(self._entry_row(entry, titles) for entry in entries)))
        else:
            layout.addWidget(EmptyState(EMPTY_MESSAGE, EMPTY_HINT))
        layout.addStretch(1)

        previous = self._log_scroll.takeWidget()
        if previous is not None:
            previous.deleteLater()
        self._log_scroll.setWidget(holder)
        holder.show()

        self._total.setText(f"Total du jour : {format_duration(self.ctx.times.total_seconds())}")

    def _entry_row(self, entry: TimeEntry, titles: dict[int, str]) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(SPACING)

        hour = _dim(_start_hour(entry.started_at))
        hour.setFixedWidth(46)
        layout.addWidget(hour)

        duration = QLabel(format_duration(entry.duration_s))
        duration.setFixedWidth(70)
        duration.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")
        layout.addWidget(duration)

        title = titles.get(entry.task_id) if entry.task_id is not None else None
        task = _dim(_short(title or SANS_TACHE), "text" if title else "text_dim")
        task.setWordWrap(True)
        layout.addWidget(task, 1)
        return row

    def _fill_chart(self) -> None:
        totals = self.ctx.times.week_totals()
        peak = max((seconds for _day, seconds in totals), default=0)
        today = date.today()

        bars = QWidget()
        layout = QHBoxLayout(bars)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for day, seconds in totals:
            layout.addWidget(self._bar(day, seconds, peak, day == today))

        wrapper = QWidget()
        outer = QVBoxLayout(wrapper)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        outer.addWidget(bars, 1)
        note = _dim(
            "Aucune concentration cette semaine."
            if peak == 0
            else f"Meilleure journée : {format_duration(peak)}"
        )
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(note)
        _replace(self._chart_box, wrapper)

    def _bar(self, day: date, seconds: int, peak: int, is_today: bool) -> QWidget:
        column = QWidget()
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addStretch(1)

        height = round(seconds / peak * BAR_MAX_PX) if peak else 0
        bar = QFrame()
        bar.setFixedHeight(max(height, 3))  # une journée vide garde un trait visible
        bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if seconds == 0:
            colour = COLORS["border"]
        else:
            colour = COLORS["accent"] if is_today else COLORS["accent_dim"]
        bar.setStyleSheet(f"background-color: {colour}; border-radius: 4px;")
        bar.setToolTip(f"{format_long(day)} — {format_duration(seconds)}")
        layout.addWidget(bar)

        label = _dim(JOURS[day.weekday()][:3], "text" if is_today else "text_dim")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        return column

    def _render_clock(self) -> None:
        """Redessine le compte à rebours, la phase et l'état des boutons."""
        self._display.setText(self.timer.display())
        focus = self.timer.phase is Phase.FOCUS
        colour = COLORS["accent"] if focus else COLORS["ok"]
        self._display.setStyleSheet(f"color: {colour}; background: transparent;")
        self._phase.setText(self.timer.label())
        self._toggle_button.setText("Pause" if self.timer.running else "Démarrer")
        self._cycles.setText(f"Concentrations terminées : {self.timer.completed_focus}")

    # -- Minuteur --------------------------------------------------------

    def _on_tick(self) -> None:
        """Rattrape les secondes réellement écoulées depuis le battement précédent.

        Qt fusionne les battements manqués : compter « une seconde par battement »
        effaçait du journal tout le temps passé pendant un blocage de la boucle.
        """
        if not self.timer.running:
            self._clock.stop()
            return
        seconds = int(self._now() - self._last_tick)
        if seconds <= 0:
            return
        self._last_tick += seconds  # la fraction de seconde restante est reportée
        if self.timer.phase is Phase.FOCUS:
            if self._started_at is None:
                self._started_at = now_iso()
                self._task_id = self._task_box.currentData()
            self._elapsed_s += seconds
        finished = self.timer.tick(seconds)
        if finished is not None:
            self._on_phase_end(finished)
        self._render_clock()

    def _on_phase_end(self, finished: Phase) -> None:
        """Une phase vient de s'achever ; la suivante est déjà en place."""
        entry = self._record(minimum=1) if finished is Phase.FOCUS else None
        QApplication.beep()
        following = self.timer.label().lower()
        if finished is Phase.FOCUS:
            recorded = f" {format_duration(entry.duration_s)} enregistrées." if entry else ""
            self._announce(f"Concentration terminée !{recorded} Place à la {following}.")
        else:
            self._announce(f"Pause terminée. Retour à la {following}.")
        if entry is not None:
            self._fill_log()
            self._fill_chart()
            self.dataChanged.emit()

    def _on_toggle(self) -> None:
        if self.timer.running:
            self.timer.pause()
            self._clock.stop()
            self._announce("Minuteur en pause.")
        else:
            self.timer.start()
            # Repère de départ : le décompte se lit ensuite à l'horloge.
            self._last_tick = self._now()
            self._clock.start()
            self._announce(f"{self.timer.label()} en cours.")
        self._render_clock()

    def _on_reset(self) -> None:
        written = self._close_focus()
        self.timer.reset()
        self._clock.stop()
        self._after_change("Minuteur réinitialisé.", written)

    def _on_skip(self) -> None:
        written = self._close_focus()
        self.timer.skip()
        if not self.timer.running:  # « Passer » ne relance jamais un minuteur à l'arrêt
            self._clock.stop()
        self._after_change(f"Phase passée, place à la {self.timer.label().lower()}.", written)

    def _on_duration_changed(self, key: str, value: int) -> None:
        self.ctx.settings.set(key, value)
        # Une phase en cours garde son décompte ; apply_config s'en charge.
        self.timer.apply_config(PomodoroConfig(*self._stored_minutes()))
        self._render_clock()

    def flush(self) -> None:
        """Arrête l'horloge et journalise la concentration entamée ; appelée à la fermeture."""
        self._clock.stop()
        self.timer.pause()
        if self._close_focus():
            self.dataChanged.emit()

    # -- Journalisation --------------------------------------------------

    def _close_focus(self) -> bool:
        """Clôt une concentration interrompue à la main : journalisée dès 60 secondes."""
        if self.timer.phase is not Phase.FOCUS:
            self._reset_session()
            return False
        return self._record(minimum=MIN_LOGGED_S) is not None

    def _reset_session(self) -> None:
        self._elapsed_s, self._started_at, self._task_id = 0, None, None

    def _record(self, minimum: int) -> TimeEntry | None:
        """Journalise le temps accumulé s'il atteint `minimum` secondes, puis remet à zéro."""
        seconds, started, task_id = self._elapsed_s, self._started_at, self._task_id
        self._reset_session()
        if started is None or seconds < minimum:
            return None
        ended = now_iso()
        entry: TimeEntry | None = None
        # Une session à cheval sur minuit est coupée en deux : sans quoi elle
        # disparaîtrait du journal du jour dès le changement de date.
        for begin, end, duration in _split_at_midnight(started, ended, seconds):
            entry = self._log(begin, end, duration, task_id)
        return entry

    def _log(self, started: str, ended: str, seconds: int, task_id: int | None) -> TimeEntry:
        try:
            return self.ctx.times.log(started, ended, seconds, task_id, Phase.FOCUS.value)
        except sqlite3.IntegrityError:
            # Tâche supprimée entre-temps : la session est gardée, sans tâche.
            return self.ctx.times.log(started, ended, seconds, None, Phase.FOCUS.value)

    def _after_change(self, message: str, written: bool) -> None:
        """Message, redessin, puis signal si une session vient d'être journalisée."""
        if written:
            message += " Temps écoulé enregistré."
        self._announce(message)
        self._render_clock()
        self._fill_log()
        self._fill_chart()
        if written:
            self.dataChanged.emit()

    def _announce(self, message: str) -> None:
        self._message.setText(message)
