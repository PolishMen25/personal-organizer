# Guide de démarrage

Ce guide fait le tour de l'application dans l'ordre où on la découvre, puis déroule une première
journée complète en exemple. Comptez vingt minutes.

## 1. Lancer l'application

- **Avec l'exécutable** : double-cliquez sur `PersonalOrganizer.exe`. Rien à installer.
- **Depuis les sources** : activez l'environnement virtuel, puis `python -m organizer`.

Au premier lancement, l'application crée son dossier de données
(`%APPDATA%\PersonalOrganizer` sous Windows) avec une base vide `organizer.db` et un fichier de
règles `regles-fichiers.json` garni des règles par défaut. Aucune question n'est posée, aucun
compte n'est demandé.

La fenêtre s'ouvre sur le volet *Aujourd'hui*. La barre latérale liste les cinq volets ; la date
du jour est rappelée en haut à droite, et la barre du bas affiche en permanence votre temps de
concentration du jour et le nombre de tâches ouvertes. À la fermeture, la taille de la fenêtre et
le volet affiché sont mémorisés : vous retrouverez l'application où vous l'avez laissée.

## 2. Écrire sa première tâche

Le champ en haut du volet *Aujourd'hui* attend une ligne de texte. Tapez :

```
Appeler la banque demain !1 #admin
```

puis `Entrée`. La tâche apparaît immédiatement. Trois raccourcis ont été reconnus et retirés du
titre :

- `!1` — priorité haute (`!2` normale, `!3` basse) ;
- `#admin` — projet ;
- `demain` — échéance.

Pour l'échéance, l'application comprend aussi `aujourd'hui` (ou `auj`), `après-demain`, un jour de
la semaine comme `vendredi` — toujours la prochaine occurrence, jamais aujourd'hui — et une date
écrite `12/10` ou `12/10/2027`. Une date `12/10` déjà passée est reportée à l'année suivante.

Si le titre est vide une fois les raccourcis retirés, la ligne n'est pas perdue : elle reste dans
le champ avec un message en rouge, à corriger.

## 3. Les cinq volets en deux minutes chacun

### Aujourd'hui

Trois sections, et seulement celles qui ont du contenu :

- **En retard** — échéance dépassée, affichée en rouge ;
- **Aujourd'hui** — échéance du jour ;
- **Important** — priorité haute sans échéance, à ne pas perdre de vue.

Cochez la case d'une tâche pour la terminer ; décochez-la pour la rouvrir. Tant qu'il n'y a rien
d'urgent, le volet affiche « Rien d'urgent. ». En bas, une ligne résume la journée : tâches
terminées et temps de concentration.

### Tâches

La liste complète, en tableau : *Fait*, *Tâche*, *Priorité* (avec une pastille de couleur),
*Projet*, *Échéance*. Au-dessus, le même champ de saisie rapide, un bouton *Ajouter* et un bouton
*Aide sur la syntaxe* qui rappelle les raccourcis.

Trois filtres se combinent : le statut (*À faire*, *Terminées*, *Toutes*), le projet, et une
recherche qui porte sur les titres et les notes.

Sélectionnez une ligne pour activer *Modifier* et *Supprimer*. Un double-clic ailleurs que sur la
case *Fait* ouvre la fiche d'édition : titre, notes libres, priorité, échéance (avec une case
*Aucune échéance*) et projet. La touche `Suppr` supprime la tâche sélectionnée, après
confirmation ; la suppression est définitive.

### Notes

La liste est à gauche, l'éditeur à droite. *Nouvelle note* (ou `Ctrl+N`) crée une note vide et
place le curseur dans le titre.

L'enregistrement est automatique : environ huit dixièmes de seconde après votre dernière frappe,
la note est écrite. L'indicateur en bas passe de « Modification en attente… » à « Enregistré ».
`Ctrl+S` force l'écriture immédiate ; changer de note ou fermer la fenêtre enregistre aussi.

Laissez le titre vide et il sera déduit de la première ligne du corps, sans le `#` du Markdown —
« Sans titre » si la note est vide. *Épingler* garde une note en haut de liste (repère `★`) ;
`Ctrl+F` va au champ de recherche, qui cherche dans les titres et les corps sans distinguer les
majuscules. Le Markdown est conservé tel quel : l'éditeur ne le transforme pas.

### Concentration

Un minuteur pomodoro. Choisissez éventuellement la tâche à laquelle rattacher vos sessions — la
liste ne propose que les tâches ouvertes — puis *Démarrer*. Le compte à rebours part de 25
minutes de concentration, suivies de 5 minutes de pause, avec une pause longue de 15 minutes
toutes les quatre concentrations terminées. Chaque fin de phase émet un bip et annonce la
suivante.

- *Pause* (le bouton *Démarrer* change de nom) suspend le décompte ;
- *Passer* abandonne la phase en cours, qui n'est pas comptée comme terminée ;
- *Réinitialiser* revient au début d'une concentration et remet le compteur de cycles à zéro.

Une concentration menée à son terme est toujours journalisée. Une concentration interrompue à la
main — *Passer*, *Réinitialiser*, ou la fermeture de la fenêtre — n'est journalisée qu'à partir
d'une minute écoulée : quelques secondes par erreur ne polluent pas vos statistiques.

