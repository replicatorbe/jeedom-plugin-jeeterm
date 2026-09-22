<?php
/* This file is part of Jeedom.
 *
 * Jeedom is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * Jeedom is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with Jeedom. If not, see <http://www.gnu.org/licenses/>.
 */

require_once __DIR__ . '/../../../../core/php/core.inc.php';

/*
 * JeeTerm n'a pas d'équipement : la classe eqLogic n'existe que parce que le
 * coeur l'exige pour charger le plugin, gérer son démon et ses dépendances.
 */
class jeeterm extends eqLogic {

    const DEFAULT_PORT = 55080;

    /* Bornes des réglages, partagées par la validation à l'enregistrement
     * (preConfig_*) et par la lecture (getDaemonSettings). */
    const MAX_IDLE_MINUTES = 10080;
    const MAX_SESSIONS = 20;

    /* Chemin public du tunnel WebSocket, relatif à la racine de Jeedom. Il doit
     * correspondre au <Location> écrit par resources/install_apt.sh. */
    const WS_PATH = 'plugins/jeeterm/ws';

    const APACHE_CONF = '/etc/apache2/conf-enabled/jeeterm.conf';
    const APACHE_CONF_AVAILABLE = '/etc/apache2/conf-available/jeeterm.conf';
    /* Première ligne de la conf écrite par install_apt.sh. Elle change quand la
     * forme de la conf change : une conf plus ancienne rend les dépendances
     * incomplètes, et leur réinstallation la réécrit. */
    const APACHE_CONF_MARKER = '# jeeterm-conf v2';
    const APACHE_PROXY = '/etc/apache2/mods-enabled/proxy.load';
    const APACHE_WSTUNNEL = '/etc/apache2/mods-enabled/proxy_wstunnel.load';

    /* Un seul examen de la configuration par requête HTTP, même si la page de
     * configuration enregistre cinq clés d'affilée. */
    private static $_restartScheduled = false;


    /* ========================================================== RÉGLAGES */

    /* Port local du démon. Une valeur hors plage retombe sur le défaut plutôt
     * que de produire une ligne de commande ou une conf Apache invalide. */
    public static function getPort() {
        $port = (int) config::byKey('port', __CLASS__, self::DEFAULT_PORT);
        if ($port < 1024 || $port > 65535) {
            return self::DEFAULT_PORT;
        }
        return $port;
    }

    /*
     * Compte sous lequel le shell est ouvert : vide pour l'utilisateur du démon
     * (www-data), sinon un nom de compte Unix. Le nom finit sur la ligne de
     * commande de sudo : il est validé ici, pas seulement échappé.
     */
    public static function getShellUser() {
        return trim((string) config::byKey('shell_user', __CLASS__, ''));
    }

    public static function isValidShellUser($_user) {
        return $_user === '' || preg_match('/^[a-z_][a-z0-9_.-]{0,31}$/i', $_user) === 1;
    }

    /* Réglages transmis au démon à son lancement. Tout changement de l'un d'eux
     * n'est pris en compte qu'au redémarrage suivant. */
    public static function getDaemonSettings() {
        $idle = (int) config::byKey('idle_timeout', __CLASS__, 15);
        $max = (int) config::byKey('max_sessions', __CLASS__, 3);
        return array(
            'port'         => self::getPort(),
            'shell_user'   => self::getShellUser(),
            'tmux'         => (config::byKey('tmux', __CLASS__, 0) == 1) ? 1 : 0,
            /* Bannière d'accueil, invite et raccourcis (resources/jeetermd/jeeterm.bashrc). */
            'goodies'      => (config::byKey('goodies', __CLASS__, 1) == 1) ? 1 : 0,
            /* Minutes côté interface, secondes côté démon. */
            'idle_timeout' => min(max(0, $idle), self::MAX_IDLE_MINUTES) * 60,
            'max_sessions' => min(max(1, $max), self::MAX_SESSIONS),
        );
    }

    /* Réglages avec lesquels le démon a été lancé pour la dernière fois.
     * config::byKey décode lui-même le JSON enregistré : on reçoit un tableau,
     * pas une chaîne. */
    private static function getRunningSettings() {
        $running = config::byKey('daemon_settings', __CLASS__, '');
        if (is_string($running) && $running !== '') {
            $running = json_decode($running, true);
        }
        return is_array($running) ? $running : null;
    }

    /* Clé API du plugin. Le mode « localhost » est passé à la création : sinon
     * le coeur crée la clé en mode « enable », joignable depuis le réseau. */
    private static function getApiKey() {
        return jeedom::getApiKey(__CLASS__, 'localhost');
    }


    /* ---------------------------------------------- validation à l'enregistrement
     *
     * config::save appelle <plugin>::preConfig_<clé>() avant d'écrire une valeur
     * différente du défaut. Une exception y arrête l'enregistrement et s'affiche
     * sur la page de configuration : c'est là que l'on refuse une saisie
     * invalide, plutôt que de laisser un démon impossible à lancer. */

    public static function preConfig_port($_value) {
        $value = trim((string) $_value);
        if (!ctype_digit($value) || (int) $value < 1024 || (int) $value > 65535) {
            throw new Exception(__('Port local invalide : entrez un nombre entre 1024 et 65535.', __FILE__));
        }
        return (string) (int) $value;
    }

