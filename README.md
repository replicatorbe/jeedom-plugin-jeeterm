# Plugin Jeedom — JeeTerm

Un vrai terminal dans l'interface de Jeedom, à la manière du module
« Terminal » de Home Assistant. Le shell tourne sur la machine Jeedom et
s'affiche dans le navigateur : couleurs, raccourcis clavier, redimensionnement,
éditeurs plein écran (`nano`, `vim`, `htop`, `mc`…), copier-coller, et en option
une session `tmux` qui survit à la fermeture de l'onglet.

Parce que certaines choses se font plus vite en trois mots dans une console
qu'en dix clics : suivre un journal, relancer un service, regarder l'espace
disque, éditer un fichier de configuration.

> **Ce terminal donne un accès root à la machine.** Sur une installation
> Jeedom, le compte `www-data` peut utiliser `sudo` sans mot de passe : même
> ouvert sous `www-data`, le shell permet de tout faire. Seuls les
> administrateurs de Jeedom peuvent l'ouvrir.

## Ce qu'il sait faire

**Un terminal complet.** xterm.js 6 en 256 couleurs et couleurs vraies, emojis,
recherche dans l'historique de l'écran (`Ctrl+Maj+F`), liens cliquables, plein
écran, copier-coller (y compris en `http://`, où le navigateur interdit la
lecture du presse-papiers), rangée de touches spéciales pour tablette et
téléphone (Échap, Tab, Ctrl, Alt, flèches).

**À votre goût.** Un panneau de réglages, mémorisés par navigateur :

- neuf thèmes : Jeedom (suit le thème clair ou sombre), **Noir**, **Phosphore
  vert**, Ambre, Dracula, Solarized sombre, Monokai, Nord, Tango clair ;
- un **effet écran cathodique** rétro : lignes de balayage, lueur, vignettage ;
- la forme du curseur, son clignotement, la police, la taille, l'interligne ;
- la cloche (éclair, bip ou rien) et la copie dès la sélection, comme sous Linux.

**Des commandes rapides.** Un menu de commandes utiles (`tail -f` des erreurs
PHP, `df -h`, `htop`…) que l'on complète avec les siennes. Une commande est
tapée dans le terminal sans être validée : c'est vous qui appuyez sur Entrée.

**Un shell à la sauce Jeedom.** Chaque nouveau shell s'ouvre sur une bannière
qui résume la machine :

```
        ▄█▄          www-data@jeedom
      ▄█████▄        ---------------
    ▄█████████▄      Système       Debian GNU/Linux 12 (bookworm)
  ▄█████████████▄    Noyau         6.1.0-25-arm64
 ▀▀▀███████████▀▀▀   Jeedom        4.4.19  (/var/www/html)
    ███▀▀▀▀▀███      Allumé depuis 2 j 6 h 36 min
    ███ ▄▄▄ ███      Charge        0.08  0.23  0.40
    ███ ███ ███      Mémoire       1,4 Gio / 3,8 Gio (37 %)
    ▀▀▀ ▀▀▀ ▀▀▀      Disque /      6,0 Gio / 28,7 Gio (23 %)
                     Adresse IP    192.168.1.20
   JeeTerm           Plugins       17
                     Temp. CPU     48,3 °C
```

Suivent une invite en couleur (rouge pour root, avec le code de sortie de la
commande qui a échoué) et des raccourcis :

| Commande | Effet |
|---|---|
| `jcd [plugin]` | aller dans le dossier d'un plugin (Tab complète le nom) |
| `jlog [journal]` | suivre un journal de Jeedom ; sans argument, les lister |
| `jerr` | suivre les erreurs PHP (`log/http.error`) |
| `jplugins` | plugins installés et leur version |
| `jinfo` | réafficher la bannière |
| `jrestart` | redémarrer Apache, après confirmation |
| `jauteur` | qui a écrit JeeTerm |
| `jhelp` | la liste de ces raccourcis |

Vos propres alias vont dans `~/.jeetermrc`. Une case de la configuration coupe
bannière et raccourcis pour retrouver un `bash` tout simple.

## Comment ça marche

