# JeeTerm : fichier de démarrage du shell (bash --rcfile <ce fichier> -i).
#
# Lancé par le démon quand le réglage « Bannière d'accueil et raccourcis
# Jeedom » est actif. Le shell n'est alors plus un shell de connexion : ce
# fichier refait d'abord ce que ferait `bash -l`, puis ajoute bannière, invite
# et raccourcis. Rien ici ne doit échouer bruyamment ni prendre du temps : ni
# réseau, ni commande lente, et le moins de sous-processus possible.
#
# Personnalisation : ~/.jeetermrc est lu en dernier. On peut y redéfinir PS1,
# des alias, ou poser JEETERM_BANNER=0 pour se passer de la bannière.

# Environnement de connexion. Par sudo -i, le shell de connexion du compte a
# déjà lu /etc/profile et ~/.profile avant de nous lancer : l'environnement
# exporté est là, il ne manque que la partie interactive. Sinon (compte du
# démon), on lit ce que lirait bash -l ; sur Debian, /etc/profile lit lui-même
# /etc/bash.bashrc.
if [ -n "${SUDO_USER:-}" ]; then
    [ -r /etc/bash.bashrc ] && . /etc/bash.bashrc
    [ -r "$HOME/.bashrc" ] && . "$HOME/.bashrc"
else
    [ -r /etc/profile ] && . /etc/profile
    if [ -r "$HOME/.bash_profile" ]; then
        . "$HOME/.bash_profile"
    elif [ -r "$HOME/.bash_login" ]; then
        . "$HOME/.bash_login"
    elif [ -r "$HOME/.profile" ]; then
        . "$HOME/.profile"
    elif [ -r "$HOME/.bashrc" ]; then
        . "$HOME/.bashrc"
    fi
fi

# Racine de Jeedom : ce fichier est dans <racine>/plugins/jeeterm/resources/jeetermd.
__jeeterm_rc_file=${BASH_SOURCE[0]:-}
case $__jeeterm_rc_file in /*) ;; *) __jeeterm_rc_file=$PWD/$__jeeterm_rc_file ;; esac
JEEDOM_DIR=${__jeeterm_rc_file%/*/*/*/*/*}
[ -f "$JEEDOM_DIR/core/config/version" ] || JEEDOM_DIR=/var/www/html
JEETERM_RCFILE=$__jeeterm_rc_file
unset __jeeterm_rc_file

# ------------------------------------------------------------ historique
HISTSIZE=10000
HISTFILESIZE=20000
HISTCONTROL=ignoreboth
HISTTIMEFORMAT='%F %T  '
shopt -s histappend checkwinsize

# ------------------------------------------------------------ couleurs
# Séquences ANSI écrites en dur (tput coûterait un processus par couleur).
if [ "${TERM:-dumb}" = dumb ]; then
    __jt_g= __jt_r= __jt_b= __jt_d= __jt_B= __jt_0=
else
    __jt_g=$'\e[38;5;112m' __jt_r=$'\e[38;5;196m' __jt_b=$'\e[38;5;39m'
    __jt_d=$'\e[2m' __jt_B=$'\e[1m' __jt_0=$'\e[0m'
fi
case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in
    *[Uu][Tt][Ff]-8*|*[Uu][Tt][Ff]8*) __jt_utf8=1 ;;
    *) __jt_utf8= ;;
esac
case "${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}" in
    fr*) __jt_fr=1 ;;
    *) __jt_fr= ;;
esac

# ------------------------------------------------------------ invite
# Code de sortie de la dernière commande, relevé avant tout autre
# PROMPT_COMMAND (celui de l'utilisateur est conservé, après le nôtre).
__jeeterm_status() {
    local s=$?
    if [ "$s" -ne 0 ]; then __jeeterm_rc=$s; else __jeeterm_rc=; fi
    return "$s"
}
case ";${PROMPT_COMMAND:-};" in
    *";__jeeterm_status;"*) ;;
    *) PROMPT_COMMAND="__jeeterm_status${PROMPT_COMMAND:+;$PROMPT_COMMAND}" ;;
esac

if [ "${EUID:-1}" -eq 0 ]; then __jt_u=$__jt_r; else __jt_u=$__jt_g; fi
if [ -n "$__jt_0" ]; then
    # Titre de la fenêtre (OSC 0) : l'onglet du navigateur l'affiche.
    PS1='\[\e]0;\u@\h: \w\a\]${__jeeterm_rc:+\['"$__jt_r"'\][$__jeeterm_rc]\['"$__jt_0"'\] }\['"$__jt_B$__jt_u"'\]\u@\h\['"$__jt_0"'\]:\['"$__jt_B$__jt_b"'\]\w\['"$__jt_0"'\]\$ '
