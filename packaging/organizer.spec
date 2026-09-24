# -*- mode: python ; coding: utf-8 -*-
"""Spécification PyInstaller : un exécutable Windows unique, sans console.

Usage depuis la racine du dépôt :
    python -m PyInstaller --noconfirm --clean packaging/organizer.spec
Le binaire est déposé dans dist/PersonalOrganizer.exe.
"""

from pathlib import Path

# La spec est exécutée depuis packaging/ ; la racine du dépôt est le dossier parent.
ROOT = Path(SPECPATH).resolve().parent

# organizer/__main__.py utilise des imports relatifs, inutilisables comme script gelé :
# on écrit un lanceur à imports absolus dans le dossier de travail de PyInstaller.
BUILD_DIR = Path(globals().get("workpath", ROOT / "build"))
BUILD_DIR.mkdir(parents=True, exist_ok=True)
ENTRY_POINT = BUILD_DIR / "personal_organizer_launcher.py"
ENTRY_POINT.write_text(
    "from organizer.app import main\n\nraise SystemExit(main())\n",
    encoding="utf-8",
)

# L'application n'utilise que QtCore, QtGui et QtWidgets : tout le reste de Qt est
# écarté pour ne pas embarquer des centaines de mégaoctets de modules inutilisés.
QT_EXCLUDES = [
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtConcurrent",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets",
    "PySide6.QtHelp",
    "PySide6.QtHttpServer",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    # Application entièrement locale : aucun accès réseau, donc aucune pile réseau Qt.
    "PySide6.QtNetwork",
    "PySide6.QtNetworkAuth",
    "PySide6.QtNfc",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtPrintSupport",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialBus",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtStateMachine",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtUiTools",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
    "PySide6.QtWebView",
    "PySide6.QtXml",
    "PySide6.scripts",
]

# Modules de la bibliothèque standard et outils de test qui n'ont rien à faire
# dans une application livrée.
OTHER_EXCLUDES = [
    "IPython",
    "numpy",
    "pytest",
    "tkinter",
]

a = Analysis(
    [str(ENTRY_POINT)],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=QT_EXCLUDES + OTHER_EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PersonalOrganizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX est absent des exécuteurs GitHub et abîme certaines DLL Qt.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # Mode fenêtre : pas de console noire derrière l'interface.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
