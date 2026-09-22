# JeeTerm — contrat de conception

Plugin Jeedom (id `jeeterm`) : un vrai terminal interactif dans l'interface
Jeedom, à la manière du module « Terminal » de Home Assistant.

## Architecture

```
Navigateur : page du plugin (desktop/php/jeeterm.php + desktop/js/jeeterm.js, xterm.js 6 vendorisé)
  1. POST core/ajax/jeeterm.ajax.php action=createSession   (isConnect('admin'))
       -> PHP génère un jeton (32 octets hex), l'enregistre auprès du démon,
          renvoie {token, path:"plugins/jeeterm/ws"}
  2. WebSocket ws(s)://<hôte>/<base>/plugins/jeeterm/ws?t=<jeton>&c=<cols>&r=<rows>
Apache : <Location /plugins/jeeterm/ws> ProxyPass ws://127.0.0.1:<port>/ws  (mod_proxy_wstunnel)
Démon : resources/jeetermd/jeetermd.py, Python 3 bibliothèque standard SEULE
        (asyncio + pty, WebSocket RFC 6455 implémenté à la main), écoute 127.0.0.1:<port>
        -> un pty par session -> shell
```

Compatibilité : Python >= 3.7 (Debian 11), aucune dépendance pip.

## Configuration du plugin (config::byKey(<clé>, 'jeeterm', <défaut>))

| clé            | défaut | sens |
|----------------|--------|------|
| `port`         | 55080  | port TCP local du démon (127.0.0.1) |
| `shell_user`   | `''`   | vide = utilisateur du démon (www-data) ; `root` ; ou un nom de compte |
| `tmux`         | 0      | 1 = session persistante `tmux new-session -A -s jeeterm` |
| `goodies`      | 1      | 1 = bannière d'accueil, invite et raccourcis (`bash --rcfile jeeterm.bashrc -i`) |
| `idle_timeout` | 15     | minutes sans entrée clavier avant fermeture (0 = jamais) |
| `max_sessions` | 3      | sessions simultanées |
| `lan_only`     | 0      | 1 = refus si `network::getUserLocation() != 'internal'` (vérifié côté PHP) |

Changer une clé impose de redémarrer le démon (la page de configuration le dit).
Changer `port` impose de relancer l'installation des dépendances (conf Apache).

## Lancement du démon

```
echo <apikey> | python3 resources/jeetermd/jeetermd.py \
   --port 55080 --pid /tmp/jeedom/jeeterm/deamon.pid --loglevel debug|info|warning|error \
   --shell-user '' --tmux 0 --goodies 1 --idle-timeout 900 --max-sessions 3 \
   >> log/jeetermd 2>&1 &
```
- La clé API est lue sur l'entrée standard (première ligne), jamais en argument.
- `--idle-timeout` est en SECONDES côté démon (PHP convertit les minutes).
- Les journaux vont sur stdout/stderr, format : `[AAAA-MM-JJ HH:MM:SS][NIVEAU] : message`.
- SIGTERM : ferme toutes les sessions (tue le groupe de processus de chaque shell), retire le pid, sort.

## Enregistrement d'un jeton (PHP -> démon)

Requête HTTP simple (non WebSocket), **uniquement depuis 127.0.0.1 et sans en-tête
X-Forwarded-For** (sinon 403 : une requête passée par Apache en porte un) :

```
GET /register HTTP/1.1
X-Jeeterm-Apikey: <apikey>
X-Jeeterm-Token: <64 caractères hex>
X-Jeeterm-User: <login Jeedom>
X-Jeeterm-Ip: <IP du navigateur>
```
Réponses : `200 {"ok":true}` ; `401` clé fausse (comparaison à temps constant) ;
`400` jeton mal formé ; `403` provenance refusée ; `429` trop de sessions ouvertes.
Le jeton vit 30 s et sert une seule fois. `GET /status` avec la même clé renvoie
`{"ok":true,"sessions":N}`.

## Protocole WebSocket (chemin `/ws`)

- Poignée de main : `?t=<jeton>` obligatoire, valide, non expiré, jamais utilisé ;
  sinon réponse HTTP 403 avant l'upgrade. `c`/`r` optionnels (défaut 80x24, bornés 10..500).