else
    PS1='${__jeeterm_rc:+[$__jeeterm_rc] }\u@\h:\w\$ '
fi
unset __jt_u

# ------------------------------------------------------------ alias
alias ls='ls --color=auto'
alias grep='grep --color=auto'
alias ll='ls -alFh'
alias la='ls -A'
alias l='ls -CF'

# ------------------------------------------------------------ raccourcis Jeedom
jcd() {
    cd "$JEEDOM_DIR/plugins/${1:-}" || return
}

jlog() {
    if [ $# -eq 0 ]; then
        ( cd "$JEEDOM_DIR/log" 2>/dev/null && ls -lhF --color=auto ) \
            || echo "$JEEDOM_DIR/log : ?" >&2
        return
    fi
    # Un nom mal tapé : tail -F attendrait indéfiniment que le fichier apparaisse.
    if [ ! -f "$JEEDOM_DIR/log/$1" ]; then
        echo "jlog: $JEEDOM_DIR/log/$1 ?" >&2
        return 1
    fi
    tail -n 100 -F "$JEEDOM_DIR/log/$1"
}

jerr() {
    tail -n 50 -F "$JEEDOM_DIR/log/http.error"
}

jplugins() {
    # Une seule invocation de python3 (toujours présent : le démon en dépend).
    python3 - "$JEEDOM_DIR/plugins" <<'EOF'
import glob, json, os, sys
rows = []
for path in sorted(glob.glob(os.path.join(sys.argv[1], '*', 'plugin_info', 'info.json'))):
    folder = os.path.basename(os.path.dirname(os.path.dirname(path)))
    try:
        with open(path, encoding='utf-8') as f:
            info = json.load(f)
    except (OSError, ValueError):
        info = {}
    version = info.get('pluginVersion') or info.get('version') or '-'
    rows.append((folder, str(version), str(info.get('name', ''))))
width = max([len(r[0]) for r in rows] + [2])
for folder, version, name in rows:
    print('%-*s  %-8s  %s' % (width, folder, version, name))
print('%d plugin(s)' % len(rows))
EOF
}

jrestart() {
    local sudo= answer fpm
    [ "${EUID:-1}" -eq 0 ] || sudo='sudo '
    fpm=$(cd /lib/systemd/system 2>/dev/null && ls -1 php*-fpm.service 2>/dev/null | head -n 1)
    if [ -n "$__jt_fr" ]; then
        echo "Commandes de redémarrage du serveur web :"
    else
        echo "Commands to restart the web server:"
    fi
    echo "  ${sudo}systemctl restart apache2"
    [ -n "$fpm" ] && echo "  ${sudo}systemctl restart ${fpm%.service}"
    if [ -n "$__jt_fr" ]; then
        echo "Les pages Jeedom ouvertes perdent leur connexion quelques secondes. Ce terminal"
        echo "survit si le démon JeeTerm tourne dans sa propre unité systemd (cas normal)."
        read -r -p "Redémarrer maintenant ? [o/N] " answer
    else
        echo "Open Jeedom pages lose their connection for a few seconds. This terminal"
        echo "survives if the JeeTerm daemon runs in its own systemd unit (the usual case)."
        read -r -p "Restart now? [y/N] " answer
    fi
    case $answer in
        [oOyY]*)
            ${sudo}systemctl restart apache2 || return
            [ -z "$fpm" ] || ${sudo}systemctl restart "${fpm%.service}"
            ;;
    esac
}

# Carte de visite de l'auteur du plugin.
jauteur() {
    local version info="${JEETERM_RCFILE%/*/*/*}/plugin_info/info.json"
    version=$(sed -n 's/.*"pluginVersion"[^"]*"\([^"]*\)".*/\1/p' "$info" 2>/dev/null)
    local line='────────────────────────────────────────'
    [ -n "$__jt_utf8" ] || line='----------------------------------------'
    printf '\n  %s%s>_ JeeTerm%s %s%s%s\n' "$__jt_g" "$__jt_B" "$__jt_0" "$__jt_d" "${version:+v$version}" "$__jt_0"
    printf '  %s%s%s\n' "$__jt_d" "$line" "$__jt_0"
    if [ -n "$__jt_fr" ]; then
        printf '  %-9s %s%ssMug%s (Jérôme Fafchamps)\n' 'Auteur' "$__jt_g" "$__jt_B" "$__jt_0"
        printf '  %-9s %sjerome@fafchamps.be%s\n' 'Courriel' "$__jt_b" "$__jt_0"
        printf '  %-9s AGPL\n' 'Licence'
        printf '  %s%s%s\n' "$__jt_d" "$line" "$__jt_0"
        printf '  %sUn vrai terminal dans Jeedom. Bon hacking !%s\n\n' "$__jt_d" "$__jt_0"
    else
        printf '  %-9s %s%ssMug%s (Jérôme Fafchamps)\n' 'Author' "$__jt_g" "$__jt_B" "$__jt_0"
        printf '  %-9s %sjerome@fafchamps.be%s\n' 'Email' "$__jt_b" "$__jt_0"
        printf '  %-9s AGPL\n' 'Licence'
        printf '  %s%s%s\n' "$__jt_d" "$line" "$__jt_0"
        printf '  %sA real terminal inside Jeedom. Happy hacking!%s\n\n' "$__jt_d" "$__jt_0"
    fi
}
jauthor() { jauteur "$@"; }

