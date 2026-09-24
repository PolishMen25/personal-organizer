# Règles de rangement des fichiers

Le volet *Fichiers* range un dossier en appliquant une liste de règles. Ces règles vivent dans un
fichier JSON, modifiable depuis l'application (bouton *Règles…*) ou à la main.

## Où se trouve le fichier

`%APPDATA%\PersonalOrganizer\regles-fichiers.json` sous Windows, ou `regles-fichiers.json` dans le
dossier de données choisi par `ORGANIZER_DATA_DIR`.

Il est lu au démarrage de l'application, et relu lorsque vous enregistrez depuis l'éditeur de
règles. **Après une modification à la main, redémarrez l'application** pour qu'elle en tienne
compte. L'écriture depuis l'application est atomique et suivie d'un `fsync` : même une coupure de
courant ne peut pas laisser le fichier à moitié écrit.

> **Vos règles ne sont jamais effacées.** Si une règle est invalide, elle seule est écartée : les
> autres sont conservées, le fichier n'est pas réécrit, et l'application signale au démarrage
> quelles règles ont été ignorées et pourquoi. Si le fichier entier est illisible (JSON tronqué,
> par exemple), l'original est renommé en `regles-fichiers.invalide-<horodatage>.json` avant que
> les règles par défaut soient écrites : vous pouvez toujours y récupérer votre travail. Le fichier
> n'est créé d'office que lorsqu'il est absent.

## Format

Le fichier contient une **liste** JSON d'objets, un par règle, encodée en UTF-8. **L'ordre
compte** : pour un fichier donné, la première règle activée qui correspond l'emporte, les
suivantes ne sont pas consultées. C'est pourquoi « Factures », qui reconnaît un nom, passe avant
« Documents PDF », qui reconnaît une extension.

```json
[
  {
    "name": "Documents PDF",
    "destination": "Documents/PDF",
    "extensions": ["pdf"],
    "patterns": [],
    "min_age_days": 0,
    "enabled": true
  }
]
```

### Les champs, un par un

| Champ | Type | Défaut | Rôle |
| --- | --- | --- | --- |
| `name` | texte | *obligatoire* | Nom de la règle, affiché dans la colonne *Règle* du tableau d'analyse. Les espaces de début et de fin sont retirés ; un nom vide est refusé. |
| `destination` | texte | *obligatoire* | Dossier d'arrivée, **toujours relatif** à votre dossier personnel (`C:\Users\<vous>` sous Windows). Les deux séparateurs sont acceptés à l'écriture (`Documents/PDF` ou `Documents\PDF`) et normalisés avec des `/`. Créé automatiquement s'il n'existe pas, au moment du rangement. |
| `extensions` | liste de textes | `[]` | Extensions concernées, sans le point — `"pdf"`, pas `".pdf"`. Le point de tête est toléré et retiré, la casse est ignorée, les doublons sont écartés. Un fichier sans extension ne correspond jamais par ce champ. |
| `patterns` | liste de textes | `[]` | Motifs comparés au **nom complet du fichier**, extension incluse, sans distinction de casse. `*` remplace n'importe quelle suite de caractères, `?` un seul caractère, `[abc]` un caractère parmi ceux listés. |
| `min_age_days` | entier ≥ 0 | `0` | Âge minimum du fichier, en jours, d'après sa date de dernière modification. `0` ne filtre rien. Un fichier plus récent est laissé en place. |
| `enabled` | booléen | `true` | `false` met la règle de côté sans la supprimer. |

Une clé absente prend sa valeur par défaut. Une chaîne seule est acceptée là où une liste est
attendue : `"extensions": "pdf"` équivaut à `"extensions": ["pdf"]`.

**Comment un fichier est apparié :** il correspond à une règle si son extension figure dans
`extensions` **ou** si son nom correspond à l'un des `patterns` ; et, dans ce cas seulement, son
âge est vérifié contre `min_age_days`. Une règle sans extension ni motif ne correspond donc à
aucun fichier — l'éditeur de règles de l'application la refuse, mais rien n'empêche de l'écrire à
la main.

## Les règles par défaut

Ce sont celles que l'application écrit dans un fichier neuf. Toutes ont `min_age_days` à `0` et
`enabled` à `true`.

