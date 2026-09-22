#!/usr/bin/env python3
# This file is part of Jeedom.
#
# Jeedom is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Jeedom is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Jeedom. If not, see <http://www.gnu.org/licenses/>.

"""Démon JeeTerm : un pty par session, relayé en WebSocket (RFC 6455).

Bibliothèque standard uniquement, Python >= 3.7. Le contrat (arguments,
points d'entrée HTTP, protocole) est décrit dans CONCEPTION.md.
"""

import argparse
import asyncio
import base64
import binascii
import fcntl
import hashlib
import hmac
import json
import locale
import os
import pwd
import re
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import termios
import time
import urllib.parse

WS_GUID = b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
TOKEN_RE = re.compile(r'^[0-9a-fA-F]{64}$')
HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
# En-têtes qui ne doivent figurer qu'une fois : un doublon est une tentative
# de contournement (deux jetons, deux hôtes…), la requête est refusée.
SINGLE_HEADERS = ('host', 'x-jeeterm-apikey', 'x-jeeterm-token', 'x-jeeterm-user', 'x-jeeterm-ip',
                  'sec-websocket-key', 'sec-websocket-version', 'upgrade', 'origin')
MAX_HEADER = 16384
MAX_CONNECTIONS = 64                # connexions simultanées, sessions comprises
MAX_MESSAGE = 1024 * 1024
MAX_PENDING_OUT = 1024 * 1024       # au-delà, les trames de contrôle ne sont plus mises en file
READ_CHUNK = 65536
STD_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
# Fichier de démarrage des « raccourcis » (bannière, invite, jlog, jcd…),
# lu par bash --rcfile. Il doit rester lisible par tous les comptes du shell.
RCFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'jeeterm.bashrc')
OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA
# Raison de fin lisible (affichée telle quelle) -> code stable pour la page.
WHY = {'fin du shell': 'exit', 'inactivité': 'idle', 'arrêt du démon': 'shutdown',
       'connexion perdue': 'lost', 'fermeture par le navigateur': 'closed'}
# Programmes qui, au premier plan, ne sont qu'un shell ou un relais en attente
# de saisie : leur présence ne suspend pas la fermeture pour inactivité.
SHELLS = ('bash', 'sh', 'dash', 'zsh', 'fish', 'ksh', 'mksh', 'tcsh', 'csh', 'su', 'sudo', 'login', 'tmux')
IDLE_WARNING = 60                   # secondes de préavis avant la fermeture pour inactivité
# Signaux que le démon peut avoir hérités « ignorés » (Apache/PHP ignore
# SIGQUIT) ou qu'il intercepte : le shell doit les retrouver à leur valeur
# par défaut, sinon Ctrl+\ ou un raccrochage n'auraient plus d'effet.
CHILD_DEFAULT_SIGNALS = ('SIGHUP', 'SIGINT', 'SIGQUIT', 'SIGTERM', 'SIGPIPE', 'SIGXFSZ', 'SIGTSTP',
                         'SIGTTIN', 'SIGTTOU', 'SIGCHLD', 'SIGALRM', 'SIGUSR1', 'SIGUSR2')

# ------------------------------------------------------------------- journal
# « audit » trace qui ouvre un shell et qui s'en voit refuser un : c'est un
# accès équivalent à root, il reste écrit quel que soit le niveau choisi, sauf
# si le journal est entièrement coupé (« none »).
LEVELS = {'debug': 0, 'info': 1, 'warning': 2, 'error': 3, 'audit': 50, 'none': 99}
# Jeedom transmet parfois le niveau sous sa forme numérique (log.class.php).
NUMERIC_LEVELS = {'100': 'debug', '200': 'info', '250': 'notice', '300': 'warning', '400': 'error',
                  '500': 'critical', '550': 'alert', '600': 'emergency', '1000': 'none'}
# Même correspondance que jeedom.py : « notice » masque les messages d'information.
ALIASES = {'notice': 'warning', 'warn': 'warning', 'critical': 'error', 'alert': 'error',
           'emergency': 'error', 'default': 'error'}
_min_level = LEVELS['error']


def set_level(value):
    global _min_level
    value = str(value).strip().lower()
    value = NUMERIC_LEVELS.get(value, value)
    value = ALIASES.get(value, value)
    _min_level = LEVELS.get(value, LEVELS['error'])


def log(level, msg):
    if LEVELS[level] < _min_level:
        return
    try:
        sys.stdout.write('[%s][%s] : %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), level.upper(), msg))
        sys.stdout.flush()
    except (OSError, ValueError):
        pass        # journal plein ou fermé : ce n'est pas une raison pour tomber


def clean(value, limit=128):
    """Valeur venue de l'extérieur (en-tête, chemin) rendue sûre pour le journal :
    ni retour à la ligne pour forger une fausse entrée, ni séquence ANSI pour
    piéger celui qui lit le journal dans un terminal."""
    text = str(value)[:limit]
    return ''.join(c if c.isprintable() else '\\x%02x' % ord(c) if ord(c) < 256 else '\\u%04x' % ord(c)
                   for c in text)


def short(token):
    """Seuls les six premiers caractères d'un jeton apparaissent au journal."""
    return (token or '')[:6] + '…'


# ------------------------------------------------------------ état du démon
class State:
    def __init__(self, args, apikey):
        self.args = args
        self.apikey = apikey
        self.tokens = {}            # jeton -> {user, ip, expires}
        self.sessions = set()
        self.connections = 0
        self.reapers = {}           # pid -> tâche de récolte d'un shell récalcitrant
        self.stopping = asyncio.Event()

    def purge_tokens(self):
        now = time.monotonic()
        for token in [t for t, v in self.tokens.items() if v['expires'] <= now]:
            del self.tokens[token]

    def take_token(self, token):
        """Consomme un jeton : il ne sert qu'une fois, qu'il soit valide ou non."""
        self.purge_tokens()
        if not token or not TOKEN_RE.match(token):
            return None
        return self.tokens.pop(token.lower(), None)

    def open_sessions(self):
        """Sessions qui occupent une place : une session en cours de fermeture
        (shell en train de mourir) ne compte plus."""
        return sum(1 for s in self.sessions if not s.closing)


# ---------------------------------------------------------------------- HTTP
class HttpError(Exception):
    pass


async def read_request(reader):
    try:
        raw = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 10)
    except (asyncio.LimitOverrunError, asyncio.IncompleteReadError, asyncio.TimeoutError, ValueError):
        raise HttpError()
    lines = raw.decode('latin-1').split('\r\n')
    parts = lines[0].split(' ')
    if len(parts) != 3 or not parts[2].startswith('HTTP/1.') or not parts[1].startswith('/'):
        raise HttpError()
    headers = {}
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.partition(':')
        # Ni repli de ligne obsolète, ni espace avant les deux-points (RFC 7230 §3.2.4).
        if not sep or not HEADER_NAME_RE.match(name) or '\n' in value or '\r' in value:
            raise HttpError()
        name, value = name.lower(), value.strip(' \t')
        if name in headers:
            if name in SINGLE_HEADERS:
                raise HttpError()
            value = headers[name] + ', ' + value
        headers[name] = value
    return parts[0], parts[1], headers