- Origin : si présent et que son hôte diffère de `X-Forwarded-Host` / `Host`,
  on journalise un avertissement (pas de refus : le jeton suffit).
- Navigateur -> démon, trames TEXTE JSON :
  - `{"t":"i","d":"<texte saisi>"}` entrée clavier (UTF-8)
  - `{"t":"r","c":<cols>,"r":<rows>}` redimensionnement
  - `{"t":"p"}` maintien (ignoré, sert à garder le tunnel Apache vivant)
- Démon -> navigateur :
  - trames BINAIRES : sortie brute du pty (octets), à passer telles quelles à `term.write(new Uint8Array(...))`
  - trame TEXTE `{"t":"x","code":<code de sortie>,"reason":"..."}` juste avant la fermeture
    (fin du shell, inactivité, arrêt du démon)
- Le démon envoie un ping WebSocket toutes les 30 s.
- Fermeture du WebSocket => SIGHUP puis SIGKILL au groupe de processus du shell
  (sauf mode tmux : le client tmux meurt, la session tmux reste).

## Shell lancé

Environnement minimal : `TERM=xterm-256color`, `COLORTERM=truecolor`, `LANG` (celui
du démon ou `C.UTF-8`), `PATH` standard, `HOME`, `USER`, `SHELL`.

| shell_user        | commande |
|-------------------|----------|
| vide (www-data)   | `/bin/bash -l` (cwd `/var/www/html`, HOME = home de l'utilisateur courant) |
| `root`            | `sudo -n -i` |
| autre compte `X`  | `sudo -n -i -u X` (refus si le compte n'existe pas ou a un shell nologin/false) |

Avec `tmux=1` : la commande devient `tmux new-session -A -s jeeterm` exécutée
comme l'utilisateur cible (`sudo -n -i -u X tmux new-session -A -s jeeterm`, etc.).
Si tmux est absent : on retombe sur le shell simple et on l'écrit dans le terminal.

## Journal des sessions

À l'ouverture et à la fermeture, niveau info : utilisateur Jeedom, IP, compte du shell,
durée, raison de fin.

## Fichiers et responsables

- Démon : `resources/jeetermd/jeetermd.py`, `resources/jeetermd/jeeterm.bashrc`, tests `tests/test_daemon.py`
- Interface : `desktop/php/jeeterm.php`, `desktop/js/jeeterm.js`, `desktop/css/jeeterm.css`,
  `3rdparty/xterm/` (xterm 6.0.0, addon-fit 0.11.0, addon-web-links 0.12.0 + LICENSE)
- PHP : `plugin_info/{info.json,install.php,configuration.php,jeeterm_icon.png}`,
  `core/class/jeeterm.class.php`, `core/ajax/jeeterm.ajax.php`, `core/config/jeeterm.config.ini`,
  `resources/install_apt.sh`, `docs/{fr_FR,en_US}/{index,changelog}.md`, `README.md`,
  `core/i18n/en_US.json`, `.gitignore`, `.deployignore`

## Évolutions après la première revue (0.1)

- **Lancement** : sous systemd avec sudo, le démon part dans une unité transitoire
  (`sudo systemd-run --unit=jeetermd-<date> --uid/--gid=<utilisateur PHP> -p KillMode=process`),
  hors du cgroup d'Apache : un redémarrage d'Apache ne tue plus les shells ni une mise à jour
  apt en cours. Repli sur un simple `exec … &` (Docker, pas de sudo).
- **Clé API** : `--apikey-file <fichier 0600>`, lu puis supprimé par le démon (stdin reste accepté).
- **Arrêt** : par pid, puis balayage de `/proc` limité à nos propres `jeetermd.py` ; plus de
  `fuser -k` sur le port. Le démon ferme ses sessions en moins de 4 s.
- **Trame de fin** : `{"t":"x","code":N,"reason":"…","why":"exit|idle|shutdown|lost|error|closed"}`.
- **`/register`** : 503 pendant l'arrêt du démon ; un jeton non utilisé réserve une place ; un
  nouveau jeton d'un même utilisateur remplace ses jetons inutilisés.
- **Inactivité** : pas de fermeture tant qu'une commande tourne et montre de l'activité
  (CPU ou sortie) ; avertissement dans le terminal 60 s avant la fermeture.
- **Shell** : locale UTF-8 forcée (`LANG`/`LC_ALL`), `tmux -u`, HOME inscriptible
  (`/var/lib/jeeterm/home` créé par les dépendances) pour www-data.
- **Apache** : `<LocationMatch "^(/.*)?/plugins/jeeterm/ws$">`, repère `# jeeterm-conf v2`
  exigé par `dependancy_info` ; la conf est retirée à la désactivation et réinstallée à la
  réactivation.
- **Ajax** : en-tête `X-Requested-With: XMLHttpRequest` exigé ; action `diagnose`
  (`{code, message, daemon, dependancy, problems, probe}`) appelée par la page quand le
  WebSocket échoue avant l'ouverture ; `health()` pour la page Santé.
- **Journal** : ouvertures, fermetures et refus de session au niveau warning.
- **Interface** : addons search (Ctrl+Maj+F) et unicode11 (`allowProposedApi: true`), zone de
  collage en http, Entrée pour rouvrir une session, aucune reconnexion automatique.

## Évolutions 0.2 : bannière et raccourcis

- **Réglage `goodies`** (défaut 1, argument `--goodies 0|1`, défaut 1 côté démon) : inclus dans
  `getDaemonSettings()`, donc dans la comparaison avec `daemon_settings` ; `postConfig_goodies`
  redémarre le démon comme les autres clés. À 0, les commandes de la section « Shell lancé »
  restent inchangées.
- **Fichier de démarrage** `resources/jeetermd/jeeterm.bashrc` (lisible par tous, jamais servi :
  `resources/.htaccess` refuse l'accès web). À 1, la commande du shell devient :

  | shell_user        | commande |
  |-------------------|----------|
  | vide (www-data)   | `/bin/bash --rcfile <rc> -i` |
  | `root` / `X`      | `sudo -n -i [-u X] /bin/bash --rcfile <rc> -i`, seulement si le shell du compte est bash ; sinon `sudo -n -i [-u X]` comme avant |
  | tmux              | `tmux -u new-session -A -s jeeterm '<commande>' ';' set-option default-command '<commande>'` (sous sudo le cas échéant) |

- **Ce que fait le fichier** : sans `SUDO_USER`, il refait la lecture d'un shell de connexion
  (`/etc/profile`, qui lit `/etc/bash.bashrc` sur Debian, puis le premier de `~/.bash_profile`,
  `~/.bash_login`, `~/.profile`, à défaut `~/.bashrc`) ; par `sudo -i`, le shell de connexion
  du compte a déjà lu les profils et le fichier ne lit que `/etc/bash.bashrc` et `~/.bashrc`.
  Il pose ensuite historique, couleurs, invite (OSC 0 pour le titre, code de sortie en rouge
  via `PROMPT_COMMAND`, placé en tête pour relever `$?`), alias et fonctions `jcd`, `jlog`,
  `jerr`, `jplugins`, `jinfo`, `jrestart`, `jhelp`, puis lit `~/.jeetermrc` et affiche la
  bannière (`JEETERM_BANNER=0` la retire ; dans tmux, seulement dans le panneau `%0`).
- **Contraintes** : aucun appel réseau, lecture de `/proc` plutôt que des commandes (seuls `df`,
  `hostname -I` et `stty size` sont lancés) : ~25 ms. Bannière dans 80 colonnes, version
  compacte sans logo sous 70. Pas de couleur si `TERM=dumb`, logo ASCII hors UTF-8. Libellés en
  français si la locale est `fr*`, en anglais sinon.
- **Racine de Jeedom** : `JEEDOM_DIR`, déduite du chemin du fichier (cinq niveaux au-dessus),
  `/var/www/html` si elle ne contient pas `core/config/version`.
- **Interface 0.2** : panneau de réglages (thèmes Jeedom auto, Noir, Phosphore vert, Ambre,
  Dracula, Solarized sombre, Monokai, Nord, Tango clair ; effet CRT ; curseur ; police ;
  interligne ; cloche ; copie à la sélection), commandes rapides personnalisables (tapées sans
  Entrée sauf « Exécuter aussitôt »), enregistrement du tampon en .txt, effacement, titre du
  shell et taille cols×rows affichés. Réglages dans le localStorage du navigateur.