    public static function preConfig_shell_user($_value) {
        $value = trim((string) $_value);
        if (!self::isValidShellUser($value)) {
            throw new Exception(__('Compte du shell invalide : lettres, chiffres, point, tiret et souligné seulement, 32 caractères au plus.', __FILE__));
        }
        /* Le compte du démon lui-même : c'est le réglage par défaut (vide). */
        if ($value === '' || $value === self::getDaemonUser()) {
            return '';
        }
        if (!function_exists('posix_getpwnam')) {
            return $value;
        }
        $account = posix_getpwnam($value);
        if ($account === false) {
            throw new Exception(__('Compte du shell inconnu sur cette machine :', __FILE__) . ' ' . $value);
        }
        /* root passe toujours : sudo -i lui ouvre un shell quel que soit le sien. */
        if ($value !== 'root' && preg_match('#/(nologin|false)$#', (string) $account['shell'])) {
            throw new Exception(__('Ce compte n\'a pas de shell de connexion :', __FILE__) . ' ' . $value
                              . ' (' . $account['shell'] . ')');
        }
        return $value;
    }

    public static function preConfig_idle_timeout($_value) {
        $value = trim((string) $_value);
        if (!ctype_digit($value) || (int) $value > self::MAX_IDLE_MINUTES) {
            throw new Exception(__('Fermeture après inactivité : entrez un nombre de minutes entre 0 et', __FILE__) . ' ' . self::MAX_IDLE_MINUTES . '.');
        }
        return (string) (int) $value;
    }

    public static function preConfig_max_sessions($_value) {
        $value = trim((string) $_value);
        if (!ctype_digit($value) || (int) $value < 1 || (int) $value > self::MAX_SESSIONS) {
            throw new Exception(__('Sessions simultanées : entrez un nombre entre 1 et', __FILE__) . ' ' . self::MAX_SESSIONS . '.');
        }
        return (string) (int) $value;
    }


    /* ========================================================== DÉMON */

    private static function getPidFile() {
        return jeedom::getTmpFolder(__CLASS__) . '/deamon.pid';
    }

    private static function getDaemonPath() {
        return realpath(__DIR__ . '/../../resources/jeetermd/jeetermd.py');
    }

    /* Journal du démon, en chemin normalisé : systemd refuse un chemin qui
     * contient « .. ». */
    private static function getDaemonLogFile() {
        $dir = realpath(dirname(log::getPathToLog(__CLASS__ . 'd')));
        return (($dir === false) ? dirname(log::getPathToLog(__CLASS__ . 'd')) : $dir) . '/' . __CLASS__ . 'd';
    }

    /* Compte sous lequel tourne PHP, donc le démon (www-data d'ordinaire). */
    public static function getDaemonUser() {
        if (function_exists('posix_geteuid') && function_exists('posix_getpwuid')) {
            $account = posix_getpwuid(posix_geteuid());
            if (is_array($account) && isset($account['name']) && $account['name'] !== '') {
                return $account['name'];
            }
        }
        return 'www-data';
    }

    private static function getDaemonGroup() {
        if (function_exists('posix_getegid') && function_exists('posix_getgrgid')) {
            $group = posix_getgrgid(posix_getegid());
            if (is_array($group) && isset($group['name']) && $group['name'] !== '') {
                return $group['name'];
            }
        }
        return 'www-data';
    }

    /* systemd est le gestionnaire de services (absent sous Docker), et
     * www-data peut l'utiliser par sudo. */
    private static function canUseSystemd() {
        return is_dir('/run/systemd/system') && jeedom::isCapable('sudo') && self::commandExists('systemd-run');
    }

    /*
     * Un PID ne suffit pas : après un plantage, le numéro peut avoir été repris
     * par un autre processus, et le démon passerait pour vivant à jamais. On
     * vérifie donc aussi la ligne de commande quand /proc la donne. Une ligne
     * vide est celle d'un processus zombie (ou d'un fil du noyau) : il ne
     * tourne plus.
     */
    public static function isDaemonPid($_pid) {
        $pid = (int) $_pid;
        if ($pid <= 1 || @posix_getsid($pid) === false) {
            return false;
        }
        $cmdline = @file_get_contents('/proc/' . $pid . '/cmdline');
        if ($cmdline === false) {
            return true;
        }
        return strpos($cmdline, 'jeetermd.py') !== false;
    }

    private static function readPidFile() {
        $pid_file = self::getPidFile();
        if (!file_exists($pid_file)) {
            return 0;
        }
        $pid = trim((string) @file_get_contents($pid_file));
        return ctype_digit($pid) ? (int) $pid : -1;
    }

    /*
     * L'état du processus se détermine indépendamment de « launchable » : si un
     * réglage invalide rendait aussi l'état « nok », le coeur croirait le démon
     * arrêté et ne l'arrêterait jamais. La vérification des dépendances, elle,
     * est ajoutée par le coeur (plugin::deamon_info).
     */
    public static function deamon_info() {
        $return = array('log' => __CLASS__ . 'd', 'state' => 'nok', 'launchable' => 'ok');

        $pid = self::readPidFile();
        if ($pid > 0 && self::isDaemonPid($pid)) {
            $return['state'] = 'ok';
        } elseif ($pid != 0) {
            /* Un fichier de PID orphelin empêcherait à jamais le chien de garde
             * de relancer le démon : on le retire dès qu'il ment. */
            @unlink(self::getPidFile());
        }

        if (!self::isValidShellUser(self::getShellUser())) {
            $return['launchable'] = 'nok';
            $return['launchable_message'] = __('Le compte du shell n\'est pas un nom de compte valide.', __FILE__);
        }
        return $return;
    }

