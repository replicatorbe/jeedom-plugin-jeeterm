/* This file is part of Jeedom.
 *
 * Jeedom is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * Jeedom is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with Jeedom. If not, see <http://www.gnu.org/licenses/>.
 */

/*
 * JeeTerm : le terminal dans la page.
 *
 * Déroulé (contrat dans CONCEPTION.md) :
 *   1. POST core/ajax/jeeterm.ajax.php action=createSession
 *        -> {state:'ok', result:{token, path}}  ou  {state:'error', result:'message'}
 *   2. WebSocket <base>/<path>?t=<jeton>&c=<colonnes>&r=<lignes>
 *        démon -> page : trames binaires = sortie du pty ; texte {"t":"x",…} = fin
 *        page -> démon : {"t":"i","d":…} saisie, {"t":"r",…} taille, {"t":"p"} maintien
 *
 * Particularités de Jeedom 4.6 dont ce script dépend :
 *   - La page arrive en AJAX (jeedomUtils.loadPage, desktop/common/js/utils.js) :
 *     DOMContentLoaded est passé depuis longtemps, tout s'attache ici même.
 *   - Quitter la page ne décharge rien : loadPage vide #div_pageContainer et le
 *     remplace par un clone (utils.js, vers la ligne 176). Aucun événement
 *     « page quittée » n'existe ; sans précaution le WebSocket, le shell et
 *     l'intervalle de maintien survivraient à la navigation. D'où plusieurs
 *     détections, voir watchPageLeave().
 *   - Revenir sur la page ré-exécute ce script (il est servi par
 *     core/php/getResource.php, réinjecté à chaque fois) alors que xterm.js,
 *     rangé dans <head>, reste chargé. L'instance précédente est détruite
 *     avant d'en créer une autre.
 *   - getResource.php traduit les chaînes entre doubles accolades de ce fichier. Une traduction
 *     y voit ses apostrophes échappées (\'), et le texte français n'est pas
 *     échappé du tout : toute chaîne traduisible contenant une apostrophe est
 *     donc écrite entre guillemets doubles.
 *   - Aucun raccourci clavier global du cœur ne gêne le terminal : le seul
 *     écouteur keydown permanent (utils.js, vers la ligne 869) ne réagit qu'à
 *     Échap sur <body>, et xterm annule lui-même les Ctrl+lettre qu'il envoie
 *     au shell (Ctrl+S, Ctrl+F, Ctrl+P…). Ctrl+W, Ctrl+T et Ctrl+N restent au
 *     navigateur : aucune page ne peut les intercepter.
 *
 * Aucune reconnexion automatique : une session terminée le reste tant que
 * l'utilisateur n'appuie pas sur Entrée ou sur « Reconnecter ».
 *
 * Réglages d'affichage (thème, police, curseur, effet cathodique, cloche,
 * copie à la sélection) et commandes rapides : propres au navigateur,
 * rangés dans localStorage, appliqués sans reconnexion. Rien n'en part vers
 * le serveur.
 */