def http_response(writer, status, body=None, extra=None):
    reasons = {101: 'Switching Protocols', 200: 'OK', 400: 'Bad Request', 401: 'Unauthorized',
               403: 'Forbidden', 404: 'Not Found', 405: 'Method Not Allowed', 429: 'Too Many Requests',
               503: 'Service Unavailable'}
    head = ['HTTP/1.1 %d %s' % (status, reasons.get(status, ''))]
    head += ['%s: %s' % kv for kv in (extra or [])]
    data = b''
    if status != 101:
        data = json.dumps(body if body is not None else {'ok': False}).encode()
        head += ['Content-Type: application/json', 'Content-Length: %d' % len(data), 'Connection: close']
    writer.write(('\r\n'.join(head) + '\r\n\r\n').encode('latin-1') + data)


def local_peer(writer, headers):
    """Seul PHP, en local et en direct, parle au démon : une requête relayée par
    Apache porte un X-Forwarded-For."""
    peer = writer.get_extra_info('peername') or ('',)
    return peer[0] in ('127.0.0.1', '::1', '::ffff:127.0.0.1') and 'x-forwarded-for' not in headers


def apikey_ok(state, headers):
    given = headers.get('x-jeeterm-apikey', '').encode('latin-1')
    return hmac.compare_digest(given, state.apikey.encode())


async def handle_client(state, reader, writer):
    if state.connections >= MAX_CONNECTIONS:
        log('warning', 'Connexion refusée : %d connexions déjà ouvertes' % state.connections)
        writer.close()
        return
    state.connections += 1
    try:
        try:
            method, target, headers = await read_request(reader)
        except HttpError:
            http_response(writer, 400)
            return
        url = urllib.parse.urlsplit(target)
        if url.path not in ('/register', '/status', '/ws'):
            http_response(writer, 404)
        elif method != 'GET':
            http_response(writer, 405)
        elif url.path == '/ws':
            await handle_ws(state, reader, writer, headers, urllib.parse.parse_qs(url.query))
        elif not local_peer(writer, headers):
            log('audit', 'Requête %s refusée : provenance non locale' % url.path)
            http_response(writer, 403)
        elif not apikey_ok(state, headers):
            log('audit', 'Requête %s refusée : clé API invalide' % url.path)
            http_response(writer, 401)
        elif url.path == '/status':
            http_response(writer, 200, {'ok': True, 'sessions': len(state.sessions)})
        else:
            register(state, writer, headers)
    except (ConnectionError, OSError) as e:
        log('debug', 'Connexion interrompue : %s' % e)
    finally:
        state.connections -= 1
        await close_writer(writer, 0.5 if state.stopping.is_set() else 2)


async def close_writer(writer, timeout=2):
    """Vide ce qui reste à envoyer, sans attendre indéfiniment un pair qui ne lit
    plus : passé le délai, la connexion est coupée net."""
    try:
        await asyncio.wait_for(writer.drain(), timeout)
        writer.close()
        await asyncio.wait_for(writer.wait_closed(), timeout)
    except (ConnectionError, OSError, asyncio.TimeoutError):
        writer.transport.abort()


def register(state, writer, headers):
    token = headers.get('x-jeeterm-token', '')
    if not TOKEN_RE.match(token):
        http_response(writer, 400)
        return
    if state.stopping.is_set():
        http_response(writer, 503)
        return
    # Les en-têtes sont lus en latin-1 ; PHP envoie le login en UTF-8.
    user = headers.get('x-jeeterm-user', '?')
    try:
        user = user.encode('latin-1').decode('utf-8')
    except UnicodeError:
        pass
    user = clean(user, 64)
    # Un jeton délivré réserve une place : deux onglets ne peuvent pas obtenir
    # chacun un jeton pour la dernière place libre. Un nouveau jeton remplace
    # ceux, inutilisés, du même utilisateur (page rechargée, connexion ratée).
    state.purge_tokens()
    for old in [t for t, v in state.tokens.items() if v['user'] == user]:
        del state.tokens[old]
    used = state.open_sessions() + len(state.tokens)
    if used >= state.args.max_sessions:
        log('warning', 'Jeton refusé pour %s : %d session(s) ouverte(s) ou réservée(s), maximum atteint'
            % (user, used))
        http_response(writer, 429)
        return
    state.tokens[token.lower()] = {'user': user,
                                   'ip': clean(headers.get('x-jeeterm-ip', '?'), 64),
                                   'expires': time.monotonic() + state.args.token_ttl}
    log('debug', 'Jeton %s enregistré pour %s' % (short(token), user))
    http_response(writer, 200, {'ok': True})