    public static function deamon_start() {
        self::deamon_stop();

        $info = self::deamon_info();
        if ($info['launchable'] != 'ok') {
            throw new Exception(__('Le démon ne peut pas être lancé :', __FILE__) . ' '
                              . $info['launchable_message']);
        }

        $settings = self::getDaemonSettings();
        $daemon = self::getDaemonPath();
        if ($daemon === false) {
            throw new Exception(__('Programme du démon introuvable :', __FILE__) . ' resources/jeetermd/jeetermd.py');
        }

        /* Python de la machine et non system::getCmdPython3() : à partir de
         * Debian 12, celui-ci désigne un environnement virtuel propre au plugin,
         * inutile ici puisque le démon n'emploie que la bibliothèque standard.
         * Chemin absolu : systemd-run ne cherche pas dans le PATH d'Apache. */
        exec('command -v python3 2>/dev/null', $found, $code);
        $python = ($code === 0 && isset($found[0]) && $found[0] !== '') ? $found[0] : 'python3';

        /*
         * La clé API ne passe jamais sur une ligne de commande : ps est lisible
         * par n'importe quel utilisateur local. Elle est confiée à un fichier
         * 0600 (tempnam le crée ainsi) que le démon lit puis supprime ;
         * systemd-run ne transmettrait pas l'entrée standard.
         */
        $keyFile = @tempnam(jeedom::getTmpFolder(__CLASS__), 'apikey');
        if ($keyFile === false || @file_put_contents($keyFile, self::getApiKey() . "\n") === false) {
            throw new Exception(__('Impossible d\'écrire le fichier de clé du démon dans', __FILE__) . ' ' . jeedom::getTmpFolder(__CLASS__));
        }
        @chmod($keyFile, 0600);

        $args  = ' ' . escapeshellarg($daemon);
        $args .= ' --port '         . escapeshellarg($settings['port']);
        $args .= ' --pid '          . escapeshellarg(self::getPidFile());
        $args .= ' --loglevel '     . escapeshellarg(log::convertLogLevel(log::getLogLevel(__CLASS__)));
        $args .= ' --shell-user '   . escapeshellarg($settings['shell_user']);
        $args .= ' --tmux '         . escapeshellarg($settings['tmux']);
        $args .= ' --goodies '      . escapeshellarg($settings['goodies']);
        $args .= ' --idle-timeout ' . escapeshellarg($settings['idle_timeout']);
        $args .= ' --max-sessions ' . escapeshellarg($settings['max_sessions']);
        $args .= ' --apikey-file '  . escapeshellarg($keyFile);

        /* Le journal doit exister avant le lancement : systemd l'ouvrirait en
         * root et le créerait à son nom, et Jeedom ne pourrait plus le vider. */
        $logFile = self::getDaemonLogFile();
        if (!file_exists($logFile)) {
            @touch($logFile);
        }

        log::add(__CLASS__, 'info', __('Lancement du démon', __FILE__));
        $launched = false;
        if (self::canUseSystemd()) {
            /*
             * Lancé depuis l'interface, le démon serait un enfant d'Apache et
             * vivrait dans son groupe de contrôle (apache2.service, KillMode=mixed) :
             * un redémarrage ou une mise à jour d'Apache tuerait au signal 9 le
             * démon, chaque shell ouvert, la session tmux et la commande en cours,
             * fût-ce un apt-get au milieu d'une mise à jour. systemd-run lui donne
             * une unité à lui.
             *
             * KillMode=process : à l'arrêt de l'unité, seul le démon reçoit le
             * signal ; il ferme lui-même ses shells, et un serveur tmux lancé en
             * mode persistant lui survit. Comme ce serveur garde l'unité en vie
             * après la fin du démon, le nom de l'unité change à chaque lancement.
             */
            $unit = 'jeetermd-' . date('YmdHis');
            $systemd  = system::getCmdSudo() . 'systemd-run --quiet --collect';
            $systemd .= ' --unit=' . escapeshellarg($unit);
            $systemd .= ' --uid=' . escapeshellarg(self::getDaemonUser());
            $systemd .= ' --gid=' . escapeshellarg(self::getDaemonGroup());
            $systemd .= ' --property=KillMode=process';
            $systemd .= ' --property=' . escapeshellarg('StandardOutput=append:' . $logFile);
            $systemd .= ' --property=' . escapeshellarg('StandardError=append:' . $logFile);
            $systemd .= ' -- ' . escapeshellarg($python) . $args . ' 2>&1';
            $output = array();
            exec($systemd, $output, $code);
            if ($code === 0) {
                $launched = true;
                log::add(__CLASS__, 'debug', __('Démon lancé dans l\'unité systemd', __FILE__) . ' ' . $unit);
            } else {
                log::add(__CLASS__, 'warning', __('systemd-run a échoué, lancement direct du démon :', __FILE__)
                       . ' ' . implode(' ', $output));
            }
        }
        if (!$launched) {
            /* Sans systemd (Docker) ou sans sudo : le démon est détaché par le
             * shell, et reste alors lié au service qui l'a lancé. */
            exec(escapeshellarg($python) . $args . ' >> ' . escapeshellarg($logFile) . ' 2>&1 < /dev/null &');
        }

        /* Réglages effectivement appliqués : ils permettent de savoir, après un
         * enregistrement de la configuration, si un redémarrage est nécessaire,
         * et sur quel port chercher un ancien démon. */
        config::save('daemon_settings', $settings, __CLASS__);

        /* Le fichier de PID est écrit avant l'ouverture du port : un démon qui
         * échoue ensuite (port déjà pris) passerait pour démarré. On attend donc
         * qu'il réponde lui-même, avec la clé API. */
        for ($i = 1; $i <= 30; $i++) {
            if (self::deamon_info()['state'] == 'ok' && self::daemonRequest('/status', array(), 1)['status'] == 200) {
                @unlink($keyFile);
                message::removeAll(__CLASS__, 'unableStartDeamon');
                message::removeAll(__CLASS__, 'settingsChanged');
                return true;
            }
            sleep(1);
        }
        /* Démon mort avant d'avoir lu sa clé : le fichier ne doit pas rester. */
        @unlink($keyFile);

        /* log::add ne pose un message que si le réglage global addMessageForErrorLog
         * est actif, et il ne l'est pas par défaut : le message est explicite. */
        log::add(__CLASS__, 'error', __('Le démon n\'a pas démarré. Consultez le journal', __FILE__)
               . ' ' . __CLASS__ . 'd.');
        message::add(__CLASS__, __('Le démon n\'a pas démarré. Consultez le journal', __FILE__)
                   . ' ' . __CLASS__ . 'd.', '', 'unableStartDeamon');
        return false;
    }

