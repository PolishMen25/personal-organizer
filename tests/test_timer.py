"""Tests du minuteur pomodoro et du journal du temps."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from organizer.db import connect
from organizer.tasks import TaskStore
from organizer.timer import Phase, PomodoroConfig, PomodoroTimer, TimeStore

CONFIG = PomodoroConfig(focus_min=1, break_min=1, long_break_min=2, cycles_before_long=4)


@pytest.fixture
def store() -> TimeStore:
    return TimeStore(connect(":memory:"))


def started_timer(config: PomodoroConfig = CONFIG) -> PomodoroTimer:
    timer = PomodoroTimer(config)
    timer.start()
    return timer


def moment(day: date, hour: int) -> str:
    return f"{day.isoformat()}T{hour:02d}:00:00"


def test_enchainement_concentration_pause_concentration():
    timer = started_timer()
    assert timer.phase is Phase.FOCUS
    assert timer.label() == "Concentration"
    assert timer.display() == "01:00"

    assert timer.tick(59) is None
    assert timer.elapsed_s() == 59
    assert timer.display() == "00:01"

    assert timer.tick() is Phase.FOCUS
    assert timer.phase is Phase.BREAK
    assert timer.label() == "Pause"
    assert timer.remaining_s == 60
    assert timer.completed_focus == 1
    assert timer.running is True

    assert timer.tick(60) is Phase.BREAK
    assert timer.phase is Phase.FOCUS
    assert timer.completed_focus == 1


def test_pause_longue_au_quatrieme_cycle():
    timer = started_timer()
    for cycle in range(3):
        assert timer.tick(60) is Phase.FOCUS
        assert timer.phase is Phase.BREAK, f"cycle {cycle + 1}"
        assert timer.tick(60) is Phase.BREAK

    assert timer.tick(60) is Phase.FOCUS
    assert timer.phase is Phase.LONG_BREAK
    assert timer.label() == "Pause longue"
    assert timer.remaining_s == 120
    assert timer.completed_focus == 4

    assert timer.tick(120) is Phase.LONG_BREAK
    assert timer.phase is Phase.FOCUS
    # Le compteur de cycles repart : la 5e concentration mène à une pause courte.
    assert timer.tick(60) is Phase.FOCUS
    assert timer.phase is Phase.BREAK
    assert timer.completed_focus == 5


def test_tick_ignore_a_l_arret():
    timer = PomodoroTimer(CONFIG)
    assert timer.running is False
    assert timer.tick(30) is None
    assert timer.remaining_s == 60
    assert timer.elapsed_s() == 0

    timer.start()
    timer.tick(30)
    timer.pause()
    assert timer.tick(30) is None
    assert timer.remaining_s == 30

    with pytest.raises(ValueError):
        timer.tick(-1)


def test_reset_repart_de_zero():
    timer = started_timer()
    timer.tick(60)
    timer.reset()
    assert timer.phase is Phase.FOCUS
    assert timer.running is False
    assert timer.completed_focus == 0
    assert timer.remaining_s == 60


def test_skip_passe_a_la_phase_suivante_sans_la_compter():
    timer = started_timer()
    timer.tick(10)
    timer.skip()
    assert timer.phase is Phase.BREAK
    assert timer.completed_focus == 0
    assert timer.remaining_s == 60

    timer.skip()
    assert timer.phase is Phase.FOCUS

    # Trois concentrations achevées puis une passée : pas encore de pause longue.
    for _ in range(3):
        timer.tick(60)
        timer.tick(60)
    timer.skip()
    assert timer.phase is Phase.BREAK


def test_apply_config():
    timer = PomodoroTimer(CONFIG)
    timer.apply_config(PomodoroConfig(focus_min=50, break_min=10, long_break_min=20, cycles_before_long=2))
    assert timer.remaining_s == 3000
    assert timer.display() == "50:00"

    timer.start()
    timer.tick(100)
    timer.apply_config(PomodoroConfig(focus_min=25, break_min=5, long_break_min=15, cycles_before_long=2))
    assert timer.remaining_s == 2900, "une phase en cours garde son compte à rebours"

    assert timer.tick(2900) is Phase.FOCUS
    assert timer.remaining_s == 300, "la phase suivante utilise la nouvelle configuration"

    with pytest.raises(ValueError):
        timer.apply_config(PomodoroConfig(focus_min=0))
    with pytest.raises(ValueError):
        timer.apply_config(PomodoroConfig(cycles_before_long=0))


def test_log_refuse_une_duree_nulle(store: TimeStore):
    today = date.today()
    with pytest.raises(ValueError):
        store.log(moment(today, 9), moment(today, 9), 0)
    with pytest.raises(ValueError):
        store.log(moment(today, 9), moment(today, 10), -60)
    assert store.entries() == []

    entry = store.log(moment(today, 9), moment(today, 10), 1500, kind="focus")
    assert entry.id > 0
    assert entry.task_id is None
    assert entry.duration_s == 1500


def test_total_du_jour(store: TimeStore):
    today = date.today()
    hier = today - timedelta(days=1)
    store.log(moment(today, 9), moment(today, 10), 1500)
    store.log(moment(today, 14), moment(today, 15), 900)
    store.log(moment(today, 15), moment(today, 15), 300, kind="break")
    store.log(moment(hier, 9), moment(hier, 10), 3600)

    assert store.total_seconds() == 2400
    assert store.total_seconds(today, kind="break") == 300
    assert store.total_seconds(hier) == 3600
    assert store.total_seconds(today + timedelta(days=1)) == 0

    du_jour = store.entries(today)
    assert [entry.started_at for entry in du_jour] == [
        moment(today, 15),
        moment(today, 14),
        moment(today, 9),
    ]
    assert len(store.entries()) == 4


def test_week_totals_sur_sept_jours_avec_un_jour_vide(store: TimeStore):
    fin = date(2026, 9, 24)
    store.log(moment(fin - timedelta(days=6), 9), moment(fin - timedelta(days=6), 10), 600)
    store.log(moment(fin - timedelta(days=3), 9), moment(fin - timedelta(days=3), 10), 1200)
    store.log(moment(fin - timedelta(days=3), 11), moment(fin - timedelta(days=3), 12), 300)
    store.log(moment(fin, 8), moment(fin, 9), 1500)
    store.log(moment(fin, 9), moment(fin, 9), 300, kind="break")
    store.log(moment(fin - timedelta(days=7), 9), moment(fin - timedelta(days=7), 10), 9999)

    semaine = store.week_totals(fin)
    assert len(semaine) == 7
    assert [jour for jour, _ in semaine] == [fin - timedelta(days=offset) for offset in range(6, -1, -1)]
    assert [total for _, total in semaine] == [600, 0, 0, 1500, 0, 0, 1500]


def test_per_task_avec_et_sans_tache(store: TimeStore):
    tasks = TaskStore(store.conn)
    banque = tasks.add("Appeler la banque")
    rapport = tasks.add("Rédiger le rapport")
    today = date.today()
    hier = today - timedelta(days=1)

    store.log(moment(hier, 9), moment(hier, 10), 600, task_id=banque.id)
    store.log(moment(today, 9), moment(today, 10), 1500, task_id=rapport.id)
    store.log(moment(today, 11), moment(today, 12), 900, task_id=rapport.id)
    store.log(moment(today, 14), moment(today, 15), 1800)
    store.log(moment(today, 15), moment(today, 15), 300, kind="break", task_id=banque.id)

    assert store.per_task() == [
        ("Rédiger le rapport", 2400),
        ("Sans tâche", 1800),
        ("Appeler la banque", 600),
    ]
    assert store.per_task(since=today) == [
        ("Rédiger le rapport", 2400),
        ("Sans tâche", 1800),
    ]

    # Une tâche supprimée laisse son temps sous « Sans tâche ».
    tasks.delete(rapport.id)
    assert store.per_task(since=today) == [("Sans tâche", 4200)]
