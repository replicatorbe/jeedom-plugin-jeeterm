#!/bin/bash
# Installation des dépendances de JeeTerm.
#
# Lancé en root par le coeur de Jeedom (plugin::dependancy_install) :
#   sudo /bin/bash install_apt.sh <fichier de progression> <port du démon> <compte du démon>
# La sortie va dans log/jeeterm_update. Le coeur passe les arguments sans
# guillemets : aucun ne contient d'espace.
#
# Le coeur considère l'installation « en cours » tant que le fichier de
# progression existe : il est supprimé en sortant, réussite ou échec, sans quoi
# le plugin resterait bloqué jusqu'au délai maximal.
#
# Ce que le script modifie sur la machine :
#   - paquets python3 et tmux (seulement s'ils manquent) ;
#   - dossier /var/lib/jeeterm/home, HOME du shell ouvert sous le compte du démon ;
#   - modules Apache proxy et proxy_wstunnel ;
#   - /etc/apache2/conf-available/jeeterm.conf, activée par a2enconf ;
#   - rechargement d'Apache (graceful : les requêtes en cours finissent).
#
# Relancer le script ne fait que réécrire la conf et recharger Apache.

PROGRESS_FILE="${1:-/tmp/jeedom/jeeterm/dependance}"
PORT="${2:-55080}"
DAEMON_USER="${3:-www-data}"
CONF_NAME="jeeterm"
CONF_FILE="/etc/apache2/conf-available/${CONF_NAME}.conf"
# Repère de version de la conf, vérifié par jeeterm::dependancyProblems()
# (APACHE_CONF_MARKER) : à changer ensemble.
CONF_MARKER="# jeeterm-conf v2"
HOME_DIR="/var/lib/jeeterm/home"

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export DEBIAN_FRONTEND=noninteractive
# Attendre (5 min au plus) qu'un autre apt, une mise à jour automatique par
# exemple, libère son verrou, plutôt que d'échouer aussitôt. Sans barre de
# progression, qui remplirait le journal.
APT_OPTS=(-o DPkg::Lock::Timeout=300 -o Dpkg::Use-Pty=0 -q)

progress() {
    echo "$1" > "${PROGRESS_FILE}"
}

step() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

fail() {
    step "ÉCHEC : $*"
    exit 1
}

cleanup() {
    rm -f "${PROGRESS_FILE}"
}
trap cleanup EXIT

mkdir -p "$(dirname "${PROGRESS_FILE}")"
progress 0
step "Début de l'installation des dépendances de JeeTerm (port ${PORT}, compte ${DAEMON_USER})"

if ! [[ "${PORT}" =~ ^[0-9]+$ ]] || [ "${PORT}" -lt 1024 ] || [ "${PORT}" -gt 65535 ]; then
    fail "port invalide : ${PORT}"
fi
if ! [[ "${DAEMON_USER}" =~ ^[a-zA-Z_][a-zA-Z0-9_.-]{0,31}$ ]] || ! id -u "${DAEMON_USER}" >/dev/null 2>&1; then
    fail "compte du démon inconnu : ${DAEMON_USER}"
fi

if [ ! -d /etc/apache2 ] || ! command -v a2enmod >/dev/null 2>&1 || ! command -v apache2ctl >/dev/null 2>&1; then
    fail "Apache introuvable (/etc/apache2, a2enmod, apache2ctl). JeeTerm a besoin de l'Apache de Jeedom, organisé à la manière de Debian, pour relayer le terminal."
fi

# Une conf Apache déjà cassée avant nous : ne rien toucher, et surtout ne pas
# recharger Apache, qui refuserait de repartir.
step "Vérification de la configuration Apache existante"
if ! apache2ctl configtest >/dev/null 2>&1; then
    apache2ctl configtest 2>&1
    fail "la configuration Apache est déjà en erreur avant toute modification. Corrigez-la d'abord."
fi
progress 10

# ---------------------------------------------------------------- paquets
MISSING=""
for pkg in python3 tmux; do
    if ! dpkg-query -W -f='${Status}' "${pkg}" 2>/dev/null | grep -q "install ok installed"; then
        MISSING="${MISSING} ${pkg}"
    fi
done
if [ -z "${MISSING}" ]; then
    step "python3 et tmux sont déjà installés"
elif ! command -v apt-get >/dev/null 2>&1; then
    fail "apt-get introuvable : installez vous-même${MISSING}"
else
    step "Mise à jour de la liste des paquets"
    apt-get "${APT_OPTS[@]}" update || step "Avertissement : apt-get update a échoué, on continue avec la liste existante"
    progress 30
    step "Installation de${MISSING}"
    # shellcheck disable=SC2086
    apt-get "${APT_OPTS[@]}" install -y ${MISSING} || fail "l'installation des paquets a échoué (${MISSING# })"