    /*
     * Arrête le démon, et tout démon JeeTerm resté en arrière (lancé à la main,
     * fichier de PID perdu, ou encore sur l'ancien port après un changement).
     *
     * Aucune recherche par motif (system::kill('jeetermd.py')) ni par port
     * (system::fuserk) : la première tuerait au signal 9 tout processus dont la
     * ligne de commande contient ce nom, y compris un « vim jeetermd.py » lancé
     * dans le terminal ; la seconde tuerait n'importe quel service installé sur
     * le port, justement celui que l'utilisateur voulait quitter en changeant
     * de port. Seuls les processus Python qui exécutent un jeetermd.py, le
     * nôtre ou un autre écoutant sur l'un de nos ports, sont visés.
     */
    public static function deamon_stop() {
        $pids = self::findDaemonPids();
        $pid = self::readPidFile();
        if ($pid > 0 && self::isDaemonPid($pid)) {
            $pids[] = $pid;
        }
        $pids = array_unique($pids);

        /* SIGTERM d'abord : le démon ferme ses shells (jusqu'à une dizaine de
         * secondes) ; le signal 9 ne lui en laisserait pas le temps. */
        foreach ($pids as $target) {
            self::signal($target, 15);
        }
        for ($i = 0; $i < 100; $i++) {
            $alive = array_filter($pids, function ($p) {
                return jeeterm::isDaemonPid($p);
            });
            if (count($alive) == 0) {
                break;
            }
            usleep(100000);
        }
        foreach ($pids as $target) {
            if (self::isDaemonPid($target)) {
                log::add(__CLASS__, 'warning', __('Le démon ne s\'est pas arrêté de lui-même, arrêt forcé du processus', __FILE__) . ' ' . $target);
                self::signal($target, 9);
            }
        }
        @unlink(self::getPidFile());
        return true;
    }

    private static function signal($_pid, $_signal) {
        if (@posix_kill($_pid, $_signal)) {
            return;
        }
        /* Démon lancé par un autre compte (root, à la main) : www-data a sudo. */
        exec(system::getCmdSudo() . 'kill -' . (int) $_signal . ' ' . (int) $_pid . ' > /dev/null 2>&1');
    }

    /* Processus Python exécutant un jeetermd.py : le nôtre (même chemin), ou un
     * autre s'il écoute sur le port configuré ou sur celui du dernier lancement. */
    private static function findDaemonPids() {
        $ours = self::getDaemonPath();
        $ports = array(self::getPort());
        $running = self::getRunningSettings();
        if ($running !== null && isset($running['port'])) {
            $ports[] = (int) $running['port'];
        }

        $found = array();
        $dirs = glob('/proc/[0-9]*', GLOB_ONLYDIR);
        foreach (($dirs === false) ? array() : $dirs as $dir) {
            $pid = (int) basename($dir);
            if ($pid <= 1 || $pid == getmypid()) {
                continue;
            }
            $cmdline = @file_get_contents($dir . '/cmdline');
            if ($cmdline === false || strpos($cmdline, 'jeetermd.py') === false) {
                continue;
            }
            $argv = explode("\0", rtrim($cmdline, "\0"));
            if (strpos(basename($argv[0]), 'python') !== 0) {
                continue;
            }
            $script = null;
            for ($i = 1; $i < count($argv); $i++) {
                if (in_array($argv[$i], array('-c', '-m'))) {
                    break;
                }
                if (in_array($argv[$i], array('-W', '-X', '-Q'))) {
                    $i++;
                    continue;
                }
                if ($argv[$i] !== '' && $argv[$i][0] == '-') {
                    continue;
                }
                $script = $argv[$i];
                $args = array_slice($argv, $i + 1);
                break;
            }
            if ($script === null || basename($script) !== 'jeetermd.py') {
                continue;
            }
            $path = ($script[0] == '/') ? realpath($script) : realpath($dir . '/cwd/' . $script);
            $port = self::DEFAULT_PORT;
            foreach ($args as $j => $arg) {
                if ($arg == '--port' && isset($args[$j + 1])) {
                    $port = (int) $args[$j + 1];
                } elseif (strpos($arg, '--port=') === 0) {
                    $port = (int) substr($arg, 7);
                }
            }
            if (($ours !== false && $path === $ours) || in_array($port, $ports)) {
                $found[] = $pid;
            }
        }
        return $found;
    }


    /* ========================================================== DÉPENDANCES */

    /*
     * Les dépendances ne sont pas seulement des paquets : le tunnel WebSocket
     * passe par Apache, et c'est la conf Apache qui est le plus souvent en
     * cause. Elle est donc vérifiée ici, port compris : après un changement de
     * port, la conf pointe encore sur l'ancien et le terminal ne pourrait pas
     * s'ouvrir.
     *
     * Le coeur passe l'état à « in_progress » tant que le fichier de progression
     * existe (plugin::dependancy_info) : le script d'installation le supprime en
     * sortant, qu'il réussisse ou non. Le coeur garde aussi en cache un état
     * « ok » : cette méthode n'est plus appelée ensuite, jusqu'à ce que ce cache
     * soit oublié (mise à jour du plugin, changement de réglage).
     */
    public static function dependancy_info() {
        $return = array(
            'log'           => log::getPathToLog(__CLASS__ . '_update'),
            'progress_file' => jeedom::getTmpFolder(__CLASS__) . '/dependance',
            'state'         => 'ok',
        );
        foreach (self::dependancyProblems() as $problem) {
            log::add(__CLASS__, 'debug', __('Dépendance manquante :', __FILE__) . ' ' . $problem);
            $return['state'] = 'nok';
        }
        return $return;
    }