À droite, l'histogramme des sept derniers jours (la barre du jour est plus claire) et les trois
durées, réglables de 1 à 180 minutes pour la concentration, 1 à 60 pour la pause et 1 à 120 pour
la pause longue. Un changement est enregistré aussitôt ; une phase déjà en cours garde son
décompte, la nouvelle durée s'applique à la suivante.

### Fichiers

Deux onglets : *Rangement* et *Doublons*. Le dossier proposé au départ est celui de vos
téléchargements ; le dernier dossier choisi est mémorisé.

*Analyser* lit le dossier et propose un tableau — fichier, dossier d'arrivée, règle appliquée —
sans rien déplacer. Tout est coché par défaut ; décochez ce que vous voulez laisser, puis
*Ranger la sélection* et confirmez. Un compte rendu indique combien de fichiers ont bougé et
détaille les échecs éventuels (un fichier ouvert dans un autre programme, par exemple) ; le reste
du lot est traité quand même.

*Annuler le dernier rangement* remet en place les fichiers du dernier lot. *Règles…* ouvre
l'éditeur de règles ; le bouton *Chercher les doublons* de l'onglet *Doublons* compare le contenu
des fichiers du dossier **et de ses sous-dossiers** et regroupe ceux qui sont identiques, sans
jamais rien déplacer ni supprimer.

Prenez le temps de lire [regles-fichiers.md](regles-fichiers.md) avant votre premier rangement
d'un vrai dossier.

## 4. Une première journée, en exemple

Voici une journée type, un jeudi matin.

**8 h 40 — vider sa tête.** Volet *Aujourd'hui*. Tout ce qui traîne y passe, une ligne après
l'autre :

```
Appeler la banque pour le prélèvement demain !1 #admin
Relire le compte rendu de réunion !2 #travail
Commander les cartouches d'encre lundi !3 #maison
Renouveler le passeport 12/10 #admin
Sauvegarder le NAS !1
```

Résultat : la section *Aujourd'hui* est encore vide (rien n'est échu ce jeudi), la section
*Important* affiche « Sauvegarder le NAS » — priorité haute, sans échéance — et les autres tâches
attendent leur jour. La barre du bas indique « 5 tâches ouvertes ».

**8 h 50 — préciser une tâche.** Volet *Tâches*, double-clic sur « Relire le compte rendu de
réunion ». Dans les notes : « Version du 23/09, corriger la partie budget ». Échéance
aujourd'hui, priorité haute. *Enregistrer*. La tâche rejoint la section *Aujourd'hui* du premier
volet.

**9 h 00 — se mettre au travail.** Volet *Concentration*. Dans la liste des tâches, « Relire le
compte rendu de réunion », puis *Démarrer*. Vingt-cinq minutes plus tard, un bip : la session est
journalisée et la pause de cinq minutes commence. Deux tours plus tard, le compte rendu est
relu : volet *Aujourd'hui*, la case est cochée. La ligne de résumé passe à « 1 tâche terminée
aujourd'hui · 50 min de concentration ».

**11 h 30 — prendre des notes.** Volet *Notes*, `Ctrl+N`. Titre laissé vide, et dans le corps :

```
# Réunion budget — 24/09

- Arbitrage reporté à la semaine prochaine
- Demander le détail des licences à Claire
- Décision : on garde le prestataire actuel
```

Le titre se remplit tout seul — « Réunion budget — 24/09 », la première ligne sans son `#` — et
l'indicateur passe à « Enregistré » sans qu'on ait rien à faire. La note est épinglée, le temps
que le sujet soit clos.

**14 h 00 — ranger ses téléchargements.** Volet *Fichiers*. Le dossier `Téléchargements` est déjà
proposé, *Analyser*. Le tableau annonce onze fichiers : trois PDF vers `Documents/PDF`, une
facture reconnue par son nom vers `Documents/Factures`, quatre images vers `Pictures`, deux
installeurs vers `Logiciels/Installeurs` et une archive vers `Archives`. Un fichier est décoché,
un installeur qu'il faudra relancer d'ici peu. *Ranger la sélection*, confirmation : « 10 fichiers
rangés. » Les destinations sont relatives à votre dossier personnel — et portent le nom des
dossiers sur le disque : `Pictures`, `Videos`, `Music`, que l'explorateur affiche « Images »,
« Vidéos » et « Musique ».

Pendant l'opération, une barre d'avancement indique où elle en est et le bouton *Interrompre*
l'arrête : la fenêtre ne se figera pas, même sur un disque USB lent.

Un doute après coup ? *Annuler le dernier rangement*, et les dix fichiers retrouvent
`Téléchargements`.

**17 h 30 — fermer.** `Ctrl+Q`. La note en cours de frappe est écrite, une concentration entamée
depuis plus d'une minute est journalisée, la taille de la fenêtre est mémorisée. Le lendemain,
« Appeler la banque » apparaît dans la section *Aujourd'hui*, et « Renouveler le passeport »
attend le 12 octobre.

## 5. Ensuite

- Ajustez les règles de rangement à vos propres dossiers :
  [regles-fichiers.md](regles-fichiers.md).
- Sauvegardez `%APPDATA%\PersonalOrganizer` de temps en temps : c'est toute votre mémoire, deux
  fichiers.
- Pour comprendre le code ou ajouter une fonctionnalité : [architecture.md](architecture.md).