jhelp() {
    if [ -n "$__jt_fr" ]; then
        cat <<EOF
${__jt_B}Raccourcis JeeTerm${__jt_0}  (Jeedom : $JEEDOM_DIR)
  ${__jt_g}jcd${__jt_0} [plugin]    aller dans le dossier d'un plugin (Tab complète le nom)
  ${__jt_g}jlog${__jt_0} [journal]  suivre un journal Jeedom ; sans nom, liste les journaux
  ${__jt_g}jerr${__jt_0}            suivre les erreurs PHP (log/http.error)
  ${__jt_g}jplugins${__jt_0}        plugins installés et leur version
  ${__jt_g}jinfo${__jt_0}           réafficher la bannière d'accueil
  ${__jt_g}jrestart${__jt_0}        redémarrer Apache (après confirmation)
  ${__jt_g}jauteur${__jt_0}         l'auteur de JeeTerm
  ${__jt_g}ll la l${__jt_0}         variantes de ls
Réglages personnels : ~/.jeetermrc (lu en dernier ; JEETERM_BANNER=0 retire la bannière).
EOF
    else
        cat <<EOF
${__jt_B}JeeTerm shortcuts${__jt_0}  (Jeedom: $JEEDOM_DIR)
  ${__jt_g}jcd${__jt_0} [plugin]    go to a plugin folder (Tab completes the name)
  ${__jt_g}jlog${__jt_0} [log]      follow a Jeedom log; without a name, list the logs
  ${__jt_g}jerr${__jt_0}            follow PHP errors (log/http.error)
  ${__jt_g}jplugins${__jt_0}        installed plugins and their version
  ${__jt_g}jinfo${__jt_0}           show the welcome banner again
  ${__jt_g}jrestart${__jt_0}        restart Apache (after confirmation)
  ${__jt_g}jauthor${__jt_0}         who wrote JeeTerm
  ${__jt_g}ll la l${__jt_0}         ls variants
Personal settings: ~/.jeetermrc (read last; JEETERM_BANNER=0 removes the banner).
EOF
    fi
}

