"""Tests du démon JeeTerm : lancés contre un vrai processus, sur un port libre.

    python3 -m unittest tests/test_daemon.py -v
"""

import base64
import hashlib
import http.client
import importlib.util
import json
import os
import pwd
import re
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAEMON = os.path.join(ROOT, 'resources', 'jeetermd', 'jeetermd.py')
APIKEY = 'cle-de-test-' + secrets.token_hex(8)


def load_module():
    """Le démon importé comme module, pour tester ses fonctions pures."""
    spec = importlib.util.spec_from_file_location('jeetermd', DAEMON)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Daemon:
    def __init__(self, env=None, preexec_fn=None, keyfile=False, wait=True, port=None, pid=None, **opts):
        self.port = port or free_port()
        self.dir = tempfile.mkdtemp(prefix='jeeterm-test-')
        self.pid = pid or os.path.join(self.dir, 'deamon.pid')
        self.logpath = os.path.join(self.dir, 'jeetermd.log')
        args = {'port': self.port, 'pid': self.pid, 'loglevel': 'debug', 'shell-user': '', 'tmux': 0,
                'idle-timeout': 0, 'max-sessions': 3}
        args.update({k.replace('_', '-'): v for k, v in opts.items()})
        if keyfile:
            self.keyfile = os.path.join(self.dir, 'apikey')
            with open(self.keyfile, 'w') as f:
                f.write(APIKEY + '\n')
            args['apikey-file'] = self.keyfile
        cmd = [sys.executable, DAEMON]
        for k, v in args.items():
            cmd += ['--' + k, str(v)]
        self.log = open(self.logpath, 'w')
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=self.log, stderr=subprocess.STDOUT,
                                     env=env, preexec_fn=preexec_fn)
        if not keyfile:
            self.proc.stdin.write((APIKEY + '\n').encode())
        self.proc.stdin.close()
        if not wait:
            return
        end = time.time() + 5
        while time.time() < end:
            try:
                if self.request('/status')[0] == 200:
                    return
            except OSError:
                time.sleep(0.05)
        text = self.logtext()
        self.stop()
        raise RuntimeError('le démon ne répond pas :\n' + text)

    def logtext(self):
        with open(self.logpath) as f:
            return f.read()

    def request(self, path, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        h = {'X-Jeeterm-Apikey': APIKEY}
        h.update(headers or {})
        conn.request('GET', path, headers=h)
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp.status, (json.loads(body) if body else None)

    def register(self, token=None, **headers):
        token = token or secrets.token_hex(32)
        h = {'X-Jeeterm-Token': token, 'X-Jeeterm-User': 'admin', 'X-Jeeterm-Ip': '192.0.2.1'}
        h.update(headers)
        status, _ = self.request('/register', h)
        return status, token

    def stop(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.log.close()
        shutil.rmtree(self.dir, ignore_errors=True)


class WSClient:
    """Client WebSocket minimal : trames masquées, comme un navigateur."""

    def __init__(self, port, token, cols=None, rows=None):
        self.sock = socket.create_connection(('127.0.0.1', port), timeout=5)
        query = '/ws' + ('?t=' + token if token is not None else '')
        if cols:
            query += '&c=%d&r=%d' % (cols, rows)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(('GET %s HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n'
                           'Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n'
                           'Sec-WebSocket-Version: 13\r\nOrigin: http://localhost\r\n\r\n'
                           % (query, key)).encode())
        head = b''
        while b'\r\n\r\n' not in head:
            chunk = self.sock.recv(1)
            if not chunk:
                break
            head += chunk
        self.status = int(head.split(b' ')[1]) if head else 0
        # Un navigateur refuse la connexion si cette valeur est fausse.
        self.accept_ok = False
        if self.status == 101:
            expected = base64.b64encode(hashlib.sha1(
                (key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
            self.accept_ok = b'Sec-WebSocket-Accept: ' + expected in head
        self.buf = b''
        self.output = b''

    def send(self, opcode, payload, fin=True):
        mask = os.urandom(4)
        n = len(payload)
        b0 = (0x80 if fin else 0) | opcode
        if n < 126:
            head = struct.pack('!BB', b0, 0x80 | n)
        elif n < 65536:
            head = struct.pack('!BBH', b0, 0xFE, n)
        else:
            head = struct.pack('!BBQ', b0, 0xFF, n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(head + mask + masked)

    def send_json(self, obj):
        self.send(0x1, json.dumps(obj).encode())

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError('fermé')
            self.buf += chunk
        data, self.buf = self.buf[:n], self.buf[n:]
        return data

    def recv_frame(self):
        b0, b1 = self._read(2)
        assert not b1 & 0x80, 'trame serveur masquée'
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack('!H', self._read(2))[0]
        elif n == 127:
            n = struct.unpack('!Q', self._read(8))[0]
        return b0 & 0x0F, self._read(n)

    def read_until(self, needle, timeout=10):
        """Accumule la sortie du pty jusqu'à voir `needle`. Renvoie le message x s'il arrive."""
        self.sock.settimeout(timeout)
        end = time.time() + timeout
        while time.time() < end:
            op, data = self.recv_frame()
            if op == 0x2:
                self.output += data
                if needle and needle.encode() in self.output:
                    return None
            elif op == 0x1:
                msg = json.loads(data)
                if msg.get('t') == 'x':
                    return msg
            elif op == 0x9:
                self.send(0xA, data)
            elif op == 0x8:
                raise ConnectionError('fermeture sans message x')
        raise AssertionError('%r introuvable dans %r' % (needle, self.output[-500:]))

    def wait_exit(self, timeout=10):
        msg = self.read_until(None, timeout)
        op, data = self.recv_frame()
        assert op == 0x8, 'trame de fermeture attendue'
        self.send(0x8, data[:2])
        return msg

    def close_code(self, timeout=5):
        """Lit jusqu'à la trame de fermeture du démon et renvoie son code."""
        self.sock.settimeout(timeout)
        while True:
            op, data = self.recv_frame()
            if op == 0x8:
                return struct.unpack('!H', data[:2])[0] if len(data) >= 2 else None

    def type(self, text):
        self.send_json({'t': 'i', 'd': text})

    def close(self):
        try:
            self.send(0x8, struct.pack('!H', 1000))
        except OSError:
            pass
        self.sock.close()


def shell_pid(client):
    client.type('echo PID_$$_END\r')
    end = time.time() + 10
    while time.time() < end:
        found = re.search(rb'PID_(\d+)_END', client.output)
        if found:
            return int(found.group(1))
        client.read_until('_END', timeout=end - time.time())
        client.output = client.output.replace(b'PID_$$_END', b'')
    raise AssertionError('pid du shell introuvable')


def ws_status(port, token):
    c = WSClient(port, token)
    c.sock.close()
    return c.status


def alive(pid):
    """Vrai si le processus existe encore, zombie compris."""
    return os.path.exists('/proc/%d' % pid)


class DaemonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Daemon(token_ttl=2, max_sessions=3)

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()

    def open(self, cols=None, rows=None):
        status, token = self.d.register()
        self.assertEqual(status, 200)
        c = WSClient(self.d.port, token, cols, rows)
        self.assertEqual(c.status, 101)
        self.addCleanup(c.close)
        return c

    def test_status(self):
        status, body = self.d.request('/status')
        self.assertEqual(status, 200)
        self.assertTrue(body['ok'])
        self.assertIn('sessions', body)
        self.assertEqual(self.d.request('/status', {'X-Jeeterm-Apikey': 'mauvaise'})[0], 401)

    def test_register(self):
        self.assertEqual(self.d.register()[0], 200)
        self.assertEqual(self.d.register(**{'X-Jeeterm-Apikey': 'mauvaise'})[0], 401)
        self.assertEqual(self.d.register(**{'X-Forwarded-For': '192.0.2.9'})[0], 403)
        self.assertEqual(self.d.register('abc')[0], 400)
        self.assertEqual(self.d.register('z' * 64)[0], 400)
        self.assertEqual(self.d.request('/nimporte')[0], 404)

    def test_ws_refused(self):
        self.assertEqual(ws_status(self.d.port, None), 403)
        self.assertEqual(ws_status(self.d.port, 'a' * 64), 403)
        self.assertEqual(ws_status(self.d.port, 'pasunjeton'), 403)

    def test_ws_token_reused(self):
        c = self.open()
        c.close()
        _, token = self.d.register()
        first = WSClient(self.d.port, token)
        self.addCleanup(first.close)
        self.assertEqual(first.status, 101)
        self.assertEqual(ws_status(self.d.port, token), 403)

    def test_ws_token_expired(self):
        _, token = self.d.register()
        time.sleep(2.5)
        self.assertEqual(ws_status(self.d.port, token), 403)

    def test_handshake_accept(self):
        # La clé attendue est recalculée ici avec le GUID de la RFC 6455.
        client = self.open()
        try:
            self.assertTrue(client.accept_ok)
        finally:
            client.close()

    def test_session_echo(self):
        c = self.open()
        c.type('echo hello_$((1+1))\r')
        self.assertIsNone(c.read_until('hello_2'))

    def test_resize(self):
        c = self.open(cols=90, rows=30)
        c.type('stty size; echo SIZE_DONE\r')
        c.read_until('30 90')
        c.send_json({'t': 'r', 'c': 100, 'r': 40})
        c.send_json({'t': 'p'})
        c.type('stty size\r')
        c.read_until('40 100')

    def test_fragmented_input(self):
        c = self.open()
        payload = json.dumps({'t': 'i', 'd': 'echo frag_$((2+3))\r'}).encode()
        # Premier fragment texte, puis continuation, avec un ping intercalé.
        mask = os.urandom(4)
        for opcode, fin, part in ((0x1, 0, payload[:10]), (0x9, 1, b'x'), (0x0, 1, payload[10:])):
            masked = bytes(b ^ mask[i % 4] for i, b in enumerate(part))
            c.sock.sendall(struct.pack('!BB', (0x80 if fin else 0) | opcode, 0x80 | len(part)) + mask + masked)
        while True:
            op, data = c.recv_frame()
            if op != 0x2:
                break
        self.assertEqual((op, data), (0xA, b'x'))
        c.read_until('frag_5')

    def test_exit_code(self):
        c = self.open()
        pid = shell_pid(c)
        c.type('exit 3\r')
        msg = c.wait_exit()
        self.assertEqual(msg['t'], 'x')
        self.assertEqual(msg['code'], 3)
        time.sleep(0.3)
        self.assertFalse(alive(pid), 'shell non récolté')

    def test_exit_plain(self):
        c = self.open()
        c.type('exit\r')
        self.assertEqual(c.wait_exit()['code'], 0)

    def test_browser_close_kills_shell(self):
        c = self.open()
        pid = shell_pid(c)
        c.close()
        end = time.time() + 5
        while alive(pid) and time.time() < end:
            time.sleep(0.1)
        self.assertFalse(alive(pid))

    def test_log_hides_secrets(self):
        _, token = self.d.register()
        text = self.d.logtext()
        self.assertNotIn(APIKEY, text)
        self.assertNotIn(token, text)
        self.assertNotIn(token[:7], text)


class SpecialDaemonTest(unittest.TestCase):
    def daemon(self, **opts):
        d = Daemon(**opts)
        self.addCleanup(d.stop)
        return d

    def test_audit_logged_at_error_level(self):
        # Niveau par défaut de Jeedom : les ouvertures de shell restent tracées.
        d = self.daemon(loglevel='error')
        _, token = d.register()
        c = WSClient(d.port, token)
        c.type('exit\r')
        c.wait_exit()
        c.close()
        WSClient(d.port, 'f' * 64).close()
        time.sleep(0.5)
        text = d.logtext()
        self.assertIn('[AUDIT] : Session ouverte', text)
        self.assertIn('[AUDIT] : Session fermée', text)
        self.assertIn('[AUDIT] : WebSocket refusé', text)
        self.assertNotIn('[DEBUG]', text)

    def test_idle_timeout(self):
        d = self.daemon(idle_timeout=2)
        _, token = d.register()
        c = WSClient(d.port, token)
        self.addCleanup(c.close)
        start = time.time()
        msg = c.wait_exit(timeout=8)
        self.assertEqual(msg['reason'], 'inactivité')
        self.assertGreaterEqual(time.time() - start, 1.5)

    def test_max_sessions(self):
        d = self.daemon(max_sessions=1)
        _, token = d.register()
        c = WSClient(d.port, token)
        self.assertEqual(c.status, 101)
        self.assertEqual(d.request('/status')[1]['sessions'], 1)
        self.assertEqual(d.register()[0], 429)
        c.type('exit\r')
        c.wait_exit()
        c.close()
        time.sleep(0.3)
        self.assertEqual(d.register()[0], 200)

    def test_sigterm(self):
        d = self.daemon()
        _, token = d.register()
        c = WSClient(d.port, token)
        self.addCleanup(c.close)
        pid = shell_pid(c)
        self.assertTrue(os.path.exists(d.pid))
        d.proc.send_signal(signal.SIGTERM)
        msg = c.wait_exit()
        self.assertEqual(msg['reason'], 'arrêt du démon')
        self.assertEqual(d.proc.wait(10), 0)
        self.assertFalse(os.path.exists(d.pid))
        self.assertFalse(alive(pid))

    def test_bad_shell_user(self):
        d = self.daemon(shell_user='compte_qui_nexiste_pas')
        _, token = d.register()
        c = WSClient(d.port, token)
        self.addCleanup(c.close)
        msg = c.wait_exit()
        self.assertEqual(msg['code'], -1)
        self.assertIn('inconnu', msg['reason'])

    def test_tmux_fallback(self):
        d = self.daemon(tmux=1)
        _, token = d.register()
        c = WSClient(d.port, token)
        self.addCleanup(c.close)
        if subprocess.call(['which', 'tmux'], stdout=subprocess.DEVNULL) == 0:
            self.skipTest('tmux installé')
        c.read_until('tmux est absent')
        c.type('echo ok_$((5*5))\r')
        c.read_until('ok_25')


def raw_request(port, text):
    """Requête HTTP écrite à la main (doublons, replis…) ; renvoie le code."""
    with socket.create_connection(('127.0.0.1', port), timeout=5) as sock:
        sock.sendall(text.encode('latin-1'))
        data = b''
        while b'\r\n' not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    return int(data.split(b' ')[1]) if data else 0


class ProtocolTest(unittest.TestCase):
    """RFC 6455 et HTTP : ce qu'un pair malformé ou hostile peut envoyer."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon(max_sessions=20)

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()

    def open(self):
        status, token = self.d.register()
        self.assertEqual(status, 200)
        c = WSClient(self.d.port, token)
        self.assertEqual(c.status, 101)
        self.addCleanup(c.close)
        return c

    def test_control_frame_too_long(self):
        c = self.open()
        c.send(0x9, b'x' * 126)
        self.assertEqual(c.close_code(), 1002)

    def test_fragmented_control_frame(self):
        c = self.open()
        c.send(0x9, b'x', fin=False)
        self.assertEqual(c.close_code(), 1002)

    def test_close_code_invalid(self):
        for payload in (struct.pack('!H', 999), struct.pack('!H', 1005), struct.pack('!H', 1015),
                        struct.pack('!H', 2000), struct.pack('!H', 5000), b'\x03'):
            c = self.open()
            c.send(0x8, payload)
            self.assertEqual(c.close_code(), 1002, payload)

    def test_close_reason_not_utf8(self):
        c = self.open()
        c.send(0x8, struct.pack('!H', 1000) + b'\xff\xfe')
        self.assertEqual(c.close_code(), 1007)

    def test_close_echoed(self):
        c = self.open()
        c.send(0x8, struct.pack('!H', 4001) + 'au revoir é'.encode())
        self.assertEqual(c.close_code(), 4001)

    def test_message_too_big(self):
        c = self.open()
        # Seul l'en-tête est envoyé : le démon doit refuser sur la longueur annoncée.
        c.sock.sendall(struct.pack('!BBQ', 0x81, 0xFF, 2 * 1024 * 1024) + os.urandom(4))
        self.assertEqual(c.close_code(), 1009)

    def test_text_not_utf8(self):
        c = self.open()
        c.send(0x1, b'{"t":"i","d":"\xc3"}')
        self.assertEqual(c.close_code(), 1007)

    def test_utf8_split_across_fragments(self):
        c = self.open()
        payload = json.dumps({'t': 'i', 'd': 'echo xéy_$((6*7))\r'}, ensure_ascii=False).encode()
        cut = payload.index('é'.encode()) + 1          # au milieu du caractère
        c.send(0x1, payload[:cut], fin=False)
        c.send(0x0, payload[cut:])
        c.read_until('xéy_42')

    def test_64bit_length(self):
        c = self.open()
        # Plus de 65535 octets : longueur sur 64 bits. Le JSON admet des blancs.
        body = json.dumps({'t': 'i', 'd': 'echo big_$((8*8))\r'})
        c.send(0x1, (body[:-1] + ' ' * 70000 + '}').encode())
        c.read_until('big_64')

    def test_bad_resize_survives(self):
        c = self.open()
        for text in ('{"t":"r","c":Infinity,"r":24}', '{"t":"r","c":"a","r":[]}', '[1,2]', '"x"',
                     '{"t":"i","d":42}', '[' * 100000 + ']' * 100000):
            c.send(0x1, text.encode())
        c.type('echo alive_$((3*3))\r')
        c.read_until('alive_9')

    def test_bad_ws_key(self):
        _, token = self.d.register()
        status = raw_request(self.d.port, 'GET /ws?t=%s HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n'
                                          'Connection: Upgrade\r\nSec-WebSocket-Key: court\r\n'
                                          'Sec-WebSocket-Version: 13\r\n\r\n' % token)
        self.assertEqual(status, 400)
        # Le jeton n'a pas été consommé par une poignée de main invalide.
        self.assertEqual(ws_status(self.d.port, token), 101)

    def test_duplicate_and_folded_headers(self):
        base = 'GET /register HTTP/1.1\r\nHost: localhost\r\nX-Jeeterm-Apikey: %s\r\n' % APIKEY
        tok = 'X-Jeeterm-Token: %s\r\n' % secrets.token_hex(32)
        self.assertEqual(raw_request(self.d.port, base + tok + '\r\n'), 200)
        self.assertEqual(raw_request(self.d.port, base + tok + tok + '\r\n'), 400)
        self.assertEqual(raw_request(self.d.port, base + tok + 'X-Jeeterm-User: a\r\n b\r\n\r\n'), 400)
        self.assertEqual(raw_request(self.d.port, base + tok + 'X-Jeeterm-User : a\r\n\r\n'), 400)
        self.assertEqual(raw_request(self.d.port, base.replace('localhost', 'a\r\nHost: b') + tok + '\r\n'),
                         400)

    def test_log_injection(self):
        status, token = self.d.register(**{'X-Jeeterm-User': 'mechant\x1b[31m\x9b', 'X-Jeeterm-Ip': '1.2.3.4\x07'})
        self.assertEqual(status, 200)
        sock = socket.create_connection(('127.0.0.1', self.d.port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(('GET /ws?t=%s HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n'
                      'Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n'
                      'Origin: http://evil\x1b]0;x\x07.example\r\n\r\n' % (token, key)).encode('latin-1'))
        sock.recv(1024)
        sock.close()
        raw_request(self.d.port, 'GET /\x1b[2Jregister HTTP/1.1\r\nX-Forwarded-For: 1\r\n\r\n')
        time.sleep(0.5)
        text = self.d.logtext()
        self.assertNotIn('\x1b', text)
        self.assertNotIn('\x07', text)
        self.assertNotIn('\x9b', text)
        self.assertIn('mechant', text)


class ModuleTest(unittest.TestCase):
    """Fonctions du démon testées sans le lancer."""

    @classmethod
    def setUpClass(cls):
        cls.m = load_module()

    def test_levels(self):
        m = self.m
        for value, level in (('debug', 0), ('100', 0), ('info', 1), ('200', 1), ('notice', 2), ('250', 2),
                             ('warning', 2), ('300', 2), ('error', 3), ('400', 3), ('critical', 3),
                             ('500', 3), ('none', 99), ('1000', 99), ('n\'importe', 3)):
            m.set_level(value)
            self.assertEqual(m._min_level, level, value)

    def test_clean(self):
        self.assertEqual(self.m.clean('a\x1b[1mb\r\nc\x9b'), 'a\\x1b[1mb\\x0d\\x0ac\\x9b')
        self.assertEqual(self.m.clean('été'), 'été')
        self.assertEqual(len(self.m.clean('x' * 1000)), 128)

    def test_close_codes(self):
        ok = self.m.close_code_valid
        self.assertTrue(all(ok(c) for c in (1000, 1001, 1002, 1003, 1007, 1011, 3000, 4999)))
        self.assertFalse(any(ok(c) for c in (0, 999, 1004, 1005, 1006, 1015, 1016, 2999, 5000)))

    def test_home_fallback(self):
        # Compte dont le dossier personnel n'est pas inscriptible (www-data et /var/www).
        account = pwd.struct_passwd(('www', 'x', 33, 33, '', '/proc', '/usr/sbin/nologin'))
        tmp = tempfile.mkdtemp(prefix='jeeterm-test-')
        self.addCleanup(shutil.rmtree, tmp, True)

        class Args:
            pid = os.path.join(tmp, 'deamon.pid')
        home = self.m.shell_home(Args, account, True)
        if home != '/var/lib/jeeterm/home':
            self.assertEqual(home, os.path.join(tmp, 'home'))
            self.assertEqual(os.stat(home).st_mode & 0o777, 0o700)
        # Un autre compte garde son dossier : sudo -i le fixe de toute façon.
        self.assertEqual(self.m.shell_home(Args, account, False), '/proc')

    def test_shell_lang(self):
        lang = self.m.shell_lang()
        self.assertRegex(lang, r'(?i)utf-?8')


class RobustnessTest(unittest.TestCase):
    def daemon(self, **opts):
        d = Daemon(**opts)
        self.addCleanup(d.stop)
        return d

    def session(self, d, **kw):
        _, token = d.register()
        c = WSClient(d.port, token, **kw)
        self.assertEqual(c.status, 101)
        self.addCleanup(c.close)
        return c

    def test_apikey_file(self):
        d = self.daemon(keyfile=True)
        self.assertFalse(os.path.exists(d.keyfile), 'fichier de clé non supprimé')
        self.assertEqual(d.register()[0], 200)
        self.assertNotIn('--apikey-file ' + APIKEY, d.logtext())

    def test_pid_dir_created(self):
        base = tempfile.mkdtemp(prefix='jeeterm-test-')
        self.addCleanup(shutil.rmtree, base, True)
        pid = os.path.join(base, 'a', 'b', 'deamon.pid')
        d = self.daemon(pid=pid)
        with open(pid) as f:
            self.assertEqual(int(f.read()), d.proc.pid)

    def test_second_daemon_keeps_pid_file(self):
        d = self.daemon()
        other = Daemon(port=d.port, pid=d.pid, wait=False)
        self.addCleanup(other.stop)
        self.assertEqual(other.proc.wait(10), 1)
        with open(d.pid) as f:
            self.assertEqual(int(f.read()), d.proc.pid, 'fichier pid du démon en service écrasé ou retiré')
        self.assertEqual(d.request('/status')[0], 200)

    def test_sighup_ignored(self):
        d = self.daemon()
        c = self.session(d)
        d.proc.send_signal(signal.SIGHUP)
        time.sleep(0.3)
        self.assertIsNone(d.proc.poll())
        c.type('echo still_$((4+4))\r')
        c.read_until('still_8')

    def test_shell_signals_reset(self):
        # Apache/PHP lance le démon avec SIGQUIT ignoré : le shell ne doit pas en hériter.
        d = self.daemon(preexec_fn=lambda: signal.signal(signal.SIGQUIT, signal.SIG_IGN))
        c = self.session(d)
        c.type('command grep SigIgn /proc/self/status; echo SIG_$((1+1))END\r')
        c.read_until('SIG_2END')
        self.assertRegex(c.output.decode(), r'SigIgn:\s+0{16}')

    def test_env_minimal_and_utf8(self):
        env = dict(os.environ, JEETERM_SECRET='fuite', LANG='C')
        env.pop('LC_ALL', None)
        d = self.daemon(env=env)
        c = self.session(d)
        c.type('env | sort; echo ENV_$((1+1))END\r')
        c.read_until('ENV_2END')
        out = c.output.decode()
        self.assertNotIn('fuite', out)
        self.assertRegex(out, r'(?m)^LANG=\S*(?i:utf-?8)')
        self.assertRegex(out, r'(?m)^LC_ALL=\S*(?i:utf-?8)')

    def test_register_reserves_slot(self):
        d = self.daemon(max_sessions=1)
        _, first = d.register()
        # Le jeton pas encore utilisé occupe la seule place…
        self.assertEqual(d.register(**{'X-Jeeterm-User': 'autre'})[0], 429)
        # … mais le même utilisateur peut réessayer : son nouveau jeton remplace l'ancien.
        status, second = d.register()
        self.assertEqual(status, 200)
        self.assertEqual(ws_status(d.port, first), 403)
        self.assertEqual(ws_status(d.port, second), 101)

    def test_why_field(self):
        d = self.daemon(idle_timeout=2)
        c = self.session(d)
        c.type('exit 4\r')
        msg = c.wait_exit()
        self.assertEqual((msg['why'], msg['code']), ('exit', 4))
        c = self.session(d)
        self.assertEqual(c.wait_exit()['why'], 'idle')

    def test_idle_warning_and_busy_command(self):
        d = self.daemon(idle_timeout=2)
        c = self.session(d)
        # Commande qui calcule 4 s sans rien afficher : plus longue que le délai,
        # elle ne doit pas être interrompue.
        c.type('timeout 4 sh -c "while :; do :; done"; echo BUSY_$((2*21))\r')
        start = time.time()
        msg = c.read_until('BUSY_42', timeout=10)
        self.assertIsNone(msg, 'session fermée pendant la commande')
        self.assertGreaterEqual(time.time() - start, 3.5)
        msg = c.wait_exit(timeout=8)
        self.assertEqual(msg['why'], 'idle')
        self.assertIn('Session inactive', c.output.decode())

    def test_lost_connection_under_backpressure(self):
        # Le navigateur ne lit plus et ne répond plus : la sortie remplit le tampon,
        # et la connexion doit quand même être déclarée perdue.
        d = self.daemon(ping_interval=1)
        _, token = d.register()
        c = WSClient(d.port, token)
        self.addCleanup(c.close)
        c.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        pid = shell_pid(c)
        c.type('yes jeeterm\r')
        end = time.time() + 15
        while time.time() < end and (d.request('/status')[1]['sessions'] or alive(pid)):
            time.sleep(0.2)
        self.assertEqual(d.request('/status')[1]['sessions'], 0)
        self.assertFalse(alive(pid))
        self.assertIn('connexion perdue', d.logtext())

    def test_fast_shutdown(self):
        d = self.daemon(max_sessions=5)
        pids = []
        for _ in range(3):
            c = self.session(d)
            pids.append(shell_pid(c))
        # Un shell qui ignore SIGHUP : il faudra le SIGKILL.
        c.type('trap "" HUP TERM; sleep 100 & wait\r')
        time.sleep(0.3)
        start = time.time()
        d.proc.send_signal(signal.SIGTERM)
        self.assertEqual(d.proc.wait(10), 0)
        self.assertLess(time.time() - start, 5)
        time.sleep(0.3)
        self.assertFalse(any(alive(p) for p in pids))

    def test_sudo_refused_message(self):
        if shutil.which('sudo') is None or subprocess.call(
                ['sudo', '-n', 'true'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
            self.skipTest('sudo absent, ou autorisé sans mot de passe')
        d = self.daemon(shell_user='root')
        c = self.session(d)
        msg = c.wait_exit()
        self.assertEqual(msg['why'], 'error')
        self.assertIn('sudo', msg['reason'])
        self.assertIn('sans mot de passe', c.output.decode())


RCFILE = os.path.join(ROOT, 'resources', 'jeetermd', 'jeeterm.bashrc')
ANSI_RE = re.compile(rb'\x1b\][^\x07]*\x07|\x1b\[[0-9;?]*[A-Za-z]')


def visible_lines(output):
    """Lignes telles que l'écran les montre : sans séquences ANSI ni titre OSC."""
    return ANSI_RE.sub(b'', output).decode('utf-8', 'replace').replace('\r', '').split('\n')


def run_rcfile(cols=80, command='exit'):
    """bash --rcfile <fichier> -i dans un vrai pty ; renvoie (sortie, durée)."""
    master, slave = os.openpty()
    struct_size = struct.pack('HHHH', 24, cols, 0, 0)
    import fcntl
    import termios
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct_size)
    env = {'TERM': 'xterm-256color', 'LANG': 'C.UTF-8', 'PATH': '/usr/bin:/bin',
           'HOME': tempfile.gettempdir(), 'USER': pwd.getpwuid(os.geteuid()).pw_name}
    start = time.monotonic()
    proc = subprocess.Popen(['/bin/bash', '--rcfile', RCFILE, '-i', '-c', command], stdin=slave,
                            stdout=slave, stderr=slave, env=env, start_new_session=True)
    os.close(slave)
    out = b''
    while True:
        try:
            chunk = os.read(master, 65536)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    proc.wait(10)
    os.close(master)
    return out, time.monotonic() - start


class GoodiesTest(unittest.TestCase):
    """Bannière, invite et raccourcis (réglage « goodies »)."""

    def session(self, cols=None, rows=None, **opts):
        d = Daemon(**opts)
        self.addCleanup(d.stop)
        _, token = d.register()
        c = WSClient(d.port, token, cols, rows)
        self.assertEqual(c.status, 101)
        self.addCleanup(c.close)
        return c

    def test_banner_prompt_and_functions(self):
        c = self.session(goodies=1)
        c.type('type -t jlog jcd jinfo jplugins; echo G_$((1+1))END\r')
        c.read_until('G_2END\r')
        out = c.output
        self.assertIn(b'Jee', out)
        self.assertIn(socket.gethostname().encode(), out)
        self.assertIn(b'Jeedom', out)
        self.assertEqual(out.count(b'function\r\n'), 4)
        # Titre de la fenêtre (OSC 0) posé par l'invite.
        self.assertRegex(out, rb'\x1b\]0;[^\x07]+@[^\x07]+:[^\x07]*\x07')

    def test_author_card(self):
        c = self.session(goodies=1)
        c.type('jauteur; jauthor >/dev/null && echo A_$((1+1))END\r')
        c.read_until('A_2END\r')
        out = c.output.decode('utf-8', 'replace')
        self.assertIn('Jérôme Fafchamps', out)
        self.assertIn('jerome@fafchamps.be', out)
        self.assertRegex(out, r'v\d+\.\d+')

    def test_exit_status_in_prompt(self):
        c = self.session(goodies=1)
        c.type('(exit 7)\r')
        c.read_until('[7]')
        # La commande suivante réussit : le code disparaît de l'invite.
        c.output = b''
        c.type('echo R_$((1+1))END\r')
        c.read_until('R_2END\r')
        if not c.output.endswith(b'$ '):
            c.read_until('$ ')
        self.assertNotIn(b'[7]', c.output)

    def test_goodies_off(self):
        c = self.session(goodies=0)
        c.type('type -t jlog || echo NO_$((2+2))_JLOG\r')
        c.read_until('NO_4_JLOG\r')
        self.assertNotIn(b'Astuce', c.output)
        self.assertNotIn(b'Tip:', c.output)
        self.assertNotIn('▄█▄'.encode(), c.output)

    def test_banner_fits_width(self):
        for cols, logo in ((80, True), (60, False)):
            out, _ = run_rcfile(cols)
            lines = visible_lines(out)
            self.assertTrue(any('Jeedom' in line for line in lines), lines)
            self.assertEqual(any('▄█▄' in line for line in lines), logo, cols)
            for line in lines:
                self.assertLessEqual(len(line), cols, line)

    def test_rcfile_syntax_and_speed(self):
        self.assertEqual(subprocess.call(['bash', '-n', RCFILE]), 0)
        # Le fichier est lu à chaque ouverture : il doit rester rapide.
        best = min(run_rcfile()[1] for _ in range(3))
        self.assertLess(best, 0.5, 'démarrage trop lent : %.3f s' % best)

    @unittest.skipIf(shutil.which('shellcheck') is None, 'shellcheck absent')
    def test_shellcheck(self):
        self.assertEqual(subprocess.call(['shellcheck', '-s', 'bash', '-S', 'warning', RCFILE]), 0)

    def test_command_line(self):
        m = load_module()

        class Args:
            shell_user = ''
            tmux = 0
            goodies = 1
            pid = os.path.join(tempfile.gettempdir(), 'jeeterm-test.pid')
        self.assertEqual(m.shell_command(Args)[1], ['/bin/bash', '--rcfile', m.RCFILE, '-i'])
        Args.goodies = 0
        self.assertEqual(m.shell_command(Args)[1], ['/bin/bash', '-l'])
        if os.geteuid() != 0:
            # Par sudo : le shell de connexion du compte lance notre bash.
            Args.goodies, Args.shell_user = 1, 'root'
            expected = ['sudo', '-n', '-i']
            if os.path.basename(pwd.getpwnam('root').pw_shell) == 'bash':
                expected += ['/bin/bash', '--rcfile', m.RCFILE, '-i']
            self.assertEqual(m.shell_command(Args)[1], expected)


@unittest.skipIf(shutil.which('tmux') is None, 'tmux absent')
class TmuxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='jeeterm-tmux-')
        # Serveur tmux isolé : celui de l'utilisateur n'est pas touché.
        self.env = dict(os.environ, TMUX_TMPDIR=self.tmp)
        self.d = Daemon(tmux=1, env=self.env)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.d.stop()
        subprocess.call(['tmux', 'kill-server'], env=self.env, stderr=subprocess.DEVNULL)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def session(self):
        _, token = self.d.register()
        c = WSClient(self.d.port, token)
        self.assertEqual(c.status, 101)
        return c

    def test_close_detaches(self):
        c = self.session()
        c.type('export MARK=41; tmux display -p "SRV_#{pid}_END"\r')
        c.read_until('_END\r')
        server = int(re.search(rb'SRV_(\d+)_END', c.output).group(1))
        c.close()
        time.sleep(1)
        self.assertTrue(alive(server), 'serveur tmux tué à la fermeture')
        self.assertEqual(subprocess.call(['tmux', 'has-session', '-t', 'jeeterm'], env=self.env), 0)
        c = self.session()
        self.addCleanup(c.close)
        c.type('echo M_$((MARK+1))\r')
        c.read_until('M_42')

    def test_goodies_in_tmux(self):
        c = self.session()
        self.addCleanup(c.close)
        c.type('type -t jlog; tmux show-options -v default-command; echo T_$((1+1))END\r')
        c.read_until('T_2END\r')
        self.assertIn(b'Jeedom', c.output)
        self.assertIn(b'function', c.output)
        self.assertIn(b'--rcfile', c.output)


if __name__ == '__main__':
    unittest.main()