    /* Liste lisible de ce qui manque, vide si tout est en place. */
    public static function dependancyProblems() {
        $problems = array();
        if (!self::commandExists('python3')) {
            $problems[] = 'python3';
        }
        if (!file_exists(self::APACHE_PROXY) || !file_exists(self::APACHE_WSTUNNEL)) {
            $problems[] = __('modules Apache proxy et proxy_wstunnel', __FILE__);
        }
        $conf = @file_get_contents(self::APACHE_CONF);
        if ($conf === false) {
            $problems[] = __('configuration Apache', __FILE__) . ' ' . self::APACHE_CONF;
        } elseif (strpos($conf, self::APACHE_CONF_MARKER) === false) {
            /* Conf écrite par une version précédente du plugin : l'installation
             * des dépendances la réécrit. */
            $problems[] = __('configuration Apache d\'une version précédente du plugin', __FILE__);
        } elseif (strpos($conf, '"ws://127.0.0.1:' . self::getPort() . '/$1"') === false) {
            $problems[] = __('configuration Apache sur un autre port que', __FILE__) . ' ' . self::getPort();
        }
        if (config::byKey('tmux', __CLASS__, 0) == 1 && !self::commandExists('tmux')) {
            $problems[] = 'tmux';
        }
        return $problems;
    }

    private static function commandExists($_command) {
        exec('command -v ' . escapeshellarg($_command) . ' 2>/dev/null', $output, $code);
        return $code === 0;
    }

    /*
     * Le coeur coupe le script au premier espace et vérifie que le premier
     * morceau existe (plugin::dependancy_install), puis le lance en root, sans
     * guillemets : « /bin/bash <script> <arguments> >> <log> 2>&1 ». Aucun
     * argument ne doit donc contenir d'espace ni de caractère spécial pour le
     * shell : le fichier de progression est sous le dossier temporaire de
     * Jeedom, le port est un entier, le compte est un nom de compte Unix.
     */
    public static function dependancy_install() {
        log::remove(__CLASS__ . '_update');
        $user = self::getDaemonUser();
        if (!preg_match('/^[a-z_][a-z0-9_.-]{0,31}$/i', $user)) {
            $user = 'www-data';
        }
        return array(
            'script' => __DIR__ . '/../../resources/install_apt.sh '
                      . jeedom::getTmpFolder(__CLASS__) . '/dependance '
                      . self::getPort() . ' '
                      . $user,
            'log'    => log::getPathToLog(__CLASS__ . '_update'),
        );
    }

    /*
     * Retire la conf Apache du plugin. Appelée par jeeterm_remove(), donc à la
     * désactivation comme à la désinstallation : le coeur ne distingue pas les
     * deux (update::deleteObjet passe par plugin::setIsEnable(0)). À la
     * réactivation, le coeur recalcule les dépendances (dependancy_info(true))
     * et, si leur gestion automatique est active, réinstalle la conf.
     */
    public static function removeApacheConf() {
        if (!file_exists(self::APACHE_CONF_AVAILABLE) && !file_exists(self::APACHE_CONF)) {
            return;
        }
        if (!jeedom::isCapable('sudo')) {
            log::add(__CLASS__, 'warning', __('Sans sudo, la configuration Apache de JeeTerm reste en place :', __FILE__) . ' ' . self::APACHE_CONF_AVAILABLE);
            return;
        }
        $sudo = system::getCmdSudo();
        exec($sudo . 'a2disconf -q jeeterm > /dev/null 2>&1');
        exec($sudo . 'rm -f ' . escapeshellarg(self::APACHE_CONF_AVAILABLE) . ' ' . escapeshellarg(self::APACHE_CONF));
        cache::byKey('dependancy' . __CLASS__)->remove();
        /* Retirer une conf ne devrait rien casser ; on vérifie tout de même,
         * pour ne jamais recharger Apache sur une configuration refusée. */
        exec($sudo . 'apache2ctl configtest > /dev/null 2>&1', $output, $code);
        if ($code !== 0) {
            log::add(__CLASS__, 'warning', __('Configuration Apache retirée, mais apache2ctl configtest échoue : Apache n\'a pas été rechargé.', __FILE__));
            return;
        }
        $reload = '(' . (is_dir('/run/systemd/system') ? $sudo . 'systemctl reload apache2 || ' : '')
                . $sudo . 'service apache2 reload || ' . $sudo . 'apache2ctl -k graceful) > /dev/null 2>&1';
        exec($reload);
        log::add(__CLASS__, 'info', __('Configuration Apache de JeeTerm retirée', __FILE__));
    }


    /* ========================================================== SESSIONS */

    /*
     * Requête HTTP simple au démon, par la boucle locale et avec la clé API.
     * Renvoie le code HTTP (0 si le démon ne répond pas) et le corps.
     */
    private static function daemonRequest($_path, $_headers = array(), $_timeout = 3) {
        $headers = array_merge(array('X-Jeeterm-Apikey: ' . self::getApiKey()), $_headers, array('Connection: close'));
        $context = stream_context_create(array('http' => array(
            'method'          => 'GET',
            'header'          => implode("\r\n", $headers),
            'timeout'         => $_timeout,
            /* Sans cette option, un code 4xx ne rend que « false » : on ne
             * saurait pas distinguer une limite atteinte d'un démon absent. */
            'ignore_errors'   => true,
            'follow_location' => 0,
        )));

        $body = @file_get_contents('http://127.0.0.1:' . self::getPort() . $_path, false, $context);
        /* $http_response_header est déprécié à partir de PHP 8.5. */
        $response = function_exists('http_get_last_response_headers')
                  ? http_get_last_response_headers()
                  : (isset($http_response_header) ? $http_response_header : null);
        $status = 0;
        if (is_array($response)) {
            foreach ($response as $line) {
                if (preg_match('#^HTTP/\S+\s+(\d{3})#', $line, $m)) {
                    $status = (int) $m[1];
                }
            }
        }
        return array('status' => $status, 'body' => ($body === false) ? '' : $body);
    }

