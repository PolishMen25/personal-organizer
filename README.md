# Personal Organizer

Organiseur personnel de bureau pour Windows : vos tâches, vos notes, votre minuteur de
concentration et le rangement de vos fichiers, dans une seule fenêtre qui ne parle à personne.

## Ce que fait l'application

La barre latérale donne accès à cinq volets.

| Volet | À quoi il sert |
| --- | --- |
| **Aujourd'hui** | Saisie rapide d'une tâche, puis trois sections : *En retard*, *Aujourd'hui* et *Important* (priorité haute sans échéance). Une ligne de résumé compte les tâches terminées du jour et le temps de concentration. |
| **Tâches** | La liste complète, filtrable par statut (*À faire*, *Terminées*, *Toutes*), par projet et par recherche plein texte. Double-clic pour ouvrir la fiche d'édition : titre, notes, priorité, échéance, projet. |
| **Notes** | Notes en texte brut ou Markdown, liste à gauche, éditeur à droite. Enregistrement automatique pendant la frappe, épinglage, recherche. |
| **Concentration** | Minuteur pomodoro (concentration, pause, pause longue) rattachable à une tâche, journal des sessions du jour et histogramme des sept derniers jours. |
| **Fichiers** | Rangement d'un dossier selon vos règles — avec analyse préalable, sélection fichier par fichier et annulation — et recherche de doublons par comparaison du contenu. |

## 100 % local

Aucune donnée ne quitte le poste. L'application n'ouvre aucune connexion réseau : pas de compte,
pas de synchronisation, pas de cloud, pas de télémétrie, pas de mise à jour automatique. Tout vit
dans un fichier SQLite et un fichier de règles JSON, sur votre disque.

La seule dépendance d'exécution est PySide6 (Qt pour Python) ; tout le reste tient dans la
bibliothèque standard. L'exécutable Windows est même construit sans les modules réseau de Qt
(voir `packaging/organizer.spec`).

## Installation sur Windows

### Voie 1 — l'exécutable, sans rien installer

`PersonalOrganizer.exe` est un fichier unique : pas d'installeur, pas de droits administrateur,
pas de Python à installer.

- **Depuis une release** : ouvrez la page *Releases* du dépôt et récupérez
  `PersonalOrganizer.exe` joint à la version voulue. Ces releases sont publiées
  automatiquement quand une étiquette `v*` est poussée.
- **Depuis les artefacts GitHub Actions** : onglet *Actions*, workflow **Build Windows**,
  dernière exécution réussie, section *Artifacts*, archive `PersonalOrganizer-exe`.
  Ces artefacts sont conservés 30 jours.

Le binaire n'étant pas signé, Windows peut afficher un avertissement SmartScreen au premier
lancement (*Informations complémentaires* → *Exécuter quand même*).

### Voie 2 — depuis les sources