| Ordre | Nom | Destination | Extensions | Motifs |
| --- | --- | --- | --- | --- |
| 1 | Factures | `Documents/Factures` | — | `*facture*`, `*invoice*` |
| 2 | Documents PDF | `Documents/PDF` | `pdf` | — |
| 3 | Feuilles de calcul | `Documents/Tableurs` | `xlsx`, `xls`, `csv`, `ods` | — |
| 4 | Documents texte | `Documents/Textes` | `docx`, `doc`, `odt`, `txt`, `rtf` | — |
| 5 | Images | `Pictures` | `jpg`, `jpeg`, `png`, `gif`, `bmp`, `webp`, `heic`, `tiff`, `svg` | — |
| 6 | Vidéos | `Videos` | `mp4`, `mkv`, `avi`, `mov`, `wmv`, `webm`, `m4v` | — |
| 7 | Audio | `Music` | `mp3`, `wav`, `flac`, `m4a`, `ogg`, `aac`, `wma` | — |
| 8 | Installeurs | `Logiciels/Installeurs` | `exe`, `msi` | — |
| 9 | Images disque | `Logiciels/Images disque` | `iso`, `img` | — |
| 10 | Archives | `Archives` | `zip`, `7z`, `rar`, `tar`, `gz`, `bz2`, `xz` | — |
| 11 | Scripts | `Développement/Scripts` | `ps1`, `py`, `sh`, `bat`, `cmd` | — |

Un fichier qui ne correspond à aucune règle reste où il est : rien n'est déplacé « par défaut ».

### Pourquoi `Pictures` et non « Images »

Les destinations portent le nom des dossiers **sur le disque**, pas leur nom affiché. Les
bibliothèques Windows s'appellent `Pictures`, `Videos` et `Music` ; l'explorateur les montre
traduites en « Images », « Vidéos » et « Musique ». Écrire les noms français créerait trois
dossiers neufs *à côté* des vraies bibliothèques — celles qui sont indexées, sauvegardées et
proposées par les autres applications.

Une réserve subsiste pour `Documents` : si la « Sauvegarde des dossiers » de OneDrive est active,
le vrai dossier est `%USERPROFILE%\OneDrive\Documents`. Dans ce cas, changez la destination des
règles concernées en `OneDrive/Documents/…` depuis le bouton *Règles…*.

## Deux exemples de règles personnalisées

### Les relevés bancaires à part

Les PDF de la banque portent tous le mot « releve » ou « RIB » dans leur nom. En plaçant cette
règle **avant** « Documents PDF », ils échappent au fourre-tout des PDF :

```json
{
  "name": "Banque",
  "destination": "Documents/Banque",
  "extensions": [],
  "patterns": ["*releve*", "*rib*", "*rib_*.pdf"],
  "min_age_days": 0,
  "enabled": true
}
```

La casse n'a pas d'importance : `*rib*` reconnaît aussi bien `RIB_2026.pdf` que `mon-rib.pdf`.
Attention toutefois à la portée d'un motif court — `*rib*` attrape également `distribution.pdf`.
Un motif plus précis, comme `*rib_*.pdf`, évite ces prises accidentelles.

### Les captures d'écran, mais seulement les anciennes

Les captures de la semaine servent encore ; celles d'avant peuvent partir. `min_age_days` laisse
tranquilles les fichiers récents :

```json
{
  "name": "Captures d'écran anciennes",
  "destination": "Pictures/Captures",
  "extensions": ["png"],
  "patterns": ["capture*", "screenshot*"],
  "min_age_days": 7,
  "enabled": true
}
```

Cette règle doit précéder « Images », sinon toutes les `.png` partiraient dans `Pictures` avant
qu'elle soit consultée. Notez qu'extensions et motifs se cumulent en « ou » : ici, tous les `.png`
de plus de sept jours **et** tous les fichiers nommés `capture…` ou `screenshot…` de plus de sept
jours, quelle que soit leur extension.

## Éditer les règles depuis l'application

Bouton *Règles…*, en haut du volet *Fichiers*. Le tableau reprend les six champs : *Nom*,
*Destination*, *Extensions*, *Motifs*, *Âge min. (jours)*, *Activée*. Extensions et motifs se
saisissent séparés par des virgules. *Ajouter* crée une ligne, *Supprimer* retire les lignes
sélectionnées, *Restaurer les règles par défaut* remplace le tableau affiché — sans rien écrire
tant que vous n'enregistrez pas.

À l'enregistrement, chaque ligne est vérifiée. Au premier défaut, un message indique le numéro de
ligne et le problème, et **rien n'est écrit** : le fichier garde les règles précédentes. Sont
refusés un nom vide, une destination vide, absolue ou contenant `..`, une ligne sans extension ni
motif, et un âge minimum qui n'est pas un entier positif ou nul.

Une fois les règles enregistrées, le plan affiché est effacé : il avait été calculé avec les
règles précédentes. Relancez *Analyser*.

## Les garde-fous

Le rangement déplace de vrais fichiers. Voici tout ce qui est en place pour que ce soit sans
regret.

