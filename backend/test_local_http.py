import errno
import json
import os
import socket
import threading
import unittest
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from common import local_urlopen


class _RecordingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._respond()

    def do_POST(self):
        self._respond()

    def _respond(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        self.server.requests.append((self.command, self.path, body))
        status, headers, payload = self.server.respond(self.command, self.path, body)
        encoded = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(encoded)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass


@contextmanager
def _server(respond, host='127.0.0.1'):
    class Server(HTTPServer):
        address_family = socket.AF_INET6 if ':' in host else socket.AF_INET

    server = Server((host, 0), _RecordingHandler)
    server.requests = []
    server.respond = respond
    server.url = 'http://{}:{}'.format(
        '[' + host + ']' if ':' in host else host, server.server_port)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


class LocalHttp(unittest.TestCase):
    def test_model_get_and_post_bypass_system_proxy_without_changing_global_client(self):
        def model_response(method, path, body):
            if method == 'GET' and path == '/api/tags':
                return 200, {}, {'models': [{'name': 'local-model'}]}
            if method == 'POST' and path == '/api/generate':
                return 200, {}, {'response': 'local answer', 'model': json.loads(body)['model']}
            return 404, {}, {'error': 'unexpected model request'}

        with _server(model_response) as model, _server(
            lambda *args: (200, {}, {'source': 'system proxy'})
        ) as proxy:
            proxies = {'http': proxy.url, 'https': proxy.url}
            proxy_environment = {
                'HTTP_PROXY': proxy.url, 'HTTPS_PROXY': proxy.url,
                'http_proxy': proxy.url, 'https_proxy': proxy.url,
                'NO_PROXY': '', 'no_proxy': '',
            }
            with patch.dict(os.environ, proxy_environment), patch.object(
                urllib.request, 'getproxies', return_value=proxies
            ), patch.object(urllib.request, 'proxy_bypass', return_value=False):
                global_opener = urllib.request.build_opener()
                original_urlopen = urllib.request.urlopen
                environment = dict(os.environ)
                with patch.object(urllib.request, '_opener', global_opener):
                    # Prove the simulated system proxy intercepts an ordinary local request.
                    with urllib.request.urlopen(model.url + '/control', timeout=2) as response:
                        self.assertEqual(json.load(response), {'source': 'system proxy'})
                    self.assertEqual(len(proxy.requests), 1)
                    self.assertEqual(model.requests, [])
                    proxy.requests.clear()

                    with local_urlopen(model.url + '/api/tags', timeout=2) as response:
                        self.assertEqual(json.load(response), {'models': [{'name': 'local-model'}]})
                    payload = {'model': 'local-model', 'prompt': 'Keep this request on this machine.'}
                    request = urllib.request.Request(
                        model.url.replace('127.0.0.1', 'localhost') + '/api/generate',
                        data=json.dumps(payload).encode('utf-8'),
                        headers={'Content-Type': 'application/json'},
                    )
                    with local_urlopen(request, timeout=2) as response:
                        self.assertEqual(json.load(response), {'response': 'local answer', 'model': 'local-model'})

                    self.assertEqual(proxy.requests, [])
                    self.assertEqual([(method, path) for method, path, _ in model.requests], [
                        ('GET', '/api/tags'), ('POST', '/api/generate'),
                    ])
                    self.assertEqual(json.loads(model.requests[1][2]), payload)
                    self.assertEqual(dict(os.environ), environment)
                    self.assertIs(urllib.request.urlopen, original_urlopen)
                    self.assertIs(urllib.request._opener, global_opener)

                    # Other callers must still use their existing proxy configuration.
                    with urllib.request.urlopen(model.url + '/control-after', timeout=2) as response:
                        self.assertEqual(json.load(response), {'source': 'system proxy'})
                    self.assertEqual(len(proxy.requests), 1)

    def test_external_and_non_http_urls_are_rejected_before_connecting(self):
        urls = [
            'https://external.invalid/api/generate',
            'http://192.0.2.1/api/tags',
            'http://localhost.external.invalid/api/tags',
            'http://127.0.0.1@external.invalid/api/tags',
            'file:///etc/hosts',
            'ftp://127.0.0.1/model',
        ]
        with patch('socket.create_connection', side_effect=AssertionError('Unexpected network connection')) as connect:
            for url in urls:
                for request in (url, urllib.request.Request(url)):
                    with self.subTest(url=url, request_type=type(request).__name__):
                        with self.assertRaises(ValueError):
                            local_urlopen(request, timeout=2)
            connect.assert_not_called()

    def test_local_redirect_chain_cannot_escape_to_external_service(self):
        def redirect_response(method, path, body):
            location = '/second' if path == '/first' else 'https://external.invalid/api/generate'
            return 302, {'Location': location}, {}

        create_connection = socket.create_connection

        def connect_only_locally(address, *args, **kwargs):
            self.assertEqual(address[0], '127.0.0.1', 'A redirect tried to connect outside loopback')
            return create_connection(address, *args, **kwargs)

        with _server(redirect_response) as model, patch(
            'socket.create_connection', side_effect=connect_only_locally
        ):
            with self.assertRaises(ValueError):
                local_urlopen(model.url + '/first', timeout=2)
            self.assertEqual([path for _, path, _ in model.requests], ['/first', '/second'])

    def test_ipv6_loopback_can_serve_local_model_requests(self):
        try:
            with _server(lambda *args: (200, {}, {'models': []}), host='::1') as model:
                with local_urlopen(model.url + '/api/tags', timeout=2) as response:
                    self.assertEqual(json.load(response), {'models': []})
                self.assertEqual(len(model.requests), 1)
        except OSError as error:
            if error.errno in (errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL, errno.EPROTONOSUPPORT):
                self.skipTest('IPv6 loopback is not available on this host')
            raise


if __name__ == '__main__':
    unittest.main()
