# RenPySlim

> La boîte à outils tout-en-un pour alléger et empaqueter les ressources de tes jeux Ren'Py

**Langue / Language:** [简体中文（默认）](README.md) | [English](README.en.md) | [Русский](README.ru.md) | [Español](README.es.md) | [Português (BR)](README.pt.md) | [Türkçe](README.tr.md) | [Deutsch](README.de.md) | **Français**

**Licence : [AGPL-3.0](LICENSE)** · Les mentions des tiers sont dans [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

> Ce projet est façonné en profondeur par l'IA : nous te conseillons de vérifier le code avant de l'utiliser. Le développeur décline toute responsabilité pour les conséquences d'une mauvaise utilisation. **Tes données n'ont pas de prix !**

---

## Qu'est-ce que c'est

RenPySlim aide les créateurs de jeux Ren'Py à **réduire la taille** de leurs œuvres, à les **ranger**, et à les **préparer pour la publication** — tout est pris en charge de bout en bout :

- **Analyser** — détecte les ressources trop volumineuses et produit un rapport de taille / problèmes / recommandations
- **Compresser** — allégement complet des images, de l'audio, de la vidéo et des polices, avec réécriture automatique des références dans les scripts ;
  le réglage par défaut privilégie la qualité (q95, quasi sans perte) et l'optimisation parallèle exploite automatiquement tous les cœurs du processeur
- **Empaqueter** — génère des paquets de publication PC / Mac / Android via le SDK officiel
- **Alléger un jeu déjà prêt** — allège en toute sécurité un jeu déjà empaqueté (dossier ou zip/7z/rar), entrée directe, sortie directe
- **Alléger un APK** — les paquets Android aussi peuvent être allégés : images converties en WebP, audio converti en OGG (remappage à l'exécution, sans toucher aux références), re-signature automatique
- **Déverrouillage par décompilation** (expérimental) — pour un jeu sans code source, l'outil intégré unrpyc permet de retrouver les scripts ;
  les images et l'audio à l'intérieur des archives peuvent aussi être convertis, puis tout est reconditionné à l'identique dans les archives RPA

Le tout accompagné d'un bilan de santé du projet en quatre volets : détection des ressources inutilisées,
nettoyage des fichiers superflus avant empaquetage, détection des doublons,
rapport des caractères manquants dans les polices — et après chaque optimisation, le lint officiel
est lancé automatiquement pour vérifier le résultat.

**Sûr par défaut** : toutes les opérations passent d'abord par une copie de travail, les originaux ne sont jamais touchés ;
« si ce n'est pas plus petit, on ne remplace pas » ;
une ressource dont la référence est introuvable n'est jamais renommée ; chaque exécution produit un rapport d'analyse et une liste des modifications.

## Démarrage rapide

**Utilisateur classique** : rends-toi sur [Releases](https://github.com/AxelBeary/renpyslim/releases),
télécharge `RenPySlim.exe`, lance-le d'un double-clic — ton navigateur ouvre automatiquement la page de l'outil.

**Développeur** :

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
python main.py            # lance l'interface graphique
```

## Interface graphique (recommandée)

L'interface utilise une barre latérale et propose **中文 / English / Русский / Español / Português (BR) / Türkçe / Deutsch / Français** ainsi que
**deux thèmes, clair et sombre** (le changement se fait en haut à droite ; sans choix manuel,
elle suit la langue du navigateur et l'apparence du système, et ton choix est mémorisé).
Quatre points d'entrée : **Super empaqueteur / Allègement de jeu prêt / Allègement d'APK / Allègement de polices**.

### Parcours guidé en quatre étapes

1. Indique le chemin (ou clique sur « Parcourir les archives / Parcourir les dossiers » pour ouvrir une boîte de sélection), puis clique sur « Scanner et analyser » → consulte le rapport d'analyse
2. Coche les optimisations que tu veux effectuer et choisis le niveau de compression
3. Clique sur « Lancer l'exécution » → suis en direct la barre de progression et les journaux
4. À la fin, récupère le résultat optimisé / les paquets de publication officiels

### Opérations qui simplifient la vie

- **Glisse-dépose directement un zip / 7z / rar / APK / dossier sur l'icône de l'outil** : le champ se remplit tout seul et la fonction correspondante s'ouvre
- Si l'outil est déjà lancé et que tu déposes un nouveau fichier, il s'ouvre automatiquement dans un nouvel onglet, sans double lancement
- Les chemins déjà utilisés sont gardés dans « Récents » : un clic et c'est reparti

### Les quatre points d'entrée

- **Super empaqueteur** : indique le dossier du projet ; après optimisation, l'empaquetage se fait automatiquement via le SDK officiel (PC/Mac/Android).
  Tu peux cocher « Empaqueter les ressources dans une archive RPA » (canal officiel)
- **Allègement de jeu prêt** : indique le dossier du jeu fini ou glisse directement une archive zip / 7z / rar
  (extraction automatique, puis ré-empaquetage automatique après allègement pour te livrer le résultat ; les archives protégées par mot de passe sont prises en charge) ; les archives RPA rencontrées sont automatiquement ouvertes, optimisées puis reconstruites ;
  si l'archive contient un APK, il est automatiquement transféré vers l'allègement d'APK sécurisé ; dans les options avancées, le commutateur expérimental
  « Décompiler les scripts pour déverrouiller la conversion de formats » permet même aux jeux sans code source de profiter de la conversion de formats
- **Allègement d'APK** : choisis un fichier .apk et c'est réglé en trois étapes (niveau / commutateur d'allègement maximal / choix de signature — trois options,
  par défaut une nouvelle clé est générée automatiquement), avec à la sortie un paquet allégé prêt à installer directement
- **Allègement de polices** (outil indépendant) : aucun projet de jeu n'est nécessaire, il suffit de choisir une police + des sources de texte pour l'alléger ;
  les collections ttc/otc sont automatiquement découpées et produites séparément selon la graisse ; les originaux ne sont jamais écrasés, et la liste des caractères utilisés est fournie

### Garanties à l'exécution

- Tu peux cliquer à tout moment sur « Arrêter la tâche » pendant une exécution (ce qui est déjà terminé est conservé) ; en cas d'échec d'une tâche, un vidage de crash est automatiquement enregistré
- L'interface te signale quand une nouvelle version est disponible (comparaison avec GitHub Releases)
- Si FFmpeg / 7-Zip manque, l'interface t'indique précisément comment l'installer (commande winget ou adresse de téléchargement)
- Pour quitter : clic droit sur l'icône de la zone de notification en bas à droite → Quitter l'outil, ou le bouton « Quitter l'outil » en bas à gauche de la barre latérale
  (fermer la page du navigateur n'arrête pas l'outil)

## Mode sans interface (pour les scripts et l'automatisation, sortie JSON de bout en bout)

```
python cli.py env                                  # bilan de santé de l'environnement
python cli.py analyze <路径> --mode project        # analyser
python cli.py optimize <路径> --preset balanced    # optimiser
python cli.py full <工程路径> --platforms pc,mac   # optimiser + empaqueter de bout en bout
python cli.py slimfont <字体> <文本来源...>        # allègement de police autonome
python cli.py slimapk <apk> --remap --gen-key      # allègement d'APK (images→WebP/audio→OGG + re-signature)
```

> Assistants IA / scripts d'automatisation : lisez d'abord [AGENTS.md](AGENTS.md) (règles de sécurité et dépannage inclus).

## Prérequis

| Dépendance | Utilisation | Remarques |
|---|---|---|
| Ren'Py SDK | Empaquetage, compilation des scripts de remappage APK | En général détecté automatiquement ; si ce n'est pas le cas, indique-le dans « Paramètres » de l'interface |
| FFmpeg | Optimisation audio/vidéo | Installé dans le PATH ou dans le dossier bin à côté du programme, les deux fonctionnent |
| Java/JDK | Empaquetage Android, re-signature d'APK | Le premier empaquetage Android nécessite de terminer d'abord la configuration Android dans le lanceur Ren'Py |

Le service de l'interface écoute par défaut sur 127.0.0.1:52786 (un port peu courant) ; s'il est occupé,
il bascule automatiquement sur un port libre attribué par le système. Tu peux spécifier un autre port avec la variable d'environnement `RENPYTOOLS_PORT`.

## Aperçu des mécanismes de sécurité

| Mécanisme | Description |
|---|---|
| Copie de travail | Par défaut, on copie d'abord vers une copie avant d'opérer ; pas un octet de l'original n'est modifié |
| Sauvegarde obligatoire | Quand « Modifier directement les fichiers originaux » est coché, une archive de sauvegarde complète (sauvegardes de partie incluses) est créée d'abord |
| Pas plus petit, pas remplacé | Chaque optimiseur écrit d'abord un fichier temporaire et ne remplace que si la taille a réellement diminué |
| Verrouillage par référence | Une ressource introuvable par référence littérale dans les scripts est seulement compressée sur place, jamais renommée |
| Protection des dossiers du moteur | En mode jeu prêt/APK, renpy/, lib/ et assets/x-renpy/ ne sont jamais touchés |
| Marquer sans supprimer | Les fichiers soupçonnés d'être sans référence finissent seulement dans le rapport par défaut ; même avec l'option activée, ils sont déplacés en zone de quarantaine |
| Le nettoyage ne supprime que le reproductible | Cache/journaux/bytecode ; en mode modification directe des originaux, ces éléments sont automatiquement sautés pour protéger les sauvegardes de partie |
| Une image n'est jamais déclarée morte | Ren'Py charge automatiquement les images par nom de fichier : référence introuvable ne veut pas dire inutile |
| Protection contre les entrées malveillantes | Désérialisation de l'index des archives sur liste blanche ; assainissement des chemins des entrées d'archives (défense contre le zip-slip) |
| Réservé au local | Le service n'écoute que sur 127.0.0.1 et vérifie la provenance des requêtes : aucun accès possible depuis l'extérieur |
| Lint automatique après optimisation | Le vérificateur statique officiel est intégré au parcours ; le résultat est archivé dans validation.txt |
| Liste des modifications | Chaque exécution produit changelog.json, qui consigne chaque changement |

## Limites de sécurité

- Le service **n'écoute que sur 127.0.0.1** (l'adresse « machine locale uniquement ») : les autres appareils
  du réseau local ou d'Internet ne peuvent tout simplement pas établir de connexion. Aucune configuration de pare-feu n'est nécessaire, et il est déconseillé de l'exposer de quelque façon que ce soit ;
- L'outil ne propose pas, et ne proposera jamais, d'option « ouvrir l'accès réseau » ; si tu modifies le code source toi-même,
  il est **fortement déconseillé** de changer l'adresse d'écoute en 0.0.0.0 ou une adresse publique —
  l'interface n'a aucune authentification par connexion : l'exposer revient à confier la lecture et l'écriture des fichiers de ta machine à n'importe qui peut y accéder ;
- L'outil lui-même n'accède jamais à Internet de sa propre initiative, à une seule exception : « Vérifier les nouvelles versions »
  (comparaison avec GitHub Releases, ignorée silencieusement en cas d'échec, sans aucun impact sur les fonctionnalités).

## Tests

```
.venv\Scripts\python -m pytest tests -q
```

Couvrent la lecture/écriture des archives RPA (y compris les deux générations de format et le blocage des archives malveillantes),
la sécurité de la réécriture des références, la préservation des originaux par les optimiseurs de polices/images,
l'analyse rpyc, l'allègement d'APK (protection du moteur / retrait de la signature /
conversion des chemins préfixés par x- / génération de la clé), l'annulation et les vidages de crash, les valeurs sûres par défaut, la non-régression des correctifs d'audit,
les protections locales du backend et l'intégrité des dictionnaires des huit langues de l'interface — 114 tests en tout.

## Développement

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt pyinstaller
python main.py            # lance l'interface graphique
build_exe.bat             # reconditionne l'exe
```

**Mainteneurs/agents, lisez d'abord :**

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) : plan de l'architecture, lignes rouges de sécurité, guide d'extension
- [docs/BACKLOG.md](docs/BACKLOG.md) : archive des demandes et tâches à faire (les nouvelles demandes atterrissent d'abord ici)
- [docs/STATUS.md](docs/STATUS.md) : état du passage de relais et résultats de tests réels

## Prise en charge multilingue / Localization

| Langue | Interface | Documentation | État |
|---|---|---|---|
| 简体中文 | ✅ par défaut | ✅ le présent document | En ligne |
| English | ✅ | [README.en.md](README.en.md) | En ligne |
| Русский | ✅ | [README.ru.md](README.ru.md) | En ligne |
| Español | ✅ | [README.es.md](README.es.md) | En ligne |
| Português (BR) | ✅ | [README.pt.md](README.pt.md) | En ligne |
| Türkçe | ✅ | [README.tr.md](README.tr.md) | En ligne |
| Deutsch | ✅ | [README.de.md](README.de.md) | En ligne |
| Français | ✅ | ✅ ce document | En ligne |

Envie d'ajouter une nouvelle langue ? Consulte le « guide de traduction » dans [CONTRIBUTING.md](CONTRIBUTING.md) :
il suffit d'ajouter un dictionnaire à l'interface et un fichier README.<code-de-langue>.md à la documentation.

## Licence et conformité

- Ce projet est publié sous **AGPL-3.0** : tu peux librement utiliser, modifier et distribuer ce logiciel,
  mais les versions modifiées (y compris lorsqu'un service est fourni via le réseau) doivent être publiées en open source sous la même licence.
  Alléger ton propre jeu n'est soumis à aucune restriction ; l'obligation d'open source ne se déclenche que lors de la distribution d'une version modifiée.
- Mentions complètes des dépendances tierces et des implémentations de référence des formats :
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
  (y compris les notes de conformité LGPL de pystray, les remerciements pour le format Ren'Py et les limites des programmes externes)
- Pour contribuer, commence par lire [CONTRIBUTING.md](CONTRIBUTING.md) ;
  les vulnérabilités passent par le canal de signalement privé décrit dans [SECURITY.md](SECURITY.md).
- Ren'Py est une marque déposée / un projet de Tom Rothamel et d'autres ; ce projet n'a aucun lien d'affiliation avec lui :
  c'est un outil tiers indépendant mis à disposition de la communauté Ren'Py.
