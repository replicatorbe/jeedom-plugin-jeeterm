# Plugin JeeTerm

Un vrai terminal dans l'interface de Jeedom, à la manière du module
« Terminal » de Home Assistant. Le shell tourne sur la machine Jeedom et
s'affiche dans le navigateur : couleurs, raccourcis clavier, redimensionnement,
éditeurs plein écran (`nano`, `vim`, `htop`, `mc`…), et en option une session
`tmux` qui survit à la fermeture de l'onglet.

> **Ce terminal donne un accès root à la machine.** Sur une installation
> Jeedom, le compte `www-data` peut utiliser `sudo` sans mot de passe : même
> ouvert sous `www-data`, le shell permet de tout faire. Seuls les
> administrateurs de Jeedom peuvent l'ouvrir. Ne donnez ce profil qu'à des
> personnes de confiance.

## Comment ça marche

```
Navigateur ──HTTPS/WebSocket──▶ Apache (Jeedom) ──ws://127.0.0.1:55080──▶ démon jeetermd ──pty──▶ shell
```

- La page du plugin demande une session à Jeedom. PHP vérifie que vous êtes
  administrateur, fabrique un jeton aléatoire de 256 bits et le confie au démon.
- Le navigateur ouvre alors un WebSocket sur `/plugins/jeeterm/ws`, que
  **Apache relaie** vers le démon. Le démon n'écoute que sur `127.0.0.1` : il
  n'est jamais joignable directement depuis le réseau.
- Le jeton n'est valable que **30 secondes** et ne sert **qu'une fois**.
- Le démon est écrit en Python, avec la seule bibliothèque standard : aucun
  paquet `pip`, aucun environnement virtuel.
- Sur une machine gérée par systemd, le démon est lancé dans sa propre unité
  (`systemd-run`, unité `jeetermd-<date>`) : un redémarrage ou une mise à jour
  d'Apache ne ferme donc pas les terminaux ouverts. Sans systemd (Docker), il
  est lancé directement.

## Installation

1. Installer le plugin depuis le Market, puis l'activer.
2. Lancer l'installation des **dépendances** (page de configuration du plugin).
3. Démarrer le **démon** (il démarre seul si la gestion automatique est active).
4. Ouvrir **Plugins → Programmation → JeeTerm**.

### Ce que l'installation des dépendances modifie

Le script `resources/install_apt.sh`, lancé en root par Jeedom, fait ceci et
rien d'autre. Le détail de son exécution est dans le journal `jeeterm_update`.

| Action | Pourquoi |
|---|---|
| `apt-get install python3 tmux`, seulement s'il en manque un | `python3` fait tourner le démon ; `tmux` sert à l'option de session persistante. Si un autre `apt` tient le verrou (mise à jour automatique…), le script attend jusqu'à 5 minutes. |
| Crée `/var/lib/jeeterm/home` (propriétaire `www-data`, droits `0700`) | Dossier personnel du shell ouvert sous `www-data`, dont le vrai dossier (`/var/www`) appartient à root : historique de bash, socket de tmux. |
| `a2enmod proxy proxy_wstunnel` | Modules d'Apache qui savent relayer un WebSocket. |
| Écrit `/etc/apache2/conf-available/jeeterm.conf` et l'active (`a2enconf jeeterm`) | Déclare le relais de `…/plugins/jeeterm/ws` vers le démon. |
| `apache2ctl configtest` | Vérifie la configuration **avant** de recharger Apache. |
| Recharge Apache en douceur | `systemctl reload`, sinon `service apache2 reload`, sinon `apache2ctl -k graceful` (installations Docker sans systemd). |

Relancer l'installation ne fait que réécrire la configuration et recharger
Apache : c'est ce qu'il faut faire après un changement de port.

La configuration écrite est la suivante (le port est celui du plugin) :

```apache
# jeeterm-conf v2
# JeeTerm — généré par le plugin, ne pas modifier : réécrit à chaque
# installation des dépendances, retiré à la désactivation du plugin.
<IfModule mod_proxy_wstunnel.c>
  <LocationMatch "^(?:/.*)?/plugins/jeeterm/(ws)$">
    ProxyPassMatch "ws://127.0.0.1:55080/$1"
    ProxyPreserveHost On
  </LocationMatch>
</IfModule>
```