    /*
     * Ouvre une session : un jeton aléatoire est confié au démon, qui l'accepte
     * une seule fois dans les 30 secondes pour ouvrir le WebSocket. Le jeton ne
     * transite qu'entre PHP et le démon par la boucle locale, puis vers le
     * navigateur de l'administrateur qui l'a demandé.
     */
    public static function createSession($_user, $_ip) {
        if (config::byKey('lan_only', __CLASS__, 0) == 1 && !self::isLanRequest()) {
            throw new Exception(__('Le terminal n\'est accessible que depuis le réseau local.', __FILE__));
        }
        if (self::deamon_info()['state'] != 'ok') {
            /* La raison que donne le coeur (dépendances absentes ou en cours
             * d'installation, gestion automatique coupée) dit quoi faire. */
            $info = plugin::byId(__CLASS__)->deamon_info();
            $reason = (isset($info['launchable_message']) && $info['launchable_message'] != '')
                    ? ' ' . $info['launchable_message'] . '.' : '';
            throw new Exception(__('Le démon JeeTerm n\'est pas démarré.', __FILE__) . $reason);
        }

        $token = bin2hex(random_bytes(32));
        $answer = self::daemonRequest('/register', array(
            'X-Jeeterm-Token: ' . $token,
            'X-Jeeterm-User: ' . self::headerValue($_user),
            'X-Jeeterm-Ip: ' . self::headerValue($_ip),
        ));

        switch ($answer['status']) {
            case 200:
                $body = json_decode($answer['body'], true);
                if (!is_array($body) || empty($body['ok'])) {
                    throw new Exception(__('Réponse inattendue du démon JeeTerm.', __FILE__));
                }
                break;
            case 0:
                throw new Exception(__('Le démon JeeTerm ne répond pas. Vérifiez qu\'il est démarré, et consultez le journal jeetermd.', __FILE__));
            case 401:
                throw new Exception(__('Le démon JeeTerm a refusé la clé API. Redémarrez le démon.', __FILE__));
            case 403:
                throw new Exception(__('Le démon JeeTerm a refusé la provenance de la demande.', __FILE__));
            case 429:
                throw new Exception(__('Nombre maximal de sessions atteint. Fermez un terminal ouvert, ou augmentez la limite dans la configuration du plugin.', __FILE__));
            case 400:
                throw new Exception(__('Le démon JeeTerm a refusé le jeton de session.', __FILE__));
            case 503:
                /* Le démon est en train de s'arrêter (redémarrage après un
                 * changement de réglage, par exemple). */
                throw new Exception(__('Le démon JeeTerm redémarre, réessayez dans quelques secondes.', __FILE__));
            default:
                throw new Exception(__('Réponse inattendue du démon JeeTerm : HTTP', __FILE__) . ' ' . $answer['status']);
        }

        return array('token' => $token, 'path' => self::WS_PATH);
    }

    /*
     * Sonde le tunnel comme le ferait le navigateur, mais depuis la machine :
     * une demande d'upgrade WebSocket vers Apache, avec un jeton bien formé
     * que le démon ne connaît pas. Le démon répond alors lui-même 403 et un
     * corps JSON ; Apache, s'il ne relaie pas, répond par sa page 404 en HTML,
     * et par 502/503 s'il relaie vers un port où rien n'écoute.
     */
    public static function probeTunnel() {
        $base = network::getNetworkAccess('internal', 'http:127.0.0.1:port:comp');
        $url = parse_url($base);
        $host = (is_array($url) && isset($url['host'])) ? $url['host'] : '127.0.0.1';
        $port = (is_array($url) && isset($url['port'])) ? (int) $url['port'] : 80;
        $path = rtrim((is_array($url) && isset($url['path'])) ? $url['path'] : '', '/') . '/' . self::WS_PATH;
        $return = array('url' => 'http://' . $host . ':' . $port . $path, 'status' => 0, 'daemon' => false);

        $socket = @fsockopen($host, $port, $errno, $errstr, 2);
        if ($socket === false) {
            $return['error'] = $errstr;
            return $return;
        }
        stream_set_timeout($socket, 3);
        fwrite($socket, 'GET ' . $path . '?t=' . str_repeat('0', 64) . " HTTP/1.1\r\n"
            . 'Host: ' . $host . "\r\n"
            . "Connection: Upgrade\r\n"
            . "Upgrade: websocket\r\n"
            . "Sec-WebSocket-Version: 13\r\n"
            . 'Sec-WebSocket-Key: ' . base64_encode(random_bytes(16)) . "\r\n"
            . "User-Agent: jeeterm-diagnose\r\n\r\n");
        $response = '';
        while (!feof($socket) && strlen($response) < 8192) {
            $chunk = fread($socket, 2048);
            if ($chunk === false || $chunk === '') {
                $meta = stream_get_meta_data($socket);
                if (!empty($meta['timed_out'])) {
                    break;
                }
                if ($chunk === false) {
                    break;
                }
            }
            $response .= $chunk;
            /* Apache garde la connexion ouverte après sa page 404 : on s'arrête
             * dès que le corps annoncé est arrivé. */
            $end = strpos($response, "\r\n\r\n");
            if ($end !== false && preg_match('/^Content-Length:\s*(\d+)/mi', substr($response, 0, $end), $cl)
                && strlen($response) - $end - 4 >= (int) $cl[1]) {
                break;
            }
        }
        fclose($socket);

        if (preg_match('#^HTTP/\S+\s+(\d{3})#', $response, $m)) {
            $return['status'] = (int) $m[1];
        }
        $parts = explode("\r\n\r\n", $response, 2);
        $body = json_decode(isset($parts[1]) ? trim($parts[1]) : '', true);
        /* Réponse du démon lui-même : {"ok": false} */
        $return['daemon'] = is_array($body) && array_key_exists('ok', $body);
        return $return;
    }

