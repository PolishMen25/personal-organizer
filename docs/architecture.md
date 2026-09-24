# Architecture

Ce document décrit le code tel qu'il est, pour qui veut le lire, le corriger ou l'étendre.

## Principes

1. **Le noyau ignore Qt.** `db`, `dates`, `tasks`, `notes`, `timer` et `files` n'importent rien de
   PySide6 : ils se testent sans interface et resteraient réutilisables ailleurs. Seuls
   `organizer/app.py` et `organizer/ui/` connaissent Qt.
2. **Une seule connexion SQLite**, ouverte par `AppContext` et partagée par tous les magasins.
   Aucun volet n'ouvre la base lui-même.
3. **Une seule dépendance d'exécution**, PySide6. Tout le reste vient de la bibliothèque standard.
4. **Aucun accès réseau.** Ni dans le code, ni dans l'exécutable : les modules réseau de Qt sont
   écartés du binaire.
5. **Tout en français** : code, commentaires, messages d'erreur (`ValueError` avec un message
   lisible par l'utilisateur), interface.

## Rôle de chaque module

### Noyau

| Fichier | Rôle |
| --- | --- |
| `organizer/__init__.py` | Version (`__version__`) et titre de l'application (`APP_TITLE`). C'est la source unique de la version, que `pyproject.toml` lit dynamiquement. |
| `organizer/__main__.py` | Point d'entrée `python -m organizer` : appelle `app.main()`. |
| `organizer/app.py` | `main()` : crée la `QApplication`, applique le thème, ouvre le contexte, montre la fenêtre, ferme le contexte à la sortie. |
| `organizer/db.py` | Emplacement des données (`data_dir()`), ouverture et migration de la base (`connect()`, `migrate()`, `MIGRATIONS`), magasin clé/valeur `Settings` sérialisé en JSON. |
| `organizer/dates.py` | Formatage et lecture des dates en français : `now_iso()`, `format_long()`, `format_due()`, `format_duration()`, `parse_day()`, et les tables `JOURS` / `MOIS`. |
| `organizer/tasks.py` | `Task`, `TaskStore` (CRUD, filtres, vue `today()`, liste des projets) et `parse_quick_add()` pour la syntaxe `!1 #projet demain`. |
| `organizer/notes.py` | `Note`, `NoteStore` (CRUD, épinglage, recherche insensible à la casse faite en Python), titre déduit du corps. |
| `organizer/timer.py` | `PomodoroTimer`, machine à états sans horloge — l'appelant rythme `tick()` — `PomodoroConfig`, `Phase`, et `TimeStore` pour le journal du temps (totaux du jour, de la semaine, par tâche). |
| `organizer/files.py` | `Rule` et son JSON (`load_rules`, `save_rules`, `DEFAULT_RULES`), `FileOrganizer` : `plan()` lit, `apply()` écrit et journalise, `undo()` revient en arrière, `find_duplicates()` compare les contenus. |
| `organizer/context.py` | `AppContext` : la connexion, les réglages, les quatre magasins, le chemin du fichier de règles. C'est l'unique objet que reçoit l'interface. |

### Interface

