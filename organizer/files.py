"""Rangement de fichiers : règles, plan de déplacement, annulation et doublons.

Le moteur déplace de vrais fichiers, donc la sûreté prime : `plan()` se contente
de lire le disque, `apply()` seul écrit, aucun fichier n'est jamais écrasé ni
supprimé, et chaque déplacement réussi est journalisé *aussitôt* pour que le
journal ne soit jamais en retard sur le disque, même si l'opération est
interrompue. Les trois opérations longues acceptent un `Progress` : elles
rendent compte de leur avancement et s'arrêtent à la demande, de façon à
pouvoir tourner hors du fil graphique.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from fnmatch import fnmatchcase
from functools import cache
from pathlib import Path, PureWindowsPath
from typing import Any
from uuid import uuid4

from .dates import now_iso

# Fichiers en cours d'écriture ou de téléchargement, et raccourcis Windows.
TEMP_PATTERNS = ("~$*", "*.part", "*.crdownload", "*.tmp", "*.lnk")

# Attributs Windows qui désignent un fichier que l'utilisateur ne voit pas.
_HIDDEN_ATTRIBUTES = stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM

# Taille de lecture pour le hachage des doublons : 1 Mio.
CHUNK_SIZE = 1024 * 1024

# Deux fichiers de même taille sont d'abord comparés sur leur début seulement :
# 64 Kio suffisent à séparer des vidéos différentes sans les lire en entier.
PREFIX_SIZE = 64 * 1024

# Caractères et noms qu'un composant de dossier Windows ne peut pas porter.
_FORBIDDEN_CHARS = '<>:"|?*'
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)

# Métacaractères de fnmatch à neutraliser : seul « * » reste un joker.
_PATTERN_SPECIAL = "?[]"


class Cancelled(Exception):
    """L'utilisateur a demandé l'arrêt d'une opération longue."""


class Progress:
    """Avancement d'une opération longue, et demande d'annulation.

    L'interface en construit un, le confie au moteur qui tourne dans un autre
    fil, puis appelle `cancel()` depuis le fil graphique.
    """

    def __init__(self, report: Callable[[str, int, int], None] | None = None):
        self._report = report
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        """Demande l'arrêt ; l'opération s'interrompt à son prochain jalon."""
        self._cancelled = True

    def step(self, label: str, done: int = 0, total: int = 0) -> None:
        if self._report is not None:
            self._report(label, done, total)

    def checkpoint(self) -> None:
        """Lève `Cancelled` si l'arrêt a été demandé."""
        if self._cancelled:
            raise Cancelled("Opération annulée.")


_IDLE = Progress()