def check_origin(headers):
    """Un Origin étranger est suspect mais pas bloquant : le jeton, à usage unique
    et remis par une page authentifiée, fait foi."""
    origin = headers.get('origin')
    if not origin:
        return
    # Tous les hôtes connus du relais : un mandataire en amont d'Apache peut
    # avoir récrit Host, X-Forwarded-Host garde alors l'hôte d'origine.
    hosts = [h.strip() for h in headers.get('x-forwarded-host', '').split(',') if h.strip()]
    hosts.append(headers.get('host', ''))
    expected = set()
    for host in hosts:
        try:
            expected.add(urllib.parse.urlsplit('//' + host).hostname)
        except ValueError:
            pass
    try:
        given = urllib.parse.urlsplit(origin).hostname
    except ValueError:
        given = None
    if given is None or given not in expected:
        log('warning', 'Origin %s différent de l\'hôte %s' % (clean(origin), clean(hosts[0])))


def clamp(query, key, default):
    try:
        return min(500, max(10, int(query.get(key, [default])[0])))
    except (ValueError, OverflowError):
        return default


def valid_ws_key(key):
    try:
        return len(base64.b64decode(key.encode('ascii'), validate=True)) == 16
    except (ValueError, binascii.Error, UnicodeError):
        return False


async def handle_ws(state, reader, writer, headers, query):
    key = headers.get('sec-websocket-key', '')
    if (headers.get('upgrade', '').lower() != 'websocket'
            or 'upgrade' not in [v.strip() for v in headers.get('connection', '').lower().split(',')]
            or headers.get('sec-websocket-version') != '13' or not valid_ws_key(key)):
        http_response(writer, 400)
        return
    info = state.take_token(query.get('t', [''])[0])
    if info is None:
        log('audit', 'WebSocket refusé (IP %s) : jeton absent, invalide, expiré ou déjà utilisé'
            % clean(headers.get('x-forwarded-for', '?').split(',')[-1].strip(), 64))
        http_response(writer, 403)
        return
    check_origin(headers)
    accept = base64.b64encode(hashlib.sha1(key.encode() + WS_GUID).digest()).decode()
    http_response(writer, 101, extra=[('Upgrade', 'websocket'), ('Connection', 'Upgrade'),
                                      ('Sec-WebSocket-Accept', accept)])
    ws = WebSocket(reader, writer)
    # Une place a été réservée à l'enregistrement du jeton, mais une session en
    # cours de fermeture peut encore l'occuper, ou le démon s'arrêter. Le refus
    # passe par le WebSocket : un refus HTTP n'afficherait rien de lisible.
    if state.stopping.is_set():
        await ws.refuse('le démon JeeTerm est en cours d\'arrêt', 'shutdown')
        return
    if state.open_sessions() >= state.args.max_sessions:
        log('audit', 'WebSocket refusé (%s, IP %s) : %d session(s) ouverte(s), maximum atteint'
            % (info['user'], info['ip'], state.open_sessions()))
        await ws.refuse('nombre maximal de sessions atteint (%d)' % state.args.max_sessions)
        return
    session = Session(state, ws, info, clamp(query, 'c', 80), clamp(query, 'r', 24))
    state.sessions.add(session)
    try:
        await session.run()
    finally:
        state.sessions.discard(session)


