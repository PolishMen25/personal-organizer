"""Fixtures communes : une seule QApplication, et un contexte isolé sous `tmp_path`."""

from __future__ import annotations

import os
import sys
import traceback
from collections.abc import Callable, Iterator
from types import TracebackType

import pytest

# Doit précéder l'import de Qt : sans écran, seul le greffon « offscreen » démarre.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEventLoop  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from organizer.context import AppContext  # noqa: E402
from organizer.ui.theme import apply_theme  # noqa: E402

# Nombre de passes par défaut dans la boucle d'événements.
_PUMP_ROUNDS = 4


@pytest.fixture(scope="session")
def qapp() -> Iterator[QApplication]:
    """L'unique QApplication de la suite : Qt refuse la seconde."""
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    yield app
    # Détruire la QApplication en fin de session ferait tomber les widgets survivants.


@pytest.fixture()
def ctx(tmp_path) -> Iterator[AppContext]:
    """Contexte applicatif jetable : base, règles et racine de rangement sous `tmp_path`."""
    context = AppContext.open(tmp_path / "organizer.db", tmp_path / "regles-fichiers.json")
    # Sans cela, un rangement viserait le vrai dossier personnel de l'utilisateur.
    context.files.target_root = tmp_path / "Rangé"
    yield context
    context.close()


@pytest.fixture()
def pump() -> Callable[..., None]:
    """Vide la file d'événements Qt, y compris les `QTimer.singleShot(0, …)` différés."""

    def run(rounds: int = _PUMP_ROUNDS) -> None:
        for _ in range(rounds):
            QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents)
            QCoreApplication.sendPostedEvents(None, 0)

    return run


@pytest.fixture(autouse=True)
def aucune_exception_avalee() -> Iterator[None]:
    """Fait échouer le test si un slot Qt a levé : PySide6 y route l'exception sans la propager."""
    captured: list[tuple[type[BaseException], BaseException, TracebackType | None]] = []
    previous = sys.excepthook
    sys.excepthook = lambda *info: captured.append(info)  # type: ignore[assignment]
    try:
        yield
    finally:
        sys.excepthook = previous
    if captured:
        details = "\n".join("".join(traceback.format_exception(*info)) for info in captured)
        pytest.fail(f"Exception levée dans un slot Qt :\n{details}")