(function () {
  'use strict'

  if (window.jeetermInstance && typeof window.jeetermInstance.destroy === 'function') {
    try {
      window.jeetermInstance.destroy()
    } catch (e) { }
  }
  window.jeetermInstance = null

  var AJAX_URL = 'plugins/jeeterm/core/ajax/jeeterm.ajax.php'
  var CONFIG_URL = 'index.php?v=d&p=plugin&id=jeeterm'
  var DEFAULT_WS_PATH = 'plugins/jeeterm/ws'
  var KEEPALIVE_MS = 25000
  var REQUEST_TIMEOUT_MS = 20000   // demande de jeton (PHP -> démon compris)
  var OPEN_TIMEOUT_MS = 15000      // poignée de main WebSocket (le jeton vit 30 s)
  var FIT_DEBOUNCE_MS = 80
  var FONT_KEY = 'jeeterm.fontSize'
  var KEYS_KEY = 'jeeterm.showKeys'
  var SETTINGS_KEY = 'jeeterm.settings'   // objet JSON, voir SETTING_DEFAULTS
  var SNIPPETS_KEY = 'jeeterm.snippets'   // liste JSON [{l, c, r}], absente = liste d'origine
  var HUD_MS = 1200                       // affichage de la taille après un redimensionnement
  var SNIPPETS_MAX = 100
  var FONT_MIN = 8
  var FONT_MAX = 32
  var FONT_DEFAULT = 14

  var root = document.getElementById('div_jeeterm')
  if (root === null) {
    return
  }
  var host = document.getElementById('div_jeetermTerminal')
  var statusEl = document.getElementById('span_jeetermStatus')
  var errorBox = document.getElementById('div_jeetermError')
  var errorText = document.getElementById('span_jeetermError')
  var btReconnect = document.getElementById('bt_jeetermReconnect')
  var btFullscreen = document.getElementById('bt_jeetermFullscreen')
  var btToggleKeys = document.getElementById('bt_jeetermToggleKeys')
  var fontSizeEl = document.getElementById('span_jeetermFontSize')
  var keysRow = document.getElementById('div_jeetermKeys')
  var pasteBox = document.getElementById('div_jeetermPaste')
  var pasteArea = document.getElementById('ta_jeetermPaste')
  var btSearch = document.getElementById('bt_jeetermSearch')
  var searchRow = document.getElementById('div_jeetermSearch')
  var findInput = document.getElementById('in_jeetermFind')
  var searchCount = document.getElementById('span_jeetermSearchCount')
  var titleEl = document.getElementById('span_jeetermTitle')
  var hudEl = document.getElementById('div_jeetermHud')
  var fontSizeEl2 = document.getElementById('span_jeetermFontSize2')
  var btSettings = document.getElementById('bt_jeetermSettings')
  var btSnippets = document.getElementById('bt_jeetermSnippets')
  var settingsPanel = document.getElementById('div_jeetermSettings')
  var snippetsPanel = document.getElementById('div_jeetermSnippets')
  var snippetList = document.getElementById('ul_jeetermSnippets')
  var snippetForm = document.getElementById('div_jeetermSnippetForm')
  var snippetLabel = document.getElementById('in_jeetermSnippetLabel')
  var snippetCmd = document.getElementById('in_jeetermSnippetCmd')
  var snippetRun = document.getElementById('cb_jeetermSnippetRun')

  /* ---------- Écouteurs retirables ----------
     Tous les écouteurs passent par on() pour que destroy() puisse les retirer :
     ceux posés sur window, document ou body survivent à loadPage, et même ceux
     de la page doivent partir si le script est exécuté deux fois sur le même
     DOM. */
  var listeners = []
  function on(target, type, fn, options) {
    if (!target) {
      return
    }
    target.addEventListener(type, fn, options)
    listeners.push({ target: target, type: type, fn: fn, options: options })
  }

  /* phase : 'connecting' (jeton demandé ou poignée de main en cours),
     'on' (WebSocket ouvert), 'off' (rien : Entrée ou « Reconnecter » relance). */
  var state = {
    destroyed: false,
    phase: 'off',
    ws: null,
    generation: 0,      // invalide les réponses d'une tentative abandonnée
    exitReceived: false,
    ctrl: false,
    alt: false,
    keepalive: null,
    fitTimer: null,
    openTimer: null,
    abort: null,
    observers: [],
    keyboardLocked: false,
    hudTimer: null,
    hudReady: false,    // pas d'affichage de taille pendant la mise en place
    bellTimer: null,
    audio: null         // AudioContext de la cloche sonore, créé au premier bip
  }

  /* ---------- Bandeau d'erreur, statut, notifications ---------- */

  function showError(message) {
    errorText.textContent = message
    errorBox.style.display = ''
    scheduleFit()
  }

  function hideError() {
    if (errorBox.style.display !== 'none') {
      errorBox.style.display = 'none'
      scheduleFit()
    }
  }

  function setPhase(phase) {
    state.phase = phase
    root.setAttribute('data-state', phase)
    if (phase === 'on') {
      statusEl.className = 'label label-success jeeterm-status'
      statusEl.textContent = '{{Connecté}}'
    } else if (phase === 'connecting') {
      statusEl.className = 'label label-warning jeeterm-status'
      statusEl.textContent = '{{Connexion…}}'
    } else {
      statusEl.className = 'label label-danger jeeterm-status'
      statusEl.textContent = '{{Déconnecté}}'
    }
    /* Reconnecter n'a de sens que déconnecté : cliqué pendant une session, il
       tuerait le shell en cours sans prévenir ; pendant une connexion, il en
       lancerait une seconde. */
    btReconnect.style.display = (phase === 'off') ? '' : 'none'
  }

  function notify(message, level) {
    if (typeof jeedomUtils !== 'object' || typeof jeedomUtils.showAlert !== 'function') {
      return
    }
    var options = { message: message, level: level || 'info', ttl: 3000 }
    /* En plein écran natif, seul le bloc plein écran est affiché : la
       notification doit y être rangée pour être visible. */
    if (isNativeFullscreen()) {
      options.attachTo = root
    }
    try {
      jeedomUtils.showAlert(options)
    } catch (e) { }
  }

  /* Un message d'erreur de Jeedom peut contenir du HTML (displayException).
     DOMParser ne charge ni n'exécute rien, contrairement à un innerHTML sur un
     élément détaché (un <img onerror> y part quand même). */
  function toPlainText(value) {
    if (value === null || value === undefined) {
      return ''
    }
    if (typeof value !== 'string') {
      try {
        value = JSON.stringify(value)
      } catch (e) {
        value = String(value)
      }
    }
    if (value.indexOf('<') === -1) {
      return value
    }
    try {
      var doc = new DOMParser().parseFromString(value, 'text/html')
      return (doc.body.textContent || '').trim()
    } catch (e) {
      return value.replace(/<[^>]*>/g, '')
    }
  }

  /* ---------- Thème ---------- */

  var THEME_DARK = {
    background: '#1e1e1e',
    foreground: '#d4d4d4',
    cursor: '#d4d4d4',
    cursorAccent: '#1e1e1e',
    selectionBackground: 'rgba(255, 255, 255, 0.28)',
    black: '#000000',
    red: '#cd3131',
    green: '#0dbc79',
    yellow: '#e5e510',
    blue: '#2472c8',
    magenta: '#bc3fbc',
    cyan: '#11a8cd',
    white: '#e5e5e5',
    brightBlack: '#666666',
    brightRed: '#f14c4c',
    brightGreen: '#23d18b',
    brightYellow: '#f5f543',
    brightBlue: '#3b8eea',
    brightMagenta: '#d670d6',
    brightCyan: '#29b8db',
    brightWhite: '#ffffff'
  }

  /* Palette claire : jaune et blanc assombris, sinon illisibles sur fond blanc
     (ls colore en jaune, beaucoup d'outils écrivent en blanc). */
  var THEME_LIGHT = {
    background: '#fafafa',
    foreground: '#1e1e1e',
    cursor: '#1e1e1e',
    cursorAccent: '#fafafa',
    selectionBackground: 'rgba(0, 0, 0, 0.18)',
    black: '#000000',
    red: '#cd3131',
    green: '#00a86b',
    yellow: '#949800',
    blue: '#0451a5',
    magenta: '#bc05bc',
    cyan: '#0598bc',
    white: '#555555',
    brightBlack: '#666666',
    brightRed: '#cd3131',
    brightGreen: '#14ce14',
    brightYellow: '#b5ba00',
    brightBlue: '#0451a5',
    brightMagenta: '#bc05bc',
    brightCyan: '#0598bc',
    brightWhite: '#a5a5a5'
  }

  /* Thèmes au choix (réglage « Thème »). Chaque entrée : dark (pour les
     couleurs de la recherche et du plein écran) et une palette xterm
     complète. cursorAccent = fond : c'est la couleur du caractère sous un
     curseur bloc. */
  function palette(bg, fg, cursor, selection, ansi) {
    var names = ['black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white',
      'brightBlack', 'brightRed', 'brightGreen', 'brightYellow', 'brightBlue', 'brightMagenta', 'brightCyan', 'brightWhite']
    var colors = {
      background: bg,
      foreground: fg,
      cursor: cursor,
      cursorAccent: bg,
      selectionBackground: selection
    }
    for (var i = 0; i < names.length; i++) {
      colors[names[i]] = ansi[i]
    }
    return colors
  }

  var THEMES = {
    /* Noir « xterm » : fond noir pur, texte gris clair, palette classique
       (bleu éclairci : le #0000ee d'origine est illisible sur du noir). */
    black: {
      dark: true,
      colors: palette('#000000', '#d0d0d0', '#d0d0d0', 'rgba(255, 255, 255, 0.3)', [
        '#000000', '#cd0000', '#00cd00', '#cdcd00', '#3a6cff', '#cd00cd', '#00cdcd', '#e5e5e5',
        '#7f7f7f', '#ff3b3b', '#00ff00', '#ffff00', '#6c8cff', '#ff00ff', '#00ffff', '#ffffff'])
    },
    /* Moniteur monochrome à phosphore vert (P1) : toutes les couleurs sont
       des nuances de vert, comme sur l'écran d'origine. */
    green: {
      dark: true,
      colors: palette('#010a01', '#33ff66', '#66ff8c', 'rgba(51, 255, 102, 0.3)', [
        '#0b3d16', '#1fbf4a', '#33ff66', '#8cff9f', '#18a33c', '#4fdf73', '#7dffa0', '#b8ffc8',
        '#2a7a3d', '#39e062', '#66ff8c', '#b3ffc0', '#2bd158', '#78f59a', '#a6ffbd', '#e6ffec'])
    },
    /* Moniteur monochrome ambre (P3). */
    amber: {
      dark: true,
      colors: palette('#0c0700', '#ffb000', '#ffc640', 'rgba(255, 176, 0, 0.3)', [
        '#3d2a00', '#d98c00', '#ffb000', '#ffd060', '#b37400', '#e89f1c', '#ffc640', '#ffe0a0',
        '#7a5500', '#ffa31a', '#ffc433', '#ffe08a', '#d68f00', '#ffb84d', '#ffd480', '#fff0cc'])
    },
    dracula: {
      dark: true,
      colors: palette('#282a36', '#f8f8f2', '#f8f8f2', 'rgba(98, 114, 164, 0.5)', [
        '#21222c', '#ff5555', '#50fa7b', '#f1fa8c', '#bd93f9', '#ff79c6', '#8be9fd', '#f8f8f2',
        '#6272a4', '#ff6e6e', '#69ff94', '#ffffa5', '#d6acff', '#ff92df', '#a4ffff', '#ffffff'])
    },
    /* Solarized sombre ; les couleurs vives sont des teintes éclaircies et
       non les gris de la palette d'origine : xterm affiche le gras en
       couleur vive, et ls écrirait ses dossiers en gris. */
    solarized: {
      dark: true,
      colors: palette('#002b36', '#93a1a1', '#93a1a1', 'rgba(147, 161, 161, 0.25)', [
        '#073642', '#dc322f', '#859900', '#b58900', '#268bd2', '#d33682', '#2aa198', '#eee8d5',
        '#586e75', '#f0514b', '#a3b72a', '#d3a51c', '#4aa3e6', '#6c71c4', '#45c1b5', '#fdf6e3'])
    },
    monokai: {
      dark: true,
      colors: palette('#272822', '#f8f8f2', '#f8f8f0', 'rgba(117, 113, 94, 0.55)', [
        '#272822', '#f92672', '#a6e22e', '#f4bf75', '#66d9ef', '#ae81ff', '#a1efe4', '#f8f8f2',
        '#75715e', '#ff5c93', '#b8f04a', '#ffd08a', '#8ae3f5', '#c4a3ff', '#bff5ee', '#f9f8f5'])
    },
    nord: {
      dark: true,
      colors: palette('#2e3440', '#d8dee9', '#d8dee9', 'rgba(136, 192, 208, 0.25)', [
        '#3b4252', '#bf616a', '#a3be8c', '#ebcb8b', '#81a1c1', '#b48ead', '#88c0d0', '#e5e9f0',
        '#4c566a', '#d57780', '#b4cf9d', '#f0d399', '#94b3d1', '#c7a1c0', '#8fbcbb', '#eceff4'])
    },
    /* Tango sur fond blanc : blanc et jaune vifs assombris, sinon
       illisibles. */
    tango: {
      dark: false,
      colors: palette('#ffffff', '#2e3436', '#2e3436', 'rgba(52, 101, 164, 0.25)', [
        '#2e3436', '#cc0000', '#4e9a06', '#a68a00', '#3465a4', '#75507b', '#06989a', '#6b6f68',
        '#555753', '#ef2929', '#5fae1e', '#b39a00', '#4a7fc1', '#ad7fa8', '#0fb3b5', '#8a8e87'])
    }
  }

  /* Polices : de simples listes font-family, rien n'est téléchargé. Chaque
     choix retombe sur la liste par défaut si la police manque. probe : noms
     cherchés pour marquer « absente » dans la liste. */
  var FONT_DEFAULT_STACK = '"DejaVu Sans Mono", "Cascadia Mono", Menlo, Consolas, "Liberation Mono", "Courier New", monospace'
  var FONTS = {
    'default': { stack: FONT_DEFAULT_STACK, probe: [] },
    dejavu: { stack: '"DejaVu Sans Mono", ' + FONT_DEFAULT_STACK, probe: ['DejaVu Sans Mono'] },
    liberation: { stack: '"Liberation Mono", ' + FONT_DEFAULT_STACK, probe: ['Liberation Mono'] },
    ubuntu: { stack: '"Ubuntu Mono", ' + FONT_DEFAULT_STACK, probe: ['Ubuntu Mono'] },
    fira: { stack: '"Fira Code", "Fira Mono", ' + FONT_DEFAULT_STACK, probe: ['Fira Code', 'Fira Mono'] },
    jetbrains: { stack: '"JetBrains Mono", ' + FONT_DEFAULT_STACK, probe: ['JetBrains Mono'] },
    cascadia: { stack: '"Cascadia Code", "Cascadia Mono", ' + FONT_DEFAULT_STACK, probe: ['Cascadia Code', 'Cascadia Mono'] },
    source: { stack: '"Source Code Pro", ' + FONT_DEFAULT_STACK, probe: ['Source Code Pro'] },
    hack: { stack: 'Hack, ' + FONT_DEFAULT_STACK, probe: ['Hack'] },
    consolas: { stack: 'Consolas, Menlo, Monaco, ' + FONT_DEFAULT_STACK, probe: ['Consolas', 'Menlo', 'Monaco'] },
    courier: { stack: '"Courier New", Courier, ' + FONT_DEFAULT_STACK, probe: ['Courier New', 'Courier'] }
  }

  /* Réglages : valeur par défaut, et valeurs admises pour les réglages à
     choix (une valeur inconnue, venue d'une autre version ou d'un
     localStorage abîmé, reprend la valeur par défaut). */
  var SETTING_DEFAULTS = {
    theme: 'auto',
    crt: false,
    font: 'default',
    lineHeight: '1',
    cursorStyle: 'block',
    cursorBlink: true,
    bell: 'visual',
    copyOnSelect: false
  }
  var SETTING_CHOICES = {
    theme: ['auto'].concat(Object.keys(THEMES)),
    font: Object.keys(FONTS),
    lineHeight: ['1', '1.1', '1.2', '1.35'],
    cursorStyle: ['block', 'bar', 'underline'],
    bell: ['visual', 'sound', 'none']
  }

  /* body[data-theme] vaut core2019_Dark, core2019_Light ou core2019_Legacy
     (posé par jeedomUtils.setJeedomTheme / switchTheme / checkThemechange,
     desktop/common/js/utils.js). Thème « auto » : on le suit ; un thème
     choisi s'impose. */
  function currentTheme() {
    var chosen = THEMES[settings.theme]
    if (chosen !== undefined) {
      return chosen
    }
    var name = document.body.getAttribute('data-theme') || ''
    return /dark/i.test(name) ? { dark: true, colors: THEME_DARK } : { dark: false, colors: THEME_LIGHT }
  }

  /* ---------- Préférences locales ---------- */

  function readPref(key) {
    try {
      return window.localStorage.getItem(key)
    } catch (e) {
      return null
    }
  }

  function writePref(key, value) {
    try {
      window.localStorage.setItem(key, String(value))
    } catch (e) { }
  }

  function readJson(key) {
    var raw = readPref(key)
    if (raw === null) {
      return null
    }
    try {
      return JSON.parse(raw)
    } catch (e) {
      return null
    }
  }

  function loadSettings() {
    var stored = readJson(SETTINGS_KEY)
    var out = {}
    Object.keys(SETTING_DEFAULTS).forEach(function (key) {
      var fallback = SETTING_DEFAULTS[key]
      var value = (stored !== null && typeof stored === 'object') ? stored[key] : undefined
      if (typeof fallback === 'boolean') {
        out[key] = (typeof value === 'boolean') ? value : fallback
      } else {
        value = (value === undefined || value === null) ? '' : String(value)
        out[key] = (SETTING_CHOICES[key].indexOf(value) !== -1) ? value : fallback
      }
    })
    return out
  }

  function saveSettings() {
    writePref(SETTINGS_KEY, JSON.stringify(settings))
  }

  var settings = loadSettings()

  function initialFontSize() {
    var value = parseInt(readPref(FONT_KEY), 10)
    return (value >= FONT_MIN && value <= FONT_MAX) ? value : FONT_DEFAULT
  }

  function isTouchDevice() {
    try {
      return window.matchMedia('(pointer: coarse)').matches
    } catch (e) {
      return false
    }
  }

  /* ---------- Bibliothèques ----------
     xterm.js (UMD) copie ses exports dans globalThis : window.Terminal est la
     classe. Les modules exposent un espace de noms : window.FitAddon.FitAddon. */
  var TerminalClass = window.Terminal
  var FitAddonClass = window.FitAddon && window.FitAddon.FitAddon
  var WebLinksAddonClass = window.WebLinksAddon && window.WebLinksAddon.WebLinksAddon
  var SearchAddonClass = window.SearchAddon && window.SearchAddon.SearchAddon
  var Unicode11AddonClass = window.Unicode11Addon && window.Unicode11Addon.Unicode11Addon

  if (typeof TerminalClass !== 'function' || typeof FitAddonClass !== 'function') {
    setPhase('off')
    btReconnect.style.display = 'none'
    showError("{{La bibliothèque xterm.js n'a pas pu être chargée. Rechargez la page (Ctrl+F5) ; si le problème persiste, réinstallez le plugin.}}")
    return
  }

  /* ---------- Terminal ---------- */

  var fontSize = initialFontSize()
  var themeInfo = currentTheme()
  var theme = themeInfo.colors
  var term = new TerminalClass({
    /* term.unicode (module unicode11) et registerDecoration (surlignage de la
       recherche) sont des API « proposées » d'xterm : sans cette option, elles
       lèvent une exception. */
    allowProposedApi: true,
    cursorBlink: settings.cursorBlink,
    cursorStyle: settings.cursorStyle,
    fontFamily: FONTS[settings.font].stack,
    fontSize: fontSize,
    lineHeight: parseFloat(settings.lineHeight),
    scrollback: 5000,
    macOptionIsMeta: true,
    theme: theme
  })
  var fitAddon = new FitAddonClass()
  term.loadAddon(fitAddon)
  if (typeof WebLinksAddonClass === 'function') {
    /* Gestionnaire par défaut du module : window.open() puis opener = null
       avant de charger l'adresse, l'onglet ouvert n'a donc aucune prise sur
       cette page. */
    try {
      term.loadAddon(new WebLinksAddonClass())
    } catch (e) { }
  }

  /* Largeurs Unicode 11 : émojis et idéogrammes sur deux colonnes, comme les
     compte le shell ; sinon le curseur se décale après un émoji. */
  if (typeof Unicode11AddonClass === 'function') {
    try {
      term.loadAddon(new Unicode11AddonClass())
      term.unicode.activeVersion = '11'
    } catch (e) { }
  }
  var searchAddon = null
  if (typeof SearchAddonClass === 'function') {
    try {
      searchAddon = new SearchAddonClass()
      term.loadAddon(searchAddon)
    } catch (e) {
      searchAddon = null
    }
  }

  fontSizeEl.textContent = String(fontSize)
  fontSizeEl2.textContent = String(fontSize)

  /* Le fond du bloc suit celui du thème (marge intérieure comprise) ; en
     plein écran, tout le bloc le prend quand un thème est choisi (variable
     --jeeterm-screen-bg, voir jeeterm.css). La barre d'outils garde le style
     de Jeedom. */
  function paintTheme() {
    host.style.backgroundColor = theme.background
    root.style.setProperty('--jeeterm-screen-bg', theme.background)
    root.classList.toggle('jeeterm-themed', settings.theme !== 'auto')
    root.setAttribute('data-scheme', themeInfo.dark ? 'dark' : 'light')
  }
  paintTheme()

  function applyTheme() {
    if (state.destroyed) {
      return
    }
    themeInfo = currentTheme()
    theme = themeInfo.colors
    term.options.theme = theme
    paintTheme()
  }

  function setOption(name, value) {
    if (term.options[name] !== value) {
      term.options[name] = value
    }
  }

  /* Applique tous les réglages d'affichage, sans toucher à la session. */
  function applySettings() {
    if (state.destroyed) {
      return
    }
    applyTheme()
    setOption('cursorStyle', settings.cursorStyle)
    setOption('cursorBlink', settings.cursorBlink)
    setOption('fontFamily', FONTS[settings.font].stack)
    setOption('lineHeight', parseFloat(settings.lineHeight))
    root.classList.toggle('jeeterm-crt', settings.crt)
    syncSettingsForm()
    /* Police et interligne changent la taille des cellules : colonnes et
       lignes sont recalculées (et le démon prévenu, via onResize). */
    scheduleFit()
  }

  /* ---------- Taille ---------- */

  function isNativeFullscreen() {
    return (document.fullscreenElement || document.webkitFullscreenElement) === root
  }

  function isFullscreen() {
    return isNativeFullscreen() || root.classList.contains('jeeterm-pseudo-fullscreen')
  }

  /* Hauteur du bloc = ce qui reste de la fenêtre sous sa position. La barre de
     Jeedom n'a pas une hauteur fixe (menu replié, mobile), d'où le calcul. La
     position est prise hors défilement de #div_mainContainer, sinon une page
     défilée donnerait un bloc trop haut. */
  function layout() {
    if (state.destroyed || !root.isConnected) {
      return
    }
    if (isFullscreen()) {
      root.style.height = ''
      return
    }
    var viewport = window.innerHeight
    if (window.visualViewport && window.visualViewport.height > 0) {
      viewport = Math.min(viewport, window.visualViewport.height)
    }
    var top = root.getBoundingClientRect().top
    var main = document.getElementById('div_mainContainer')
    if (main !== null && main.contains(root)) {
      top += main.scrollTop
    }
    root.style.height = Math.max(240, Math.floor(viewport - top - 10)) + 'px'
  }

  function doFit() {
    if (state.fitTimer !== null) {
      clearTimeout(state.fitTimer)
      state.fitTimer = null
    }
    /* Bloc masqué (onglet en arrière-plan, élément détaché) : fit() calculerait
       une taille nulle. On garde la précédente ; ResizeObserver rappellera. */
    if (state.destroyed || !root.isConnected || host.clientWidth === 0 || host.clientHeight === 0) {
      return
    }
    try {
      /* fit() ne déclenche term.onResize que si la taille change : c'est là
         que part le message {"t":"r"}. */
      fitAddon.fit()
    } catch (e) { }
  }

  function scheduleFit() {
    if (state.destroyed) {
      return
    }
    if (state.fitTimer !== null) {
      clearTimeout(state.fitTimer)
    }
    state.fitTimer = setTimeout(doFit, FIT_DEBOUNCE_MS)
  }

  /* ---------- WebSocket ---------- */

  function sendJson(message) {
    var ws = state.ws
    if (ws !== null && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify(message))
        return true
      } catch (e) { }
    }
    return false
  }

  /* Message local, en gris, pour ne pas le confondre avec la sortie du shell.
     Le texte peut venir du démon : les caractères de contrôle sont retirés
     pour qu'il ne puisse pas piloter le terminal. */
  function writeNotice(text) {
    var clean = String(text).replace(/[\x00-\x1f\x7f-\x9f]/g, ' ')
    term.write('\r\n\x1b[0m\x1b[2m' + clean + '\x1b[0m\r\n')
  }

  function writeReconnectHint() {
    term.write('\x1b[0m\x1b[1m{{Appuyez sur Entrée pour ouvrir une nouvelle session.}}\x1b[0m\r\n')
  }

  function startKeepalive() {
    stopKeepalive()
    state.keepalive = setInterval(function () {
      if (!root.isConnected) {
        destroy()
        return
      }
      sendJson({ t: 'p' })
    }, KEEPALIVE_MS)
  }

  function stopKeepalive() {
    if (state.keepalive !== null) {
      clearInterval(state.keepalive)
      state.keepalive = null
    }
  }

  function clearOpenTimer() {
    if (state.openTimer !== null) {
      clearTimeout(state.openTimer)
      state.openTimer = null
    }
  }

  /* Abandonne tout : demande de jeton en vol, WebSocket ouvert ou en cours
     d'ouverture. Le WebSocket est détaché de ses gestionnaires avant d'être
     fermé : aucun onclose tardif ne peut venir toucher l'état. */
  function closeSocket() {
    if (state.abort !== null) {
      try {
        state.abort.abort()
      } catch (e) { }
      state.abort = null
    }
    clearOpenTimer()
    stopKeepalive()
    var ws = state.ws
    state.ws = null
    if (ws !== null) {
      ws.onopen = ws.onmessage = ws.onerror = ws.onclose = null
      try {
        ws.close(1000, 'page')
      } catch (e) { }
    }
  }

  function requestSession() {
    var body = new URLSearchParams()
    body.append('action', 'createSession')
    var controller = (typeof AbortController === 'function') ? new AbortController() : null
    state.abort = controller
    var timedOut = false
    var timer = setTimeout(function () {
      timedOut = true
      if (controller !== null) {
        controller.abort()
      }
    }, REQUEST_TIMEOUT_MS)
    return fetch(AJAX_URL, {
      method: 'POST',
      body: body,
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      signal: controller !== null ? controller.signal : undefined
    }).then(function (response) {
      return response.text().then(function (text) {
        return { response: response, text: text }
      })
    }).then(function (reply) {
      clearTimeout(timer)
      return reply
    }, function () {
      clearTimeout(timer)
      throw new Error(timedOut
        ? '{{Le serveur Jeedom ne répond pas (délai dépassé).}}'
        : '{{Serveur Jeedom injoignable.}}')
    }).then(function (reply) {
      var response = reply.response
      var data = null
      try {
        data = JSON.parse(reply.text)
      } catch (e) { }
      if (data === null || typeof data !== 'object') {
        throw new Error('{{Réponse inattendue du serveur}} (HTTP ' + response.status + ')')
      }
      if (data.state !== 'ok') {
        throw new Error(toPlainText(data.result) || '{{Erreur inconnue du serveur.}}')
      }
      var result = data.result || {}
      if (typeof result.token !== 'string' || result.token === '') {
        throw new Error('{{Réponse du serveur incomplète : jeton absent.}}')
      }
      return {
        token: result.token,
        path: (typeof result.path === 'string' && result.path !== '') ? result.path : DEFAULT_WS_PATH
      }
    })
  }

  function socketUrl(session) {
    /* Chemin relatif à la page : Jeedom peut être servi sous un sous-dossier. */
    var url = new URL(session.path, window.location.href)
    url.protocol = (window.location.protocol === 'https:') ? 'wss:' : 'ws:'
    url.hash = ''
    url.searchParams.set('t', session.token)
    url.searchParams.set('c', String(term.cols))
    url.searchParams.set('r', String(term.rows))
    return url.toString()
  }

  /* Échec avant toute session : bandeau explicite, et dans le terminal une
     ligne qui dit comment réessayer. Jamais de nouvel essai automatique. */
  function failConnect(message) {
    closeSocket()
    setPhase('off')
    showError(message)
    writeNotice('[{{Connexion impossible}}]')
    writeReconnectHint()
  }

  function connect() {
    if (state.destroyed || state.phase === 'connecting') {
      return
    }
    closeSocket()
    var generation = ++state.generation
    state.exitReceived = false
    releaseModifiers()
    hideError()
    setTitle('')
    setPhase('connecting')
    doFit() // le jeton ne vit que 30 s : la taille est prise avant, pas après

    requestSession().then(function (session) {
      if (state.destroyed || generation !== state.generation) {
        return
      }
      state.abort = null
      openSocket(session, generation)
    }).catch(function (error) {
      if (state.destroyed || generation !== state.generation) {
        return
      }
      state.abort = null
      failConnect((error && error.message) ? error.message : String(error))
    })
  }

  var OPEN_FAILED = "{{Le terminal n'a pas pu joindre le démon JeeTerm. Vérifiez sur la page de configuration du plugin que le démon est démarré et que les dépendances sont installées (elles configurent le relais WebSocket d'Apache).}}"

  /* Le navigateur ne dit rien d'un refus avant l'upgrade (code 1006, quelle
     que soit la cause : 403, 404, 502, 503). Le serveur, lui, peut vérifier
     le démon et le relais Apache : action « diagnose », appelée une seule
     fois par échec, sans nouvel essai de connexion. */
  function diagnose(generation) {
    var body = new URLSearchParams()
    body.append('action', 'diagnose')
    fetch(AJAX_URL, {
      method: 'POST',
      body: body,
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'X-Requested-With': 'XMLHttpRequest' }
    }).then(function (response) {
      return response.json()
    }).then(function (data) {
      if (state.destroyed || generation !== state.generation || state.phase !== 'off') {
        return
      }
      var message = (data && data.state === 'ok' && data.result) ? toPlainText(data.result.message) : ''
      if (message !== '') {
        showError(message)
      }
    }).catch(function () { })
  }

  function failOpen(generation) {
    failConnect(OPEN_FAILED)
    diagnose(generation)
  }

  function openSocket(session, generation) {
    var ws
    try {
      ws = new WebSocket(socketUrl(session))
    } catch (e) {
      failConnect('{{Adresse du terminal invalide :}} ' + e.message)
      return
    }
    ws.binaryType = 'arraybuffer'
    state.ws = ws
    var opened = false

    /* Un relais qui ne répond jamais laisserait « Connexion… » affiché sans
       fin, bouton Reconnecter masqué. */
    state.openTimer = setTimeout(function () {
      state.openTimer = null
      if (ws === state.ws && !opened) {
        failOpen(generation)
      }
    }, OPEN_TIMEOUT_MS)

    ws.onopen = function () {
      if (ws !== state.ws || generation !== state.generation) {
        return
      }
      opened = true
      clearOpenTimer()
      setPhase('on')
      hideError()
      startKeepalive()
      /* La taille a pu changer pendant la poignée de main. */
      doFit()
      sendJson({ t: 'r', c: term.cols, r: term.rows })
      term.focus()
    }

    ws.onmessage = function (event) {
      if (ws !== state.ws) {
        return
      }
      if (typeof event.data === 'string') {
        handleControl(event.data)
      } else {
        term.write(new Uint8Array(event.data))
      }
    }

    /* Toujours suivi de onclose, qui fait le travail. */
    ws.onerror = function () { }

    ws.onclose = function (event) {
      if (ws !== state.ws) {
        return
      }
      state.ws = null
      clearOpenTimer()
      stopKeepalive()
      if (!opened) {
        /* Refus avant l'upgrade (403 du démon, 502/503 d'Apache) : le
           navigateur ne donne aucun détail, seulement le code 1006. */
        failOpen(generation)
        return
      }
      setPhase('off')
      if (!state.exitReceived) {
        writeNotice('[{{Connexion perdue}}' + (event.code && event.code !== 1005 ? ' (code ' + event.code + ')' : '') + ']')
      }
      writeReconnectHint()
    }
  }

  /* Fin de session envoyée par le démon (jeetermd.py, Session.run) :
     {"t":"x","code":N,"reason":"<français>","why":"exit|idle|shutdown|lost|error|closed"}.
     « why » est fait pour la machine, « reason » pour un humain francophone :
     on traduit via « why », et on retombe sur « reason » pour une valeur
     inconnue (démon plus récent que la page). */
  function describeEnd(message) {
    var reason = (typeof message.reason === 'string') ? message.reason : ''
    var code = message.code
    switch (message.why) {
      case 'exit':
        return "{{Le shell s'est terminé}}" + ((typeof code === 'number' && code > 0) ? ' (code ' + code + ')' : '') + '.'
      case 'idle':
        return '{{Session fermée après une période sans saisie au clavier.}}'
      case 'shutdown':
        return "{{Le démon JeeTerm s'est arrêté (redémarrage ou modification de la configuration).}}"
      case 'lost':
        return '{{Connexion perdue.}}'
      case 'closed':
        return '{{Connexion fermée.}}'
      case 'error':
        return reason !== '' ? reason : '{{Erreur du démon.}}'
    }
    return reason !== '' ? reason : ('code ' + code)
  }

  function handleControl(text) {
    var message
    try {
      message = JSON.parse(text)
    } catch (e) {
      return
    }
    if (!message || message.t !== 'x') {
      return
    }
    state.exitReceived = true
    var description = describeEnd(message)
    writeNotice('[{{Session terminée :}} ' + description + ']')
    /* Le shell n'a même pas pu être lancé (compte inexistant, sudo refusé…) :
       ce n'est pas une fin normale, le bandeau l'explique. Code -1 : démon
       antérieur au champ « why ». */
    if (message.why === 'error' || message.code === -1) {
      showError("{{Le shell n'a pas pu être lancé :}} " + description + ' ' + '{{Vérifiez le compte du shell dans la configuration du plugin.}}')
    }
  }

  /* ---------- Saisie et modificateurs collants ---------- */

  function setModifier(name, active) {
    state[name] = active
    var button = keysRow.querySelector('.jeeterm-key[data-key="' + name + '"]')
    if (button !== null) {
      button.classList.toggle('active', active)
      button.setAttribute('aria-pressed', active ? 'true' : 'false')
    }
  }

  function releaseModifiers() {
    if (state.ctrl) {
      setModifier('ctrl', false)
    }
    if (state.alt) {
      setModifier('alt', false)
    }
  }

  /* Ctrl + caractère : même calcul qu'un terminal matériel (code & 31).
     Couvre a-z, A-Z, @ [ \ ] ^ _ ; Ctrl+Espace donne NUL, Ctrl+? donne DEL.
     Les modificateurs sont relâchés après usage, quelle que soit la touche. */
  function applyModifiers(data) {
    var out = data
    if (state.ctrl && data.length === 1) {
      var code = data.charCodeAt(0)
      if (data === ' ' || data === '@') {
        out = '\x00'
      } else if (data === '?') {
        out = '\x7f'
      } else if ((code >= 0x41 && code <= 0x5f) || (code >= 0x61 && code <= 0x7a)) {
        out = String.fromCharCode(code & 31)
      }
    }
    if (state.alt) {
      out = '\x1b' + out
    }
    releaseModifiers()
    return out
  }

  function sendInput(data) {
    if (data !== '' && state.phase === 'on') {
      sendJson({ t: 'i', d: data })
    }
  }

  term.onData(function (data) {
    if (state.phase === 'on') {
      if (state.ctrl || state.alt) {
        data = applyModifiers(data)
      }
      sendInput(data)
      return
    }
    /* Déconnecté : Entrée ouvre une nouvelle session (comme le terminal de
       Home Assistant), le reste est ignoré. Pendant une connexion, rien. */
    if (state.phase === 'off' && data.indexOf('\r') !== -1) {
      releaseModifiers()
      writeNotice('[{{Reconnexion…}}]')
      connect()
    }
  })

  term.onResize(function (size) {
    sendJson({ t: 'r', c: size.cols, r: size.rows })
    if (state.hudReady) {
      hud(size.cols + ' × ' + size.rows)
    }
  })

  term.onTitleChange(setTitle)
  term.onBell(ringBell)

  /* Ctrl+Maj+C copie, Ctrl+Maj+V laisse le navigateur coller (xterm reçoit
     alors l'événement paste et gère lui-même le mode « bracketed paste »),
     Ctrl+Maj+F ouvre la recherche. Sans Maj, ces touches vont au shell. */
  term.attachCustomKeyEventHandler(function (event) {
    /* Panneau ouvert à la souris (le focus est resté au terminal) : Échap le
       ferme, comme tout menu, au lieu de partir au shell. Panneaux fermés,
       Échap va au shell comme toujours. */
    if (event.key === 'Escape' && !event.ctrlKey && !event.altKey && !event.metaKey && anyPanelOpen()) {
      if (event.type === 'keydown') {
        event.preventDefault()
        closePanels(false)
      }
      return false
    }
    if (event.ctrlKey && event.shiftKey && !event.altKey && !event.metaKey) {
      var key = (event.key || '').toLowerCase()
      if (key === 'c') {
        if (event.type === 'keydown') {
          event.preventDefault()
          copySelection()
        }
        return false
      }
      if (key === 'v') {
        return false
      }
      if (key === 'f' && searchAddon !== null) {
        if (event.type === 'keydown') {
          event.preventDefault()
          openSearch()
        }
        return false
      }
    }
    return true
  })

  var ARROWS = { up: 'A', down: 'B', right: 'C', left: 'D', home: 'H', end: 'F' }

  function specialKey(key, button) {
    if (key === 'ctrl' || key === 'alt') {
      setModifier(key, !state[key])
      return
    }
    if (state.phase !== 'on') {
      releaseModifiers()
      return
    }
    if (ARROWS[key] !== undefined) {
      var letter = ARROWS[key]
      var modifier = 1 + (state.alt ? 2 : 0) + (state.ctrl ? 4 : 0)
      releaseModifiers()
      if (modifier > 1) {
        sendInput('\x1b[1;' + modifier + letter)
      } else if (term.modes && term.modes.applicationCursorKeysMode) {
        sendInput('\x1bO' + letter)
      } else {
        sendInput('\x1b[' + letter)
      }
      return
    }
    if (key === 'esc') {
      sendInput(applyModifiers('\x1b'))
    } else if (key === 'tab') {
      sendInput(applyModifiers('\t'))
    } else if (key === 'text') {
      sendInput(applyModifiers(button.getAttribute('data-text') || ''))
    }
  }

  /* ---------- Presse-papiers ----------
     navigator.clipboard n'existe que dans un contexte sécurisé (https, ou
     http://localhost). Jeedom est souvent servi en http:// sur le réseau
     local : il faut alors un repli, execCommand('copy') pour copier, une zone
     de saisie pour coller. */

  function copyFallback(text) {
    var active = document.activeElement
    var area = document.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', '')
    area.style.position = 'fixed'
    area.style.top = '-1000px'
    area.style.opacity = '0'
    root.appendChild(area) // dans root : reste accessible en plein écran
    area.focus()
    area.select()
    try {
      area.setSelectionRange(0, text.length) // iOS
    } catch (e) { }
    var ok = false
    try {
      ok = document.execCommand('copy')
    } catch (e) { }
    root.removeChild(area)
    if (active && typeof active.focus === 'function') {
      try {
        active.focus()
      } catch (e) { }
    }
    return ok
  }

  /* Copie par l'API du presse-papiers, sinon par le repli. */
  function writeClipboard(text, done, failed) {
    var fallback = function () {
      if (copyFallback(text)) {
        done()
      } else {
        failed()
      }
    }
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function' && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, fallback)
    } else {
      fallback()
    }
  }

  function copySelection() {
    var text = term.getSelection()
    if (!text) {
      notify('{{Aucune sélection à copier : sélectionnez du texte dans le terminal.}}', 'warning')
      term.focus()
      return
    }
    writeClipboard(text, function () {
      notify('{{Sélection copiée.}}', 'success')
      term.focus()
    }, function () {
      notify('{{Copie refusée par le navigateur : utilisez le clic droit, Copier.}}', 'warning')
      term.focus()
    })
  }

  /* Copie à la sélection, comme les terminaux Linux : au relâchement du
     bouton de la souris (pas à chaque changement de sélection, qui tombe à
     chaque ligne parcourue pendant le glisser). Discrète : une bulle dans
     le coin du terminal, pas une notification. Le focus reste au terminal
     (copyFallback le lui rend). */
  function copyOnSelect() {
    if (!settings.copyOnSelect || state.destroyed) {
      return
    }
    var text = term.getSelection()
    if (!text) {
      return
    }
    writeClipboard(text, function () {
      hud('{{Copié}}')
    }, function () { })
  }

  function pasteText(text) {
    if (!text) {
      return
    }
    if (state.phase !== 'on') {
      notify("{{Le terminal n'est pas connecté.}}", 'warning')
      return
    }
    releaseModifiers()
    term.paste(text) // passe par onData, avec « bracketed paste » si le shell l'a demandé
  }

  function openPasteBox() {
    pasteArea.value = ''
    pasteBox.style.display = ''
    scheduleFit()
    pasteArea.focus()
  }

  function closePasteBox() {
    if (pasteBox.style.display !== 'none') {
      pasteBox.style.display = 'none'
      pasteArea.value = ''
      scheduleFit()
    }
    term.focus()
  }

  function sendPasteBox() {
    var text = pasteArea.value
    closePasteBox()
    pasteText(text)
  }

  function pasteClipboard() {
    if (navigator.clipboard && typeof navigator.clipboard.readText === 'function' && window.isSecureContext) {
      navigator.clipboard.readText().then(function (text) {
        if (text) {
          pasteText(text)
          term.focus()
        } else {
          openPasteBox()
        }
      }, openPasteBox) // permission refusée : la zone de collage prend le relais
    } else {
      openPasteBox()
    }
  }

  on(pasteArea, 'keydown', function (event) {
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      closePasteBox()
    } else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault()
      sendPasteBox()
    }
  })

  /* ---------- Recherche ---------- */

  function searchOptions(incremental) {
    var dark = themeInfo.dark
    return {
      caseSensitive: false,
      incremental: !!incremental,
      decorations: {
        matchBackground: dark ? '#5c4d00' : '#ffe68a',
        matchOverviewRuler: '#d4a200',
        activeMatchBackground: dark ? '#b36b00' : '#ffb454',
        activeMatchColorOverviewRuler: '#ff8c00'
      }
    }
  }

  function isSearchOpen() {
    return searchRow.style.display !== 'none'
  }

  function find(forward, incremental) {
    if (searchAddon === null) {
      return
    }
    var query = findInput.value
    if (query === '') {
      searchAddon.clearDecorations()
      term.clearSelection()
      searchCount.textContent = ''
      return
    }
    var found = false
    try {
      found = forward
        ? searchAddon.findNext(query, searchOptions(incremental))
        : searchAddon.findPrevious(query, searchOptions(false))
    } catch (e) { }
    if (!found) {
      searchCount.textContent = '{{Aucun résultat}}'
    }
  }

  function openSearch() {
    if (searchAddon === null) {
      return
    }
    searchRow.style.display = ''
    btSearch.classList.add('active')
    btSearch.setAttribute('aria-pressed', 'true')
    scheduleFit()
    var selection = term.getSelection()
    if (selection && selection.indexOf('\n') === -1) {
      findInput.value = selection
    }
    findInput.focus()
    findInput.select()
    if (findInput.value !== '') {
      find(true, true)
    }
  }

  function closeSearch() {
    if (isSearchOpen()) {
      searchRow.style.display = 'none'
      btSearch.classList.remove('active')
      btSearch.setAttribute('aria-pressed', 'false')
      searchCount.textContent = ''
      if (searchAddon !== null) {
        try {
          searchAddon.clearDecorations()
        } catch (e) { }
      }
      scheduleFit()
    }
    term.focus()
  }

  if (searchAddon === null) {
    btSearch.style.display = 'none'
  } else {
    try {
      searchAddon.onDidChangeResults(function (results) {
        if (!results || results.resultCount === 0) {
          searchCount.textContent = findInput.value !== '' ? '{{Aucun résultat}}' : ''
        } else if (results.resultIndex >= 0) {
          searchCount.textContent = (results.resultIndex + 1) + ' / ' + results.resultCount
        } else {
          searchCount.textContent = String(results.resultCount)
        }
      })
    } catch (e) { }
  }

  on(findInput, 'input', function () {
    find(true, true)
  })

  on(findInput, 'keydown', function (event) {
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      closeSearch()
    } else if (event.key === 'Enter') {
      event.preventDefault()
      find(!event.shiftKey, false)
    }
  })

  /* ---------- Plein écran ---------- */

  function updateFullscreenIcon() {
    var icon = btFullscreen.querySelector('i')
    var full = isFullscreen()
    if (icon !== null) {
      icon.className = full ? 'fas fa-compress' : 'fas fa-expand'
    }
    btFullscreen.setAttribute('title', full ? '{{Quitter le plein écran}}' : '{{Plein écran}}')
    btFullscreen.setAttribute('aria-label', full ? '{{Quitter le plein écran}}' : '{{Plein écran}}')
  }

  /* En plein écran natif, Échap le quitte : gênant dans vi ou less. Là où
     l'API Keyboard Lock existe (Chrome, Edge, en https), Échap est rendu au
     terminal et un appui long sur Échap quitte le plein écran. */
  function lockEscape() {
    if (navigator.keyboard && typeof navigator.keyboard.lock === 'function') {
      try {
        navigator.keyboard.lock(['Escape']).then(function () {
          state.keyboardLocked = true
        }, function () { })
      } catch (e) { }
    }
  }

  function unlockEscape() {
    if (state.keyboardLocked && navigator.keyboard && typeof navigator.keyboard.unlock === 'function') {
      try {
        navigator.keyboard.unlock()
      } catch (e) { }
    }
    state.keyboardLocked = false
  }

  function afterFullscreenChange() {
    if (state.destroyed) {
      return
    }
    if (isNativeFullscreen()) {
      lockEscape()
    } else {
      unlockEscape()
    }
    updateFullscreenIcon()
    layout()
    scheduleFit()
    repositionPanels()
    term.focus()
  }

  function exitNativeFullscreen() {
    var exit = document.exitFullscreen || document.webkitExitFullscreen
    if (exit) {
      try {
        var p = exit.call(document)
        if (p && typeof p.catch === 'function') {
          p.catch(function () { })
        }
      } catch (e) { }
    }
  }

  function exitFullscreen() {
    if (isNativeFullscreen()) {
      exitNativeFullscreen()
    } else if (root.classList.contains('jeeterm-pseudo-fullscreen')) {
      root.classList.remove('jeeterm-pseudo-fullscreen')
      afterFullscreenChange()
    }
  }

  /* API plein écran si elle existe (sur tout le bloc : la barre et les touches
     spéciales restent disponibles), sinon repli CSS (iPhone : Safari n'a pas
     requestFullscreen sur un élément autre qu'une vidéo). */
  function toggleFullscreen() {
    if (isFullscreen()) {
      exitFullscreen()
      return
    }
    var pseudo = function () {
      root.classList.add('jeeterm-pseudo-fullscreen')
      afterFullscreenChange()
    }
    var request = root.requestFullscreen || root.webkitRequestFullscreen
    if (!request) {
      pseudo()
      return
    }
    try {
      var promise = request.call(root)
      if (promise && typeof promise.catch === 'function') {
        promise.catch(pseudo)
      }
    } catch (e) {
      pseudo()
    }
  }

  /* ---------- Bulle d'information, titre, cloche ---------- */

  /* Bulle dans le coin du terminal : taille après un redimensionnement
     (comme tmux ou iTerm), « Copié »… Transparente à la souris. */
  function hud(text) {
    if (state.destroyed) {
      return
    }
    hudEl.textContent = text
    hudEl.classList.add('jeeterm-hud-on')
    if (state.hudTimer !== null) {
      clearTimeout(state.hudTimer)
    }
    state.hudTimer = setTimeout(function () {
      state.hudTimer = null
      hudEl.classList.remove('jeeterm-hud-on')
    }, HUD_MS)
  }

  /* Titre posé par le shell (OSC 0 / OSC 2, par exemple « user@hôte: ~ »).
     Il vient du shell, donc de n'importe quel programme lancé : textContent
     seulement, caractères de contrôle retirés, longueur bornée. */
  function setTitle(title) {
    var clean = String(title || '').replace(/[\x00-\x1f\x7f-\x9f]/g, ' ').trim().slice(0, 200)
    titleEl.textContent = clean
    titleEl.title = clean
  }

  /* Cloche (caractère BEL) : éclair sur le terminal, bip, ou rien. */
  function flashBell() {
    root.classList.remove('jeeterm-bell-on')
    void host.offsetWidth // relance l'animation si la cloche sonne en rafale
    root.classList.add('jeeterm-bell-on')
    if (state.bellTimer !== null) {
      clearTimeout(state.bellTimer)
    }
    state.bellTimer = setTimeout(function () {
      state.bellTimer = null
      root.classList.remove('jeeterm-bell-on')
    }, 250)
  }

  function beep() {
    var AudioContextClass = window.AudioContext || window.webkitAudioContext
    if (typeof AudioContextClass !== 'function') {
      flashBell()
      return
    }
    try {
      if (state.audio === null) {
        state.audio = new AudioContextClass()
      }
      var audio = state.audio
      if (audio.state === 'suspended' && typeof audio.resume === 'function') {
        audio.resume().catch(function () { })
      }
      var now = audio.currentTime
      var oscillator = audio.createOscillator()
      var gain = audio.createGain()
      oscillator.type = 'square'
      oscillator.frequency.value = 880
      gain.gain.setValueAtTime(0.04, now)
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.12)
      oscillator.connect(gain)
      gain.connect(audio.destination)
      oscillator.start(now)
      oscillator.stop(now + 0.13)
    } catch (e) { }
  }

  function ringBell() {
    if (settings.bell === 'visual') {
      flashBell()
    } else if (settings.bell === 'sound') {
      beep()
    }
  }

  /* ---------- Enregistrer, effacer ---------- */

  function pad2(n) {
    return (n < 10 ? '0' : '') + n
  }

  /* Tout le tampon (historique compris), une ligne logique par ligne : une
     ligne coupée par la largeur du terminal (isWrapped) est recollée à la
     précédente, espaces de fin gardés au point de coupure. */
  function bufferText() {
    var buffer = term.buffer.active
    var lines = []
    for (var i = 0; i < buffer.length; i++) {
      var line = buffer.getLine(i)
      if (!line) {
        continue
      }
      var next = buffer.getLine(i + 1)
      var text = line.translateToString(!(next && next.isWrapped))
      if (line.isWrapped && lines.length > 0) {
        lines[lines.length - 1] += text
      } else {
        lines.push(text)
      }
    }
    while (lines.length > 0 && lines[lines.length - 1].trim() === '') {
      lines.pop()
    }
    return lines.join('\n') + '\n'
  }

  function saveSession() {
    var now = new Date()
    var name = 'jeeterm-' + now.getFullYear() + pad2(now.getMonth() + 1) + pad2(now.getDate()) +
      '-' + pad2(now.getHours()) + pad2(now.getMinutes()) + pad2(now.getSeconds()) + '.txt'
    var url
    try {
      url = URL.createObjectURL(new Blob([bufferText()], { type: 'text/plain;charset=utf-8' }))
    } catch (e) {
      notify("{{Impossible d'enregistrer la session.}}", 'warning')
      return
    }
    /* Le lien est rangé dans le bloc le temps du clic (Firefox l'exige), mais
       le clic ne doit pas remonter à <body> : le cœur y charge en AJAX tout
       <a href> cliqué (desktop/common/js/utils.js, vers la ligne 1445). */
    var link = document.createElement('a')
    link.href = url
    link.download = name
    link.style.display = 'none'
    link.addEventListener('click', function (event) {
      event.stopPropagation()
    })
    root.appendChild(link)
    link.click()
    root.removeChild(link)
    setTimeout(function () {
      URL.revokeObjectURL(url)
    }, 30000)
    notify('{{Session enregistrée :}} ' + name, 'success')
  }

  /* Efface l'écran et l'historique ; la ligne du curseur (l'invite) reste. */
  function clearScreen() {
    term.clear()
  }

  /* ---------- Panneaux (réglages, commandes rapides) ----------
     Rangés dans le bloc principal (visibles en plein écran), placés sous leur
     bouton en position absolue : ils ne prennent pas de place au terminal. */

  var PANELS = [
    { panel: settingsPanel, button: btSettings },
    { panel: snippetsPanel, button: btSnippets }
  ]

  function panelOf(button) {
    for (var i = 0; i < PANELS.length; i++) {
      if (PANELS[i].button === button) {
        return PANELS[i]
      }
    }
    return null
  }

  function isPanelOpen(entry) {
    return entry.panel.style.display !== 'none'
  }

  function anyPanelOpen() {
    return PANELS.some(isPanelOpen)
  }

  function positionPanel(entry) {
    var rootBox = root.getBoundingClientRect()
    var buttonBox = entry.button.getBoundingClientRect()
    /* Bouton masqué (petit écran) : sous la barre, à droite. */
    var anchorBottom = buttonBox.height > 0 ? buttonBox.bottom : root.querySelector('.jeeterm-toolbar').getBoundingClientRect().bottom
    var anchorRight = buttonBox.width > 0 ? buttonBox.right : rootBox.right
    var top = Math.round(anchorBottom - rootBox.top + 4)
    var panel = entry.panel
    panel.style.top = top + 'px'
    panel.style.maxHeight = Math.max(160, root.clientHeight - top - 8) + 'px'
    var width = panel.offsetWidth
    var left = Math.round(anchorRight - rootBox.left - width)
    panel.style.left = Math.max(4, Math.min(left, root.clientWidth - width - 4)) + 'px'
  }

  function openPanel(entry, focusInside) {
    PANELS.forEach(function (other) {
      if (other !== entry && isPanelOpen(other)) {
        hidePanel(other)
      }
    })
    if (entry.panel === settingsPanel) {
      markMissingFonts()
      syncSettingsForm()
    } else {
      renderSnippets()
    }
    entry.panel.style.display = ''
    entry.button.classList.add('active')
    entry.button.setAttribute('aria-expanded', 'true')
    positionPanel(entry)
    if (focusInside) {
      var first = entry.panel.querySelector('select, input, button')
      if (first !== null) {
        first.focus()
      }
    }
  }

  function hidePanel(entry) {
    entry.panel.style.display = 'none'
    entry.button.classList.remove('active')
    entry.button.setAttribute('aria-expanded', 'false')
    if (entry.panel === snippetsPanel) {
      hideSnippetForm()
    }
  }

  /* refocus : rendre le clavier au terminal si le focus était dans le
     panneau (Échap, commande choisie). */
  function closePanels(refocus) {
    var hadFocus = false
    PANELS.forEach(function (entry) {
      if (isPanelOpen(entry)) {
        hadFocus = hadFocus || entry.panel.contains(document.activeElement)
        hidePanel(entry)
      }
    })
    if (refocus && hadFocus) {
      term.focus()
    }
  }

  function togglePanel(entry, focusInside) {
    if (isPanelOpen(entry)) {
      closePanels(true)
    } else {
      openPanel(entry, focusInside)
    }
  }

  function repositionPanels() {
    PANELS.forEach(function (entry) {
      if (isPanelOpen(entry)) {
        positionPanel(entry)
      }
    })
  }

  /* Clic ou toucher hors d'un panneau ouvert (terminal compris) : il se
     ferme. Son propre bouton est laissé au gestionnaire de la barre. */
  function onOutsidePointer(event) {
    PANELS.forEach(function (entry) {
      if (isPanelOpen(entry) && !entry.panel.contains(event.target) && !entry.button.contains(event.target)) {
        hidePanel(entry)
      }
    })
  }

  /* ---------- Réglages ---------- */

  function syncSettingsForm() {
    settingsPanel.querySelectorAll('[data-setting]').forEach(function (field) {
      var value = settings[field.getAttribute('data-setting')]
      if (field.type === 'checkbox') {
        field.checked = !!value
      } else {
        field.value = String(value)
      }
    })
  }

  /* Polices absentes marquées dans la liste (une seule fois) : même mesure
     que les détecteurs de polices classiques, la largeur d'un texte avec la
     police demandée puis une famille générique, comparée à la générique
     seule. */
  var fontsChecked = false
  function fontInstalled(names) {
    var context
    try {
      context = document.createElement('canvas').getContext('2d')
    } catch (e) {
      return true
    }
    if (!context) {
      return true
    }
    var sample = 'mmmmmmmmmwwwwwlli10OQ@#'
    var generics = ['monospace', 'serif', 'sans-serif']
    for (var i = 0; i < names.length; i++) {
      for (var j = 0; j < generics.length; j++) {
        context.font = '32px ' + generics[j]
        var base = context.measureText(sample).width
        context.font = '32px "' + names[i] + '", ' + generics[j]
        if (context.measureText(sample).width !== base) {
          return true
        }
      }
    }
    return false
  }

  function markMissingFonts() {
    if (fontsChecked) {
      return
    }
    fontsChecked = true
    var select = document.getElementById('sel_jeetermFont')
    Array.prototype.forEach.call(select.options, function (option) {
      var font = FONTS[option.value]
      if (font && font.probe.length > 0 && !fontInstalled(font.probe)) {
        option.textContent += ' — {{absente}}'
        option.className = 'jeeterm-font-missing'
      }
    })
  }

  function changeSetting(field) {
    var key = field.getAttribute('data-setting')
    if (!key || !Object.prototype.hasOwnProperty.call(SETTING_DEFAULTS, key)) {
      return
    }
    if (typeof SETTING_DEFAULTS[key] === 'boolean') {
      settings[key] = !!field.checked
    } else if (SETTING_CHOICES[key].indexOf(field.value) !== -1) {
      settings[key] = field.value
    }
    saveSettings()
    applySettings()
  }

  function resetSettings() {
    try {
      window.localStorage.removeItem(SETTINGS_KEY)
    } catch (e) { }
    settings = loadSettings()
    applySettings()
    setFontSize(FONT_DEFAULT)
  }

  /* ---------- Commandes rapides ----------
     Tapées dans le terminal comme un collage : rien n'est exécuté tant que
     l'utilisateur n'appuie pas sur Entrée, sauf pour une commande marquée
     « exécuter » (r: true). jlog, jcd et jinfo sont des fonctions du bashrc
     de JeeTerm. */

  var DEFAULT_SNIPPETS = [
    { l: "{{Suivre le journal d'un plugin (jlog <plugin>)}}", c: 'jlog ', r: false },
    { l: "{{Aller dans le dossier d'un plugin (jcd <plugin>)}}", c: 'jcd ', r: false },
    { l: '{{Résumé de Jeedom (jinfo)}}', c: 'jinfo', r: false },
    { l: '{{Erreurs Apache et PHP en direct}}', c: 'tail -f /var/www/html/log/http.error', r: false },
    { l: '{{Plugins installés}}', c: 'ls /var/www/html/plugins', r: false },
    { l: '{{Dossier de Jeedom}}', c: 'cd /var/www/html', r: false },
    { l: "{{État d'Apache}}", c: 'sudo systemctl status apache2', r: false },
    { l: '{{Journal du système en direct}}', c: 'sudo journalctl -f', r: false },
    { l: '{{Espace disque}}', c: 'df -h', r: false },
    { l: '{{Mémoire}}', c: 'free -h', r: false },
    { l: '{{Processus}}', c: 'htop || top', r: false }
  ]

  function copySnippets(list) {
    return list.map(function (item) {
      return { l: item.l, c: item.c, r: item.r }
    })
  }

  function loadSnippets() {
    var stored = readJson(SNIPPETS_KEY)
    if (!Array.isArray(stored)) {
      return copySnippets(DEFAULT_SNIPPETS)
    }
    var out = []
    stored.forEach(function (item) {
      if (out.length < SNIPPETS_MAX && item && typeof item.c === 'string' && item.c !== '' && item.c.length <= 1000) {
        out.push({ l: (typeof item.l === 'string') ? item.l.slice(0, 60) : '', c: item.c, r: item.r === true })
      }
    })
    return out
  }

  var snippets = loadSnippets()

  function saveSnippets() {
    writePref(SNIPPETS_KEY, JSON.stringify(snippets))
  }

  function renderSnippets() {
    snippetList.textContent = ''
    if (snippets.length === 0) {
      var empty = document.createElement('li')
      empty.className = 'jeeterm-hint'
      empty.textContent = '{{Aucune commande : ajoutez la vôtre.}}'
      snippetList.appendChild(empty)
      return
    }
    snippets.forEach(function (item, index) {
      var row = document.createElement('li')
      row.className = 'jeeterm-snippet'
      var run = document.createElement('button')
      run.type = 'button'
      run.className = 'jeeterm-snippet-run'
      run.setAttribute('data-action', 'snippetRun')
      run.setAttribute('data-index', String(index))
      run.title = item.r ? item.c + ' — {{exécutée aussitôt}}' : item.c
      var label = document.createElement('span')
      label.className = 'jeeterm-snippet-label'
      label.textContent = item.l !== '' ? item.l : item.c
      var command = document.createElement('code')
      command.className = 'jeeterm-snippet-cmd'
      command.textContent = item.c + (item.r ? ' ⏎' : '')
      run.appendChild(label)
      run.appendChild(command)
      var remove = document.createElement('button')
      remove.type = 'button'
      remove.className = 'jeeterm-snippet-del'
      remove.setAttribute('data-action', 'snippetDelete')
      remove.setAttribute('data-index', String(index))
      remove.title = '{{Retirer de la liste}}'
      remove.setAttribute('aria-label', '{{Retirer de la liste}}' + ' — ' + label.textContent)
      var icon = document.createElement('i')
      icon.className = 'fas fa-times'
      icon.setAttribute('aria-hidden', 'true')
      remove.appendChild(icon)
      row.appendChild(run)
      row.appendChild(remove)
      snippetList.appendChild(row)
    })
  }

  function runSnippet(index) {
    var item = snippets[index]
    closePanels(false)
    if (!item) {
      return
    }
    if (state.phase !== 'on') {
      notify("{{Le terminal n'est pas connecté.}}", 'warning')
      term.focus()
      return
    }
    releaseModifiers()
    /* Comme un collage : avec le « bracketed paste » de bash, la commande
       s'affiche en surbrillance et n'est pas exécutée. */
    term.paste(item.c)
    if (item.r) {
      sendInput('\r')
    }
    term.focus()
  }

  function showSnippetForm() {
    snippetForm.style.display = ''
    snippetLabel.value = ''
    snippetCmd.value = ''
    snippetRun.checked = false
    snippetCmd.classList.remove('jeeterm-invalid')
    positionPanel(PANELS[1])
    snippetLabel.focus()
  }

  function hideSnippetForm() {
    snippetForm.style.display = 'none'
  }

  function addSnippet() {
    var command = snippetCmd.value.replace(/[\r\n]+/g, ' ')
    if (command.trim() === '') {
      snippetCmd.classList.add('jeeterm-invalid')
      snippetCmd.focus()
      return
    }
    if (snippets.length >= SNIPPETS_MAX) {
      notify('{{Liste pleine : retirez une commande.}}', 'warning')
      return
    }
    snippets.push({ l: snippetLabel.value.trim().slice(0, 60), c: command.slice(0, 1000), r: snippetRun.checked })
    saveSnippets()
    hideSnippetForm()
    renderSnippets()
    positionPanel(PANELS[1])
    snippetsPanel.querySelector('.jeeterm-snippet:last-child .jeeterm-snippet-run').focus()
  }

  function deleteSnippet(index) {
    if (index >= 0 && index < snippets.length) {
      snippets.splice(index, 1)
      saveSnippets()
      renderSnippets()
    }
  }

  function resetSnippets() {
    try {
      window.localStorage.removeItem(SNIPPETS_KEY)
    } catch (e) { }
    snippets = copySnippets(DEFAULT_SNIPPETS)
    renderSnippets()
  }

  /* Boutons des panneaux (data-action). */
  function panelAction(action, button) {
    switch (action) {
      case 'fontMinus':
        setFontSize(fontSize - 1)
        break
      case 'fontPlus':
        setFontSize(fontSize + 1)
        break
      case 'save':
        closePanels(false)
        saveSession()
        break
      case 'clear':
        closePanels(true)
        clearScreen()
        break
      case 'config':
        closePanels(false)
        openConfig()
        break
      case 'resetSettings':
        resetSettings()
        break
      case 'snippetRun':
        runSnippet(parseInt(button.getAttribute('data-index'), 10))
        break
      case 'snippetDelete':
        deleteSnippet(parseInt(button.getAttribute('data-index'), 10))
        break
      case 'snippetAdd':
        showSnippetForm()
        break
      case 'snippetSave':
        addSnippet()
        break
      case 'snippetCancel':
        hideSnippetForm()
        break
      case 'snippetReset':
        resetSnippets()
        break
    }
  }

  /* ---------- Configuration du plugin ----------
     Comme le bouton « Configuration » des pages de plugin du cœur
     (core/js/plugin.template.js, gotoPluginConf) : dans une fenêtre modale,
     ce qui garde la session ouverte. */
  function openConfig() {
    exitFullscreen()
    if (typeof jeeDialog === 'object' && jeeDialog !== null && typeof jeeDialog.dialog === 'function') {
      try {
        jeeDialog.dialog({
          id: 'jee_modal',
          title: '{{Configuration du plugin}}',
          height: '85%',
          contentUrl: CONFIG_URL.replace('p=plugin', 'p=plugin&ajax=1')
        })
        return
      } catch (e) { }
    }
    if (typeof jeedomUtils === 'object' && typeof jeedomUtils.loadPage === 'function') {
      jeedomUtils.loadPage(CONFIG_URL)
    } else {
      window.location.href = CONFIG_URL
    }
  }

  /* ---------- Barre d'outils ---------- */

  function setFontSize(size) {
    size = Math.max(FONT_MIN, Math.min(FONT_MAX, size))
    fontSize = size
    term.options.fontSize = size
    fontSizeEl.textContent = String(size)
    fontSizeEl2.textContent = String(size)
    writePref(FONT_KEY, size)
    scheduleFit()
  }

  function setKeysVisible(visible, remember) {
    keysRow.style.display = visible ? '' : 'none'
    btToggleKeys.classList.toggle('active', visible)
    btToggleKeys.setAttribute('aria-pressed', visible ? 'true' : 'false')
    if (remember) {
      writePref(KEYS_KEY, visible ? '1' : '0')
    }
    if (!visible) {
      releaseModifiers()
    }
    scheduleFit()
  }

  /* mousedown sans action par défaut : le bouton ne prend pas le focus, le
     terminal le garde, sa sélection aussi (Copier), et sur mobile le clavier
     virtuel ne se referme pas à chaque touche spéciale. On note au passage si
     le terminal avait le focus : les boutons qui ne sont pas des touches ne
     le lui rendent que dans ce cas, pour ne pas ouvrir le clavier virtuel
     d'un téléphone à chaque changement de police. */
  var terminalHadFocus = false
  function terminalHasFocus() {
    return !!(term.textarea && document.activeElement === term.textarea)
  }

  var toolbar = root.querySelector('.jeeterm-toolbar')
  on(toolbar, 'mousedown', function (event) {
    if (event.target.closest('.btn') !== null) {
      terminalHadFocus = terminalHasFocus()
      event.preventDefault()
    }
  })
  on(toolbar, 'touchstart', function () {
    terminalHadFocus = terminalHasFocus()
  }, { passive: true })

  on(toolbar, 'click', function (event) {
    var button = event.target.closest('button.btn')
    if (button === null || button.disabled) {
      return
    }
    event.preventDefault()
    var refocus = terminalHadFocus || !isTouchDevice()
    terminalHadFocus = false
    if (button.classList.contains('jeeterm-key')) {
      specialKey(button.getAttribute('data-key'), button)
      term.focus()
      return
    }
    switch (button.id) {
      case 'bt_jeetermReconnect':
        writeNotice('[{{Reconnexion…}}]')
        connect()
        break
      case 'bt_jeetermFontMinus':
        setFontSize(fontSize - 1)
        break
      case 'bt_jeetermFontPlus':
        setFontSize(fontSize + 1)
        break
      case 'bt_jeetermCopy':
        copySelection()
        return
      case 'bt_jeetermPaste':
        pasteClipboard()
        return
      case 'bt_jeetermFullscreen':
        toggleFullscreen()
        return
      case 'bt_jeetermToggleKeys':
        setKeysVisible(keysRow.style.display === 'none', true)
        break
      case 'bt_jeetermConfig':
        openConfig()
        return
      case 'bt_jeetermSettings':
      case 'bt_jeetermSnippets':
        /* Au clavier (le bouton a le focus), le focus entre dans le panneau ;
           à la souris, il reste au terminal. */
        togglePanel(panelOf(button), document.activeElement === button)
        return
      case 'bt_jeetermSave':
        saveSession()
        break
      case 'bt_jeetermClear':
        clearScreen()
        break
      case 'bt_jeetermSearch':
        if (isSearchOpen()) {
          closeSearch()
        } else {
          openSearch()
        }
        return
      case 'bt_jeetermFindNext':
        find(true, false)
        return
      case 'bt_jeetermFindPrev':
        find(false, false)
        return
      case 'bt_jeetermFindClose':
        closeSearch()
        return
    }
    if (refocus) {
      term.focus()
    }
  })

  on(errorBox, 'click', function (event) {
    if (event.target.closest('#bt_jeetermErrorConfig') !== null) {
      event.preventDefault()
      openConfig()
    }
  })

  on(pasteBox, 'mousedown', function (event) {
    if (event.target.closest('.btn') !== null) {
      event.preventDefault()
    }
  })
  on(pasteBox, 'click', function (event) {
    var button = event.target.closest('button.btn')
    if (button === null) {
      return
    }
    event.preventDefault()
    if (button.id === 'bt_jeetermPasteSend') {
      sendPasteBox()
    } else if (button.id === 'bt_jeetermPasteCancel') {
      closePasteBox()
    }
  })

  PANELS.forEach(function (entry) {
    var panel = entry.panel
    /* Comme la barre : un bouton cliqué ne prend pas le focus (le terminal
       le garde, et le clavier virtuel d'un téléphone reste ouvert). Les
       listes et les champs, eux, le prennent normalement. */
    on(panel, 'mousedown', function (event) {
      if (event.target.closest('button') !== null) {
        event.preventDefault()
      }
    })
    on(panel, 'click', function (event) {
      var button = event.target.closest('button[data-action]')
      if (button === null || !panel.contains(button)) {
        return
      }
      event.preventDefault()
      panelAction(button.getAttribute('data-action'), button)
    })
    on(panel, 'change', function (event) {
      if (event.target.hasAttribute('data-setting')) {
        changeSetting(event.target)
      }
    })
    on(panel, 'keydown', function (event) {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        if (snippetForm.style.display !== 'none' && snippetForm.contains(event.target)) {
          hideSnippetForm()
          btSnippets.focus()
        } else {
          closePanels(true)
        }
      } else if (event.key === 'Enter' && snippetForm.contains(event.target) && event.target.tagName === 'INPUT' && event.target.type === 'text') {
        event.preventDefault()
        addSnippet()
      }
    })
  })
  on(snippetCmd, 'input', function () {
    snippetCmd.classList.remove('jeeterm-invalid')
  })
  on(document, 'mousedown', onOutsidePointer, true)
  on(document, 'touchstart', onOutsidePointer, { capture: true, passive: true })
  on(host, 'mouseup', function (event) {
    if (event.button === 0) {
      /* Après le traitement du relâchement par xterm (fin de sélection). */
      setTimeout(copyOnSelect, 0)
    }
  })

  /* ---------- Départ de la page ---------- */

  function destroy() {
    if (state.destroyed) {
      return
    }
    state.destroyed = true
    state.generation++
    closeSocket()
    if (state.fitTimer !== null) {
      clearTimeout(state.fitTimer)
      state.fitTimer = null
    }
    if (state.hudTimer !== null) {
      clearTimeout(state.hudTimer)
      state.hudTimer = null
    }
    if (state.bellTimer !== null) {
      clearTimeout(state.bellTimer)
      state.bellTimer = null
    }
    if (state.audio !== null) {
      try {
        state.audio.close()
      } catch (e) { }
      state.audio = null
    }
    state.observers.forEach(function (observer) {
      try {
        observer.disconnect()
      } catch (e) { }
    })
    state.observers = []
    listeners.forEach(function (l) {
      try {
        l.target.removeEventListener(l.type, l.fn, l.options)
      } catch (e) { }
    })
    listeners = []
    unlockEscape()
    if (isNativeFullscreen()) {
      exitNativeFullscreen()
    }
    try {
      term.dispose()
    } catch (e) { }
    if (window.jeetermInstance === instance) {
      window.jeetermInstance = null
    }
  }

  /* Jeedom 4.6 n'émet aucun événement « page quittée ». Trois filets :
     1. un MutationObserver sur #div_mainContainer (enfants directs seulement,
        la sortie du terminal ne le réveille donc pas) : loadPage y remplace
        #div_pageContainer par un clone (desktop/common/js/utils.js, vers la
        ligne 178) — détection immédiate ;
     2. l'événement « jeedom_page_load » déclenché sur body quand la page
        suivante est chargée (utils.js, vers la ligne 200) ;
     3. la vérification de l'intervalle de maintien (root.isConnected), au cas
        où le chargement suivant échouerait avant 1 et 2.
     Plus pagehide pour un vrai déchargement du navigateur (rechargement,
     fermeture de l'onglet, mise en cache « bfcache »). */
  function checkStillHere() {
    if (!root.isConnected) {
      destroy()
    }
  }

  function watchPageLeave() {
    var main = document.getElementById('div_mainContainer')
    if (main !== null && typeof MutationObserver === 'function') {
      var observer = new MutationObserver(checkStillHere)
      observer.observe(main, { childList: true })
      state.observers.push(observer)
    }
    /* Le parent direct aussi : si un cœur futur vidait #div_pageContainer
       sans le remplacer, le filet 1 ne verrait rien. */
    if (root.parentNode !== null && root.parentNode !== main && typeof MutationObserver === 'function') {
      var parentObserver = new MutationObserver(checkStillHere)
      parentObserver.observe(root.parentNode, { childList: true })
      state.observers.push(parentObserver)
    }
    on(document.body, 'jeedom_page_load', checkStillHere)
    /* Page mise en cache puis restaurée (bouton Précédent) : l'écran dit
       « Déconnecté » et propose Entrée, au lieu d'afficher une session morte. */
    on(window, 'pagehide', function () {
      if (state.destroyed || state.phase === 'off') {
        return
      }
      state.generation++
      closeSocket()
      setPhase('off')
      writeNotice('[{{Session fermée : la page a été quittée.}}]')
      writeReconnectHint()
    })
  }

  /* ---------- Démarrage ---------- */

  var instance = {
    term: term,
    connect: connect,
    destroy: destroy
  }
  window.jeetermInstance = instance

  /* Touches spéciales : choix mémorisé, sinon affichées sur écran tactile
     seulement (un clavier physique les a déjà). */
  var keysPref = readPref(KEYS_KEY)
  setKeysVisible(keysPref === null ? isTouchDevice() : keysPref === '1', false)

  watchPageLeave()

  var onViewportChange = function () {
    layout()
    scheduleFit()
    repositionPanels()
  }
  on(window, 'resize', onViewportChange)
  if (window.visualViewport) {
    on(window.visualViewport, 'resize', onViewportChange)
  }
  on(document, 'fullscreenchange', afterFullscreenChange)
  on(document, 'webkitfullscreenchange', afterFullscreenChange)

  if (typeof ResizeObserver === 'function') {
    var resizeObserver = new ResizeObserver(function () {
      scheduleFit()
    })
    resizeObserver.observe(host)
    state.observers.push(resizeObserver)
  }

  /* Le thème peut changer sans recharger la page (bouton de bascule, ou
     bascule automatique jour / nuit de Jeedom) : on suit l'attribut. */
  if (typeof MutationObserver === 'function') {
    var themeObserver = new MutationObserver(applyTheme)
    themeObserver.observe(document.body, { attributes: true, attributeFilter: ['data-theme'] })
    state.observers.push(themeObserver)
  }

  root.classList.toggle('jeeterm-crt', settings.crt)
  layout()
  term.open(host)
  doFit()
  /* La taille n'est affichée qu'aux redimensionnements suivants, pas pendant
     la mise en place. */
  setTimeout(function () {
    state.hudReady = true
  }, 1000)
  /* Au premier affichage (chargement complet de la page), la barre de Jeedom
     peut encore changer de hauteur une fois ses scripts passés : on refait la
     mise en page au prochain tour. */
  setTimeout(onViewportChange, 0)
  connect()
  term.focus()
})()
