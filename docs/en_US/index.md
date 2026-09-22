# JeeTerm plugin

A real terminal inside the Jeedom interface, much like the Home Assistant
"Terminal" add-on. The shell runs on the Jeedom host and is displayed in the
browser: colours, keyboard shortcuts, resizing, full-screen programs (`nano`,
`vim`, `htop`, `mc`…), and optionally a `tmux` session that survives closing
the tab.

> **This terminal gives root access to the machine.** On a Jeedom
> installation, the `www-data` account can use `sudo` without a password: even
> opened as `www-data`, the shell can do anything. Only Jeedom administrators
> can open it. Give that profile to trusted people only.

## How it works

```
Browser ──HTTPS/WebSocket──▶ Apache (Jeedom) ──ws://127.0.0.1:55080──▶ jeetermd daemon ──pty──▶ shell
```

- The plugin page asks Jeedom for a session. PHP checks that you are an
  administrator, generates a random 256-bit token and hands it to the daemon.
- The browser then opens a WebSocket on `/plugins/jeeterm/ws`, which **Apache
  relays** to the daemon. The daemon only listens on `127.0.0.1`: it is never
  reachable directly from the network.
- The token is valid for **30 seconds** and can be used **once**.
- The daemon is written in Python using only the standard library: no `pip`
  package, no virtual environment.
- On a systemd-managed host, the daemon runs in its own unit (`systemd-run`,
  unit `jeetermd-<date>`): restarting or upgrading Apache therefore does not
  close open terminals. Without systemd (Docker), it is started directly.

## Installation

1. Install the plugin from the Market, then enable it.
2. Run the **dependency** installation (plugin configuration page).
3. Start the **daemon** (it starts by itself when automatic management is on).
4. Open **Plugins → Programming → JeeTerm**.

### What the dependency installation changes

The `resources/install_apt.sh` script, run as root by Jeedom, does this and
nothing else. Its output is in the `jeeterm_update` log.

| Action | Why |
|---|---|
| `apt-get install python3 tmux`, only if one is missing | `python3` runs the daemon; `tmux` backs the persistent session option. If another `apt` holds the lock (automatic upgrades…), the script waits up to 5 minutes. |
| Creates `/var/lib/jeeterm/home` (owner `www-data`, mode `0700`) | Home directory of the shell opened as `www-data`, whose real home (`/var/www`) belongs to root: bash history, tmux socket. |
| `a2enmod proxy proxy_wstunnel` | Apache modules able to relay a WebSocket. |
| Writes `/etc/apache2/conf-available/jeeterm.conf` and enables it (`a2enconf jeeterm`) | Declares the relay from `…/plugins/jeeterm/ws` to the daemon. |
| `apache2ctl configtest` | Checks the configuration **before** reloading Apache. |
| Graceful Apache reload | `systemctl reload`, else `service apache2 reload`, else `apache2ctl -k graceful` (Docker installs without systemd). |

Running the installation again only rewrites the configuration and reloads
Apache: that is what to do after a port change.

