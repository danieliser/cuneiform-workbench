"""Loopback-only HTTP server for the Cuneiform query UI (stdlib only)."""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.dont_write_bytecode = True

from .bridge import Backend, UserError, VersionChanged  # noqa: E402
from .explore import Explorer  # noqa: E402
from .falkor import Falkor  # noqa: E402

HOST = '127.0.0.1'  # never configurable: no public exposure
MAX_BODY = 64 * 1024
SCAN_S = 30.0  # server-side stat scan; pages only poll /api/status (review F-4)
WEB = Path(__file__).resolve().parent.parent / 'web'
STATIC = {  # fixed allow-list; no filesystem path is derived from the URL
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
    '/graph.js': ('graph.js', 'text/javascript; charset=utf-8'),
    '/app.css': ('app.css', 'text/css; charset=utf-8'),
    '/favicon.svg': ('favicon.svg', 'image/svg+xml'),
}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
log = logging.getLogger('cuneiform-ui')
UNTIMED = {'/api/status', '/api/health'}
TIMING_NOTE = ('end_to_end_ms covers the whole request on the server, including the per-request snapshot check named in '
               'breakdown.per_request_check (a full re-hash of every file plus the ledger up to 2 GiB; above it, stat fingerprint, '
               'manifest bytes and ledger) and any backend trace (which re-hashes every file itself); query_ms is only the SQL/graph '
               'part and is not the request latency.')
# Backend 63f4221 refusal types, surfaced as typed states instead of a generic 400/500 (review N-2).
TYPED_BACKEND_ERRORS = {
    'PublicationError': (409, 'publication-refused'), 'LedgerCorrupt': (409, 'publication-refused'),
    'ScopeMismatch': (400, 'scope-mismatch'), 'LayerUnavailable': (422, 'layer-unavailable'),
}


class NotFound(Exception):
    """Unknown route (distinct from KeyError, which is a server bug and must surface as 500)."""


class App:
    def __init__(self, port: int, discover_wait: float = 0.0):
        self.port = port
        self.started = time.time()
        home = Path(os.environ.get('CUNEIFORM_UI_HOME', str(Path.home() / '.local/share/cuneiform-ui'))).expanduser()
        self.backend = Backend(Path(os.environ.get('CUNEIFORM_SOURCE_REPO', str(Path.home() / 'Projects/Fun/cuneiform-research'))),
                               Path(os.environ.get('CUNEIFORM_RESEARCH_ROOT', str(Path.home() / '.local/share/cuneiform-research'))),
                               os.environ.get('CUNEIFORM_UI_SHOW_DEMOS', 'auto'), home=home)
        self.explorer = Explorer(self.backend)
        self.falkor = Falkor(self.backend, home, port=int(os.environ.get('CUNEIFORM_FALKORDB_PORT', '26380')))
        self.allowed_hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        self.backend.discover(wait=discover_wait)
        self._stop = threading.Event()

    def start_scanner(self) -> None:
        def loop():
            while not self._stop.wait(SCAN_S):
                try:
                    self.backend.discover()
                except Exception:
                    log.error('background discovery failed\n%s', traceback.format_exc())
        threading.Thread(target=loop, name='discovery', daemon=True).start()

    def stop_scanner(self) -> None:
        self._stop.set()

    def status(self) -> dict:
        return {'backend': self.backend.info, **self.backend.listing(), 'uptime_s': round(time.time() - self.started),
                'server': {'bind': f'{HOST}:{self.port}', 'pid': os.getpid()}}

    def get(self, path: str, q: dict) -> dict:
        one = lambda k: (q.get(k) or [None])[0]  # noqa: E731
        ver = one('version')
        if path == '/api/health':
            return {'ok': True, 'compatibility': self.backend.info['compatibility']}
        if path == '/api/status':
            return self.status()
        if path == '/api/report':
            return self.explorer.report(one('dataset'), ver)
        if path == '/api/coverage':
            return self.explorer.coverage(one('dataset'), ver, one('offset'))
        if path == '/api/top':
            return self.explorer.top(one('dataset'), one('field') or 'atf', one('policy') or 'source', ver)
        if path == '/api/occurrence':
            return self.explorer.occurrence(one('dataset'), one('id'), ver)
        if path == '/api/node':
            return self.explorer.node(one('dataset'), one('id'), ver)
        if path == '/api/neighborhood':
            return self.explorer.neighborhood(one('dataset'), one('id'), one('per'), ver)
        if path == '/api/search':
            return self.explorer.search_nodes(one('dataset'), one('q'), ver)
        if path == '/api/word':
            return self.explorer.word(one('dataset'), one('id'), ver)
        if path == '/api/words':
            return self.explorer.words(one('dataset'), one('q'), ver, one('offset'))
        if path == '/api/falkordb/status':
            return self.falkor.status()
        raise NotFound

    def post(self, path: str, body) -> dict:
        if not isinstance(body, dict):
            raise UserError('JSON object body required')
        if path == '/api/refresh':
            return self.backend.discover(deep=bool(body.get('deep')))
        if path == '/api/query':
            return self.explorer.query(body)
        if path == '/api/falkordb/load':
            return self.falkor.load(self.backend.get(body.get('dataset'), body.get('version')))
        if path == '/api/falkordb/verify':
            return self.falkor.verify_graph(body.get('graph'))
        if path == '/api/falkordb/template':
            return self.falkor.template(body.get('graph'), body.get('template'), body.get('params'), body.get('limit'))
        if path == '/api/falkordb/query':
            raise PermissionError('arbitrary Cypher is disabled (LOAD CSV https egress and range() OOM are possible under GRAPH.RO_QUERY); '
                                  'use /api/falkordb/template')
        if path == '/api/falkordb/drop':
            return self.falkor.drop(body.get('graph'))
        raise NotFound