def _as_list(value: Any) -> list[str]:
    """Accepte une liste, une chaîne seule ou une clé absente."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _check_part(part: str, raw: str, rule_name: str) -> None:
    """Refuse un composant de dossier que Windows ne pourrait pas créer.

    La validation vaut sur toutes les plateformes : une règle écrite sous Linux
    doit rester utilisable sur la machine Windows de l'utilisateur.
    """
    prefix = f"Règle « {rule_name} » : la destination « {raw} »"
    found = next((char for char in part if char in _FORBIDDEN_CHARS), None)
    if found is not None:
        raise ValueError(f"{prefix} ne peut pas contenir « {found} ».")
    if any(ord(char) < 32 for char in part):
        raise ValueError(f"{prefix} ne peut pas contenir de caractère de contrôle.")
    if PureWindowsPath(part).stem.upper() in _RESERVED_NAMES:
        raise ValueError(f"{prefix} utilise « {part} », un nom réservé par Windows.")


def _clean_destination(destination: str, rule_name: str) -> str:
    """Valide un chemin relatif et le normalise avec des « / »."""
    raw = str(destination).strip()
    if not raw:
        raise ValueError(f"Règle « {rule_name} » : la destination ne peut pas être vide.")
    # `PureWindowsPath` reconnaît les deux séparateurs, les lettres de lecteur et les chemins réseau.
    pure = PureWindowsPath(raw)
    if pure.drive or pure.root:
        raise ValueError(f"Règle « {rule_name} » : la destination « {raw} » doit être un chemin relatif.")
    # Win32 retire les espaces et les points finaux du DERNIER segment seulement :
    # « Documents / Factures » créerait « Documents », puis chercherait
    # « Documents \ Factures » et échouerait pour chaque fichier de la règle.
    # Les espaces, saisie naturelle autour d'un « / », sont donc retirées ici.
    parts = [part.strip() for part in pure.parts if part.strip() != "."]
    if any(part == ".." for part in parts):
        raise ValueError(f"Règle « {rule_name} » : la destination « {raw} » ne peut pas contenir « .. ».")
    if any(not part for part in parts):
        raise ValueError(f"Règle « {rule_name} » : la destination « {raw} » contient un dossier sans nom.")
    if not parts:
        raise ValueError(f"Règle « {rule_name} » : la destination ne peut pas être vide.")
    for part in parts:
        if part.endswith("."):
            raise ValueError(
                f"Règle « {rule_name} » : la destination « {raw} » ne peut pas contenir « {part} » : "
                "Windows ne conserve pas le point final d'un nom de dossier."
            )
        _check_part(part, raw, rule_name)
    return "/".join(parts)


def _literal(fragment: str) -> str:
    """Neutralise les métacaractères de fnmatch dans un fragment de motif."""
    return "".join(f"[{char}]" if char in _PATTERN_SPECIAL else char for char in fragment)


def _prepared(pattern: str) -> str:
    """Motif où seul « * » reste un joker.

    Les crochets d'un nom de fichier ordinaire (« photo[1].jpg ») ne doivent pas
    être lus comme une classe de caractères, sous peine d'envoyer au rangement
    des fichiers que l'utilisateur n'a pas désignés.
    """
    return "*".join(_literal(part) for part in pattern.split("*"))


@dataclass
class Rule:
    """Une règle de rangement : où va un fichier, et lesquels sont concernés."""

    name: str
    destination: str
    extensions: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    min_age_days: int = 0
    enabled: bool = True

    def __post_init__(self) -> None:
        self.name = str(self.name).strip()
        if not self.name:
            raise ValueError("La règle doit avoir un nom.")
        self.destination = _clean_destination(self.destination, self.name)
        self.extensions = _unique(
            cleaned for item in _as_list(self.extensions) if (cleaned := item.strip().lstrip(".").casefold())
        )
        self.patterns = _unique(
            cleaned for item in _as_list(self.patterns) if (cleaned := item.strip().casefold())
        )
        try:
            self.min_age_days = int(self.min_age_days)
        except (TypeError, ValueError):
            raise ValueError(f"Règle « {self.name} » : l'âge minimum doit être un nombre de jours.") from None
        if self.min_age_days < 0:
            raise ValueError(f"Règle « {self.name} » : l'âge minimum ne peut pas être négatif.")
        self.enabled = bool(self.enabled)

    def matches(self, path: Path, today: date | None = None) -> bool:
        """Vrai si la règle est active et que le fichier lui correspond (extension ou motif)."""
        if not self.enabled:
            return False
        extension = path.suffix.lstrip(".").casefold()
        name = path.name.casefold()
        # Le test sur la fin du nom accepte aussi les extensions composées
        # (« tar.gz »), qu'un `Path.suffix` seul ne rend jamais.
        by_extension = bool(extension) and any(
            item == extension or name.endswith(f".{item}") for item in self.extensions
        )
        by_pattern = any(fnmatchcase(name, _prepared(pattern)) for pattern in self.patterns)
        if not (by_extension or by_pattern):
            return False
        return self._old_enough(path, today)

    def _old_enough(self, path: Path, today: date | None) -> bool:
        if self.min_age_days <= 0:
            return True
        try:
            modified = date.fromtimestamp(path.stat().st_mtime)
        except OSError:
            return False  # fichier illisible : on ne le range pas
        return ((today or date.today()) - modified).days >= self.min_age_days

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "destination": self.destination,
            "extensions": list(self.extensions),
            "patterns": list(self.patterns),
            "min_age_days": self.min_age_days,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Rule":
        """Construit une règle depuis le fichier JSON ; les clés absentes prennent leur défaut."""
        if not isinstance(data, dict):
            raise ValueError("Une règle doit être un objet JSON.")
        return cls(
            name=str(data.get("name", "")),
            destination=str(data.get("destination", "")),
            extensions=_as_list(data.get("extensions")),
            patterns=_as_list(data.get("patterns")),
            min_age_days=data.get("min_age_days") or 0,
            enabled=data.get("enabled", True),
        )


# L'ordre compte : la première règle qui correspond gagne, donc les règles par
# motif (une facture PDF va dans « Factures », pas dans « PDF ») passent devant.
#
# Les destinations portent le nom des dossiers *sur le disque*, pas leur nom
# affiché : les bibliothèques Windows s'appellent « Pictures », « Videos » et
# « Music », que l'explorateur montre traduites en « Images », « Vidéos » et
# « Musique ». Écrire les noms français y créerait trois dossiers neufs à côté
# des vraies bibliothèques.
DEFAULT_RULES: list[Rule] = [
    Rule("Factures", "Documents/Factures", [], ["*facture*", "*invoice*"]),
    Rule("Documents PDF", "Documents/PDF", ["pdf"]),
    Rule("Feuilles de calcul", "Documents/Tableurs", ["xlsx", "xls", "csv", "ods"]),
    Rule("Documents texte", "Documents/Textes", ["docx", "doc", "odt", "txt", "rtf"]),
    Rule("Images", "Pictures", ["jpg", "jpeg", "png", "gif", "bmp", "webp", "heic", "tiff", "svg"]),
    Rule("Vidéos", "Videos", ["mp4", "mkv", "avi", "mov", "wmv", "webm", "m4v"]),
    Rule("Audio", "Music", ["mp3", "wav", "flac", "m4a", "ogg", "aac", "wma"]),
    Rule("Installeurs", "Logiciels/Installeurs", ["exe", "msi"]),
    Rule("Images disque", "Logiciels/Images disque", ["iso", "img"]),
    Rule("Archives", "Archives", ["zip", "7z", "rar", "tar", "gz", "bz2", "xz"]),
    Rule("Scripts", "Développement/Scripts", ["ps1", "py", "sh", "bat", "cmd"]),
]


@dataclass
class RulesReport:
    """Résultat d'une lecture du fichier de règles."""

    rules: list[Rule]
    errors: list[str] = field(default_factory=list)
    # Copie du fichier d'origine, quand il a fallu l'écarter pour repartir des défauts.
    backup: Path | None = None