Le motif accepte n'importe quel préfixe : le terminal fonctionne aussi quand
Jeedom est servi dans un sous-dossier (`http://hote/jeedom/`), sans rien
adapter.

Garde-fous : si la configuration d'Apache est **déjà** en erreur avant
l'installation, le script s'arrête sans rien toucher. Si c'est la configuration
de JeeTerm que refuse `configtest`, il remet l'état précédent et s'arrête en
erreur : Apache n'est jamais rechargé avec une configuration invalide.

Les dépendances sont jugées correctes quand `python3` est présent, que les
modules `proxy` et `proxy_wstunnel` sont actifs, et que
`/etc/apache2/conf-enabled/jeeterm.conf` existe, est de la version attendue par
le plugin et pointe sur le port configuré. Avec l'option tmux, `tmux` doit
aussi être installé. Une mise à jour du plugin qui change la forme de la
configuration rend donc les dépendances incomplètes, et leur réinstallation la
réécrit.

JeeTerm suppose l'Apache de Jeedom organisé à la manière de Debian
(`/etc/apache2`, `a2enmod`, `a2enconf`). Ailleurs, l'installation s'arrête en
erreur.

## Configuration

| Réglage | Défaut | Rôle |
|---|---|---|
| Compte du shell | www-data | `www-data` (le compte de Jeedom), `root`, ou un autre compte Unix. `root` et les autres comptes passent par `sudo -n -i`. Un compte inexistant ou au shell désactivé (`nologin`) est refusé dès l'enregistrement. |
| Session persistante (tmux) | non | Le shell tourne dans la session tmux `jeeterm`, qu'on retrouve telle quelle à la réouverture. Toutes les fenêtres partagent cette session. |
| Bannière d'accueil et raccourcis Jeedom | oui | Bannière d'accueil, invite en couleur et raccourcis `jlog`, `jcd`… Voir « Bannière et raccourcis ». Décoché, le shell est un simple `bash -l` (ou `sudo -n -i`). |
| Fermeture après inactivité | 15 min | Délai sans aucune frappe au clavier avant fermeture, de `0` (jamais) à `10080` (une semaine). |
| Sessions simultanées | 3 | Nombre maximal de terminaux ouverts, tous administrateurs confondus, de 1 à 20. |
| Réseau local uniquement | non | Refuse d'ouvrir un terminal depuis l'extérieur. Voir plus bas. |
| Port local | 55080 | Port du démon, sur `127.0.0.1` seulement, de 1024 à 65535. |

Une valeur invalide est refusée à l'enregistrement, avec un message qui dit
pourquoi.

**Les réglages du démon s'appliquent à son redémarrage.** À l'enregistrement de
la configuration, JeeTerm compare les réglages à ceux avec lesquels le démon
tourne, et le redémarre en arrière-plan s'ils diffèrent : cela ferme tous les
terminaux ouverts (une session tmux, elle, continue). Enregistrer sans rien
changer ne redémarre rien. Si la gestion automatique du démon est désactivée,
un message vous demande de le redémarrer vous-même. « Réseau local uniquement »
s'applique aussitôt, sans redémarrage. Le niveau du journal, lui, n'est pris en
compte par le démon qu'à son prochain redémarrage.

**Changer le port** impose de relancer l'installation des dépendances, qui
réécrit la configuration Apache. Tant qu'elle n'est pas faite, les dépendances
sont signalées incomplètes et le démon reste arrêté. Si les gestions
automatiques du démon **et** des dépendances sont actives, Jeedom la relance de
lui-même au bout de quelques minutes.

## Utilisation

Ouvrez la page du plugin : le terminal s'ouvre directement. La barre d'outils
permet de reconnecter, d'ouvrir une nouvelle session, de copier la sélection
(`Ctrl+Maj+C`), de coller, de changer la taille de la police et de passer en
plein écran. Une rangée de touches spéciales (Échap, Tab, Ctrl, Alt, flèches)
est prévue pour les tablettes.

Fermer l'onglet ferme le shell et tout ce qu'il a lancé, sauf en mode tmux où la
session continue de tourner.

Quand une session se termine, **Entrée** en ouvre une nouvelle. `Ctrl+Maj+F`
cherche dans le texte affiché ; `Ctrl+W`, `Ctrl+T` et `Ctrl+N` restent au
navigateur, aucune page web ne peut les intercepter.