```
Navigateur ──HTTP(S)/WebSocket──▶ Apache (Jeedom) ──ws://127.0.0.1:55080──▶ démon jeetermd ──pty──▶ shell
```

- La page demande une session à Jeedom. PHP vérifie que vous êtes
  administrateur, tire un **jeton aléatoire de 256 bits** et le confie au démon.
  Le jeton vit 30 secondes et ne sert qu'une fois.
- Le navigateur ouvre un WebSocket sur `/plugins/jeeterm/ws`, qu'**Apache
  relaie** vers le démon. Le démon n'écoute que sur `127.0.0.1` : il n'est
  jamais joignable directement depuis le réseau, et tout passe par le port
  habituel de Jeedom, en HTTP comme en HTTPS.
- Le démon est écrit en **Python, avec la seule bibliothèque standard** : aucun
  paquet `pip`, aucun environnement virtuel. Il vise Debian 11 à 13 (Python 3.7
  et plus), sur Raspberry Pi comme sur PC.
- Sous systemd, le démon tourne dans sa propre unité : redémarrer ou mettre à
  jour Apache ne ferme pas les terminaux ouverts.

## Installation

1. Installer le plugin, puis l'activer.
2. Lancer l'installation des **dépendances** depuis la page de configuration du
   plugin.
3. Démarrer le **démon** (il démarre seul si la gestion automatique est active).
4. Ouvrir **Plugins → Programmation → JeeTerm**.

L'installation des dépendances est la seule étape qui touche au système. Elle
installe `python3` et `tmux` s'ils manquent, active les modules Apache `proxy`
et `proxy_wstunnel`, et écrit `/etc/apache2/conf-available/jeeterm.conf`. La
configuration d'Apache est vérifiée avant chaque rechargement : une
configuration refusée est retirée, jamais appliquée. Elle est enlevée à la
désactivation du plugin. Le détail est dans la
[documentation](docs/fr_FR/index.md#installation).

## Configuration

| Réglage | Défaut | Rôle |
|---|---|---|
| Compte du shell | `www-data` | `www-data`, `root`, ou un autre compte Unix |
| Session persistante (tmux) | non | retrouver sa session à la réouverture |
| Bannière d'accueil et raccourcis Jeedom | oui | bannière, invite en couleur, commandes `j…` |
| Fermeture après inactivité | 15 min | une commande qui travaille n'est jamais coupée |
| Sessions simultanées | 3 | de 1 à 20 |
| Réseau local uniquement | non | refuser les ouvertures depuis l'extérieur |
| Port local | 55080 | port du démon, sur `127.0.0.1` seulement |

## Sécurité

- **Administrateurs seulement**, par une requête POST protégée contre les
  requêtes forgées depuis un autre site.
- **Jeton à usage unique**, valable 30 secondes : même relevé dans le journal
  d'accès d'Apache, il ne sert plus à rien.
- **Démon injoignable du réseau** : il n'écoute que sur la boucle locale, et
  refuse les demandes d'enregistrement qui arrivent par Apache.
- **Clé API** du plugin limitée à la boucle locale, transmise au démon par un
  fichier `0600` supprimé aussitôt lu : elle n'apparaît jamais dans `ps`.
- **Audit** : chaque ouverture, fermeture et refus de session est écrit dans le
  journal `jeetermd`, quel que soit le niveau de journal choisi.
- **Inactivité** : un terminal oublié se ferme tout seul.

« Réseau local uniquement » est un filtre de confort, pas une frontière : il
s'appuie sur l'adresse du client telle qu'Apache la voit.

## Documentation

- [Documentation complète](docs/fr_FR/index.md) —
  [English](docs/en_US/index.md)
- [Changements](docs/fr_FR/changelog.md)
- [Conception](CONCEPTION.md) : le contrat entre le démon, PHP et l'interface

## Développement

```bash
python3 -m unittest tests/test_daemon.py   # démon : protocole, sessions, shell
php tests/check-classes.php                # pièges des classes Jeedom
```

## Auteur

**sMug** (Jérôme Fafchamps) — jerome@fafchamps.be

## Licence

AGPL v3. xterm.js et ses extensions (`3rdparty/xterm/`) sont sous licence MIT.