def _defaults() -> list[Rule]:
    """Copie neuve des règles par défaut : l'appelant peut les modifier."""
    return [Rule.from_dict(rule.to_dict()) for rule in DEFAULT_RULES]


def _set_aside(path: Path) -> Path | None:
    """Range le fichier illisible à côté, sous « .invalide-<horodatage>.json »."""
    stamp = now_iso().replace(":", "-")
    backup = path.with_name(f"{path.stem}.invalide-{stamp}{path.suffix}")
    try:
        os.replace(path, backup)
    except OSError:
        return None
    return backup


def read_rules(path: str | Path) -> RulesReport:
    """Lit les règles sans jamais détruire un fichier existant.

    Une règle fautive est laissée de côté et signalée ; les autres sont gardées.
    Un fichier entièrement illisible est renommé plutôt qu'écrasé, pour que
    l'utilisateur puisse récupérer son travail à la main.
    """
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        rules = _defaults()
        _try_save(path, rules)
        return RulesReport(rules=rules)
    except (OSError, UnicodeDecodeError) as error:
        return RulesReport(rules=_defaults(), errors=[f"Fichier de règles illisible : {error}"])

    try:
        data = json.loads(raw)
    except ValueError as error:
        return _fall_back(path, f"Fichier de règles illisible (JSON invalide : {error})")
    if not isinstance(data, list):
        return _fall_back(path, "Le fichier de règles doit contenir une liste de règles.")

    rules: list[Rule] = []
    errors: list[str] = []
    for index, item in enumerate(data, start=1):
        try:
            rules.append(Rule.from_dict(item))
        except (ValueError, TypeError, AttributeError) as error:
            errors.append(f"Règle {index} ignorée : {error}")
    if not rules:
        # Aucune règle exploitable : on travaille avec les défauts, mais le
        # fichier de l'utilisateur reste intact sur le disque.
        return RulesReport(rules=_defaults(), errors=errors or ["Le fichier de règles est vide."])
    return RulesReport(rules=rules, errors=errors)