# ----------------------------------------------------------------- WebSocket
class WsClosed(Exception):
    """Fin de la connexion : fermeture reçue, erreur de protocole ou coupure."""
    def __init__(self, code, reason):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def unmask(data, mask):
    if not data:
        return data
    key = (mask * (len(data) // 4 + 1))[:len(data)]
    return (int.from_bytes(data, 'big') ^ int.from_bytes(key, 'big')).to_bytes(len(data), 'big')


def close_code_valid(code):
    """Codes qu'un pair a le droit d'envoyer (RFC 6455 §7.4, registre IANA)."""
    return 1000 <= code <= 1003 or 1007 <= code <= 1014 or 3000 <= code <= 4999


def terminal_notice(text):
    """Message du démon affiché dans le terminal, sur sa propre ligne."""
    return ('\r\n\x1b[1;33m[jeeterm]\x1b[0m ' + text.replace('\n', '\r\n[jeeterm] ') + '\r\n').encode('utf-8')


class WebSocket:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.lock = asyncio.Lock()      # drain() concurrent interdit avant Python 3.10
        self.close_sent = False
        self.last_rx = time.monotonic()

    @staticmethod
    def frame(opcode, payload):
        n = len(payload)
        if n < 126:
            head = struct.pack('!BB', 0x80 | opcode, n)
        elif n < 65536:
            head = struct.pack('!BBH', 0x80 | opcode, 126, n)
        else:
            head = struct.pack('!BBQ', 0x80 | opcode, 127, n)
        return head + payload

    async def send(self, opcode, payload=b'', timeout=None):
        """Envoi d'une trame de données. Le délai éventuel couvre aussi l'attente
        du verrou, que la pompe peut tenir longtemps si le navigateur n'absorbe plus."""
        if timeout is None:
            await self._send(opcode, payload)
        else:
            await asyncio.wait_for(self._send(opcode, payload), timeout)

    async def _send(self, opcode, payload):
        async with self.lock:
            if self.writer.is_closing():
                raise ConnectionResetError()
            # La trame part en une seule écriture : une trame de contrôle
            # glissée entre deux envois ne peut pas la couper en deux.
            self.writer.write(self.frame(opcode, payload))
            # Contrôle de flux : tant que le navigateur n'absorbe pas, on attend ici,
            # le pty n'est plus lu et c'est le shell qui se retrouve bloqué.
            await self.writer.drain()

    async def try_send(self, opcode, payload, timeout=2):
        """Envoi de fin de session : un pair disparu ou muet ne doit rien bloquer."""
        try:
            await self.send(opcode, payload, timeout)
            return True
        except (ConnectionError, OSError, asyncio.TimeoutError):
            return False

    def send_control(self, opcode, payload=b''):
        """Ping, pong et fermeture partent sans attendre : ils ne doivent jamais
        rester bloqués derrière une sortie que le navigateur n'absorbe pas."""
        if self.writer.is_closing():
            return False
        transport = self.writer.transport
        if transport is not None and transport.get_write_buffer_size() > MAX_PENDING_OUT:
            return False
        self.writer.write(self.frame(opcode, payload))
        return True

    def close(self, code=1000):
        if not self.close_sent:
            self.close_sent = True
            self.send_control(OP_CLOSE, struct.pack('!H', code) if code else b'')

    async def wait_peer_close(self, timeout):
        """Attend la réponse du navigateur à notre fermeture (RFC 6455 §7.1.1).
        Échéance fixe : un pair qui continue d'envoyer des messages ne la repousse pas."""
        end = time.monotonic() + timeout
        try:
            while True:
                await asyncio.wait_for(self.recv(), max(0.01, end - time.monotonic()))
        except (WsClosed, asyncio.TimeoutError):
            pass

    async def send_exit(self, code, reason, why, timeout=2):
        """Dernier message avant fermeture : raison en français pour l'affichage,
        `why` stable pour la page."""
        return await self.try_send(OP_TEXT, json.dumps({'t': 'x', 'code': code, 'reason': reason,
                                                        'why': why}).encode(), timeout)

    async def refuse(self, reason, why='error'):
        """Refus après la poignée de main : message lisible, puis fermeture."""
        await self.try_send(OP_BIN, terminal_notice('Session refusée : ' + reason + '.'))
        await self.send_exit(-1, reason, why)
        self.close(1013)
        await self.wait_peer_close(2)

    async def _frame(self):
        b0, b1 = await self.reader.readexactly(2)
        if b0 & 0x70:
            raise WsClosed(1002, 'bits réservés')
        if not b1 & 0x80:
            raise WsClosed(1002, 'trame client non masquée')
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack('!H', await self.reader.readexactly(2))[0]
        elif n == 127:
            n = struct.unpack('!Q', await self.reader.readexactly(8))[0]
        opcode = b0 & 0x0F
        if opcode >= 0x8 and (n > 125 or not b0 & 0x80):
            raise WsClosed(1002, 'trame de contrôle invalide')
        if n > MAX_MESSAGE:
            raise WsClosed(1009, 'message trop grand')
        mask = await self.reader.readexactly(4)
        payload = unmask(await self.reader.readexactly(n), mask)
        # Toute trame compte, pong compris : c'est la réponse automatique des
        # navigateurs à nos pings qui prouve que la connexion vit.
        self.last_rx = time.monotonic()
        return bool(b0 & 0x80), opcode, payload

    def _on_close(self, payload):
        if len(payload) == 1:
            raise WsClosed(1002, 'trame de fermeture invalide')
        code = struct.unpack('!H', payload[:2])[0] if payload else 1005
        if payload and not close_code_valid(code):
            raise WsClosed(1002, 'code de fermeture invalide')
        try:
            payload[2:].decode('utf-8')
        except UnicodeDecodeError:
            raise WsClosed(1007, 'raison de fermeture non UTF-8')
        if not self.close_sent:
            self.close_sent = True
            self.send_control(OP_CLOSE, payload[:2])
        raise WsClosed(code, 'fermeture par le navigateur')

    async def recv(self):
        """Renvoie le message de données suivant, en traitant au passage les
        trames de contrôle et la fragmentation."""
        opcode, parts, size = None, [], 0
        while True:
            try:
                fin, op, payload = await self._frame()
            except (asyncio.IncompleteReadError, ConnectionError, OSError):
                raise WsClosed(1006, 'connexion perdue')
            if op == OP_PING:
                self.send_control(OP_PONG, payload)
                continue
            if op == OP_PONG:
                continue
            if op == OP_CLOSE:
                self._on_close(payload)
            if op == OP_CONT:
                if opcode is None:
                    raise WsClosed(1002, 'continuation inattendue')
            elif op in (OP_TEXT, OP_BIN):
                if opcode is not None:
                    raise WsClosed(1002, 'message fragmenté interrompu')
                opcode = op
            else:
                raise WsClosed(1002, 'opcode inconnu')
            size += len(payload)
            if size > MAX_MESSAGE:
                raise WsClosed(1009, 'message trop grand')
            parts.append(payload)
            if fin:
                data = b''.join(parts)
                if opcode == OP_TEXT:
                    # Décodage du message entier : un caractère coupé entre deux
                    # fragments est légal.
                    try:
                        return opcode, data.decode('utf-8')
                    except UnicodeDecodeError:
                        raise WsClosed(1007, 'texte non UTF-8')
                return opcode, data


# ------------------------------------------------------------------- shell
_lang = None


def locale_exists(name):
    """Vrai si la locale est installée (sinon bash se plaint à chaque ouverture)."""
    saved = locale.setlocale(locale.LC_CTYPE)
    try:
        locale.setlocale(locale.LC_CTYPE, name)
        return True
    except locale.Error:
        return False
    finally:
        locale.setlocale(locale.LC_CTYPE, saved)


def shell_lang():
    """Le shell a besoin d'une locale UTF-8 : un démon lancé par Apache hérite de
    LANG=C (/etc/apache2/envvars), et les accents comme les cadres de tmux s'en
    trouvent cassés. Ordre : locale UTF-8 du démon, celle du système, C.UTF-8."""
    global _lang
    if _lang is None:
        candidates = [os.environ.get('LANG', '')]
        try:
            with open('/etc/default/locale') as f:
                for line in f:
                    name, sep, value = line.strip().partition('=')
                    if sep and name.strip() == 'LANG':
                        candidates.append(value.strip().strip('"\''))
        except OSError:
            pass
        _lang = next((c for c in candidates if re.search(r'utf-?8', c, re.I) and locale_exists(c)),
                     'C.UTF-8')
    return _lang


def shell_home(args, account, same):
    """HOME du shell. Celui de www-data (/var/www) appartient à root : ni
    historique, ni fichiers de réglage. On lui substitue un dossier à lui."""
    if not same or os.access(account.pw_dir, os.W_OK):
        return account.pw_dir
    if os.access('/var/lib/jeeterm/home', os.W_OK):
        return '/var/lib/jeeterm/home'
    home = os.path.join(os.path.dirname(os.path.abspath(args.pid)), 'home')
    try:
        os.makedirs(home, 0o700, exist_ok=True)
        if os.access(home, os.W_OK):
            return home
    except OSError:
        pass
    return account.pw_dir


def shell_command(args):
    """Commande, environnement et dossier de départ du shell.

    Lève ValueError si le compte demandé n'est pas utilisable."""
    me = pwd.getpwuid(os.geteuid())
    target = args.shell_user.strip() or me.pw_name
    try:
        account = pwd.getpwnam(target)
    except KeyError:
        raise ValueError('le compte %s est inconnu sur ce système' % target)
    # Le compte du démon (www-data) a nologin pour shell : on lui lance bash
    # directement. Un autre compte passe par sudo -i, qui exige un vrai shell.
    same = account.pw_uid == me.pw_uid
    if not same and account.pw_shell.endswith(('nologin', 'false')):
        raise ValueError('le compte %s n\'a pas de shell de connexion (%s)' % (target, account.pw_shell))
    notice = ''
    # Raccourcis : bash lancé sur notre fichier de démarrage, qui refait lui-même
    # ce que ferait un shell de connexion. Un compte dont le shell n'est pas
    # bash (zsh…) garde le sien, sans raccourcis.
    rc = []
    if args.goodies and os.path.isfile(RCFILE) and (same or os.path.basename(account.pw_shell) == 'bash'):
        rc = ['/bin/bash', '--rcfile', RCFILE, '-i']
    # -u : tmux dessine en UTF-8 même si la locale reçue ne le dit pas.
    tmux = ['tmux', '-u', 'new-session', '-A', '-s', 'jeeterm'] if args.tmux else []
    if tmux and shutil.which('tmux', path=STD_PATH) is None:
        notice = ('tmux n\'est pas installé : ouverture d\'un shell simple, qui ne survivra pas '
                  'à la fermeture de la page.')
        tmux = []
    if tmux and rc:
        # La commande ne sert qu'à la création de la session ; default-command
        # donne aussi les raccourcis aux fenêtres ouvertes ensuite dans tmux.
        line = ' '.join(shlex.quote(a) for a in rc)
        tmux += [line, ';', 'set-option', 'default-command', line]
    if same:
        argv = tmux or rc or ['/bin/bash', '-l']
        cwd = '/var/www/html' if not args.shell_user and os.path.isdir('/var/www/html') else account.pw_dir
    else:
        # sudo -i passe la commande au shell de connexion du compte (bash -c),
        # qui lit /etc/profile et ~/.profile avant de lancer notre bash.
        argv = ['sudo', '-n', '-i'] + ([] if target == 'root' else ['-u', target]) + (tmux or rc)
        cwd = '/'
    if not os.path.isdir(cwd):
        cwd = '/'
    lang = shell_lang()
    env = {'TERM': 'xterm-256color', 'COLORTERM': 'truecolor',
           'LANG': lang, 'LC_ALL': lang, 'PATH': STD_PATH,
           'HOME': shell_home(args, account, same), 'USER': target,
           'SHELL': '/bin/bash' if same else (account.pw_shell or '/bin/sh')}
    # Seule exception à l'environnement minimal : l'emplacement des sockets
    # tmux, pour que les tests n'utilisent pas le serveur tmux de l'utilisateur.
    if tmux and os.environ.get('TMUX_TMPDIR'):
        env['TMUX_TMPDIR'] = os.environ['TMUX_TMPDIR']
    return target, argv, env, cwd, notice


def set_winsize(fd, cols, rows):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))


