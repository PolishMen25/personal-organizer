"""Tests du formatage et de la saisie des dates."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from organizer.dates import (
    format_due,
    format_duration,
    format_long,
    now_iso,
    parse_day,
)

# Jeudi : la veille, le lendemain et le surlendemain restent dans le même mois.
JEUDI = date(2026, 9, 24)


def test_now_iso_a_la_seconde():
    stamp = now_iso()
    assert len(stamp) == 19
    assert stamp[10] == "T"
    assert datetime.fromisoformat(stamp).microsecond == 0


def test_format_long_en_francais():
    assert format_long(JEUDI) == "jeudi 24 septembre 2026"
    assert format_long(date(2026, 8, 2)) == "dimanche 2 août 2026"
    assert format_long(date(2026, 12, 31)) == "jeudi 31 décembre 2026"


@pytest.mark.parametrize(
    ("token", "attendu"),
    [
        ("aujourd'hui", JEUDI),
        ("aujourd’hui", JEUDI),  # apostrophe typographique
        ("aujourdhui", JEUDI),
        ("auj", JEUDI),
        ("AUJ", JEUDI),
        ("demain", date(2026, 9, 25)),
        ("Demain", date(2026, 9, 25)),
        ("après-demain", date(2026, 9, 26)),
        ("apres-demain", date(2026, 9, 26)),
        ("après-d", date(2026, 9, 26)),
    ],
)
def test_parse_day_mots_relatifs(token, attendu):
    assert parse_day(token, JEUDI) == attendu


def test_parse_day_jour_de_semaine_vise_toujours_le_futur():
    assert parse_day("vendredi", JEUDI) == date(2026, 9, 25)
    assert parse_day("mercredi", JEUDI) == date(2026, 9, 30)
    assert parse_day("Dimanche", JEUDI) == date(2026, 9, 27)
    # Le jour même désigne la semaine suivante, pas aujourd'hui.
    assert parse_day("jeudi", JEUDI) == date(2026, 10, 1)


def test_parse_day_jj_mm_bascule_sur_l_annee_suivante():
    assert parse_day("12/10", JEUDI) == date(2026, 10, 12)
    assert parse_day("24/09", JEUDI) == JEUDI
    assert parse_day("23/09", JEUDI) == date(2027, 9, 23)
    assert parse_day("12/03", JEUDI) == date(2027, 3, 12)
    assert parse_day("1/1", JEUDI) == date(2027, 1, 1)


def test_parse_day_annee_explicite_reste_telle_quelle():
    assert parse_day("12/10/27", JEUDI) == date(2027, 10, 12)
    assert parse_day("12/10/2027", JEUDI) == date(2027, 10, 12)
    # Une année explicite n'est jamais décalée, même dans le passé.
    assert parse_day("12/10/2020", JEUDI) == date(2020, 10, 12)
    assert parse_day("05/06/99", JEUDI) == date(2099, 6, 5)


@pytest.mark.parametrize("token", ["31/02", "30/02/2026", "31/04", "12/13", "32/01", "0/10", "12/0"])
def test_parse_day_refuse_les_dates_impossibles(token):
    assert parse_day(token, JEUDI) is None


def test_parse_day_29_fevrier():
    # Année bissextile en cours : la date existe.
    assert parse_day("29/02", date(2028, 1, 1)) == date(2028, 2, 29)
    # Année non bissextile : la date n'existe pas.
    assert parse_day("29/02", date(2026, 1, 1)) is None
    # Bissextile mais déjà passé : la bascule viserait 2029, qui ne l'est pas.
    assert parse_day("29/02", date(2028, 6, 1)) is None


@pytest.mark.parametrize("token", ["", "hier", "banque", "12-10", "2026-10-12", "12/10/5", "12/10/20265", "#admin"])
def test_parse_day_ignore_les_autres_jetons(token):
    assert parse_day(token, JEUDI) is None


def test_format_due_mots_relatifs():
    assert format_due(JEUDI - timedelta(days=1), JEUDI) == "Hier"
    assert format_due(JEUDI, JEUDI) == "Aujourd'hui"
    assert format_due(JEUDI + timedelta(days=1), JEUDI) == "Demain"


def test_format_due_cette_semaine_donne_le_jour():
    assert format_due(date(2026, 9, 26), JEUDI) == "Samedi"
    assert format_due(date(2026, 9, 30), JEUDI) == "Mercredi"


def test_format_due_meme_annee_en_jj_mm():
    assert format_due(date(2026, 10, 1), JEUDI) == "01/10"
    assert format_due(date(2026, 12, 31), JEUDI) == "31/12"
    # Au-delà d'hier, le passé repasse aussi en numérique.
    assert format_due(date(2026, 9, 20), JEUDI) == "20/09"


def test_format_due_autre_annee_avec_l_annee():
    assert format_due(date(2027, 1, 5), JEUDI) == "05/01/2027"
    assert format_due(date(2025, 12, 31), JEUDI) == "31/12/2025"


def test_format_due_sans_echeance():
    assert format_due(None, JEUDI) == ""
    assert format_due(None) == ""


def test_format_due_utilise_le_jour_courant_par_defaut():
    assert format_due(date.today()) == "Aujourd'hui"


@pytest.mark.parametrize(
    ("secondes", "attendu"),
    [
        (0, "0 min"),
        (59, "0 min"),
        (60, "1 min"),
        (90, "1 min"),
        (1500, "25 min"),
        (3540, "59 min"),
        (3600, "1 h 00"),
        (3660, "1 h 01"),
        (3900, "1 h 05"),
        (7199, "1 h 59"),
        (86400, "24 h 00"),
        (90.7, "1 min"),  # les fractions de seconde sont tronquées
    ],
)
def test_format_duration(secondes, attendu):
    assert format_duration(secondes) == attendu