def _fall_back(path: Path, message: str) -> RulesReport:
    """Écarte un fichier inexploitable et repart des défauts."""
    backup = _set_aside(path)
    if backup is not None:
        message += f" L'original a été conservé sous « {backup.name} »."
    rules = _defaults()
    _try_save(path, rules)
    return RulesReport(rules=rules, errors=[message], backup=backup)


def _try_save(path: Path, rules: list[Rule]) -> None:
    try:
        save_rules(path, rules)
    except OSError:
        pass  # dossier en lecture seule : l'application tourne quand même avec les défauts


def load_rules(path: str | Path) -> list[Rule]:
    """Règles utilisables du fichier ; voir `read_rules` pour le détail des erreurs."""
    return read_rules(path).rules


def save_rules(path: str | Path, rules: list[Rule]) -> None:
    """Écrit les règles en JSON indenté (UTF-8), de façon atomique."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps([rule.to_dict() for rule in rules], indent=2, ensure_ascii=False)
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        # fsync avant le remplacement : une coupure ne peut pas laisser un
        # fichier de règles vide, qui serait pris pour un fichier corrompu.
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


@dataclass
class PlannedMove:
    """Un déplacement proposé : rien n'a encore été fait sur le disque."""

    src: Path
    dst: Path
    rule: str


@dataclass
class FileBatch:
    """Un lot de déplacements journalisé, tel qu'on le propose à l'annulation."""

    batch_id: str
    moved_at: str
    count: int


def _claim_key(path: Path) -> str:
    """Clé de réservation d'une cible, toujours insensible à la casse.

    Windows ne distingue pas « Rapport.PDF » de « rapport.pdf » : deux fichiers
    qui ne diffèrent que par la casse doivent recevoir des cibles distinctes,
    quelle que soit la plateforme sur laquelle le plan est calculé.
    """
    return os.path.normcase(os.path.realpath(path)).casefold()


def _same_folder(left: Path, right: Path) -> bool:
    """Compare deux dossiers comme le fait la plateforme courante."""
    return os.path.normcase(os.path.realpath(left)) == os.path.normcase(os.path.realpath(right))


def _is_hidden(path: Path) -> bool:
    if path.name.startswith("."):
        return True
    try:
        attributes = getattr(path.stat(), "st_file_attributes", 0)
    except OSError:
        return True  # illisible : on préfère le laisser en place
    return bool(attributes & _HIDDEN_ATTRIBUTES)


def _is_ignored(path: Path) -> bool:
    """Dossiers, liens, fichiers cachés et fichiers temporaires : on n'y touche pas."""
    if path.is_symlink() or not path.is_file():
        return True
    name = path.name.casefold()
    if any(fnmatchcase(name, pattern) for pattern in TEMP_PATTERNS):
        return True
    return _is_hidden(path)


def _is_regular(path: Path) -> bool:
    """Vrai si `path` est un fichier ordinaire, sans lever si le chemin est douteux."""
    try:
        return path.is_file() and not path.is_symlink()
    except OSError:
        return False


def _size(path: Path) -> int:
    """Taille du fichier, ou 0 s'il n'est plus lisible."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _moved_despite_error(source: Path, destination: Path, size: int) -> bool:
    """Vrai seulement si le déplacement est PROUVÉ malgré l'erreur levée.

    Une source qui ne répond plus n'a pas forcément disparu : clé USB retirée,
    partage réseau coupé. Conclure au succès sur ce seul indice garderait une
    copie tronquée sous le nom canonique, journalisée comme réussie, et
    l'annulation serait ensuite refusée sans fin. Il faut donc que la
    destination ait la taille relevée avant le déplacement, et que le dossier
    source soit lisible et ne contienne plus le fichier.
    """
    if not _is_regular(destination) or _size(destination) != size:
        return False
    try:
        names = os.listdir(source.parent)
    except OSError:
        return False  # dossier injoignable : l'absence de la source n'est pas prouvée
    return source.name not in names


def _discard(path: Path) -> None:
    """Efface un reste de copie inachevée ; l'échec ne doit rien interrompre."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _absolute(path: str | Path) -> Path:
    """Chemin absolu, sans résoudre liens ni jonctions.

    Le journal doit désigner le même endroit quel que soit le dossier courant au
    moment de l'annulation. Sous Windows, « \\Users\\Jean » et « C:Downloads »
    ont l'air absolus mais ne le sont pas : ils dépendent du lecteur ou du
    dossier courant.
    """
    return Path(os.path.abspath(path))