Le bouton **⚙** ouvre les réglages du terminal, mémorisés par navigateur :
thème (Jeedom automatique, Noir, Phosphore vert, Ambre, Dracula, Solarized
sombre, Monokai, Nord, Tango clair), effet écran cathodique, curseur, police,
taille, interligne, cloche et copie dès la sélection. Le bouton **⚡
Commandes** tape une commande toute prête dans le terminal, sans la valider ;
on y ajoute les siennes. La barre permet aussi d'enregistrer tout le contenu
du terminal dans un fichier `.txt` et d'effacer l'écran.

### Bannière et raccourcis

Avec le réglage « Bannière d'accueil et raccourcis Jeedom » (actif par défaut),
chaque nouveau shell s'ouvre sur un résumé de la machine :

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

Astuce : jlog <plugin> suit un journal Jeedom, Tab complète le nom.
```

La température n'apparaît que si le système la fournit (Raspberry Pi…). Sous 70
colonnes, le logo disparaît et seules les informations restent.

L'invite est en couleur (`compte@machine:dossier$`, en rouge pour root) ; quand
la dernière commande a échoué, son code de sortie la précède, par exemple `[1]`.
Elle donne aussi son titre à l'onglet. Les raccourcis :

| Commande | Effet |
|---|---|
| `jcd [plugin]` | va dans le dossier du plugin (sans argument : le dossier des plugins) ; Tab complète le nom |
| `jlog [journal]` | suit un journal de Jeedom (`tail -F`) ; sans argument, liste les journaux ; Tab complète le nom |
| `jerr` | suit `log/http.error`, les erreurs PHP |
| `jplugins` | liste les plugins installés et leur version |
| `jinfo` | réaffiche la bannière |
| `jrestart` | affiche les commandes de redémarrage d'Apache et les lance après confirmation |
| `jauteur` | carte de l'auteur : sMug (Jérôme Fafchamps), jerome@fafchamps.be |
| `jhelp` | liste ces raccourcis |
| `ll`, `la`, `l` | variantes de `ls` ; `ls` et `grep` sont en couleur |

L'historique garde 10 000 commandes, sans doublons successifs ni commandes
commençant par une espace.

Le shell reste celui du compte : `/etc/profile`, puis `~/.profile` (ou
`~/.bash_profile`) et `~/.bashrc` sont lus comme dans un shell de connexion ;
pour `www-data`, le dossier personnel est `/var/lib/jeeterm/home`. L'invite
de JeeTerm remplace celle de `~/.bashrc` : pour garder la vôtre, ou pour ajouter
vos propres alias, écrivez-les dans `~/.jeetermrc`, lu en dernier.
`JEETERM_BANNER=0` dans ce fichier retire la bannière. Un compte dont le shell
n'est pas bash (zsh…) garde son shell, sans raccourcis.

En mode tmux, la bannière s'affiche à la création de la session, pas quand on
la retrouve ; les fenêtres ouvertes ensuite dans tmux ont les raccourcis, sans
bannière.

## Sécurité

- **Équivalent root.** `www-data` a `sudo` sans mot de passe sur Jeedom : quel
  que soit le compte choisi, un administrateur qui ouvre le terminal a la main
  sur toute la machine.
- **Administrateurs seulement.** La demande de session exige le profil
  administrateur de Jeedom, par une requête POST. Une requête forgée depuis un
  autre site est écartée : le cookie de session de Jeedom est `SameSite=Strict`,
  et JeeTerm exige en plus l'en-tête `X-Requested-With`.
- **Jeton à usage unique.** 256 bits aléatoires, valables 30 secondes, utilisables
  une fois. Il est enregistré auprès du démon par la boucle locale, avec la clé
  API du plugin, et refusé s'il arrive par Apache.
- **Démon injoignable du réseau.** Il n'écoute que sur `127.0.0.1` ; seul Apache
  y accède. L'API du plugin est réglée en mode « localhost ».
- **Réseau local uniquement.** Cette option refuse l'ouverture quand Jeedom situe
  le navigateur hors du réseau local, et exige en plus que l'adresse réelle de
  la connexion soit locale. C'est un **filtre de confort, pas une frontière de
  sécurité** : l'adresse du client est déduite d'en-têtes HTTP, et la
  configuration Apache de Jeedom (`mod_remoteip`) leur fait confiance. Un proxy
  inverse installé sur le réseau local fait passer tous ses clients pour locaux.
  Les règles de Jeedom ne connaissent que l'IPv4 : un navigateur connecté en
  IPv6 est refusé, sauf si son adresse exacte figure dans les adresses locales
  de la configuration réseau de Jeedom.
- **Inactivité.** Un terminal oublié se ferme après le délai réglé.
- **Journal.** Chaque demande de session, acceptée ou refusée, est journalisée
  dans `jeeterm` au niveau *warning* : utilisateur Jeedom, adresse IP déclarée
  et, si elle diffère, adresse réelle de la connexion. Ouvertures et fermetures
  le sont dans `jeetermd` au niveau *AUDIT*, avec le compte du shell, la durée
  et la raison de fin. Ces lignes d'audit sont écrites quel que soit le niveau
  de journal choisi, sauf s'il est réglé sur « Aucun ».

## En cas de problème

**Le terminal ne s'ouvre pas.** Regardez d'abord :

- le journal `jeetermd` (démon) et le journal `jeeterm` (demandes de session) ;
- le journal `jeeterm_update` si les dépendances sont signalées incomplètes ;
- `/var/www/html/log/http.error` pour toute erreur PHP de la page ;
- la page **Santé** de Jeedom, où JeeTerm teste le démon et le relais Apache.

Quand la connexion échoue, la page du terminal lance un diagnostic côté
serveur : elle interroge le relais Apache comme le ferait le navigateur, et dit
où se situe le blocage (dépendances, démon, configuration Apache, ou
intermédiaire devant Apache).

**« Le démon JeeTerm n'est pas démarré »** : démarrez-le depuis la page de
configuration du plugin, et lisez le journal `jeetermd` s'il refuse.

**« Nombre maximal de sessions atteint »** : un terminal est resté ouvert dans
un autre onglet ou sur un autre appareil. Fermez-le, attendez la fermeture pour
inactivité, ou augmentez la limite.

**La connexion est refusée ou se coupe aussitôt** : la configuration Apache ne
pointe peut-être pas sur le bon port. Relancez l'installation des dépendances et
vérifiez que `/etc/apache2/conf-enabled/jeeterm.conf` existe.

**Accès par le DNS Jeedom ou par un proxy inverse.** Le terminal passe par un
WebSocket : chaque intermédiaire doit le laisser passer (en-têtes `Upgrade` et
`Connection`). Ce n'est **pas garanti** avec le DNS Jeedom ; avec votre propre
proxy inverse (nginx, Traefik, Caddy…), activez le support WebSocket sur
`/plugins/jeeterm/ws`.

**Jeedom installé dans un sous-dossier** (par exemple `http://hote/jeedom/`) :
rien à faire, la configuration Apache relaie `…/plugins/jeeterm/ws` quel que
soit le préfixe.

## Désinstaller

Désactiver ou supprimer le plugin arrête le démon et **retire la configuration
Apache** (`a2disconf jeeterm`, suppression du fichier, rechargement d'Apache).
Jeedom appelle la même fonction pour une désactivation et pour une suppression ;
à la réactivation, il constate que les dépendances manquent et les réinstalle
de lui-même si leur gestion automatique est active (sinon, bouton « Relancer »
des dépendances).

Si `sudo` n'est pas disponible pour `www-data`, la configuration reste en place
(le journal `jeeterm` le signale). Sans démon, le tunnel ne mène nulle part
(Apache répond 503). Pour la retirer à la main :

```bash
sudo a2disconf jeeterm
sudo rm /etc/apache2/conf-available/jeeterm.conf
sudo systemctl reload apache2      # ou : sudo service apache2 reload
```

Restent en place : les modules `proxy` et `proxy_wstunnel`, qui peuvent servir à
d'autres usages (ne les désactivez, `sudo a2dismod proxy_wstunnel`, que si vous
savez que rien d'autre n'en dépend), les paquets `python3` et `tmux`, et le
dossier `/var/lib/jeeterm` (`sudo rm -r /var/lib/jeeterm` pour l'effacer, avec
l'historique du shell qu'il contient).