def _child_setup():
    # Exécuté dans l'enfant, après setsid() : le pty devient son terminal de contrôle.
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    # Un signal ignoré se transmet à travers exec (bash ne peut même plus le
    # rétablir) : on rend au shell des signaux neufs, et aucun signal bloqué.
    signal.set_wakeup_fd(-1)
    for name in CHILD_DEFAULT_SIGNALS:
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_SETMASK, [])


# ------------------------------------------------------------------ session
class Session:
    def __init__(self, state, ws, info, cols, rows):
        self.state = state
        self.ws = ws
        self.info = info
        self.cols, self.rows = cols, rows
        self.loop = asyncio.get_running_loop()
        self.proc = None
        self.fd = None
        self.account = '?'
        self.via_sudo = False
        self.head = b''             # début de la sortie, pour reconnaître un refus de sudo
        self.inbuf = bytearray()
        self.writing = False
        self.dropping = False
        self.closing = False
        self.output = 0             # octets sortis du pty, pour juger l'activité
        self.started = self.last_input = self.last_busy = time.monotonic()

    def describe(self):
        return 'utilisateur Jeedom %s, IP %s, compte %s' % (self.info['user'], self.info['ip'], self.account)

    def spawn(self):
        self.account, argv, env, cwd, notice = shell_command(self.state.args)
        self.via_sudo = argv[0] == 'sudo'
        master, slave = os.openpty()
        try:
            set_winsize(slave, self.cols, self.rows)
            self.proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, env=env, cwd=cwd,
                                         start_new_session=True, preexec_fn=_child_setup, close_fds=True)
        except Exception:
            os.close(master)
            raise
        finally:
            os.close(slave)
        os.set_blocking(master, False)
        self.fd = master
        return notice

    async def run(self):
        try:
            await self.serve()
        finally:
            # Filet : quelle que soit la sortie (exception, annulation à l'arrêt
            # du démon), le shell ne survit pas à sa session.
            self.closing = True
            self.kill_now()

    async def serve(self):
        try:
            notice = self.spawn()
        except (ValueError, OSError, subprocess.SubprocessError) as e:
            log('error', 'Session refusée (%s) : %s' % (self.describe(), e))
            reason = 'impossible d\'ouvrir le shell : %s' % e
            await self.ws.try_send(OP_BIN, terminal_notice(reason[0].upper() + reason[1:] + '.'))
            await self.ws.send_exit(-1, reason, 'error')
            self.ws.close()
            await self.ws.wait_peer_close(2)
            return
        log('audit', 'Session ouverte : %s, pid %d, %dx%d' % (self.describe(), self.proc.pid, self.cols, self.rows))
        if notice:
            log('warning', 'tmux introuvable : shell simple à la place')
            await self.ws.try_send(OP_BIN, terminal_notice(notice))
        pump = asyncio.ensure_future(self.pump())
        recv = asyncio.ensure_future(self.receive())
        keeper = asyncio.ensure_future(self.keeper())
        stop = asyncio.ensure_future(self.state.stopping.wait())
        try:
            done, _ = await asyncio.wait({pump, recv, keeper, stop}, return_when=asyncio.FIRST_COMPLETED)
        except BaseException:       # annulation : run() tue le shell
            for task in (pump, recv, keeper, stop):
                task.cancel()
            raise
        self.closing = True
        for task in (pump, keeper, stop):
            task.cancel()
        await asyncio.gather(pump, keeper, stop, return_exceptions=True)
        reason, peer_gone = self.end_reason(done, pump, recv, keeper)
        why = WHY.get(reason, 'closed' if peer_gone else 'error')
        # À l'arrêt du démon, tout doit tenir en quelques secondes (Jeedom finit
        # par un kill -9) : délais courts, les sessions se ferment en parallèle.
        stopping = self.state.stopping.is_set()
        if reason == 'fin du shell':
            await self.flush_output()
        code = await self.terminate(1 if stopping else 2)
        # sudo -n qui échoue sort avec le code 1 après un message en anglais ou
        # dans la langue du système, avant toute saisie : on l'explique en français.
        if (self.via_sudo and code == 1 and self.last_input == self.started
                and time.monotonic() - self.started < 10 and b'sudo' in self.head.lower()):
            log('error', 'sudo a refusé d\'ouvrir le shell du compte %s : droit sudo sans mot de passe '
                'requis pour le compte du démon' % self.account)
            reason, why = 'sudo a refusé d\'ouvrir le shell', 'error'
            if not peer_gone:
                await self.ws.try_send(OP_BIN, terminal_notice(
                    'sudo a refusé d\'ouvrir le shell du compte %s.\n'
                    'Le compte du démon (%s) doit pouvoir utiliser sudo sans mot de passe.\n'
                    'Vérifiez la configuration de sudo, ou choisissez un autre « Compte du shell » '
                    'dans la configuration du plugin.' % (self.account, pwd.getpwuid(os.geteuid()).pw_name)))
        if not peer_gone:
            await self.ws.send_exit(code, reason, why, 0.5 if stopping else 2)
            self.ws.close()
            # Le navigateur répond à la fermeture : receive() se termine alors seul.
            await asyncio.wait({recv}, timeout=0.5 if stopping else 2)
        recv.cancel()
        await asyncio.gather(recv, return_exceptions=True)
        log('audit', 'Session fermée : %s, durée %d s, raison : %s, code %s'
            % (self.describe(), time.monotonic() - self.started, reason, code))

    @staticmethod
    def end_reason(done, pump, recv, keeper):
        """Raison de fin, et si le navigateur est déjà hors d'atteinte."""
        def failed(task):
            return not task.cancelled() and task.exception() is not None
        if recv in done:
            return (recv.result() if not failed(recv) else 'connexion perdue'), True
        if pump in done:
            # Une erreur d'envoi vers le navigateur n'est pas la fin du shell.
            return ('connexion perdue', True) if failed(pump) else ('fin du shell', False)
        if keeper in done:
            return ('connexion perdue', True) if failed(keeper) else (keeper.result(), False)
        return 'arrêt du démon', False

    async def readable(self):
        fd, fut = self.fd, self.loop.create_future()
        self.loop.add_reader(fd, lambda: fut.done() or fut.set_result(None))
        try:
            await fut
        finally:
            # Si le pty a été fermé entre-temps, son numéro peut déjà servir à
            # une autre session : on ne retire que notre propre surveillance.
            if self.fd == fd:
                self.loop.remove_reader(fd)

    def read_available(self, limit=READ_CHUNK * 4):
        """Lit ce qui est disponible sans bloquer. None : le pty est fermé côté shell."""
        buf, closed = bytearray(), False
        while len(buf) < limit and self.fd is not None:
            try:
                data = os.read(self.fd, READ_CHUNK)
            except BlockingIOError:
                break
            except OSError:         # EIO : plus aucun processus ne tient le pty
                closed = True
                break
            if not data:
                closed = True
                break
            buf += data
        self.output += len(buf)
        if len(self.head) < 4096:
            self.head += bytes(buf[:4096 - len(self.head)])
        return None if closed and not buf else bytes(buf)

    async def pump(self):
        """pty -> navigateur. Une courte attente regroupe les petites écritures du
        shell en une seule trame, au lieu d'une trame par caractère."""
        while True:
            await self.readable()
            await asyncio.sleep(0.005)
            data = self.read_available()
            if data is None:
                return
            if data:
                await self.ws.send(OP_BIN, data)

    async def flush_output(self):
        data = self.read_available()
        if data:
            await self.ws.try_send(OP_BIN, data)

    async def receive(self):
        """Navigateur -> pty. Renvoie la raison de fin quand la connexion se termine."""
        while True:
            try:
                opcode, data = await self.ws.recv()
            except WsClosed as e:
                if e.code not in (1000, 1001, 1005, 1006):
                    log('debug', 'WebSocket fermé, code %d : %s (%s)' % (e.code, e.reason, self.describe()))
                    self.ws.close(e.code)
                return e.reason
            except (ConnectionError, OSError):
                return 'connexion perdue'
            if opcode != OP_TEXT or self.ws.close_sent or self.fd is None:
                continue
            try:
                self.handle_message(json.loads(data))
            except (ValueError, TypeError, AttributeError, OverflowError, RecursionError):
                log('debug', 'Message illisible ignoré')

    def handle_message(self, msg):
        kind = msg.get('t')
        if kind == 'i' and isinstance(msg.get('d'), str):
            self.last_input = time.monotonic()
            self.write_input(msg['d'].encode('utf-8', 'replace'))
        elif kind == 'r':
            cols = min(500, max(10, int(msg.get('c'))))
            rows = min(500, max(10, int(msg.get('r'))))
            self.cols, self.rows = cols, rows
            try:
                set_winsize(self.fd, cols, rows)
            except OSError:
                pass

    def write_input(self, data):
        if len(self.inbuf) + len(data) > MAX_MESSAGE:
            if not self.dropping:
                log('warning', 'Entrée ignorée : le shell ne lit plus (%s)' % self.describe())
                self.dropping = True
            return
        self.dropping = False
        self.inbuf += data
        self.flush_input()

    def flush_input(self):
        """Écriture non bloquante : le reste attend que le pty redevienne inscriptible."""
        while self.inbuf and self.fd is not None:
            try:
                del self.inbuf[:os.write(self.fd, self.inbuf)]
            except BlockingIOError:
                break
            except OSError:         # shell parti : rien ne lira plus cette saisie
                self.inbuf.clear()
        if self.inbuf and self.fd is not None and not self.writing:
            self.loop.add_writer(self.fd, self.flush_input)
            self.writing = True
        elif (not self.inbuf or self.fd is None) and self.writing:
            self.stop_writer()

    def stop_writer(self):
        if self.writing:
            self.loop.remove_writer(self.fd)
            self.writing = False

    async def keeper(self):
        """Surveillance : fin du shell, inactivité, ping, connexion morte."""
        idle = self.state.args.idle_timeout
        interval = self.state.args.ping_interval
        warn_before = min(IDLE_WARNING, idle / 2)
        sample_every = max(0.5, min(10, idle / 4))
        last_ping = last_sample = time.monotonic()
        sample, warned = None, False
        while True:
            await asyncio.sleep(min(0.5, interval / 2))
            now = time.monotonic()
            # Un processus détaché peut garder le pty ouvert après la fin du shell.
            if self.proc.poll() is not None:
                return 'fin du shell'
            if idle > 0:
                if now - last_sample >= sample_every:
                    last_sample, previous, sample = now, sample, self.foreground_activity()
                    if sample is not None and sample != previous:
                        self.last_busy = now
                quiet = now - max(self.last_input, self.last_busy)
                if quiet >= idle:
                    return 'inactivité'
                if quiet >= idle - warn_before and not warned:
                    warned = True
                    await self.ws.try_send(OP_BIN, terminal_notice(
                        'Session inactive : fermeture dans %d s. Appuyez sur une touche pour la garder.'
                        % round(idle - quiet)), 1)
                elif quiet < idle - warn_before:
                    warned = False
            # Le navigateur répond seul à nos pings, et la page envoie son propre
            # maintien : trois intervalles sans rien recevoir, la connexion est morte.
            if now - self.ws.last_rx > interval * 3:
                return 'connexion perdue'
            if now - last_ping >= interval:
                last_ping = now
                self.ws.send_control(OP_PING, b'jeeterm')

    def foreground_activity(self):
        """Commande au premier plan du shell, et ce qu'elle a fait jusqu'ici.

        Une longue commande (apt full-upgrade…) ne doit pas être tuée pour
        inactivité clavier. On parcourt les descendants du processus lancé,
        jusque dans le pty que sudo crée pour lui : une commande au premier plan
        de son terminal, qui n'est pas un shell, est un travail en cours. Elle
        n'empêche la fermeture que si elle vit (temps processeur ou sortie qui
        change) : un « less » ou un « vim » oublié n'occupe pas la session.
        Renvoie None si seul un shell attend une saisie."""
        table = proc_table()
        children = {}
        for pid, (comm, ppid, pgrp, tpgid, cpu) in table.items():
            children.setdefault(ppid, []).append(pid)
        tree, todo = [], [self.proc.pid]
        while todo:
            pid = todo.pop()
            if pid in table:
                tree.append(pid)
                todo.extend(children.get(pid, ()))
        jobs = set()
        for pid in tree:
            comm, ppid, pgrp, tpgid, cpu = table[pid]
            name = comm.split(':')[0].split(' ')[0].lstrip('-')
            if pid != self.proc.pid and pgrp == tpgid and name not in SHELLS:
                jobs.add(pgrp)
        if not jobs:
            return None
        return (frozenset(jobs), sum(table[pid][4] for pid in tree if table[pid][2] in jobs), self.output)

    def killpg(self, sig):
        try:
            os.killpg(self.proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    async def wait_exit(self, delay):
        end = time.monotonic() + delay
        while self.proc.poll() is None and time.monotonic() < end:
            await asyncio.sleep(0.05)
        return self.proc.returncode

    def close_pty(self):
        if self.fd is not None:
            self.stop_writer()
            self.loop.remove_reader(self.fd)
            os.close(self.fd)
            self.fd = None

    async def terminate(self, delay=2):
        """SIGHUP puis SIGKILL au groupe du shell. Fermer le maître provoque aussi
        un raccrochage du terminal, le seul signal qui atteigne un sudo root.

        En mode tmux, le groupe est celui du client : le serveur tmux s'est
        détaché dans sa propre session au démarrage, la session tmux survit."""
        self.killpg(signal.SIGHUP)
        self.close_pty()
        if await self.wait_exit(delay) is None:
            self.killpg(signal.SIGKILL)
            if await self.wait_exit(delay) is None:
                self.reap_later()
                return None
        code = self.proc.returncode
        return code if code >= 0 else 128 - code

    def kill_now(self):
        """Nettoyage synchrone, pour une session interrompue avant terminate()."""
        self.close_pty()
        if self.proc is not None and self.proc.poll() is None:
            self.killpg(signal.SIGHUP)
            self.killpg(signal.SIGKILL)
            if self.proc.poll() is None:
                self.reap_later()

    def reap_later(self):
        pid, reapers = self.proc.pid, self.state.reapers
        if pid in reapers:
            return
        log('warning', 'Le shell pid %d résiste, il sera récolté plus tard' % pid)
        reapers[pid] = asyncio.ensure_future(reap_later(self.proc))
        reapers[pid].add_done_callback(lambda task: reapers.pop(pid, None))


def proc_table():
    """pid -> (nom, parent, groupe, groupe au premier plan du terminal, temps
    processeur) pour tous les processus visibles, d'après /proc/<pid>/stat."""
    table = {}
    try:
        names = os.listdir('/proc')
    except OSError:
        return table
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open('/proc/%s/stat' % name, 'rb') as f:
                data = f.read()
            # Le nom est entre parenthèses et peut lui-même en contenir.
            end = data.rindex(b')')
            fields = data[end + 2:].split()
            table[int(name)] = (data[data.index(b'(') + 1:end].decode('utf-8', 'replace'),
                                int(fields[1]), int(fields[2]), int(fields[5]),
                                int(fields[11]) + int(fields[12]))
        except (OSError, ValueError, IndexError):
            continue        # processus disparu entre-temps
    return table


async def reap_later(proc):
    while proc.poll() is None:
        await asyncio.sleep(5)


# ---------------------------------------------------------------- démarrage
def write_pid(path):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid(path):
    """Ne retire le fichier que s'il est bien le nôtre : un second démon lancé
    par erreur ne doit pas effacer celui du démon en service."""
    try:
        with open(path) as f:
            if f.read().strip() != str(os.getpid()):
                return
        os.unlink(path)
    except OSError:
        pass


def on_sighup():
    # Le démon n'a pas de terminal : un SIGHUP (fermeture de la console qui l'a
    # lancé à la main) ne doit pas l'arrêter. Pas de SIG_IGN, qui serait hérité
    # par les shells et les rendrait sourds au raccrochage.
    log('info', 'SIGHUP reçu et ignoré (SIGTERM arrête le démon)')


async def main(args, apikey):
    state = State(args, apikey)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, state.stopping.set)
    loop.add_signal_handler(signal.SIGHUP, on_sighup)
    server = await asyncio.start_server(lambda r, w: handle_client(state, r, w),
                                        '127.0.0.1', args.port, limit=MAX_HEADER)
    # Le fichier pid n'apparaît qu'une fois le port ouvert : pour Jeedom, « démarré »
    # veut dire « prêt à répondre ».
    try:
        write_pid(args.pid)
    except OSError as e:
        server.close()
        raise OSError('impossible d\'écrire le fichier pid %s : %s' % (args.pid, e))
    log('info', 'Démon démarré sur 127.0.0.1:%d (compte shell : %s, tmux : %d, raccourcis : %d, '
        'inactivité : %d s, sessions max : %d)' % (args.port, args.shell_user or 'démon', args.tmux,
                                                   args.goodies, args.idle_timeout, args.max_sessions))
    await state.stopping.wait()
    log('info', 'Arrêt demandé, fermeture de %d session(s)' % len(state.sessions))
    server.close()
    # Les sessions voient le même événement et se ferment d'elles-mêmes, en
    # parallèle. Au-delà de l'échéance, asyncio.run() les annule et Session.run()
    # tue alors les shells restants sans attendre.
    end = time.monotonic() + 4
    while (state.sessions or state.connections) and time.monotonic() < end:
        await asyncio.sleep(0.05)
    try:
        await asyncio.wait_for(server.wait_closed(), max(0.1, end - time.monotonic()))
    except asyncio.TimeoutError:
        pass