def _existing_folder(folder: str | Path) -> Path:
    path = _absolute(folder)
    if not path.is_dir():
        raise ValueError(f"Le dossier « {path} » est introuvable.")
    return path


def _iter_files(folder: Path, recursive: bool) -> Iterator[Path]:
    """Parcourt les entrées du dossier dans un ordre stable, sans suivre les liens."""
    if not recursive:
        yield from sorted(folder.iterdir(), key=lambda item: (item.name.casefold(), item.name))
        return
    for root, folders, names in os.walk(folder):
        current = Path(root)
        # Même critère que pour les fichiers : le nom en « . » de POSIX *et*
        # l'attribut caché ou système de Windows, où aucun dossier caché
        # (AppData, $RECYCLE.BIN…) ne commence par un point.
        folders[:] = sorted(
            name
            for name in folders
            if not _is_hidden(current / name) and not (current / name).is_symlink()
        )
        for name in sorted(names, key=lambda item: (item.casefold(), item)):
            yield current / name


def _occupied(path: Path) -> bool:
    """Vrai si quelque chose occupe déjà `path`.

    Une erreur autre que « absent » (dossier interdit) signifie que
    l'emplacement est inutilisable : elle remonte au lieu de laisser croire
    que la place est libre.
    """
    try:
        return path.exists() or path.is_symlink()
    except OSError as error:
        raise OSError(f"L'emplacement « {path} » est inutilisable : {error}") from error


# Longueur maximale d'un nom de fichier ou de dossier : 255 unités UTF-16 sous
# Windows (NTFS, exFAT), 255 octets ailleurs (ext4, APFS).
MAX_NAME_LENGTH = 255
# Sans prise en charge des chemins longs, réglage par défaut de Windows 10 et 11 :
# un chemin complet doit rester sous MAX_PATH, un dossier à créer sous 248.
MAX_PATH = 260
MAX_DIRECTORY_PATH = 248


def _windows() -> bool:
    return os.name == "nt"


def _name_length(name: str) -> int:
    if _windows():
        # « surrogatepass » : NTFS accepte une moitié d'emoji isolée, qu'un
        # navigateur laisse en tronquant un nom ; `listdir` la rend telle quelle.
        return len(name.encode("utf-16-le", "surrogatepass")) // 2
    return len(os.fsencode(name))


@cache
def _long_paths_enabled() -> bool:
    """Vrai si ce processus peut dépasser MAX_PATH.

    Il faut à la fois le réglage système LongPathsEnabled et un exécutable
    déclaré longPathAware ; `RtlAreLongPathsEnabled` combine les deux. Dans le
    doute, la limite classique s'applique : mieux vaut un refus au plan qu'un
    échec au rangement.
    """
    if not _windows():
        return True
    try:
        import ctypes

        function = ctypes.WinDLL("ntdll").RtlAreLongPathsEnabled
        function.argtypes = []
        function.restype = ctypes.c_ubyte  # BOOLEAN : un seul octet significatif
        return bool(function())
    except (AttributeError, OSError):
        return False


def _check_path_lengths(path: Path) -> None:
    """Refuse un chemin que le système de fichiers ne pourrait pas créer.

    `_occupied` ne peut pas s'en apercevoir partout : sous Windows,
    `Path.exists()` répond « absent » à un nom ou un chemin trop long au lieu de
    lever une erreur, et la place paraîtrait libre.
    """
    try:
        for part in path.parts:
            if _name_length(part) > MAX_NAME_LENGTH:
                raise OSError(
                    f"L'emplacement « {path} » est inutilisable : le nom « {part[:40]}… » "
                    f"dépasse {MAX_NAME_LENGTH} caractères."
                )
        if _windows() and not _long_paths_enabled():
            full = os.path.abspath(path)
            if _name_length(full) >= MAX_PATH:
                raise OSError(
                    f"L'emplacement « {path} » est inutilisable : le chemin complet dépasse "
                    f"{MAX_PATH - 1} caractères. Raccourcissez le nom du fichier ou la "
                    "destination de la règle, ou activez les chemins longs de Windows."
                )
            directory = os.path.dirname(full)
            # La limite de 248 vaut pour CRÉER un dossier : dans un dossier qui
            # existe déjà, seul compte le chemin complet.
            if _name_length(directory) >= MAX_DIRECTORY_PATH and not os.path.isdir(directory):
                raise OSError(
                    f"L'emplacement « {path} » est inutilisable : le dossier de destination "
                    f"dépasse {MAX_DIRECTORY_PATH - 1} caractères."
                )
    except UnicodeError as error:
        # Une ValueError ne serait pas rattrapée par `plan()` : l'analyse de tout
        # le dossier s'arrêterait, au lieu d'écarter ce seul fichier.
        raise OSError(f"L'emplacement « {path} » est inutilisable : nom illisible ({error}).") from error