    /*
     * Diagnostic d'un WebSocket qui échoue avant de s'ouvrir, appelé par la
     * page. Renvoie :
     *   code    : deps_in_progress | deps | daemon_down | tunnel_ok | no_tunnel
     *             | tunnel_down | probe_failed
     *   message : phrase à afficher, qui dit quoi faire
     *   daemon, dependancy, problems, probe : détails bruts
     */
    public static function diagnose() {
        $plugin = plugin::byId(__CLASS__);
        $daemon = $plugin->deamon_info();
        $dependancy = $plugin->dependancy_info(true);
        $problems = self::dependancyProblems();
        $probe = self::probeTunnel();
        $return = array(
            'daemon'     => $daemon['state'],
            'dependancy' => $dependancy['state'],
            'problems'   => $problems,
            'probe'      => $probe,
        );

        if ($dependancy['state'] == 'in_progress') {
            $return['code'] = 'deps_in_progress';
            $return['message'] = __('Les dépendances de JeeTerm sont en cours d\'installation. Réessayez dans une minute.', __FILE__);
        } elseif (count($problems) > 0) {
            $return['code'] = 'deps';
            $return['message'] = __('Dépendances incomplètes :', __FILE__) . ' ' . implode(', ', $problems) . '. '
                               . __('Relancez l\'installation des dépendances depuis la page de configuration du plugin.', __FILE__);
        } elseif ($daemon['state'] != 'ok') {
            $return['code'] = 'daemon_down';
            $return['message'] = __('Le démon JeeTerm n\'est pas démarré.', __FILE__)
                               . (($daemon['launchable_message'] != '') ? ' ' . $daemon['launchable_message'] . '.' : '');
        } elseif ($probe['status'] == 403 && $probe['daemon']) {
            $return['code'] = 'tunnel_ok';
            $return['message'] = __('Le relais Apache et le démon fonctionnent : la connexion est bloquée avant Apache. Un proxy inverse ou l\'accès par le DNS Jeedom ne laisse sans doute pas passer les WebSocket ; sinon, le jeton a expiré : reconnectez-vous.', __FILE__);
        } elseif ($probe['status'] == 404 || ($probe['status'] > 0 && !$probe['daemon'] && $probe['status'] < 500)) {
            $return['code'] = 'no_tunnel';
            $return['message'] = __('Apache ne relaie pas le terminal : sa configuration JeeTerm n\'est pas active. Relancez l\'installation des dépendances.', __FILE__);
        } elseif ($probe['status'] >= 500) {
            $return['code'] = 'tunnel_down';
            $return['message'] = __('Apache relaie le terminal, mais le démon ne lui répond pas. Redémarrez le démon ; si cela persiste, relancez l\'installation des dépendances.', __FILE__);
        } else {
            $return['code'] = 'probe_failed';
            $return['message'] = __('Impossible de joindre Apache en local pour tester le relais :', __FILE__) . ' ' . $probe['url'];
        }
        return $return;
    }

    /* Page Santé du coeur (desktop/php/health.php) : une ligne par test. */
    public static function health() {
        $return = array();
        try {
            $status = self::daemonRequest('/status', array(), 2);
            $body = json_decode($status['body'], true);
            $ok = ($status['status'] == 200 && is_array($body));
            $return[] = array(
                'test'   => __('Démon', __FILE__),
                'result' => $ok ? __('OK', __FILE__) . ' (' . (isset($body['sessions']) ? (int) $body['sessions'] : 0) . ' ' . __('session(s) ouverte(s)', __FILE__) . ')'
                                : __('Ne répond pas', __FILE__),
                'advice' => $ok ? '' : __('Démarrez le démon depuis la page de configuration du plugin.', __FILE__),
                'state'  => $ok,
            );
            $probe = self::probeTunnel();
            $ok = ($probe['status'] == 403 && $probe['daemon']);
            $return[] = array(
                'test'   => __('Relais WebSocket Apache', __FILE__),
                'result' => $ok ? __('OK', __FILE__) : __('En échec', __FILE__) . ' (HTTP ' . $probe['status'] . ')',
                'advice' => $ok ? '' : __('Relancez l\'installation des dépendances, puis redémarrez le démon.', __FILE__),
                'state'  => $ok,
            );
        } catch (Throwable $e) {
            /* health.php ne rattrape que les Exception : une Error ici ferait
             * tomber toute la page Santé. */
            $return[] = array('test' => 'JeeTerm', 'result' => $e->getMessage(), 'advice' => '', 'state' => false);
        }
        return $return;
    }

    /* Un en-tête HTTP ne peut porter ni retour chariot ni caractère de
     * contrôle : un login ou une IP qui en contiendrait injecterait des lignes.
     * La coupe respecte l'UTF-8, pour ne pas laisser un caractère à moitié. */
    private static function headerValue($_value) {
        $value = preg_replace('/[\x00-\x1F\x7F]/', '', (string) $_value);
        return function_exists('mb_strcut') ? mb_strcut($value, 0, 128, 'UTF-8') : substr($value, 0, 128);
    }