# Complétion : noms de plugins pour jcd, fichiers du dossier log pour jlog.
__jeeterm_complete_jcd() {
    local cur=${COMP_WORDS[COMP_CWORD]} IFS=$'\n'
    COMPREPLY=($(cd "$JEEDOM_DIR/plugins" 2>/dev/null && compgen -d -- "$cur"))
}
__jeeterm_complete_jlog() {
    local cur=${COMP_WORDS[COMP_CWORD]} IFS=$'\n'
    COMPREPLY=($(cd "$JEEDOM_DIR/log" 2>/dev/null && compgen -f -- "$cur" | while read -r f; do
        if [ -d "$f" ]; then echo "$f/"; else echo "$f"; fi
    done))
    # Un dossier (scenarioLog/) : pas d'espace, pour continuer la saisie.
    [ ${#COMPREPLY[@]} -eq 1 ] && [ "${COMPREPLY[0]%/}" != "${COMPREPLY[0]}" ] && compopt -o nospace 2>/dev/null
    return 0
}
complete -F __jeeterm_complete_jcd jcd
complete -F __jeeterm_complete_jlog jlog

# ------------------------------------------------------------ bannière
# Durée lisible : 273845 -> « 3 j 4 h 4 min ».
__jeeterm_duration() {
    local s=$1 d h m out=
    d=$((s / 86400)) h=$((s % 86400 / 3600)) m=$((s % 3600 / 60))
    if [ "$d" -gt 0 ]; then
        if [ -n "$__jt_fr" ]; then out="$d j "; else out="$d d "; fi
    fi
    [ "$d" -gt 0 ] || [ "$h" -gt 0 ] && out="$out$h h "
    echo "$out$m min"
}

# Quantité en kio -> « 1,2 Gio » / « 1.2 GiB ».
__jeeterm_size() {
    local k=$1 unit sep=. i=G
    [ -n "$__jt_fr" ] && sep=,
    if [ "$k" -ge 1048576 ]; then
        k=$((k * 10 / 1048576))
    else
        k=$((k * 10 / 1024)) i=M
    fi
    if [ -n "$__jt_fr" ]; then unit="${i}io"; else unit="${i}iB"; fi
    echo "$((k / 10))$sep$((k % 10)) $unit"
}

jinfo() {
    local cols rows logo=() info=() labels line key value i n w pad
    local os= kernel= version= up= load= mem= disk= ip= plugins=0 temp=
    local total=0 avail=0 f pct='%' dec=.
    [ -n "$__jt_fr" ] && pct=' %' dec=,

    # Largeur : COLUMNS n'est connu qu'après la première commande.
    cols=${COLUMNS:-0}
    if [ "$cols" -le 0 ] 2>/dev/null; then
        read -r rows cols < <(stty size 2>/dev/null) || cols=80
    fi
    [ "${cols:-0}" -gt 0 ] 2>/dev/null || cols=80

    if [ -r /etc/os-release ]; then
        while IFS='=' read -r key value; do
            [ "$key" = PRETTY_NAME ] && { value=${value#\"}; os=${value%\"}; break; }
        done < /etc/os-release
    fi
    [ -r /proc/sys/kernel/osrelease ] && read -r kernel < /proc/sys/kernel/osrelease
    [ -r "$JEEDOM_DIR/core/config/version" ] && read -r version < "$JEEDOM_DIR/core/config/version"
    if [ -r /proc/uptime ]; then
        read -r up _ < /proc/uptime
        up=$(__jeeterm_duration "${up%%.*}")
    fi
    [ -r /proc/loadavg ] && { read -r n i w _ < /proc/loadavg; load="$n  $i  $w"; }
    if [ -r /proc/meminfo ]; then
        while read -r key value _; do
            case $key in
                MemTotal:) total=$value ;;
                MemAvailable:) avail=$value; break ;;
            esac
        done < /proc/meminfo
        [ "$total" -gt 0 ] && mem="$(__jeeterm_size $((total - avail))) / $(__jeeterm_size "$total") ($(((total - avail) * 100 / total))$pct)"
    fi
    { read -r _; read -r _ n i _ w _; } < <(df -Pk / 2>/dev/null)
    [ -n "${w:-}" ] && [ "${n:-0}" -gt 0 ] 2>/dev/null && disk="$(__jeeterm_size "$i") / $(__jeeterm_size "$n") (${w%\%}$pct)"
    ip=$(hostname -I 2>/dev/null)
    ip=${ip%"${ip##*[![:space:]]}"}
    for f in "$JEEDOM_DIR"/plugins/*/plugin_info/info.json; do
        [ -e "$f" ] && plugins=$((plugins + 1))
    done
    if [ -r /sys/class/thermal/thermal_zone0/temp ]; then
        read -r i < /sys/class/thermal/thermal_zone0/temp
        [ "${i:-0}" -gt 0 ] 2>/dev/null && temp="$((i / 1000))$dec$((i % 1000 / 100)) °C"
    fi

    if [ -n "$__jt_fr" ]; then
        labels=(Système Noyau Jeedom 'Allumé depuis' Charge Mémoire 'Disque /' 'Adresse IP' Plugins 'Temp. CPU')
    else
        labels=(OS Kernel Jeedom Uptime Load Memory 'Disk /' 'IP address' Plugins 'CPU temp')
    fi
    info=("${__jt_B}${__jt_g}${USER:-$(id -un)}${__jt_0}@${__jt_B}${__jt_g}${HOSTNAME:-?}${__jt_0}")
    line="${USER:-?}@${HOSTNAME:-?}"
    printf -v line '%*s' "${#line}" ''
    info+=("${__jt_d}${line// /-}${__jt_0}")
    i=0
    for value in "$os" "$kernel" "${version:+$version  ${__jt_d}($JEEDOM_DIR)${__jt_0}}" "$up" "$load" \
                 "$mem" "$disk" "$ip" "$plugins" "$temp"; do
        # Alignement en caractères, pas en octets (printf %-14s compte les octets).
        if [ -n "$value" ]; then
            key=${labels[$i]}
            printf -v pad '%*s' $((14 - ${#key})) ''
            info+=("$__jt_g$key$__jt_0$pad$value")
        fi
        i=$((i + 1))
    done

    # Logo : la maison Jeedom, en blocs Unicode ou en ASCII.
    if [ -n "$__jt_utf8" ]; then
        logo=('        ▄█▄        ' '      ▄█████▄      ' '    ▄█████████▄    ' '  ▄█████████████▄  '
              ' ▀▀▀███████████▀▀▀ ' '    ███▀▀▀▀▀███    ' '    ███ ▄▄▄ ███    ' '    ███ ███ ███    '
              '    ▀▀▀ ▀▀▀ ▀▀▀    ')
    else
        logo=('        /\         ' '      /    \       ' '    /        \     ' '  /____________\   '
              '    |  ____  |     ' '    | |    | |     ' '    | |    | |     ' '    |_|____|_|     '
              '                   ')
    fi
    logo+=('                   ' "   ${__jt_B}Jee${__jt_g}Term${__jt_0}         ")

    # Place pour les valeurs : le texte trop long est coupé, jamais replié.
    if [ "$cols" -lt 70 ]; then
        w=$((cols - 1))
        for line in "${info[@]}"; do
            __jeeterm_fit "$line" "$w"
        done
    else
        w=$((cols - 21))
        [ "$w" -gt 59 ] && w=59
        n=${#logo[@]}
        [ ${#info[@]} -gt "$n" ] && n=${#info[@]}
        for ((i = 0; i < n; i++)); do
            pad=${logo[$i]:-                   }
            printf '%s%s%s  ' "$__jt_g" "$pad" "$__jt_0"
            __jeeterm_fit "${info[$i]:-}" "$w"
        done
    fi
    # L'astuce tient sur 80 colonnes ; plus étroit, elle est repliée aux espaces.
    if [ "$cols" -lt 80 ]; then
        __jeeterm_tip | fold -s -w "$cols"
    else
        __jeeterm_tip
    fi
}

# Affiche une ligne colorée sans dépasser `w` caractères visibles.
__jeeterm_fit() {
    local text=$1 w=$2 plain
    plain=${text//$'\e['[0-9]m/}
    plain=${plain//$'\e['[0-9][0-9]m/}
    plain=${plain//$'\e[38;5;'[0-9][0-9][0-9]m/}
    plain=${plain//$'\e[38;5;'[0-9][0-9]m/}
    if [ ${#plain} -le "$w" ]; then
        printf '%s\n' "$text"
    else
        if [ -n "$__jt_utf8" ]; then
            printf '%s…\n' "${plain:0:$((w - 1))}"
        else
            printf '%s~\n' "${plain:0:$((w - 1))}"
        fi
    fi
}

__jeeterm_tip() {
    local tips
    if [ -n "$__jt_fr" ]; then
        tips=('jlog <plugin> suit un journal Jeedom, Tab complète le nom.'
              'jcd <plugin> mène au dossier du plugin, Tab complète le nom.'
              'jerr suit les erreurs PHP : le réflexe quand une page ne répond plus.'
              'jplugins liste les plugins installés et leur version.'
              'jhelp liste tous les raccourcis JeeTerm.'
              'jauteur : qui se cache derrière JeeTerm ?'
              'Ctrl+R cherche dans l'"'"'historique des commandes.'
              '~/.jeetermrc est lu à chaque ouverture : vos alias, votre invite.')
        printf '\n%sAstuce :%s %s\n\n' "$__jt_g" "$__jt_0" "${tips[RANDOM % ${#tips[@]}]}"
    else
        tips=('jlog <plugin> follows a Jeedom log, Tab completes the name.'
              'jcd <plugin> takes you to the plugin folder, Tab completes the name.'
              'jerr follows PHP errors: the first check when a page stops responding.'
              'jplugins lists the installed plugins and their version.'
              'jhelp lists every JeeTerm shortcut.'
              'jauthor: who is behind JeeTerm?'
              'Ctrl+R searches the command history.'
              '~/.jeetermrc is read at every start: aliases and prompt to your liking.')
        printf '\n%sTip:%s %s\n\n' "$__jt_g" "$__jt_0" "${tips[RANDOM % ${#tips[@]}]}"
    fi
}

# ------------------------------------------------------------ fin
[ -r "$HOME/.jeetermrc" ] && . "$HOME/.jeetermrc"

# Une seule bannière par shell ; dans tmux, seulement dans le premier panneau
# du serveur (les fenêtres suivantes démarrent sans elle).
if [ "${JEETERM_BANNER:-1}" != 0 ] && [ -z "${__jeeterm_banner_done:-}" ]; then
    if [ -z "${TMUX:-}" ] || [ "${TMUX_PANE:-}" = "%0" ]; then
        jinfo
    fi
fi
__jeeterm_banner_done=1