fi
progress 50

# ---------------------------------------------------------------- HOME du shell
# Le HOME de www-data (/var/www) appartient à root : bash n'y écrirait ni son
# historique, ni tmux sa socket. Le démon prend ce dossier s'il peut y écrire.
step "Dossier personnel du shell : ${HOME_DIR}"
DAEMON_GROUP="$(id -gn "${DAEMON_USER}")"
install -d -m 0755 -o root -g root "$(dirname "${HOME_DIR}")" || fail "impossible de créer $(dirname "${HOME_DIR}")"
install -d -m 0700 -o "${DAEMON_USER}" -g "${DAEMON_GROUP}" "${HOME_DIR}" || fail "impossible de créer ${HOME_DIR}"
# install -d ne corrige pas un dossier existant : propriétaire et droits sont
# réimposés.
chown "${DAEMON_USER}:${DAEMON_GROUP}" "${HOME_DIR}" && chmod 0700 "${HOME_DIR}"
progress 60

# ---------------------------------------------------------------- Apache
step "Activation des modules Apache proxy et proxy_wstunnel"
a2enmod -q proxy proxy_wstunnel || fail "impossible d'activer les modules proxy et proxy_wstunnel"
progress 70

BACKUP=""
if [ -f "${CONF_FILE}" ]; then
    BACKUP="$(mktemp)"
    cp -p "${CONF_FILE}" "${BACKUP}"
fi
WAS_ENABLED=0
[ -e "/etc/apache2/conf-enabled/${CONF_NAME}.conf" ] && WAS_ENABLED=1

# LocationMatch plutôt que Location : Jeedom peut être servi sous un
# sous-dossier (http://hôte/jeedom/), et la page ouvre le WebSocket relativement
# à sa propre adresse. Le motif accepte tout préfixe mais aucun suffixe.
# ProxyPassMatch sans renvoi ($1) ajouterait l'URL entière à la cible : le
# groupe (ws) donne exactement /ws au démon, la chaîne de requête (?t=…) suit.
step "Écriture de ${CONF_FILE} (tunnel …/plugins/jeeterm/ws vers 127.0.0.1:${PORT})"
cat > "${CONF_FILE}" <<EOF
${CONF_MARKER}
# JeeTerm — généré par le plugin, ne pas modifier : réécrit à chaque
# installation des dépendances, retiré à la désactivation du plugin.
# Relaie le terminal (WebSocket) vers le démon JeeTerm, qui n'écoute que sur
# la boucle locale.
<IfModule mod_proxy_wstunnel.c>
  <LocationMatch "^(?:/.*)?/plugins/jeeterm/(ws)\$">
    ProxyPassMatch "ws://127.0.0.1:${PORT}/\$1"
    ProxyPreserveHost On
  </LocationMatch>
</IfModule>
EOF
chmod 644 "${CONF_FILE}"
a2enconf -q "${CONF_NAME}" || fail "impossible d'activer la configuration ${CONF_NAME}"
progress 80

step "Vérification de la nouvelle configuration Apache"
if ! apache2ctl configtest 2>&1; then
    step "La configuration est refusée par Apache : retour à l'état précédent"
    if [ -n "${BACKUP}" ]; then
        cp -p "${BACKUP}" "${CONF_FILE}"
        [ "${WAS_ENABLED}" -eq 0 ] && a2disconf -q "${CONF_NAME}"
    else
        a2disconf -q "${CONF_NAME}"
        rm -f "${CONF_FILE}"
    fi
    [ -n "${BACKUP}" ] && rm -f "${BACKUP}"
    fail "apache2ctl configtest a refusé la configuration de JeeTerm"
fi
[ -n "${BACKUP}" ] && rm -f "${BACKUP}"
progress 90

# Rechargement en douceur : la requête qui a lancé ce script a déjà reçu sa
# réponse. systemd n'existe pas dans un conteneur Docker, d'où les replis.
step "Rechargement d'Apache"
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ] && systemctl reload apache2 2>&1; then
    step "Apache rechargé (systemctl)"
elif command -v service >/dev/null 2>&1 && service apache2 reload 2>&1; then
    step "Apache rechargé (service)"
elif apache2ctl -k graceful 2>&1; then
    step "Apache rechargé (apache2ctl graceful)"
else
    step "Avertissement : Apache n'a pas pu être rechargé. Redémarrez-le pour activer le terminal."
fi

progress 100
step "Installation des dépendances de JeeTerm terminée"
exit 0
