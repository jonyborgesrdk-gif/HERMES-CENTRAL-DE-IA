"""POST /refresh do servidor do painel (B1) e /favicon.ico (B10), com coletor sempre falso.

Nunca toca a porta 7420 real: cada teste aponta o servidor para um coletor fake ou para uma porta fechada.
"""
import http.client
import http.server
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import servidor

CSP_ESPERADA = "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"


def setUpModule():
    for nome in ('Popen', 'run'):
        guarda = patch.object(subprocess, nome, side_effect=AssertionError('coletor real proibido'))
        guarda.start()
        unittest.addModuleCleanup(guarda.stop)


def porta_fechada():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    porta = s.getsockname()[1]
    s.close()
    return porta


class ColetorFalso(http.server.HTTPServer):
    def __init__(self, status):
        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.server.recebidos.append((self.path, self.headers.get('Content-Length')))
                self.send_response(self.server.status)
                self.send_header('Content-Length', '0')
                self.end_headers()

            def log_message(self, *a):
                pass
        super().__init__(('127.0.0.1', 0), H)
        self.status, self.recebidos = status, []


class ProxyRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.publico = Path(self.tmp.name)
        (self.publico / 'index.html').write_text('<h1>x</h1>', encoding='utf-8')
        (self.publico / 'dados.js').write_text(servidor.PREFIXO + json.dumps({'agentes': []}) + ');\n', encoding='utf-8')
        self.server = servidor.PainelHTTP(self.publico, 0, porta_coletor=porta_fechada())
        for srv in (self.server,):
            fio = threading.Thread(target=srv.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
            fio.start()
        self.addCleanup(self.encerrar, self.server)

    def encerrar(self, srv):
        srv.parar.set()
        srv.shutdown()
        srv.server_close()

    def coletor(self, status=204):
        fake = ColetorFalso(status)
        threading.Thread(target=fake.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True).start()
        self.addCleanup(lambda: (fake.shutdown(), fake.server_close()))
        self.server.porta_coletor = fake.server_address[1]
        return fake

    def pedir(self, metodo, caminho, headers=None, corpo=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        self.addCleanup(conn.close)
        conn.request(metodo, caminho, body=corpo, headers=headers or {})
        r = conn.getresponse()
        return r.status, dict(r.getheaders()), r.read()

    def mesma_origem(self):
        return {'Origin': 'http://127.0.0.1:%d' % self.server.server_port, 'Sec-Fetch-Site': 'same-origin',
                'Content-Length': '0'}

    def test_204_quando_o_coletor_aceita(self):
        fake = self.coletor(204)
        status, headers, corpo = self.pedir('POST', '/refresh', self.mesma_origem())
        self.assertEqual((status, corpo), (204, b''))
        self.assertEqual(fake.recebidos, [('/refresh', '0')])
        self.assertEqual(headers['Content-Security-Policy'], CSP_ESPERADA)
        self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_204_sem_cabecalhos_de_navegador(self):
        self.coletor(204)
        self.assertEqual(self.pedir('POST', '/refresh', {'Content-Length': '0'})[0], 204)

    def test_503_sem_coletor_ouvindo(self):
        status, _, corpo = self.pedir('POST', '/refresh', self.mesma_origem())
        self.assertEqual((status, corpo), (503, b''))

    def test_502_quando_o_coletor_recusa(self):
        self.coletor(500)
        self.assertEqual(self.pedir('POST', '/refresh', self.mesma_origem())[0], 502)

    def test_403_cross_site_sem_chamar_o_coletor(self):
        fake = self.coletor(204)
        base = {'Content-Length': '0'}
        casos = [
            dict(base, **{'Sec-Fetch-Site': 'cross-site', 'Origin': 'http://exemplo.invalid'}),
            dict(base, **{'Sec-Fetch-Site': 'same-site'}),
            dict(base, **{'Sec-Fetch-Site': 'none'}),
            dict(base, **{'Origin': 'http://exemplo.invalid'}),
            dict(base, **{'Referer': 'http://exemplo.invalid/'}),
            dict(base, **{'Host': 'exemplo.invalid'}),
        ]
        for headers in casos:
            with self.subTest(headers=headers):
                status, _, corpo = self.pedir('POST', '/refresh', headers)
                self.assertEqual((status, corpo), (403, b''))
        self.assertEqual(fake.recebidos, [])

    def test_outros_caminhos_e_metodos_nao_repassam(self):
        fake = self.coletor(204)
        self.assertEqual(self.pedir('POST', '/dados.json', self.mesma_origem())[0], 404)
        self.assertEqual(self.pedir('POST', '/outro', self.mesma_origem())[0], 404)
        self.assertNotEqual(self.pedir('GET', '/refresh')[0], 204)
        self.assertEqual(fake.recebidos, [])

    def test_corpo_grande_recusado(self):
        fake = self.coletor(204)
        headers = dict(self.mesma_origem(), **{'Content-Length': '5000'})
        status, _, _ = self.pedir('POST', '/refresh', headers, corpo=b'x' * 5000)
        self.assertEqual(status, 413)
        self.assertEqual(fake.recebidos, [])

    def test_escuta_so_em_loopback(self):
        self.assertEqual(self.server.server_address[0], '127.0.0.1')

    def test_favicon_204_sem_afrouxar_csp(self):
        status, headers, corpo = self.pedir('GET', '/favicon.ico')
        self.assertEqual((status, corpo), (204, b''))
        self.assertEqual(headers['Content-Security-Policy'], CSP_ESPERADA)
        self.assertEqual(self.pedir('HEAD', '/favicon.ico')[0], 204)
        self.assertEqual(self.pedir('GET', '/favicon.ico', {'Host': 'exemplo.invalid'})[0], 403)

    def test_favicon_real_tem_precedencia(self):
        (self.publico / 'favicon.ico').write_bytes(b'\0\0\1\0')
        status, _, corpo = self.pedir('GET', '/favicon.ico')
        self.assertEqual((status, corpo), (200, b'\0\0\1\0'))


if __name__ == '__main__':
    unittest.main()