Python 3.11 ou plus récent est nécessaire.

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m organizer
```

Pour reconstruire l'exécutable vous-même, ajoutez les outils de développement
(`pip install -r requirements-dev.txt`) puis :

```bat
python -m PyInstaller --noconfirm --clean packaging/organizer.spec
```

Le résultat est déposé dans `dist\PersonalOrganizer.exe`.

## Où sont vos données, et comment les sauvegarder

Sous Windows, tout est réuni dans **`%APPDATA%\PersonalOrganizer`** :

| Fichier | Contenu |
| --- | --- |
| `organizer.db` | Base SQLite : tâches, notes, sessions de concentration, journal des déplacements de fichiers, réglages (taille de la fenêtre, dernier volet affiché, dernier dossier choisi dans le volet *Fichiers*, durées du minuteur). |
| `regles-fichiers.json` | Vos règles de rangement. Créé avec les règles par défaut s'il est absent. Une règle invalide est écartée sans toucher aux autres ; un fichier entièrement illisible est renommé en `regles-fichiers.invalide-<horodatage>.json` avant d'être remplacé, jamais effacé. |

Collez `%APPDATA%\PersonalOrganizer` dans la barre d'adresse de l'explorateur pour y aller
directement.

**Sauvegarde** : fermez l'application, copiez le dossier entier ailleurs (disque externe,
partage réseau). **Restauration** : fermez l'application, remettez les fichiers en place.
Ce sont de simples fichiers, rien d'autre n'est à réinstaller.

**Si l'application refuse de démarrer**, elle l'explique en français et nomme le fichier en cause.
Pour une base endommagée (coupure de courant, synchronisation interrompue) : renommez
`organizer.db` en `organizer.db.corrompue`, relancez — l'application repart d'une base neuve — puis
remettez une sauvegarde à la place du fichier renommé si vous en avez une.

La variable d'environnement `ORGANIZER_DATA_DIR` permet de déplacer ce dossier, par exemple pour
une installation portable sur clé USB. Elle doit désigner un **chemin absolu** : un chemin relatif
est refusé avec un message explicite, plutôt que de créer une base à côté de l'exécutable — donc
une base différente selon le raccourci utilisé pour lancer l'application. Hors Windows, les données vont dans
`$XDG_DATA_HOME/PersonalOrganizer` ou, à défaut, `~/.local/share/PersonalOrganizer`.

## Syntaxe de la saisie rapide

Le champ de saisie des volets *Aujourd'hui* et *Tâches* accepte trois raccourcis, où que vous les
placiez dans la ligne. Ce qui reste forme le titre.

| Vous tapez | Effet |
| --- | --- |
| `!1` `!2` `!3` | Priorité haute, normale (par défaut) ou basse |
| `#projet` | Projet, par exemple `#admin` |
| `aujourd'hui`, `auj`, `demain`, `après-demain` | Échéance relative |
| `lundi` … `dimanche` | Prochaine occurrence de ce jour (jamais aujourd'hui) |
| `12/10` | Échéance au 12 octobre ; l'année suivante si la date est déjà passée |
| `12/10/2027`, `12/10/27` | Échéance à une date précise |

Exemple : `Appeler la banque demain !1 #admin` crée la tâche « Appeler la banque », en priorité
haute, échue demain, dans le projet `admin`.

Entrée valide la saisie. Si le titre est vide une fois les raccourcis retirés, la ligne reste en
place avec un message d'erreur. Le bouton *Aide sur la syntaxe* du volet *Tâches* rappelle tout
cela dans l'application.

## Raccourcis clavier

| Raccourci | Effet |
| --- | --- |
| `Ctrl+1` … `Ctrl+5` | Aller à *Aujourd'hui*, *Tâches*, *Notes*, *Concentration*, *Fichiers* |
| `Ctrl+Q` | Fermer l'application (les modifications en attente sont enregistrées) |
| `Entrée` | Dans un champ de saisie rapide : créer la tâche |
| `Suppr` | Dans la liste du volet *Tâches* : supprimer la tâche sélectionnée, après confirmation |
| Double-clic | Dans la liste du volet *Tâches* : ouvrir la fiche d'édition |
| `Ctrl+N` | *Notes* : nouvelle note |
| `Ctrl+S` | *Notes* : enregistrer tout de suite, sans attendre l'enregistrement automatique |
| `Ctrl+F` | *Notes* : aller au champ de recherche |

Les trois derniers n'agissent que lorsque le volet *Notes* est affiché.

## Avertissement : le rangement de fichiers déplace de vrais fichiers

Le volet *Fichiers* écrit sur votre disque. Trois garanties, et une méthode.

- **Analysez toujours avant de ranger.** Le bouton *Analyser* ne fait que lire le dossier et
  proposer un tableau : fichier, dossier d'arrivée, règle appliquée. Rien n'est déplacé.
  Décochez ce que vous ne voulez pas, puis *Ranger la sélection*, et confirmez.
- **L'annulation existe.** Chaque rangement est journalisé comme un lot ; *Annuler le dernier
  rangement* remet les fichiers à leur place d'origine. Le retour est refusé, fichier par
  fichier, si quelque chose porte de nouveau le nom d'origine.
- **L'application ne supprime jamais de fichier, et n'écrase jamais rien.** Si le nom est déjà
  pris à l'arrivée, le fichier est renommé `nom (2).ext`. La recherche de doublons, elle, se
  contente d'afficher les groupes trouvés : aucune suppression, aucun déplacement.

À savoir également : seul le contenu direct du dossier choisi est analysé, jamais ses
sous-dossiers ; les dossiers, les liens, les fichiers cachés ou système et les fichiers
temporaires (`~$*`, `*.part`, `*.crdownload`, `*.tmp`, `*.lnk`) sont laissés de côté ; les
destinations sont relatives à votre dossier personnel. Un fichier verrouillé par une autre
application n'interrompt pas le lot : il est signalé dans le compte rendu final.

Le format des règles et tous les garde-fous sont détaillés dans
[docs/regles-fichiers.md](docs/regles-fichiers.md).

## Lancer les tests

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest
```

Sur une machine sans affichage (serveur, conteneur, intégration continue), demandez à Qt de
rendre hors écran :

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

La suite couvre les dates, la base, les tâches, les notes, le minuteur, le rangement de fichiers
et l'interface (chaque volet est monté et manipulé pour de bon). Deux tests marqués `xfail`
documentent des bogues connus de la validation des tâches ; ils ne font pas échouer la suite.

Le workflow **Tests** (`.github/workflows/tests.yml`) rejoue la suite sur Python 3.11 et 3.12 à
chaque poussée et chaque pull request. Le workflow **Build Windows**
(`.github/workflows/build-windows.yml`) rejoue la suite puis construit l'exécutable.

## Documentation

- [docs/guide-demarrage.md](docs/guide-demarrage.md) — prise en main pas à pas.
- [docs/regles-fichiers.md](docs/regles-fichiers.md) — format des règles de rangement.
- [docs/architecture.md](docs/architecture.md) — organisation du code, pour contribuer.

## Feuille de route

Rien de ce qui suit n'est encore implémenté.

- **Assistant de tri par IA locale**, via un [Ollama](https://ollama.com) déjà installé sur le
  poste ou sur le réseau local : proposer une règle à partir des fichiers non classés, deviner le
  bon dossier pour un nom ambigu. L'appel resterait facultatif, désactivé par défaut, et dirigé
  vers une adresse que vous choisissez — aucune donnée envoyée à un service en ligne.
- **Widget d'état du homelab** : un coup d'œil sur ses machines et ses services depuis le volet
  *Aujourd'hui*.
- **Export** : tâches, notes et temps passé en CSV, JSON et Markdown, pour archiver ou traiter
  ailleurs.