class Handler(BaseHTTPRequestHandler):
    server_version = 'cuneiform-ui'
    sys_version = ''
    timeout = 30
    app: App

    def log_message(self, fmt, *args):
        log.info('%s %s', self.address_string(), fmt % args)

    def _headers(self, status: int, ctype: str, length: int, cache: str = 'no-store'):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(length))
        self.send_header('Cache-Control', cache)
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.end_headers()

    def _json(self, status: int, payload):
        data = json.dumps(payload, ensure_ascii=False, default=str).encode('utf-8')
        self._headers(status, 'application/json; charset=utf-8', len(data))
        self.wfile.write(data)

    def _guard(self) -> bool:
        # DNS-rebinding and cross-site defenses for a loopback service.
        if self.headers.get('Host') not in self.app.allowed_hosts:
            self._json(421, {'error': 'unexpected Host header'})
            return False
        origin = self.headers.get('Origin')
        if origin is not None and origin not in {f'http://{h}' for h in self.app.allowed_hosts}:
            self._json(403, {'error': 'cross-origin request refused'})
            return False
        site = self.headers.get('Sec-Fetch-Site')
        navigation = self.command == 'GET' and self.headers.get('Sec-Fetch-Mode') == 'navigate' and urlsplit(self.path).path == '/'
        if site not in (None, 'same-origin', 'none') and not navigation:
            self._json(403, {'error': 'cross-site request refused'})
            return False
        return True

    def _dispatch(self, fn):
        backend = self.app.backend
        backend.timing_begin()
        started = time.monotonic()
        try:
            out = fn()
            if isinstance(out, dict) and urlsplit(self.path).path not in UNTIMED:
                # Whole server-side handling of this request (checks, full snapshot verification, query, post-checks,
                # backend traces). The breakdown is optional detail; query_ms alone is never the request latency.
                out['timing'] = {'end_to_end_ms': round((time.monotonic() - started) * 1000, 1), 'breakdown': backend.timing_parts(),
                                 'scope': 'server-side, from request parsed to response built', 'note': TIMING_NOTE}
            self._json(200, out)
        except NotFound:
            self._json(404, {'error': 'not found'})
        except PermissionError as exc:
            self._json(403, {'error': str(exc)})
        except VersionChanged as exc:
            self._json(409, {'error': str(exc), 'code': 'dataset-version-changed'})
        except UserError as exc:
            self._json(400, {'error': str(exc)})
        except ValueError as exc:  # backend validation messages are plain, data-free strings
            status, code = TYPED_BACKEND_ERRORS.get(type(exc).__name__, (400, 'backend-refused'))
            body = {'error': f'backend refused: {exc}', 'code': code}
            if isinstance(getattr(exc, 'state', None), str):
                body['state'] = exc.state
            self._json(status, body)
        except Exception:
            log.error('unhandled error\n%s', traceback.format_exc())
            self._json(500, {'error': 'internal error; see server log'})

    def do_GET(self):
        if not self._guard():
            return
        url = urlsplit(self.path)
        if len(self.path) > 4096:
            return self._json(414, {'error': 'URL too long'})
        if url.path in STATIC:
            name, ctype = STATIC[url.path]
            data = (WEB / name).read_bytes()
            self._headers(200, ctype, len(data), 'no-cache')
            return self.wfile.write(data)
        if not url.path.startswith('/api/'):
            return self._json(404, {'error': 'not found'})
        try:
            q = parse_qs(url.query, max_num_fields=20, strict_parsing=False)
        except ValueError:
            return self._json(400, {'error': 'too many or malformed query fields'})
        self._dispatch(lambda: self.app.get(url.path, q))

    def do_POST(self):
        if not self._guard():
            return
        url = urlsplit(self.path)
        if (self.headers.get('Content-Type') or '').split(';')[0].strip() != 'application/json':
            return self._json(415, {'error': 'application/json required'})
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            return self._json(400, {'error': 'bad Content-Length'})
        if not 0 <= length <= MAX_BODY:
            return self._json(413, {'error': f'body exceeds {MAX_BODY} bytes'})
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw or b'{}')
        except (ValueError, RecursionError):
            return self._json(400, {'error': 'invalid or too deeply nested JSON'})
        self._dispatch(lambda: self.app.post(url.path, body))

    def do_PUT(self):
        self._json(405, {'error': 'method not allowed'})

    do_DELETE = do_PATCH = do_PUT


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    port = int(os.environ.get('CUNEIFORM_UI_PORT', '8740'))
    app = App(port)
    Handler.app = app
    httpd = Server((HOST, port), Handler)
    log.info('backend %s commit %s compatibility %s', app.backend.info['repo'], app.backend.info['commit'], app.backend.info['compatibility'])
    log.info('serving http://%s:%d/ pid %d', HOST, port, os.getpid())
    app.start_scanner()

    def stop(*_):
        threading.Thread(target=httpd.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        httpd.serve_forever()
    finally:
        app.stop_scanner()
        app.backend.close()
        log.info('stopped')


if __name__ == '__main__':
    main()