The configuration written is (the port is the plugin's):

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

The pattern accepts any prefix: the terminal also works when Jeedom is served
from a sub-folder (`http://host/jeedom/`), with nothing to adapt.

Safeguards: if the Apache configuration is **already** broken before the
installation, the script stops without touching anything. If `configtest`
rejects the JeeTerm configuration, the previous state is restored and the script
exits with an error: Apache is never reloaded with an invalid configuration.

Dependencies are considered complete when `python3` is present, the `proxy` and
`proxy_wstunnel` modules are enabled, and `/etc/apache2/conf-enabled/jeeterm.conf`
exists, is the version the plugin expects and points to the configured port.
With the tmux option, `tmux` must be installed too. A plugin update that changes
the shape of the configuration therefore makes dependencies incomplete, and
reinstalling them rewrites it.

JeeTerm expects the Jeedom Apache to use the Debian layout (`/etc/apache2`,
`a2enmod`, `a2enconf`). Elsewhere, the installation stops with an error.

## Configuration

| Setting | Default | Purpose |
|---|---|---|
| Shell account | www-data | `www-data` (the Jeedom account), `root`, or another Unix account. `root` and other accounts go through `sudo -n -i`. A missing account, or one whose shell is disabled (`nologin`), is refused when saving. |
| Persistent session (tmux) | off | The shell runs in the `jeeterm` tmux session, found as it was when reopened. All windows share that session. |
| Welcome banner and Jeedom shortcuts | on | Welcome banner, coloured prompt and `jlog`, `jcd`… shortcuts. See "Banner and shortcuts". Unchecked, the shell is a plain `bash -l` (or `sudo -n -i`). |
| Close after inactivity | 15 min | Time without any keystroke before the terminal closes, from `0` (never) to `10080` (one week). |
| Simultaneous sessions | 3 | Maximum number of open terminals, all administrators combined, from 1 to 20. |
| Local network only | off | Refuses to open a terminal from outside. See below. |
| Local port | 55080 | Daemon port, on `127.0.0.1` only, from 1024 to 65535. |

An invalid value is refused when saving, with a message saying why.

**Daemon settings apply when it restarts.** When the configuration is saved,
JeeTerm compares the settings with those the daemon is running with, and
restarts it in the background if they differ: this closes all open terminals (a
tmux session keeps running). Saving without changes restarts nothing. If
automatic daemon management is off, a message asks you to restart it yourself.
"Local network only" applies immediately, without a restart. The log level is
only picked up by the daemon at its next restart.

**Changing the port** requires running the dependency installation again, which
rewrites the Apache configuration. Until then, dependencies are reported
incomplete and the daemon stays stopped. With both automatic daemon **and**
dependency management on, Jeedom runs it by itself within a few minutes.

## Usage

Open the plugin page: the terminal opens straight away. The toolbar lets you
reconnect, open a new session, copy the selection (`Ctrl+Shift+C`), paste,
change the font size and go full screen. A row of special keys (Esc, Tab, Ctrl,
Alt, arrows) is there for tablets.

Closing the tab closes the shell and everything it started, except in tmux mode
where the session keeps running.

When a session ends, **Enter** opens a new one. `Ctrl+Shift+F` searches the
displayed text; `Ctrl+W`, `Ctrl+T` and `Ctrl+N` belong to the browser, no web
page can intercept them.

The **⚙** button opens the terminal settings, remembered per browser: theme
(Jeedom automatic, Black, Green phosphor, Amber, Dracula, Solarized dark,
Monokai, Nord, Tango light), CRT effect, cursor, font, size, line height, bell
and copy on select. The **⚡ Commands** button types a ready-made command into
the terminal without running it; you can add your own. The toolbar can also
save the whole terminal content to a `.txt` file and clear the screen.

### Banner and shortcuts

With the "Welcome banner and Jeedom shortcuts" setting (on by default), every
new shell opens on a summary of the machine (labels follow the system language):

```
        ▄█▄          www-data@jeedom
      ▄█████▄        ---------------
    ▄█████████▄      OS            Debian GNU/Linux 12 (bookworm)
  ▄█████████████▄    Kernel        6.1.0-25-arm64
 ▀▀▀███████████▀▀▀   Jeedom        4.4.19  (/var/www/html)
    ███▀▀▀▀▀███      Uptime        2 d 6 h 36 min
    ███ ▄▄▄ ███      Load          0.08  0.23  0.40
    ███ ███ ███      Memory        1.4 GiB / 3.8 GiB (37%)
    ▀▀▀ ▀▀▀ ▀▀▀      Disk /        6.0 GiB / 28.7 GiB (23%)
                     IP address    192.168.1.20
   JeeTerm           Plugins       17
                     CPU temp      48.3 °C

Tip: jlog <plugin> follows a Jeedom log, Tab completes the name.
```

The temperature only shows when the system provides it (Raspberry Pi…). Below
70 columns, the logo is left out and only the information remains.

The prompt is coloured (`account@host:folder$`, red for root); when the last
command failed, its exit code comes first, for example `[1]`. It also sets the
tab title. The shortcuts:

| Command | Effect |
|---|---|
| `jcd [plugin]` | go to the plugin folder (no argument: the plugins folder); Tab completes the name |
| `jlog [log]` | follow a Jeedom log (`tail -F`); no argument lists the logs; Tab completes the name |
| `jerr` | follow `log/http.error`, the PHP errors |
| `jplugins` | list the installed plugins and their version |
| `jinfo` | show the banner again |
| `jrestart` | print the Apache restart commands and run them after confirmation |
| `jauthor` (or `jauteur`) | author card: sMug (Jérôme Fafchamps), jerome@fafchamps.be |
| `jhelp` | list these shortcuts |
| `ll`, `la`, `l` | `ls` variants; `ls` and `grep` are coloured |

History keeps 10,000 commands, without consecutive duplicates or commands
starting with a space.

The shell stays the account's own: `/etc/profile`, then `~/.profile` (or
`~/.bash_profile`) and `~/.bashrc` are read as in a login shell; for
`www-data`, the home folder is `/var/lib/jeeterm/home`. The JeeTerm prompt
replaces the one from `~/.bashrc`: to keep yours, or to add your own aliases,
put them in `~/.jeetermrc`, read last. `JEETERM_BANNER=0` in that file removes
the banner. An account whose shell is not bash (zsh…) keeps its shell, without
shortcuts.