def _free_path(folder: Path, name: str, claimed: set[str]) -> Path:
    """Chemin libre dans `folder` : « nom (2).ext », « nom (3).ext »… si besoin.

    `claimed` retient les cibles déjà réservées par le lot en cours, pour que deux
    fichiers différents ne visent jamais le même chemin final. Chaque candidat
    est contrôlé : le suffixe « (2) » peut faire dépasser la limite à un nom
    qui la frôlait.
    """
    candidate = folder / name
    stem, suffix = candidate.stem, candidate.suffix
    index = 2
    while True:
        _check_path_lengths(candidate)
        if _claim_key(candidate) not in claimed and not _occupied(candidate):
            break
        candidate = folder / f"{stem} ({index}){suffix}"
        index += 1
    claimed.add(_claim_key(candidate))
    return candidate


def _identity(path: Path) -> tuple[int, int] | None:
    """Identité du fichier physique : `(st_dev, st_ino)`, renseignés aussi sous Windows."""
    try:
        info = path.stat()
    except OSError:
        return None
    if not info.st_ino:  # certains systèmes de fichiers ne numérotent pas
        return None
    return (info.st_dev, info.st_ino)


def _digest(path: Path, limit: int | None = None) -> str | None:
    """Empreinte blake2b lue par blocs ; `None` si le fichier est illisible.

    `limit` borne la lecture aux premiers octets, pour écarter deux fichiers de
    même taille sans les lire en entier.
    """
    digest = hashlib.blake2b()
    remaining = limit if limit is not None else -1
    try:
        with path.open("rb") as handle:
            while remaining != 0:
                size = CHUNK_SIZE if remaining < 0 else min(CHUNK_SIZE, remaining)
                chunk = handle.read(size)
                if not chunk:
                    break
                digest.update(chunk)
                if remaining > 0:
                    remaining -= len(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _grouped_by_digest(
    paths: list[Path], limit: int | None, progress: Progress
) -> Iterator[list[Path]]:
    """Regroupe des fichiers de même taille par empreinte, complète ou bornée."""
    by_digest: dict[str, list[Path]] = {}
    for path in paths:
        progress.checkpoint()
        digest = _digest(path, limit)
        if digest is not None:
            by_digest.setdefault(digest, []).append(path)
    for group in by_digest.values():
        if len(group) >= 2:
            yield group


class FileOrganizer:
    """Range un dossier selon des règles, journalise les lots et sait les annuler."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        rules: list[Rule],
        target_root: str | Path | None = None,
    ):
        self.conn = conn
        self.rules = list(rules)
        self.target_root = _absolute(target_root if target_root is not None else Path.home())
        # Échecs (chemin, message) du dernier `apply()` ou `undo()`, à montrer à l'utilisateur.
        self.last_failures: list[tuple[Path, str]] = []

    def plan(
        self,
        folder: str | Path,
        today: date | None = None,
        progress: Progress | None = None,
    ) -> list[PlannedMove]:
        """Propose les déplacements d'un dossier. Ne touche à rien sur le disque.

        Un fichier dont la destination est inutilisable (chemin trop long, dossier
        interdit) est laissé de côté et signalé dans `last_failures`, au lieu de
        faire échouer l'analyse du dossier entier.
        """
        progress = progress or _IDLE
        source_folder = _existing_folder(folder)
        today = today or date.today()
        claimed: set[str] = set()
        moves: list[PlannedMove] = []
        self.last_failures = []
        paths = [path for path in _iter_files(source_folder, recursive=False) if not _is_ignored(path)]
        for index, path in enumerate(paths, start=1):
            progress.checkpoint()
            progress.step(f"Analyse de « {path.name} »", index, len(paths))
            rule = self._first_match(path, today)
            if rule is None:
                continue
            destination_folder = self.target_root.joinpath(*rule.destination.split("/"))
            try:
                if _same_folder(path.parent, destination_folder):
                    continue  # déjà rangé
                destination = _free_path(destination_folder, path.name, claimed)
            except OSError as error:
                self.last_failures.append((path, str(error)))
                continue
            moves.append(PlannedMove(src=path, dst=destination, rule=rule.name))
        return moves

    def apply(self, moves: list[PlannedMove], progress: Progress | None = None) -> str:
        """Exécute les déplacements et renvoie l'identifiant du lot.

        Un échec (fichier verrouillé, permission refusée) est collecté dans
        `last_failures` et n'interrompt pas le reste du lot. Chaque déplacement
        réussi est journalisé aussitôt, dans sa propre transaction : une
        interruption laisse un lot plus court, jamais un lot inannulable.
        """
        progress = progress or _IDLE
        batch_id = uuid4().hex
        self.last_failures = []
        claimed: set[str] = set()
        for index, move in enumerate(moves, start=1):
            if progress.cancelled:
                break  # ce qui est déjà déplacé reste journalisé, donc annulable
            source = Path(move.src)
            progress.step(f"Rangement de « {source.name} »", index, len(moves))
            if not (source.is_absolute() and Path(move.dst).is_absolute()):
                # Un chemin relatif journalisé serait résolu à l'annulation contre un
                # autre dossier courant, et le fichier partirait ailleurs.
                self.last_failures.append((source, "chemin relatif refusé : il rendrait l'annulation incertaine"))
                continue
            try:
                destination = self._move_file(source, Path(move.dst), claimed)
            except OSError as error:
                self.last_failures.append((source, str(error)))
                continue
            self._journal(batch_id, source, destination)
        return batch_id

    def _move_file(self, source: Path, destination: Path, claimed: set[str]) -> Path:
        """Déplace un fichier et renvoie la destination réellement utilisée."""
        if source.is_symlink() or not source.is_file():
            raise OSError(f"« {source} » n'est plus un fichier à déplacer")
        # La cible a pu apparaître depuis le plan : on renomme plutôt que d'écraser.
        destination = _free_path(destination.parent, destination.name, claimed)
        destination.parent.mkdir(parents=True, exist_ok=True)
        size = _size(source)
        try:
            shutil.move(str(source), str(destination))
        except OSError:
            if _moved_despite_error(source, destination, size):
                return destination
            # Vers un autre volume, `shutil.move` copie puis supprime : une copie
            # interrompue laisserait à destination un fichier tronqué portant le
            # nom canonique. `_free_path` a garanti que la place était libre,
            # donc ce qui s'y trouve vient de cette copie et doit disparaître.
            # L'original, lui, n'a pas été supprimé.
            _discard(destination)
            raise
        return destination

    def _journal(self, batch_id: str, source: Path, destination: Path) -> None:
        """Enregistre un déplacement réussi ; un échec d'écriture est signalé, pas levé."""
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO file_moves (batch_id, src, dst, moved_at) VALUES (?, ?, ?, ?)",
                    (batch_id, str(source), str(destination), now_iso()),
                )
        except sqlite3.Error as error:
            self.last_failures.append(
                (source, f"déplacé vers « {destination} » mais non journalisé : {error}")
            )

    def undo(self, batch_id: str, progress: Progress | None = None) -> int:
        """Remet en place les fichiers du lot ; renvoie le nombre de retours réussis.

        Chaque ligne est marquée aussitôt après son retour, dans sa propre
        transaction : une annulation interrompue ne laisse pas un journal qui
        proposerait indéfiniment des fichiers déjà revenus.
        """
        progress = progress or _IDLE
        rows = self.conn.execute(
            "SELECT id, src, dst FROM file_moves WHERE batch_id = ? AND undone = 0 ORDER BY id DESC",
            (batch_id,),
        ).fetchall()
        self.last_failures = []
        restored = 0
        for index, row in enumerate(rows, start=1):
            if progress.cancelled:
                break
            source, destination = Path(row["src"]), Path(row["dst"])
            progress.step(f"Retour de « {source.name} »", index, len(rows))
            try:
                moved = self._restore_file(source, destination)
            except OSError as error:
                self.last_failures.append((destination, str(error)))
                continue
            try:
                with self.conn:
                    self.conn.execute("UPDATE file_moves SET undone = 1 WHERE id = ?", (row["id"],))
            except sqlite3.Error as error:
                self.last_failures.append((source, f"remis en place mais toujours journalisé : {error}"))
                continue
            restored += int(moved)
        return restored

    def _restore_file(self, source: Path, destination: Path) -> bool:
        """Ramène un fichier à sa source ; `False` s'il y était déjà revenu."""
        if _occupied(source):
            if _occupied(destination):
                raise OSError(f"« {source} » est occupé par un autre fichier : le retour l'écraserait")
            # La source est revenue sans que le journal l'enregistre : c'est une
            # annulation précédente interrompue. Rien à faire, mais la ligne doit
            # être soldée, sinon le lot serait proposé sans fin à l'annulation.
            return False
        if destination.is_symlink() or not destination.is_file():
            raise OSError(f"« {destination} » est introuvable")
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(destination), str(source))
        return True

    def batches(self, limit: int = 20) -> list[FileBatch]:
        """Lots encore annulables, du plus récent au plus ancien."""
        rows = self.conn.execute(
            "SELECT batch_id, MAX(moved_at) AS moved_at, COUNT(*) AS count FROM file_moves "
            "WHERE undone = 0 GROUP BY batch_id ORDER BY MAX(id) DESC LIMIT ?",
            (max(0, int(limit)),),
        ).fetchall()
        return [
            FileBatch(batch_id=row["batch_id"], moved_at=row["moved_at"], count=row["count"]) for row in rows
        ]

    def find_duplicates(
        self,
        folder: str | Path,
        recursive: bool = True,
        progress: Progress | None = None,
    ) -> list[list[Path]]:
        """Groupes de fichiers au contenu identique. Ne déplace et ne supprime rien.

        Deux chemins qui désignent un seul fichier physique (lien dur, jonction
        Windows) ne forment pas un doublon : les signaler inviterait l'utilisateur
        à supprimer son unique copie.
        """
        progress = progress or _IDLE
        root = _existing_folder(folder)
        by_size: dict[int, list[Path]] = {}
        seen: set[tuple[int, int]] = set()
        examined = 0
        for path in _iter_files(root, recursive):
            progress.checkpoint()
            if _is_ignored(path):
                continue
            try:
                info = path.stat()
            except OSError:
                continue
            if not info.st_size:  # les fichiers vides sont tous « identiques » : sans intérêt
                continue
            identity = (info.st_dev, info.st_ino)
            if info.st_ino and identity in seen:
                continue  # déjà vu sous un autre nom : c'est le même fichier
            if info.st_ino:
                seen.add(identity)
            examined += 1
            progress.step(f"Inventaire : {examined} fichiers", examined, 0)
            by_size.setdefault(info.st_size, []).append(path)

        candidates = [paths for paths in by_size.values() if len(paths) >= 2]
        total = sum(len(paths) for paths in candidates)
        groups: list[list[Path]] = []
        done = 0
        for paths in candidates:
            size = _size(paths[0])  # un fichier disparu entre-temps ne fait rien échouer
            done += len(paths)
            progress.step(f"Comparaison de {total} fichiers de même taille", done, total)
            # Un préfixe suffit presque toujours à séparer deux gros fichiers ;
            # la lecture intégrale est réservée aux débuts identiques.
            if size <= PREFIX_SIZE:
                heads = [paths]
            else:
                heads = list(_grouped_by_digest(paths, PREFIX_SIZE, progress))
            for head in heads:
                for group in _grouped_by_digest(head, None, progress):
                    groups.append(sorted(group))
        groups.sort(key=lambda group: str(group[0]))
        return groups

    def _first_match(self, path: Path, today: date) -> Rule | None:
        for rule in self.rules:
            if rule.enabled and rule.matches(path, today):
                return rule
        return None