def parse_args():
    p = argparse.ArgumentParser(description='Démon JeeTerm')
    p.add_argument('--port', type=int, default=55080)
    p.add_argument('--pid', default='/tmp/jeedom/jeeterm/deamon.pid')
    p.add_argument('--loglevel', default='error')
    p.add_argument('--shell-user', default='')
    p.add_argument('--tmux', type=int, default=0)
    p.add_argument('--goodies', type=int, default=1)
    p.add_argument('--idle-timeout', type=int, default=900)
    p.add_argument('--max-sessions', type=int, default=3)
    p.add_argument('--apikey-file', default='')
    p.add_argument('--token-ttl', type=float, default=30, help=argparse.SUPPRESS)
    p.add_argument('--ping-interval', type=float, default=30, help=argparse.SUPPRESS)
    return p.parse_args()


def read_apikey(path):
    """Clé API : première ligne du fichier donné (supprimé aussitôt lu, il ne
    doit pas traîner sur le disque), sinon de l'entrée standard."""
    if not path:
        return sys.stdin.readline().strip()
    try:
        with open(path) as f:
            key = f.readline().strip()
    except OSError as e:
        log('error', 'Fichier de clé API %s illisible : %s' % (path, e))
        return ''
    try:
        os.unlink(path)
    except OSError:
        pass
    return key


def early_stop(signum, frame):
    # SIGTERM reçu avant que la boucle n'ait pris la main : sortie propre quand même.
    raise SystemExit(0)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, early_stop)
    signal.signal(signal.SIGHUP, lambda signum, frame: None)
    arguments = parse_args()
    set_level(arguments.loglevel)
    # La clé arrive par un fichier ou sur l'entrée standard : un argument serait
    # visible dans `ps`. systemd-run ne transmet pas l'entrée standard.
    key = read_apikey(arguments.apikey_file)
    if not key:
        log('error', 'Clé API absente (--apikey-file ou entrée standard)')
        sys.exit(1)
    code = 0
    try:
        asyncio.run(main(arguments, key))
    except OSError as e:
        log('error', 'Démon arrêté : %s' % e)
        code = 1
    finally:
        remove_pid(arguments.pid)
    log('info', 'Démon arrêté')
    sys.exit(code)
