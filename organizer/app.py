"""Démarrage de l'application : contexte, thème, fenêtre principale."""

from __future__ import annotations

import sqlite3
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_TITLE, __version__
from .context import AppContext
from .db import data_dir
from .ui.main_window import MainWindow
from .ui.theme import apply_theme

# Code de sortie quand les données ne peuvent pas être ouvertes.
EXIT_DATA_ERROR = 2


def startup_error_text(error: BaseException) -> tuple[str, str]:
    """Titre et marche à suivre, en français, pour un démarrage impossible.

    La spécification PyInstaller construit avec `console=False` : sans ce
    message, l'utilisateur ne verrait qu'une trace Python en anglais.
    """
    try:
        location = str(data_dir())
    except (OSError, ValueError):
        location = ""  # le dossier est lui-même en cause : l'erreur en porte le chemin
    if isinstance(error, sqlite3.Error):
        title = "La base de données n'a pas pu être ouverte"
        place = f" dans le dossier :\n{location}\n\n" if location else ", "
        advice = (
            f"Renommez le fichier « organizer.db » en « organizer.db.corrompue »{place}"
            "puis relancez l'application : elle repartira d'une base neuve. Vous pourrez "
            "ensuite remettre une sauvegarde à la place du fichier renommé."
        )
    else:
        title = "Le dossier de données n'est pas accessible"
        advice = (
            "Vérifiez que ce dossier existe et qu'il est accessible en écriture. Vous pouvez "
            "aussi désigner un autre emplacement avec la variable d'environnement "
            "ORGANIZER_DATA_DIR, puis relancer l'application."
        )
    return title, f"{error}\n\n{advice}"


def main(argv: list[str] | None = None) -> int:
    """Lance l'interface et renvoie le code de sortie de la boucle Qt."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(list(argv) if argv else list(sys.argv))
    app.setApplicationName(APP_TITLE)
    app.setApplicationDisplayName(APP_TITLE)
    app.setApplicationVersion(__version__)
    apply_theme(app)

    try:
        ctx = AppContext.open()
    except (OSError, ValueError, sqlite3.Error) as error:
        title, detail = startup_error_text(error)
        QMessageBox.critical(None, title, detail)
        return EXIT_DATA_ERROR

    try:
        window = MainWindow(ctx)
        window.show()
        return app.exec()
    finally:
        ctx.close()