    /*
     * network::getUserLocation() se fonde sur l'adresse déclarée par les
     * en-têtes X-Forwarded-For et apparentés, qu'un client peut forger. On exige
     * en plus que l'adresse réelle de la connexion soit locale, selon la même
     * règle que le coeur (plages de network::localip, sinon le /24 de Jeedom).
     *
     * Ce n'est pas une frontière de sécurité absolue : si Apache réécrit lui-même
     * REMOTE_ADDR (mod_remoteip sans proxy de confiance déclaré), les deux
     * adresses viennent du même en-tête. L'authentification administrateur et le
     * jeton restent la vraie protection.
     *
     * Les règles du coeur ne connaissent que l'IPv4 : un navigateur connecté en
     * IPv6 n'est local que si son adresse exacte figure dans network::localip.
     */
    public static function isLanRequest() {
        if (network::getUserLocation() != 'internal') {
            return false;
        }
        $peer = isset($_SERVER['REMOTE_ADDR']) ? trim($_SERVER['REMOTE_ADDR']) : '';
        /* Adresse IPv4 vue au travers d'une socket IPv6 : ::ffff:192.168.1.10 */
        if (stripos($peer, '::ffff:') === 0 && filter_var(substr($peer, 7), FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
            $peer = substr($peer, 7);
        }
        if (!filter_var($peer, FILTER_VALIDATE_IP)) {
            return false;
        }
        if (config::byKey('network::localip') != '') {
            foreach (explode(';', config::byKey('network::localip')) as $localIp) {
                if (trim($localIp) != '' && netMatch(trim($localIp), $peer)) {
                    return true;
                }
            }
        }
        $jeedomIp = network::getNetworkAccess('internal', 'ip', '', false);
        $parts = explode('.', $jeedomIp);
        if (!filter_var($jeedomIp, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) || count($parts) != 4
            || !filter_var($peer, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
            return false;
        }
        return netMatch($parts[0] . '.' . $parts[1] . '.' . $parts[2] . '.*', $peer);
    }


    /* ========================================================== CONFIGURATION */

    /*
     * Le coeur appelle <plugin>::postConfig_<clé>() après chaque config::save
     * (config.class.php), et la page de configuration enregistre toutes ses clés
     * dans une seule requête, modifiées ou non. On ne redémarre donc le démon
     * qu'une fois par requête, et seulement si les réglages diffèrent de ceux
     * avec lesquels il tourne : un redémarrage ferme tous les terminaux ouverts.
     *
     * lan_only n'a pas de hook : il est relu à chaque demande de session.
     */
    public static function postConfig_port($_value)         { self::scheduleSettingsCheck(); }
    public static function postConfig_shell_user($_value)   { self::scheduleSettingsCheck(); }
    public static function postConfig_tmux($_value)         { self::scheduleSettingsCheck(); }
    public static function postConfig_goodies($_value)      { self::scheduleSettingsCheck(); }
    public static function postConfig_idle_timeout($_value) { self::scheduleSettingsCheck(); }
    public static function postConfig_max_sessions($_value) { self::scheduleSettingsCheck(); }

    private static function scheduleSettingsCheck() {
        if (self::$_restartScheduled) {
            return;
        }
        self::$_restartScheduled = true;
        /*
         * En fin de requête, une fois toutes les clés enregistrées, et dans un
         * processus détaché (plugin::launch) : un redémarrage peut prendre une
         * trentaine de secondes, pendant lesquelles la page de configuration
         * attendrait la réponse de son enregistrement.
         */
        register_shutdown_function(function () {
            try {
                plugin::byId('jeeterm')->launch('applySettings');
            } catch (Throwable $e) {
                log::add('jeeterm', 'error', __('Application des réglages :', __FILE__) . ' ' . $e->getMessage());
            }
        });
    }

    public static function applySettings() {
        $running = self::getRunningSettings();
        $wanted = self::getDaemonSettings();
        if ($running !== null && $running == $wanted) {
            return;
        }

        /* Le port ou tmux peuvent rendre les dépendances caduques : le coeur garde
         * en cache un état « ok », qu'il faut oublier pour qu'il soit recalculé.
         * Si les gestions automatiques du démon et des dépendances sont actives,
         * le chien de garde du coeur relancera alors lui-même l'installation. */
        cache::byKey('dependancy' . __CLASS__)->remove();

        if (self::deamon_info()['state'] != 'ok') {
            return;
        }
        $plugin = plugin::byId(__CLASS__);
        $info = $plugin->deamon_info();
        if ($info['launchable'] != 'ok') {
            /* Typiquement un nouveau port que la conf Apache ignore encore : le
             * démon resterait sur l'ancien, où plus aucune session n'irait. */
            $problems = self::dependancyProblems();
            $reason = (count($problems) > 0)
                    ? __('dépendances incomplètes', __FILE__) . ' (' . implode(', ', $problems) . ')'
                    : $info['launchable_message'];
            self::deamon_stop();
            message::add(__CLASS__, __('Les réglages de JeeTerm ont changé et le démon a été arrêté :', __FILE__)
                       . ' ' . $reason . '. '
                       . __('Relancez l\'installation des dépendances si le port a changé.', __FILE__),
                         '', 'settingsChanged');
            return;
        }
        if ($info['auto'] == 1) {
            log::add(__CLASS__, 'info', __('Réglages modifiés : redémarrage du démon', __FILE__));
            self::deamon_start();
            return;
        }
        message::add(__CLASS__, __('Les réglages de JeeTerm ont changé : redémarrez le démon pour les appliquer.', __FILE__),
                     '', 'settingsChanged');
    }
}

/*
 * La classe de commande est obligatoire, même vide : sans elle le coeur refuse
 * de charger les équipements du plugin.
 */
class jeetermCmd extends cmd {

    public function execute($_options = array()) {
        return true;
    }
}
