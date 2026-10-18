# Changelog

## 0.3

- The diagnostic and health-check probes, deliberately refused by the daemon,
  no longer fill the audit log: coming from the loopback with the sentinel
  token, they are logged at debug level. Every other WebSocket refusal is still
  audited, and the sentinel token can no longer be registered.

## 0.2

- Welcome banner when the terminal opens: system, Jeedom version, uptime, load,
  memory, disk, IP address, number of plugins, CPU temperature when known.
- Coloured prompt with the exit code of the last command, and tab title.
- Jeedom shortcuts: `jlog`, `jcd` (with completion), `jerr`, `jplugins`,
  `jinfo`, `jrestart`, `jauthor`, `jhelp`; longer history.
- New "Welcome banner and Jeedom shortcuts" setting, on by default; unchecked,
  the shell starts as before.

## 0.1

First release.

- Interactive terminal inside the Jeedom interface, based on xterm.js: colours,
  resizing, full-screen programs, copy and paste.
- Python daemon with no external dependency, listening on the loopback
  interface only; Apache relays the WebSocket.
- Opening restricted to administrators, through a single-use token valid for
  30 seconds.
- Shell as www-data, root or another account; optional persistent tmux session.
- Inactivity timeout, session limit, optional local-network-only restriction.
- The dependency installation configures Apache, checks the configuration
  before reloading it, and rolls back on error. The relay also works when
  Jeedom is served from a sub-folder; it is removed when the plugin is
  disabled.
- Daemon started in its own systemd unit: an Apache restart does not close
  terminals.
- Settings validated when saving; the daemon is only restarted when a setting
  it uses has changed.
- Diagnosis of a failing connection, and tests in the Health page.