| Fichier | Rôle |
| --- | --- |
| `organizer/ui/theme.py` | `COLORS` et `TINTS` (source unique des couleurs), la feuille de style globale, `apply_theme()` : style Fusion, palette sombre, styles. |
| `organizer/ui/widgets.py` | Briques réutilisables : `SectionHeader`, `EmptyState`, `styled_button` (`primary`, `ghost`, `danger`), `card`. Chacune porte son propre style et reste correcte hors de `apply_theme`. |
| `organizer/ui/main_window.py` | `MainWindow` : barre latérale, en-tête, pile des volets, barre d'état, raccourcis, mémorisation de la géométrie, synchronisation des volets. La table `_SECTIONS` définit l'ordre des volets. |
| `organizer/ui/today_tab.py` | `TodayTab` : saisie rapide, sections *En retard* / *Aujourd'hui* / *Important*, résumé du jour. |
| `organizer/ui/tasks_tab.py` | `TasksTab` : tableau filtrable, `TaskDialog` (fiche d'édition), suppression par `Suppr`, aide sur la syntaxe. |
| `organizer/ui/notes_tab.py` | `NotesTab` : liste et éditeur, enregistrement automatique différé, épinglage, raccourcis `Ctrl+N/S/F` limités au volet. |
| `organizer/ui/focus_tab.py` | `FocusTab` : minuteur, journal du jour, histogramme des sept derniers jours, réglage des durées. |
| `organizer/ui/files_tab.py` | `FilesTab` : analyse, sélection, rangement, annulation, doublons, et `RulesDialog` pour éditer les règles. |

### Autour du code

| Chemin | Rôle |
| --- | --- |
| `tests/` | `test_dates.py`, `test_db.py`, `test_tasks.py`, `test_notes.py`, `test_timer.py`, `test_files.py` pour le noyau, et `test_ui_smoke.py` qui monte chaque volet pour de bon. `conftest.py` fournit les fixtures. |
| `packaging/organizer.spec` | Spécification PyInstaller : exécutable unique, sans console, modules Qt inutiles écartés. Elle écrit son propre lanceur à imports absolus dans le dossier de travail, `organizer/__main__.py` utilisant des imports relatifs. |
| `.github/workflows/tests.yml` | Suite de tests sur Python 3.11 et 3.12, Qt en mode `offscreen`. |
| `.github/workflows/build-windows.yml` | Tests puis construction de l'exécutable ; artefact à chaque exécution, release sur une étiquette `v*`. |

## Le schéma SQLite

Une seule migration pour l'instant. `MIGRATIONS` est une liste de scripts SQL ; l'indice d'un
script, plus un, devient le `user_version` de la base, ce qui permet de reprendre les migrations là
où elles se sont arrêtées. `connect()` active `PRAGMA foreign_keys = ON` et migre avant de rendre la
connexion.

**Chaque migration est tout ou rien.** `executescript` valide chaque instruction DDL séparément et
solde toute transaction déjà ouverte : un `BEGIN` posé de l'extérieur ne l'englobe donc pas, et
`migrate()` place le `BEGIN … COMMIT` dans le script lui-même, avec un `ROLLBACK` sur exception.
Le schéma et `user_version` avancent ainsi ensemble. Sans cela, une interruption (coupure,
disque plein, processus tué au premier démarrage) laissait les tables créées et `user_version` à
`0`, et toute ouverture ultérieure échouait sur « table tasks already exists » — base
définitivement inutilisable. Les `CREATE … IF NOT EXISTS` de la première migration rattrapent en
plus une base laissée à mi-chemin par une version antérieure.

Quand une nouvelle migration est ajoutée, **ne modifiez jamais un script déjà publié** : les bases
existantes ne le rejoueraient pas.

Les dates et horodatages sont des **textes ISO 8601** : `2026-09-24` pour une échéance,
`2026-09-24T09:12:05` (à la seconde) pour un horodatage. Ce choix rend les comparaisons de
chaînes équivalentes aux comparaisons chronologiques, et les requêtes de plage se font par
préfixe.

### `tasks`

| Colonne | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER | Clé primaire auto-incrémentée |
| `title` | TEXT | Non nul |
| `notes` | TEXT | Non nul, `''` par défaut |
| `priority` | INTEGER | Non nul, `2` par défaut, `CHECK (priority BETWEEN 1 AND 3)` — 1 haute, 2 normale, 3 basse |
| `due_date` | TEXT | `NULL` si aucune échéance |
| `project` | TEXT | Non nul, `''` si aucun projet |
| `done` | INTEGER | Non nul, `0` ou `1` |
| `created_at` | TEXT | Non nul |
| `completed_at` | TEXT | Renseigné au passage à *terminée*, remis à `NULL` à la réouverture |

Index `idx_tasks_open (done, due_date)` : la liste des tâches ouvertes triées par échéance est la
requête la plus fréquente.

### `notes`

| Colonne | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER | Clé primaire auto-incrémentée |
| `title` | TEXT | Non nul, `''` par défaut ; déduit du corps si vide |
| `body` | TEXT | Non nul, `''` par défaut |
| `pinned` | INTEGER | Non nul, `0` ou `1` |
| `created_at` / `updated_at` | TEXT | Non nuls ; `updated_at` suit toute modification de contenu, mais pas l'épinglage |

### `time_entries`

| Colonne | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER | Clé primaire auto-incrémentée |
| `task_id` | INTEGER | `REFERENCES tasks (id) ON DELETE SET NULL` : supprimer une tâche ne supprime pas l'historique, la session devient « Sans tâche » |
| `kind` | TEXT | Non nul, `'focus'` par défaut ; correspond aux valeurs de `Phase`. Seules les concentrations sont journalisées par l'interface |
| `started_at` / `ended_at` | TEXT | Non nuls |
| `duration_s` | INTEGER | Non nul, strictement positif (contrôlé par `TimeStore.log`) |

Index `idx_time_started (started_at)` : les totaux du jour et de la semaine filtrent sur cette
colonne.

### `file_moves`

| Colonne | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER | Clé primaire auto-incrémentée |
| `batch_id` | TEXT | Non nul ; identifiant du lot, un `uuid4` hexadécimal par appel à `apply()` |
| `src` / `dst` | TEXT | Non nuls ; chemins absolus avant et après déplacement |
| `moved_at` | TEXT | Non nul |
| `undone` | INTEGER | Non nul, `0` ou `1` ; un lot annulé n'est plus proposé |

Index `idx_file_moves_batch (batch_id)`.

### `settings`

`key` (clé primaire) et `value`, une valeur **JSON** écrite et relue par `Settings`. Clés
utilisées aujourd'hui :

| Clé | Écrite par | Contenu |
| --- | --- | --- |
| `window_geometry` | `MainWindow` | Géométrie Qt de la fenêtre, encodée en base64 |
| `last_section` | `MainWindow` | Indice du dernier volet affiché |
| `last_folder` | `FilesTab` | Dernier dossier analysé |
| `pomodoro_focus_min`, `pomodoro_break_min`, `pomodoro_long_break_min` | `FocusTab` | Durées en minutes |

## Circulation des données

```
python -m organizer
        │
        ▼
app.main()  ──►  QApplication + apply_theme()
        │
        ▼
AppContext.open()
        │  connect()  ──►  migrate()   (une connexion SQLite, PRAGMA foreign_keys)
        │  load_rules(rules_path)      (JSON, recréé avec les défauts si besoin)
        ▼
  ctx : conn · settings · tasks · notes · times · files · rules_path
        │
        ▼
MainWindow(ctx)
        │  construit les cinq volets d'après _SECTIONS, chacun recevant ctx
        ▼
TodayTab · TasksTab · NotesTab · FocusTab · FilesTab
```

Chaque volet respecte le même petit contrat :

- son constructeur prend le `ctx` ;
- il expose `refresh()`, qui **relit la base** et redessine ;
- il expose un signal `dataChanged`, émis dès qu'il a écrit quelque chose ;
- s'il garde un état non encore écrit, il expose `flush()`.

`MainWindow` relie le tout. Quand un volet émet `dataChanged`, `_on_data_changed(source)`
**marque les autres volets « à relire »** dans l'ensemble `_stale` et arme un `QTimer` monocoup de
50 ms. À son terme, seul le volet **visible** est relu, puis la barre d'état. Les autres attendent
d'être affichés : `_on_section_changed` les relit à ce moment-là, et seulement s'ils sont marqués.
Un drapeau `_refreshing` empêche une cascade : un `refresh()` qui écrirait et émettrait à son tour
ne relance pas le cycle.

Relire les cinq volets à chaque écriture coûtait de 0,3 à 3,7 s de fenêtre gelée par case cochée,
selon la quantité de données ; le report rend ce coût invisible, sans qu'aucun volet connaisse les
autres. Le `QTimer` regroupe aussi plusieurs émissions rapprochées en une seule relecture.
`flush_refresh()` force l'application immédiate — la fermeture s'en sert, et les tests aussi.

Un `QTimer` de 30 s surveille le passage de minuit : la date de l'en-tête est réécrite et tous les
volets sont marqués, puisque « aujourd'hui » a changé de sens.

Changer de volet mémorise `last_section`. À la fermeture, `closeEvent` applique une relecture en
attente, enregistre la géométrie puis appelle `flush()` sur les volets qui en ont un — `NotesTab`
écrit la note en cours, `FocusTab` journalise une concentration entamée depuis au moins une minute,
`FilesTab` interrompt et attend son fil de travail, `TasksTab` désarme sa recherche différée — en
ignorant une exception éventuelle : la fermeture ne doit pas être bloquée.

Quelques détails utiles à connaître :

- **Le volet *Fichiers* ne relit pas le disque sur `refresh()`.** Le dossier choisi et le plan
  affiché viennent du disque, pas de la base ; un rafraîchissement déclenché par un autre volet
  ne les effacerait pas sans raison. Seuls les lots annulables sont relus.
- **Les rafraîchissements déclenchés par un widget sont différés** par
  `QTimer.singleShot(0, self.refresh)`. Redessiner immédiatement détruirait la case à cocher ou la
  cellule en train d'émettre son propre signal.
- **Les opérations disque tournent hors du fil graphique.** `FilesTab._start()` confie `plan()`,
  `apply()`, `undo()` ou `find_duplicates()` à un `_Worker(QThread)` et leur passe un `Progress`
  (`organizer/files.py`) qui remonte l'avancement par signal et porte le drapeau d'annulation.
  `files.busy()` dit si une opération tourne. La connexion SQLite est ouverte avec
  `check_same_thread=False` pour que le fil de travail journalise ses déplacements lui-même :
  SQLite est compilé en mode sérialisé (`sqlite3.threadsafety == 3`).
- **Les listes redessinées sont plafonnées.** *Aujourd'hui* n'affiche que `MAX_ROWS` lignes par
  section, avec un renvoi vers *Tâches* : un `QWidget` par ligne coûte environ 2 ms, ce qui
  devenait le poste le plus lourd de l'application. Les couleurs de ligne passent par la propriété
  Qt `role`, que la feuille de style globale reconnaît, plutôt que par un `setStyleSheet` par
  widget.
- **Les champs de recherche sont temporisés** de 250 ms (`SEARCH_DELAY_MS` dans `notes_tab.py` et
  `tasks_tab.py`) : sans cela, chaque touche frappée relisait toute la base.

## Où brancher une nouvelle fonctionnalité

**Un nouveau volet.** Créez `organizer/ui/mon_volet.py` avec une classe `QWidget` qui déclare
`dataChanged = Signal()`, prend `ctx` en premier argument et implémente `refresh()`. Ajoutez une
ligne `("Mon volet", MonVolet)` à `_SECTIONS` dans `organizer/ui/main_window.py` : le raccourci
`Ctrl+<n>` est créé automatiquement à partir de la longueur de cette table. Si le volet garde une
saisie non écrite, ajoutez-lui `flush()`.

**Un nouveau magasin de données.** Un module `organizer/mon_domaine.py` sur le modèle de
`notes.py` : une `dataclass` avec `from_row()`, une classe `MonStore(conn)` dont les méthodes
lèvent `ValueError` avec un message français sur entrée invalide. Puis un champ dans `AppContext`
et sa construction dans `AppContext.open()`.

**Un réglage à retenir.** `ctx.settings.set("ma_cle", valeur)` et, en lecture,
`read_setting(ctx.settings, "ma_cle", defaut)` de `organizer/ui/widgets.py` — il rattrape une valeur
qui ne serait plus du JSON et rend le défaut, plutôt que d'empêcher le démarrage : n'importe quelle valeur sérialisable en JSON, sans toucher au schéma. C'est aussi le
contournement quand une colonne manque et qu'une migration n'est pas souhaitable.

**Une table ou une colonne.** Ajoutez un script SQL **à la fin** de `MIGRATIONS` dans
`organizer/db.py` ; ne modifiez jamais une entrée existante, les bases déjà migrées ne la
rejoueraient pas. Le `user_version` s'occupe du reste, à l'ouverture suivante.

**Une brique d'interface.** Dans `organizer/ui/widgets.py`, et les couleurs uniquement depuis
`theme.COLORS` / `theme.TINTS` : aucune valeur hexadécimale en dur ailleurs.

**Une règle de rangement d'un nouveau genre.** Un champ de plus sur `Rule` doit apparaître dans
`__post_init__` (validation), `to_dict()`, `from_dict()`, dans les colonnes de `RulesDialog` et
dans `docs/regles-fichiers.md`.

**Des tests.** Un fichier `tests/test_*.py`. Les fixtures de `conftest.py` : `ctx` (contexte
jetable, base, règles et racine de rangement sous `tmp_path`), `qapp` (l'unique `QApplication` de
la session) et `pump` (vide la file d'événements Qt, y compris les `singleShot(0, …)`). Une
fixture automatique fait échouer le test si une exception a été levée dans un slot Qt — PySide6 y
route les exceptions sans les propager. Sans écran, `QT_QPA_PLATFORM=offscreen`.

**Un module Qt supplémentaire.** Retirez-le de `QT_EXCLUDES` dans `packaging/organizer.spec`,
sinon l'exécutable se construira sans lui et échouera au lancement.

## Limites connues

- Deux tests marqués `xfail` dans `tests/test_tasks.py` documentent deux défauts de validation de
  `TaskStore` : une priorité hors de 1–3 remonte une `sqlite3.IntegrityError` au lieu d'un
  `ValueError` français, et une échéance non ISO passée à `update()` est écrite en base avant tout
  contrôle, rendant la tâche illisible. Les corriger doit se faire dans `tasks.py`, pas dans le
  schéma.
- Tout s'exécute dans le fil de l'interface. Une analyse ou un rangement sur un très gros dossier
  fige la fenêtre le temps de l'opération ; `FilesTab._busy()` en atténue l'effet (curseur
  d'attente, boutons désactivés, message peint avant de bloquer) mais ne déporte pas le travail.
- La recherche de notes ignore la casse mais pas les accents : « reunion » ne trouve pas
  « réunion ».
- L'analyse de rangement ne descend pas dans les sous-dossiers, contrairement à la recherche de
  doublons.