**Rien n'est jamais écrasé.** Si un fichier du même nom occupe déjà la destination, le fichier
déplacé est renommé `nom (2).ext`, puis `nom (3).ext`, etc. Le tableau d'analyse signale ce
renommage. La vérification est refaite juste avant chaque déplacement, au cas où la cible serait
apparue entre l'analyse et le rangement, et deux fichiers du même lot ne peuvent pas viser le même
chemin final — y compris s'ils ne diffèrent que par la casse, que Windows ne distingue pas.

**Rien n'est jamais supprimé.** L'application ne possède aucun code de suppression de fichier. La
recherche de doublons se contente d'afficher les groupes trouvés, à vous de décider quoi en faire
dans l'explorateur.

**Aucun chemin absolu.** Une destination avec une lettre de lecteur (`C:\Rangé`), une racine
(`\Rangé`) ou un chemin réseau (`\\serveur\partage`) est refusée, de même qu'une destination
contenant `..`. Un rangement ne peut donc pas sortir de votre dossier personnel.

**Aucun nom que Windows refuserait.** Sont également refusés, sur toutes les plateformes, les
caractères `< > : " | ? *`, les caractères de contrôle, et les noms réservés par Windows — `CON`,
`PRN`, `AUX`, `NUL`, `COM1` à `COM9`, `LPT1` à `LPT9`. Une règle écrite sous Linux reste ainsi
utilisable sur la machine Windows.

**Un fichier fautif ne bloque pas les autres.** Si la destination d'un fichier est inutilisable —
chemin trop long, dossier interdit —, ce fichier seul est écarté et nommé dans un compte rendu ;
le reste du dossier est proposé au rangement comme d'habitude.

**L'âge minimum.** `min_age_days` protège les fichiers que vous venez de recevoir. Quand une règle
en impose un, un fichier dont la date de modification ne peut pas être lue n'est pas rangé.

**Les fichiers laissés de côté.** Ne sont jamais déplacés : les dossiers, les liens symboliques,
les fichiers dont le nom commence par un point, les fichiers marqués cachés ou système par
Windows, ceux dont l'état est illisible, et les fichiers temporaires ou en cours de
téléchargement — `~$*`, `*.part`, `*.crdownload`, `*.tmp` — ainsi que les raccourcis `*.lnk`.

**Les dossiers laissés de côté.** La recherche de doublons, qui descend dans les sous-dossiers,
applique aux dossiers le même critère qu'aux fichiers : nom commençant par un point, ou attribut
« caché » ou « système ». Elle n'entre donc ni dans `AppData`, ni dans `$RECYCLE.BIN`, ni dans
`System Volume Information`.

**Seul le dossier choisi.** L'analyse ne regarde que le contenu direct du dossier, jamais ses
sous-dossiers : un dossier déjà rangé ne sera pas réorganisé au passage. Un fichier qui se trouve
déjà dans son dossier de destination est ignoré.

**Vous gardez la main.** *Analyser* ne fait que lire le disque. Chaque ligne du plan porte une
case à cocher, et seul ce qui est coché est déplacé, après une confirmation qui annonce le nombre
de fichiers.

**Tout est journalisé, et annulable.** Chaque rangement forme un lot enregistré dans la base.
*Annuler le dernier rangement* remet ses fichiers à leur emplacement d'origine ; les dix derniers
lots restent visibles dans la carte *Derniers rangements*. Un retour est refusé, fichier par
fichier, si un **autre** fichier occupe l'emplacement d'origine — l'annulation n'écrase pas plus
que le rangement. Si le fichier y est déjà revenu de lui-même, la ligne du journal est simplement
soldée, sans message d'échec.

Chaque déplacement est inscrit au journal dès qu'il a eu lieu, et chaque retour marqué dès qu'il
est fait : une opération interrompue en cours de route laisse donc toujours le journal d'accord
avec le disque, et le lot reste annulable.

**Un échec n'interrompt rien.** Un fichier verrouillé par une autre application, une permission
refusée : l'erreur est collectée et le reste du lot est traité. Le compte rendu final liste les
échecs, fichier par fichier, avec la raison.

**Aucun fichier tronqué.** Vers un autre volume — carte SD, clé USB, disque externe —, un
déplacement est une copie suivie d'une suppression. Si la copie échoue en cours de route (disque
plein), le fragment écrit à destination est effacé : l'original reste intact à sa place, et aucun
fichier corrompu ne subsiste sous le nom canonique.

**Rien ne gèle la fenêtre.** Analyse, rangement et recherche de doublons tournent hors du fil
graphique : une barre d'avancement indique où l'opération en est, et le bouton *Interrompre*
l'arrête proprement. Un rangement interrompu garde ce qu'il a déjà fait, et reste annulable.