In tmux mode, the banner shows when the session is created, not when it is
resumed; windows opened later inside tmux get the shortcuts, without the banner.

## Security

- **Root equivalent.** `www-data` has passwordless `sudo` on Jeedom: whatever
  account is chosen, an administrator who opens the terminal controls the whole
  machine.
- **Administrators only.** Requesting a session requires the Jeedom
  administrator profile, through a POST request. A request forged from another
  site is rejected: the Jeedom session cookie is `SameSite=Strict`, and JeeTerm
  also requires the `X-Requested-With` header.
- **Single-use token.** 256 random bits, valid for 30 seconds, usable once. It
  is registered with the daemon over the loopback interface, with the plugin API
  key, and refused if it comes through Apache.
- **Daemon unreachable from the network.** It only listens on `127.0.0.1`; only
  Apache talks to it. The plugin API is set to "localhost" mode.
- **Local network only.** This option refuses to open a terminal when Jeedom
  places the browser outside the local network, and also requires the actual
  connection address to be local. It is a **convenience filter, not a security
  boundary**: the client address is derived from HTTP headers, which the Jeedom
  Apache configuration (`mod_remoteip`) trusts. A reverse proxy on the local
  network makes all its clients look local. Jeedom's rules only know IPv4: a
  browser connected over IPv6 is refused, unless its exact address is listed
  among the local addresses of the Jeedom network configuration.
- **Inactivity.** A forgotten terminal closes after the configured delay.
- **Logs.** Every session request, accepted or refused, is logged in `jeeterm`
  at *warning* level: Jeedom user, declared IP address and, when it differs,
  the actual connection address. Openings and closings are logged in
  `jeetermd` at *AUDIT* level, with the shell account, duration and reason.
  These audit lines are written whatever the log level, unless it is set to
  "None".

## Troubleshooting

**The terminal does not open.** Look first at:

- the `jeetermd` log (daemon) and the `jeeterm` log (session requests);
- the `jeeterm_update` log if dependencies are reported incomplete;
- `/var/www/html/log/http.error` for any PHP error on the page;
- the Jeedom **Health** page, where JeeTerm tests the daemon and the Apache
  relay.

When the connection fails, the terminal page runs a server-side diagnosis: it
probes the Apache relay the way the browser would, and tells where the block is
(dependencies, daemon, Apache configuration, or an intermediary in front of
Apache).

**"The JeeTerm daemon is not running"**: start it from the plugin configuration
page, and read the `jeetermd` log if it refuses.

**"Maximum number of sessions reached"**: a terminal is still open in another
tab or on another device. Close it, wait for the inactivity timeout, or raise
the limit.

**The connection is refused or drops immediately**: the Apache configuration
may point to the wrong port. Run the dependency installation again and check
that `/etc/apache2/conf-enabled/jeeterm.conf` exists.

**Access through Jeedom DNS or a reverse proxy.** The terminal uses a
WebSocket: every intermediary must let it through (`Upgrade` and `Connection`
headers). This is **not guaranteed** with Jeedom DNS; with your own reverse
proxy (nginx, Traefik, Caddy…), enable WebSocket support on
`/plugins/jeeterm/ws`.

**Jeedom installed in a sub-folder** (for example `http://host/jeedom/`):
nothing to do, the Apache configuration relays `…/plugins/jeeterm/ws` whatever
the prefix.

## Uninstalling

Disabling or removing the plugin stops the daemon and **removes the Apache
configuration** (`a2disconf jeeterm`, file deletion, Apache reload). Jeedom
calls the same function for a deactivation and for a removal; on reactivation,
it finds the dependencies missing and reinstalls them by itself when automatic
dependency management is on (otherwise, use the dependencies "Relaunch"
button).

If `sudo` is not available to `www-data`, the configuration stays in place (the
`jeeterm` log says so). Without the daemon, the relay leads nowhere (Apache
answers 503). To remove it by hand:

```bash
sudo a2disconf jeeterm
sudo rm /etc/apache2/conf-available/jeeterm.conf
sudo systemctl reload apache2      # or: sudo service apache2 reload
```

Left in place: the `proxy` and `proxy_wstunnel` modules, which may serve other
purposes (only disable them, `sudo a2dismod proxy_wstunnel`, if you know nothing
else relies on them), the `python3` and `tmux` packages, and the
`/var/lib/jeeterm` directory (`sudo rm -r /var/lib/jeeterm` to erase it, along
with the shell history it holds).
